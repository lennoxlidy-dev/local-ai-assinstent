"""Driver updates through Windows Update - the official, signed drivers for this PC's hardware.

* ``check``: asks Windows Update which driver updates are available (no admin rights needed).
* ``install``: shows exactly that list, and after the user's yes installs *only those* updates with
  administrator rights (one Windows UAC prompt). It runs in the background - downloads can take minutes -
  and a notification reports the result. An installation is never interrupted half-way.

Windows Update often lags behind NVIDIA's newest "Game Ready" drivers, so the answer names the installed
graphics driver and points to the NVIDIA App for those.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from ..core.cancel import CancelToken
from ..core.events import Topic
from ..permissions.policy import Risk
from .base import Plan, Tool, ToolContext, ToolError, ToolRegistry, ToolResult
from .schema import Str, obj

log = logging.getLogger(__name__)

CRITERIA = "IsInstalled=0 and Type='Driver' and IsHidden=0"

SEARCH_PS = f"""$ErrorActionPreference = 'Stop'
$s = New-Object -ComObject Microsoft.Update.Session
$s.ClientApplicationID = 'Assistant'
$r = $s.CreateUpdateSearcher().Search("{CRITERIA}")
$list = @(foreach ($u in $r.Updates) {{
  [pscustomobject]@{{ id = $u.Identity.UpdateID; title = $u.Title; cls = [string]$u.DriverClass
    maker = [string]$u.DriverManufacturer; size = [int64]$u.MaxDownloadSize
    date = $(if ($u.DriverVerDate) {{ $u.DriverVerDate.ToString('yyyy-MM-dd') }} else {{ '' }}) }}
}})
ConvertTo-Json -InputObject $list -Compress -Depth 3"""

_INSTALL_PS = """$ErrorActionPreference = 'Stop'
try {
$want = @(__IDS__)
$s = New-Object -ComObject Microsoft.Update.Session
$s.ClientApplicationID = 'Assistant'
$r = $s.CreateUpdateSearcher().Search("__CRITERIA__")
$todo = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $r.Updates) { if ($want -contains $u.Identity.UpdateID) {
  if (-not $u.EulaAccepted) { $u.AcceptEula() }
  [void]$todo.Add($u) } }
$out = [ordered]@{ found = $todo.Count; reboot = $false; items = @() }
if ($todo.Count -gt 0) {
  $d = $s.CreateUpdateDownloader(); $d.Updates = $todo; [void]$d.Download()
  $i = $s.CreateUpdateInstaller(); $i.Updates = $todo; $res = $i.Install()
  $out.reboot = [bool]$res.RebootRequired
  $out.items = @(for ($k = 0; $k -lt $todo.Count; $k++) {
    [pscustomobject]@{ title = $todo.Item($k).Title; code = [int]$res.GetUpdateResult($k).ResultCode } })
}
ConvertTo-Json -InputObject $out -Compress -Depth 4
} catch { ConvertTo-Json -InputObject @{ error = $_.Exception.Message } -Compress }"""

_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
#: IUpdateInstaller result codes
RESULT = {0: "not started", 1: "in progress", 2: "installed", 3: "installed with errors", 4: "failed", 5: "aborted"}
SEARCH_TIMEOUT_S = 300.0
INSTALL_TIMEOUT_S = 3600.0


def install_script(ids: list[str]) -> str:
    """The elevated script: installs exactly these update ids (the ones the user confirmed), nothing else."""
    bad = [i for i in ids if not _GUID.match(i)]
    if bad or not ids:
        raise ToolError("Invalid driver update id.")
    return _INSTALL_PS.replace("__IDS__", ",".join(f"'{i}'" for i in ids)).replace("__CRITERIA__", CRITERIA)


def _json(text: str) -> Any:
    """The JSON line of a PowerShell output (ignores anything printed before it)."""
    for line in reversed([x.strip() for x in (text or "").splitlines() if x.strip()]):
        if line[:1] in "[{":
            return json.loads(line)
    raise ValueError("no JSON in the output")


def search(ctx: ToolContext) -> list[dict[str, Any]]:
    out = ctx.platform.shell.run(SEARCH_PS, "powershell", SEARCH_TIMEOUT_S, ctx.task.cancel)
    if out.timed_out:
        raise ToolError("Windows Update didn't answer in time - try again later.")
    try:
        data = _json(out.stdout)
    except (ValueError, json.JSONDecodeError):
        msg = (out.stderr or out.stdout or "").strip().splitlines()
        raise ToolError("Windows Update couldn't be checked" + (f": {msg[-1][:200]}" if msg else ".")) from None
    items = data if isinstance(data, list) else [data]
    return [{"id": str(u.get("id", "")), "title": str(u.get("title", "")).strip(), "class": u.get("cls") or "",
             "maker": u.get("maker") or "", "date": u.get("date") or "", "size": int(u.get("size") or 0)}
            for u in items if isinstance(u, dict) and _GUID.match(str(u.get("id", "")))]


def _mb(n: int) -> str:
    return f"{n / 2**20:.0f} MB" if n >= 2**20 else ("< 1 MB" if n else "")


def describe_update(u: dict[str, Any]) -> str:
    extra = ", ".join(x for x in (u.get("date", ""), _mb(u.get("size", 0))) if x)
    return u["title"] + (f" ({extra})" if extra else "")


def _gpu_note() -> str:
    try:
        from ..core import hardware

        gpus = hardware.nvidia_gpus()
    except Exception:
        return ""
    if not gpus:
        return ""
    g = gpus[0]
    return (f"Graphics: {g.name}, driver {g.driver}. Windows Update usually has older NVIDIA drivers - the newest "
            "Game Ready driver comes from the NVIDIA App.")


# ---------------------------------------------------------------------------------------------- the tool

def _plan(a: dict[str, Any], ctx: ToolContext) -> Plan:
    updates = search(ctx)        # (read-only; needed to show the exact list before asking)
    if a["action"] == "check" or not updates:
        return Plan("Check Windows Update for driver updates", capability="terminal.read",
                    state={"updates": updates})
    total = sum(u["size"] for u in updates)
    return Plan(f"Install {len(updates)} driver update(s) from Windows Update"
                f"{f' ({_mb(total)})' if total else ''} - as administrator: Windows will ask",
                [describe_update(u) for u in updates], risks={Risk.INSTALL, Risk.ADMIN},
                capability="software.install", state={"updates": updates})


def _run(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    updates: list[dict[str, Any]] = plan.state["updates"]
    gpu = _gpu_note()
    data: dict[str, Any] = {"updates": updates, "gpu": gpu, "started": False}
    if not updates:
        return ToolResult(True, "Windows Update has no driver updates - your drivers are up to date."
                          + (f"\n{gpu}" if gpu else ""), data=data)
    lines = [f"- {describe_update(u)}" for u in updates]
    if a["action"] == "check":
        return ToolResult(True, f"{len(updates)} driver update(s) available:\n" + "\n".join(lines)
                          + (f"\n{gpu}" if gpu else ""), data=data)
    script = install_script([u["id"] for u in updates])
    shell, bus = ctx.platform.shell, ctx.bus

    def install() -> None:      # (own token: a later "stop" must not cut a driver installation in half)
        title = "Driver updates"
        try:
            out = shell.run_elevated(script, INSTALL_TIMEOUT_S, CancelToken())
            if out.code == 1223:
                msg, level = "Not installed - the administrator prompt was cancelled.", "warning"
            else:
                try:
                    res = _json(out.stdout)
                except (ValueError, json.JSONDecodeError):
                    tail = (out.stderr or out.stdout or "").strip().splitlines()
                    raise RuntimeError(tail[-1][:200] if tail else f"exit code {out.code}") from None
                if res.get("error"):
                    raise RuntimeError(f"Windows Update: {str(res['error'])[:200]}")
                items = res.get("items") or []
                done = [i for i in items if i.get("code") in (2, 3)]
                failed = [i for i in items if i.get("code") not in (2, 3)]
                msg = f"Installed {len(done)} of {len(updates)} driver update(s)."
                if failed:
                    msg += " Failed: " + "; ".join(f"{i.get('title', '?')} ({RESULT.get(i.get('code'), '?')})"
                                                   for i in failed)
                if res.get("reboot"):
                    msg += " Restart the PC to finish."
                level = "warning" if failed or not done else "info"
        except Exception as e:
            log.exception("driver installation failed")
            msg, level = f"The driver installation failed: {e}", "warning"
        log.info("driver updates: %s", msg)
        if bus is not None:
            bus.publish(Topic.NOTIFY, title=title, message=msg, level=level)

    threading.Thread(target=install, name="driver-updates", daemon=True).start()
    data["started"] = True
    return ToolResult(True, f"Installing {len(updates)} driver update(s) in the background (Windows asks for "
                      "administrator rights). A notification says when they're done - some need a restart.\n"
                      + "\n".join(lines), data=data)


def register(reg: ToolRegistry) -> None:
    reg.register(Tool(
        "driver_updates", "Check Windows Update for driver updates, or install them (install always asks the user "
                          "and shows the exact list; runs as administrator via the UAC prompt).",
        obj(("action",), action=Str("check = list available driver updates; install = install them",
                                    enum=("check", "install"))),
        "terminal.read", _plan, _run))


__all__ = ["SEARCH_PS", "install_script", "register", "search"]
