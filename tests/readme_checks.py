"""README coverage/freshness regressions; no network or production changes."""
import copy
import importlib.util
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("readme_builder", ROOT / "scripts/Build-Readmes.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ReadmeTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "docs/readme-translations.json").read_text(encoding="utf-8"))

    def test_eight_complete_and_current_editions(self):
        rendered = builder.render_all(self.data)
        self.assertEqual(len(rendered), 8)
        for path, expected in rendered.items():
            self.assertEqual(path.read_text(encoding="utf-8-sig"), expected, str(path))
            self.assertEqual(len(re.findall(r"^<!-- readme:", expected, re.M)), len(self.data["blocks"]))
            self.assertEqual(len(re.findall(r"^## ", expected, re.M)), 9)

    def test_missing_translation_fails(self):
        del next(b for b in self.data["blocks"] if b["id"] == "b25")["text"]["ja"]
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            builder.render_all(self.data)

    def test_truncated_table_or_limit_fails(self):
        for target in ("b35", "b36"):
            data = copy.deepcopy(self.data)
            block = next(b for b in data["blocks"] if b["id"] == target)
            block["text"]["de"] = block["text"]["de"].rsplit("\n", 1)[0] if target == "b35" else block["text"]["de"].replace("64 MiB", "")
            with self.assertRaises(ValueError):
                builder.render_all(data)

    def test_examples_and_diagram_topology_match(self):
        outputs = list(builder.render_all(self.data).values())
        def fences(text):
            return re.findall(r"```(\w+)\n(.*?)\n```", text, re.S)
        original = fences(outputs[0])
        self.assertEqual(len(original), 4)
        for text in outputs:
            for (kind, source), (translated_kind, translated) in zip(original, fences(text)):
                self.assertEqual(kind, translated_kind)
                if kind == "mermaid":
                    source = re.sub(r"\[[^\]]+\]", "[]", source)
                    translated = re.sub(r"\[[^\]]+\]", "[]", translated)
                self.assertEqual(source, translated)

    def test_links_resolve_and_use_current_release(self):
        for path, content in builder.render_all(self.data).items():
            refs = dict(re.findall(r"^\[([\w-]+)\]: (.+)$", content, re.M))
            for reference in re.findall(r"\]\[([\w-]+)\]", content):
                self.assertIn(reference, refs)
            links = list(refs.values()) + re.findall(r"\]\(([^)]+)\)", content)
            for target in links:
                if target.startswith("https://"):
                    self.assertIn("/releases/", target)
                    self.assertIn("v1.6.1", target)
                else:
                    self.assertTrue((path.parent / target).is_file(), (path, target))


if __name__ == "__main__":
    unittest.main()
