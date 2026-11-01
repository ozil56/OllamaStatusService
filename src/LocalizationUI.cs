using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;

// Shared by the installer, tray and the separate PowerShell management worker.
// Public so scripts can load the compiled tray assembly without executing Main.
public static class OssDialog
{
    internal static readonly Color Background = Color.FromArgb(245, 247, 250);
    internal static readonly Color Ink = Color.FromArgb(32, 48, 72);
    internal static readonly Color Accent = Color.FromArgb(36, 93, 167);
    static bool initialized;
    public static void Initialize()
    {
        if (initialized) return;
        Application.EnableVisualStyles(); initialized = true;
    }
    internal static void Apply(Control control)
    {
        if (control is Form) { control.BackColor = Background; control.ForeColor = Ink; }
        var button = control as Button;
        if (button != null) {
            button.FlatStyle = FlatStyle.Flat; button.UseVisualStyleBackColor = false;
            button.BackColor = Color.White; button.ForeColor = Ink;
            button.FlatAppearance.BorderColor = Color.FromArgb(196, 207, 221);
            button.FlatAppearance.MouseOverBackColor = Color.FromArgb(232, 239, 248);
            button.FlatAppearance.MouseDownBackColor = Color.FromArgb(219, 231, 245);
            button.Padding = new Padding(8, 0, 8, 0);
        }
        var text = control as TextBox;
        if (text != null) { text.BackColor = Color.White; text.ForeColor = Ink; text.BorderStyle = BorderStyle.FixedSingle; }
        var link = control as LinkLabel;
        if (link != null) link.LinkColor = Accent;
        foreach (Control child in control.Controls) Apply(child);
    }
    internal static void Primary(Button button)
    {
        button.BackColor = Accent; button.ForeColor = Color.White;
        button.FlatAppearance.BorderColor = Accent;
        button.FlatAppearance.MouseOverBackColor = Color.FromArgb(28, 76, 139);
        button.FlatAppearance.MouseDownBackColor = Color.FromArgb(23, 61, 112);
    }
    public static Form Create(string text, string title, string affirmative, string cancel, bool confirmation)
    {
        Initialize();
        var form = new Form {
            Text = title, Font = new Font("Segoe UI", 10F),
            AutoScaleDimensions = new SizeF(96F, 96F), AutoScaleMode = AutoScaleMode.Dpi,
            FormBorderStyle = FormBorderStyle.FixedDialog, MaximizeBox = false, MinimizeBox = false,
            StartPosition = FormStartPosition.CenterScreen, ShowInTaskbar = true
        };
        int height = TextRenderer.MeasureText(text, form.Font, new Size(552, 0), TextFormatFlags.WordBreak | TextFormatFlags.TextBoxControl).Height;
        form.ClientSize = new Size(600, Math.Max(80, Math.Min(330, height + 20)) + 160);
        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(24), ColumnCount = 1, RowCount = 3 };
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 48));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 64));
        var heading = new Label { Text = title, Font = new Font(form.Font.FontFamily, 15F, FontStyle.Bold), Dock = DockStyle.Fill, AutoEllipsis = true };
        var body = new TextBox { Name = "message", Text = text, Multiline = true, ReadOnly = true,
            ScrollBars = height + 20 > 330 ? ScrollBars.Vertical : ScrollBars.None, Dock = DockStyle.Fill, TabStop = false, Margin = new Padding(0, 0, 0, 4) };
        var buttons = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.RightToLeft, WrapContents = false, Padding = new Padding(0, 20, 0, 0) };
        var accept = new Button { Name = "affirmative", Text = affirmative, DialogResult = DialogResult.OK, AutoSize = true, MinimumSize = new Size(120, 36), Margin = new Padding(8, 0, 0, 0) };
        if (confirmation) {
            var dismiss = new Button { Name = "cancel", Text = cancel, DialogResult = DialogResult.Cancel, AutoSize = true, MinimumSize = new Size(120, 36), Margin = new Padding(8, 0, 0, 0), TabIndex = 0 };
            buttons.Controls.Add(dismiss); buttons.Controls.Add(accept); accept.TabIndex = 1;
            // Enter, Escape and the initial focus must not interrupt a task.
            form.AcceptButton = dismiss; form.CancelButton = dismiss;
            form.Shown += delegate { dismiss.Select(); };
        } else {
            buttons.Controls.Add(accept); form.AcceptButton = accept; form.CancelButton = accept;
        }
        layout.Controls.Add(heading, 0, 0); layout.Controls.Add(body, 0, 1); layout.Controls.Add(buttons, 0, 2);
        form.Controls.Add(layout); Apply(form); Primary(accept);
        body.BackColor = form.BackColor; body.BorderStyle = BorderStyle.None;
        return form;
    }
    public static bool Show(string text, string title, string affirmative, string cancel, bool confirmation)
    {
        using (var form = Create(text, title, affirmative, cancel, confirmation)) return form.ShowDialog() == DialogResult.OK;
    }
    internal static void Message(string text, string title) { Message(null, text, title); }
    internal static void Message(IWin32Window owner, string text, string title)
    {
        using (var form = Create(text, title, L.T("关闭"), L.T("取消"), false)) {
            if (owner != null) { form.StartPosition = FormStartPosition.CenterParent; form.ShowInTaskbar = false; form.ShowDialog(owner); }
            else form.ShowDialog();
        }
    }
}

internal static class LanguageUI
{
    internal static void Bind(Control control, Func<string> render)
    {
        control.Tag = render;
        control.Text = render();
    }
    internal static ComboBox Attach(Form form, int x, int y, int width)
    {
        var picker = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = width, Left = x, Top = y, Anchor = AnchorStyles.Left | AnchorStyles.Bottom, AccessibleName = "Language" };
        picker.Items.AddRange(L.Names); picker.Items[0] = L.T("自动（跟随系统）"); picker.SelectedIndex = Array.IndexOf(L.Codes, L.Choice);
        form.Controls.Add(picker); picker.BringToFront();
        string previous = L.Language;
        Action changed = delegate {
            TranslateControls(form, previous); previous = L.Language;
            picker.Items[0] = L.T("自动（跟随系统）");
            picker.SelectedIndex = Array.IndexOf(L.Codes, L.Choice);
        };
        L.Changed += changed;
        form.Disposed += delegate { L.Changed -= changed; };
        picker.SelectedIndexChanged += delegate {
            if (picker.SelectedIndex < 0 || L.Choice == L.Codes[picker.SelectedIndex]) return;
            try { L.Select(L.Codes[picker.SelectedIndex], true); }
            catch (Exception ex) { OssDialog.Message(form, ex.Message, L.T("Ollama 状态服务")); }
        };
        // The installer disables this with the other inputs while an operation runs.
        picker.Name = "language-picker";
        return picker;
    }
    static void TranslateControls(Control control, string previous)
    {
        var render = control.Tag as Func<string>;
        if (render != null) control.Text = render();
        else if (!(control is ComboBox) && (!(control is TextBox) || ((TextBox)control).ReadOnly))
            control.Text = L.FromLanguage(control.Text, previous);
        foreach (Control child in control.Controls) TranslateControls(child, previous);
    }
}

internal sealed class LocalizedHistory
{
    sealed class Entry { internal UiText Message; internal string Timestamp; }
    readonly List<Entry> entries = new List<Entry>();
    readonly TextBox control;
    readonly bool timestamps;
    internal LocalizedHistory(TextBox control, bool timestamps)
    {
        this.control = control; this.timestamps = timestamps;
        if (control.Text.Length > 0) entries.Add(new Entry { Message = UiText.Stored(control.Text, L.Language), Timestamp = "" });
        LanguageUI.Bind(control, Render);
    }
    internal void Append(UiText message)
    {
        if (entries.Count > 0 && entries[entries.Count - 1].Message.Render() == message.Render()) return;
        entries.Add(new Entry { Message = message, Timestamp = timestamps ? DateTime.Now.ToString("HH:mm:ss") + "  " : "" });
        control.Text = Render(); control.SelectionStart = control.TextLength; control.ScrollToCaret();
    }
    string Render()
    {
        var lines = new List<string>();
        foreach (var entry in entries) lines.Add(entry.Timestamp + entry.Message.Render());
        return String.Join(Environment.NewLine, lines);
    }
}
