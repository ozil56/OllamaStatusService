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

namespace OllamaStatusTray
{
    static class Program
    {
        [STAThread]
        static int Main(string[] args)
        {
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
        readonly ToolStripMenuItem headline = new ToolStripMenuItem("正在连接…");
        readonly string root = AppDomain.CurrentDomain.BaseDirectory;
        readonly string stateDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "OllamaStatusService");
        bool refreshing;
        bool closing;
        string lastState = "";

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
            ContextMenuStrip menu = new ContextMenuStrip();
            headline.Enabled = false; menu.Items.Add(headline); menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("打开状态面板", null, delegate { Open("http://127.0.0.1:11434/dashboard"); });
            menu.Items.Add("打开日志目录", null, delegate { Open(Path.Combine(stateDir,"logs")); });
            menu.Items.Add("查看部署说明", null, delegate { Open(Path.Combine(root,"部署说明.html")); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("启动后台服务", null, delegate { Manage("Start"); });
            menu.Items.Add("停止后台服务…", null, delegate { Manage("Stop"); });
            menu.Items.Add("恢复服务（中止当前任务）…", null, delegate { Manage("Recover"); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("退出图标（后台服务继续运行）", null, delegate { ExitThread(); });
            tray.ContextMenuStrip = menu;
            tray.Icon = icons["offline"];
            tray.Text = "Ollama：正在连接状态服务";
            tray.DoubleClick += delegate { Open("http://127.0.0.1:11434/dashboard"); };
            tray.Visible = true;
            timer.Interval = 3000;
            timer.Tick += async delegate { await RefreshState(); };
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
            string text = "状态服务离线";
            try
            {
                string json = await http.GetStringAsync("http://127.0.0.1:11434/status");
                var data = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json);
                state = Convert.ToString(data["state"]);
                bool backend = Convert.ToBoolean(data["ollama_online"]);
                if (state == "idle") text = "空闲，可接收任务";
                else if (state == "busy") text = data.ContainsKey("queued_local_requests")
                    ? "处理中；本机排队 " + Convert.ToString(data["queued_local_requests"]) + "，远端 " + Convert.ToString(data["queued_remote_requests"])
                    : "处理中，排队 " + Convert.ToString(data["queued_requests"]) + " 个";
                else if (state == "unknown") text = "状态未知，请恢复服务";
                else if (!backend) text = "Ollama 后端离线";
                else text = "后台服务正在停止";
            }
            catch (Exception) { state = "offline"; text = "状态服务离线"; }
            finally { refreshing = false; }
            if (closing) return;
            if (!icons.ContainsKey(state)) state = "offline";
            tray.Icon = icons[state]; tray.Text = "Ollama：" + text; headline.Text = text;
            if (state != lastState && (state == "unknown" || (state == "offline" && lastState != "")))
            {
                tray.BalloonTipTitle = "Ollama 状态服务";
                tray.BalloonTipText = text + "。右键图标可查看日志或恢复。";
                tray.BalloonTipIcon = ToolTipIcon.Warning;
                tray.ShowBalloonTip(5000);
            }
            lastState = state;
        }

        void Open(string target)
        {
            try { Process.Start(new ProcessStartInfo(target) { UseShellExecute = true }); }
            catch (Exception e) { MessageBox.Show(e.Message, "Ollama 状态服务"); }
        }

        void Manage(string action)
        {
            string powershell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), @"System32\WindowsPowerShell\v1.0\powershell.exe");
            string script = Path.Combine(root, @"scripts\Manage.ps1");
            try
            {
                Process.Start(new ProcessStartInfo(powershell, "-NoProfile -STA -ExecutionPolicy Bypass -File \"" + script + "\" -Action " + action)
                { UseShellExecute = true, Verb = "runas", WindowStyle = ProcessWindowStyle.Normal });
            }
            catch (System.ComponentModel.Win32Exception e) { if (e.NativeErrorCode != 1223) MessageBox.Show(e.Message, "Ollama 状态服务"); }
        }

        protected override void ExitThreadCore()
        {
            closing = true; timer.Stop(); timer.Dispose(); tray.Visible = false; tray.Dispose(); http.Dispose();
            foreach (Icon icon in icons.Values) icon.Dispose();
            base.ExitThreadCore();
        }
    }
}
