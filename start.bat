@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 查找真实安装的 Python（排除微软商店的占位程序），用 pythonw 启动以避免黑色控制台窗口
set "PYW="
for /f "delims=" %%i in ('python -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do set "PYW=%%i"
if defined PYW if exist "%PYW%" (
    start "" "%PYW%" launcher.py
    exit /b 0
)
where pyw >nul 2>nul && (
    start "" pyw -3 launcher.py
    exit /b 0
)

echo 未检测到 Python 3（运行源码版需要 Python 3.8 或更高版本）。
choice /c YN /m "是否通过 winget 自动安装 Python 3.12"
if errorlevel 2 (
    start "" https://www.python.org/downloads/
    exit /b 1
)
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
    echo 自动安装失败，请手动安装：https://www.python.org/downloads/
    pause
    exit /b 1
)
echo.
echo Python 安装完成，请关闭本窗口后重新双击 start.bat 启动。
pause
