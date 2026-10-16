@echo off
if exist "%ProgramFiles%\OllamaStatusService\OllamaTray.exe" (
  start "" "%ProgramFiles%\OllamaStatusService\OllamaTray.exe"
) else (
  echo 尚未安装，请先双击安装.exe。
  pause
)
exit /b
