"""File tools: find, list, read, create, copy, move, rename, delete (Recycle Bin only), open."""

from __future__ import annotations

import datetime as _dt
import os
import shutil
import time
from pathlib import Path
from typing import Any

from ..permissions.pathrules import NormPath, PathError, is_within
from ..permissions.policy import Level, Risk
from .base import Plan, Tool, ToolContext, ToolError, ToolRegistry, ToolResult
from .extract import SUPPORTED_DOCS, TEXT_EXT, ExtractError, extract_text
from .schema import Bool, Int, Str, obj

INSTALLER_EXT = {".msi", ".msix", ".msixbundle", ".appx", ".appxbundle"}
EXEC_EXT = {".exe", ".bat", ".cmd", ".com", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta",
            ".scr", ".pif", ".cpl", ".jar", ".reg", ".lnk", ".url", ".application", ".msc"}
KINDS = {
    "document": {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".xls", ".xlsx", ".ods", ".csv",
                 ".ppt", ".pptx", ".odp"},
    "image": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp", ".tif", ".tiff", ".svg", ".raw"},
    "audio": {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma"},
    "video": {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".m4v"},
    "archive": {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz"},
    "code": {".py", ".js", ".ts", ".java", ".c", ".cpp", ".cs", ".go", ".rs", ".html", ".css", ".json", ".ps1"},
}


# ----------------------------------------------------------------------------- helpers

def _fmt_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def _describe(p: Path) -> str:
    try:
        st = p.stat()
    except OSError:
        return str(p)
    when = _dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
    if p.is_dir():
        return f"{p}  (folder, modified {when})"
    return f"{p}  ({_fmt_size(st.st_size)}, modified {when})"


def _readable(ctx: ToolContext, raw: str) -> bool:
    """False for paths inside blocked folders (they must not even show up in search results)."""
    rules = ctx.services.get("path_rules")
    if rules is None:
        return True
    try:
        np = ctx.normalizer.normalize(raw)
    except PathError:
        return False
    return rules.level(np, "read", Level.ALLOW)[0] is not Level.BLOCK


def _guard_destructive(ctx: ToolContext, np: NormPath) -> None:
    """Hard refusals that no permission setting can override."""
    p = Path(np.path)
    if p.parent == p:
        raise ToolError("I won't touch a whole drive.")
    protected = {ctx.normalizer.normalize(v).key for v in ctx.platform.known_folders.values() if v}
    try:
        protected.add(ctx.normalizer.normalize("~").key)
    except PathError:
        pass
    if np.key in protected:
        raise ToolError(f"{np.path} is one of your main folders; I won't move or delete it as a whole.")


def _target_for(src: NormPath, dst: NormPath) -> Path:
    """copy/move semantics: into an existing folder, or to the exact new path."""
    d = Path(dst.path)
    return d / Path(src.path).name if d.is_dir() else d


def _trash(ctx: ToolContext, path: Path) -> None:
    ctx.platform.trash.send(str(path))


# ----------------------------------------------------------------------------- find / list / read

def _plan_find(a: dict[str, Any], ctx: ToolContext) -> Plan:
    paths = [(a["folder"], "read")] if a.get("folder") else []
    where = f" in {a['folder']}" if a.get("folder") else ""
    return Plan(f"Search files for “{a['query']}”{where}", paths=paths)


def _run_find(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    query, limit = a["query"].strip(), a["limit"]
    folder = ctx.path(a["folder"]).path if a.get("folder") else None
    kind_ext = KINDS.get(a.get("kind") or "")
    want_dirs = a.get("kind") == "folder"
    engines = []
    if ctx.platform.search is not None:
        engines.append(ctx.platform.search)
    if ctx.services.get("file_index") is not None:
        engines.append(ctx.services["file_index"])
    found: list[str] = []
    used = "none"
    for engine in engines:
        try:
            found = engine.search(query, limit * 4, folder, ctx.task.cancel)
            used = engine.name
        except Exception as e:  # engine unavailable -> next one
            found = []
            used = f"{getattr(engine, 'name', 'search')} failed: {e}"
        if found:
            break
    if not found and folder:
        found, used = _walk_search(Path(folder), query, limit * 4, ctx), "folder scan"
    results = []
    for f in found:
        p = Path(f)
        if kind_ext and p.suffix.lower() not in kind_ext:
            continue
        if want_dirs and not p.is_dir():
            continue
        if not _readable(ctx, f):
            continue
        results.append(f)
        if len(results) >= limit:
            break
    if not results:
        return ToolResult(True, f"No files found for “{query}”.", data={"results": [], "engine": used})
    lines = [f"{i}. {_describe(Path(r))}" for i, r in enumerate(results, 1)]
    return ToolResult(True, "\n".join(lines), data={"results": results, "engine": used})


def _walk_search(root: Path, query: str, limit: int, ctx: ToolContext, budget_s: float = 4.0) -> list[str]:
    q = query.casefold()
    out, t0 = [], time.monotonic()
    for dirpath, dirnames, filenames in os.walk(root):
        ctx.check_cancel()
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "$")) and d not in ("node_modules", "__pycache__")]
        for name in dirnames + filenames:
            if q in name.casefold():
                out.append(os.path.join(dirpath, name))
                if len(out) >= limit:
                    return out
        if time.monotonic() - t0 > budget_s:
            break
    return out


def _plan_list(a: dict[str, Any], ctx: ToolContext) -> Plan:
    return Plan(f"List {a['path']}", paths=[(a["path"], "read")])


def _run_list(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    p = Path(ctx.path(a["path"]).path)
    if not p.exists():
        raise ToolError(f"{p} does not exist.")
    if p.is_file():
        return ToolResult(True, _describe(p))
    entries = []
    try:
        with os.scandir(p) as it:
            for e in it:
                if not _readable(ctx, e.path):
                    continue
                entries.append(e)
    except PermissionError:
        raise ToolError(f"Windows denied access to {p}.") from None
    entries.sort(key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.casefold()))
    shown = entries[: a["limit"]]
    lines = [f"{'[folder] ' if e.is_dir(follow_symlinks=False) else ''}{e.name}" for e in shown]
    more = f"\n… and {len(entries) - len(shown)} more" if len(entries) > len(shown) else ""
    return ToolResult(True, f"{p} ({len(entries)} items):\n" + "\n".join(lines) + more,
                      data={"items": [e.path for e in shown]})


def _plan_read(a: dict[str, Any], ctx: ToolContext) -> Plan:
    return Plan(f"Read {a['path']}", paths=[(a["path"], "read")])


def _run_read(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    p = Path(ctx.path(a["path"]).path)
    if not p.exists():
        raise ToolError(f"{p} does not exist.")
    if p.is_dir():
        raise ToolError(f"{p} is a folder; use list_folder.")
    ext = p.suffix.lower()
    if ext not in TEXT_EXT and ext not in SUPPORTED_DOCS and ext:
        # unknown extension: try as text, extract_text refuses binaries
        pass
    try:
        text, info = extract_text(p, a["max_chars"])
    except ExtractError as e:
        raise ToolError(f"Can't read {p.name}: {e}") from None
    except PermissionError:
        raise ToolError(f"Windows denied access to {p}.") from None
    note = " (truncated)" if info.get("truncated") else ""
    return ToolResult(True, f"Content of {p}{note}:\n{text}", data={"path": str(p), **info}, untrusted=True)


# ----------------------------------------------------------------------------- create

def _plan_create_file(a: dict[str, Any], ctx: ToolContext) -> Plan:
    np = ctx.normalizer.normalize(a["path"])
    exists = Path(np.path).exists()
    if exists and Path(np.path).is_dir():
        raise ToolError(f"{np.path} is a folder.")
    if exists and not a["overwrite"]:
        raise ToolError(f"{np.path} already exists. Ask the user whether to overwrite it, then set overwrite=true.")
    size = len(a["content"])
    details = [f"file: {np.path}", f"content: {size} characters"]
    if exists:
        details.append("the old file goes to the Recycle Bin first")
        return Plan("Overwrite file", details, [(a["path"], "write")], {Risk.OVERWRITE}, capability="files.modify",
                    state={"exists": True})
    return Plan("Create file", details, [(a["path"], "write")], state={"exists": False})


def _run_create_file(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    p = Path(ctx.path(a["path"]).path)
    if p.exists():
        if not plan.state.get("exists"):
            raise ToolError(f"{p} appeared in the meantime; not overwriting it.")
        _trash(ctx, p)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "x", encoding="utf-8", newline="") as fh:
        fh.write(a["content"])
    return ToolResult(True, f"{'Overwrote' if plan.state.get('exists') else 'Created'} {p}.", data={"path": str(p)})


def _plan_create_folder(a: dict[str, Any], ctx: ToolContext) -> Plan:
    return Plan("Create folder", [f"folder: {ctx.normalizer.normalize(a['path']).path}"], [(a["path"], "write")])


def _run_create_folder(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    p = Path(ctx.path(a["path"]).path)
    if p.exists():
        return ToolResult(True, f"{p} already exists.")
    p.mkdir(parents=True)
    return ToolResult(True, f"Created folder {p}.", data={"path": str(p)})


# ----------------------------------------------------------------------------- copy / move / rename / delete

def _plan_transfer(a: dict[str, Any], ctx: ToolContext, *, move: bool) -> Plan:
    src = ctx.normalizer.normalize(a["source"])
    dst = ctx.normalizer.normalize(a["destination"])
    sp = Path(src.path)
    if not sp.exists():
        raise ToolError(f"{src.path} does not exist.")
    if move:
        _guard_destructive(ctx, src)
    target = _target_for(src, dst)
    tnorm = ctx.normalizer.normalize(str(target))
    if tnorm.key == src.key:
        raise ToolError("Source and destination are the same.")
    sep = "\\" if ctx.normalizer.flavor == "windows" else "/"
    if sp.is_dir() and is_within(tnorm.key, src.key, sep):
        raise ToolError("Can't put a folder inside itself.")
    risks: set[Risk] = {Risk.MOVE} if move else set()
    exists = target.exists()
    if exists and not a["overwrite"]:
        raise ToolError(f"{target} already exists. Ask the user whether to overwrite it, then set overwrite=true.")
    if exists:
        risks.add(Risk.OVERWRITE)
    verb = "Move" if move else "Copy"
    details = [f"from: {src.path}", f"to:   {target}"]
    if exists:
        details.append("the existing file goes to the Recycle Bin first")
    return Plan(f"{verb} {'folder' if sp.is_dir() else 'file'}", details,
                [(a["source"], "write" if move else "read"), (str(target), "write")], risks,
                state={"target": str(target), "exists": exists})


def _run_transfer(a: dict[str, Any], ctx: ToolContext, plan: Plan, *, move: bool) -> ToolResult:
    src = Path(ctx.path(a["source"]).path)
    target = Path(plan.state["target"])
    if target.exists():
        if not plan.state.get("exists"):
            raise ToolError(f"{target} appeared in the meantime; not overwriting it.")
        _trash(ctx, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(src), str(target))
    elif src.is_dir():
        shutil.copytree(src, target)
    else:
        shutil.copy2(src, target)
    return ToolResult(True, f"{'Moved' if move else 'Copied'} to {target}.", data={"path": str(target)})


def _plan_rename(a: dict[str, Any], ctx: ToolContext) -> Plan:
    src = ctx.normalizer.normalize(a["path"])
    new = a["new_name"].strip()
    if any(c in new for c in '\\/:*?"<>|') or new in (".", ".."):
        raise ToolError("The new name must be a plain file name without folders or special characters.")
    sp = Path(src.path)
    if not sp.exists():
        raise ToolError(f"{src.path} does not exist.")
    _guard_destructive(ctx, src)
    target = sp.with_name(new)
    if target.exists() and target.name.casefold() != sp.name.casefold():
        raise ToolError(f"{target} already exists.")
    return Plan("Rename", [f"from: {src.path}", f"to:   {target}"],
                [(a["path"], "write"), (str(target), "write")], {Risk.MOVE}, state={"target": str(target)})


def _run_rename(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    src = Path(ctx.path(a["path"]).path)
    target = Path(plan.state["target"])
    src.rename(target)
    return ToolResult(True, f"Renamed to {target.name}.", data={"path": str(target)})


def _plan_delete(a: dict[str, Any], ctx: ToolContext) -> Plan:
    np = ctx.normalizer.normalize(a["path"])
    p = Path(np.path)
    if not p.exists():
        raise ToolError(f"{np.path} does not exist.")
    _guard_destructive(ctx, np)
    what = "folder (and everything in it)" if p.is_dir() else "file"
    return Plan(f"Delete {what} to the Recycle Bin", [f"path: {np.path}"], [(a["path"], "write")], {Risk.DELETE})


def _run_delete(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    p = Path(ctx.path(a["path"]).path)
    _trash(ctx, p)
    return ToolResult(True, f"Moved {p.name} to the Recycle Bin.", data={"path": str(p)})


# ----------------------------------------------------------------------------- open

def _plan_open(a: dict[str, Any], ctx: ToolContext) -> Plan:
    np = ctx.normalizer.normalize(a["path"])
    p = Path(np.path)
    if not p.exists():
        raise ToolError(f"{np.path} does not exist.")
    ext = p.suffix.lower()
    if ext in INSTALLER_EXT or (ext == ".exe" and any(k in p.name.lower() for k in ("setup", "install"))):
        return Plan("Run installer", [f"file: {np.path}"], [(a["path"], "read")], {Risk.INSTALL},
                    capability="software.install")
    if ext in EXEC_EXT:
        return Plan("Run program/script", [f"file: {np.path}"], [(a["path"], "read")], {Risk.TERMINAL_CHANGE},
                    capability="terminal.run")
    return Plan(f"Open {p.name}", [f"path: {np.path}"], [(a["path"], "read")], capability="apps.open")


def _run_open(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    p = ctx.path(a["path"]).path
    ctx.platform.apps.open_target(p)
    return ToolResult(True, f"Opened {Path(p).name}.")


# ----------------------------------------------------------------------------- registration

def register(reg: ToolRegistry) -> None:
    path = Str("Absolute path, or relative to the user's home. Folder names like Desktop/Downloads work.",
               min_len=1, max_len=1000)
    reg.register(Tool(
        "find_files", "Search the PC for files or folders by (part of) their name.",
        obj(("query",), query=Str("Name or part of the name", min_len=1, max_len=200),
            folder=Str("Only search inside this folder (optional)", max_len=1000),
            kind=Str("Filter by type", enum=("any", "file", "folder", *KINDS.keys()), default="any"),
            limit=Int("Max results", min=1, max=50, default=15)),
        "files.read", _plan_find, _run_find))
    reg.register(Tool(
        "list_folder", "List the contents of a folder (or describe one file).",
        obj(("path",), path=path, limit=Int("Max entries", min=1, max=500, default=60)),
        "files.read", _plan_list, _run_list))
    reg.register(Tool(
        "read_file", "Read the text of a file: txt/code, PDF, Word, Excel, PowerPoint, OpenDocument, HTML.",
        obj(("path",), path=path, max_chars=Int("Max characters to return", min=200, max=60000, default=8000)),
        "files.read", _plan_read, _run_read))
    reg.register(Tool(
        "create_file", "Create a text file with the given content.",
        obj(("path", "content"), path=path, content=Str("File content", max_len=500000),
            overwrite=Bool("Replace an existing file (only if the user said so)", default=False)),
        "files.write", _plan_create_file, _run_create_file))
    reg.register(Tool(
        "create_folder", "Create a folder (including missing parent folders).",
        obj(("path",), path=path), "files.write", _plan_create_folder, _run_create_folder))
    reg.register(Tool(
        "copy_path", "Copy a file or folder. If destination is an existing folder, copy into it.",
        obj(("source", "destination"), source=path, destination=path,
            overwrite=Bool("Replace an existing target (only if the user said so)", default=False)),
        "files.modify", lambda a, c: _plan_transfer(a, c, move=False),
        lambda a, c, p: _run_transfer(a, c, p, move=False)))
    reg.register(Tool(
        "move_path", "Move a file or folder. If destination is an existing folder, move into it.",
        obj(("source", "destination"), source=path, destination=path,
            overwrite=Bool("Replace an existing target (only if the user said so)", default=False)),
        "files.modify", lambda a, c: _plan_transfer(a, c, move=True),
        lambda a, c, p: _run_transfer(a, c, p, move=True)))
    reg.register(Tool(
        "rename_path", "Rename a file or folder (same folder, new name).",
        obj(("path", "new_name"), path=path, new_name=Str("New name incl. extension", min_len=1, max_len=255)),
        "files.modify", _plan_rename, _run_rename))
    reg.register(Tool(
        "delete_path", "Delete a file or folder by moving it to the Recycle Bin (never permanent).",
        obj(("path",), path=path), "files.delete", _plan_delete, _run_delete))
    reg.register(Tool(
        "open_path", "Open a file or folder with its default app (Explorer for folders).",
        obj(("path",), path=path), "apps.open", _plan_open, _run_open))
