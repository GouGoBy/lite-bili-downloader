"""
B 站下载核心逻辑：接口访问、WBI 签名、扫码登录、热门/推荐列表、视频解析与下载。
仅供个人学习使用，请遵守 B 站用户协议并尊重视频作者版权。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

NAV_API = "https://api.bilibili.com/x/web-interface/nav"
POPULAR_API = "https://api.bilibili.com/x/web-interface/popular"
RCMD_API = "https://api.bilibili.com/x/web-interface/wbi/index/top/feed/rcmd"
VIEW_API = "https://api.bilibili.com/x/web-interface/view"
PLAYURL_API = "https://api.bilibili.com/x/player/playurl"
QR_GEN_API = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL_API = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

QUALITIES = [
    (127, "8K 超高清"), (126, "杜比视界"), (125, "HDR 真彩"), (120, "4K 超清"),
    (116, "1080P 60帧"), (112, "1080P 高码率"), (80, "1080P 高清"),
    (74, "720P 60帧"), (64, "720P 高清"), (32, "480P 清晰"), (16, "360P 流畅"),
]
QUALITY_NAMES = dict(QUALITIES)

MODE_MERGE, MODE_VIDEO, MODE_AUDIO = "merge", "video", "audio"
MODE_NAMES = {MODE_MERGE: "合并音视频", MODE_VIDEO: "仅视频", MODE_AUDIO: "仅音频"}

# 扫码登录状态码
QR_OK, QR_EXPIRED, QR_SCANNED, QR_WAITING = 0, 86038, 86090, 86101

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


class BiliError(Exception):
    pass


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------- 工具函数
def safe_name(name: str, maxlen: int = 90) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name).strip(" .")
    return name[:maxlen] or "video"


def fmt_duration(sec) -> str:
    sec = int(sec or 0)
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_count(n) -> str:
    n = int(n or 0)
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return str(n)


def app_base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def find_ffmpeg(custom: str = ""):
    candidates = []
    if custom:
        candidates.append(custom)
    candidates.append(os.path.join(app_base_dir(), "ffmpeg.exe"))
    try:
        from paths import DATA_DIR
        candidates.append(os.path.join(DATA_DIR, "ffmpeg.exe"))  # 启动器可能下载到这里
    except ImportError:
        pass
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    candidates.append(os.path.join(appdata, "BiliDownloader", "ffmpeg.exe"))
    if hasattr(sys, "_MEIPASS"):
        candidates.append(os.path.join(sys._MEIPASS, "ffmpeg.exe"))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return shutil.which("ffmpeg")


def run_ffmpeg(ffmpeg: str, args: list) -> None:
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW，避免弹出黑框
    r = subprocess.run([ffmpeg, "-y", "-loglevel", "error", *args],
                       capture_output=True, creationflags=flags)
    if r.returncode != 0:
        raise BiliError("ffmpeg 处理失败: " + r.stderr.decode(errors="ignore")[-300:])


def _remove(*paths):
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


# ---------------------------------------------------------------- 客户端
class BiliClient:
    def __init__(self, cookie_file: str = None):
        self.cookie_file = cookie_file
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
        })
        self._inited = False
        self._wbi_key = None
        self._wbi_time = 0
        self.save_error = None
        self.load_cookies()

    # ---------- cookie
    def load_cookies(self):
        if not (self.cookie_file and os.path.isfile(self.cookie_file)):
            return
        try:
            with open(self.cookie_file, encoding="utf-8") as f:
                for k, v in json.load(f).items():
                    self.s.cookies.set(k, v, domain=".bilibili.com")
        except (OSError, ValueError):
            pass

    def save_cookies(self):
        if not self.cookie_file:
            return
        os.makedirs(os.path.dirname(self.cookie_file), exist_ok=True)
        data = {c.name: c.value for c in self.s.cookies if "bilibili.com" in (c.domain or "")}
        with open(self.cookie_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def has_login_cookie(self) -> bool:
        return any(c.name == "SESSDATA" for c in self.s.cookies)

    def logout(self):
        self.s.cookies.clear()
        _remove(self.cookie_file)
        self._inited = False
        self._wbi_key = None

    # ---------- 请求
    def _ensure_init(self):
        if self._inited:
            return
        if not any(c.name == "buvid3" for c in self.s.cookies):
            try:  # 访问首页获取 buvid3 等基础 cookie，降低风控概率
                self.s.get("https://www.bilibili.com/", timeout=10)
            except requests.RequestException:
                pass
        self._inited = True

    def _get_json(self, url, params=None, ok_codes=(0,)) -> dict:
        self._ensure_init()
        try:
            r = self.s.get(url, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            raise BiliError(f"网络错误: {e}") from e
        except ValueError as e:
            raise BiliError("接口返回了无法解析的数据") from e
        if data.get("code") not in ok_codes:
            raise BiliError(f"接口错误 {data.get('code')}: {data.get('message')}")
        return data

    # ---------- WBI 签名
    def _get_wbi_key(self) -> str:
        if self._wbi_key and time.time() - self._wbi_time < 3600:
            return self._wbi_key
        img = self.nav()["wbi_img"]
        img_key = img["img_url"].rsplit("/", 1)[1].split(".")[0]
        sub_key = img["sub_url"].rsplit("/", 1)[1].split(".")[0]
        orig = img_key + sub_key
        self._wbi_key = "".join(orig[i] for i in MIXIN_KEY_ENC_TAB)[:32]
        self._wbi_time = time.time()
        return self._wbi_key

    def _sign(self, params: dict) -> dict:
        key = self._get_wbi_key()
        params = dict(params, wts=int(time.time()))
        params = {k: "".join(ch for ch in str(v) if ch not in "!'()*")
                  for k, v in sorted(params.items())}
        query = urllib.parse.urlencode(params)
        params["w_rid"] = hashlib.md5((query + key).encode()).hexdigest()
        return params

    # ---------- 用户与登录
    def nav(self) -> dict:
        return self._get_json(NAV_API, ok_codes=(0, -101))["data"]

    def user_info(self):
        d = self.nav()
        return d if d.get("isLogin") else None

    def qr_generate(self):
        d = self._get_json(QR_GEN_API)["data"]
        return d["url"], d["qrcode_key"]

    def qr_poll(self, key):
        d = self._get_json(QR_POLL_API, {"qrcode_key": key})["data"]
        if d.get("code") == QR_OK:
            self._wbi_key = None
            try:
                self.save_cookies()
                self.save_error = None
            except OSError as e:  # 无法写入时仍保持本次会话登录
                self.save_error = f"{e}"
        return d.get("code"), d.get("message", "")

    # ---------- 列表
    @staticmethod
    def _norm(v: dict) -> dict:
        return {
            "bvid": v.get("bvid"),
            "cid": v.get("cid"),
            "title": v.get("title", ""),
            "owner": (v.get("owner") or {}).get("name", ""),
            "view": (v.get("stat") or {}).get("view", 0),
            "duration": v.get("duration", 0),
        }

    def popular(self, n: int) -> list:
        out, pn, ps = [], 1, max(1, min(n, 50))
        while len(out) < n:
            d = self._get_json(POPULAR_API, {"ps": ps, "pn": pn})["data"]
            lst = d.get("list") or []
            out += [self._norm(v) for v in lst if v.get("bvid")]
            if d.get("no_more") or not lst:
                break
            pn += 1
        return out[:n]

    def recommend(self, n: int) -> list:
        out, seen, idx = [], set(), 1
        while len(out) < n and idx <= 15:
            params = self._sign({
                "ps": max(10, min(n, 30)), "fresh_type": 4, "fresh_idx": idx,
                "fresh_idx_1h": idx, "brush": idx, "feed_version": "V8",
                "homepage_ver": 1, "y_num": 5, "web_location": 1430650,
            })
            items = self._get_json(RCMD_API, params)["data"].get("item") or []
            for it in items:
                bvid = it.get("bvid")
                if it.get("goto") != "av" or not bvid or bvid in seen:
                    continue
                seen.add(bvid)
                out.append(self._norm(it))
            if not items:
                break
            idx += 1
        return out[:n]

    # ---------- 解析链接
    def resolve(self, text: str) -> dict:
        text = text.strip()
        if not text:
            raise BiliError("请输入视频链接或 BV 号")
        m = re.search(r"b23\.tv/([0-9A-Za-z]+)", text)
        if m:
            try:
                text = self.s.get("https://b23.tv/" + m.group(1), timeout=10).url
            except requests.RequestException as e:
                raise BiliError(f"短链接解析失败: {e}") from e

        bv = re.search(r"BV[0-9A-Za-z]{10}", text)
        av = re.search(r"(?i)(?:^|[^0-9A-Za-z])av(\d+)", text)
        if bv:
            params = {"bvid": bv.group(0)}
        elif av:
            params = {"aid": av.group(1)}
        else:
            raise BiliError("未识别到 BV 号或 av 号")
        p = re.search(r"[?&]p=(\d+)", text)

        d = self._get_json(VIEW_API, params)["data"]
        return {
            "bvid": d["bvid"],
            "title": d.get("title", ""),
            "owner": (d.get("owner") or {}).get("name", ""),
            "view": (d.get("stat") or {}).get("view", 0),
            "pages": [{"cid": pg["cid"], "page": pg["page"], "part": pg.get("part", ""),
                       "duration": pg.get("duration", 0)} for pg in d.get("pages") or []],
            "p": int(p.group(1)) if p else None,
        }

    # ---------- 下载
    def playurl(self, bvid, cid) -> dict:
        return self._get_json(PLAYURL_API, {
            "bvid": bvid, "cid": cid, "qn": 127, "fnval": 4048, "fnver": 0, "fourk": 1,
        })["data"]

    @staticmethod
    def _stream_urls(item: dict) -> list:
        urls = [item.get("baseUrl") or item.get("base_url")]
        urls += item.get("backupUrl") or item.get("backup_url") or []
        return [u for u in urls if u]

    @staticmethod
    def _pick_video(videos: list, max_qn: int) -> dict:
        cand = [v for v in videos if v["id"] <= max_qn] or [min(videos, key=lambda v: v["id"])]
        best = max(v["id"] for v in cand)
        codec_pref = {7: 0, 12: 1, 13: 2}  # 优先 AVC(H.264)，兼容性最好
        return min((v for v in cand if v["id"] == best),
                   key=lambda v: (codec_pref.get(v.get("codecid"), 9), -v.get("bandwidth", 0)))

    def _fetch(self, urls, path, label, progress, cancel):
        last_err = None
        for url in urls:
            try:
                self._fetch_one(url, path, label, progress, cancel)
                return
            except Cancelled:
                raise
            except (requests.RequestException, OSError) as e:
                last_err = e
        raise BiliError(f"{label}下载失败: {last_err}")

    def _fetch_one(self, url, path, label, progress, cancel):
        part = path + ".part"
        try:
            with self.s.get(url, stream=True, timeout=(10, 30)) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length", 0))
                done, t0, last = 0, time.time(), 0.0
                with open(part, "wb") as f:
                    for chunk in r.iter_content(chunk_size=256 * 1024):
                        if cancel is not None and cancel.is_set():
                            raise Cancelled()
                        if not chunk:
                            continue
                        f.write(chunk)
                        done += len(chunk)
                        now = time.time()
                        if progress and (now - last > 0.2 or done == total):
                            last = now
                            progress(label, done, total, done / max(now - t0, 1e-6))
            os.replace(part, path)
        except BaseException:
            _remove(part)
            raise

    def download(self, bvid, cid, name, mode, max_qn, out_dir,
                 ffmpeg=None, progress=None, cancel=None):
        """下载单个视频（分P）。返回 (输出文件路径, 说明文字)。"""
        os.makedirs(out_dir, exist_ok=True)
        base = os.path.join(out_dir, safe_name(name))
        final = base + (".m4a" if mode == MODE_AUDIO else ".mp4")
        if os.path.exists(final):
            return final, "文件已存在，已跳过"

        info = self.playurl(bvid, cid)
        dash = info.get("dash")
        if not dash:
            return self._download_durl(info, base, mode, ffmpeg, progress, cancel)

        videos = dash.get("video") or []
        audios = dash.get("audio") or []
        v = self._pick_video(videos, max_qn) if mode != MODE_AUDIO and videos else None
        a = max(audios, key=lambda x: x.get("bandwidth", 0)) if mode != MODE_VIDEO and audios else None
        if mode == MODE_AUDIO and not a:
            raise BiliError("该视频没有独立音轨")
        if mode != MODE_AUDIO and not v:
            raise BiliError("未获取到视频流（可能需要登录或大会员）")
        qdesc = QUALITY_NAMES.get(v["id"], str(v["id"])) if v else "音频"

        tmp_v, tmp_a = base + ".video.m4s", base + ".audio.m4s"
        try:
            if v:
                self._fetch(self._stream_urls(v), tmp_v, "视频", progress, cancel)
            if a:
                self._fetch(self._stream_urls(a), tmp_a, "音频", progress, cancel)
        except BaseException:
            _remove(tmp_v, tmp_a)
            raise

        if progress:
            progress("处理中", 0, 0, 0)

        if mode == MODE_MERGE:
            if not a:
                self._finalize(tmp_v, final, ffmpeg)
                return final, f"无独立音轨，已保存视频 ({qdesc})"
            if ffmpeg:
                run_ffmpeg(ffmpeg, ["-i", tmp_v, "-i", tmp_a, "-c", "copy", final])
                _remove(tmp_v, tmp_a)
                return final, f"已合并 ({qdesc})"
            os.replace(tmp_v, base + ".video.mp4")
            os.replace(tmp_a, base + ".audio.m4a")
            return base + ".video.mp4", f"未找到 ffmpeg，已分别保存音视频 ({qdesc})"

        if mode == MODE_VIDEO:
            self._finalize(tmp_v, final, ffmpeg)
            return final, f"仅视频 ({qdesc})"

        self._finalize(tmp_a, final, ffmpeg)
        return final, "仅音频"

    @staticmethod
    def _finalize(src, dst, ffmpeg):
        if ffmpeg:
            run_ffmpeg(ffmpeg, ["-i", src, "-c", "copy", dst])
            _remove(src)
        else:
            os.replace(src, dst)

    def _download_durl(self, info, base, mode, ffmpeg, progress, cancel):
        """旧式 FLV/MP4 分段地址的回退下载。"""
        segs = info.get("durl") or []
        if not segs:
            raise BiliError("未获取到视频地址（可能需要登录或大会员）")
        ext = ".mp4" if "mp4" in (info.get("format") or "") else ".flv"
        files = []
        try:
            for i, seg in enumerate(segs):
                p = f"{base}.seg{i}{ext}"
                urls = [seg["url"]] + (seg.get("backup_url") or [])
                self._fetch(urls, p, f"分段 {i + 1}/{len(segs)}", progress, cancel)
                files.append(p)
        except BaseException:
            _remove(*files)
            raise

        if not ffmpeg:
            outs = []
            for i, p in enumerate(files):
                out = base + (f"_part{i + 1}" if len(files) > 1 else "") + ext
                os.replace(p, out)
                outs.append(out)
            return outs[0], "未找到 ffmpeg，已保存原始文件"

        if progress:
            progress("处理中", 0, 0, 0)
        list_file = base + ".concat.txt"
        with open(list_file, "w", encoding="utf-8") as f:
            for p in files:
                f.write("file '{}'\n".format(os.path.abspath(p).replace("'", r"'\''")))
        try:
            if mode == MODE_AUDIO:
                out = base + ".m4a"
                run_ffmpeg(ffmpeg, ["-f", "concat", "-safe", "0", "-i", list_file, "-vn", "-c:a", "copy", out])
            else:
                out = base + ".mp4"
                extra = ["-an"] if mode == MODE_VIDEO else []
                run_ffmpeg(ffmpeg, ["-f", "concat", "-safe", "0", "-i", list_file, *extra, "-c", "copy", out])
        finally:
            _remove(list_file, *files)
        return out, MODE_NAMES.get(mode, "")
