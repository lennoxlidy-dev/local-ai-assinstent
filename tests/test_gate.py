"""The permission gate: allow/ask/block, risks, per-task grants, 'always'."""

from assistant.core.cancel import CancelToken
from assistant.permissions.gate import Action, AutoConfirm, ConfirmChoice, PermissionGate, Task
from assistant.permissions.pathrules import PathNormalizer, PathRules
from assistant.permissions.policy import Access, FolderRule, Level, PolicyStore, Risk


def make(tmp_path, choice=ConfirmChoice.ONCE, folders=()):
    store = PolicyStore(tmp_path / "permissions.toml")
    store.load()
    store.policy.folders = list(folders)
    norm = PathNormalizer("posix", env={"HOME": str(tmp_path)}, home=str(tmp_path))
    conf = AutoConfirm(choice)
    return PermissionGate(store, PathRules(norm, store.policy.folders), conf), store, conf, norm


def test_defaults_allow_reads_ask_for_changes(tmp_path):
    gate, _, conf, norm = make(tmp_path)
    p = norm.normalize(str(tmp_path / "a.txt"))
    assert gate.authorize(Action("read_file", "files.read", "Read", paths=[(p, "read")]), Task()).decision == "allowed"
    assert conf.requests == []
    res = gate.authorize(Action("create_file", "files.write", "Create", paths=[(p, "write")]), Task())
    assert res.decision == "approved" and len(conf.requests) == 1


def test_deny_and_block(tmp_path):
    gate, store, conf, norm = make(tmp_path, ConfirmChoice.DENY)
    res = gate.authorize(Action("delete_path", "files.delete", "Delete", risks={Risk.DELETE}), Task())
    assert not res.allowed and res.decision == "denied"
    store.set_level("files.delete", Level.BLOCK)
    res = gate.authorize(Action("delete_path", "files.delete", "Delete", risks={Risk.DELETE}), Task())
    assert res.decision == "blocked" and len(conf.requests) == 1  # blocked never even asks


def test_risky_actions_ask_even_when_capability_allows(tmp_path):
    gate, store, conf, _ = make(tmp_path)
    store.set_level("files.delete", Level.ALLOW)
    gate.authorize(Action("delete_path", "files.delete", "Delete", risks={Risk.DELETE}), Task())
    assert len(conf.requests) == 1 and not conf.requests[0].allow_task and not conf.requests[0].allow_always
    store.set_always_confirm(Risk.DELETE, False)  # the user opted out of confirming deletes
    gate.authorize(Action("delete_path", "files.delete", "Delete", risks={Risk.DELETE}), Task())
    assert len(conf.requests) == 1


def test_admin_always_asks_and_cannot_be_allowed(tmp_path):
    import pytest

    gate, store, conf, _ = make(tmp_path)
    with pytest.raises(ValueError):
        store.set_level("system.admin", Level.ALLOW)
    with pytest.raises(ValueError):
        store.set_always_confirm(Risk.ADMIN, False)


def test_task_grant_covers_rest_of_task_only(tmp_path):
    gate, _, conf, _ = make(tmp_path, ConfirmChoice.TASK)
    task = Task()
    a = Action("mouse", "input", "Click")
    assert gate.authorize(a, task).decision == "approved"
    assert gate.authorize(a, task).decision == "allowed"       # no second question
    assert gate.authorize(a, Task()).decision == "approved"    # new task asks again
    assert len(conf.requests) == 2


def test_always_persists(tmp_path):
    gate, store, conf, _ = make(tmp_path, ConfirmChoice.ALWAYS)
    gate.authorize(Action("mouse", "input", "Click"), Task())
    again = PolicyStore(tmp_path / "permissions.toml")
    assert again.load().level("input") is Level.ALLOW


def test_folder_rules_in_gate(tmp_path):
    secret = tmp_path / "secret"
    trusted = tmp_path / "scratch"
    gate, _, conf, norm = make(tmp_path, folders=[FolderRule(str(secret), Access.NONE),
                                                  FolderRule(str(trusted), Access.FULL)])
    p_secret = norm.normalize(str(secret / "pw.txt"))
    p_trusted = norm.normalize(str(trusted / "x.txt"))
    assert gate.authorize(Action("read_file", "files.read", "Read", paths=[(p_secret, "read")]),
                          Task()).decision == "blocked"
    assert gate.authorize(Action("create_file", "files.write", "Create", paths=[(p_trusted, "write")]),
                          Task()).decision == "allowed"
    # a move out of the trusted folder into an untrusted one still asks
    p_out = norm.normalize(str(tmp_path / "x.txt"))
    res = gate.authorize(Action("move_path", "files.modify", "Move", paths=[(p_trusted, "write"), (p_out, "write")],
                                risks={Risk.MOVE}), Task())
    assert res.decision == "approved" and len(conf.requests) == 1


def test_cancelled_task_is_refused(tmp_path):
    gate, _, conf, _ = make(tmp_path)
    t = Task(cancel=CancelToken())
    t.cancel.cancel("panic")
    assert gate.authorize(Action("volume", "system.volume", "Volume"), t).decision == "cancelled"


def test_unknown_capability_blocked(tmp_path):
    gate, *_ = make(tmp_path)
    assert gate.authorize(Action("x", "made.up", "x"), Task()).decision == "blocked"


def test_broken_permissions_file_falls_back_to_safe_defaults(tmp_path):
    (tmp_path / "permissions.toml").write_text("[[[", encoding="utf-8")
    store = PolicyStore(tmp_path / "permissions.toml")
    pol = store.load()
    assert pol.level("files.delete") is Level.ASK and store.warnings
    (tmp_path / "permissions.toml").write_text('[capabilities]\n"system.admin" = "allow"\n"input" = "nope"\n',
                                               encoding="utf-8")
    pol = store.load()
    assert pol.level("system.admin") is Level.ASK and len(store.warnings) == 2
