"""The assistant pipeline: fast path vs LLM, conversations, stop/cancel, privacy."""

import threading
import time

from scripted_llm import ScriptedLLM, answer, call, tools

from assistant.core.assistant import Assistant


def make(ts, *script):
    system, p, conf, home = ts
    llm = ScriptedLLM(*script)
    return Assistant(system.ctx, system, llm), llm, p


def test_fast_path_runs_without_llm(ts):
    a, llm, p = make(ts)
    r = a.handle("set the volume to 35")
    assert (r.via, r.intent, r.ok, r.text) == ("fast", "volume.set", True, "Volume 35 percent.")
    assert p.audio.volume == 35 and llm.seen == []
    assert a.handle("what time is it").via == "local"
    assert r.timings["input>fastpath"] < 50


def test_fast_path_fallback_opens_music_app(ts):
    a, llm, p = make(ts)
    p.media.command = lambda action: "Spotify" in p.apps.launched   # no player until the music app runs
    r = a.handle("play some music")
    assert r.ok and p.apps.launched == ["Spotify"] and r.text == "Playing."


def test_llm_path_and_history(ts):
    a, llm, p = make(ts, tools(call("remember", fact="Sam likes green")), answer("Noted!"), answer("Green."))
    assert a.fastpath.match("my favourite colour is green, keep that in mind") is None
    r = a.handle("my favourite colour is green, keep that in mind")
    assert (r.via, r.text) == ("llm", "Noted!")
    r = a.handle("what colour do I like?")
    msgs = llm.seen[-1]
    assert "Sam likes green" in msgs[0]["content"]                # fact is in the system prompt
    roles = [m["role"] for m in msgs]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "user"]   # previous turn kept
    assert r.text == "Green."


def test_conversation_saved_and_searchable(ts):
    system, *_ = ts
    a, llm, p = make(ts, answer("Paris is the capital of France."))
    a.handle("what is the capital of France")
    a.handle("mute")
    convs = a.conversations.recent()
    assert len(convs) == 1 and convs[0].title == "what is the capital of France"
    msgs = a.conversations.messages(convs[0].id)
    assert [m.role for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert a.conversations.search("capital")[0][0].id == convs[0].id
    assert "**You:** what is the capital of France" in a.conversations.export(convs[0].id)


def test_privacy_no_saving(ts):
    system, *_ = ts
    system.ctx.config.data.privacy.save_conversations = False
    a, llm, p = make(ts)
    a.handle("mute")
    assert a.conversations.recent() == []


def test_new_conversation_after_timeout(ts):
    system, *_ = ts
    a, llm, p = make(ts, answer("one"), answer("two"))
    a.handle("first question")
    a._last = time.time() - 3600
    a.handle("second question")
    assert len(llm.seen[-1]) == 2                                 # system + new user only


def test_stop_cancels_running_request(ts):
    system, p, conf, home = ts
    started = threading.Event()

    def slow(messages):
        started.set()
        time.sleep(0.05)
        return tools(call("volume", level=10))

    llm = ScriptedLLM(slow, *[slow] * 50)
    a = Assistant(system.ctx, system, llm)
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("r", a.handle("do something long")))
    t.start()
    started.wait(2)
    time.sleep(0.1)
    stop = a.handle("Jarvis, stop")
    t.join(5)
    assert stop.text == "Cancelled." and result["r"].via == "cancelled"
    assert ("release_all",) in p.input.events


def test_stop_when_idle_pauses_music(ts):
    a, llm, p = make(ts)
    r = a.handle("stop")
    assert r.text == "OK." and "pause" in p.media.commands


def test_llm_unavailable_is_reported(ts):
    system, *_ = ts
    from assistant.llm.service import LlmUnavailable

    class Broken(ScriptedLLM):
        def chat(self, *a, **k):
            raise LlmUnavailable("model not downloaded")

    a = Assistant(system.ctx, system, Broken())
    r = a.handle("write me a poem")
    assert not r.ok and "language model isn't available" in r.text
    assert a.handle("mute").ok                                    # fast path still works


def test_german_replies(ts):
    system, *_ = ts
    system.ctx.config.data.general.language = "de"
    a, llm, p = make(ts)
    assert a.handle("Lautstärke auf 40").text == "Lautstärke 40 Prozent."
    assert a.handle("nächstes Lied").text == "Nächster Titel."


def test_vision_tool_registered(ts):
    a, llm, p = make(ts, tools(call("look_at_screen", locate="the OK button")), answer("Found it."))
    a.handle("where is the OK button on screen?")
    assert "(960, 270)" in llm.seen[1][-1]["content"]             # 50%/25% of the fake 1920x1080 screen
