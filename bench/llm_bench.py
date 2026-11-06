"""Benchmark local models: latency on *this* GPU + tool-calling accuracy on our real tools.

    uv run python bench/llm_bench.py                       # smart + fast model from config
    uv run python bench/llm_bench.py --models qwen3.5-9b,qwen3.5-4b,gemma-4-e4b
    uv run python bench/llm_bench.py --cases open_and_volume,injection --verbose

For every model it starts llama-server, warms the prompt cache, runs each case
(fresh simulated PC per case: real agent loop, real tool registry and permission
gate, fake OS backends, auto-approved confirmations), checks the resulting state
and records timings. Results go to %LOCALAPPDATA%\\Assistant\\bench\\; the
recommendation in ``latest.json`` is what ``llm.profile = "auto"`` uses:
the smart model if its median "reply starts" latency is <= 2 s and it is not less
accurate than the fast model, otherwise the fast one.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "bench"))

from cases import CASES, Env  # noqa: E402

from assistant.core.assistant import Assistant  # noqa: E402
from assistant.core.context import bootstrap  # noqa: E402
from assistant.core.timing import Trace  # noqa: E402
from assistant.llm.models import get_model, is_installed  # noqa: E402
from assistant.llm.service import LlmService  # noqa: E402
from assistant.permissions.gate import AutoConfirm, ConfirmChoice, Task  # noqa: E402
from assistant.permissions.pathrules import PathNormalizer  # noqa: E402
from assistant.storage.db import Database  # noqa: E402
from assistant.testing.fakes import fake_platform  # noqa: E402
from assistant.tools.runtime import build_tool_system, folder_aliases  # noqa: E402

TARGET_REPLY_MS = 2000


def run_case(case, ctx, llm, verbose: bool) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "user"
        home.mkdir()
        platform = fake_platform(home)
        confirmer = AutoConfirm(ConfirmChoice.ONCE)
        norm = PathNormalizer("posix", env={"HOME": str(home)}, home=str(home),
                              aliases=folder_aliases(platform.known_folders))
        system = build_tool_system(ctx, platform=platform, confirmer=confirmer, db=Database(":memory:"),
                                   start_background=False, normalizer=norm)
        ctx.config.data.general.language = case.lang
        assistant = Assistant(ctx, system, llm)
        env = Env(home, platform, system, [], "", confirmer.requests)
        case.setup(env)
        first_answer_ms: list[float] = []
        t0 = time.perf_counter()

        def on_content(_: str) -> None:
            if not first_answer_ms:
                first_answer_ms.append((time.perf_counter() - t0) * 1000)

        task = Task(source="llm")
        assistant._current = task
        reply = assistant._run_llm(case.text, task, Trace(), on_content)
        total_ms = (time.perf_counter() - t0) * 1000
        env.calls, env.answer = reply.calls, reply.text
        try:
            ok = bool(case.check(env))
        except Exception as e:  # a check that crashes is a failure, not a benchmark crash
            ok = False
            reply.text += f"  [check error: {e}]"
        claimed = any(w in reply.text.lower() for w in ("done", "deleted", "moved", "created", "erledigt", "gelöscht"))
        failed_calls = [c for c in reply.calls if not c["ok"]]
        result = {"id": case.id, "lang": case.lang, "ok": ok, "steps": len(reply.calls), "total_ms": round(total_ms),
                  "reply_start_ms": round(first_answer_ms[0]) if first_answer_ms else None,
                  "calls": [f"{c['name']}({json.dumps(c['args'], ensure_ascii=False)[:120]})" + ("" if c["ok"] else "!")
                            for c in reply.calls],
                  "answer": reply.text[:300], "invalid_args": sum("Invalid arguments" in c["text"] for c in failed_calls),
                  "claimed_after_failure": bool(failed_calls and claimed and not ok), "tags": list(case.tags)}
        if verbose:
            print(f"    {case.id}: {'PASS' if ok else 'FAIL'} {result['total_ms']} ms  {result['calls']}\n"
                  f"      -> {reply.text[:200]!r}")
        return result


def bench_model(model_id: str, args, base_ctx) -> dict:
    spec = get_model(model_id)
    ctx = base_ctx
    ctx.config.data.llm.profile = "smart"
    ctx.config.data.llm.model_smart = model_id
    ctx.config.data.llm.vision = False
    ctx.config.data.llm.context_tokens = args.ctx
    ctx.config.data.llm.thinking = "off" if args.no_thinking else "auto"
    if args.server:
        ctx.config.data.llm.server_path = args.server
    llm = LlmService(ctx)
    llm.spec, llm.reason = spec, "benchmark"
    t0 = time.perf_counter()
    # warm-up through a throwaway assistant so the system prompt + tools are cached
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        platform = fake_platform(home)
        system = build_tool_system(ctx, platform=platform, db=Database(":memory:"), start_background=False,
                                   normalizer=PathNormalizer("posix", env={"HOME": str(home)}, home=str(home)))
        Assistant(ctx, system, llm)
        llm.start(wait=True)
    load_ms = (time.perf_counter() - t0) * 1000
    print(f"  {spec.title}: loaded + warmed in {load_ms / 1000:.1f} s")
    results = []
    selected = [c for c in CASES if not args.cases or c.id in args.cases.split(",")]
    selected.sort(key=lambda c: c.lang != ctx.config.data.general.language)  # fewer prompt-cache misses
    for case in selected:
        results.append(run_case(case, ctx, llm, args.verbose))
        if not args.verbose:
            print("    " + ("." if results[-1]["ok"] else "F"), end="", flush=True)
    print()
    llm.stop()
    starts = [r["reply_start_ms"] for r in results if r["reply_start_ms"]]
    acc = sum(r["ok"] for r in results) / max(len(results), 1)
    summary = {
        "model": model_id, "title": spec.title, "load_ms": round(load_ms), "accuracy": round(acc, 3),
        "passed": sum(r["ok"] for r in results), "cases": len(results),
        "reply_start_p50_ms": round(statistics.median(starts)) if starts else None,
        "reply_start_p90_ms": round(sorted(starts)[int(0.9 * (len(starts) - 1))]) if starts else None,
        "total_p50_ms": round(statistics.median(r["total_ms"] for r in results)) if results else None,
        "invalid_args": sum(r["invalid_args"] for r in results),
        "claimed_after_failure": sum(r["claimed_after_failure"] for r in results),
        "injection_resisted": next((r["ok"] for r in results if r["id"] == "injection"), None),
        "results": results,
    }
    return summary


def recommend(summaries: dict[str, dict], smart: str, fast: str) -> tuple[str, str]:
    s, f = summaries.get(smart), summaries.get(fast)
    if s is None:
        return fast, f"{smart} was not benchmarked"
    if f is None:
        return smart, f"{fast} was not benchmarked"
    p50 = s["reply_start_p50_ms"] or 10**9
    if p50 <= TARGET_REPLY_MS and s["accuracy"] >= f["accuracy"]:
        return smart, f"{smart}: reply starts after {p50} ms (target {TARGET_REPLY_MS}), accuracy {s['accuracy']:.0%}"
    if p50 > TARGET_REPLY_MS:
        return fast, f"{smart} too slow here (reply starts after {p50} ms > {TARGET_REPLY_MS} ms)"
    return fast, f"{fast} was as accurate ({f['accuracy']:.0%} vs {s['accuracy']:.0%}) and faster"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", help="comma-separated model ids (default: config smart + fast)")
    ap.add_argument("--cases", help="comma-separated case ids (default: all)")
    ap.add_argument("--server", help="path to llama-server (default: installed one)")
    ap.add_argument("--home", help="assistant data dir (default: the normal one)")
    ap.add_argument("--ctx", type=int, default=16384)
    ap.add_argument("--no-thinking", action="store_true", help="never use thinking mode")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--no-save", action="store_true", help="don't write latest.json")
    args = ap.parse_args()

    ctx = bootstrap("cli", home=args.home, detect_hardware="off")
    ctx.config.data.privacy.save_conversations = False
    c = ctx.config.data.llm
    smart, fast = c.model_smart, c.model_fast
    ids = args.models.split(",") if args.models else [smart, fast]
    summaries: dict[str, dict] = {}
    for mid in ids:
        spec = get_model(mid)
        if not is_installed(spec, ctx.paths.models_dir, vision=False):
            print(f"skipping {mid}: not downloaded (assistant-cli --setup --models {mid})")
            continue
        print(f"Benchmarking {spec.title} ...")
        summaries[mid] = bench_model(mid, args, ctx)

    print("\nmodel              accuracy  reply starts p50/p90   total p50  invalid args  injection  load")
    for s in summaries.values():
        print(f"{s['model']:<18} {s['passed']:>2}/{s['cases']:<2} {s['accuracy']:>4.0%}   "
              f"{s['reply_start_p50_ms'] or 0:>6} / {s['reply_start_p90_ms'] or 0:<6} ms  {s['total_p50_ms']:>7} ms"
              f"  {s['invalid_args']:>6}        {'ok' if s['injection_resisted'] else 'FAILED':<9}  "
              f"{s['load_ms'] / 1000:.1f} s")
    for s in summaries.values():
        fails = [r["id"] for r in s["results"] if not r["ok"]]
        if fails:
            print(f"  {s['model']} failed: {', '.join(fails)}")
    model, reason = recommend(summaries, smart, fast)
    print(f"\nRecommended default: {model} - {reason}")
    out_dir = ctx.paths.local_dir / "bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"date": time.strftime("%Y-%m-%d %H:%M"), "recommended": model, "reason": reason,
              "hardware": ctx.hardware, "models": summaries}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (out_dir / f"results-{stamp}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if not args.no_save and smart in summaries and fast in summaries:
        (out_dir / "latest.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved as the default choice for profile=auto: {out_dir / 'latest.json'}")
    print(f"Full results: {out_dir / f'results-{stamp}.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
