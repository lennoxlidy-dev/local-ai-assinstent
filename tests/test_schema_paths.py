"""Schema validation and Windows path canonicalization (the parts attackers would target)."""

import pytest

from assistant.permissions.pathrules import PathError, PathNormalizer, PathRules
from assistant.permissions.policy import Access, FolderRule, Level
from assistant.tools.schema import Arr, Bool, Int, Num, Obj, SchemaError, Str, obj

# ----------------------------------------------------------------------------- schema

S = obj(("path",), path=Str("p", min_len=1), overwrite=Bool(default=False), n=Int(min=1, max=10),
        mode=Str(enum=("a", "b")), x=Num(min=0), tags=Arr(items=Str()))


def test_schema_json_is_strict():
    j = S.to_json()
    assert j["additionalProperties"] is False and j["required"] == ["path"]
    assert j["properties"]["mode"]["enum"] == ["a", "b"]


def test_schema_validates_and_coerces_safely():
    dropped = []
    out = S.validate({"path": "C:\\x", "n": "3", "overwrite": "true", "mode": "A", "x": "1,5", "evil": 1},
                     "args", dropped)
    assert out == {"path": "C:\\x", "n": 3, "overwrite": True, "mode": "a", "x": 1.5}
    assert dropped == ["args.evil"]


@pytest.mark.parametrize("bad, msg", [
    ({}, "required"), ({"path": ""}, "empty"), ({"path": "x", "n": 11}, "<= 10"), ({"path": "x", "n": True}, "integer"),
    ({"path": "x", "mode": "c"}, "one of"), ({"path": "x", "overwrite": "yes"}, "true or false"),
    ({"path": "x", "tags": "a"}, "list"), ({"path": 5.5j}, "string"),
])
def test_schema_rejects(bad, msg):
    with pytest.raises(SchemaError, match=msg):
        S.validate(bad)


def test_null_optional_is_ignored_and_defaults_applied():
    assert S.validate({"path": "x", "n": None}) == {"path": "x", "overwrite": False}


def test_open_object():
    assert Obj(open=True).validate({"anything": 1}) == {"anything": 1}


# ----------------------------------------------------------------------------- windows paths

ENV = {"USERPROFILE": "C:\\Users\\Sam", "APPDATA": "C:\\Users\\Sam\\AppData\\Roaming", "WINDIR": "C:\\Windows",
       "SYSTEMDRIVE": "C:"}


def winnorm(resolver=None):
    return PathNormalizer("windows", env=ENV, resolver=resolver,
                          aliases={"downloads": "C:\\Users\\Sam\\Downloads", "desktop": "D:\\OneDrive\\Desktop"})


@pytest.mark.parametrize("raw, expected", [
    ("C:\\Windows\\System32", "c:\\windows\\system32"),
    ("c:/WINDOWS/system32/", "c:\\windows\\system32"),
    ("C:\\Windows.\\System32", "c:\\windows\\system32"),        # trailing dot stripped like Win32 does
    ("C:\\Windows \\System32", "c:\\windows\\system32"),        # trailing space too
    ("C:\\Users\\Sam\\..\\..\\Windows", "c:\\windows"),
    ("\\\\?\\C:\\Windows\\notepad.exe", "c:\\windows\\notepad.exe"),
    ("%WINDIR%\\System32", "c:\\windows\\system32"),
    ("%windir%\\System32", "c:\\windows\\system32"),
    ("~\\Documents", "c:\\users\\sam\\documents"),
    ("Downloads\\a.pdf", "c:\\users\\sam\\downloads\\a.pdf"),     # folder alias
    ("desktop", "d:\\onedrive\\desktop"),
    ("notes.txt", "c:\\users\\sam\\notes.txt"),                   # relative -> home
    ('"C:\\Program Files\\App"', "c:\\program files\\app"),
    ("\\\\server\\share\\x", "\\\\server\\share\\x"),
])
def test_windows_normalization(raw, expected):
    assert winnorm().normalize(raw).key == expected


@pytest.mark.parametrize("raw", [
    "\\\\.\\PhysicalDrive0", "\\\\?\\GLOBALROOT\\Device", "C:\\x\\file.txt:secret", "C:\\x\\NUL", "C:\\x\\con.txt",
    "C:\\x\\COM1", "C:relative", "C:\\x\\a*b", "%NOPE%\\x", "C:\\x\\...", "", "C:\\x\x00y", "\\\\server",
])
def test_windows_rejects_tricky_paths(raw):
    with pytest.raises(PathError):
        winnorm().normalize(raw)


def test_resolver_catches_junctions_and_short_names():
    links = {"c:\\users\\sam\\innocent": "C:\\Windows\\System32", "c:\\progra~1\\x": "C:\\Program Files\\x"}
    norm = winnorm(resolver=lambda p: links.get(p.lower(), p))
    rules = PathRules(norm, [FolderRule("C:\\Windows", Access.READ), FolderRule("C:\\Program Files", Access.READ)])
    for raw in ("C:\\Users\\Sam\\innocent", "C:\\PROGRA~1\\x"):
        lvl, why = rules.level(norm.normalize(raw), "write", Level.ASK)
        assert lvl is Level.BLOCK and "protected" in why


def test_folder_rules_most_specific_wins():
    norm = winnorm()
    rules = PathRules(norm, [
        FolderRule("C:\\Users\\Sam", Access.FULL, "home"),
        FolderRule("C:\\Users\\Sam\\.ssh", Access.NONE, "keys"),
        FolderRule("C:\\Users\\Sam\\Taxes", Access.ASK, "taxes"),
        FolderRule("C:\\Windows", Access.READ),
    ], internal=["C:\\Users\\Sam\\AppData\\Roaming\\Assistant"])
    n = norm.normalize
    assert rules.level(n("~\\notes.txt"), "write", Level.ASK)[0] is Level.ALLOW          # trusted folder
    assert rules.level(n("~\\.ssh\\id_rsa"), "read", Level.ALLOW)[0] is Level.BLOCK       # blocked even for reading
    assert rules.level(n("~\\Taxes\\2025.pdf"), "read", Level.ALLOW)[0] is Level.ASK
    assert rules.level(n("C:\\Windows\\win.ini"), "read", Level.ALLOW)[0] is Level.ALLOW
    assert rules.level(n("C:\\Windows\\win.ini"), "write", Level.ALLOW)[0] is Level.BLOCK
    assert rules.level(n("C:\\WindowsApps\\x"), "write", Level.ASK)[0] is Level.ASK        # prefix != parent
    assert rules.level(n("%APPDATA%\\Assistant\\permissions.toml"), "write", Level.ALLOW)[0] is Level.BLOCK
    assert rules.level(n("D:\\data\\x"), "write", Level.BLOCK)[0] is Level.BLOCK           # capability blocked
