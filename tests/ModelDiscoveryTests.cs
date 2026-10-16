using System;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;

internal static class ModelDiscoveryTests
{
    static string root;
    static int count;
    static void Assert(bool result, string message) { if (!result) throw new Exception(message); }
    static string Models(string name) {
        string path = Path.Combine(root, name);
        Directory.CreateDirectory(Path.Combine(path, "blobs")); Directory.CreateDirectory(Path.Combine(path, "manifests"));
        return path;
    }
    static ModelDiscoveryInputs Input() {
        string sandbox = Path.Combine(root, Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(sandbox);
        return new ModelDiscoveryInputs { Profile=sandbox, LocalAppData=Path.Combine(sandbox,"Local"),
            InstallRoot=Path.Combine(sandbox,"Install"), StateRoot=Path.Combine(sandbox,"State") };
    }
    static void Case(string name, Action test) { test(); count++; Console.WriteLine("PASS " + name); }
    static string Log(string path) {
        return "time=2026-10-14 level=INFO msg=\"server config\" env=" + new JavaScriptSerializer().Serialize("map[OLLAMA_HOST:http://127.0.0.1:11434 OLLAMA_MODELS:" + path + " OLLAMA_NUM_PARALLEL:1]");
    }
    static void Record(ModelDiscoveryInputs input, string path) {
        Directory.CreateDirectory(input.StateRoot);
        File.WriteAllText(Path.Combine(input.StateRoot,"installation.json"),new JavaScriptSerializer().Serialize(new {models=path}),Encoding.UTF8);
    }
    static void Service(ModelDiscoveryInputs input, string path) {
        input.Installed=true; Directory.CreateDirectory(input.InstallRoot);
        File.WriteAllText(Path.Combine(input.InstallRoot,"OllamaStatusBackend.xml"),"<service><env name=\"OLLAMA_MODELS\" value=\""+System.Security.SecurityElement.Escape(path)+"\"/></service>",Encoding.UTF8);
    }
    static int Main() {
        root=Path.Combine(Path.GetTempPath(),"OllamaModelDiscovery-"+Guid.NewGuid().ToString("N")); Directory.CreateDirectory(root);
        try {
            Case("machine registry candidate, blank user ignored",delegate {
                var i=Input(); i.UserModels="   "; i.MachineModels=Models("system");
                Assert(ModelDiscovery.Find(i).Path==i.MachineModels,"machine path lost");
            });
            Case("invalid user path does not hide valid machine path",delegate {
                var i=Input(); i.UserModels=Path.Combine(root,"missing"); i.MachineModels=Models("valid-system");
                Assert(ModelDiscovery.Find(i).Path==i.MachineModels,"invalid candidate stopped lookup");
            });
            Case("process-only environment candidate",delegate {
                var i=Input(); i.ProcessModels=Models("process");
                Assert(ModelDiscovery.Find(i).Path==i.ProcessModels,"process value ignored");
            });
            Case("quoted Unicode path and spaces",delegate {
                var i=Input(); string path=Models("中文 模型 & 数据"); i.UserModels="  \""+path+"\"  ";
                Assert(ModelDiscovery.Find(i).Path==path,"quotes not normalized");
            });
            Case("USERPROFILE expands in original user context",delegate {
                var i=Input(); string path=Models(Path.Combine(i.Profile,"models")); i.UserModels=@"%USERPROFILE%\models";
                Assert(ModelDiscovery.Find(i).Path==path,"wrong profile expansion");
            });
            Case("service config outranks stale record and environment",delegate {
                var i=Input(); string actual=Models("active-service"); Service(i,actual); Record(i,Models("stale-record")); i.UserModels=Models("wrong-user");
                Assert(ModelDiscovery.Find(i).Path==actual,"service path lost");
            });
            Case("invalid service path never silently changes model store",delegate {
                var i=Input(); Service(i,Path.Combine(root,"missing-service")); i.MachineModels=Models("unrelated");
                Assert(!ModelDiscovery.Find(i).Found,"silently selected unrelated models");
            });
            Case("installed record fallback",delegate {
                var i=Input(); i.Installed=true; string path=Models("recorded"); Record(i,path);
                Assert(ModelDiscovery.Find(i).Path==path,"record not read");
            });
            Case("old record survives uninstall",delegate {
                var i=Input(); string path=Models("after-uninstall"); Record(i,path);
                Assert(ModelDiscovery.Find(i).Path==path,"uninstalled record not read");
            });
            Case("Go log escapes and latest restart, bounded large file",delegate {
                var i=Input(); string path=Models("日志 中文 & 空格"), old=Models("old-log");
                string folder=Path.Combine(i.LocalAppData,"Ollama"); Directory.CreateDirectory(folder);
                File.WriteAllText(Path.Combine(folder,"server.log"),Log(old)+"\n"+new String('x',3*1024*1024)+"\n"+Log(path)+"\n",Encoding.UTF8);
                var result=ModelDiscovery.Find(i); Assert(result.Path==path,"latest log path lost"); Assert(result.Warning.Length>0,"log treated as guaranteed current");
            });
            Case("default directory structure",delegate {
                var i=Input(); string path=Models(Path.Combine(i.Profile,@".ollama\models"));
                Assert(ModelDiscovery.Find(i).Path==path,"default not detected");
            });
            Case("missing default not presented as found",delegate {
                var i=Input(); var result=ModelDiscovery.Find(i);
                Assert(!result.Found && result.Path=="" && result.Warning.Length>0,"missing path was guessed");
            });
            Case("directory without model structure rejected",delegate {
                var i=Input(); i.UserModels=i.Profile;
                Assert(!ModelDiscovery.Find(i).Found,"arbitrary folder accepted");
            });
            Case("malformed record and log do not crash discovery",delegate {
                var i=Input(); Directory.CreateDirectory(i.StateRoot); File.WriteAllText(Path.Combine(i.StateRoot,"installation.json"),"broken");
                Assert(!ModelDiscovery.Find(i).Found,"invalid record accepted");
                Assert(ModelDiscovery.LogModelPath("hello OLLAMA_MODELS:C:\\fake")=="","nonconfig log accepted");
            });
            Console.WriteLine(count+" model discovery tests passed."); return 0;
        } catch(Exception ex) { Console.Error.WriteLine(ex); return 1; }
        finally {
            string full=Path.GetFullPath(root), temp=Path.GetFullPath(Path.GetTempPath()).TrimEnd('\\')+"\\";
            if(full.StartsWith(temp,StringComparison.OrdinalIgnoreCase) && Path.GetFileName(full).StartsWith("OllamaModelDiscovery-")) Directory.Delete(full,true);
        }
    }
}
