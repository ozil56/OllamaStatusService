// .NET Framework 4.8 / Windows Forms. No Python or extra runtime is needed for the tray.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyFileVersion("1.6.1.0")]
[assembly: System.Reflection.AssemblyInformationalVersion("1.6.1")]

namespace OllamaStatusTray
{
    static class Program
    {
        [STAThread]
        static int Main(string[] args)
        {
            args = L.Arguments(args);
            if (args.Length == 2 && (args[0] == "--manage-worker" || args[0] == "--update-worker"))
                return TrayWorker.Run(args[0], args[1]);
            if (args.Length == 2 && args[0] == "--render-update-preview")
            {
                Application.EnableVisualStyles();
                using (var form = new OllamaUpdateForm()) form.RenderPreview(args[1]);
                return 0;
            }
            if (args.Length == 1 && args[0] == "--updates")
            {
                Application.EnableVisualStyles();
                Application.Run(new OllamaUpdateForm());
                return 0;
            }
            if (args.Length == 2 && args[0] == "--render-icons")
            {
                Directory.CreateDirectory(args[1]);
                foreach (string state in new [] {"idle","busy","offline","unknown"})
                    using (Bitmap bmp = TrayContext.DrawStatus(state, 64)) bmp.Save(Path.Combine(args[1], state + ".png"));
                return 0;
            }
            bool created;
            using (Mutex mutex = new Mutex(true, @"Local\OllamaStatusServiceTray", out created))
            {
                if (!created) return 0;
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new TrayContext());
                mutex.ReleaseMutex();
            }
            return 0;
        }
    }

    sealed class TrayContext : ApplicationContext
    {
        [DllImport("user32.dll", SetLastError = true)] static extern bool DestroyIcon(IntPtr handle);
        readonly NotifyIcon tray = new NotifyIcon();
        readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
        readonly HttpClient http = new HttpClient(new HttpClientHandler { UseProxy = false });
        readonly Dictionary<string, Icon> icons = new Dictionary<string, Icon>();
        readonly ToolStripMenuItem headline = new ToolStripMenuItem(L.T("正在连接…"));
        readonly string root = AppDomain.CurrentDomain.BaseDirectory;
        readonly string stateDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "OllamaStatusService");
        bool refreshing;
        bool closing;
        string lastState = "";
        readonly ToolStripMenuItem updateMenu = new ToolStripMenuItem(L.T("检查 Ollama 更新…"));
        OllamaUpdateForm updateForm;
        DateTime nextUpdateCheck = DateTime.UtcNow.AddSeconds(15);
        bool checkingUpdate;
        bool updateBalloon;
        readonly OllamaUpdateMenuState updateState = new OllamaUpdateMenuState();
        DateTime nextLocalVersionCheck = DateTime.MinValue;
        DateTime lastUpdateCompletion = DateTime.MinValue;
        bool readingVersion;
        int versionGeneration;
        Action languageChanged;

        public TrayContext()
        {
            http.Timeout = TimeSpan.FromSeconds(3);
            foreach (string state in new [] {"idle", "busy", "offline", "unknown"})
            {
                using (Bitmap bitmap = DrawStatus(state, 32))
                {
                    IntPtr handle = bitmap.GetHicon();
                    using (Icon borrowed = Icon.FromHandle(handle)) icons[state] = (Icon)borrowed.Clone();
                    DestroyIcon(handle);
                }
            }
            OllamaUpdates.ReleaseChecked += ApplyRelease;
            OllamaUpdates.InstallationChanged += InstallationChanged;
            ContextMenuStrip menu = new ContextMenuStrip();
            menu.Opening += async delegate { ObserveUpdateCompletion(); await RefreshInstalledVersion(true); };
            headline.Enabled = false; menu.Items.Add(headline); menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(L.T("打开状态面板"), null, delegate { Open("http://127.0.0.1:11434/dashboard"); });
            menu.Items.Add(L.T("打开日志目录"), null, delegate { Open(Path.Combine(stateDir,"logs")); });
            menu.Items.Add(L.T("查看部署说明"), null, delegate { Open(Path.Combine(root,"locales","guide." + L.Language + ".html")); });
            updateMenu.Click += delegate { OpenUpdates(); }; menu.Items.Add(updateMenu);
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(L.T("启动后台服务"), null, delegate { Manage("Start"); });
            menu.Items.Add(L.T("停止后台服务…"), null, delegate { Manage("Stop"); });
            menu.Items.Add(L.T("恢复服务（中止当前任务）…"), null, delegate { Manage("Recover"); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(L.T("退出图标（后台服务继续运行）"), null, delegate { ExitThread(); });
            var languages = new ToolStripMenuItem("🌐 Language");
            for (int i=0; i<L.Codes.Length; i++) {
                string code = L.Codes[i];
                var item = new ToolStripMenuItem(i == 0 ? L.T("自动（跟随系统）") : L.Names[i]) { Checked = code == L.Choice, Tag = code };
                item.Click += delegate { L.Select(code, true); }; languages.DropDownItems.Add(item);
            }
            menu.Items.Insert(menu.Items.Count-1, languages);
            string previousLanguage = L.Language;
            languageChanged = delegate {
                foreach (ToolStripItem item in menu.Items) if (item != languages) item.Text = L.FromLanguage(item.Text, previousLanguage);
                foreach (ToolStripMenuItem item in languages.DropDownItems) item.Checked = (string)item.Tag == L.Choice;
                languages.DropDownItems[0].Text = L.T("自动（跟随系统）");
                previousLanguage = L.Language; updateMenu.Text = updateState.Text;
                nextLocalVersionCheck = DateTime.MinValue;
            };
            L.Changed += languageChanged;
            tray.ContextMenuStrip = menu;
            tray.Icon = icons["offline"];
            tray.Text = L.T("Ollama：正在连接状态服务");
            tray.DoubleClick += delegate { Open("http://127.0.0.1:11434/dashboard"); };
            tray.BalloonTipClicked += delegate { if (updateBalloon) OpenUpdates(); };
            tray.Visible = true;
            timer.Interval = 3000;
            timer.Tick += async delegate { await RefreshState(); ObserveUpdateCompletion(); await RefreshInstalledVersion(false); await CheckUpdates(); };
            timer.Start();
        }

        static Color ColorOf(string state)
        {
            if (state == "idle") return Color.FromArgb(23,155,100);
            if (state == "busy") return Color.FromArgb(236,148,24);
            if (state == "unknown") return Color.FromArgb(145,85,196);
            return Color.FromArgb(213,62,82);
        }

        public static Bitmap DrawStatus(string state, int size)
        {
            Bitmap image = new Bitmap(size, size);
            using (Graphics g = Graphics.FromImage(image))
            {
                g.SmoothingMode = SmoothingMode.AntiAlias;
                float scale = size / 32f; g.ScaleTransform(scale, scale);
                using (Brush dark = new SolidBrush(Color.FromArgb(27,39,60))) g.FillEllipse(dark,1,1,30,30);
                using (Brush fill = new SolidBrush(ColorOf(state))) g.FillEllipse(fill,4,4,24,24);
                using (Pen pen = new Pen(Color.White,2.8f))
                {
                    pen.StartCap = LineCap.Round; pen.EndCap = LineCap.Round;
                    if (state == "idle") g.DrawLines(pen, new [] {new PointF(9,16),new PointF(14,21),new PointF(23,11)});
                    else if (state == "busy") { g.DrawEllipse(pen,9,9,14,14); g.DrawLine(pen,16,12,16,16); g.DrawLine(pen,16,16,20,18); }
                    else if (state == "unknown") { g.DrawArc(pen,11,8,10,9,190,280); g.DrawLine(pen,16,16,16,18); g.DrawEllipse(pen,15.5f,23,1,1); }
                    else { g.DrawLine(pen,11,11,21,21); g.DrawLine(pen,21,11,11,21); }
                }
            }
            return image;
        }

        async Task RefreshState()
        {
            if (refreshing || closing) return;
            refreshing = true;
            string state = "offline";
            string text = L.T("状态服务离线");
            try
            {
                string json = await http.GetStringAsync("http://127.0.0.1:11434/status");
                var data = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json);
                state = Convert.ToString(data["state"]);
                bool backend = Convert.ToBoolean(data["ollama_online"]);
                if (state == "idle") text = L.T("空闲，可接收任务");
                else if (state == "busy") text = data.ContainsKey("queued_local_requests")
                    ? L.F("处理中；本机排队 {0}，远端 {1}", data["queued_local_requests"], data["queued_remote_requests"])
                    : L.T("处理中，排队 ") + Convert.ToString(data["queued_requests"]) + L.T(" 个");
                else if (state == "unknown") text = L.T("状态未知，请恢复服务");
                else if (state == "maintenance") text = L.T("Ollama 升级维护中");
                else if (!backend) text = L.T("Ollama 后端离线");
                else text = L.T("后台服务正在停止");
            }
            catch (Exception) { state = "offline"; text = L.T("状态服务离线"); }
            finally { refreshing = false; }
            if (closing) return;
            if (File.Exists(OllamaUpdates.Marker)) { text = L.T("Ollama 升级维护中；右键查看进度"); state = "unknown"; }
            if (!icons.ContainsKey(state)) state = "offline";
            tray.Icon = icons[state]; string tip = "Ollama: " + text; tray.Text = tip.Length > 63 ? tip.Substring(0, 63) : tip; headline.Text = text;
            if (!File.Exists(OllamaUpdates.Marker) && state != lastState && (state == "unknown" || (state == "offline" && lastState != "")))
            {
                updateBalloon = false;
                tray.BalloonTipTitle = L.T("Ollama 状态服务");
                tray.BalloonTipText = text + L.T("。右键图标可查看日志或恢复。");
                tray.BalloonTipIcon = ToolTipIcon.Warning;
                tray.ShowBalloonTip(5000);
            }
            lastState = state;
        }

        void OpenUpdates()
        {
            if (updateForm == null || updateForm.IsDisposed) updateForm = new OllamaUpdateForm();
            updateForm.Show(); updateForm.Activate();
        }

        void ApplyRelease(OllamaRelease release)
        {
            if (closing) return;
            versionGeneration++;
            updateState.SetRelease(release); updateMenu.Text = updateState.Text;
            if (!release.Available) updateBalloon = false;
        }

        void InstallationChanged()
        {
            if (closing) return;
            versionGeneration++;
            updateState.Invalidate(); updateMenu.Text = updateState.Text; updateBalloon = false;
            nextLocalVersionCheck = nextUpdateCheck = DateTime.MinValue;
        }

        void ObserveUpdateCompletion()
        {
            try {
                if (!File.Exists(OllamaUpdates.StatusFile)) return;
                var status = OllamaUpdates.Read(OllamaUpdates.StatusFile);
                DateTime updated = DateTime.Parse(Convert.ToString(status["updated"])).ToUniversalTime();
                if (updated > lastUpdateCompletion && !Convert.ToBoolean(status["active"]) && Convert.ToBoolean(status["success"])) {
                    lastUpdateCompletion = updated; InstallationChanged();
                }
            } catch (IOException) { } catch (UnauthorizedAccessException) { } catch (ArgumentException) { }
        }

        async Task RefreshInstalledVersion(bool force)
        {
            if (closing || readingVersion || (!force && DateTime.UtcNow < nextLocalVersionCheck)) return;
            readingVersion = true; int generation = versionGeneration;
            nextLocalVersionCheck = DateTime.UtcNow.AddSeconds(15);
            try {
                string current = await OllamaUpdates.CurrentVersion();
                if (!closing && generation == versionGeneration) {
                    updateState.ObserveVersion(current);
                    updateMenu.Text = File.Exists(OllamaUpdates.Marker) ? L.T("Ollama 升级进度／恢复…") : updateState.Text;
                }
            } catch {
                if (!closing && generation == versionGeneration) { updateState.Invalidate(); updateMenu.Text = updateState.Text; }
            } finally { readingVersion = false; }
        }

        async Task CheckUpdates()
        {
            if (closing || checkingUpdate || DateTime.UtcNow < nextUpdateCheck) return;
            checkingUpdate = true;
            nextUpdateCheck = DateTime.UtcNow.AddHours(6);
            try {
                if (File.Exists(OllamaUpdates.Marker)) { updateMenu.Text = L.T("Ollama 升级进度／恢复…"); return; }
                var release = await OllamaUpdates.Check();
                if (closing) return;
                ApplyRelease(release);
                if (release.Available && OllamaUpdates.ShouldNotify(release.Latest)) {
                    updateBalloon = true;
                    tray.BalloonTipTitle = L.T("Ollama 有新版本 ") + release.Latest;
                    tray.BalloonTipText = L.T("当前版本 ") + release.Current + L.T("。点击查看并升级；有运行中的任务时不会停服。");
                    tray.BalloonTipIcon = ToolTipIcon.Info; tray.ShowBalloonTip(8000);
                    OllamaUpdates.RememberNotice(release.Latest);
                }
            } catch { nextUpdateCheck = DateTime.UtcNow.AddMinutes(30); }
            finally { checkingUpdate = false; }
        }

        void Open(string target)
        {
            try { Process.Start(new ProcessStartInfo(target) { UseShellExecute = true }); }
            catch (Exception e) { MessageBox.Show(e.Message, L.T("Ollama 状态服务")); }
        }

        void Manage(string action)
        {
            try { TrayWorker.Start("--manage-worker", action); }
            catch (System.ComponentModel.Win32Exception e) { if (e.NativeErrorCode != 1223) MessageBox.Show(e.Message, L.T("Ollama 状态服务")); }
        }

        protected override void ExitThreadCore()
        {
            L.Changed -= languageChanged;
            OllamaUpdates.ReleaseChecked -= ApplyRelease;
            OllamaUpdates.InstallationChanged -= InstallationChanged;
            if (updateForm != null && !updateForm.IsDisposed) updateForm.Close();
            closing = true; timer.Stop(); timer.Dispose(); tray.Visible = false; tray.Dispose(); http.Dispose();
            foreach (Icon icon in icons.Values) icon.Dispose();
            base.ExitThreadCore();
        }
    }
}
