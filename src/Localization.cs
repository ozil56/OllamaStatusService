using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using System.Web.Script.Serialization;

internal static class L
{
    internal static readonly string[] Codes = { "auto", "zh-CN", "zh-TW", "en", "ja", "ko", "de", "fr", "es" };
    internal static readonly string[] Names = { "🌐 Auto", "简体中文", "繁體中文", "English", "日本語", "한국어", "Deutsch", "Français", "Español" };
    static readonly string Preference = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "OllamaStatusService", "language.txt");
    static readonly Dictionary<string, Dictionary<string, string>> Catalog = Load();
    internal static string Choice = ReadChoice();
    internal static event Action Changed;
    internal static string Language { get { return Choice == "auto" ? Match(CultureInfo.CurrentUICulture.Name) : Choice; } }
    internal static string Match(string value)
    {
        value = (value ?? "").ToLowerInvariant().Replace('_', '-');
        if (value.StartsWith("zh")) return value.Contains("hant") || value.Contains("-tw") || value.Contains("-hk") || value.Contains("-mo") ? "zh-TW" : "zh-CN";
        foreach (string code in Codes) if (code != "auto" && (value == code.ToLowerInvariant() || value.StartsWith(code.ToLowerInvariant()+"-"))) return code;
        return "en";
    }
    static Dictionary<string, Dictionary<string, string>> Load()
    {
        using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("OSS.Translations"))
        using (var reader = new StreamReader(stream, Encoding.UTF8))
            return new JavaScriptSerializer { MaxJsonLength = 4000000 }.Deserialize<Dictionary<string, Dictionary<string, string>>>(reader.ReadToEnd());
    }
    static string ReadChoice()
    {
        string forced = Environment.GetEnvironmentVariable("OSS_UI_LANGUAGE");
        if (Array.IndexOf(Codes, forced) >= 0) return forced;
        try { string choice = File.ReadAllText(Preference).Trim(); if (Array.IndexOf(Codes, choice) >= 0) return choice; } catch (IOException) { } catch (UnauthorizedAccessException) { }
        return "auto";
    }
    internal static void Select(string choice, bool save)
    {
        if (Array.IndexOf(Codes, choice) < 0) choice = "auto";
        Choice = choice;
        if (save) { Directory.CreateDirectory(Path.GetDirectoryName(Preference)); File.WriteAllText(Preference, choice, Encoding.UTF8); }
        if (Changed != null) Changed();
    }
    internal static string F(string source, params object[] values) { return String.Format(T(source), values); }
    internal static string T(string source)
    {
        if (String.IsNullOrEmpty(source) || Language == "zh-CN") return source;
        Dictionary<string,string> row; string translated;
        if (Catalog.TryGetValue(source, out row) && (row.TryGetValue(Language, out translated) || row.TryGetValue("en", out translated))) return translated;
        return source;
    }
    static string ConvertExact(string source, string from, string to)
    {
        foreach (var entry in Catalog) {
            string original = from == "zh-CN" ? entry.Key : entry.Value[from];
            if (source == original) return to == "zh-CN" ? entry.Key : entry.Value[to];
        }
        return source;
    }
    static string ConvertText(string source, string from, string to)
    {
        foreach (var entry in Catalog) {
            string original = from == "zh-CN" ? entry.Key : entry.Value.ContainsKey(from) ? entry.Value[from] : entry.Key;
            string translated = to == "zh-CN" ? entry.Key : entry.Value.ContainsKey(to) ? entry.Value[to] : entry.Value["en"];
            if (source == original) return translated;
            if (original.Contains("{0}")) {
                string pattern = System.Text.RegularExpressions.Regex.Escape(original);
                foreach (System.Text.RegularExpressions.Match hole in System.Text.RegularExpressions.Regex.Matches(original, @"\{(\d+)(?::[^{}]+)?\}"))
                    pattern = pattern.Replace(System.Text.RegularExpressions.Regex.Escape(hole.Value), "(?<p"+hole.Groups[1].Value+">[\\s\\S]*?)");
                var match = System.Text.RegularExpressions.Regex.Match(source, "^"+pattern+"$");
                if (match.Success) {
                    return System.Text.RegularExpressions.Regex.Replace(translated, @"\{(\d+)(?::[^{}]+)?\}", m=>ConvertExact(match.Groups["p"+m.Groups[1].Value].Value, from, to));
                }
            }
        }
        // Concatenated labels and diagnostics: translate the longest known prefix
        // or suffix, while preserving an unrecognized path/model/error verbatim.
        var entries = new List<KeyValuePair<string,Dictionary<string,string>>>(Catalog);
        entries.Sort((a,b)=>b.Key.Length.CompareTo(a.Key.Length));
        foreach (var entry in entries) {
            string original = from == "zh-CN" ? entry.Key : entry.Value.ContainsKey(from) ? entry.Value[from] : entry.Key;
            string translated = to == "zh-CN" ? entry.Key : entry.Value.ContainsKey(to) ? entry.Value[to] : entry.Value["en"];
            if (original.Length < 2 || original.Contains("{0}")) continue;
            if (source.StartsWith(original, StringComparison.Ordinal)) return translated + ConvertText(source.Substring(original.Length), from, to);
            if (source.EndsWith(original, StringComparison.Ordinal)) return ConvertText(source.Substring(0, source.Length-original.Length), from, to) + translated;
        }
        return source;
    }
    internal static string Display(string source) { return ConvertText(source ?? "", "zh-CN", Language); }
    internal static string FromLanguage(string source, string from) { return ConvertText(source ?? "", from, Language); }
    internal static string Canonical(string source, string from) { return from == "zh-CN" ? source : ConvertText(source ?? "", from, "zh-CN"); }
    internal static string[] Arguments(string[] args)
    {
        var kept = new List<string>();
        for (int i=0; i<args.Length; i++) {
            if (args[i] == "--language" && i+1<args.Length) Select(args[++i], false);
            else kept.Add(args[i]);
        }
        return kept.ToArray();
    }
}

// Keep message data separate from rendered controls, timestamps and previous UI
// languages. Template arguments are user data and must remain byte-for-byte intact.
internal sealed class UiText
{
    readonly string source, language;
    readonly object[] arguments;
    UiText(string source, string language, object[] arguments) { this.source = source ?? ""; this.language = language; this.arguments = arguments; }
    internal static UiText Template(string source, params object[] arguments) { return new UiText(source, null, arguments); }
    internal static UiText Stored(string source, string language)
    {
        if (Array.IndexOf(L.Codes, language) <= 0) language = "zh-CN";
        // Older workers could save a translated prefix followed by Chinese text.
        // Recover the known source first, then render the complete message.
        return new UiText(L.Canonical(source, language), "zh-CN", null);
    }
    internal static UiText Read(Dictionary<string, object> status)
    {
        object source, language;
        if (status.TryGetValue("source_message", out source)) return Stored(Convert.ToString(source), "zh-CN");
        return Stored(Convert.ToString(status["message"]), status.TryGetValue("language", out language) ? Convert.ToString(language) : "zh-CN");
    }
    internal string Render() { return arguments != null ? L.F(source, arguments) : L.FromLanguage(source, language); }
}
