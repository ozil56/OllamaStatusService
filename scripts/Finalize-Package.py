"""Generate launchers, offline guide and dependency lock; run before Package.ps1."""
from pathlib import Path
import base64
import html
import importlib.metadata
import json
import re
import sys

root=Path(__file__).resolve().parents[1]
launchers={
    '打开托盘.cmd': '''@echo off
if exist "%ProgramFiles%\\OllamaStatusService\\OllamaTray.exe" (
  start "" "%ProgramFiles%\\OllamaStatusService\\OllamaTray.exe"
) else (
  echo 尚未安装，请先双击安装.exe。
  pause
)
exit /b
'''
}
for name,text in launchers.items():
    (root/name).write_bytes(text.replace('\n','\r\n').encode('gb2312'))

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

source=(root/'部署说明.txt').read_text(encoding='utf-8')
blocks=[]
for paragraph in source.split('\n\n'):
    paragraph=paragraph.strip()
    if not paragraph: continue
    if paragraph.startswith('Ollama 状态服务 1.2.3'):
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
<main><header><h1>Ollama 状态服务</h1><p>Windows x64 · 1.2.3 · 离线安装 · 后台服务与托盘图标</p></header><div class="steps"><b>开始使用：</b>解压整个安装包 → 双击“安装.exe” → 首次安装核对路径；已安装则自动卸载后重装 → 查看右下角图标</div><div class="icons">'''+''.join(icons)+'</div><article>'+''.join(blocks)+'</article></main></html>'
(root/'部署说明.html').write_text(page,encoding='utf-8')
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
print('Generated GB2312/CRLF launchers, UTF-8-BOM PowerShell scripts, HTML guide and version lock.')
