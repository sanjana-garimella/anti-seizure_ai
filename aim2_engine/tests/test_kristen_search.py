"""
test_kristen_search.py
----------------------
Tests for tools/kristen_search.py, the retrieval-only search over kristen/.
Every fixture is a small SYNTHETIC file built in a temporary folder under
aim2_engine/ - the real kristen/ files are never read here. No model is ever
loaded: the --semantic path is tested only for its offline fallback.

Run:  python -m unittest discover -s tests
"""
import io
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ENGINE, "tools"))

import kristen_search as ks  # noqa: E402

CT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
      '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
SML = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
WML = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def write_xlsx(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", CT)
        z.writestr("xl/workbook.xml",
                   f'<workbook xmlns="{SML}" xmlns:r="{REL}"><sheets>'
                   f'<sheet name="Max doses" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   f'<Relationships xmlns="{PKG}">'
                   f'<Relationship Id="rId1" Type="{REL}/worksheet" Target="worksheets/sheet1.xml"/>'
                   f'</Relationships>')
        z.writestr("xl/sharedStrings.xml",
                   f'<sst xmlns="{SML}"><si><t>Drug</t></si><si><t>Ceiling mg/day</t></si>'
                   f'<si><t>Zonisamide</t></si><si><r><t>Hold at </t></r><r><t>ceiling</t></r></si>'
                   f'<si><t>Same note</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml",
                   f'<worksheet xmlns="{SML}"><sheetData>'
                   f'<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>'
                   f'<c r="C1" t="inlineStr"><is><t>Comment</t></is></c></row>'
                   f'<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>500.0</v></c>'
                   f'<c r="C2" t="s"><v>3</v></c></row>'
                   f'<row r="3"><c r="A3" t="inlineStr"><is><t>Zonisamide</t></is></c>'
                   f'<c r="C3" t="s"><v>4</v></c></row>'
                   f'<row r="4"><c r="A4" t="inlineStr"><is><t>Zonisamide</t></is></c>'
                   f'<c r="C4" t="s"><v>4</v></c></row>'
                   f'</sheetData></worksheet>')
        z.writestr("xl/worksheets/_rels/sheet1.xml.rels",
                   f'<Relationships xmlns="{PKG}">'
                   f'<Relationship Id="rId1" Type="{REL}/comments" Target="../comments1.xml"/>'
                   f'</Relationships>')
        z.writestr("xl/comments1.xml",
                   f'<comments xmlns="{SML}"><authors><author>Test PI</author></authors>'
                   f'<commentList><comment ref="B2" authorId="0"><text><r><t>check renal function'
                   f'</t></r></text></comment></commentList></comments>')


def write_docx(path):
    body = (
        f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>F. Switch medicine</w:t></w:r></w:p>'
        f'<w:p><w:commentRangeStart w:id="0"/><w:r><w:t xml:space="preserve">Reduce the old dose </w:t></w:r>'
        f'<w:ins w:id="1" w:author="Test PI"><w:r><w:t>weekly</w:t></w:r></w:ins>'
        f'<w:del w:id="2" w:author="Test PI"><w:r><w:delText>gradually overnight</w:delText></w:r></w:del>'
        f'<w:r><w:t xml:space="preserve"> while tapering.</w:t></w:r>'
        f'<w:r><w:commentReference w:id="0"/></w:r></w:p>'
        f'<w:p/>'
        f'<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Week</w:t></w:r></w:p></w:tc>'
        f'<w:tc><w:p><w:r><w:t>Dose</w:t></w:r></w:p></w:tc></w:tr>'
        f'<w:tr><w:tc><w:p><w:r><w:t>Week 2</w:t></w:r></w:p></w:tc>'
        f'<w:tc><w:p><w:r><w:t>current dose x0.75</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        f'<w:p><w:ins w:id="3" w:author="Test PI"><w:r><w:t>Brand new sentence.</w:t></w:r></w:ins></w:p>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", CT)
        z.writestr("word/document.xml", f'<w:document xmlns:w="{WML}"><w:body>{body}</w:body></w:document>')
        z.writestr("word/comments.xml",
                   f'<w:comments xmlns:w="{WML}"><w:comment w:id="0" w:author="Test PI">'
                   f'<w:p><w:r><w:t>Cross-taper over four weeks</w:t></w:r></w:p></w:comment></w:comments>')


BS = chr(92)   # a literal backslash, so the \uN escape below stays an RTF escape
RTF = (r"{\rtf1\ansi{\fonttbl\f0\fswiss Helvetica;}{\colortbl;\red255\green0\blue0;}"
       r"\f0 Call the clinic for any caf\'e9 rash\par Second " + BS + r"u8212? paragraph about drowsiness\par}")
MD = "# Questions\n\nIntro line about seizures.\n\n## Weaning\n\n- Wean slowly over months\n- Second bullet\n"
SVG = ('<svg xmlns="http://www.w3.org/2000/svg"><text x="1">Steady</text>'
       '<text x="2"><tspan>Barometer</tspan> <tspan>holding</tspan></text></svg>')
HTML = ("<html><head><style>.x{color:red}</style><script>var hiddenword=1;</script></head>"
        "<body><h1>Dose increments</h1><p>Step &amp; ceiling chart</p></body></html>")


class FixtureCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(dir=ENGINE, prefix=".test_kristen_")
        cls.root = os.path.join(cls.tmp.name, "kristen")
        cls.cache = os.path.join(cls.tmp.name, "cache")
        os.makedirs(cls.root)
        write_xlsx(os.path.join(cls.root, "doses.xlsx"))
        write_docx(os.path.join(cls.root, "templates - kb edits.docx"))
        for name, text in (("modif.rtf", RTF), ("notes.md", MD), ("chart.svg", SVG),
                           ("chart.html", HTML), ("~$templates.docx", "lock"),
                           (".DS_Store", "x"), ("data.bin", "x")):
            with open(os.path.join(cls.root, name), "w", encoding="utf-8") as fh:
                fh.write(text)
        with open(os.path.join(cls.root, "scan.pdf"), "wb") as fh:
            fh.write(b"%PDF-1.4 not a real pdf")
        with mock.patch.object(ks.shutil, "which", return_value=None):
            cls.chunks, cls.notes = ks.build_index(cls.root, cache_dir=None)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def find(self, file, loc):
        return next(c for c in self.chunks if c.file == file and c.loc == loc)


class TestExtraction(FixtureCase):
    def test_xlsx_cell_citation_with_row_label_and_header(self):
        c = self.find("doses.xlsx", "Max doses > B2")
        self.assertEqual(c.text, "500")
        self.assertIn("row: Zonisamide", c.context)
        self.assertIn("column: Ceiling mg/day", c.context)
        self.assertEqual(c.citation, "doses.xlsx > Max doses > B2")

    def test_xlsx_rich_shared_string_and_inline_string(self):
        self.assertEqual(self.find("doses.xlsx", "Max doses > C2").text, "Hold at ceiling")
        self.assertEqual(self.find("doses.xlsx", "Max doses > C1").text, "Comment")

    def test_xlsx_cell_note(self):
        c = self.find("doses.xlsx", "Max doses > B2 (note)")
        self.assertEqual(c.text, "check renal function")
        self.assertIn("Test PI", c.context)

    def test_docx_paragraph_numbering_heading_and_tracked_changes(self):
        c = self.find("templates - kb edits.docx", "paragraph #2")
        self.assertEqual(c.text, "Reduce the old dose weekly while tapering.")   # insertions accepted
        self.assertNotIn("overnight", c.text)                                   # deletions removed
        self.assertIn('inserted by Test PI: "weekly"', c.edits)
        self.assertIn('deleted by Test PI: "gradually overnight"', c.edits)
        self.assertEqual(c.context, "section: F. Switch medicine")

    def test_docx_wholly_inserted_paragraph_is_summarised(self):
        c = self.find("templates - kb edits.docx", "paragraph #4")
        self.assertEqual(c.text, "Brand new sentence.")
        self.assertEqual(c.edits, "added by Test PI")

    def test_docx_table_rows(self):
        c = self.find("templates - kb edits.docx", "table 1 > row 2")
        self.assertEqual(c.text, "Week 2 | current dose x0.75")

    def test_docx_comment_with_author_and_anchor(self):
        c = next(c for c in self.chunks if c.kind == "comment")
        self.assertEqual(c.text, "Cross-taper over four weeks")
        self.assertEqual(c.loc, "comment #1 by Test PI (on paragraph #2)")

    def test_rtf_strips_control_words_and_decodes(self):
        texts = [c.text for c in self.chunks if c.file == "modif.rtf"]
        self.assertEqual(texts[0], "Call the clinic for any café rash")
        self.assertIn("—", texts[1])
        self.assertFalse(any("Helvetica" in t or "red255" in t for t in texts))

    def test_md_lines_and_sections(self):
        c = next(c for c in self.chunks if c.file == "notes.md" and "Wean slowly" in c.text)
        self.assertEqual(c.loc, "line 7")
        self.assertEqual(c.context, "section: Weaning")

    def test_svg_and_html_text(self):
        svg = [c.text for c in self.chunks if c.file == "chart.svg"]
        self.assertIn("Barometer holding", svg)
        html = [c.text for c in self.chunks if c.file == "chart.html"]
        self.assertIn("Step & ceiling chart", html)
        self.assertFalse(any("hiddenword" in t or "color" in t for t in html))

    def test_skips_lock_files_and_notes_unsupported_and_pdf(self):
        files = {c.file for c in self.chunks}
        self.assertNotIn("~$templates.docx", files)
        self.assertNotIn(".DS_Store", files)
        self.assertTrue(any(n.startswith("data.bin: skipped") for n in self.notes))
        self.assertTrue(any(n.startswith("scan.pdf: skipped (pdftotext not installed") for n in self.notes))


class TestRanking(FixtureCase):
    def test_tokenizer_folds_suffixes_and_drops_stopwords(self):
        self.assertEqual(ks.tokenize("Tapering the doses"), ["taper", "dose"])
        self.assertEqual(ks.tokenize("what did Dr. Barbour say about weaning"), ["wean"])

    def test_bm25_prefers_rarer_and_denser_matches(self):
        docs = [["taper", "dose"], ["dose", "dose", "week"], ["week", "rash"]]
        s = ks.BM25(docs).scores(["taper"])
        self.assertGreater(s[0], 0)
        self.assertEqual(s[1], 0)
        self.assertEqual(s[2], 0)

    def search(self, q, **kw):
        kw.setdefault("cache_dir", None)
        with mock.patch.object(ks.shutil, "which", return_value=None):
            return ks.search(q, self.root, **kw)

    def test_search_returns_cited_hit(self):
        hits = self.search("tapering weekly")
        self.assertEqual(hits[0].citation, "templates - kb edits.docx > paragraph #2")
        self.assertEqual(hits[0].method, "bm25")

    def test_deleted_wording_is_still_findable(self):
        hits = self.search("overnight")
        self.assertEqual(hits[0].loc, "paragraph #2")

    def test_identical_cells_are_collapsed(self):
        hits = self.search("same note")
        self.assertEqual(len([h for h in hits if h.text == "Same note"]), 1)
        h = next(h for h in hits if h.text == "Same note")
        self.assertEqual(h.extra["same_text_at"], ["C4"])

    def test_file_filter_and_no_match(self):
        self.assertTrue(all(h.file == "modif.rtf" for h in self.search("rash", file_filter="modif")))
        self.assertEqual(self.search("xylophone"), [])

    def test_semantic_falls_back_offline_without_package(self):
        notes = []
        with mock.patch.dict(sys.modules, {"sentence_transformers": None}), \
                mock.patch.dict(os.environ, {}, clear=False):
            hits = self.search("rash", semantic=True, notes_out=notes)
            self.assertEqual(os.environ.get("HF_HUB_OFFLINE"), "1")
        self.assertTrue(hits)
        self.assertEqual(hits[0].method, "bm25")
        self.assertTrue(any("--semantic unavailable" in n for n in notes))


class TestCacheAndCli(FixtureCase):
    def test_cache_round_trip_and_invalidation(self):
        md = os.path.join(self.root, "extra.md")
        try:
            a, _ = ks.build_index(self.root, self.cache)
            b, _ = ks.build_index(self.root, self.cache)
            self.assertEqual([c.citation for c in a], [c.citation for c in b])
            with open(md, "w") as fh:
                fh.write("quokka\n")
            c, _ = ks.build_index(self.root, self.cache)
            self.assertTrue(any(ch.text == "quokka" for ch in c))
        finally:
            if os.path.exists(md):
                os.remove(md)

    def test_cache_outside_engine_is_refused(self):
        with self.assertRaises(ValueError):
            ks.build_index(self.root, os.path.dirname(ENGINE))

    def test_default_paths_stay_in_project(self):
        self.assertEqual(os.path.dirname(ks.DEFAULT_CACHE_DIR), ENGINE)
        self.assertEqual(ks.DEFAULT_ROOT, os.path.join(os.path.dirname(ENGINE), "kristen"))

    def test_cli_prints_citations_and_json(self):
        out = io.StringIO()
        with mock.patch.object(ks.shutil, "which", return_value=None):
            ks.main(["tapering", "weekly", "--root", self.root, "--no-cache"], out=out)
        self.assertIn("[1] templates - kb edits.docx > paragraph #2", out.getvalue())
        self.assertIn("tracked changes:", out.getvalue())
        out = io.StringIO()
        with mock.patch.object(ks.shutil, "which", return_value=None):
            ks.main(["renal", "--root", self.root, "--no-cache", "--json"], out=out)
        import json
        data = json.loads(out.getvalue())
        self.assertEqual(data["hits"][0]["citation"], "doses.xlsx > Max doses > B2 (note)")


class TestIsolation(unittest.TestCase):
    def test_no_network_or_generation_imports(self):
        with open(ks.__file__, encoding="utf-8") as fh:
            src = fh.read()
        for bad in ("import socket", "import urllib", "from urllib", "http.client",
                    "import requests", "openai", "anthropic", "pipeline("):
            self.assertNotIn(bad, src, bad)

    def test_engine_core_never_imports_search_tool(self):
        core = os.path.join(ENGINE, "engine_core")
        for fn in os.listdir(core):
            if fn.endswith(".py"):
                with open(os.path.join(core, fn), encoding="utf-8") as fh:
                    self.assertNotIn("kristen_search", fh.read(), fn)


if __name__ == "__main__":
    unittest.main()
