# Lite Bili Downloader

一个 Windows 图形界面的 B 站视频下载器。支持粘贴视频链接、BV 号、av 号或 `b23.tv` 短链接，选择分 P 后加入下载队列。也可以在「热门视频」和「我的推荐」里选取视频下载。

## 下载与启动

打开 [最新 Release](https://github.com/GouGoBy/lite-bili-downloader/releases/latest)，在 **Assets** 下选择一个 Windows x64（64 位）版本。两个版本都已包含 Python 运行环境。

| 文件 | 启动方式 |
| --- | --- |
| `BiliDownloader_folder.zip`（推荐） | 完整解压到可写目录，打开 `BiliDownloader` 文件夹，双击 `BiliDownloader.exe`。请保留旁边的 `_internal` 目录。 |
| `BiliDownloader.exe` | 保存到单独的可写目录，双击运行。启动时会在当前工作目录解包运行环境。 |

首次启动请阅读用户协议，滚动到底部后点击「同意并继续」。启动器检测不到 FFmpeg 时会尝试下载，失败时可以重试或跳过，之后可以在「设置」中指定 `ffmpeg.exe`。下载包的 SHA-256 校验值见同一 Release 的 `SHA256SUMS.txt`，组件许可证见 `ThirdPartyNotices.zip`。

## 下载视频

1. 打开「链接下载」，粘贴 B 站视频链接或稿件编号，点击「解析」。
2. 在窗口下方选择下载模式、最高画质和保存位置。支持「合并音视频」「仅视频」「仅音频」。实际画质取决于视频和当前账号可用的播放流。
3. 在分 P 列表选中需要的条目，点击「下载所选」，或点击「下载全部」。在「下载任务」可查看进度、取消任务或打开保存目录。

程序优先把视频保存到 `%USERPROFILE%\Downloads\Bilibili`，可以通过窗口下方的「更改…」修改位置。FFmpeg 用于合并音视频。跳过 FFmpeg 时，独立的音视频流会分别保存为 `.video.mp4` 和 `.audio.m4a`。

需要登录时，打开「设置」→「扫码登录」，用哔哩哔哩 App 扫码并确认。登录凭据优先保存在程序旁的 `data/cookies.json`，程序目录不可写时通常回退到 `%APPDATA%\BiliDownloader`。请勿分享该文件。可以在「设置」中点「退出登录」删除本地凭据。

## 从源码运行和打包

在 Windows 安装带 Tkinter 的 Python 3.12（x64），并把 `python` 加入 `PATH`。从仓库根目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe launcher.py
```

入口是 `launcher.py`。使用系统 Python 时，也可以双击 `start.bat` 启动。需要重新打包时，用下面的命令把虚拟环境加入当前终端的 `PATH`，再调用打包脚本：

```powershell
$env:Path = "$PWD\.venv\Scripts;$env:Path"
cmd /c build.bat
```

打包完成后，`dist/BiliDownloader/` 是文件夹版，`dist/BiliDownloader.exe` 是单文件版。

本工具与哔哩哔哩无官方关联。请只下载你有权保存的内容，并遵守平台规则及内容授权。
