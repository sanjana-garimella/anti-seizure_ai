#!/usr/bin/env python3
"""
kristen_search.py - local, retrieval-only search over the PI's source folder
(kristen/). Ask "what did Dr. Barbour say about X" and get cited snippets.

WHAT IT DOES
    Indexes every file in kristen/ into small chunks, ranks them against your
    query with BM25 (pure Python), and prints the top hits with an exact
    citation for each:

        .xlsx  every non-empty cell     file > sheet > B4   (+ row label, column header)
               cell notes/comments      file > sheet > B4 (note)
        .docx  body paragraphs          file > paragraph #12   (+ section heading)
               table rows               file > table 2 > row 3
               tracked changes          shown on the paragraph as [inserted by ...] / [deleted by ...]
               comments                 file > comment #4 by <author> (on paragraph #12)
        .md    paragraphs               file > line 40   (+ heading)
        .rtf   paragraphs               file > paragraph #7
        .pdf   paragraphs per page      file > page 3 > para 2   (needs `pdftotext`; else skipped)
        .svg   <text> elements          file > text #5
        .html  block text               file > block #9

WHAT IT DOES NOT DO
    No generation, no summarising, no LLM, no network, no external services. It
    returns the PI's own words with a pointer to where they are. It is NOT part
    of the dose-decision path (engine_core/ never imports it) and its output is
    reference material only - the engine's rules in data/ remain the authority.
    Read-only on kristen/: it never writes there.

USAGE (from aim2_engine/)
    python3 tools/kristen_search.py "taper"
    python3 tools/kristen_search.py "lamotrigine rash" --top 5
    python3 tools/kristen_search.py "max dose" --file titration      # only files whose name contains "titration"
    python3 tools/kristen_search.py "taper" --json                   # machine-readable
    python3 tools/kristen_search.py --list                           # what got indexed / skipped
    python3 tools/kristen_search.py "when to call the clinic" --semantic

    --semantic  Re-ranks with sentence-transformers, fused with BM25 (reciprocal
                rank fusion). Used ONLY if the package is already installed AND
                the model is already cached on disk: the hub is forced offline
                (HF_HUB_OFFLINE=1) and loading uses local files only, so nothing
                is ever downloaded. Otherwise it says so and falls back to BM25.
                Default model: sentence-transformers/all-MiniLM-L6-v2 (--model).
    --root DIR  Search another folder instead of kristen/ (tests use this).
    --no-cache  Rebuild the index in memory without reading/writing the cache.

CACHE
    aim2_engine/.kristen_search_cache/ (git-ignored). Keyed on each file's size
    and mtime, so edits to kristen/ are picked up automatically. Safe to delete.
    The tool refuses to write a cache anywhere outside aim2_engine/.

Search tips: BM25 matches words, not meaning - try the PI's vocabulary
("taper", "wean", "breakthrough", "emergent") and drug names. Stopwords are
dropped and simple plurals/-ing/-ed are folded, so "tapering" finds "taper".
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict, dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_DIR = os.path.dirname(HERE)                      # aim2_engine/
DEFAULT_ROOT = os.path.join(os.path.dirname(ENGINE_DIR), "kristen")
DEFAULT_CACHE_DIR = os.path.join(ENGINE_DIR, ".kristen_search_cache")
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
INDEX_VERSION = 2
DELETED_ONLY = "(deleted text only)"

SUPPORTED = {".xlsx", ".docx", ".md", ".rtf", ".pdf", ".svg", ".html", ".htm"}

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


@dataclass
class Chunk:
    file: str            # path relative to the root
    loc: str             # location inside the file, e.g. "Sheet1 > B4"
    text: str            # the PI's text
    context: str = ""    # label/heading that helps a reader (and the ranker)
    kind: str = "text"   # text | cell | note | table | comment | edit
    edits: str = ""      # docx tracked changes on this paragraph/row, e.g. 'deleted by KB: "..."'

    @property
    def citation(self) -> str:
        return f"{self.file} > {self.loc}"


# ---------------------------------------------------------------- utilities

def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _read_zip_xml(z: zipfile.ZipFile, name: str):
    try:
        return ET.fromstring(z.read(name))
    except KeyError:
        return None


def _rels(z: zipfile.ZipFile, rels_path: str, base_dir: str) -> dict:
    """Relationship id -> absolute part name inside the zip."""
    root = _read_zip_xml(z, rels_path)
    out = {}
    if root is None:
        return out
    for rel in root.iter(PKG_REL + "Relationship"):
        tgt = rel.get("Target", "")
        if tgt.startswith("/"):
            part = tgt.lstrip("/")
        else:
            part = os.path.normpath(os.path.join(base_dir, tgt)).replace(os.sep, "/")
        out[rel.get("Id")] = (part, rel.get("Type", ""))
    return out


# ---------------------------------------------------------------- .xlsx

def _col_letters(ref: str) -> str:
    return re.match(r"[A-Z]+", ref).group(0) if ref and re.match(r"[A-Z]+", ref) else ""


def _row_num(ref: str) -> int:
    m = re.search(r"(\d+)$", ref or "")
    return int(m.group(1)) if m else 0


def _si_text(si) -> str:
    # concatenate <t> in this shared string, skipping phonetic runs (<rPh>)
    rph = {id(t) for r in si.iter(S + "rPh") for t in r.iter(S + "t")}
    return "".join(t.text or "" for t in si.iter(S + "t") if id(t) not in rph)


def _fmt_num(v: str) -> str:
    try:
        f = float(v)
    except ValueError:
        return v
    if f.is_integer() and abs(f) < 1e15:
        return str(int(f))
    return f"{f:.6g}"


def extract_xlsx(path: str, rel: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    with zipfile.ZipFile(path) as z:
        shared = []
        sst = _read_zip_xml(z, "xl/sharedStrings.xml")
        if sst is not None:
            shared = [_si_text(si) for si in sst.findall(S + "si")]
        wb = _read_zip_xml(z, "xl/workbook.xml")
        wb_rels = _rels(z, "xl/_rels/workbook.xml.rels", "xl")
        sheets = []
        if wb is not None:
            for sh in wb.iter(S + "sheet"):
                part = wb_rels.get(sh.get(R_ID), (None, ""))[0]
                if part:
                    sheets.append((sh.get("name", "?"), part))
        for sheet_name, part in sheets:
            root = _read_zip_xml(z, part)
            if root is None:
                continue
            cells: dict[int, dict[str, str]] = {}
            for c in root.iter(S + "c"):
                ref = c.get("r", "")
                t = c.get("t", "n")
                val = ""
                if t == "s":
                    v = c.find(S + "v")
                    if v is not None and v.text and v.text.isdigit() and int(v.text) < len(shared):
                        val = shared[int(v.text)]
                elif t == "inlineStr":
                    is_ = c.find(S + "is")
                    val = _si_text(is_) if is_ is not None else ""
                elif t == "b":
                    v = c.find(S + "v")
                    val = {"1": "TRUE", "0": "FALSE"}.get(v.text if v is not None else "", "")
                else:
                    v = c.find(S + "v")
                    val = (v.text or "") if v is not None else ""
                    if t == "n" and val:
                        val = _fmt_num(val)
                val = _norm_ws(val)
                if val and ref:
                    cells.setdefault(_row_num(ref), {})[_col_letters(ref)] = val
            # header row: first row with >= 2 non-empty cells
            header = {}
            for r in sorted(cells):
                if len(cells[r]) >= 2:
                    header = {"row": r, **cells[r]}
                    break
            for r in sorted(cells):
                row = cells[r]
                first_col = min(row, key=lambda col: (len(col), col))
                label = row[first_col]
                for col in sorted(row, key=lambda col: (len(col), col)):
                    ctx = []
                    if col != first_col:
                        ctx.append(f"row: {label}")
                    if header and r != header["row"] and header.get(col):
                        ctx.append(f"column: {header[col]}")
                    chunks.append(Chunk(rel, f"{sheet_name} > {col}{r}", row[col],
                                        "; ".join(ctx), "cell"))
            # legacy cell notes / comments attached to this sheet
            sdir = os.path.dirname(part)
            srels = _rels(z, f"{sdir}/_rels/{os.path.basename(part)}.rels", sdir)
            for target, typ in srels.values():
                if not typ.endswith("/comments"):
                    continue
                croot = _read_zip_xml(z, target)
                if croot is None:
                    continue
                authors = [a.text or "" for a in croot.iter(S + "author")]
                for cm in croot.iter(S + "comment"):
                    txt = _norm_ws("".join(t.text or "" for t in cm.iter(S + "t")))
                    if not txt:
                        continue
                    aid = cm.get("authorId", "")
                    who = authors[int(aid)] if aid.isdigit() and int(aid) < len(authors) else ""
                    chunks.append(Chunk(rel, f"{sheet_name} > {cm.get('ref', '?')} (note)", txt,
                                        f"note by {who}" if who else "note", "note"))
    return chunks


# ---------------------------------------------------------------- .docx

def _docx_segments(el, state=None, out=None):
    """Walk a paragraph and return [(kind, text, author)] with kind in
    {'keep', 'ins', 'del'} so tracked changes can be shown separately."""
    if out is None:
        out, state = [], ("keep", "")
    tag = el.tag
    if tag in (W + "ins", W + "moveTo"):
        state = ("ins", el.get(W + "author", ""))
    elif tag in (W + "del", W + "moveFrom"):
        state = ("del", el.get(W + "author", ""))
    if tag in (W + "t", W + "delText", W + "instrText"):
        if tag != W + "instrText" and el.text:
            kind = state[0]
            if tag == W + "delText":
                kind = "del"
            out.append((kind, el.text, state[1]))
    elif tag == W + "tab":
        out.append((state[0], " ", state[1]))
    elif tag in (W + "br", W + "cr"):
        out.append((state[0], " ", state[1]))
    for child in el:
        _docx_segments(child, state, out)
    return out


def _para_texts(p):
    """(final text with tracked changes accepted, [edit notes])."""
    segs = _docx_segments(p)
    final = _norm_ws("".join(t for k, t, _ in segs if k != "del"))
    edits = []
    # merge consecutive segments of the same kind/author into one edit note
    cur = None
    for k, t, a in segs:
        if k == "keep":
            if cur:
                edits.append(cur)
                cur = None
            continue
        if cur and cur[0] == k and cur[2] == a:
            cur = (k, cur[1] + t, a)
        else:
            if cur:
                edits.append(cur)
            cur = (k, t, a)
    if cur:
        edits.append(cur)
    notes = [(k, _norm_ws(t), a) for k, t, a in edits if _norm_ws(t)]
    return final, notes


def _edit_summary(notes, whole_text_inserted: bool) -> str:
    """One short line per tracked change. A paragraph that is entirely new is
    summarised as 'added by X' instead of repeating its text."""
    out = []
    for k, t, a in notes:
        by = f" by {a}" if a else ""
        if k == "ins":
            if whole_text_inserted:
                if f"added{by}" not in out:
                    out.append(f"added{by}")
                continue
            out.append(f'inserted{by}: "{t}"')
        else:
            out.append(f'deleted{by}: "{t}"')
    return "; ".join(out)


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _ctx(label: str, value: str, limit: int = 140) -> str:
    if not value:
        return ""
    value = value if len(value) <= limit else value[:limit].rsplit(" ", 1)[0] + "..."
    return f"{label}: {value}"


def _is_heading(p) -> bool:
    ps = p.find(f"{W}pPr/{W}pStyle")
    val = (ps.get(W + "val", "") if ps is not None else "").lower()
    return val.startswith("heading") or val in ("title", "subtitle")


def extract_docx(path: str, rel: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    with zipfile.ZipFile(path) as z:
        doc = _read_zip_xml(z, "word/document.xml")
        if doc is None:
            return chunks
        body = doc.find(W + "body")
        if body is None:
            return chunks
        comment_anchor: dict[str, int] = {}
        heading = ""
        pnum = tnum = 0

        def body_items(parent):
            for child in parent:
                if child.tag == W + "sdt":
                    content = child.find(W + "sdtContent")
                    if content is not None:
                        yield from body_items(content)
                else:
                    yield child

        for el in body_items(body):
            if el.tag == W + "p":
                pnum += 1
                for cs in el.iter(W + "commentRangeStart"):
                    comment_anchor.setdefault(cs.get(W + "id"), pnum)
                for cr in el.iter(W + "commentReference"):
                    comment_anchor.setdefault(cr.get(W + "id"), pnum)
                final, notes = _para_texts(el)
                if _is_heading(el) and final:
                    heading = final
                if final or notes:
                    new = bool(final) and _squash(final) == _squash("".join(t for k, t, _ in notes if k == "ins"))
                    chunks.append(Chunk(rel, f"paragraph #{pnum}", final or DELETED_ONLY,
                                        _ctx("section", heading) if heading != final else "",
                                        "edit" if notes else "text", _edit_summary(notes, new)))
            elif el.tag == W + "tbl":
                tnum += 1
                rnum = 0
                for tr in el.iter(W + "tr"):
                    rnum += 1
                    cells, notes = [], []
                    for tc in tr.findall(W + "tc"):
                        ptexts = []
                        for p in tc.iter(W + "p"):
                            for cs in p.iter(W + "commentRangeStart"):
                                comment_anchor.setdefault(cs.get(W + "id"), -tnum)
                            f, n = _para_texts(p)
                            if f:
                                ptexts.append(f)
                            notes.extend(n)
                        cells.append(" ".join(ptexts))
                    if any(cells) or notes:
                        text = " | ".join(cells).strip()
                        new = bool(text) and all(k == "ins" for k, _, _ in notes) and \
                            _squash("".join(cells)) == _squash("".join(t for _, t, _ in notes))
                        chunks.append(Chunk(rel, f"table {tnum} > row {rnum}", text or DELETED_ONLY,
                                            _ctx("section", heading),
                                            "edit" if notes else "table", _edit_summary(notes, new)))
        com = _read_zip_xml(z, "word/comments.xml")
        if com is not None:
            for i, cm in enumerate(com.iter(W + "comment"), 1):
                txt = _norm_ws(" ".join(_para_texts(p)[0] for p in cm.iter(W + "p")))
                if not txt:
                    continue
                cid = cm.get(W + "id")
                who = cm.get(W + "author", "")
                anchor = comment_anchor.get(cid)
                on = ""
                if anchor is not None:
                    on = f" (on paragraph #{anchor})" if anchor > 0 else f" (in table {-anchor})"
                anchored = ""
                if anchor and anchor > 0:
                    anchored = next((c.text for c in chunks if c.loc == f"paragraph #{anchor}"), "")
                chunks.append(Chunk(rel, f"comment #{i}{' by ' + who if who else ''}{on}", txt,
                                    f"on: {anchored[:160]}" if anchored else "", "comment"))
    return chunks


# ---------------------------------------------------------------- .md

def extract_md(path: str, rel: str) -> list[Chunk]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    chunks, buf, start, heading = [], [], 0, ""

    def flush():
        if buf:
            text = _norm_ws(" ".join(buf))
            if text:
                chunks.append(Chunk(rel, f"line {start}", text,
                                    _ctx("section", heading)))
        buf.clear()

    in_table = False
    for i, line in enumerate(lines, 1):
        m = re.match(r"^\s{0,3}(#{1,6})\s+(.*)$", line)
        if m:
            flush()
            heading = _norm_ws(m.group(2).strip("# "))
            chunks.append(Chunk(rel, f"line {i}", heading, "heading"))
            continue
        is_row = line.strip().startswith("|")
        if not line.strip() or (is_row != in_table and buf):
            flush()
        in_table = is_row
        if is_row:
            # table rows are their own chunks
            if re.match(r"^\s*\|[\s:\-|]+\|\s*$", line):
                continue
            flush()
            start = i
            buf.append(line.strip().strip("|").replace("|", " | "))
            flush()
            continue
        if line.strip():
            if not buf:
                start = i
            # bullets start a new chunk so lists stay granular
            if re.match(r"^\s*([-*+]|\d+[.)])\s+", line) and buf:
                flush()
                start = i
            buf.append(line.strip())
    flush()
    return chunks


# ---------------------------------------------------------------- .rtf

def rtf_to_text(data: str) -> str:
    """Minimal RTF -> text: drop destinations like fonttbl/colortbl, decode
    \\'hh and \\uN, turn \\par / \\line into newlines."""
    out, stack, skip = [], [], False
    i, n = 0, len(data)
    ignorable = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "listtable",
                 "listoverridetable", "expandedcolortbl", "generator", "themedata",
                 "colorschememapping", "latentstyles", "datastore", "xmlnstbl",
                 "rsidtbl", "mmathPr", "header", "footer", "listtext", "pntext"}
    uc_skip = 1
    while i < n:
        ch = data[i]
        if ch == "{":
            stack.append(skip)
            i += 1
            if data.startswith("\\*", i):
                skip = True
        elif ch == "}":
            skip = stack.pop() if stack else False
            i += 1
        elif ch == "\\":
            m = re.match(r"\\([a-zA-Z]+)(-?\d+)? ?", data[i:])
            if m:
                word, arg = m.group(1), m.group(2)
                i += m.end()
                if word in ignorable:
                    skip = True
                elif skip:
                    pass
                elif word in ("par", "line", "row", "sect", "page"):
                    out.append("\n")
                elif word in ("tab", "cell"):
                    out.append(" ")
                elif word == "uc" and arg:
                    uc_skip = int(arg)
                elif word == "u" and arg:
                    code = int(arg)
                    out.append(chr(code + 65536 if code < 0 else code))
                    # skip the ANSI fallback characters
                    k = 0
                    while k < uc_skip and i < n:
                        if data.startswith("\\'", i):
                            i += 4
                        elif data[i] in "{}\\":
                            break
                        else:
                            i += 1
                        k += 1
                continue
            nxt = data[i + 1] if i + 1 < n else ""
            if nxt == "'":
                if not skip:
                    try:
                        out.append(bytes([int(data[i + 2:i + 4], 16)]).decode("cp1252"))
                    except (ValueError, UnicodeDecodeError):
                        pass
                i += 4
            elif nxt in "\\{}":
                if not skip:
                    out.append(nxt)
                i += 2
            elif nxt in "\r\n":
                if not skip:
                    out.append("\n")
                i += 2
            else:
                i += 2
        elif ch in "\r\n":
            i += 1
        else:
            if not skip:
                out.append(ch)
            i += 1
    return "".join(out)


def extract_rtf(path: str, rel: str) -> list[Chunk]:
    with open(path, encoding="latin-1") as fh:
        text = rtf_to_text(fh.read())
    chunks, num = [], 0
    for para in text.split("\n"):
        para = _norm_ws(para)
        if para:
            num += 1
            chunks.append(Chunk(rel, f"paragraph #{num}", para))
    return chunks


# ---------------------------------------------------------------- .pdf

def extract_pdf(path: str, rel: str, notes: list) -> list[Chunk]:
    exe = shutil.which("pdftotext")
    if not exe:
        notes.append(f"{rel}: skipped (pdftotext not installed; `brew install poppler` to index PDFs)")
        return []
    try:
        res = subprocess.run([exe, "-enc", "UTF-8", "-layout", path, "-"],
                             capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        notes.append(f"{rel}: skipped (pdftotext failed: {e})")
        return []
    if res.returncode != 0:
        notes.append(f"{rel}: skipped (pdftotext exit {res.returncode})")
        return []
    chunks = []
    for pno, page in enumerate(res.stdout.decode("utf-8", "replace").split("\f"), 1):
        for qno, para in enumerate([p for p in re.split(r"\n\s*\n", page) if p.strip()], 1):
            text = _norm_ws(para)
            if text:
                chunks.append(Chunk(rel, f"page {pno} > para {qno}", text))
    return chunks


# ---------------------------------------------------------------- .svg / .html

def extract_svg(path: str, rel: str) -> list[Chunk]:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return _html_chunks(fh.read(), rel, "text")
    chunks, num = [], 0
    for el in root.iter():
        local = el.tag.rsplit("}", 1)[-1]
        if local in ("text", "title", "desc"):
            num += 1
            txt = _norm_ws(" ".join(el.itertext()))
            if txt:
                chunks.append(Chunk(rel, f"{local} #{num}", txt))
    return chunks


_BLOCK = r"</?(p|div|li|ul|ol|tr|table|h[1-6]|br|section|article|header|footer|td|th|title|text|tspan|option|label|button|caption)\b[^>]*>"


def _html_chunks(src: str, rel: str, label: str) -> list[Chunk]:
    src = re.sub(r"(?is)<!--.*?-->", " ", src)
    src = re.sub(r"(?is)<(script|style)\b.*?</\1>", "\n", src)
    src = re.sub(r"(?i)" + _BLOCK, "\n", src)
    src = re.sub(r"(?s)<[^>]+>", " ", src)
    chunks, num = [], 0
    for block in src.split("\n"):
        txt = _norm_ws(html.unescape(block))
        if txt:
            num += 1
            chunks.append(Chunk(rel, f"{label} #{num}", txt))
    return chunks


def extract_html(path: str, rel: str) -> list[Chunk]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return _html_chunks(fh.read(), rel, "block")


# ---------------------------------------------------------------- indexing

def _iter_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fn in sorted(filenames):
            yield os.path.join(dirpath, fn)


def extract_file(path: str, rel: str, notes: list) -> list[Chunk]:
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".xlsx":
            return extract_xlsx(path, rel)
        if ext == ".docx":
            return extract_docx(path, rel)
        if ext == ".md":
            return extract_md(path, rel)
        if ext == ".rtf":
            return extract_rtf(path, rel)
        if ext == ".pdf":
            return extract_pdf(path, rel, notes)
        if ext == ".svg":
            return extract_svg(path, rel)
        if ext in (".html", ".htm"):
            return extract_html(path, rel)
    except (zipfile.BadZipFile, ET.ParseError, OSError, KeyError, ValueError) as e:
        notes.append(f"{rel}: skipped (could not read: {type(e).__name__}: {e})")
    return []


def _signature(root: str) -> list:
    sig = []
    for p in _iter_files(root):
        try:
            st = os.stat(p)
        except OSError:
            continue
        sig.append([os.path.relpath(p, root), st.st_size, int(st.st_mtime_ns)])
    return sig


def _safe_cache_dir(cache_dir: str) -> str:
    real = os.path.realpath(cache_dir)
    if os.path.commonpath([real, os.path.realpath(ENGINE_DIR)]) != os.path.realpath(ENGINE_DIR):
        raise ValueError(f"cache dir must be inside {ENGINE_DIR}: {cache_dir}")
    return real


def _cache_path(cache_dir: str, root: str, suffix: str) -> str:
    key = hashlib.sha256(os.path.realpath(root).encode()).hexdigest()[:16]
    return os.path.join(cache_dir, f"index-{key}{suffix}")


def build_index(root: str = DEFAULT_ROOT, cache_dir: str | None = DEFAULT_CACHE_DIR):
    """Return (chunks, notes). Uses/refreshes the JSON cache when cache_dir is set."""
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise FileNotFoundError(f"folder not found: {root}")
    sig = _signature(root)
    cpath = None
    if cache_dir:
        cache_dir = _safe_cache_dir(cache_dir)
        cpath = _cache_path(cache_dir, root, ".json")
        try:
            with open(cpath, encoding="utf-8") as fh:
                cached = json.load(fh)
            if cached.get("version") == INDEX_VERSION and cached.get("signature") == sig \
                    and cached.get("pdftotext") == bool(shutil.which("pdftotext")):
                return [Chunk(**c) for c in cached["chunks"]], cached["notes"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    chunks: list[Chunk] = []
    notes: list[str] = []
    for p in _iter_files(root):
        rel = os.path.relpath(p, root)
        base = os.path.basename(p)
        if base.startswith(".") or base.startswith("~$"):
            continue                       # .DS_Store, Office lock files
        if os.path.splitext(p)[1].lower() not in SUPPORTED:
            notes.append(f"{rel}: skipped (unsupported type)")
            continue
        got = extract_file(p, rel, notes)
        if not got and not any(n.startswith(rel + ":") for n in notes):
            notes.append(f"{rel}: no text found")
        chunks.extend(got)
    if cpath:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            tmp = cpath + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"version": INDEX_VERSION, "signature": sig,
                           "pdftotext": bool(shutil.which("pdftotext")),
                           "chunks": [asdict(c) for c in chunks], "notes": notes}, fh)
            os.replace(tmp, cpath)
        except OSError:
            pass
    return chunks, notes


# ---------------------------------------------------------------- BM25

STOPWORDS = set("""a an and are as at be but by can could did do does for from had has
have he her his how i if in into is it its me my no not of on or our she so than that
the their them then there these they this those to too us was we were what when where
which who why will with would you your about said say says dr barbour kristen""".split())


def _stem(tok: str) -> str:
    if tok.isdigit() or len(tok) <= 3:
        return tok
    for suf in ("ing", "ed"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 3:
            base = tok[: -len(suf)]
            if len(base) >= 2 and base[-1] == base[-2] and base[-1] not in "ls":
                base = base[:-1]           # "stopped" -> "stop"
            return base
    if tok.endswith("ies") and len(tok) > 4:
        return tok[:-3] + "y"
    if tok.endswith(("sses", "xes", "zes", "ches", "shes")):      # classes, boxes, patches
        return tok[:-2]
    if tok.endswith("s") and not tok.endswith(("ss", "us", "is")):
        return tok[:-1]
    return tok


def tokenize(text: str) -> list[str]:
    toks = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", (text or "").lower())
    return [_stem(t) for t in toks if t not in STOPWORDS]


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tfs = [Counter(d) for d in docs]
        self.lens = [len(d) for d in docs]
        self.avg = (sum(self.lens) / len(docs)) if docs else 0.0
        df = Counter()
        for tf in self.tfs:
            df.update(tf.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        q = [t for t in dict.fromkeys(query) if t in self.idf]
        out = []
        for tf, ln in zip(self.tfs, self.lens):
            s = 0.0
            norm = self.k1 * (1 - self.b + self.b * ln / self.avg) if self.avg else self.k1
            for t in q:
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + norm)
            out.append(s)
        return out


def _index_text(c: Chunk) -> str:
    # the file name and context label are searchable too, so "taper template F"
    # can find the right document even if the paragraph itself is terse
    # (deleted wording stays findable; inserted wording is already in c.text)
    name = os.path.splitext(os.path.basename(c.file))[0]
    deleted = " ".join(re.findall(r'deleted[^:]*: "([^"]*)"', c.edits))
    # a comment's context quotes the paragraph it is on - shown, not indexed,
    # so the comment does not outrank the paragraph itself
    context = "" if c.kind == "comment" else c.context
    return f"{c.text} {deleted} {context} {name} {c.loc}"


# ---------------------------------------------------------------- semantic (optional)

def _semantic_scores(query: str, chunks: list[Chunk], model_name: str,
                     cache_dir: str | None, root: str):
    """Cosine scores from a locally cached sentence-transformers model, or
    (None, reason) if that is not possible without a download."""
    os.environ["HF_HUB_OFFLINE"] = "1"          # never touch the network
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer
    except Exception as e:                       # not installed / broken install
        return None, f"sentence-transformers not available ({type(e).__name__})"
    try:
        try:
            model = SentenceTransformer(model_name, local_files_only=True, device="cpu")
        except TypeError:                         # older versions: no local_files_only kwarg
            model = SentenceTransformer(model_name, device="cpu")
    except Exception as e:
        return None, f"model {model_name!r} is not cached locally ({type(e).__name__})"
    texts = [_index_text(c) for c in chunks]
    emb = None
    epath = None
    if cache_dir:
        cache_dir = _safe_cache_dir(cache_dir)
        h = hashlib.sha256((model_name + "\0" + "\0".join(texts)).encode()).hexdigest()[:16]
        epath = _cache_path(cache_dir, root, f"-emb-{h}.npy")
        if os.path.exists(epath):
            try:
                emb = np.load(epath)
                if emb.shape[0] != len(texts):
                    emb = None
            except Exception:
                emb = None
    if emb is None:
        emb = model.encode(texts, batch_size=64, normalize_embeddings=True,
                           show_progress_bar=False)
        if epath:
            try:
                os.makedirs(os.path.dirname(epath), exist_ok=True)
                stem = os.path.basename(_cache_path(cache_dir, root, "-emb-"))
                for old in os.listdir(cache_dir):     # drop embeddings of older index versions
                    if old.startswith(stem) and old.endswith(".npy"):
                        os.remove(os.path.join(cache_dir, old))
                np.save(epath, emb)
            except OSError:
                pass
    q = model.encode([query], normalize_embeddings=True, show_progress_bar=False)[0]
    # float64 avoids spurious overflow warnings from Accelerate's float32 matmul
    emb64 = np.asarray(emb, dtype=np.float64)
    q64 = np.asarray(q, dtype=np.float64)
    with np.errstate(all="ignore"):
        sims = np.nan_to_num(emb64 @ q64)
    return [float(x) for x in sims], None


# ---------------------------------------------------------------- search

@dataclass
class Hit:
    rank: int
    score: float
    citation: str
    file: str
    loc: str
    kind: str
    context: str
    text: str
    snippet: str = ""
    method: str = "bm25"
    edits: str = ""
    extra: dict = field(default_factory=dict)


def _snippet(text: str, qtoks: list[str], width: int = 320) -> str:
    if len(text) <= width:
        return text
    words = re.finditer(r"[A-Za-z0-9]+", text)
    pos = next((m.start() for m in words if _stem(m.group(0).lower()) in set(qtoks)), 0)
    start = max(0, pos - width // 3)
    end = min(len(text), start + width)
    start = max(0, end - width)
    return ("..." if start else "") + text[start:end].strip() + ("..." if end < len(text) else "")


def search(query: str, root: str = DEFAULT_ROOT, top: int = 10, file_filter: str = "",
           semantic: bool = False, model: str = DEFAULT_MODEL,
           cache_dir: str | None = DEFAULT_CACHE_DIR, notes_out: list | None = None) -> list[Hit]:
    chunks, notes = build_index(root, cache_dir)
    if notes_out is not None:
        notes_out.extend(notes)
    if file_filter:
        ff = file_filter.lower()
        chunks = [c for c in chunks if ff in c.file.lower()]
    if not chunks:
        return []
    qtoks = tokenize(query)
    bm = BM25([tokenize(_index_text(c)) for c in chunks]).scores(qtoks)
    # a paragraph that is nothing but a deleted fragment ("Dr. [Name]") is short,
    # so its section heading alone would rank it high - halve it
    bm = [x * 0.5 if c.text == DELETED_ONLY else x for x, c in zip(bm, chunks)]
    method = "bm25"
    final = bm
    if semantic:
        sem, why = _semantic_scores(query, chunks, model, cache_dir, os.path.abspath(root))
        if sem is None:
            if notes_out is not None:
                notes_out.append(f"--semantic unavailable: {why}; using BM25 only")
        else:
            # reciprocal rank fusion of the two rankings (k=60 is the usual constant)
            method = "bm25+semantic"
            rb = {i: r for r, i in enumerate(sorted(range(len(bm)), key=lambda i: -bm[i]), 1)}
            rs = {i: r for r, i in enumerate(sorted(range(len(sem)), key=lambda i: -sem[i]), 1)}
            final = [(1 / (60 + rb[i]) if bm[i] > 0 else 0) + 1 / (60 + rs[i])
                     for i in range(len(chunks))]
    order = sorted((i for i in range(len(chunks)) if final[i] > 0),
                   key=lambda i: (-final[i], chunks[i].file, i))
    # collapse identical text (same file, same context) into one hit, e.g. a
    # spreadsheet column that repeats "At maximum" down 20 rows
    hits, seen = [], {}
    for i in order:
        c = chunks[i]
        key = (c.file, c.loc.split(" > ")[0] if c.kind == "cell" else c.loc, c.text, c.context)
        if c.kind == "cell" and key in seen:
            seen[key].extra.setdefault("same_text_at", []).append(c.loc.split(" > ")[-1])
            continue
        if len(hits) >= max(0, top):
            continue
        h = Hit(len(hits) + 1, round(final[i], 4), c.citation, c.file, c.loc, c.kind, c.context,
                c.text, _snippet(c.text, qtoks), method, c.edits)
        seen[key] = h
        hits.append(h)
    return hits


def _print_hits(query, hits, notes, out):
    print(f'kristen/ search: "{query}"  ({len(hits)} hit{"s" if len(hits) != 1 else ""}, '
          f'{hits[0].method if hits else "bm25"})', file=out)
    print("Reference only - the PI's source text, not a recommendation. "
          "Verify in the original file.\n", file=out)
    if not hits:
        print("No matches. Try the PI's own words or a drug name.", file=out)
    for h in hits:
        print(f"[{h.rank}] {h.citation}   (score {h.score:g})", file=out)
        if h.context:
            print(f"    {h.context}", file=out)
        print(f"    {h.snippet}", file=out)
        if h.edits:
            print(f"    tracked changes: {h.edits if len(h.edits) <= 400 else h.edits[:400] + '...'}", file=out)
        same = h.extra.get("same_text_at")
        if same:
            print(f"    same text also at: {', '.join(same[:12])}{' ...' if len(same) > 12 else ''}", file=out)
        print(file=out)
    for n in notes:
        if n.startswith("--semantic"):
            print(f"note: {n}", file=out)


def main(argv=None, out=None) -> int:
    out = out or sys.stdout
    ap = argparse.ArgumentParser(description="Retrieval-only search over the PI's kristen/ folder "
                                             "(cited snippets, no generation).")
    ap.add_argument("query", nargs="*", help="words to search for")
    ap.add_argument("--top", "-n", type=int, default=10, help="number of hits (default 10)")
    ap.add_argument("--file", "-f", default="", help="only files whose path contains this text")
    ap.add_argument("--json", action="store_true", help="print hits as JSON")
    ap.add_argument("--list", action="store_true", help="list indexed files and skip notes")
    ap.add_argument("--semantic", action="store_true",
                    help="fuse BM25 with a locally cached sentence-transformers model (no downloads)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"embedding model (default {DEFAULT_MODEL})")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="folder to search (default: kristen/)")
    ap.add_argument("--no-cache", action="store_true", help="do not read or write the index cache")
    a = ap.parse_args(argv)
    cache = None if a.no_cache else DEFAULT_CACHE_DIR

    if a.list:
        chunks, notes = build_index(a.root, cache)
        per = Counter(c.file for c in chunks)
        for f in sorted(per):
            print(f"{per[f]:6d} chunks  {f}", file=out)
        for n in notes:
            print(f"  note: {n}", file=out)
        print(f"{sum(per.values())} chunks from {len(per)} files", file=out)
        return 0
    query = " ".join(a.query).strip()
    if not query:
        ap.error("give a query, e.g.  kristen_search.py \"taper\"   (or --list)")
    notes: list = []
    hits = search(query, a.root, a.top, a.file, a.semantic, a.model, cache, notes)
    if a.json:
        json.dump({"query": query, "hits": [asdict(h) for h in hits],
                   "notes": notes}, out, indent=2, ensure_ascii=False)
        print(file=out)
    else:
        _print_hits(query, hits, notes, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
