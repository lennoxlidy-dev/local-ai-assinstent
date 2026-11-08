"""Driver updates: Windows Update and NVIDIA's driver service are asked through PowerShell (faked here), Dell
Command | Update is found on disk; installing needs a yes + ONE UAC prompt for all of them."""

import json
import threading

import pytest

from assistant.core import hardware
from assistant.core.assistant import Assistant
from assistant.intents.fastpath import FastPath
from assistant.permissions.gate import ConfirmChoice, Task
from assistant.permissions.policy import Risk
from assistant.tools import vendor_drivers as vendor
from assistant.tools.base import ToolError
from assistant.tools.drivers import elevated_script
from assistant.tools.platform import AppEntry, CommandOutput

INTEL = {"id": "11111111-2222-3333-4444-555555555555", "title": "Intel Corporation - Display - 31.0.101.5186",
         "cls": "Display", "maker": "Intel Corporation", "size": 734003200, "date": "2026-09-30"}
REALTEK = {"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "title": "Realtek Semiconductor Corp. - Audio - 6.0.9600.1",
           "cls": "MEDIA", "maker": "Realtek", "size": 52428800, "date": "2026-08-12"}
NV_URL = "https://us.download.nvidia.com/Windows/572.16/572.16-notebook-win10-win11-64bit-international-dch-whql.exe"


def nv_answer(version="572.16", url=NV_URL, success="1"):
    """What NVIDIA's driver service answers (the fields the nvidia-update script and we read)."""
    return json.dumps({"Success": success, "IDS": [{"downloadInfo": {
        "Success": "1", "ID": "240190", "Version": version, "DownloadURL": url, "Name": "GeForce%20Game%20Ready%20Driver",
        "DownloadURLFileSize": "665.43 MB", "ReleaseDateTime": "Thu Nov 5, 2026", "IsWHQL": "1"}}]})


@pytest.fixture(autouse=True)
def no_gpu_no_dell(monkeypatch):
    """By default: no NVIDIA card and no Dell tool (tests add them)."""
    monkeypatch.setattr(hardware, "nvidia_gpus", lambda: [])
    monkeypatch.setattr(vendor, "dell_command_update", lambda: None)


def gpu(monkeypatch, name="NVIDIA GeForce RTX 4070 Laptop GPU", driver="566.36"):
    monkeypatch.setattr(hardware, "nvidia_gpus", lambda: [hardware.GpuInfo(name, 8188, 6591, driver)])


class Drivers:
    """Stands in for PowerShell: the Windows Update search lists ``available``, NVIDIA's service answers ``nvidia``;
    the ONE elevated script 'installs' whatever parts it contains."""

    def __init__(self, shell, available, *, nvidia=None, install_codes=None, uac_cancelled=False, reboot=True,
                 nv_result=None, dell_code=500):
        self.available, self.nvidia, self.codes, self.uac, self.reboot = available, nvidia, install_codes, uac_cancelled, reboot
        self.nv_result = nv_result if nv_result is not None else {"code": 0, "now": "572.16"}
        self.dell_code = dell_code
        self.searches, self.lookups, self.elevated = 0, [], []
        shell.run = self.run
        shell.run_elevated = self.run_elevated

    def run(self, command, shell, timeout, cancel):
        if "AjaxDriverService" in command:
            self.lookups.append(command)
            if isinstance(self.nvidia, CommandOutput):
                return self.nvidia
            return CommandOutput(0, self.nvidia or nv_answer(), "")
        assert "Microsoft.Update.Session" in command and "Type='Driver'" in command
        self.searches += 1
        return CommandOutput(0, json.dumps(self.available), "")

    def run_elevated(self, command, timeout, cancel):
        self.elevated.append(command)
        if self.uac:
            return CommandOutput(1223, "", "The administrator prompt was cancelled.")
        out = {}
        if "Microsoft.Update.Session" in command:
            ids = [u["id"] for u in self.available if u["id"] in command]
            codes = self.codes or [2] * len(ids)
            out["windows"] = {"found": len(ids), "reboot": self.reboot,
                              "items": [{"title": u["title"], "code": c}
                                        for u, c in zip(self.available, codes, strict=False)]}
        if "Get-AuthenticodeSignature" in command:
            out["nvidia"] = self.nv_result
        if "dcu-cli" in command:
            out["dell"] = {"code": self.dell_code}
        return CommandOutput(0, "Downloading...\n" + json.dumps(out), "")


def _notes(system):
    got, done = [], threading.Event()

    def on(e):
        if e.data.get("title") == "Driver updates":
            got.append(e.data["message"])
            done.set()

    system.ctx.bus.subscribe("ui.notify", on)
    return got, done


# ---------------------------------------------------------------------------------------------- Windows Update

def test_check_lists_updates_without_asking(ts):
    system, p, conf, _ = ts
    wu = Drivers(p.shell, [INTEL, REALTEK])
    r = system.invoke("driver_updates", {"action": "check"}, Task())
    assert r.ok and "2 driver update(s) available" in r.text and "Intel Corporation - Display" in r.text
    assert "700 MB" in r.text and conf.requests == [] and wu.elevated == [] and wu.lookups == []   # (no NVIDIA card)


def test_install_shows_the_exact_list_and_installs_only_those(ts):
    system, p, conf, _ = ts
    wu = Drivers(p.shell, [INTEL, REALTEK])
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
    assert "Get-AuthenticodeSignature" not in script and "dcu-cli" not in script      # nothing else in it


def test_declined_installs_nothing(ts):
    system, p, conf, _ = ts
    conf.choice = ConfirmChoice.DENY
    wu = Drivers(p.shell, [INTEL])
    r = system.invoke("driver_updates", {"action": "install"}, Task())
    assert not r.ok and wu.elevated == []


def test_failures_and_cancelled_uac_are_reported(ts):
    system, p, _, _ = ts
    Drivers(p.shell, [INTEL, REALTEK], install_codes=[2, 4], reboot=False)
    got, done = _notes(system)
    system.invoke("driver_updates", {"action": "install"}, Task())
    assert done.wait(5) and got[0].startswith("Installed 1 of 2") and "Realtek" in got[0] and "failed" in got[0]
    Drivers(p.shell, [INTEL], uac_cancelled=True)
    got2, done2 = _notes(system)
    system.invoke("driver_updates", {"action": "install"}, Task())
    assert done2.wait(5) and "administrator prompt was cancelled" in got2[0]


def test_an_installer_error_is_passed_on(ts):
    system, p, _, _ = ts
    wu = Drivers(p.shell, [INTEL])
    p.shell.run_elevated = lambda *a: CommandOutput(0, json.dumps({"windows": {"error": "0x80240022 all failed"}}), "")
    got, done = _notes(system)
    system.invoke("driver_updates", {"action": "install"}, Task())
    assert done.wait(5) and "Windows Update: 0x80240022" in got[0] and wu.searches == 1


def test_nothing_to_update(ts):
    system, p, conf, _ = ts
    wu = Drivers(p.shell, [])
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


def test_the_elevated_script_only_takes_checked_values():
    assert "'11111111-2222-3333-4444-555555555555'" in elevated_script([INTEL["id"]])
    for bad in (["x'; Remove-Item C:\\ -Recurse; '"], []):
        with pytest.raises(ToolError):
            elevated_script(bad)
    evil = vendor.NvidiaDriver("GeForce RTX 4070 Laptop GPU", "566.36", "572.16",
                               "https://us.download.nvidia.com/Windows/x.exe'; Remove-Item C:\\ -Recurse; '")
    with pytest.raises(ToolError):
        elevated_script([], evil)
    with pytest.raises(ToolError):
        elevated_script([], None, "C:\\Temp\\dcu-cli.exe")                       # not one of Dell's own paths


# ---------------------------------------------------------------------------------------------- NVIDIA

@pytest.mark.parametrize("name,notebook,pfid", [
    ("NVIDIA GeForce RTX 4070 Laptop GPU", True, 1006), ("NVIDIA GeForce RTX 4090", False, 995),
    ("NVIDIA GeForce RTX 4070 Super", False, 1039), ("NVIDIA GeForce RTX 4090 Laptop GPU", True, 1004),
    ("NVIDIA GeForce RTX 4070 Laptop GPU", False, 1006)])        # (not detected as a laptop: still found)
def test_gpu_names_map_to_nvidias_product_ids(name, notebook, pfid):
    clean = vendor.clean_gpu_name(name)
    assert not clean.startswith("NVIDIA") and vendor.product_id(clean, notebook) == pfid


def test_gpu_name_variants_are_cleaned():
    assert vendor.clean_gpu_name("NVIDIA GeForce GTX 1060 6GB") == "GeForce GTX 1060"
    assert vendor.clean_gpu_name("NVIDIA GeForce RTX 2070 with Max-Q Design") == "GeForce RTX 2070"
    assert vendor.clean_gpu_name("NVIDIA  GeForce GTX 760 Ti (OEM)") == "GeForce GTX 760 Ti"
    assert vendor.version_key("572.16") > vendor.version_key("566.36") > vendor.version_key("")


def test_check_compares_with_nvidias_newest_driver(ts, monkeypatch):
    system, p, conf, _ = ts
    gpu(monkeypatch)
    d = Drivers(p.shell, [INTEL])
    r = system.invoke("driver_updates", {"action": "check"}, Task())
    assert r.ok and conf.requests == [] and d.elevated == []
    assert "pfid=1006&osID=57&dch=1" in d.lookups[0]                      # the RTX 4070 Laptop GPU, Windows 10/11
    assert ("NVIDIA GeForce RTX 4070 Laptop GPU: driver 566.36 installed - GeForce Game Ready Driver 572.16 is "
            "available (665.43 MB).") in r.text
    assert r.data["speak"]["en"] == ("2 driver updates available: Intel Corporation - Display - 31.0.101.5186, "
                                     "NVIDIA driver 572.16. Say “update my drivers” to install them.")


def test_an_up_to_date_nvidia_driver(ts, monkeypatch):
    system, p, conf, _ = ts
    gpu(monkeypatch, driver="572.16")
    Drivers(p.shell, [])
    r = system.invoke("driver_updates", {"action": "install"}, Task())
    assert r.ok and conf.requests == [] and "NVIDIA GeForce RTX 4070 Laptop GPU: driver 572.16 - the newest." in r.text
    assert r.data["speak"]["en"] == "Your drivers are up to date - Windows Update and NVIDIA."


def test_update_my_nvidia_driver(ts, monkeypatch):
    """Downloaded from NVIDIA inside the one elevated step, only if signed by NVIDIA, installed silently."""
    system, p, conf, _ = ts
    gpu(monkeypatch)
    d = Drivers(p.shell, [INTEL])
    got, done = _notes(system)
    r = Assistant(system.ctx, system, None).handle("update my nvidia driver")
    assert r.ok and r.via == "fast" and d.searches == 0                     # (only NVIDIA - not Windows Update)
    assert r.text == "Installing 1 driver update in the background - I'll tell you when it's done."
    req = conf.requests[-1]
    assert {Risk.INSTALL, Risk.ADMIN} <= req.action.risks and "NVIDIA 572.16" in req.action.summary
    assert any(NV_URL in x and "566.36" in x for x in req.action.details)  # the exact address, shown before
    assert any("-s -noreboot -noeula" in x for x in req.action.details)
    assert done.wait(5) and got == ["NVIDIA driver 572.16 installed."]
    script = d.elevated[0]
    assert NV_URL in script and "O=NVIDIA Corporation" in script and "'-s','-noreboot','-noeula'" in script
    assert "$env:ProgramFiles 'Assistant\\driver-download'" in script         # a folder only admins can write
    assert "Microsoft.Update.Session" not in script


def test_an_nvidia_install_that_did_not_take(ts, monkeypatch):
    system, p, _, _ = ts
    gpu(monkeypatch)
    Drivers(p.shell, [], nv_result={"code": 1, "now": "566.36"})
    got, done = _notes(system)
    system.invoke("driver_updates", {"action": "install", "source": "nvidia"}, Task())
    assert done.wait(5) and "still says 566.36" in got[0] and "restart the PC" in got[0]
    Drivers(p.shell, [], nv_result={"error": "the download is not signed by NVIDIA (HashMismatch)"})
    got2, done2 = _notes(system)
    system.invoke("driver_updates", {"action": "install", "source": "nvidia"}, Task())
    assert done2.wait(5) and got2 == ["NVIDIA driver 572.16 not installed: the download is not signed by NVIDIA "
                                      "(HashMismatch)."]


@pytest.mark.parametrize("answer,problem", [
    (CommandOutput(1, "", "Invoke-WebRequest : The remote name could not be resolved: 'gfwsl.geforce.com'"),
     "couldn't be asked: Invoke-WebRequest : The remote name could not be resolved"),
    (nv_answer(url="https://evil.example.com/Windows/572.16/setup.exe"), "unexpected download address"),
    (nv_answer(version="572.16; calc"), "unexpected driver version"),
    (nv_answer(success="0"), "has no Windows driver for the GeForce RTX 4070 Laptop GPU")])
def test_nvidia_service_problems_are_explained_and_nothing_is_installed(ts, monkeypatch, answer, problem):
    system, p, conf, _ = ts
    gpu(monkeypatch)
    d = Drivers(p.shell, [], nvidia=answer)
    r = system.invoke("driver_updates", {"action": "install", "source": "nvidia"}, Task())
    assert r.ok and problem in r.text and conf.requests == [] and d.elevated == []


def test_no_nvidia_card_or_an_unknown_one(ts, monkeypatch):
    system, p, _, _ = ts
    Drivers(p.shell, [])
    r = system.invoke("driver_updates", {"action": "check", "source": "nvidia"}, Task())
    assert r.ok and "There's no NVIDIA graphics card in this PC." in r.text
    gpu(monkeypatch, name="NVIDIA GeForce RTX 9090 Laptop GPU")
    r = system.invoke("driver_updates", {"action": "check", "source": "nvidia"}, Task())
    assert "isn't in NVIDIA's driver list yet" in r.text


# ---------------------------------------------------------------------------------------------- Dell + makers' apps

def test_dell_command_update_runs_in_the_same_elevated_step(ts, monkeypatch, tmp_path):
    system, p, conf, _ = ts
    dcu = tmp_path / "Dell" / "CommandUpdate" / "dcu-cli.exe"
    dcu.parent.mkdir(parents=True)
    dcu.write_bytes(b"MZ")
    monkeypatch.setattr(vendor, "dell_command_update", lambda: str(dcu))
    monkeypatch.setattr(vendor, "_dcu_candidates", lambda: [str(dcu)])
    gpu(monkeypatch)
    d = Drivers(p.shell, [INTEL], dell_code=1)
    got, done = _notes(system)
    r = system.invoke("driver_updates", {"action": "install"}, Task())
    assert r.ok and r.data["started"]
    req = conf.requests[-1]
    assert any(f"{dcu} /applyUpdates -updateType=driver -reboot=disable -silent" in x for x in req.action.details)
    assert "once" in req.action.summary
    assert done.wait(5) and len(d.elevated) == 1                            # ONE UAC prompt for all three
    script = d.elevated[0]
    assert INTEL["id"] in script and NV_URL in script and str(dcu) in script
    assert got == ["Installed 1 of 1 driver update(s). NVIDIA driver 572.16 installed. Dell Command | Update: "
                   "installed driver updates (a restart finishes them). Restart the PC to finish."]


def test_the_makers_apps_are_named(ts, monkeypatch):
    system, p, _, _ = ts
    p.apps.apps += [AppEntry("Lenovo Vantage", "E046963F.LenovoCompanion_k1h2ywk1493x8!App", "store"),
                    AppEntry("NVIDIA App", "{GUID}\\NVIDIA Corporation\\NVIDIA App\\CEF\\NVIDIA App.exe"),
                    AppEntry("Intel® Driver & Support Assistant", "https://www.intel.com/dsa", "url")]
    system.services["apps"].apps(refresh=True)
    gpu(monkeypatch, driver="572.16")
    Drivers(p.shell, [])
    r = system.invoke("driver_updates", {"action": "check"}, Task())
    assert "The makers' apps on this PC: NVIDIA App, Intel® Driver & Support Assistant, Lenovo Vantage" in r.text
    assert r.data["speak"]["en"].endswith("Intel® Driver & Support Assistant can check the rest - say “open Intel® "
                                          "Driver & Support Assistant”.")       # (not the NVIDIA App: just checked)


@pytest.mark.parametrize("n_updates,from_file", [(5, False), (40, True)])
def test_the_elevated_command_always_fits_windows_command_line(monkeypatch, tmp_path, n_updates, from_file):
    """The UAC step encodes the script twice. A usual one (5 updates + NVIDIA + Dell) goes inline, as before; a
    longer one (40 updates) runs from a file, only if its SHA-256 matches - the command line always fits."""
    import base64
    import hashlib
    import re
    import tempfile

    from assistant.win.backends import WinShell

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))                  # (its temp files land here)
    dcu = r"C:\Program Files (x86)\Dell\CommandUpdate\dcu-cli.exe"
    monkeypatch.setattr(vendor, "_dcu_candidates", lambda: [dcu])
    nv = vendor.NvidiaDriver("GeForce RTX 4070 Laptop GPU", "566.36", "572.16", NV_URL)
    script = elevated_script([f"{i:08x}-2222-3333-4444-555555555555" for i in range(n_updates)], nv, dcu)
    sh = WinShell()
    sh.powershell = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    seen = {}

    def run(outer, shell, timeout, cancel):
        seen["outer"] = outer
        seen["files"] = [f.read_bytes() for f in tmp_path.glob("assistant-admin-*.ps1.txt")]
        return CommandOutput(0, "", "")

    sh.run = run
    sh.run_elevated(script, 10, None)
    assert len(WinShell._encode(seen["outer"])) + 300 < WinShell.MAX_COMMAND_LINE
    inner = base64.b64decode(re.search(r"'-EncodedCommand','([^']+)'", seen["outer"]).group(1)).decode("utf-16-le")
    if from_file:
        assert seen["files"] == [script.encode("utf-8")]
        assert hashlib.sha256(seen["files"][0]).hexdigest().upper() in inner and "ScriptBlock]::Create" in inner
        assert "Get-AuthenticodeSignature" not in inner                     # (the script itself is in the file)
    else:
        assert seen["files"] == [] and "Get-AuthenticodeSignature" in inner  # inline, as before
    assert list(tmp_path.glob("assistant-admin-*")) == []                   # cleaned up afterwards


# ---------------------------------------------------------------------------------------------- voice

@pytest.mark.parametrize("said,intent", [
    ("update all my drivers", "drivers.update"), ("Jarvis, can you update my drivers?", "drivers.update"),
    ("install the driver updates", "drivers.update"), ("aktualisiere meine Treiber", "drivers.update"),
    ("kannst du meine Treiber aktualisieren", "drivers.update"), ("check for driver updates", "drivers.check"),
    ("are my drivers up to date", "drivers.check"), ("gibt es neue Treiber-Updates", "drivers.check"),
    ("sind meine Treiber aktuell", "drivers.check"),
    ("update my nvidia driver", "drivers.nvidia.update"), ("Jarvis, update my graphics drivers", "drivers.nvidia.update"),
    ("install the latest nvidia driver", "drivers.nvidia.update"), ("update the gpu driver", "drivers.nvidia.update"),
    ("aktualisiere meinen Grafiktreiber", "drivers.nvidia.update"),
    ("installiere den neuesten NVIDIA-Treiber", "drivers.nvidia.update"),
    ("is there a new nvidia driver", "drivers.nvidia.check"), ("check for nvidia driver updates", "drivers.nvidia.check"),
    ("is my graphics driver up to date", "drivers.nvidia.check"),
    ("gibt es einen neuen NVIDIA-Treiber", "drivers.nvidia.check"), ("ist mein Grafiktreiber aktuell", "drivers.nvidia.check")])
def test_voice_commands(said, intent):
    fp = FastPath(lambda name: None, lambda: ["Jarvis"], lambda: "en")
    m = fp.match(said)
    assert m is not None and m.intent == intent and m.tool == "driver_updates"
    assert (m.args.get("source") == "nvidia") == intent.startswith("drivers.nvidia")


def test_spoken_answers(ts, monkeypatch):
    system, p, conf, _ = ts
    Drivers(p.shell, [INTEL, REALTEK])
    a = Assistant(system.ctx, system, None)
    r = a.handle("check for driver updates")
    assert r.via == "fast" and r.text.startswith("2 driver updates available: Intel Corporation - Display")
    r = a.handle("update my drivers")
    assert r.ok and r.text == "Installing 2 driver updates in the background - I'll tell you when it's done."
    Drivers(p.shell, [])
    assert a.handle("are my drivers up to date").text.startswith("Windows Update says your drivers are up to date.")
    gpu(monkeypatch, driver="572.16")
    assert a.handle("is my nvidia driver up to date").text == "Your NVIDIA driver 572.16 is the newest."
    gpu(monkeypatch)
    assert a.handle("is there a new nvidia driver").text == ("NVIDIA driver 572.16 is available - you have 566.36. "
                                                            "Say “update my NVIDIA driver” to install it.")
