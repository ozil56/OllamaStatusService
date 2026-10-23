"""Offline localization coverage and real HTTP compatibility checks."""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from localization import CATALOG, LANGUAGES, display, match_language, request_language
from starlette.requests import Request
sys.path.insert(0, str(ROOT / "tests"))
import ollama_update_guard as guard


class CatalogTests(unittest.TestCase):
    def test_all_supported_languages_and_placeholders_are_complete(self):
        self.assertGreater(len(CATALOG), 500)
        expected = set(LANGUAGES) - {"zh-CN"}
        for source, row in CATALOG.items():
            self.assertEqual(set(row), expected, source)
            placeholders = sorted(re.findall(r"\{\d+(?::[^{}]+)?\}", source))
            for language, value in row.items():
                self.assertIsInstance(value, str)
                self.assertEqual(sorted(re.findall(r"\{\d+(?::[^{}]+)?\}", value)), placeholders, (source, language, value))
                self.assertNotRegex(value, r"OSSPARAM|OSSNEWLINE", (source, language))
                for term in ("Ollama", "OSS", "HTTPS", "LocalService", "Nginx", "Install.exe", "Uninstall.exe"):
                    if term in source:
                        self.assertIn(term.lower(), value.lower(), (source, language, term))

    def test_dynamic_diagnostics_keep_parameters_and_raw_errors(self):
        result = display("正在下载 Ollama 0.40.9：23.5%（123 / 456 MB），服务继续运行。", "en")
        self.assertIn("23.5%", result)
        self.assertNotRegex(result, r"[\u4e00-\u9fff]")
        raw = r"TLS failed for D:\中文模型\qwen-test"
        self.assertEqual(display(raw, "de"), raw)

    def test_updater_completion_templates_preserve_versions_and_paths(self):
        path = r"D:\中文模型\关闭\{0}\backup"
        source = "已恢复 OSS 接管，Ollama 版本 0.40.9。原模型和配置验证通过。备份与日志：" + path
        for language in LANGUAGES:
            result = display(source, language)
            self.assertIn(path, result)
            self.assertIn("0.40.9", result)
            if language in ("en", "de", "fr", "es", "ko"):
                self.assertNotRegex(result.replace(path, ""), r"[\u4e00-\u9fff]")
        raw = r"TLS failed for D:\中文模型\qwen-test"
        result = display(raw + "\r\n已恢复 OSS 服务，当前 Ollama：0.40.9。", "en")
        self.assertIn(raw, result)
        self.assertIn("OSS services restored. Current Ollama: 0.40.9.", result)

    def test_html_keys_never_contain_markup(self):
        for source in CATALOG:
            self.assertNotRegex(source, r"<[^>]+>|aria-label=|^>")

    def test_locale_matching_and_quality_weights(self):
        for supplied, wanted in (("zh-Hant-HK", "zh-TW"), ("zh-SG", "zh-CN"), ("en_GB", "en"), ("ja-JP", "ja"), ("de-DE", "de")):
            self.assertEqual(match_language(supplied), wanted)
        request = Request({"type": "http", "query_string": b"", "headers": [(b"accept-language", b"xx;q=1,de;q=0.4,fr-CA;q=0.9,en;q=0")]})
        self.assertEqual(request_language(request), "fr")
        request = Request({"type": "http", "query_string": b"lang=es", "headers": [(b"cookie", b"oss.language=de"), (b"accept-language", b"ja")]})
        self.assertEqual(request_language(request), "es")
        request = Request({"type": "http", "query_string": b"lang=auto", "headers": [(b"cookie", b"oss.language=de"), (b"accept-language", b"ja")]})
        self.assertEqual(request_language(request), "ja")


class HttpTests(unittest.TestCase):
    setUpClass = classmethod(guard.UpdateGuardTests.setUpClass.__func__)
    start = classmethod(guard.UpdateGuardTests.start.__func__)
    wait = classmethod(guard.UpdateGuardTests.wait.__func__)
    status = classmethod(guard.UpdateGuardTests.status.__func__)

    def test_status_languages_keep_machine_fields_stable(self):
        original = self.status()
        for language in LANGUAGES:
            response = self.client.get(self.direct + "/status", params={"lang": language})
            body = response.json()
            self.assertEqual(response.headers["content-language"], language)
            self.assertEqual(body["language"], language)
            self.assertEqual(body["state"], original["state"])
            self.assertEqual(body["ollama_update"]["protocol"], 1)
            self.assertEqual(body["state_text"], "空闲" if language == "zh-CN" else CATALOG["空闲"][language])

    def test_help_localizes_prose_not_protocol(self):
        english = self.client.get(self.direct + "/help?lang=en").json()
        german = self.client.get(self.direct + "/help?lang=de").json()
        self.assertNotEqual(english["description"], german["description"])
        for field in ("urls", "manage_policy", "examples"):
            if field != "examples":
                self.assertEqual(english[field], german[field])
        self.assertEqual(english["queue_policy"]["ordering"], "local_first_fifo_per_origin")

    def test_rendered_assets_parse_in_every_language(self):
        with tempfile.TemporaryDirectory(prefix="oss-i18n-js-") as directory:
            for language in LANGUAGES:
                for endpoint in ("/manage", "/dashboard"):
                    response = self.client.get(self.direct + endpoint, params={"lang": language})
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('lang="' + language + '"', response.text)
                    self.assertIn("data-language-picker", response.text)
                    self.assertIn("Accept-Language", response.headers["vary"])
                for i, endpoint in enumerate(("/manage/app.js", "/oss/ui/dashboard.js")):
                    response = self.client.get(self.direct + endpoint, params={"lang": language})
                    self.assertEqual(response.status_code, 200)
                    path = Path(directory) / f"{language}-{i}.js"
                    path.write_text(response.text, encoding="utf-8")
                    result = subprocess.run(["node.exe", "--check", str(path)], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if language == "en":
                        self.assertNotRegex(response.text, r"[\u4e00-\u9fff]")

    def test_preference_is_per_browser_and_not_global_server_state(self):
        for language in ("de", "ja", "en"):
            response = self.client.get(self.direct + "/status", headers={"Cookie": "oss.language=" + language, "Accept-Language": "zh-CN"})
            self.assertEqual(response.json()["language"], language)
        self.assertEqual(self.client.get(self.direct + "/status", headers={"Accept-Language": "ko-KR"}).json()["language"], "ko")
        self.assertEqual(self.client.get(self.direct + "/status?lang=../../secret", headers={"Accept-Language": "unknown"}).json()["language"], "en")

    def test_manage_error_has_localized_display_without_changing_original(self):
        response = self.client.post(self.direct + "/manage/api/refresh?lang=en", json={})
        self.assertEqual(response.status_code, 403)
        data = response.json()
        self.assertIn("页面", data["error"])
        self.assertNotRegex(data["display_error"], r"[\u4e00-\u9fff]")

    def test_language_changes_leave_active_download_running(self):
        model = "held-localization-download"
        transfer = self.pool.submit(self.client.post, self.direct + "/api/pull", json={"model": model, "stream": False})
        self.wait(lambda: self.status()["inflight_model_transfers"] == 1)
        try:
            for language in LANGUAGES:
                response = self.client.get(self.direct + "/manage", headers={"Cookie": "oss.language=" + language})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.status()["inflight_model_transfers"], 1)
                self.assertFalse(transfer.done())
        finally:
            self.client.post(self.backend + "/__release/" + model)
        self.assertEqual(transfer.result(10).status_code, 200)


if __name__ == "__main__":
    unittest.main()
