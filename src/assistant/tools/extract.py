"""Text extraction for read_file / summarize: plain text, PDF, Office (docx/xlsx/pptx), OpenDocument, HTML.

Office/OpenDocument files are zip + XML, parsed with the standard library
(no heavy dependencies). PDFs use pypdfium2 if installed. Everything is
bounded (characters, zip member sizes) because files are untrusted input.
"""

from __future__ import annotations

import html.parser
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_ZIP_MEMBER = 60 * 2**20  # refuse absurd (zip bomb) members
TEXT_EXT = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".log", ".json", ".jsonl", ".xml", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp", ".hpp", ".cs",
    ".go", ".rs", ".rb", ".php", ".ps1", ".psm1", ".bat", ".cmd", ".sh", ".sql", ".css", ".scss", ".srt", ".vtt",
    ".tex", ".rtf", ".bib", ".env", ".properties", ".gitignore", ".lua", ".kt", ".swift", ".r", ".m", ".pl",
}
SUPPORTED_DOCS = {".pdf", ".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pptm", ".odt", ".ods", ".odp",
                  ".html", ".htm"}


class ExtractError(Exception):
    pass


def extract_text(path: Path, max_chars: int = 20000) -> tuple[str, dict]:
    """Return (text, info). ``info`` has e.g. pages/sheets/truncated."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        text, info = _pdf(path, max_chars)
    elif ext in (".docx", ".docm"):
        text, info = _docx(path), {}
    elif ext in (".xlsx", ".xlsm"):
        text, info = _xlsx(path, max_chars)
    elif ext in (".pptx", ".pptm"):
        text, info = _pptx(path)
    elif ext in (".odt", ".ods", ".odp"):
        text, info = _odf(path), {}
    elif ext in (".html", ".htm"):
        text, info = _html(_read_text(path, max_chars * 4)), {}
    else:
        text, info = _read_text(path, max_chars + 1), {}
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    info = dict(info)
    info["chars"] = len(text)
    if len(text) > max_chars:
        text = text[:max_chars]
        info["truncated"] = True
    return text, info


# ----------------------------------------------------------------------------- plain text

def _read_text(path: Path, limit: int) -> str:
    with open(path, "rb") as fh:
        raw = fh.read(min(limit * 4 + 16, 64 * 2**20))
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    if b"\x00" in raw[:4096]:
        raise ExtractError("this looks like a binary file, not text")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


# ----------------------------------------------------------------------------- PDF

def _pdf(path: Path, max_chars: int) -> tuple[str, dict]:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise ExtractError("PDF support is not installed (pypdfium2)") from None
    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as e:  # password protected / corrupt
        raise ExtractError(f"cannot open PDF: {e}") from None
    parts, total = [], 0
    try:
        n = len(pdf)
        for i in range(n):
            page = pdf[i]
            try:
                tp = page.get_textpage()
                try:
                    t = tp.get_text_range()
                finally:
                    tp.close()
            finally:
                page.close()
            parts.append(f"[page {i + 1}]\n{t.strip()}")
            total += len(t)
            if total > max_chars:
                break
    finally:
        pdf.close()
    text = "\n\n".join(parts)
    if not text.replace("[page", "").strip(" \n]0123456789"):
        raise ExtractError("this PDF has no text layer (it is probably a scan)")
    return text, {"pages": n}


# ----------------------------------------------------------------------------- Office Open XML

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _zip_xml(z: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        info = z.getinfo(name)
    except KeyError:
        raise ExtractError(f"missing part {name}") from None
    if info.file_size > MAX_ZIP_MEMBER:
        raise ExtractError("document part is too large")
    return ET.fromstring(z.read(name))


def _open_zip(path: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ExtractError("not a valid Office document (old .doc/.xls/.ppt files are not supported)") from None


def _docx(path: Path) -> str:
    with _open_zip(path) as z:
        root = _zip_xml(z, "word/document.xml")
    out = []
    for p in root.iter(W + "p"):
        buf = []
        for node in p.iter():
            if node.tag == W + "t" and node.text:
                buf.append(node.text)
            elif node.tag == W + "tab":
                buf.append("\t")
            elif node.tag in (W + "br", W + "cr"):
                buf.append("\n")
        out.append("".join(buf))
    return "\n".join(out)


def _pptx(path: Path) -> tuple[str, dict]:
    with _open_zip(path) as z:
        slides = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                        key=lambda n: int(re.findall(r"\d+", n)[-1]))
        out = []
        for i, name in enumerate(slides, 1):
            root = _zip_xml(z, name)
            paras = ["".join(t.text or "" for t in p.iter(A + "t")) for p in root.iter(A + "p")]
            out.append(f"[slide {i}]\n" + "\n".join(x for x in paras if x.strip()))
    return "\n\n".join(out), {"slides": len(slides)}


def _xlsx(path: Path, max_chars: int) -> tuple[str, dict]:
    with _open_zip(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in _zip_xml(z, "xl/sharedStrings.xml").iter(S + "si"):
                shared.append("".join(t.text or "" for t in si.iter(S + "t")))
        wb = _zip_xml(z, "xl/workbook.xml")
        rels = _zip_xml(z, "xl/_rels/workbook.xml.rels")
        targets = {r.get("Id"): r.get("Target", "") for r in rels.iter(PR + "Relationship")}
        out, total, sheets = [], 0, 0
        for sheet in wb.iter(S + "sheet"):
            sheets += 1
            target = targets.get(sheet.get(R + "id"), "")
            target = target.lstrip("/")
            name = target if target.startswith("xl/") else f"xl/{target}"
            if name not in z.namelist():
                continue
            rows = []
            for row in _zip_xml(z, name).iter(S + "row"):
                cells = []
                for c in row.iter(S + "c"):
                    t = c.get("t")
                    v = c.find(S + "v")
                    if t == "s" and v is not None and v.text and v.text.isdigit():
                        idx = int(v.text)
                        cells.append(shared[idx] if idx < len(shared) else "")
                    elif t == "inlineStr":
                        cells.append("".join(x.text or "" for x in c.iter(S + "t")))
                    else:
                        cells.append(v.text if v is not None and v.text else "")
                if any(cells):
                    rows.append("\t".join(cells))
                    total += len(rows[-1])
                if total > max_chars:
                    break
            out.append(f"[sheet {sheet.get('name')}]\n" + "\n".join(rows))
            if total > max_chars:
                break
    return "\n\n".join(out), {"sheets": sheets}


# ----------------------------------------------------------------------------- OpenDocument, HTML

def _odf(path: Path) -> str:
    with _open_zip(path) as z:
        root = _zip_xml(z, "content.xml")
    text_ns = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
    out = []
    for el in root.iter():
        if el.tag in (text_ns + "p", text_ns + "h"):
            out.append("".join(el.itertext()))
    return "\n".join(out)


class _HtmlText(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "template"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:  # type: ignore[override]
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def _html(source: str) -> str:
    p = _HtmlText()
    p.feed(source)
    text = "".join(p.parts)
    return re.sub(r"[ \t]+", " ", text)
