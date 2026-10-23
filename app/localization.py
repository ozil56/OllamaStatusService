"""Request-scoped display localization; protocol keys and user data stay intact."""
from functools import lru_cache
import html
import json
from pathlib import Path
import re

LANGUAGES = {"zh-CN": "简体中文", "zh-TW": "繁體中文", "en": "English", "ja": "日本語",
             "ko": "한국어", "de": "Deutsch", "fr": "Français", "es": "Español"}
CATALOG = json.loads((Path(__file__).resolve().parents[1] / "locales" / "messages.json").read_text(encoding="utf-8-sig"))
PATTERN = re.compile("|".join(re.escape(k) for k in sorted(CATALOG, key=len, reverse=True)))


def match_language(value):
    value = (value or "").lower().replace("_", "-")
    if value.startswith("zh"):
        return "zh-TW" if any(x in value for x in ("hant", "-tw", "-hk", "-mo")) else "zh-CN"
    return next((key for key in LANGUAGES if value == key.lower() or value.startswith(key.lower() + "-")), None)


def request_language(request):
    selected = request.query_params.get("lang", request.cookies.get("oss.language", "auto"))
    if selected != "auto":
        chosen = match_language(selected)
        if chosen:
            return chosen
    choices = []
    for order, entry in enumerate(request.headers.get("accept-language", "").split(",")):
        parts = entry.strip().split(";")
        try:
            quality = float(parts[1].strip().removeprefix("q=")) if len(parts) > 1 else 1
        except ValueError:
            continue
        if 0 < quality <= 1 and (language := match_language(parts[0])):
            choices.append((-quality, order, language))
    return min(choices)[2] if choices else "en"


def text(source, language):
    if language == "zh-CN":
        return source
    row = CATALOG.get(source)
    return row.get(language, row.get("en", source)) if row else source


def display(source, language):
    """For OSS-owned diagnostic prose only, never names, URLs or model payloads."""
    if not isinstance(source, str) or language == "zh-CN":
        return source
    if source in CATALOG:
        return text(source, language)
    for key in sorted(CATALOG, key=len, reverse=True):
        if "{0}" in key:
            pattern = re.escape(key)
            seen = set()
            for hole in re.finditer(r"\{(\d+)(?::[^{}]+)?\}", key):
                name = "p" + hole[1]
                capture = "(?P=" + name + ")" if name in seen else "(?P<" + name + ">[\\s\\S]*?)"
                pattern = pattern.replace(re.escape(hole[0]), capture, 1)
                seen.add(name)
            found = re.fullmatch(pattern, source)
            if found:
                return re.sub(r"\{(\d+)(?::[^{}]+)?\}", lambda m: found.group("p" + m[1]), text(key, language))
        elif len(key) >= 2:
            if source.startswith(key):
                return text(key, language) + display(source[len(key):], language)
            if source.endswith(key):
                return display(source[:-len(key)], language) + text(key, language)
    return source


def render_asset(path, request):
    return rendered_source(path, request_language(request))


@lru_cache(maxsize=64)
def rendered_source(path, language):
    source = path.read_text(encoding="utf-8-sig")
    # Only trusted, shipped source literals are localized here. Runtime user
    # strings are not passed through this renderer. Catalog text cannot add JS.
    def replacement(match):
        value = text(match[0], language)
        if path.suffix == ".js":
            return value.translate(str.maketrans({"\\": "\\\\", "'": "\\u0027", '"': "\\u0022",
                "`": "\\u0060", "<": "\\u003c", "\r": "\\r", "\n": "\\n", "$": "\\u0024"}))
        return html.escape(value, quote=True)
    if language != "zh-CN":
        source = PATTERN.sub(replacement, source)
    if path.suffix == ".html":
        source = source.replace('lang="zh-CN"', 'lang="' + language + '"')
        for asset in ("/manage/app.js", "/oss/ui/dashboard.js"):
            source = source.replace('src="' + asset + '"', 'src="' + asset + '?lang=' + language + '"')
    return source


def metadata(value, language):
    if isinstance(value, dict):
        return {key: metadata(child, language) for key, child in value.items()}
    if isinstance(value, list):
        return [metadata(child, language) for child in value]
    return text(value, language) if isinstance(value, str) else value


def headers(request):
    return {"Content-Language": request_language(request), "Vary": "Accept-Language, Cookie", "Cache-Control": "no-store"}
