import csv
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "convert_oxygen_webhelp.py"
SPEC = importlib.util.spec_from_file_location("convert_oxygen_webhelp", SCRIPT)
IMPORTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = IMPORTER
SPEC.loader.exec_module(IMPORTER)


TOPIC = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE topic PUBLIC "-//OASIS//DTD DITA Topic//EN" "topic.dtd">
<topic id="topic1">
  <title><?FM MARKER [Description] ignored?>Example
    Topic</title>
  <shortdesc>A converted help topic.</shortdesc>
  <prolog><metadata><keywords><keyword>hidden</keyword></keywords></metadata></prolog>
  <body>
    <p>Use the <codeph>Column()</codeph> function in <keyword keyref="version"/>.</p>
    <note type="tip"><p>Keep this real content.</p></note>
    <codeblock outputclass="language-jsl">Column("age");</codeblock>
    <fig>
      <title>A sample diagram</title>
      <image href="Images/diagram.svg"/>
    </fig>
    <p>Choose <menucascade><uicontrol>Cols</uicontrol><uicontrol>Recode</uicontrol></menucascade>.</p>
    <p><xref href="related.xml#related1/details"/> and
      <xref href="/UG/related.xml">Absolute topic link</xref> and
      <xref href="https://example.com/" scope="external" format="html">External link</xref>.</p>
    <p><xref href="javascript:alert(1)">Unsafe link</xref></p>
    <p conref="../reuse/snippet.xml#snippet/shared"/>
    <p><table>
      <title>Options</title>
      <tgroup cols="2">
        <colspec colname="c1" colnum="1"/>
        <colspec colname="c2" colnum="2"/>
        <thead><row><entry namest="c1" nameend="c2">Heading</entry></row></thead>
        <tbody><row><entry>A</entry><entry>B</entry></row>
        <row><entry><p><cite>Discovering JMP</cite></p></entry><entry><p>Starting point.</p></entry></row></tbody>
      </tgroup>
    </table></p>
    <draft-comment author="reviewer">Internal review note</draft-comment>
  </body>
  <topic id="nested">
    <title>Nested Section</title>
    <body><p>Nested body.</p></body>
  </topic>
</topic>
"""

RELATED = """<?xml version="1.0" encoding="UTF-8"?>
<topic id="related1">
  <title>Related Topic</title>
  <body><p id="details">Details.</p></body>
</topic>
"""

SNIPPET = """<?xml version="1.0" encoding="UTF-8"?>
<topic id="snippet">
  <title>Snippets</title>
  <body><p id="shared">Shared text from reuse.</p></body>
</topic>
"""

NESTED_MAP = """<?xml version="1.0" encoding="UTF-8"?>
<map>
  <title>UG</title>
  <topicref href="topic.xml"/>
  <topicref href="missing.xml"/>
</map>
"""

ROOT_MAP = """<?xml version="1.0" encoding="UTF-8"?>
<map>
  <title>JMP Help</title>
  <keydef keys="version">
    <topicmeta><keywords><keyword>2027</keyword></keywords></topicmeta>
  </keydef>
  <mapref href="UG/_ug.ditamap"/>
  <topicref href="UG/related.xml"/>
</map>
"""


class OxygenWebHelpImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.output = self.root / "output"
        (self.source / "UG" / "Images").mkdir(parents=True)
        (self.source / "reuse").mkdir(parents=True)
        (self.source / "JMP_Documentation_Library.ditamap").write_text(ROOT_MAP, encoding="utf-8")
        (self.source / "UG" / "_ug.ditamap").write_text(NESTED_MAP, encoding="utf-8")
        (self.source / "UG" / "topic.xml").write_text(TOPIC, encoding="utf-8")
        (self.source / "UG" / "related.xml").write_text(RELATED, encoding="utf-8")
        (self.source / "reuse" / "snippet.xml").write_text(SNIPPET, encoding="utf-8")
        (self.source / "UG" / "Images" / "diagram.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8"
        )
        (self.root / "outside.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8"
        )
        # An image that escapes the source folder must be reported and not copied.
        topic_path = self.source / "UG" / "topic.xml"
        topic_path.write_text(
            TOPIC.replace(
                "</fig>",
                '<image href="../../outside.svg"><alt>Outside file</alt></image></fig>',
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def read_csv(self, name):
        with (self.output / "_migration" / name).open(encoding="utf-8", newline="") as file:
            return list(csv.DictReader(file))

    def test_stages_dita_topics_from_the_root_map(self):
        summary = IMPORTER.convert(self.source, self.output)

        # topic.xml, related.xml, and the nested topic "nested" as its own child page.
        self.assertEqual(summary["topicPages"], 3)
        self.assertEqual(summary["topicsInMap"], 2)
        self.assertEqual(summary["copiedAssets"], 0)
        self.assertEqual(summary["unhandledElements"], 0)

        page = (self.output / "help" / "ug" / "topic.html").read_text(encoding="utf-8")
        self.assertTrue(page.startswith("<body>\n<header></header>\n<main><div>"))
        self.assertTrue(page.rstrip().endswith("</main>\n<footer></footer>\n</body>"))
        self.assertIn("<h1>Example Topic</h1>", page)
        self.assertIn("A converted help topic.", page)
        self.assertIn("<code>Column()</code>", page)
        self.assertIn("2027", page)
        self.assertIn("<blockquote>", page)
        self.assertIn("<pre class=\"language-jsl\"><code>Column(\"age\");</code></pre>", page)
        self.assertIn("<figure><figcaption>A sample diagram</figcaption>", page)
        self.assertIn("<strong>Cols</strong> &gt; <strong>Recode</strong>", page)
        self.assertIn('href="/help/ug/related#details"', page)
        self.assertIn('href="/help/ug/related"', page)
        self.assertIn('href="https://example.com/"', page)
        self.assertIn('<th colspan="2">Heading</th>', page)
        self.assertIn("<td><p><em>Discovering JMP</em></p></td>", page)
        self.assertNotIn("cite", page)
        self.assertIn("<div><table>", page)
        self.assertNotIn("<p><table>", page)
        self.assertIn("<caption>Options</caption>", page)
        self.assertIn("Shared text from reuse.", page)
        self.assertIn('href="/help/ug/topic/nested-section"', page)
        self.assertNotIn("<h2>Nested Section</h2>", page)
        child = (self.output / "help" / "ug" / "topic" / "nested-section.html").read_text(encoding="utf-8")
        self.assertIn("<h1>Nested Section</h1>", child)
        self.assertIn("Nested body.", child)
        self.assertIn('href="/help/ug/topic">Example Topic</a>', child)
        self.assertIn("noindex, follow", page)
        self.assertNotIn("Internal review note", page)
        self.assertNotIn("hidden", page)
        self.assertNotIn("javascript:", page)
        self.assertIn("Unsafe link", page)

        self.assertFalse((self.output / "help" / "UG" / "Images").exists())
        self.assertIn('<img src="data:image/svg+xml;base64,', page)
        self.assertFalse((self.output / "outside.svg").exists())

        landing = (self.output / "help" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/help/catalog/ug"', landing)
        catalog = (self.output / "help" / "catalog" / "ug.html").read_text(encoding="utf-8")
        self.assertIn('href="/help/ug/topic"', catalog)

        self.assertIn(
            {"source": "/UG/topic.html", "destination": "/help/ug/topic", "title": "Example Topic"},
            self.read_csv("legacy-url-map.csv"),
        )

    def test_converts_only_the_requested_book(self):
        summary = IMPORTER.convert(self.source, self.output, book="ug")

        # Root map has UG/_ug.ditamap (topic.xml) and a loose UG/related.xml; only the book's topic is staged.
        # topic.xml plus its nested child page; related.xml is outside the book.
        self.assertEqual(summary["topicPages"], 2)
        self.assertTrue((self.output / "help" / "ug" / "topic.html").is_file())
        self.assertFalse((self.output / "help" / "ug" / "related.html").exists())

    def test_book_can_be_given_as_a_map_path(self):
        summary = IMPORTER.convert(self.source, self.output, book="UG/_ug.ditamap")
        self.assertEqual(summary["topicPages"], 2)

    def test_unknown_book_lists_available_books(self):
        with self.assertRaisesRegex(ValueError, "Available books: UG"):
            IMPORTER.convert(self.source, self.output, book="NOPE")

    def test_reports_unsafe_missing_and_unalt_references(self):
        IMPORTER.convert(self.source, self.output)

        issues = self.read_csv("unresolved-links.csv")
        kinds = {issue["kind"] for issue in issues}
        self.assertIn("unsafe-url", kinds)
        self.assertIn("unsafe-path", kinds)
        self.assertIn("missing-topic", kinds)

        accessibility = self.read_csv("accessibility.csv")
        self.assertEqual(len(accessibility), 1)
        self.assertEqual(accessibility[0]["kind"], "missing-alt")

    def test_refuses_to_write_into_a_nonempty_output_folder_without_force(self):
        self.output.mkdir()
        marker = self.output / "keep.txt"
        marker.write_text("do not overwrite", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "not empty"):
            IMPORTER.convert(self.source, self.output)

        self.assertEqual(marker.read_text(encoding="utf-8"), "do not overwrite")

    def test_requires_the_root_map(self):
        with self.assertRaisesRegex(ValueError, "Root map not found"):
            IMPORTER.convert(self.source, self.output, map_name="Missing.ditamap")


if __name__ == "__main__":
    unittest.main()
