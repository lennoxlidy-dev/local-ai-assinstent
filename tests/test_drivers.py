"""Driver updates: Windows Update is asked through PowerShell (faked here); installing needs a yes + UAC."""

import json
import threading

import pytest

from assistant.core.assistant import Assistant
from assistant.intents.fastpath import FastPath
from assistant.permissions.gate import ConfirmChoice, Task
from assistant.permissions.policy import Risk
from assistant.tools.base import ToolError
from assistant.tools.drivers import install_script
from assistant.tools.platform import CommandOutput

INTEL = {"id": "11111111-2222-3333-4444-555555555555", "title": "Intel Corporation - Display - 31.0.101.5186",
         "cls": "Display", "maker": "Intel Corporation", "size": 734003200, "date": "2026-09-30"}
REALTEK = {"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "title": "Realtek Semiconductor Corp. - Audio - 6.0.9600.1",
           "cls": "MEDIA", "maker": "Realtek", "size": 52428800, "date": "2026-08-12"}


class WindowsUpdate:
    """Stands in for PowerShell: the search script lists ``available``; the elevated script 'installs'."""

    def __init__(self, shell, available, install_codes=None, uac_cancelled=False, reboot=True):
        self.available, self.codes, self.uac, self.reboot = available, install_codes, uac_cancelled, reboot
        self.searches, self.elevated = 0, []
        self.done = threading.Event()
        shell.run = self.run
        shell.run_elevated = self.run_elevated

    def run(self, command, shell, timeout, cancel):
        assert "Microsoft.Update.Session" in command and "Type='Driver'" in command
        self.searches += 1
        return CommandOutput(0, json.dumps(self.available), "")

    def run_elevated(self, command, timeout, cancel):
        self.elevated.append(command)
        if self.uac:
            return CommandOutput(1223, "", "The administrator prompt was cancelled.")
        ids = [u["id"] for u in self.available if u["id"] in command]
        codes = self.codes or [2] * len(ids)
        items = [{"title": u["title"], "code": c} for u, c in zip(self.available, codes, strict=False)]
        return CommandOutput(0, "Downloading...\n" + json.dumps({"found": len(ids), "reboot": self.reboot,
                                                                 "items": items}), "")


def _notes(system):
    got, done = [], threading.Event()

    def on(e):
        if e.data.get("title") == "Driver updates":
            got.append(e.data["message"])
            done.set()

    system.ctx.bus.subscribe("ui.notify", on)
    return got, done


def test_check_lists_updates_without_asking(ts):
    system, p, conf, _ = ts
    wu = WindowsUpdate(p.shell, [INTEL, REALTEK])
    r = system.invoke("driver_updates", {"action": "check"}, Task())
    assert r.ok and "2 driver update(s) available" in r.text and "Intel Corporation - Display" in r.text
    assert "700 MB" in r.text and conf.requests == [] and wu.elevated == []


def test_install_shows_the_exact_list_and_installs_only_those(ts):
    system, p, conf, _ = ts
    wu = WindowsUpdate(p.shell, [INTEL, REALTEK])
    got, done = _notes(system)
    r = system.invoke("driver_updates", {"action": "install"}, Task())
    assert r.ok and r.data["started"] and "in the background" in r.text
    req = conf.requests[-1]                                    # asked first, with the exact updates
    assert req.action.capability == "software.install" and {Risk.INSTALL, Risk.ADMIN} <= req.action.risks
    assert any("Intel Corporation - Display" in d for d in req.action.details)
    assert any("Realtek" in d for d in req.action.details) and "administrator" in req.action.summary
    assert done.wait(5)
    assert got == ["Installed 2 of 2 driver update(s). Restart the PC to finish."]
    script = wu.elevated[0]
    assert INTEL["id"] in script and REALTEK["id"] in script and "CreateUpdateInstaller" in script


def test_declined_installs_nothing(ts):
    system, p, conf, _ = ts
    conf.choice = ConfirmChoice.DENY
    wu = WindowsUpdate(p.shell, [INTEL])
    r = system.invoke("driver_updates", {"action": "install"}, Task())
    assert not r.ok and wu.elevated == []


def test_failures_and_cancelled_uac_are_reported(ts):
    system, p, _, _ = ts
    WindowsUpdate(p.shell, [INTEL, REALTEK], install_codes=[2, 4], reboot=False)
    got, done = _notes(system)
    system.invoke("driver_updates", {"action": "install"}, Task())
    assert done.wait(5) and got[0].startswith("Installed 1 of 2") and "Realtek" in got[0] and "failed" in got[0]
    WindowsUpdate(p.shell, [INTEL], uac_cancelled=True)
    got2, done2 = _notes(system)
    system.invoke("driver_updates", {"action": "install"}, Task())
    assert done2.wait(5) and "administrator prompt was cancelled" in got2[0]


def test_an_installer_error_is_passed_on(ts):
    system, p, _, _ = ts
    wu = WindowsUpdate(p.shell, [INTEL])
    p.shell.run_elevated = lambda *a: CommandOutput(0, json.dumps({"error": "0x80240022 all updates failed"}), "")
    got, done = _notes(system)
    system.invoke("driver_updates", {"action": "install"}, Task())
    assert done.wait(5) and "Windows Update: 0x80240022" in got[0] and wu.searches == 1


def test_nothing_to_update(ts):
    system, p, conf, _ = ts
    wu = WindowsUpdate(p.shell, [])
    r = system.invoke("driver_updates", {"action": "install"}, Task())
    assert r.ok and "up to date" in r.text and conf.requests == [] and wu.elevated == []


def test_windows_update_errors_are_explained(ts):
    system, p, _, _ = ts
    p.shell.run = lambda *a: CommandOutput(1, "", "Exception from HRESULT: 0x8024402C")
    r = system.invoke("driver_updates", {"action": "check"}, Task())
    assert not r.ok and "Windows Update couldn't be checked" in r.text and "0x8024402C" in r.text


def test_a_single_update_object_is_understood(ts):
    system, p, _, _ = ts
    p.shell.run = lambda *a: CommandOutput(0, json.dumps(INTEL), "")    # in case PowerShell unwraps the list
    r = system.invoke("driver_updates", {"action": "check"}, Task())
    assert r.ok and "1 driver update(s)" in r.text


def test_install_script_only_takes_update_ids():
    assert "'11111111-2222-3333-4444-555555555555'" in install_script([INTEL["id"]])
    for bad in (["x'; Remove-Item C:\\ -Recurse; '"], []):
        with pytest.raises(ToolError):
            install_script(bad)


@pytest.mark.parametrize("said,intent", [
    ("update all my drivers", "drivers.update"), ("Jarvis, can you update my drivers?", "drivers.update"),
    ("install the driver updates", "drivers.update"), ("aktualisiere meine Treiber", "drivers.update"),
    ("kannst du meine Treiber aktualisieren", "drivers.update"), ("check for driver updates", "drivers.check"),
    ("are my drivers up to date", "drivers.check"), ("gibt es neue Treiber-Updates", "drivers.check"),
    ("sind meine Treiber aktuell", "drivers.check")])
def test_voice_commands(said, intent):
    fp = FastPath(lambda name: None, lambda: ["Jarvis"], lambda: "en")
    m = fp.match(said)
    assert m is not None and m.intent == intent and m.tool == "driver_updates"


def test_spoken_answers(ts):
    system, p, conf, _ = ts
    WindowsUpdate(p.shell, [INTEL, REALTEK])
    a = Assistant(system.ctx, system, None)
    r = a.handle("check for driver updates")
    assert r.via == "fast" and r.text.startswith("2 driver updates available: Intel Corporation - Display")
    r = a.handle("update my drivers")
    assert r.ok and r.text == "Installing 2 driver updates in the background - I'll tell you when it's done."
    WindowsUpdate(p.shell, [])
    assert a.handle("are my drivers up to date").text.startswith("Windows Update says your drivers are up to date.")
