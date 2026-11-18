"""
B站视频下载器 - 启动器（仅依赖 Python 标准库）

启动流程：
  1. 用户协议与免责声明（必须阅读并同意，拒绝则退出）
  2. 加载页：检查 Python / pip，自动安装 requests、sv-ttk、qrcode、Pillow，下载 ffmpeg
  3. 启动主程序 bili_gui
"""
import ctypes
import importlib
import importlib.util
import json
import os
import queue
import shutil
import site
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
import tkinter as tk
from tkinter import font as tkfont, messagebox

from agreement import AGREEMENT_TEXT, AGREEMENT_VERSION
from paths import BASE_DIR, DATA_DIR, FROZEN, CONFIG_FILE, LAUNCHER_FILE

APP_NAME = "B站视频下载器"
APP_DIR = DATA_DIR  # 便携模式：通常为程序目录下的 data 文件夹
LAUNCHER_CFG = LAUNCHER_FILE
MAIN_CFG = CONFIG_FILE
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
MB = 1024 * 1024

# pip 源：优先国内镜像，最后回退官方源
PIP_INDEXES = [
    ("https://pypi.tuna.tsinghua.edu.cn/simple", "pypi.tuna.tsinghua.edu.cn"),
    ("https://mirrors.aliyun.com/pypi/simple/", "mirrors.aliyun.com"),
    (None, None),
]
FFMPEG_URLS = [
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
]
# (导入名, pip 包名, 显示名称, 是否必需)
PACKAGES = [
    ("requests", "requests", "安装网络请求库 requests", True),
    ("sv_ttk", "sv-ttk", "安装 Win11 界面主题 sv-ttk", False),
    ("qrcode", "qrcode", "安装二维码库 qrcode（扫码登录）", False),
    ("PIL", "pillow", "安装图像库 Pillow（显示二维码）", False),
]

LIGHT = {"bg": "#f3f3f3", "card": "#ffffff", "border": "#e5e5e5", "fg": "#1a1a1a", "sub": "#5f5f5f",
         "accent": "#005fb8", "accent_hover": "#196ebf", "accent_fg": "#ffffff",
         "btn": "#fbfbfb", "btn_hover": "#f0f0f0", "dis_bg": "#c5c5c5", "dis_fg": "#ffffff",
         "dis_text": "#a0a0a0", "ok": "#0f7b0f", "warn": "#9d5d00", "err": "#c42b1c", "track": "#cfcfcf"}
DARK = {"bg": "#202020", "card": "#2b2b2b", "border": "#3a3a3a", "fg": "#ffffff", "sub": "#a6a6a6",
        "accent": "#60cdff", "accent_hover": "#5abce9", "accent_fg": "#000000",
        "btn": "#373737", "btn_hover": "#3e3e3e", "dis_bg": "#434343", "dis_fg": "#a0a0a0",
        "dis_text": "#787878", "ok": "#6ccb5f", "warn": "#fce100", "err": "#ff99a4", "track": "#4a4a4a"}

# 步骤状态图标：(Segoe Fluent/MDL2 码位, 回退字符, 颜色键)
STATE_ICON = {
    "pending": ("\uEA3A", "○", "sub"),
    "running": ("\uE895", "↻", "accent"),
    "ok": ("\uE73E", "✓", "ok"),
    "warn": ("\uE7BA", "!", "warn"),
    "fail": ("\uE711", "✕", "err"),
    "skip": ("\uE738", "–", "sub"),
}


class StepError(Exception):
    pass


# ---------------------------------------------------------------- 工具函数
def read_json(path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def system_is_dark() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


def set_titlebar_dark(win, dark):
    if sys.platform != "win32":
        return
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:
        pass


def is_writable(d) -> bool:
    try:
        os.makedirs(d, exist_ok=True)
        test = os.path.join(d, ".write_test")
        with open(test, "w"):
            pass
        os.remove(test)
        return True
    except OSError:
        return False


def has_module(name) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def python_cmd() -> str:
    """pythonw 下运行时，用同目录的 python.exe 调用 pip（输出更可靠）。"""
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        cand = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.isfile(cand):
            return cand
    return exe


def find_ffmpeg():
    custom = read_json(MAIN_CFG).get("ffmpeg") or ""
    for c in (custom, os.path.join(BASE_DIR, "ffmpeg.exe"), os.path.join(APP_DIR, "ffmpeg.exe")):
        if c and os.path.isfile(c):
            return c
    return shutil.which("ffmpeg")


# ---------------------------------------------------------------- Fluent 风格控件
class FluentButton(tk.Label):
    def __init__(self, ui, parent, text, command, accent=False):
        super().__init__(parent, text=text, font=(ui.font, 10), padx=ui.px(20), pady=ui.px(6),
                         cursor="hand2", highlightthickness=1, bd=0)
        self.ui, self.command, self.accent = ui, command, accent
        self.enabled, self.hover = True, False
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<ButtonRelease-1>", self._click)
        self.refresh()

    def _hover(self, v):
        self.hover = v
        self.refresh()

    def _click(self, _):
        if self.enabled and self.command:
            self.command()

    def set_enabled(self, v):
        self.enabled = v
        self.configure(cursor="hand2" if v else "arrow")
        self.refresh()

    def refresh(self):
        p = self.ui.pal
        hot = self.hover and self.enabled
        if self.accent:
            bg = p["accent_hover"] if hot else p["accent"]
            fg = p["accent_fg"]
            if not self.enabled:
                bg, fg = p["dis_bg"], p["dis_fg"]
            border = bg
        else:
            bg = p["btn_hover"] if hot else p["btn"]
            fg = p["fg"] if self.enabled else p["dis_text"]
            border = p["border"]
        self.configure(bg=bg, fg=fg, highlightbackground=border, highlightcolor=border)


class FluentCheck(tk.Frame):
    def __init__(self, ui, parent, text, command=None):
        super().__init__(parent, bg=ui.pal["bg"], cursor="hand2")
        self.ui, self.command, self.checked = ui, command, False
        self.box = tk.Label(self, bg=ui.pal["bg"], font=(ui.icon_font or ui.font, 13))
        self.box.pack(side="left", anchor="n", padx=(0, ui.px(8)))
        self.text = tk.Label(self, text=text, bg=ui.pal["bg"], fg=ui.pal["fg"], font=(ui.font, 10),
                             anchor="w", justify="left", wraplength=ui.px(540))
        self.text.pack(side="left", fill="x")
        for w in (self, self.box, self.text):
            w.bind("<Button-1>", self._toggle)
        self.refresh()

    def _toggle(self, _):
        self.checked = not self.checked
        self.refresh()
        if self.command:
            self.command()

    def refresh(self):
        p = self.ui.pal
        if self.ui.icon_font:
            glyph = "\uE73D" if self.checked else "\uE739"
        else:
            glyph = "☑" if self.checked else "☐"
        self.box.configure(text=glyph, fg=p["accent"] if self.checked else p["sub"])


class Step:
    def __init__(self, name, func, required, skippable=False):
        self.name, self.func, self.required, self.skippable = name, func, required, skippable
        self.icon = self.label = self.status = None


# ---------------------------------------------------------------- 启动器
class Launcher:
    def __init__(self):
        if sys.platform == "win32":
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        self.root.resizable(False, False)
        self.scale = self.root.winfo_fpixels("1i") / 96
        families = set(tkfont.families(self.root))
        self.font = next((f for f in ("Microsoft YaHei UI", "Segoe UI") if f in families), "TkDefaultFont")
        self.icon_font = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in families), None)
        self.dark = system_is_dark()
        self.pal = DARK if self.dark else LIGHT
        self.root.configure(bg=self.pal["bg"])

        w, h = self.px(680), self.px(620)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")

        self.lcfg = read_json(LAUNCHER_CFG)
        self.q = queue.Queue()
        self.result = False
        self.closing = False
        self.proc = None
        self.skip_evt = threading.Event()
        self.screen = None
        self.prog = 0.0          # 当前步骤进度，None 表示不确定
        self.anim = 0.0

        if self.lcfg.get("agreement_version") == AGREEMENT_VERSION and self.lcfg.get("skip_agreement"):
            self.show_loading()
        else:
            self.show_agreement()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.deiconify()
        self.root.after(30, lambda: set_titlebar_dark(self.root, self.dark))
        self.root.after(80, self._poll)

    def px(self, n):
        return int(round(n * self.scale))

    def run(self) -> bool:
        self.root.mainloop()
        return self.result

    def _new_screen(self):
        if self.screen:
            self.screen.destroy()
        self.screen = tk.Frame(self.root, bg=self.pal["bg"], padx=self.px(28), pady=self.px(24))
        self.screen.pack(fill="both", expand=True)
        return self.screen

    def _label(self, parent, text="", size=10, bold=False, color="fg", bg="bg", **kw):
        return tk.Label(parent, text=text, font=(self.font, size, "bold" if bold else "normal"),
                        fg=self.pal[color], bg=self.pal[bg], **kw)

    def on_close(self):
        if self.screen is not None and getattr(self, "running", False):
            if not messagebox.askyesno(APP_NAME, "正在准备运行环境，确定要退出吗？", parent=self.root):
                return
        self.closing = True
        self._kill_proc()
        self.result = False
        self.root.destroy()

    # ============================================================ 用户协议
    def show_agreement(self):
        px, pal = self.px, self.pal
        f = self._new_screen()
        self._label(f, "用户协议与免责声明", 18, True).pack(anchor="w")
        self._label(f, "使用本软件前，请仔细阅读以下全部条款", color="sub").pack(anchor="w", pady=(px(2), px(14)))

        btns = tk.Frame(f, bg=pal["bg"])
        btns.pack(side="bottom", fill="x", pady=(px(14), 0))
        self.accept_btn = FluentButton(self, btns, "同意并继续", self._accept, accent=True)
        self.accept_btn.pack(side="right")
        FluentButton(self, btns, "拒绝并退出", self._decline).pack(side="right", padx=px(8))
        self.read_hint = self._label(btns, "请滚动阅读完全部条款", 9, color="sub")
        self.read_hint.pack(side="left")

        self.chk_skip = FluentCheck(self, f, "以后启动时不再显示（协议更新后会重新显示）")
        self.chk_skip.pack(side="bottom", fill="x", pady=(px(6), 0))
        self.chk_agree = FluentCheck(self, f, "我已阅读、理解并同意以上全部条款，自愿承担使用本软件产生的一切后果，"
                                              "与本软件作者无关", command=self._update_accept)
        self.chk_agree.pack(side="bottom", fill="x", pady=(px(14), 0))

        card = tk.Frame(f, bg=pal["card"], highlightthickness=1,
                        highlightbackground=pal["border"], highlightcolor=pal["border"])
        card.pack(fill="both", expand=True)
        txt = tk.Text(card, wrap="word", bd=0, relief="flat", bg=pal["card"], fg=pal["fg"],
                      font=(self.font, 10), padx=px(18), pady=px(14), spacing1=px(2), spacing3=px(5),
                      highlightthickness=0, cursor="arrow", insertwidth=0, selectbackground=pal["accent"])
        txt.insert("1.0", AGREEMENT_TEXT)
        txt.tag_configure("h", font=(self.font, 11, "bold"), spacing1=px(10))
        for i, line in enumerate(AGREEMENT_TEXT.split("\n"), 1):
            if line[:2] in ("一、", "二、", "三、", "四、", "五、", "六、", "七、", "八、"):
                txt.tag_add("h", f"{i}.0", f"{i}.end")
        txt.configure(state="disabled", yscrollcommand=self._on_text_scroll)
        txt.pack(fill="both", expand=True)
        txt.focus_set()
        self.read_done = False
        self._update_accept()

    def _on_text_scroll(self, first, last):
        if not self.read_done and float(last) >= 0.995:
            self.read_done = True
            self.read_hint.configure(text="已阅读至末尾")
            self._update_accept()

    def _update_accept(self):
        self.accept_btn.set_enabled(self.read_done and self.chk_agree.checked)

    def _accept(self):
        self.lcfg.update({
            "agreement_version": AGREEMENT_VERSION,
            "agreed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "skip_agreement": self.chk_skip.checked,
        })
        write_json(LAUNCHER_CFG, self.lcfg)
        self.show_loading()

    def _decline(self):
        self.closing = True
        self.result = False
        self.root.destroy()

    # ============================================================ 加载页
    def show_loading(self):
        px, pal = self.px, self.pal
        f = self._new_screen()
        self._label(f, APP_NAME, 18, True).pack(anchor="w")
        self._label(f, "正在准备运行环境，首次启动需要联网下载组件，请稍候…", color="sub").pack(
            anchor="w", pady=(px(2), px(14)))

        self.steps = [Step("检查 Python 运行环境", self.step_python, True)]
        if not FROZEN:
            self.steps.append(Step("检查 pip 包管理器", self.step_pip, True))
        for imp, pipname, desc, req in PACKAGES:
            self.steps.append(Step(desc, lambda i=imp, p=pipname: self.step_package(i, p), req, skippable=not req))
        self.steps.append(Step("准备 ffmpeg（合并音视频用，约 100 MB）", self.step_ffmpeg, False, skippable=True))

        card = tk.Frame(f, bg=pal["card"], highlightthickness=1, padx=px(16), pady=px(10),
                        highlightbackground=pal["border"], highlightcolor=pal["border"])
        card.pack(fill="x")
        for st in self.steps:
            row = tk.Frame(card, bg=pal["card"])
            row.pack(fill="x", pady=px(4))
            st.icon = tk.Label(row, bg=pal["card"], width=2, font=(self.icon_font or self.font, 11))
            st.icon.pack(side="left")
            st.label = self._label(row, st.name, bg="card", anchor="w")
            st.label.pack(side="left", padx=(px(8), 0))
            st.status = self._label(row, "", 9, color="sub", bg="card", anchor="e")
            st.status.pack(side="right")
            self._set_state(st, "pending", "")

        self.pcanvas = tk.Canvas(f, height=px(8), bg=pal["bg"], highlightthickness=0, bd=0)
        self.pcanvas.pack(fill="x", pady=(px(18), px(6)))
        self.pcanvas.bind("<Configure>", lambda e: self._draw_progress())
        self.detail = self._label(f, "", 9, color="sub", anchor="w", justify="left", wraplength=px(620))
        self.detail.pack(fill="x")

        bottom = tk.Frame(f, bg=pal["bg"])
        bottom.pack(side="bottom", fill="x", pady=(px(10), 0))
        self.log_toggle = self._label(bottom, "显示详细信息", 9, color="accent", cursor="hand2")
        self.log_toggle.pack(side="left")
        self.log_toggle.bind("<Button-1>", lambda e: self._toggle_log())
        self.quit_btn = FluentButton(self, bottom, "退出", self.on_close)
        self.retry_btn = FluentButton(self, bottom, "重试", self._retry, accent=True)
        self.skip_btn = FluentButton(self, bottom, "跳过此步", self._skip)

        self.log_frame = tk.Frame(f, bg=pal["card"], highlightthickness=1,
                                  highlightbackground=pal["border"], highlightcolor=pal["border"])
        self.log_text = tk.Text(self.log_frame, height=8, bd=0, bg=pal["card"], fg=pal["sub"],
                                font=("Consolas", 9), wrap="char", padx=px(10), pady=px(8),
                                highlightthickness=0, state="disabled")
        self.log_text.pack(fill="both", expand=True)
        self.log_visible = False

        self.running = True
        self._start_worker(0)

    def _set_state(self, st, state, msg):
        glyph, fallback, color = STATE_ICON[state]
        st.icon.configure(text=glyph if self.icon_font else fallback, fg=self.pal[color])
        st.label.configure(fg=self.pal["fg"] if state != "pending" else self.pal["sub"])
        msg = msg if len(msg) <= 34 else msg[:33] + "…"
        st.status.configure(text=msg, fg=self.pal["err"] if state == "fail" else
                            self.pal["warn"] if state == "warn" else self.pal["sub"])

    def _toggle_log(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_frame.pack(fill="both", expand=True, pady=(self.px(10), 0))
            self.log_toggle.configure(text="隐藏详细信息")
        else:
            self.log_frame.pack_forget()
            self.log_toggle.configure(text="显示详细信息")

    def _append_log(self, line):
        t = self.log_text
        t.configure(state="normal")
        t.insert("end", line + "\n")
        if int(t.index("end-1c").split(".")[0]) > 600:
            t.delete("1.0", "100.0")
        t.see("end")
        t.configure(state="disabled")

    # ---------- 进度条（Win11 风格细线）
    def _draw_progress(self):
        c = self.pcanvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 10:
            return
        y, t = h / 2, self.px(3)
        c.create_line(t, y, w - t, y, fill=self.pal["track"], width=max(1, self.px(1)))
        if self.prog is None:
            seg = w * 0.3
            x = (self.anim % 1.0) * (w + seg) - seg
            x1, x2 = max(t, x), min(w - t, x + seg)
            if x2 > x1:
                c.create_line(x1, y, x2, y, fill=self.pal["accent"], width=t, capstyle="round")
        elif self.prog > 0:
            c.create_line(t, y, t + (w - 2 * t) * min(self.prog, 1.0), y,
                          fill=self.pal["accent"], width=t, capstyle="round")

    def _animate(self):
        if self.prog is None and not self.closing:
            self.anim += 0.012
            self._draw_progress()
            self.root.after(16, self._animate)

    def _set_progress(self, value):
        was_indet = self.prog is None
        self.prog = value
        self._draw_progress()
        if value is None and not was_indet:
            self._animate()

    # ---------- 线程通信
    def post(self, *msg):
        self.q.put(msg)

    def log(self, line):
        self.post("log", line)

    def _poll(self):
        if self.closing:
            return
        try:
            while True:
                kind, *a = self.q.get_nowait()
                if kind == "state":
                    i, state, msg = a
                    st = self.steps[i]
                    self._set_state(st, state, msg)
                    if state == "running":
                        self._set_progress(0.0)
                        self.detail.configure(text=f"步骤 {i + 1}/{len(self.steps)}：{st.name}")
                        if st.skippable:
                            self.skip_btn.set_enabled(True)
                            self.skip_btn.pack(side="right")
                        else:
                            self.skip_btn.pack_forget()
                    else:
                        self.skip_btn.pack_forget()
                elif kind == "progress":
                    self._set_progress(a[0])
                elif kind == "detail":
                    self.detail.configure(text=a[0])
                elif kind == "log":
                    self._append_log(a[0])
                elif kind == "failed":
                    self.running = False
                    self._set_progress(0.0)
                    self.failed_at = a[0]
                    self.detail.configure(text=f"必需组件「{self.steps[a[0]].name}」准备失败：{a[1]}\n"
                                               "请检查网络连接后点击「重试」，或点击「显示详细信息」查看原因。")
                    self.retry_btn.pack(side="right")
                    self.quit_btn.pack(side="right", padx=self.px(8))
                elif kind == "done":
                    self.running = False
                    self._set_progress(1.0)
                    self.detail.configure(text="准备完成，正在启动主程序…")
                    self.root.after(500, self._finish)
        except queue.Empty:
            pass
        self.root.after(60, self._poll)

    def _finish(self):
        self.closing = True
        self.result = True
        self.root.destroy()

    def _retry(self):
        self.retry_btn.pack_forget()
        self.quit_btn.pack_forget()
        self.running = True
        self._start_worker(self.failed_at)

    def _skip(self):
        self.skip_evt.set()
        self.skip_btn.set_enabled(False)
        self._kill_proc()

    def _kill_proc(self):
        p = self.proc
        if p is not None:
            try:
                p.kill()
            except OSError:
                pass

    # ---------- 步骤执行（后台线程）
    def _start_worker(self, start):
        threading.Thread(target=self._run_steps, args=(start,), daemon=True).start()

    def _run_steps(self, start):
        for i in range(start, len(self.steps)):
            if self.closing:
                return
            st = self.steps[i]
            self.skip_evt.clear()
            self.post("state", i, "running", "进行中…")
            try:
                state, msg = st.func()
            except Exception as e:
                state, msg = "fail", str(e)
            if self.skip_evt.is_set() and state != "ok":
                state, msg = "skip", msg if state == "skip" else "已跳过"
            if state == "fail" and not st.required:
                state = "warn"
            self.post("state", i, state, msg)
            if state == "fail":
                self.post("failed", i, msg)
                return
        self.post("done")

    def _run(self, cmd) -> int:
        self.log("> " + " ".join(cmd))
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PIP_NO_COLOR="1")
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, env=env,
                                 text=True, encoding="utf-8", errors="replace")
        except OSError as e:
            self.log(f"无法运行：{e}")
            return -1
        self.proc = p
        try:
            for line in p.stdout:
                line = line.rstrip()
                if line:
                    self.log(line)
                    if line.startswith(("Collecting", "Downloading", "Installing", "Successfully")):
                        self.post("detail", line[:120])
            return p.wait()
        finally:
            self.proc = None

    def step_python(self):
        if FROZEN:
            return "ok", "内置运行环境"
        v = sys.version_info
        ver = f"{v.major}.{v.minor}.{v.micro}"
        if v < (3, 8):
            raise StepError(f"需要 Python 3.8+，当前 {ver}")
        return "ok", f"Python {ver}"

    def step_pip(self):
        py = python_cmd()
        if self._run([py, "-m", "pip", "--version"]) == 0:
            return "ok", "已就绪"
        self.post("progress", None)
        self.post("detail", "未找到 pip，正在使用 ensurepip 安装…")
        self._run([py, "-m", "ensurepip", "--upgrade"])
        if self._run([py, "-m", "pip", "--version"]) == 0:
            return "ok", "已安装"
        raise StepError("pip 不可用，请重新安装 Python 并勾选 pip")

    def step_package(self, import_name, pip_name):
        if has_module(import_name):
            return "ok", "已安装"
        if FROZEN:
            raise StepError("程序包中缺少该组件")
        self.post("progress", None)
        self.post("detail", f"正在安装 {pip_name}…")
        py = python_cmd()
        for user in (False, True):
            for index, host in PIP_INDEXES:
                if self.skip_evt.is_set() or self.closing:
                    return "skip", "已跳过"
                cmd = [py, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                       "--timeout", "30", pip_name]
                if user:
                    cmd.append("--user")
                if index:
                    cmd += ["-i", index, "--trusted-host", host]
                if self._run(cmd) == 0:
                    self._refresh_paths()
                    if has_module(import_name):
                        return "ok", "安装完成"
        if self.skip_evt.is_set():
            return "skip", "已跳过"
        raise StepError("安装失败，请检查网络")

    @staticmethod
    def _refresh_paths():
        importlib.invalidate_caches()
        try:
            usp = site.getusersitepackages()
            if os.path.isdir(usp) and usp not in sys.path:
                site.addsitedir(usp)
        except Exception:
            pass
        importlib.invalidate_caches()

    def step_ffmpeg(self):
        path = find_ffmpeg()
        if path:
            return "ok", "已就绪"
        if self.lcfg.get("ffmpeg_skipped"):
            return "skip", "之前已跳过，可在设置中配置"

        dest_dir = BASE_DIR if is_writable(BASE_DIR) else APP_DIR
        os.makedirs(APP_DIR, exist_ok=True)
        zip_path = os.path.join(APP_DIR, "ffmpeg_download.zip")
        last_err, ok = None, False
        for url in FFMPEG_URLS:
            if self.skip_evt.is_set() or self.closing:
                break
            try:
                self.log(f"下载 {url}")
                self._download(url, zip_path)
                ok = True
                break
            except Exception as e:
                last_err = e
                self.log(f"下载失败：{e}")
        if self.skip_evt.is_set() or self.closing:
            self._remove(zip_path)
            self.lcfg["ffmpeg_skipped"] = True
            write_json(LAUNCHER_CFG, self.lcfg)
            return "skip", "已跳过，可在设置中配置"
        if not ok:
            self._remove(zip_path)
            raise StepError(f"下载失败：{last_err}")

        self.post("progress", None)
        self.post("detail", "正在解压 ffmpeg…")
        try:
            with zipfile.ZipFile(zip_path) as z:
                member = next((n for n in z.namelist() if n.lower().endswith("bin/ffmpeg.exe")), None)
                if not member:
                    raise StepError("压缩包中未找到 ffmpeg.exe")
                target = os.path.join(dest_dir, "ffmpeg.exe")
                with z.open(member) as src, open(target + ".part", "wb") as dst:
                    shutil.copyfileobj(src, dst, MB)
                os.replace(target + ".part", target)
        finally:
            self._remove(zip_path)
        self.log(f"ffmpeg 已安装到 {target}")
        return "ok", "安装完成"

    def _download(self, url, path):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 BiliDownloader"})
        with urllib.request.urlopen(req, timeout=30) as r, open(path, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done, t0, last = 0, time.time(), 0.0
            while True:
                if self.skip_evt.is_set() or self.closing:
                    raise StepError("已取消")
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                now = time.time()
                if now - last > 0.15:
                    last = now
                    speed = done / max(now - t0, 1e-6) / MB
                    size = f"{done / MB:.1f}/{total / MB:.1f} MB" if total else f"{done / MB:.1f} MB"
                    self.post("progress", done / total if total else None)
                    self.post("detail", f"正在下载 ffmpeg：{size}   {speed:.2f} MB/s")
        if total and done < total:
            raise StepError("下载不完整")

    @staticmethod
    def _remove(path):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


def main():
    if not Launcher().run():
        return
    try:
        import bili_gui
    except Exception as e:
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror(APP_NAME, f"主程序加载失败：{e}\n\n请重新启动程序以重新检查运行环境。")
        r.destroy()
        return
    bili_gui.App().run()


if __name__ == "__main__":
    main()
