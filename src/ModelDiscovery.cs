using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using System.Xml;

internal sealed class ModelLocation
{
    internal string Path = "", Source = "", Warning = "";
    internal bool Found;
}

internal sealed class ModelDiscoveryInputs
{
    internal string Profile, LocalAppData, InstallRoot, StateRoot;
    internal string UserModels, MachineModels, ProcessModels;
    internal bool Installed;
}

internal static class ModelDiscovery
{
    internal static string Normalize(string value, string profile)
    {
        if (String.IsNullOrWhiteSpace(value)) return "";
        value = value.Trim().Trim('"', '\'').Trim();
        // USERPROFILE belongs to the original user, not an elevated helper.
        value = Regex.Replace(value, "%USERPROFILE%", delegate(Match m) { return profile; }, RegexOptions.IgnoreCase);
        value = Environment.ExpandEnvironmentVariables(value).Trim().Trim('"', '\'').Trim();
        if (value == "~") value = profile;
        else if (value.StartsWith(@"~\") || value.StartsWith("~/")) value = System.IO.Path.Combine(profile, value.Substring(2));
        // LocalService installations support local absolute paths only.
        if (!Regex.IsMatch(value, @"^[A-Za-z]:[\\/]") || value.Contains("%")) return "";
        try { return System.IO.Path.GetFullPath(value); }
        catch (ArgumentException) { return ""; }
        catch (NotSupportedException) { return ""; }
        catch (PathTooLongException) { return ""; }
    }

    internal static bool IsModelDirectory(string path)
    {
        return !String.IsNullOrWhiteSpace(path) && Directory.Exists(System.IO.Path.Combine(path, "blobs"))
            && Directory.Exists(System.IO.Path.Combine(path, "manifests"));
    }

    static ModelLocation Candidate(string value, string source, string profile)
    {
        string path = Normalize(value, profile);
        return IsModelDirectory(path) ? new ModelLocation { Path = path, Source = source, Found = true } : null;
    }

    internal static string ServiceModelPath(string xmlPath)
    {
        if (!File.Exists(xmlPath)) return "";
        try {
            var settings = new XmlReaderSettings { DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null };
            using (var reader = XmlReader.Create(xmlPath, settings)) {
                var xml = new XmlDocument { XmlResolver = null }; xml.Load(reader);
                var node = xml.SelectSingleNode("/service/env[@name='OLLAMA_MODELS']/@value");
                if (node == null) return "";
                var profile = xml.SelectSingleNode("/service/env[@name='USERPROFILE']/@value");
                return profile == null ? node.Value : Regex.Replace(node.Value, "%USERPROFILE%",
                    delegate(Match m) { return profile.Value; }, RegexOptions.IgnoreCase);
            }
        } catch (IOException) { return ""; }
        catch (UnauthorizedAccessException) { return ""; }
        catch (XmlException) { return ""; }
    }

    static string RecordedModels(string stateRoot)
    {
        try {
            string path = System.IO.Path.Combine(stateRoot, "installation.json");
            if (!File.Exists(path)) return "";
            var record = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(path, Encoding.UTF8));
            object value;
            return record != null && record.TryGetValue("models", out value) ? Convert.ToString(value) : "";
        } catch (IOException) { return ""; }
        catch (UnauthorizedAccessException) { return ""; }
        catch (ArgumentException) { return ""; }
        catch (InvalidOperationException) { return ""; }
    }

    internal static string LogModelPath(string line)
    {
        if (!line.Contains("server config") || !line.Contains("OLLAMA_MODELS:")) return "";
        // Ollama's Go log quotes escape Windows backslashes. Decode the env
        // string first; never execute or display other environment/log contents.
        Match quoted = Regex.Match(line, "env=(\"(?:\\\\.|[^\"\\\\])*\")");
        string map = line;
        if (quoted.Success) {
            try { map = new JavaScriptSerializer().Deserialize<string>(quoted.Groups[1].Value); }
            catch (ArgumentException) { return ""; }
        }
        Match model = Regex.Match(map, @"(?:^|[\s\[])OLLAMA_MODELS:(.*?)(?=\s[A-Z][A-Z0-9_]*:|\]$)");
        return model.Success ? model.Groups[1].Value.Trim() : "";
    }

    static IEnumerable<string> LogCandidates(string localAppData)
    {
        string folder = System.IO.Path.Combine(localAppData, "Ollama");
        var files = new List<FileInfo>();
        try {
            if (!Directory.Exists(folder)) yield break;
            foreach (string path in Directory.GetFiles(folder, "server*.log")) files.Add(new FileInfo(path));
            files.Sort(delegate(FileInfo a, FileInfo b) { return b.LastWriteTimeUtc.CompareTo(a.LastWriteTimeUtc); });
        } catch (IOException) { yield break; }
        catch (UnauthorizedAccessException) { yield break; }
        for (int index = 0; index < Math.Min(files.Count, 3); index++) {
            string text;
            try {
                using (var file = new FileStream(files[index].FullName, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete)) {
                    // Bounded reads: startup at the beginning plus most recent restarts.
                    int headLength = (int)Math.Min(file.Length, 65536);
                    var head = new byte[headLength]; int n = file.Read(head, 0, head.Length);
                    text = Encoding.UTF8.GetString(head, 0, n);
                    if (file.Length > headLength) {
                        file.Seek(Math.Max(headLength, file.Length - 2 * 1024 * 1024), SeekOrigin.Begin);
                        var tail = new byte[(int)Math.Min(file.Length - file.Position, 2 * 1024 * 1024)];
                        int total = 0;
                        while (total < tail.Length) { int read = file.Read(tail, total, tail.Length - total); if (read == 0) break; total += read; }
                        text += "\n" + Encoding.UTF8.GetString(tail, 0, total);
                    }
                }
            } catch (IOException) { continue; }
            catch (UnauthorizedAccessException) { continue; }
            string[] lines = text.Split('\n');
            for (int i = lines.Length - 1; i >= 0; i--) {
                string value = LogModelPath(lines[i].TrimEnd('\r'));
                if (!String.IsNullOrEmpty(value)) { yield return value; break; }
            }
        }
    }

    internal static ModelLocation Find(ModelDiscoveryInputs input)
    {
        ModelLocation found;
        string recorded = RecordedModels(input.StateRoot);
        if (input.Installed) {
            string service = ServiceModelPath(System.IO.Path.Combine(input.InstallRoot, "OllamaStatusBackend.xml"));
            if (!String.IsNullOrWhiteSpace(service)) {
                found = Candidate(service, L.T("现有服务配置"), input.Profile);
                // Do not silently switch an installed service to a different model store.
                return found ?? new ModelLocation { Path = Normalize(service, input.Profile), Source = L.T("现有服务配置"),
                    Warning = L.T("现有服务指定的目录无法访问，或缺少 blobs/manifests；请核对路径和权限。") };
            }
            found = Candidate(recorded, L.T("已有安装记录"), input.Profile);
            if (found != null) return found;
        }
        string[] values = { input.UserModels, input.MachineModels, input.ProcessModels };
        string[] sources = { L.T("用户环境变量（注册表）"), L.T("系统环境变量（注册表）"), L.T("启动进程环境变量") };
        for (int i = 0; i < values.Length; i++) {
            found = Candidate(values[i], sources[i], input.Profile);
            if (found != null) return found;
        }
        foreach (string logged in LogCandidates(input.LocalAppData)) {
            found = Candidate(logged, L.T("Ollama 启动日志"), input.Profile);
            if (found != null) {
                found.Warning = L.T("路径来自启动日志，已确认目录结构；请核对是否仍为当前使用的模型库。");
                return found;
            }
        }
        found = Candidate(recorded, L.T("上次安装记录"), input.Profile);
        if (found != null) return found;
        string defaultPath = System.IO.Path.Combine(input.Profile, @".ollama\models");
        found = Candidate(defaultPath, L.T("Ollama 默认目录"), input.Profile);
        return found ?? new ModelLocation { Path = "", Source = L.T("未找到"),
            Warning = L.T("未找到包含 blobs 和 manifests 的模型目录。请通过“浏览”选择；不会把不存在的默认路径当成已识别。") };
    }
}
