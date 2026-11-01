using System;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

internal static class DialogTests
{
    static int count;
    static void Assert(bool ok, string message) { if (!ok) throw new Exception(message); }
    static void Case(string name, Action action) {
        try { action(); count++; Console.WriteLine("PASS " + name); }
        catch (Exception ex) { Console.Error.WriteLine("FAIL " + name + ": " + ex.GetType().Name + " " + ex.Message); Environment.Exit(1); }
    }
    static Control Find(Control control, string name) { return control.Controls.Find(name, true)[0]; }
    static void Key(Form form, Keys key)
    {
        typeof(Form).GetMethod("ProcessDialogKey", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(form, new object[] {key});
    }
    static DialogResult Modal(Form form, Action<Form> action)
    {
        using (var timer = new Timer { Interval = 150 }) {
            Exception failure = null;
            timer.Tick += delegate { timer.Stop(); try { action(form); } catch (Exception ex) { failure = ex; form.Close(); } };
            form.StartPosition = FormStartPosition.Manual; form.Location = new Point(-10000, -10000);
            timer.Start(); var result = form.ShowDialog(); if (failure != null) throw failure; return result;
        }
    }
    [STAThread]
    static int Main(string[] args)
    {
        OssDialog.Initialize(); Application.SetCompatibleTextRenderingDefault(false);
        string output = args[0]; Directory.CreateDirectory(output);
        if (args.Length > 1 && args[1] == "--visual-preview") {
            L.Select("zh-CN", false);
            OssDialog.Show(L.T("此操作会停止 Ollama，尚未完成的任务将被中止。是否继续？"), L.T("Ollama 状态服务"), L.T("继续"), L.T("取消"), true);
            string folder = Path.Combine(output, "模型 folder"); Directory.CreateDirectory(folder);
            File.WriteAllText(Path.Combine(output, "selected-folder.txt"), ModelFolderPicker.Show(null, folder, "Ollama models") ?? "cancelled");
            return 0;
        }
        foreach (string language in new[] {"zh-CN", "zh-TW", "en", "ja", "ko", "de", "fr", "es"}) {
            L.Select(language, false);
            Case("confirmation layout and keyboard " + language, delegate {
                Func<Form> create = delegate { return OssDialog.Create(L.T("此操作会停止 Ollama，尚未完成的任务将被中止。是否继续？"), L.T("Ollama 状态服务"), L.T("继续"), L.T("取消"), true); };
                using (var form = create()) {
                    var result = Modal(form, delegate(Form f) {
                        var cancel = (Button)Find(f, "cancel"); var accept = (Button)Find(f, "affirmative");
                        Assert(f.AcceptButton == cancel && f.CancelButton == cancel && cancel.Focused, "Destructive default/focus");
                        Assert(accept.Right <= cancel.Left && cancel.Right <= cancel.Parent.ClientSize.Width, "Overlapping/clipped actions");
                        Assert(accept.Height >= 34 && accept.FlatStyle == FlatStyle.Flat && accept.BackColor == OssDialog.Accent, "Unstyled action");
                        var body = (TextBox)Find(f, "message");
                        int measured = TextRenderer.MeasureText(body.Text, body.Font, new Size(body.ClientSize.Width - SystemInformation.VerticalScrollBarWidth, 0), TextFormatFlags.WordBreak | TextFormatFlags.TextBoxControl).Height;
                        Assert(measured <= body.ClientSize.Height, "Short confirmation clipped");
                        using (var bitmap = new Bitmap(f.Width, f.Height)) { f.DrawToBitmap(bitmap, new Rectangle(0, 0, f.Width, f.Height)); bitmap.Save(Path.Combine(output, "confirm-" + language + ".png")); }
                        Key(f, Keys.Enter);
                    });
                    Assert(result == DialogResult.Cancel, "Enter did not cancel");
                }
                using (var form = create()) Assert(Modal(form, delegate(Form f) { Key(f, Keys.Escape); }) == DialogResult.Cancel, "Escape did not cancel");
                using (var form = create()) Assert(Modal(form, delegate(Form f) { ((Button)Find(f, "affirmative")).PerformClick(); }) == DialogResult.OK, "Explicit confirmation failed");
            });
        }
        Case("long diagnostic scrolls and remains selectable", delegate {
            string text = String.Join(Environment.NewLine, new string('x', 1000).ToCharArray());
            using (var form = OssDialog.Create(text, "Ollama Status Service", "Close", "Cancel", false)) {
                var result = Modal(form, delegate(Form f) {
                    var body = (TextBox)Find(f, "message");
                    Assert(body.ReadOnly && body.Enabled && body.ScrollBars == ScrollBars.Vertical && body.Text == text, "Lost diagnostic");
                    Assert(f.Height < 600, "Unbounded dialog");
                    body.SelectionStart = body.TextLength; body.ScrollToCaret();
                    using (var bitmap = new Bitmap(f.Width, f.Height)) { f.DrawToBitmap(bitmap, new Rectangle(0, 0, f.Width, f.Height)); bitmap.Save(Path.Combine(output, "long-error.png")); }
                    Key(f, Keys.Escape);
                });
                Assert(result == DialogResult.OK, "Close failed");
            }
        });
        Case("modern folder dialog COM options and Unicode path", delegate {
            string folder = Path.Combine(output, "模型 folder"); Directory.CreateDirectory(folder);
            var dialog = ModelFolderPicker.Create(folder, "Ollama models"); ModelFolderPicker.IShellItem item = null;
            try {
                uint options; dialog.GetOptions(out options);
                Assert((options & 0x860) == 0x860, "Folder/filesystem/path restrictions missing");
                dialog.GetFolder(out item); Assert(ModelFolderPicker.PathOf(item) == folder, "Unicode folder not preserved");
            } finally { if (item != null) Marshal.ReleaseComObject(item); Marshal.ReleaseComObject(dialog); }
        });
        Console.WriteLine(count + " dialog checks passed; previews: " + output); return 0;
    }
}
