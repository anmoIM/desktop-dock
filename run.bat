@echo off
rem 双击本文件即可启动桌面收录面板（无控制台窗口）
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0main.py"
) else (
    start "" python "%~dp0main.py"
)