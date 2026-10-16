using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyFileVersion("1.2.5")]
[assembly: System.Reflection.AssemblyInformationalVersion("1.2.5")]

// A Windows-subsystem executable owns the only UI. The elevated copy has no UI;
// it starts the existing PowerShell installation engine with CREATE_NO_WINDOW.
internal static class SetupHost
{
    internal static readonly string Root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
    internal static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    internal static bool IsAdmin { get { return new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator); } }
    [DllImport("user32.dll")] internal static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("kernel32.dll")] internal static extern IntPtr GetConsoleWindow();

    // Windows CommandLineToArgv/CRT quoting, including quotes and trailing slashes.
    internal static string Arg(string value)
    {
        var b = new StringBuilder("\""); int slashes = 0;
        foreach (char c in value) {
            if (c == '\\') { slashes++; continue; }
            b.Append('\\', c == '"' ? slashes * 2 + 1 : slashes); b.Append(c); slashes = 0;
        }
        b.Append('\\', slashes * 2); return b.Append('"').ToString();
    }
    internal static Dictionary<string, object> Read(string path)
    {
        return Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path, Encoding.UTF8));
    }
    internal static void Write(string path, object value)
    {
        File.WriteAllText(path, Json.Serialize(value), new UTF8Encoding(true));
    }

    [STAThread]
    static int Main(string[] args)
    {
        if (args.Length == 2 && args[0] == "--worker") return Worker(args[1]);
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        try {
            bool uninstall = Path.GetFileNameWithoutExtension(Application.ExecutablePath) == "卸载";
            bool verify = Array.IndexOf(args, "--verify") >= 0;
            string evidence = null; bool test = false;
            if (args.Length == 2 && (args[0] == "--test-selection" || args[0] == "--test-verify")) {
                test = true; verify = args[0] == "--test-verify"; evidence = Path.GetFullPath(args[1]);
            }
            using (var form = new SetupForm(uninstall, verify, test, evidence)) {
                Application.Run(form); return form.ExitCode;
            }
        } catch (Exception ex) {
            MessageBox.Show("无法启动安装窗口：\r\n" + ex.Message, "Ollama 状态服务", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    static int Worker(string contextPath)
    {
        Dictionary<string, object> context = null;
        Mutex operationLock = null; bool ownsLock = false;
        try {
            context = Read(contextPath);
            string action = (string)context["Action"];
            if (action != "Verify" && !IsAdmin) throw new InvalidOperationException("未获得管理员权限。");
            if (action != "Install" && action != "Upgrade" && action != "Uninstall" && action != "Verify") throw new InvalidOperationException("未知操作。");
            if (action != "Verify") {
                operationLock = new Mutex(false, @"Global\OllamaStatusService.Setup");
                try { ownsLock = operationLock.WaitOne(0); } catch (AbandonedMutexException) { ownsLock = true; }
                if (!ownsLock) throw new InvalidOperationException("另一个安装或卸载任务正在执行，请等待其完成。");
            }
            var info = new ProcessStartInfo {
                FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe"),
                Arguments = "-NoProfile -NonInteractive -STA -ExecutionPolicy Bypass -File " + Arg(Path.Combine(Root, "scripts", "SetupWorker.ps1")) + " -ContextPath " + Arg(contextPath),
                WorkingDirectory = Root, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
            using (var process = Process.Start(info)) {
                var stdout = process.StandardOutput.ReadToEndAsync();
                var stderr = process.StandardError.ReadToEndAsync();
                process.WaitForExit();
                // Drain both streams concurrently to avoid pipe deadlock on verbose failures.
                File.WriteAllText((string)context["OutputLog"], stdout.Result + "\r\n" + stderr.Result, new UTF8Encoding(true));
                return process.ExitCode;
            }
        } catch (Exception ex) {
            if (context != null) {
                try { Write((string)context["ResultFile"], new { success = false, message = ex.Message }); } catch { }
            }
            return 1;
        } finally {
            if (operationLock != null) { if (ownsLock) operationLock.ReleaseMutex(); operationLock.Dispose(); }
        }
    }
}

internal sealed class SetupForm : Form
{
    readonly bool uninstall, verify, test;
    readonly string evidence;
    readonly TextBox exeBox = new TextBox(), modelBox = new TextBox(), details = new TextBox();
    readonly Button browseExe = new Button(), browseModel = new Button(), primary = new Button(), close = new Button();
    readonly LinkLabel logs = new LinkLabel();
    readonly Label state = new Label();
    readonly Label modelSource = new Label();
    readonly ProgressBar progress = new ProgressBar();
    readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
    Process worker;
    bool busy, completed, upgrade;
    string progressFile, resultFile, logDirectory, bootstrapLog, lastMessage = "", errorMessage;
    DateTime testCloseAt;
    long initialHandle;
    internal int ExitCode = 0;

    internal SetupForm(bool uninstall, bool verify, bool test, string evidence)
    {
        this.uninstall = uninstall; this.verify = verify; this.test = test; this.evidence = evidence;
        Text = (verify ? "校验" : uninstall ? "卸载" : "安装") + " Ollama 状态服务";
        Font = new Font("Microsoft YaHei UI", 10F);
        AutoScaleMode = AutoScaleMode.Dpi; ClientSize = new Size(740, 510);
        FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        Icon = SystemIcons.Application;
        AddLabel("Ollama 状态服务 · 1.2.5", 22, 18, 690, 30).Font = new Font(Font.FontFamily, 16F, FontStyle.Bold);
        AddLabel("直连 11434 · 代理入口 11436 · 共享队列与本机优先", 22, 58, 690, 28);
        AddLabel("Ollama 主程序", 22, 99, 680, 24);
        exeBox.SetBounds(22, 126, 589, 28); Controls.Add(exeBox);
        browseExe.Text = "浏览…"; browseExe.SetBounds(621, 125, 95, 30); Controls.Add(browseExe);
        browseExe.Click += delegate { using (var dlg = new OpenFileDialog { Filter = "Ollama 主程序|ollama.exe" }) { if (dlg.ShowDialog(this) == DialogResult.OK) exeBox.Text = dlg.FileName; } };
        modelSource.Text = "模型目录（包含 blobs 和 manifests）";
        modelSource.SetBounds(22, 168, 690, 24); Controls.Add(modelSource);
        modelBox.SetBounds(22, 195, 589, 28); Controls.Add(modelBox);
        browseModel.Text = "浏览…"; browseModel.SetBounds(621, 194, 95, 30); Controls.Add(browseModel);
        browseModel.Click += delegate { using (var dlg = new FolderBrowserDialog()) { if (dlg.ShowDialog(this) == DialogResult.OK) { modelBox.Text = dlg.SelectedPath; modelSource.Text = "模型目录（手动选择）"; } } };
        var note = AddLabel(verify ? "只校验安装包完整性，不修改系统或现有服务。" : uninstall
            ? "卸载将停止当前推理，移除服务和防火墙规则，并恢复原启动项。原 Ollama、模型、任务记录及日志保留。"
            : "安装会停止现有 Ollama 及正在执行的任务，并备份原启动项。\r\n请先结束当前任务；点击“开始安装”后确认 Windows 管理员权限提示。", 22, 238, 693, 48);
        state.SetBounds(22, 299, 693, 25); state.Text = "就绪"; Controls.Add(state);
        progress.SetBounds(22, 332, 693, 10); Controls.Add(progress);
        details.SetBounds(22, 357, 693, 88); details.Multiline = true; details.ReadOnly = true;
        details.ScrollBars = ScrollBars.Vertical; details.BorderStyle = BorderStyle.FixedSingle; Controls.Add(details);
        logs.Text = "查看日志"; logs.SetBounds(22, 467, 105, 25); logs.Enabled = false; Controls.Add(logs);
        logs.LinkClicked += delegate { if (Directory.Exists(logDirectory)) Process.Start(new ProcessStartInfo(logDirectory) { UseShellExecute = true }); };
        primary.Text = verify ? "开始校验" : uninstall ? "开始卸载" : "开始安装";
        primary.SetBounds(475, 462, 135, 34); Controls.Add(primary); primary.Click += delegate { Begin(); };
        close.Text = "关闭"; close.SetBounds(621, 462, 95, 34); Controls.Add(close); close.Click += delegate { Close(); };
        AcceptButton = primary; CancelButton = close;
        string profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var candidates = new List<string> { Path.Combine(profile, @"AppData\Local\Programs\Ollama\ollama.exe"), Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), @"Ollama\ollama.exe") };
        foreach (string dir in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';')) { try { candidates.Add(Path.Combine(dir.Trim('"'), "ollama.exe")); } catch { } }
        exeBox.Text = candidates.Find(File.Exists) ?? "";
        bool hasInstall = Installed();
        ModelLocation models = null;
        if (!uninstall && !verify) {
            models = ModelDiscovery.Find(new ModelDiscoveryInputs {
                Profile = profile, LocalAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                InstallRoot = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "OllamaStatusService"),
                StateRoot = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "OllamaStatusService"),
                UserModels = Environment.GetEnvironmentVariable("OLLAMA_MODELS", EnvironmentVariableTarget.User),
                MachineModels = Environment.GetEnvironmentVariable("OLLAMA_MODELS", EnvironmentVariableTarget.Machine),
                ProcessModels = Environment.GetEnvironmentVariable("OLLAMA_MODELS"), Installed = hasInstall
            });
            modelBox.Text = models.Path;
            modelSource.Text = "模型目录（来源：" + models.Source + "）";
            details.Text = models.Warning;
        }
        EnableInputs(!uninstall && !verify);
        if (!uninstall && !verify && hasInstall) {
            upgrade = true; primary.Text = "升级到 1.2.5"; state.Text = "检测到已有服务，可直接升级";
            Text = "升级 Ollama 状态服务"; EnableInputs(false);
            note.Text = "升级将自动卸载旧版，再安装新版，期间 Ollama 会短暂停服。\r\n请先暂停本机和远端提交，等待当前任务完成，再点击升级。";
            details.Text = "无需手动卸载。保留原 Ollama、模型和服务配置。1.2.5 修复模型目录权限：自动核实 LocalService 访问权限，并检查模型列表。保留并发 pull/push 修复。代理入口仍为 127.0.0.1:11436。";
            try {
                var installed = SetupHost.Read(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), @"OllamaStatusService\installation.json"));
                exeBox.Text = (string)installed["source_exe"];
                state.Text = "当前版本 " + installed["version"] + " → 1.2.5";
                if (!models.Found) { primary.Enabled = false; details.Text = models.Warning; }
            } catch (Exception ex) { primary.Enabled = false; details.Text = "无法读取旧安装记录，未修改系统：" + ex.Message; }
        }
        FormClosing += delegate(object sender, FormClosingEventArgs e) { if (busy) { e.Cancel = true; state.Text = "操作正在进行，请等待完成后关闭。"; } };
        Shown += delegate {
            initialHandle = Handle.ToInt64(); Activate();
            if (test) {
                if (verify) Begin();
                else { completed = true; testCloseAt = DateTime.UtcNow.AddMilliseconds(900); }
            }
        };
        timer.Interval = 100; timer.Tick += delegate { Poll(); }; timer.Start();
    }
    static bool Installed()
    {
        using (var gateway = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(@"SYSTEM\CurrentControlSet\Services\OllamaStatusGateway"))
        using (var backend = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(@"SYSTEM\CurrentControlSet\Services\OllamaStatusBackend"))
            return gateway != null || backend != null;
    }
    Label AddLabel(string text, int x, int y, int width, int height)
    {
        var label = new Label { Text = text }; label.SetBounds(x, y, width, height); Controls.Add(label); return label;
    }
    void EnableInputs(bool enabled) { exeBox.Enabled = modelBox.Enabled = browseExe.Enabled = browseModel.Enabled = enabled; }
    void Append(string message)
    {
        if (message == lastMessage) return;
        lastMessage = message; details.AppendText((details.TextLength > 0 ? "\r\n" : "") + message);
    }
    void Begin()
    {
        if (busy || completed) return;
        if (!verify && !uninstall && !upgrade && (!File.Exists(exeBox.Text.Trim()) || !ModelDiscovery.IsModelDirectory(modelBox.Text.Trim()))) {
            state.Text = "请检查 Ollama 程序和模型目录"; return;
        }
        try {
            logDirectory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "OllamaStatusService", "setup-logs", DateTime.Now.ToString("yyyyMMdd-HHmmss") + "-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(logDirectory); logs.Enabled = true;
            bootstrapLog = Path.Combine(logDirectory, "setup.log");
            File.WriteAllText(bootstrapLog, DateTime.Now.ToString("s") + " GUI started. Action=" + (verify ? "Verify" : uninstall ? "Uninstall" : upgrade ? "Upgrade" : "Install") + "\r\n", new UTF8Encoding(true));
            progressFile = Path.Combine(logDirectory, "progress.json"); resultFile = Path.Combine(logDirectory, "result.json");
            string contextPath = Path.Combine(logDirectory, "context.json");
            SetupHost.Write(contextPath, new {
                Action = verify ? "Verify" : uninstall ? "Uninstall" : upgrade ? "Upgrade" : "Install",
                CallerProfile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), CallerSid = WindowsIdentity.GetCurrent().User.Value,
                SourceExe = exeBox.Text.Trim(), Models = modelBox.Text.Trim(), ProgressFile = progressFile, ResultFile = resultFile,
                BootstrapLog = bootstrapLog, OutputLog = Path.Combine(logDirectory, "engine-output.log")
            });
            busy = true; primary.Enabled = close.Enabled = false; EnableInputs(false);
            progress.Style = ProgressBarStyle.Marquee; state.Text = verify ? "正在校验安装包…" : "正在请求管理员权限…";
            var info = new ProcessStartInfo(Application.ExecutablePath, "--worker " + SetupHost.Arg(contextPath)) { WorkingDirectory = SetupHost.Root };
            if (!verify && !SetupHost.IsAdmin) { info.UseShellExecute = true; info.Verb = "runas"; }
            else { info.UseShellExecute = false; info.CreateNoWindow = true; }
            worker = Process.Start(info);
            // Retain a process handle so ExitCode survives a fast child exit.
            IntPtr ignored = worker.Handle;
            state.Text = verify ? "正在校验安装包…" : uninstall ? "正在卸载…" : upgrade ? "正在升级…" : "正在安装…";
        } catch (Win32Exception ex) {
            if (ex.NativeErrorCode == 1223) {
                busy = false; primary.Enabled = close.Enabled = true; EnableInputs(!uninstall && !verify && !upgrade);
                progress.Style = ProgressBarStyle.Blocks; state.Text = "已取消管理员授权，可重新点击开始。";
                File.AppendAllText(bootstrapLog, "UAC cancelled by user.\r\n", Encoding.UTF8);
            } else Finish(false, ex.Message);
        } catch (Exception ex) { Finish(false, ex.Message); }
    }
    void Poll()
    {
        if (busy && worker != null) {
            try { if (File.Exists(progressFile)) Append((string)SetupHost.Read(progressFile)["message"]); } catch (IOException) { } catch (ArgumentException) { } catch (InvalidOperationException) { }
            if (worker.HasExited) {
                try {
                    var result = SetupHost.Read(resultFile);
                    Finish(worker.ExitCode == 0 && (bool)result["success"], (string)result["message"]);
                } catch (Exception ex) { Finish(false, "安装引擎未正常返回结果（退出码 " + worker.ExitCode + "）。请查看日志。\r\n" + ex.Message); }
                worker.Dispose(); worker = null;
            }
        }
        if (test && completed && DateTime.UtcNow >= testCloseAt) {
            using (var bitmap = new Bitmap(Width, Height)) { DrawToBitmap(bitmap, new Rectangle(0, 0, Width, Height)); bitmap.Save(Path.ChangeExtension(evidence, ".png")); }
            SetupHost.Write(evidence, new { visible = SetupHost.IsWindowVisible(Handle), console = SetupHost.GetConsoleWindow().ToInt64(),
                same_window = initialHandle == Handle.ToInt64(), exit_code = ExitCode, log_directory = logDirectory,
                upgrade_mode = upgrade, primary_text = primary.Text, primary_enabled = primary.Enabled,
                model_path = modelBox.Text, model_source = modelSource.Text,
                message = errorMessage ?? state.Text });
            Close();
        }
    }
    void Finish(bool success, string message)
    {
        busy = false; completed = true; ExitCode = success ? 0 : 1; errorMessage = success ? null : message;
        progress.Style = ProgressBarStyle.Blocks; progress.Value = success ? 100 : 0;
        state.Text = success ? (verify ? "安装包校验通过" : uninstall ? "卸载完成" : upgrade ? "升级完成" : "安装完成") : "操作未完成，请查看下方原因";
        Append(message); close.Enabled = true; primary.Enabled = false;
        if (success && !uninstall && !verify) {
            try { Process.Start(new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), @"OllamaStatusService\OllamaTray.exe")) { UseShellExecute = true }); }
            catch (Exception ex) { Append("托盘未自动打开，可从开始菜单启动：" + ex.Message); }
        }
        testCloseAt = DateTime.UtcNow.AddMilliseconds(900);
    }
    protected override void Dispose(bool disposing)
    {
        if (disposing) { timer.Dispose(); if (worker != null) worker.Dispose(); }
        base.Dispose(disposing);
    }
}
