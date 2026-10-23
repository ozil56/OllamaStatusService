using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using System.Text.RegularExpressions;
using System.Windows.Forms;
using OllamaStatusTray;

internal static class LocalizationTests
{
    static int count;
    static void Assert(bool ok, string reason) { if (!ok) throw new Exception(reason); }
    static void Case(string name, Action action) { action(); count++; Console.WriteLine("PASS " + name); }
    static T Field<T>(object form, string name) { return (T)form.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic).GetValue(form); }
    static void Await(Task task) {
        DateTime deadline = DateTime.UtcNow.AddSeconds(5);
        while (!task.IsCompleted && DateTime.UtcNow < deadline) { Application.DoEvents(); Thread.Sleep(10); }
        Assert(task.IsCompleted, "Async check timed out"); task.GetAwaiter().GetResult();
    }
    [STAThread]
    static int Main(string[] args)
    {
        string output = args[0]; Directory.CreateDirectory(output);
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        Case("locale matching and Windows auto", delegate {
            Assert(L.Match("zh-Hant-HK") == "zh-TW" && L.Match("zh-SG") == "zh-CN", "Chinese variants");
            Assert(L.Match("de-AT") == "de" && L.Match("en_GB") == "en" && L.Match("xx") == "en", "Locale fallback");
            var previous = Thread.CurrentThread.CurrentUICulture;
            try { Thread.CurrentThread.CurrentUICulture = CultureInfo.GetCultureInfo("ja-JP"); L.Select("auto", false); Assert(L.Language == "ja", "Auto must follow Windows UI culture"); }
            finally { Thread.CurrentThread.CurrentUICulture = previous; }
        });
        Case("saved preference and explicit worker language", delegate {
            var pref = typeof(L).GetField("Preference", BindingFlags.Static | BindingFlags.NonPublic);
            string path = Path.Combine(output, "preference", "language.txt"); pref.SetValue(null, path);
            L.Select("de", true); Assert(File.ReadAllText(path).Trim() == "de", "Preference not saved");
            var remaining = L.Arguments(new[] {"--update-worker", "recover", "--language", "fr"});
            Assert(L.Language == "fr" && remaining.Length == 2 && remaining[1] == "recover", "Worker arguments");
            Assert(File.ReadAllText(path).Trim() == "de", "Worker override changed preference");
        });
        Case("localized formatted diagnostics preserve paths and numbers", delegate {
            L.Select("en", false);
            string path = @"D:\中文模型\qwen-test";
            string result = L.Display("模型目录（来源：" + path + "）");
            Assert(result == "Model directory (source: " + path + ")", result);
            result = L.Display("正在下载 Ollama 0.40.9：23.5%（123 / 456 MB），服务继续运行。");
            Assert(result.Contains("23.5%") && !result.Contains("正在"), result);
            Assert(L.Display("TLS error at " + path) == "TLS error at " + path, "Raw diagnostic changed");
            string en = L.F("升级 Ollama 到 {0}", "0.40.9"); L.Select("de", false);
            Assert(L.FromLanguage(en, "en") == L.F("升级 Ollama 到 {0}", "0.40.9"), "Template retranslation");
        });
        Case("legacy updater completion is fully translated", delegate {
            L.Select("en", false);
            string path = @"C:\ProgramData\OllamaStatusService\updater\backups\ollama-update-test";
            string message = "已恢复 OSS 接管，Ollama 版本 0.40.9。原模型和配置验证通过。备份与日志：" + path;
            string result = L.Display(message);
            Assert(!System.Text.RegularExpressions.Regex.IsMatch(result, @"[\u4e00-\u9fff]"), result);
            Assert(result.Contains("0.40.9") && result.Contains(path), "Version/path changed");
        });
        Case("real updater state and timestamped legacy history switch languages", delegate {
            string statusFile = Path.Combine(output, "legacy-status.json");
            string backup = @"C:\ProgramData\OllamaStatusService\updater\backups\ollama-update-test";
            string source = "已恢复 OSS 接管，Ollama 版本 0.40.9。原模型和配置验证通过。备份与日志：" + backup;
            SetupHost.Write(statusFile, new {phase="complete", message=source, active=false, success=true, updated=DateTime.UtcNow.ToString("o")});
            L.Select("zh-CN", false);
            using (var form = new OllamaUpdateForm(delegate { return Task.FromResult(new OllamaRelease { Current="0.40.9", Latest="0.40.9" }); }, statusFile)) {
                form.Poll(); Await(form.Check());
                string original = Field<TextBox>(form, "details").Text;
                string timestamp = original.Substring(0, 10);
                foreach (string language in new[] {"en", "de", "fr", "es", "ko", "ja", "zh-TW", "zh-CN", "en"}) {
                    L.Select(language, false);
                    string header = Field<Label>(form, "versions").Text;
                    string details = Field<TextBox>(form, "details").Text;
                    Assert(header == L.F("当前 {0}    最新稳定版 {1}", "0.40.9", "0.40.9"), "Mixed version header: " + header);
                    Assert(details.StartsWith(timestamp) && details.Contains(backup), "Timestamp/backup changed");
                    Assert(details.Contains(L.Display(source)) && details.Contains(L.T("当前已是相同或更新版本。")), "Stale history: " + details);
                    if (language == "en" || language == "de" || language == "fr" || language == "es" || language == "ko")
                        Assert(!Regex.IsMatch(header + details, @"[\u4e00-\u9fff]"), "Untranslated UI: " + details);
                    Assert(!Field<Button>(form, "upgrade").Enabled, "Upgrade enabled at latest version");
                    form.Poll(); Assert(Field<TextBox>(form, "details").Text == details, "Polling duplicated or changed old history");
                }
                form.RenderPreview(Path.Combine(output, "regression-updater-en.png"), false);
                L.Select("zh-CN", false); Assert(Field<TextBox>(form, "details").Text == original, "History did not round-trip");
            }
        });
        Case("new and mixed legacy worker messages keep user paths intact", delegate {
            string backup = @"D:\中文模型\关闭\{0}\backup";
            string source = "已恢复 OSS 接管，Ollama 版本 0.40.9。原模型和配置验证通过。备份与日志：" + backup;
            L.Select("en", false);
            var old = UiText.Read(new Dictionary<string, object> { {"language", "en"}, {"message", "OSS takeover restored, Ollama version 0.40.9。原模型和配置验证通过。备份与日志：" + backup} });
            var current = UiText.Read(new Dictionary<string, object> { {"language", "en"}, {"source_message", source}, {"message", "STALE RENDERED MESSAGE"} });
            foreach (string language in new[] {"en", "de", "ja", "zh-CN"}) {
                L.Select(language, false);
                Assert(current.Render() == L.Display(source) && old.Render() == current.Render(), "Persisted message not rebuilt");
                Assert(current.Render().Contains(backup), "User path translated");
            }
        });
        Case("language change while asynchronous update check completes", delegate {
            var pending = new TaskCompletionSource<OllamaRelease>();
            L.Select("zh-CN", false);
            using (var form = new OllamaUpdateForm(delegate { return pending.Task; }, Path.Combine(output, "absent.json"))) {
                Task task = form.Check(); L.Select("en", false);
                Assert(Field<Label>(form, "versions").Text == L.T("正在连接官方发行页面…"), "Checking label stale");
                pending.SetResult(new OllamaRelease {Current="0.40.8", Latest="0.40.9", Size=1661992960}); Await(task);
                Assert(Field<Button>(form, "upgrade").Enabled, "Available update missing");
                Assert(!Regex.IsMatch(Field<TextBox>(form, "details").Text, @"[\u4e00-\u9fff]"), "New-version message mixed");
                L.Select("de", false);
                Assert(Field<TextBox>(form, "details").Text.Contains(L.F("发现新版。官方安装包约 {0:N0} MB；点击升级后先下载，下载期间服务继续运行。", 1585.0)), "Download size message stale");
            }
        });
        Case("failed update check translates its wrapper and preserves diagnostics", delegate {
            string raw = @"TLS error: D:\中文模型\{0}\latest";
            L.Select("zh-CN", false);
            using (var form = new OllamaUpdateForm(delegate { throw new IOException(raw); }, Path.Combine(output, "absent.json"))) {
                Await(form.Check()); L.Select("en", false);
                Assert(Field<Label>(form, "versions").Text == L.T("检查未完成"), "Failure label stale");
                Assert(Field<TextBox>(form, "details").Text.Contains(L.F("无法检查更新：{0}。可稍后重试；未修改 Ollama 或服务。", raw)), "Failure wrapper stale");
                Assert(Field<TextBox>(form, "details").Text.Contains(raw), "Raw diagnostic changed");
            }
        });
        Case("installer completion history translates after completion", delegate {
            L.Select("zh-CN", false);
            using (var form = new SetupForm(false, true, false, null)) {
                var message = UiText.Read(new Dictionary<string, object> { {"source_message", "校验通过，共 716 个文件。未修改系统或现有服务。"}, {"language", "en"}, {"message", "STALE RENDERED MESSAGE"} });
                form.Finish(true, message);
                foreach (string language in new[] {"en", "de", "fr", "es", "ko", "ja", "zh-TW", "zh-CN", "en"}) {
                    L.Select(language, false);
                    Assert(Field<TextBox>(form, "details").Text == L.F("校验通过，共 {0} 个文件。未修改系统或现有服务。", 716), "Setup history stale");
                    Assert(Field<Label>(form, "state").Text == L.T("安装包校验通过"), "Setup state stale");
                }
                form.RenderPreview(Path.Combine(output, "regression-setup-en.png"));
            }
        });
        foreach (string code in L.Codes) {
            if (code == "auto") continue;
            Case("native UI and current-version menu " + code, delegate {
                L.Select(code, false);
                var menu = new OllamaUpdateMenuState();
                menu.SetRelease(new OllamaRelease {Current="0.40.9", Latest="0.40.9"});
                Assert(menu.Text == L.F("检查更新（当前 {0}）", "0.40.9") + "…", "Stale upgrade text " + code);
                using (var form = new SetupForm(false, false, false, null)) form.RenderPreview(Path.Combine(output, "setup-"+code+".png"));
                using (var form = new SetupForm(true, false, false, null)) form.RenderPreview(Path.Combine(output, "uninstall-"+code+".png"));
                using (var form = new OllamaUpdateForm()) form.RenderPreview(Path.Combine(output, "update-"+code+".png"));
            });
        }
        Case("live selection translates existing controls", delegate {
            L.Select("zh-CN", false);
            using (var form = new SetupForm(false, false, false, null)) {
                L.Select("en", false); Assert(!form.Text.Contains("状态"), "Window title did not update");
                L.Select("de", false); Assert(form.Controls["language-picker"] is ComboBox, "Picker missing");
                form.RenderPreview(Path.Combine(output, "switched-de.png"));
            }
        });
        Console.WriteLine(count + " native localization checks passed; previews: " + output);
        return 0;
    }
}
