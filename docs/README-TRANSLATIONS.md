# Maintaining complete README translations

The root English README and all seven translations are full editions with equal information coverage. Translated editions must not become abridged summaries or redirect readers to English for omitted operating details. Character and word counts naturally differ by language.

Edit `docs/readme-translations.json`, then run:

```powershell
python scripts\Build-Readmes.py
python scripts\Build-Readmes.py --check
python tests\readme_checks.py
```

Each content block has a stable ID. Provide every supported language when adding or changing a translated block. Preserve the meaning of requirements, limitations, maintenance instructions and validation claims. Review changed translations together with the English source. Commands are shared verbatim; architecture diagram labels are translated with the same graph topology.

`scripts/Build-Readmes.py` generates `README.md` and the seven `docs/README.<language>.md` files. Do not edit generated editions independently. Shared links are resolved relative to each document. The generator checks language coverage, paragraphs, tables, list items, inline technical fields, queue limits and reference links. The tests additionally verify rendered files, graph structure, shared examples and local link targets. These structural checks help detect omissions; they do not replace review of translation meaning.

`scripts/Build-Package.ps1` runs the freshness check before copying source into a release package. A missing or stale edition fails the build. Update release and download URLs in the shared link map when preparing a new version. GitHub release assets and their checksum must be published before treating the links as verified.

## 维护约定

英文、简体中文、繁体中文、日文、韩文、德文、法文和西班牙文均为完整版本。更新时逐块同步，不得把其他语言改成摘要，也不能用“详见英文版”代替缺少的操作说明。不同语言字数不必相同，但功能、参数、示例、限制及验证范围必须一致。自动检查用于发现结构性遗漏，翻译含义仍需人工复核。
