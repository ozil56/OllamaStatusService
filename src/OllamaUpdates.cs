using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace OllamaStatusTray
{
    internal sealed class OllamaRelease
    {
        public string Current, Latest, Url;
        public long Size;
        public bool Available { get {
            var latest = OllamaUpdates.VersionOf(Latest); var current = OllamaUpdates.VersionOf(Current);
            return latest > current || (latest == current && Current.Contains("-"));
        } }
    }

    internal sealed class OllamaUpdateMenuState
    {
        string current, latest;
        internal void SetRelease(OllamaRelease release) { latest = release.Latest; ObserveVersion(release.Current); }
        internal void ObserveVersion(string version) { OllamaUpdates.VersionOf(version); current = version; }
        internal void Invalidate() { current = null; }
        internal string Text { get {
            if (String.IsNullOrEmpty(current)) return L.T("检查 Ollama 更新…");
            if (!String.IsNullOrEmpty(latest) && new OllamaRelease { Current = current, Latest = latest }.Available)
                return L.F("升级 Ollama 到 {0}", latest) + "…";
            return L.F("检查更新（当前 {0}）", current) + "…";
        } }
    }

    // Elevate a GUI executable, then create PowerShell with CREATE_NO_WINDOW.
    // Hiding a console after shell/UAC launch still allows a visible flash.
    internal static class TrayWorker
    {
        internal static string ScriptArguments(string mode, string value)
        {
            if (mode == "--manage-worker" && (value == "Start" || value == "Stop" || value == "Recover"))
                return " -Action " + value;
            if (mode == "--update-worker") {
                if (value == "recover") return " -Recover";
                if (System.Text.RegularExpressions.Regex.IsMatch(value ?? "", @"^\d+\.\d+\.\d+$")) return " -TargetVersion " + value;
            }
            throw new ArgumentException(L.T("无效的后台操作。"));
        }
        internal static Process Start(string mode, string value)
        {
            ScriptArguments(mode, value);
            return Process.Start(new ProcessStartInfo(Application.ExecutablePath, mode + " " + OllamaUpdates.Quote(value) + " --language " + L.Language) {
                UseShellExecute = true, Verb = "runas", WorkingDirectory = AppDomain.CurrentDomain.BaseDirectory
            });
        }
        internal static ProcessStartInfo PowerShellInfo(string script, string arguments)
        {
            return new ProcessStartInfo {
                FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe"),
                Arguments = "-NoProfile -NonInteractive -STA -ExecutionPolicy Bypass -File " + OllamaUpdates.Quote(script) + arguments,
                UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden,
                RedirectStandardOutput = true, RedirectStandardError = true
            };
        }
        internal static int Run(string mode, string value)
        {
            try {
                string arguments = ScriptArguments(mode, value);
                string script = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "scripts",
                    mode == "--manage-worker" ? "Manage.ps1" : "OllamaUpdate.ps1");
                var info = PowerShellInfo(script, arguments); info.EnvironmentVariables["OSS_UI_LANGUAGE"] = L.Language;
                using (var process = Process.Start(info)) {
                    var output = process.StandardOutput.ReadToEndAsync();
                    var error = process.StandardError.ReadToEndAsync();
                    process.WaitForExit(); Task.WaitAll(output, error);
                    if (process.ExitCode != 0 && !String.IsNullOrWhiteSpace(error.Result))
                        MessageBox.Show(error.Result, L.T("Ollama 状态服务"), MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return process.ExitCode;
                }
            } catch (Exception ex) { MessageBox.Show(ex.Message, L.T("Ollama 状态服务")); return 1; }
        }
    }

    internal static class OllamaUpdates
    {
        internal static event Action<OllamaRelease> ReleaseChecked;
        internal static event Action InstallationChanged;
        internal static void NotifyInstallationChanged() { if (InstallationChanged != null) InstallationChanged(); }

        internal static readonly string StateDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "OllamaStatusService");
        internal static readonly string Marker = Path.Combine(StateDir, @"updater\maintenance.lock");
        internal static readonly string StatusFile = Path.Combine(StateDir, @"updater\status.json");
        internal static readonly string NoticeFile = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "OllamaStatusService", "ollama-update-notice.txt");

        internal static Dictionary<string, object> Read(string path)
        {
            return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(path, Encoding.UTF8));
        }
        internal static Version VersionOf(string value)
        {
            var match = System.Text.RegularExpressions.Regex.Match(value ?? "", @"^v?(\d+\.\d+\.\d+)(?:[-+][0-9A-Za-z.-]+)?$");
            if (!match.Success) throw new InvalidOperationException(L.T("无法识别 Ollama 版本：") + value);
            return new Version(match.Groups[1].Value);
        }
        internal static string GatewayUrl()
        {
            var config = Read(Path.Combine(StateDir, "config.json"));
            string host = Convert.ToString(config["listen_host"]);
            if (host == "0.0.0.0" || host == "::" || host == "localhost") host = "127.0.0.1";
            return new UriBuilder("http", host, Convert.ToInt32(config["listen_port"])).Uri.ToString().TrimEnd('/');
        }
        internal static OllamaRelease ParseRelease(string json, string current)
        {
            var release = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json);
            string tag = Convert.ToString(release["tag_name"]);
            if (Convert.ToBoolean(release["draft"]) || Convert.ToBoolean(release["prerelease"]) ||
                !System.Text.RegularExpressions.Regex.IsMatch(tag, @"^v\d+\.\d+\.\d+$"))
                throw new InvalidOperationException(L.T("官方未返回可用的稳定发行版。"));
            VersionOf(current);
            var found = new List<Dictionary<string, object>>();
            foreach (Dictionary<string, object> asset in (IEnumerable)release["assets"])
                if (Convert.ToString(asset["name"]) == "OllamaSetup.exe") found.Add(asset);
            if (found.Count != 1) throw new InvalidOperationException(L.T("官方发行版缺少 Windows 安装包。"));
            var selected = found[0];
            string expected = "https://github.com/ollama/ollama/releases/download/" + tag + "/OllamaSetup.exe";
            if (Convert.ToString(selected["browser_download_url"]) != expected || !selected.ContainsKey("digest") ||
                !System.Text.RegularExpressions.Regex.IsMatch(Convert.ToString(selected["digest"]), @"^sha256:[a-fA-F0-9]{64}$"))
                throw new InvalidOperationException(L.T("官方安装包地址或校验信息无效。"));
            long size = Convert.ToInt64(selected["size"]);
            if (size <= 0) throw new InvalidOperationException(L.T("官方安装包大小无效。"));
            return new OllamaRelease { Current = current, Latest = tag.Substring(1), Url = "https://github.com/ollama/ollama/releases/tag/" + tag, Size = size };
        }
        internal static async Task<string> CurrentVersion()
        {
            using (var local = new HttpClient(new HttpClientHandler { UseProxy = false })) {
                local.Timeout = TimeSpan.FromSeconds(5);
                var version = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(await local.GetStringAsync(GatewayUrl() + "/api/version"));
                string current = Convert.ToString(version["version"]); VersionOf(current); return current;
            }
        }
        internal static async Task<OllamaRelease> Check()
        {
            ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
            string json;
            using (var client = new HttpClient()) {
                client.Timeout = TimeSpan.FromSeconds(30);
                client.DefaultRequestHeaders.UserAgent.ParseAdd("OllamaStatusService-Updater/1.6.1");
                json = await client.GetStringAsync("https://api.github.com/repos/ollama/ollama/releases/latest");
            }
            // Read the installed version AFTER the potentially slow Internet request.
            // An upgrade completing during that request must not resurrect an old offer.
            var release = ParseRelease(json, await CurrentVersion());
            if (ReleaseChecked != null) ReleaseChecked(release);
            return release;
        }
        internal static bool ShouldNotify(string version)
        {
            try { return !File.Exists(NoticeFile) || File.ReadAllText(NoticeFile).Trim() != version; }
            catch { return true; }
        }
        internal static void RememberNotice(string version)
        {
            try { Directory.CreateDirectory(Path.GetDirectoryName(NoticeFile)); File.WriteAllText(NoticeFile, version, Encoding.UTF8); }
            catch { /* An unavailable preference file must not break the tray. */ }
        }
        internal static string Quote(string value)
        {
            var result = new StringBuilder("\""); int slashes = 0;
            foreach (char c in value) {
                if (c == '\\') { slashes++; continue; }
                result.Append('\\', c == '"' ? slashes * 2 + 1 : slashes); result.Append(c); slashes = 0;
            }
            result.Append('\\', slashes * 2); return result.Append('"').ToString();
        }
    }

    internal sealed class OllamaUpdateForm : Form
    {
        readonly Label versions = new Label(), explanation = new Label();
        readonly TextBox details = new TextBox();
        readonly Button check = new Button(), upgrade = new Button(), recover = new Button(), close = new Button();
        readonly ProgressBar progress = new ProgressBar();
        readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
        OllamaRelease release;
        Process worker;
        bool checking, working;
        bool preview;
        DateTime launched;
        DateTime lastStatus = DateTime.MinValue;
        readonly Func<Task<OllamaRelease>> checkRelease;
        readonly string statusFile;
        readonly LocalizedHistory history;
        UiText versionText = UiText.Template("正在检查 Ollama 版本…");

        internal OllamaUpdateForm() : this(OllamaUpdates.Check, OllamaUpdates.StatusFile) { }
        internal OllamaUpdateForm(Func<Task<OllamaRelease>> checkRelease, string statusFile)
        {
            this.checkRelease = checkRelease; this.statusFile = statusFile;
            Text = L.T("检查更新／升级 Ollama");
            Font = new Font("Segoe UI", 10F);
            AutoScaleMode = AutoScaleMode.Dpi; ClientSize = new Size(900, 580); MinimumSize = new Size(916, 619);
            StartPosition = FormStartPosition.CenterScreen;
            var layout = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(22), RowCount = 6, ColumnCount = 1 };
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 55));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 125));
            layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 28));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 90));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 35));
            LanguageUI.Bind(versions, delegate { return versionText.Render(); }); versions.Font = new Font(Font.FontFamily, 13F, FontStyle.Bold); versions.Dock = DockStyle.Fill;
            explanation.Text = L.T("有新版时仅提示，由你决定升级。安装前会校验官方签名，并在没有运行、排队、下载／上传或未知任务时停服。升级保留原模型和配置，完成后自动恢复 OSS 接管。");
            explanation.Dock = DockStyle.Fill;
            details.Multiline = true; details.ReadOnly = true; details.ScrollBars = ScrollBars.Vertical; details.Dock = DockStyle.Fill; details.BackColor = SystemColors.Window;
            progress.Dock = DockStyle.Fill; progress.Style = ProgressBarStyle.Blocks;
            history = new LocalizedHistory(details, true);
            var buttons = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.LeftToRight, Padding = new Padding(0, 8, 0, 0) };
            check.Text = L.T("检查更新"); upgrade.Text = L.T("升级 Ollama"); recover.Text = L.T("恢复上次升级"); close.Text = L.T("关闭");
            foreach (var b in new[] { check, upgrade, recover, close }) { b.AutoSize = true; b.Height = 32; buttons.Controls.Add(b); }
            upgrade.Enabled = false; recover.Enabled = File.Exists(OllamaUpdates.Marker);
            check.Click += async delegate { await Check(); };
            upgrade.Click += delegate { Launch(false); };
            recover.Click += delegate { Launch(true); };
            close.Click += delegate { Close(); };
            foreach (Control control in new Control[] { versions, explanation, details, progress, buttons }) layout.Controls.Add(control);
            Controls.Add(layout);
            var picker = LanguageUI.Attach(this, 0, 0, 245);
            Controls.Remove(picker); picker.Anchor = AnchorStyles.Left; layout.Controls.Add(picker);
            timer.Interval = 700; timer.Tick += delegate { Poll(); }; timer.Start();
            Shown += async delegate {
                if (preview) return;
                Poll();
                if (File.Exists(OllamaUpdates.Marker)) SetVersion("检测到未完成的升级，可恢复 OSS 接管");
                else if (!working) await Check();
            };

        }
        void SetVersion(string source, params object[] arguments)
        {
            versionText = UiText.Template(source, arguments); versions.Text = versionText.Render();
        }
        void Append(string source, params object[] arguments) { history.Append(UiText.Template(source, arguments)); }
        internal void RenderPreview(string path, bool sample = true)
        {
            preview = true; timer.Stop(); StartPosition = FormStartPosition.Manual; Location = new Point(-10000, -10000);
            if (sample) ShowRelease(new OllamaRelease { Current = "0.40.8", Latest = "0.40.9", Size = 1661992960 });
            Show(); Application.DoEvents(); PerformLayout();
            using (var bitmap = new Bitmap(Width, Height)) { DrawToBitmap(bitmap, new Rectangle(0, 0, Width, Height)); bitmap.Save(path); }
            Hide();
        }
        void ShowRelease(OllamaRelease value)
        {
            release = value;
            SetVersion("当前 {0}    最新稳定版 {1}", release.Current, release.Latest);
            if (release.Available) Append("发现新版。官方安装包约 {0:N0} MB；点击升级后先下载，下载期间服务继续运行。", release.Size / 1048576.0);
            else Append("当前已是相同或更新版本。");
        }
        internal async Task Check()
        {
            if (checking || working) return;
            checking = true; check.Enabled = upgrade.Enabled = false;
            SetVersion("正在连接官方发行页面…");
            try {
                var value = await checkRelease();
                if (IsDisposed) return;
                ShowRelease(value);
            } catch (Exception ex) {
                if (IsDisposed) return;
                release = null; SetVersion("检查未完成");
                Append("无法检查更新：{0}。可稍后重试；未修改 Ollama 或服务。", ex.Message);
            } finally { if (!IsDisposed) { checking = false; EnableButtons(); } }
        }
        void EnableButtons()
        {
            check.Enabled = !checking && !working && !File.Exists(OllamaUpdates.Marker);
            upgrade.Enabled = check.Enabled && release != null && release.Available;
            recover.Enabled = !working && File.Exists(OllamaUpdates.Marker);
            progress.Style = working || checking ? ProgressBarStyle.Marquee : ProgressBarStyle.Blocks;
            close.Text = working ? L.T("关闭窗口（继续升级）") : L.T("关闭");
        }
        void Launch(bool recovery)
        {
            if (working) return;
            if (!recovery && (release == null || !release.Available)) return;
            try {
                launched = DateTime.UtcNow;
                worker = TrayWorker.Start("--update-worker", recovery ? "recover" : release.Latest);
                working = true; EnableButtons(); Append("已启动升级协调程序。此窗口关闭后流程仍会继续，可随时从托盘查看进度。");
            } catch (System.ComponentModel.Win32Exception ex) {
                if (ex.NativeErrorCode == 1223) Append("已取消 Windows 授权，未执行升级。");
                else Append("{0}", ex.Message);
            }
        }
        internal void Poll()
        {
            if (IsDisposed) return;
            try {
                if (File.Exists(statusFile)) {
                    var status = OllamaUpdates.Read(statusFile);
                    var updated = DateTime.Parse(Convert.ToString(status["updated"])).ToUniversalTime();
                    bool active = Convert.ToBoolean(status["active"]);
                    // A stale status from a crashed process is not an active worker.
                    bool alive = worker != null && !worker.HasExited;
                    if (worker == null) alive = IsCoordinatorRunning();
                    if (worker == null || updated >= launched.AddSeconds(-1)) {
                        working = active && alive;
                        if (updated > lastStatus) history.Append(UiText.Read(status));
                        if (!alive && active) Append("上次协调程序已退出。若仍有维护记录，请使用“恢复上次升级”。");
                        if (updated > lastStatus && !active && Convert.ToBoolean(status["success"])) { upgrade.Enabled = false; release = null; SetVersion("Ollama 更新／恢复已完成"); OllamaUpdates.NotifyInstallationChanged(); }
                        lastStatus = updated;
                    }
                    if (worker != null && worker.HasExited) {
                        working = false;
                        if (updated < launched.AddSeconds(-1)) Append("协调程序未能启动，可能已有其他 OSS 安装或升级正在运行。");
                        worker.Dispose(); worker = null;
                    }
                }
                else if (worker != null && worker.HasExited) {
                    working = false; Append("协调程序未能写入进度，请检查权限或其他 OSS 安装操作。");
                    worker.Dispose(); worker = null;
                }
            } catch (IOException) { } catch (UnauthorizedAccessException) { } catch (InvalidOperationException) { } catch (ArgumentException) { }
            EnableButtons();
        }
        protected override void Dispose(bool disposing)
        {
            if (disposing) { timer.Dispose(); if (worker != null) worker.Dispose(); }
            base.Dispose(disposing);
        }
        static bool IsCoordinatorRunning()
        {
            // A global mutex owned by the elevated coordinator survives tray exit.
            // Inspecting it never changes the gate or service state.
            try {
                using (var mutex = System.Threading.Mutex.OpenExisting(@"Global\OllamaStatusService.Setup")) {
                    bool acquired = false;
                    try { acquired = mutex.WaitOne(0); }
                    catch (System.Threading.AbandonedMutexException) { acquired = true; }
                    finally { if (acquired) mutex.ReleaseMutex(); }
                    return !acquired;
                }
            } catch (System.Threading.WaitHandleCannotBeOpenedException) { return false; }
            catch (UnauthorizedAccessException) { return true; }
        }
    }
}
