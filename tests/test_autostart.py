from assistant.core.autostart import (APPROVED_KEY, RUN_KEY, VALUE_NAME, Autostart, AutostartState,
                                      MemoryBackend, launch_command)

CMD = '"C:\\venv\\Scripts\\pythonw.exe" -m assistant --autostart'


def make():
    reg = MemoryBackend()
    return reg, Autostart(reg, command=CMD)


def test_enable_disable_roundtrip():
    reg, a = make()
    assert a.state() is AutostartState.DISABLED
    a.enable()
    assert reg.get(RUN_KEY, VALUE_NAME) == CMD
    assert a.state() is AutostartState.ENABLED
    a.disable()
    assert reg.get(RUN_KEY, VALUE_NAME) is None
    assert a.state() is AutostartState.DISABLED


def test_disabled_in_task_manager_is_respected_by_sync_but_cleared_by_enable():
    reg, a = make()
    a.enable()
    reg.set_binary(APPROVED_KEY, VALUE_NAME, b"\x03" + b"\x00" * 11)  # user disabled it in Task Manager
    assert a.state() is AutostartState.DISABLED_BY_WINDOWS
    assert a.sync(True) is AutostartState.DISABLED_BY_WINDOWS     # startup sync keeps the user's choice
    a.enable()                                                      # explicit toggle in our UI
    assert a.state() is AutostartState.ENABLED
    assert reg.get(APPROVED_KEY, VALUE_NAME) is None


def test_sync_repairs_stale_command_and_removes_when_off():
    reg, a = make()
    reg.set_string(RUN_KEY, VALUE_NAME, '"D:\\old\\pythonw.exe" -m assistant --autostart')
    assert a.sync(True) is AutostartState.ENABLED
    assert reg.get(RUN_KEY, VALUE_NAME) == CMD
    assert a.sync(False) is AutostartState.DISABLED
    assert reg.get(RUN_KEY, VALUE_NAME) is None


def test_unsupported_without_backend(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    a = Autostart(None, command=CMD)
    assert a.state() is AutostartState.UNSUPPORTED
    a.enable()  # no-op, no crash
    assert a.sync(True) is AutostartState.UNSUPPORTED


def test_launch_command_prefers_pythonw(tmp_path):
    exe = tmp_path / "python.exe"
    exe.write_text("")
    assert launch_command(str(exe), frozen=False) == f'"{exe}" -I -m assistant --autostart'
    (tmp_path / "pythonw.exe").write_text("")
    assert launch_command(str(exe), frozen=False) == f'"{tmp_path / "pythonw.exe"}" -I -m assistant --autostart'
    assert launch_command(str(tmp_path / "Assistant.exe"), frozen=True) == \
        f'"{tmp_path / "Assistant.exe"}" --autostart'
