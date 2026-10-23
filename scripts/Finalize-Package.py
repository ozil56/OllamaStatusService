"""Generate launchers, offline guide and dependency lock; run before Package.ps1."""
from pathlib import Path
import base64
import html
import importlib.metadata
import json
import re
import sys

root=Path(__file__).resolve().parents[1]
for path in (root/'scripts').glob('*.ps1'):
    content=path.read_text(encoding='utf-8-sig')
    path.write_bytes(b'\xef\xbb\xbf'+content.replace('\r\n','\n').replace('\n','\r\n').encode('utf-8'))
# The test script must also be readable by Windows PowerShell 5.1.
for path in (root/'tests').glob('*.ps1'):
    content=path.read_text(encoding='utf-8-sig')
    path.write_bytes(b'\xef\xbb\xbf'+content.replace('\r\n','\n').replace('\n','\r\n').encode('utf-8'))

lock=[]
for dist in importlib.metadata.distributions(path=[str(root/'vendor')]):
    lock.append(dist.metadata['Name']+'=='+dist.version)
(root/'requirements.lock.txt').write_text('\n'.join(sorted(lock,key=str.lower))+'\n',encoding='utf-8')

source=(root/'Deployment-Guide.txt').read_text(encoding='utf-8')
blocks=[]
for paragraph in source.split('\n\n'):
    paragraph=paragraph.strip()
    if not paragraph: continue
    if paragraph.startswith('Ollama 状态服务 1.6.1'):
        continue
    if re.match(r'^[一二三四五六七八九十]+、',paragraph):
        blocks.append('<h2>'+html.escape(paragraph)+'</h2>')
    else:
        safe=html.escape(paragraph)
        safe=re.sub(r'https://[^\s<>]+',lambda m:'<a href="'+m[0]+'">'+m[0]+'</a>',safe)
        blocks.append('<pre>'+safe+'</pre>')
icons=[]
for state,label in [('idle','空闲'),('busy','忙碌'),('offline','离线'),('unknown','状态未知')]:
    path=root/'docs'/'images'/(state+'.png')
    if path.exists():
        icons.append('<span><img alt="'+label+'" src="data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode()+'">'+label+'</span>')
page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ollama 状态服务 · 部署说明</title>
<style>body{margin:0;background:#f2f5fa;color:#203048;font:16px/1.85 "Segoe UI","Microsoft YaHei",sans-serif}main{max-width:1000px;margin:32px auto;padding:0 24px}header{background:#173155;color:white;border-radius:16px;padding:36px}h1{margin:0;font-size:32px}header p{color:#c9d9ef}article{background:white;padding:12px 32px 40px;border:1px solid #dce4ee;border-radius:16px;margin-top:24px}h2{font-size:22px;margin:34px 0 12px;padding-top:12px;border-top:1px solid #e5ebf3}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;margin:14px 0}a{color:#245da7}.icons{display:flex;gap:24px;flex-wrap:wrap;margin-top:24px}.icons span{display:flex;align-items:center;gap:8px}.icons img{width:32px;height:32px}.steps{background:#eaf5f1;padding:18px 24px;border-radius:12px;margin-top:24px}@media print{body{background:white}header{color:#203048;background:#eee}main{margin:0}article{border:0;padding:0}a{color:inherit}}</style>
<main><header><h1>Ollama 状态服务</h1><p>Windows x64 · 1.6.1 · 离线安装 · 后台服务与托盘图标</p></header><div class="steps"><b>开始使用：</b>解压整个安装包 → 双击“Install.exe” → 首次安装核对路径；已安装则自动卸载后重装 → 查看右下角图标</div><div class="icons">'''+''.join(icons)+'</div><article>'+''.join(blocks)+'</article></main></html>'
(root/'Deployment-Guide.html').write_text(page,encoding='utf-8')
# Compact offline guides follow the same language as the desktop UI.
catalog=json.loads((root/'locales/messages.json').read_text(encoding='utf-8'))
names={'zh-CN':'简体中文','zh-TW':'繁體中文','en':'English','ja':'日本語','ko':'한국어','de':'Deutsch','fr':'Français','es':'Español'}
sections=[
    ('语言', ['界面支持八种语言。','默认跟随系统或浏览器语言；可在安装窗口、托盘菜单和网页中选择语言。']),
    ('安装与卸载', ['解压整个安装包，然后运行 Install.exe。卸载请运行 Uninstall.exe。', next(k for k in catalog if k.startswith('安装会停止现有 Ollama')), next(k for k in catalog if k.startswith('升级将自动卸载旧版')), next(k for k in catalog if k.startswith('卸载将停止当前推理'))]),
    ('服务器与模型', [next(k for k in catalog if k.startswith('任务由远端 OSS 托管。')), '自动使用现有代理；无法连接代理时，使用内置本机转发。HTTPS 证书校验保持开启。']),
    ('检查更新／升级 Ollama', [next(k for k in catalog if k.startswith('有新版时仅提示，由你决定升级。'))])]
for language in names:
    def tr(value): return html.escape(value if language=='zh-CN' else catalog[value][language])
    nav=' · '.join('<a href="guide.'+code+'.html" lang="'+code+'">'+name+'</a>' for code,name in names.items())
    body=''.join('<section><h2>'+tr(title)+'</h2>'+''.join('<p>'+tr(p).replace('\n','<br>')+'</p>' for p in paragraphs)+'</section>' for title,paragraphs in sections)
    guide='<!doctype html><html lang="'+language+'"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ollama Status Service</title><style>body{max-width:900px;margin:40px auto;padding:0 24px;font:16px/1.8 system-ui,sans-serif;color:#203048;background:#f5f7fa}section{background:white;padding:12px 24px;margin:20px 0;border-radius:12px}a{color:#245da7}nav{display:flex;gap:8px;flex-wrap:wrap}code{overflow-wrap:anywhere}</style><h1>Ollama Status Service · 1.6.1</h1><nav>'+nav+'</nav>'+body+'<section><a href="http://127.0.0.1:11434/dashboard?lang='+language+'">/dashboard</a> · <a href="http://127.0.0.1:11434/manage?lang='+language+'">/manage</a> · <a href="http://127.0.0.1:11434/status?lang='+language+'">/status</a> · <a href="http://127.0.0.1:11434/help?lang='+language+'">/help</a></section></html>'
    (root/'locales'/('guide.'+language+'.html')).write_text(guide,encoding='utf-8')
notice='''THIRD-PARTY COMPONENTS

Python 3.13.12 Windows x64 embeddable runtime: https://www.python.org/ftp/python/3.13.12/
License: runtime/LICENSE.txt (Python Software Foundation and bundled components).

WinSW 2.12.0: https://github.com/winsw/winsw/releases/tag/v2.12.0
License: licenses/WinSW.txt (MIT).

Python library versions are pinned in requirements.lock.txt.
Each bundled distribution includes its own license/metadata under vendor/*.dist-info.
No Ollama binaries or models are redistributed in this archive; installer references the existing Ollama installation on server A without copying it.
'''
(root/'licenses'/'THIRD-PARTY-NOTICES.txt').write_text(notice,encoding='utf-8')
print('Generated UTF-8-BOM PowerShell scripts, HTML guide and version lock; GUI launchers were compiled.')
