@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [1/2] 安装打包依赖...
python -m pip install -r requirements.txt pyinstaller || goto :error

echo [2/2] 打包 exe（入口为启动器：用户协议 + 加载页 + 主程序）...
rem 文件夹版：不需要解压到临时目录，兼容性最好
python -m PyInstaller --noconfirm --onedir --windowed --name BiliDownloader ^
    --collect-data sv_ttk --hidden-import bili_gui --hidden-import agreement --hidden-import paths ^
    --workpath build\onedir launcher.py || goto :error
rem 单文件版：解压到 exe 所在目录（--runtime-tmpdir .），避免安全软件拦截 %%TEMP%%
python -m PyInstaller --noconfirm --onefile --windowed --name BiliDownloader --runtime-tmpdir . ^
    --collect-data sv_ttk --hidden-import bili_gui --hidden-import agreement --hidden-import paths ^
    --workpath build\onefile launcher.py || goto :error

echo.
echo 打包完成：
echo   文件夹版 dist\BiliDownloader\BiliDownloader.exe（推荐）
echo   单文件版 dist\BiliDownloader.exe
echo 设置与登录信息保存在 exe 旁边的 data 文件夹（便携模式）。
pause
exit /b 0

:error
echo 打包失败，请检查上面的错误信息。
pause
exit /b 1
