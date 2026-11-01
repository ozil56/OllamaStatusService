using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyFileVersion("1.6.3.0")]
internal static class TrayLauncher
{
    internal static ProcessStartInfo TrayStartInfo(string path)
    {
        return new ProcessStartInfo(path) { UseShellExecute = true, WorkingDirectory = Path.GetDirectoryName(path) };
    }
    [STAThread]
    static int Main()
    {
        OssDialog.Initialize(); Application.SetCompatibleTextRenderingDefault(false);
        string path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), @"OllamaStatusService\OllamaTray.exe");
        try {
            if (!File.Exists(path)) throw new FileNotFoundException(L.T("尚未安装，请先运行 Install.exe。"));
            Process.Start(TrayStartInfo(path));
            return 0;
        } catch (Exception ex) { OssDialog.Message(ex.Message, L.T("Ollama 状态服务")); return 1; }
    }
}
