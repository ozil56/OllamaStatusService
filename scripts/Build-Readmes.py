"""Render complete, parallel READMEs; fail on missing sections or examples."""
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = {"en": "English", "zh-CN": "简体中文", "zh-TW": "繁體中文", "ja": "日本語",
             "ko": "한국어", "de": "Deutsch", "fr": "Français", "es": "Español"}


def render_all(data=None):
    if data is None:
        data = json.loads((ROOT / "docs/readme-translations.json").read_text(encoding="utf-8"))
    if data["languages"] != list(LANGUAGES):
        raise ValueError("README language inventory differs")
    documents = {code: [] for code in LANGUAGES}
    seen = set()
    for block in data["blocks"]:
        key = block["id"]
        if key in seen:
            raise ValueError("Duplicate README block: " + key)
        seen.add(key)
        if "literal" in block:
            texts = dict.fromkeys(LANGUAGES, block["literal"])
        else:
            texts = block["text"]
            if set(texts) != set(LANGUAGES) or any(not value.strip() for value in texts.values()):
                raise ValueError("Incomplete README translations: " + key)
            # Technical examples, values and link destinations must be preserved.
            english = texts["en"]
            for code, value in texts.items():
                for pattern in (r"`[^`\n]+`", r"\]\[([\w-]+)\]", r"(?m)^\|", r"(?m)^- ", r"(?m)^\d+\. "):
                    expected, actual = sorted(re.findall(pattern, english)), sorted(re.findall(pattern, value))
                    if expected != actual:
                        raise ValueError(f"README structure/technical text differs: {key}/{code}: {pattern}")
                if len(english.split("\n\n")) != len(value.split("\n\n")):
                    raise ValueError(f"README paragraph omitted: {key}/{code}")
                if key == "b36" and any(number not in value for number in ("15", "3600", "64", "64 MiB")):
                    raise ValueError(f"Queue limit omitted: {key}/{code}")
        for code, value in texts.items():
            documents[code].append(f"<!-- readme:{key} -->\n\n{value}")
    result = {}
    for code, blocks in documents.items():
        prefix = "" if code == "en" else "../"
        nav = []
        for target, label in LANGUAGES.items():
            path = "README.md" if target == "en" else f"docs/README.{target}.md"
            if code != "en":
                path = "../README.md" if target == "en" else f"README.{target}.md"
            nav.append(f"[{label}]({path})")
        refs = []
        for name, target in data["links"].items():
            refs.append(f"[{name}]: " + (target if target.startswith("https://") else prefix + target))
        content = " | ".join(nav) + "\n\n" + "\n\n".join(blocks) + "\n\n" + "\n".join(refs) + "\n"
        path = ROOT / ("README.md" if code == "en" else f"docs/README.{code}.md")
        result[path] = content
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check generated files without writing")
    args = parser.parse_args()
    outputs = render_all()
    for path, content in outputs.items():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8-sig") != content:
                raise SystemExit("README is missing or stale: " + str(path))
        else:
            path.write_bytes(content.replace("\n", "\r\n").encode("utf-8"))
    print(f"{'Checked' if args.check else 'Generated'} {len(outputs)} complete READMEs; sections, examples, technical fields and links match.")


if __name__ == "__main__":
    main()
