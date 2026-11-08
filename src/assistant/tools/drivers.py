"""Driver updates: Windows Update, plus the makers' own (``vendor_drivers.py``) - the newest NVIDIA graphics driver
straight from NVIDIA, and Dell Command | Update where it is installed.

* ``check``: what is available - Windows Update's driver updates (no admin rights needed), NVIDIA's newest driver
  for this GPU against the installed one, and the makers' apps found on this PC.
* ``install``: shows exactly that list (with NVIDIA's download address and the commands), and after the user's yes
  installs *only those* with administrator rights - ONE Windows UAC prompt for all of them. It runs in the
  background - downloads can take minutes - and a notification reports the result. An installation is never
  interrupted half-way.
* ``source``: ``all`` (default), ``windows_update`` or ``nvidia``.
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
from . import vendor_drivers as vendor
from .base import Plan, Tool, ToolContext, ToolError, ToolRegistry, ToolResult
from .schema import Str, obj

log = logging.getLogger(__name__)

CRITERIA = "IsInstalled=0 and Type='Driver' and IsHidden=0"
SOURCES = ("all", "windows_update", "nvidia")

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

# the Windows Update piece of the elevated script: installs exactly these update ids
_WU_PART = """$out.windows = & { try {
$want = @(__IDS__)
$s = New-Object -ComObject Microsoft.Update.Session; $s.ClientApplicationID = 'Assistant'
$r = $s.CreateUpdateSearcher().Search("__CRITERIA__")
$todo = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $r.Updates) { if ($want -contains $u.Identity.UpdateID) {
  if (-not $u.EulaAccepted) { $u.AcceptEula() }; [void]$todo.Add($u) } }
$o = [ordered]@{ found = $todo.Count; reboot = $false; items = @() }
if ($todo.Count -gt 0) {
  $d = $s.CreateUpdateDownloader(); $d.Updates = $todo; [void]$d.Download()
  $i = $s.CreateUpdateInstaller(); $i.Updates = $todo; $res = $i.Install()
  $o.reboot = [bool]$res.RebootRequired
  $o.items = @(for ($k = 0; $k -lt $todo.Count; $k++) {
    [pscustomobject]@{ title = $todo.Item($k).Title; code = [int]$res.GetUpdateResult($k).ResultCode } }) }
$o } catch { @{ error = $_.Exception.Message } } }"""

_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
#: IUpdateInstaller result codes
RESULT = {0: "not started", 1: "in progress", 2: "installed", 3: "installed with errors", 4: "failed", 5: "aborted"}
SEARCH_TIMEOUT_S = 300.0
INSTALL_TIMEOUT_S = 3600.0


def elevated_script(ids: list[str], nvidia: vendor.NvidiaDriver | None = None, dell: str | None = None) -> str:
    """The ONE elevated script: exactly these Windows Update ids (the ones the user confirmed), the NVIDIA driver,
    Dell Command | Update - each in its own try, results as one JSON line."""
    if any(not _GUID.match(i) for i in ids):
        raise ToolError("Invalid driver update id.")
    parts = []
    if ids:
        parts.append(_WU_PART.replace("__IDS__", ",".join(f"'{i}'" for i in ids)).replace("__CRITERIA__", CRITERIA))
    if nvidia is not None:
        parts.append(vendor.nvidia_part(nvidia))
    if dell is not None:
        parts.append(vendor.dell_part(dell))
    if not parts:
        raise ToolError("Nothing to install.")
    return ("$ErrorActionPreference = 'Stop'\n$out = [ordered]@{}\n" + "\n".join(parts)
            + "\nConvertTo-Json -InputObject $out -Compress -Depth 5")


def search(ctx: ToolContext) -> list[dict[str, Any]]:
    out = ctx.platform.shell.run(SEARCH_PS, "powershell", SEARCH_TIMEOUT_S, ctx.task.cancel)
    if out.timed_out:
        raise ToolError("Windows Update didn't answer in time - try again later.")
    try:
        data = vendor.json_line(out.stdout)
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


def nvidia_line(d: vendor.NvidiaDriver) -> str:
    have = f"driver {d.installed}" if d.installed else "driver version unknown"
    if d.problem:
        return f"NVIDIA {d.gpu}: {have} - couldn't check for a newer one: {d.problem}."
    if d.newer:
        return (f"NVIDIA {d.gpu}: {have} installed - {d.name or 'driver'} {d.latest} is available"
                + (f" ({d.size})" if d.size else "") + ".")
    return f"NVIDIA {d.gpu}: {have} - the newest."


def _nvidia_details(d: vendor.NvidiaDriver) -> list[str]:
    return [f"NVIDIA: {d.name or 'driver'} {d.latest} for the {d.gpu} (you have {d.installed or '?'})"
            + (f", {d.size}" if d.size else "") + f" from {d.host}: {d.url}",
            f"  only if signed by NVIDIA, then silently: {d.file} {' '.join(vendor.NV_ARGS)} - close games first, "
            "the screen may go dark for a moment"]


def _n_updates(n: int, de: bool) -> str:
    return (f"{n} Treiber-Update" + ("s" if n != 1 else "")) if de else (f"{n} driver update" + ("s" if n != 1 else ""))


def _apps_hint(data: dict[str, Any], de: bool) -> str:
    """The first maker app that may update the rest (not NVIDIA's, when NVIDIA was just checked)."""
    nv_ok = bool(data.get("nvidia")) and not (data.get("nvidia") or {}).get("problem")
    apps = [a for a in data.get("apps") or [] if not (nv_ok and re.match(r"(?i)nvidia|geforce", a))]
    if not apps:
        return ""
    return (f" Für den Rest gibt es {apps[0]} - sag „öffne {apps[0]}“." if de
            else f" {apps[0]} can check the rest - say “open {apps[0]}”.")


def spoken(data: dict[str, Any], lang: str) -> str:
    """What the assistant says about it (voice command, or a routine step)."""
    de = lang == "de"
    ups = data.get("updates") or []
    nv = data.get("nvidia") or {}
    nv_new = bool(nv.get("newer"))
    if data.get("started"):
        n = len(ups) + int(nv_new)
        what = _n_updates(n, de) if n else ("die Treiber-Updates" if de else "the driver updates")
        return (f"Ich installiere {what} im Hintergrund und sage Bescheid, wenn es fertig ist." if de
                else f"Installing {what} in the background - I'll tell you when it's done.")
    if data.get("source") == "nvidia":
        if not nv:
            return "In diesem PC ist keine NVIDIA-Grafikkarte." if de else "There's no NVIDIA graphics card in this PC."
        if nv.get("problem"):
            return ("Den NVIDIA-Treiber konnte ich gerade nicht prüfen." if de
                    else f"I couldn't check the NVIDIA driver: {nv['problem']}.")
        if nv_new:
            return (f"NVIDIA-Treiber {nv['latest']} ist verfügbar, du hast {nv['installed']}. Sag „aktualisiere meinen "
                    "NVIDIA-Treiber“, um ihn zu installieren." if de else
                    f"NVIDIA driver {nv['latest']} is available - you have {nv['installed']}. Say “update my NVIDIA "
                    "driver” to install it.")
        return (f"Dein NVIDIA-Treiber {nv['installed']} ist der neueste." if de
                else f"Your NVIDIA driver {nv['installed']} is the newest.")
    items = [u["title"] for u in ups] + ([f"NVIDIA {nv['latest']}" if de else f"NVIDIA driver {nv['latest']}"]
                                         if nv_new else [])
    if not items:
        if nv and not nv.get("problem"):
            base = ("Deine Treiber sind aktuell - Windows Update und NVIDIA." if de
                    else "Your drivers are up to date - Windows Update and NVIDIA.")
        else:
            base = ("Laut Windows Update sind deine Treiber aktuell." if de
                    else "Windows Update says your drivers are up to date.")
        return base + _apps_hint(data, de)
    names = ", ".join(items[:3]) + (" …" if len(items) > 3 else "")
    return (f"{_n_updates(len(items), True)} verfügbar: {names}. Sag „aktualisiere meine Treiber“, um sie zu "
            "installieren." if de else
            f"{_n_updates(len(items), False)} available: {names}. Say “update my drivers” to install them.")


def _result(ok: bool, text: str, data: dict[str, Any]) -> ToolResult:
    data["speak"] = {"en": spoken(data, "en"), "de": spoken(data, "de")}
    return ToolResult(ok, text, data=data)


# ---------------------------------------------------------------------------------------------- the tool

def _gather(source: str, ctx: ToolContext) -> dict[str, Any]:
    """What each source has (read-only: needed to show the exact list before asking)."""
    nv = vendor.nvidia_driver(ctx) if source in ("all", "nvidia") else None
    dell = vendor.dell_command_update() if source == "all" else None
    updates: list[dict[str, Any]] = []
    wu_error = ""
    if source in ("all", "windows_update"):
        try:
            updates = search(ctx)
        except ToolError as e:
            if source == "windows_update" or (nv is None and dell is None):
                raise                                     # (nothing else to report)
            wu_error = str(e)
    return {"source": source, "updates": updates, "wu_error": wu_error, "nvidia": nv, "dell": dell,
            "apps": vendor.maker_apps(ctx)}


def _plan(a: dict[str, Any], ctx: ToolContext) -> Plan:
    st = _gather(a.get("source") or "all", ctx)
    nv: vendor.NvidiaDriver | None = st["nvidia"]
    updates, dell = st["updates"], st["dell"]
    nv_new = nv is not None and nv.newer
    if a["action"] == "check" or not (updates or nv_new or dell):
        return Plan("Check for driver updates", capability="terminal.read", state=st)
    details = [f"Windows Update: {describe_update(u)}" for u in updates]
    what = [f"{len(updates)} from Windows Update"] if updates else []
    if nv_new:
        details += _nvidia_details(nv)
        what.append(f"NVIDIA {nv.latest}")
    if dell:
        details.append(f"Dell Command | Update - the driver updates it finds (no BIOS or firmware): "
                       f"{vendor.dell_command(dell)}")
        what.append("Dell Command | Update")
    total = sum(u["size"] for u in updates)
    return Plan(f"Install driver updates ({', '.join(what)}{f', {_mb(total)}' if total else ''})"
                " - as administrator: Windows will ask once", details, risks={Risk.INSTALL, Risk.ADMIN},
                capability="software.install", state=st)


def _check_text(st: dict[str, Any]) -> str:
    updates, nv, source = st["updates"], st["nvidia"], st["source"]
    lines = []
    if st["wu_error"]:
        lines.append(st["wu_error"])
    elif updates:
        lines.append(f"{len(updates)} driver update(s) available from Windows Update:\n"
                     + "\n".join(f"- {describe_update(u)}" for u in updates))
    elif source != "nvidia":
        lines.append("Windows Update has no driver updates - your drivers are up to date."
                     if nv is None or not nv.newer else "Windows Update has no driver updates.")
    if nv is not None:
        lines.append(nvidia_line(nv))
    elif source == "nvidia":
        lines.append("There's no NVIDIA graphics card in this PC.")
    if st["dell"]:
        lines.append("Dell Command | Update is installed: installing runs it too (driver updates only - no BIOS or "
                     "firmware).")
    if st["apps"]:
        lines.append("The makers' apps on this PC: " + ", ".join(st["apps"])
                     + " - say “open …” to use one for its other updates.")
    return "\n".join(lines)


def _install_message(out: Any, st: dict[str, Any]) -> tuple[str, str]:
    """The notification after the elevated step: one sentence per source."""
    if out.code == 1223:
        return "Not installed - the administrator prompt was cancelled.", "warning"
    try:
        res = vendor.json_line(out.stdout)
    except (ValueError, json.JSONDecodeError):
        tail = (out.stderr or out.stdout or "").strip().splitlines()
        raise RuntimeError(tail[-1][:200] if tail else f"exit code {out.code}") from None
    if not isinstance(res, dict) or res.get("error"):
        raise RuntimeError(str(res.get("error") if isinstance(res, dict) else res)[:200])
    msgs: list[str] = []
    warn = reboot = False
    updates, nv, dell = st["updates"], st["nvidia"], st["dell"]
    if updates:
        w = res.get("windows") or {}
        if w.get("error"):
            msgs.append(f"Windows Update: {str(w['error'])[:200]}")
            warn = True
        else:
            items = w.get("items") or []
            done = [i for i in items if i.get("code") in (2, 3)]
            failed = [i for i in items if i.get("code") not in (2, 3)]
            m = f"Installed {len(done)} of {len(updates)} driver update(s)."
            if failed:
                m += " Failed: " + "; ".join(f"{i.get('title', '?')} ({RESULT.get(i.get('code'), '?')})"
                                             for i in failed)
            msgs.append(m)
            warn = warn or bool(failed or not done)
            reboot = reboot or bool(w.get("reboot"))
    if nv is not None and nv.newer:
        n = res.get("nvidia") or {}
        if n.get("error"):
            msgs.append(f"NVIDIA driver {nv.latest} not installed: {str(n['error'])[:200]}.")
            warn = True
        elif n.get("now") == nv.latest:
            msgs.append(f"NVIDIA driver {nv.latest} installed.")
        else:
            msgs.append(f"NVIDIA driver {nv.latest}: the installer finished (code {n.get('code')}), but the driver "
                        f"still says {n.get('now') or '?'} - restart the PC; if it stays, use the NVIDIA App.")
            warn = True
    if dell:
        d = res.get("dell") or {}
        if d.get("error"):
            msgs.append(f"Dell Command | Update: {str(d['error'])[:200]}.")
            warn = True
        else:
            code = int(d.get("code", -1))
            msgs.append(f"Dell Command | Update: {vendor.dell_result(code)}.")
            warn = warn or code not in (0, 1, 500)
            reboot = reboot or code == 1
    if reboot:
        msgs.append("Restart the PC to finish.")
    return " ".join(msgs) or "Nothing was installed.", "warning" if warn else "info"


def _run(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    st: dict[str, Any] = plan.state
    nv: vendor.NvidiaDriver | None = st["nvidia"]
    data: dict[str, Any] = {"source": st["source"], "updates": st["updates"], "dell": st["dell"], "apps": st["apps"],
                            "nvidia": nv.as_data() if nv is not None else None, "started": False}
    if plan.capability != "software.install":                 # check - or nothing to install
        return _result(True, _check_text(st), data)
    script = elevated_script([u["id"] for u in st["updates"]], nv if nv is not None and nv.newer else None,
                             st["dell"])
    shell, bus = ctx.platform.shell, ctx.bus

    def install() -> None:      # (own token: a later "stop" must not cut a driver installation in half)
        try:
            out = shell.run_elevated(script, INSTALL_TIMEOUT_S, CancelToken())
            msg, level = _install_message(out, st)
        except Exception as e:
            log.exception("driver installation failed")
            msg, level = f"The driver installation failed: {e}", "warning"
        log.info("driver updates: %s", msg)
        if bus is not None:
            bus.publish(Topic.NOTIFY, title="Driver updates", message=msg, level=level)

    threading.Thread(target=install, name="driver-updates", daemon=True).start()
    data["started"] = True
    return _result(True, "Installing in the background (Windows asks for administrator rights once). A notification "
                   "says when it's done - some need a restart:\n" + "\n".join(f"- {d}" for d in plan.details), data)


def register(reg: ToolRegistry) -> None:
    reg.register(Tool(
        "driver_updates", "Check for driver updates, or install them: Windows Update, the newest NVIDIA graphics "
                          "driver (straight from NVIDIA, like the NVIDIA App) and Dell Command | Update where "
                          "installed. Install always asks the user and shows the exact list; it runs as "
                          "administrator via one UAC prompt.",
        obj(("action",), action=Str("check = list available driver updates; install = install them",
                                    enum=("check", "install")),
            source=Str("all (default); windows_update; nvidia = only the NVIDIA graphics driver", enum=SOURCES)),
        "terminal.read", _plan, _run))


__all__ = ["SEARCH_PS", "SOURCES", "elevated_script", "register", "search", "spoken"]
