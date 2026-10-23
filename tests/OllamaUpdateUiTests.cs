using System;
using System.IO;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using OllamaStatusTray;

internal static class OllamaUpdateUiTests
{
    static int count;
    static void Assert(bool condition, string message) { if (!condition) throw new Exception(message); count++; }
    static string Release(string tag, bool prerelease, string url, string digest, long size)
    {
        return new JavaScriptSerializer().Serialize(new { tag_name = tag, draft = false, prerelease = prerelease,
            assets = new[] { new { name = "OllamaSetup.exe", browser_download_url = url, digest = digest, size = size } } });
    }
    static void Rejected(string json)
    {
        bool failed = false;
        try { OllamaUpdates.ParseRelease(json, "0.9.9"); } catch (InvalidOperationException) { failed = true; }
        Assert(failed, "Invalid release was accepted");
    }
    [STAThread]
    static int Main(string[] args)
    {
        L.Select("zh-CN", false);
        try {
            string url = "https://github.com/ollama/ollama/releases/download/v0.10.0/OllamaSetup.exe";
            string digest = "sha256:" + new string('a', 64);
            var available = OllamaUpdates.ParseRelease(Release("v0.10.0", false, url, digest, 200), "0.9.9");
            Assert(available.Available, "Numeric version comparison failed");
            Assert(!OllamaUpdates.ParseRelease(Release("v0.10.0", false, url, digest, 200), "0.10.0").Available, "Equal version offered");
            Assert(!OllamaUpdates.ParseRelease(Release("v0.10.0", false, url, digest, 200), "0.11.0").Available, "Downgrade offered");
            Assert(OllamaUpdates.ParseRelease(Release("v0.10.0", false, url, digest, 200), "0.10.0-rc1").Available, "Stable replacement for release candidate missing");
            Rejected(Release("v0.10.0", true, url, digest, 200));
            Rejected(Release("v0.10.0-rc1", false, url, digest, 200));
            Rejected(Release("v0.10.0", false, "http://example.com/setup.exe", digest, 200));
            Rejected(Release("v0.10.0", false, url, null, 200));
            Rejected(Release("v0.10.0", false, url, digest, 0));
            Assert(OllamaUpdates.Quote(@"C:\路径 空格\") == "\"C:\\路径 空格\\\\\"", "Native quoting failed");
            var menu = new OllamaUpdateMenuState();
            menu.SetRelease(new OllamaRelease { Current = "0.48.0", Latest = "0.49.0" });
            Assert(menu.Text.Contains("升级 Ollama 到 0.49.0"), "Missing available upgrade action");
            menu.ObserveVersion("0.49.0");
            Assert(!menu.Text.Contains("升级") && menu.Text.Contains("0.49.0"), "Installed latest version still offered");
            menu.ObserveVersion("0.50.0");
            Assert(!menu.Text.Contains("升级"), "Newer local version still offered a downgrade");
            menu.SetRelease(new OllamaRelease { Current = "0.48.0", Latest = "0.49.0" });
            menu.Invalidate();
            Assert(!menu.Text.Contains("0.49.0") && !menu.Text.Contains("升级"), "Completion/offline state retained stale offer");
            menu.SetRelease(new OllamaRelease { Current = "0.49.0", Latest = "0.49.0" });
            Assert(!menu.Text.Contains("升级"), "Manual latest check failed to clear stale offer");
            menu.SetRelease(new OllamaRelease { Current = "0.49.0-rc1", Latest = "0.49.0" });
            Assert(menu.Text.Contains("升级"), "Stable replacement for prerelease not offered");
            Assert(TrayWorker.ScriptArguments("--manage-worker", "Recover") == " -Action Recover", "Recovery action changed");
            Assert(TrayWorker.ScriptArguments("--update-worker", "recover") == " -Recover", "Update recovery action changed");
            bool rejected = false;
            try { TrayWorker.ScriptArguments("--manage-worker", "Recover;bad"); } catch (ArgumentException) { rejected = true; }
            Assert(rejected, "Unrecognized worker command accepted");
            string fixture = Path.Combine(Path.GetTempPath(), "oss-console-" + Guid.NewGuid().ToString("N") + ".ps1");
            try {
                File.WriteAllText(fixture, "Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class ConsoleProbe { [DllImport(\"kernel32.dll\")] public static extern IntPtr GetConsoleWindow(); }'\r\n[ConsoleProbe]::GetConsoleWindow().ToInt64()\r\n", System.Text.Encoding.UTF8);
                var info = TrayWorker.PowerShellInfo(fixture, "");
                Assert(!info.UseShellExecute && info.CreateNoWindow, "Worker creates a console");
                using (var process = System.Diagnostics.Process.Start(info)) {
                    var output = process.StandardOutput.ReadToEndAsync(); var error = process.StandardError.ReadToEndAsync();
                    process.WaitForExit(); Task.WaitAll(output, error);
                    Assert(process.ExitCode == 0 && output.Result.Trim() == "0", "Console was attached: " + error.Result);
                }
            } finally { File.Delete(fixture); }
            if (args.Length == 1 && args[0] == "--official-check") {
                var result = OllamaUpdates.Check().GetAwaiter().GetResult();
                Console.WriteLine("Official read-only check: current=" + result.Current + ", latest=" + result.Latest + ", available=" + result.Available);
            }
            Console.WriteLine("PASS " + count + " Ollama update UI/release checks");
            return 0;
        } catch (Exception ex) { Console.Error.WriteLine(ex); return 1; }
    }
}
