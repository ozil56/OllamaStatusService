using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyFileVersion("1.6.1.0")]
internal static class TrayLauncher
{
    [STAThread]
    static int Main()
    {
        string path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), @"OllamaStatusService\OllamaTray.exe");
        try {
            if (!File.Exists(path)) throw new FileNotFoundException(L.T("尚未安装，请先运行 Install.exe。"));
            Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
            return 0;
        } catch (Exception ex) { MessageBox.Show(ex.Message, L.T("Ollama 状态服务"), MessageBoxButtons.OK, MessageBoxIcon.Information); return 1; }
    }
}
