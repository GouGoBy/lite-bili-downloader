"""
统一管理程序的数据目录与默认下载目录（仅依赖标准库）。

部分安全软件会阻止未签名程序写入用户目录（%TEMP%、%APPDATA%、下载、文档等），
因此优先使用「便携模式」：数据保存在程序所在文件夹的 data 子目录；
只有程序目录不可写（例如放在 Program Files 中）时才回退到 AppData。
"""
import os
import sys
import tempfile

APP_FOLDER = "BiliDownloader"
FROZEN = getattr(sys, "frozen", False)
BASE_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))


def is_writable(d: str) -> bool:
    """确认目录可创建、可写文件（会按需创建目录）。"""
    if not d:
        return False
    try:
        os.makedirs(d, exist_ok=True)
        test = os.path.join(d, f".write_test_{os.getpid()}")
        with open(test, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(test)
        return True
    except OSError:
        return False


def _pick(candidates):
    for d in candidates:
        if is_writable(d):
            return d
    return None


def _data_dir() -> str:
    appdata = os.environ.get("APPDATA") or ""
    local = os.environ.get("LOCALAPPDATA") or ""
    return _pick([
        os.path.join(BASE_DIR, "data"),
        os.path.join(appdata, APP_FOLDER) if appdata else "",
        os.path.join(local, APP_FOLDER) if local else "",
        os.path.join(tempfile.gettempdir(), APP_FOLDER),
    ]) or os.path.join(BASE_DIR, "data")


DATA_DIR = _data_dir()
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
COOKIE_FILE = os.path.join(DATA_DIR, "cookies.json")
LAUNCHER_FILE = os.path.join(DATA_DIR, "launcher.json")


def default_download_dir() -> str:
    home = os.path.expanduser("~")
    return _pick([
        os.path.join(home, "Downloads", "Bilibili"),
        os.path.join(BASE_DIR, "downloads"),
        os.path.join(DATA_DIR, "downloads"),
    ]) or os.path.join(BASE_DIR, "downloads")


def ffmpeg_install_dir() -> str:
    return BASE_DIR if is_writable(BASE_DIR) else DATA_DIR


def describe_permission_error(path: str) -> str:
    return (f"没有写入权限：{path}\n"
            "可能被安全软件（如火绒 / 联想杀毒）拦截。请把保存位置改到程序所在文件夹，"
            "或在安全软件中将本程序加入信任列表。")
