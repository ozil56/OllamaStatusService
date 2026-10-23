using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;

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
            catch (Exception ex) { MessageBox.Show(ex.Message, "Ollama Status Service"); }
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
