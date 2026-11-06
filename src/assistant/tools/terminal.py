"""Terminal tools: run_command (PowerShell/cmd), run_as_admin (UAC), winget install/uninstall/search.

The classifier decides which commands are harmless enough to run without
asking (``terminal.read``). It is an allowlist: a command is read-only only
if *every* pipeline segment starts with a known information command, there is
no redirection, no sub-expression/script block/.NET call/escape trickery, no
network access, and every path it mentions is readable. Everything else asks
(``terminal.run`` + risk "terminal_change"), showing the exact command.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..permissions.pathrules import PathError, PathNormalizer
from ..permissions.policy import Risk
from .base import Plan, Tool, ToolContext, ToolError, ToolRegistry, ToolResult
from .schema import Int, Str, obj

PS_READ = {
    "get-childitem", "gci", "ls", "dir", "get-content", "gc", "cat", "type", "get-item", "gi",
    "get-itemproperty", "gp", "get-itempropertyvalue", "gpv", "get-location", "gl", "pwd", "get-date",
    "get-process", "gps", "ps", "get-service", "gsv", "get-computerinfo", "get-volume", "get-psdrive", "gdr",
    "get-disk", "get-partition", "get-physicaldisk", "get-netadapter", "get-netipaddress",
    "get-netipconfiguration", "get-nettcpconnection", "get-netroute", "get-command", "gcm", "get-help",
    "get-member", "gm", "get-hotfix", "get-timezone", "get-culture", "get-uptime", "get-host", "get-alias",
    "gal", "get-filehash", "get-acl", "get-appxpackage", "get-ciminstance", "gcim", "get-wmiobject", "gwmi",
    "get-counter", "get-winevent", "get-printer", "get-pnpdevice", "get-scheduledtask", "get-localuser",
    "get-localgroup", "get-clipboard", "get-random", "select-object", "select", "where-object", "where",
    "sort-object", "sort", "measure-object", "measure", "group-object", "group", "format-table", "ft",
    "format-list", "fl", "format-wide", "fw", "out-string", "convertto-json", "convertto-csv",
    "convertfrom-json", "select-string", "sls", "test-path", "resolve-path", "rvpa", "split-path", "join-path",
    "write-output", "echo", "write", "write-host", "compare-object", "get-unique", "out-host", "out-null",
    "hostname", "whoami", "systeminfo", "ipconfig", "tasklist", "getmac", "where.exe", "ver",
}
CMD_READ = {
    "dir", "type", "echo", "ver", "vol", "where", "whoami", "hostname", "systeminfo", "ipconfig", "tasklist",
    "driverquery", "tree", "find", "findstr", "more", "getmac", "date", "time", "path", "set", "netstat",
    "nvidia-smi", "powercfg", "query",
}
#: commands whose arguments must be restricted to be read-only
ARG_RULES: dict[str, Any] = {
    "ipconfig": lambda args: all(a.lower() in ("/all", "/displaydns", "-all") for a in args),
    "date": lambda args: any(a.lower() == "/t" for a in args),
    "time": lambda args: any(a.lower() == "/t" for a in args),
    "powercfg": lambda args: bool(args) and args[0].lower() in (
        "/l", "/list", "/q", "/query", "/a", "/availablesleepstates", "/getactivescheme"),
    "nvidia-smi": lambda args: all(a.startswith(("--query", "--format", "-q", "-L", "--list")) for a in args),
    "set": lambda args: not args or all("=" not in a for a in args),
    "path": lambda args: not args,
    "netstat": lambda args: True,
    "query": lambda args: bool(args) and args[0].lower() in ("user", "session"),
}
NETWORK = {
    "ping", "tracert", "nslookup", "curl", "curl.exe", "wget", "iwr", "irm", "invoke-webrequest",
    "invoke-restmethod", "test-connection", "test-netconnection", "resolve-dnsname", "ftp", "ssh", "scp", "sftp",
    "telnet", "bitsadmin", "start-bitstransfer", "certutil", "net", "pathping", "send-mailmessage",
}
INSTALLERS = {"winget", "choco", "scoop", "msiexec", "install-package", "install-module", "install-script",
              "uninstall-package", "add-appxpackage", "remove-appxpackage", "pip", "pip3", "npm", "pnpm", "yarn"}
SUSPICIOUS = [
    (re.compile(r">"), "redirects output to a file"),
    (re.compile(r"\$\("), "contains a sub-expression"),
    (re.compile(r"[{}]"), "contains a script block"),
    (re.compile(r"::"), "calls .NET directly"),
    (re.compile(r"`"), "contains escape characters"),
    (re.compile(r"\^"), "contains cmd escape characters"),
    (re.compile(r"(?<![\w-])(iex|invoke-expression|invoke-command|icm|start-process|saps|start|"
                r"invoke-item|ii|add-type|new-object)(?![\w-])", re.I), "can start other code"),
    (re.compile(r"-enc(odedcommand)?\b", re.I), "contains an encoded command"),
    (re.compile(r"@[({]"), "contains an array/hash expression"),
    (re.compile(r"\[[\w.]+\]"), "contains a type literal"),
]


@dataclass
class Classification:
    read_only: bool
    capability: str
    risks: set[Risk] = field(default_factory=set)
    reason: str = ""
    paths: list[str] = field(default_factory=list)


def _split_segments(cmd: str) -> list[str]:
    return [s.strip() for s in re.split(r"\|\||&&|[|;\n\r]|(?<!&)&(?!&)", cmd) if s.strip()]


def _tokens(segment: str) -> list[str]:
    return [t.strip("'\"") for t in re.findall(r"\"[^\"]*\"|'[^']*'|\S+", segment)]


def _looks_like_path(tok: str) -> bool:
    return bool(re.match(r"^([A-Za-z]:|\\\\|~|\.{1,2}[\\/]|%\w+%|\$env:)", tok)) or "\\" in tok or "/" in tok


def classify(command: str, shell: str, normalizer: PathNormalizer | None = None) -> Classification:
    cmd = command.strip()
    low = cmd.lower()
    if re.search(r"(?<![\w-])runas(?![\w-])|-verb\s+runas|\bsudo\b", low):
        return Classification(False, "system.admin", {Risk.ADMIN}, "asks for administrator rights")
    segments = _split_segments(cmd)
    if not segments:
        raise ToolError("empty command")

    firsts = []
    for seg in segments:
        toks = _tokens(seg)
        if not toks:
            continue
        first = toks[0].lower()
        first = first[:-4] if first.endswith(".exe") and first not in ("where.exe",) else first
        firsts.append((first, toks[1:]))
    if any(f in INSTALLERS for f, _ in firsts):
        return Classification(False, "software.install", {Risk.INSTALL}, "installs or removes software")

    change = Classification(False, "terminal.run", {Risk.TERMINAL_CHANGE})
    for pattern, why in SUSPICIOUS:
        if pattern.search(cmd):
            change.reason = why
            return change
    if re.search(r"\$(?!env:[A-Za-z_]\w*)", cmd):
        change.reason = "uses variables"
        return change
    allowed = PS_READ if shell == "powershell" else CMD_READ
    paths: list[str] = []
    for first, args in firsts:
        if first in NETWORK:
            return Classification(False, "terminal.run", set(), "uses the network")
        if first not in allowed:
            change.reason = f"'{first}' may change something"
            return change
        rule = ARG_RULES.get(first)
        if rule is not None and not rule([a for a in args]):
            change.reason = f"'{first}' with these options may change something"
            return change
        for a in args:
            if a.startswith("-") or (shell == "cmd" and a.startswith("/") and len(a) <= 20 and "\\" not in a):
                continue
            if _looks_like_path(a):
                if "*" in a or "?" in a:
                    change.reason = "uses wildcards in paths"
                    return change
                if normalizer is not None:
                    try:
                        normalizer.normalize(_env_path(a))
                    except PathError:
                        change.reason = f"unclear path {a!r}"
                        return change
                paths.append(a)
    return Classification(True, "terminal.read", set(), "read-only", paths)


def _env_path(tok: str) -> str:
    m = re.match(r"^\$env:([A-Za-z_]\w*)(.*)$", tok, re.I)
    return f"%{m.group(1)}%{m.group(2)}" if m else tok


def _clip(text: str, limit: int = 4000) -> str:
    text = text.replace("\r\n", "\n").strip()
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n… [{len(text) - limit} characters omitted] …\n{text[-half:]}"


# ----------------------------------------------------------------------------- tools

def _plan_run(a: dict[str, Any], ctx: ToolContext) -> Plan:
    shell = a["shell"]
    c = classify(a["command"], shell, ctx.normalizer)
    prompt = "PS>" if shell == "powershell" else "cmd>"
    details = [f"{prompt} {a['command']}"]
    if c.reason and not c.read_only:
        details.append(f"why I ask: {c.reason}")
    return Plan("Run a terminal command", details, [(_env_path(p), "read") for p in c.paths], c.risks,
                capability=c.capability)


def _format_output(out: Any) -> ToolResult:
    body = _clip((out.stdout or "") + (("\n[stderr]\n" + out.stderr) if out.stderr.strip() else ""))
    if out.timed_out:
        return ToolResult(False, f"The command timed out and was stopped.\n{body}".strip(), untrusted=True)
    status = "ok" if out.code == 0 else f"exit code {out.code}"
    return ToolResult(out.code == 0, f"[{status}]\n{body or '(no output)'}", data={"code": out.code},
                      untrusted=True)


def _run_run(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    out = ctx.platform.shell.run(a["command"], a["shell"], float(a["timeout_s"]), ctx.task.cancel)
    return _format_output(out)


def _plan_admin(a: dict[str, Any], ctx: ToolContext) -> Plan:
    return Plan("Run as administrator (Windows will show a UAC prompt)", [f"PS> {a['command']}"],
                risks={Risk.ADMIN}, capability="system.admin")


def _run_admin(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    out = ctx.platform.shell.run_elevated(a["command"], float(a["timeout_s"]), ctx.task.cancel)
    return _format_output(out)


_WINGET_ID = re.compile(r"^[\w.+-]{1,128}$")


def _plan_winget(a: dict[str, Any], ctx: ToolContext) -> Plan:
    action, pkg = a["action"], a["package"].strip()
    if action == "search":
        return Plan(f"Search for apps: {pkg}", [f"winget search \"{pkg}\""], capability="terminal.read")
    if not _WINGET_ID.match(pkg):
        raise ToolError("Use the exact winget package id (search first), e.g. 'Spotify.Spotify'.")
    return Plan(f"{action.capitalize()} {pkg}", [f"winget {action} --id {pkg} --exact"], risks={Risk.INSTALL},
                capability="software.install")


def _run_winget(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    action, pkg = a["action"], a["package"].strip()
    if action == "search":
        safe = pkg.replace('"', "")
        cmd = f'winget search "{safe}" --accept-source-agreements --disable-interactivity'
        timeout = 60.0
    else:
        extra = " --accept-package-agreements" if action == "install" else ""
        cmd = (f"winget {action} --id {pkg} --exact --silent --accept-source-agreements"
               f"{extra} --disable-interactivity")
        timeout = 900.0
    return _format_output(ctx.platform.shell.run(cmd, "cmd", timeout, ctx.task.cancel))


def register(reg: ToolRegistry) -> None:
    reg.register(Tool(
        "run_command",
        "Run a PowerShell (default) or cmd command and return its output. Harmless read-only commands run "
        "directly; anything that could change the system needs the user's confirmation.",
        obj(("command",), command=Str("The exact command", min_len=1, max_len=4000),
            shell=Str("Which shell", enum=("powershell", "cmd"), default="powershell"),
            timeout_s=Int("Stop after this many seconds", min=1, max=600, default=30)),
        "terminal.run", _plan_run, _run_run))
    reg.register(Tool(
        "run_as_admin", "Run a PowerShell command with administrator rights (Windows shows a UAC prompt).",
        obj(("command",), command=Str("The exact command", min_len=1, max_len=4000),
            timeout_s=Int("Stop waiting after this many seconds", min=5, max=900, default=120)),
        "system.admin", _plan_admin, _run_admin))
    reg.register(Tool(
        "winget", "Search, install or uninstall software with winget (install/uninstall need confirmation).",
        obj(("action", "package"), action=Str("What to do", enum=("search", "install", "uninstall")),
            package=Str("Search text, or the exact package id for install/uninstall", min_len=1, max_len=200)),
        "software.install", _plan_winget, _run_winget))
