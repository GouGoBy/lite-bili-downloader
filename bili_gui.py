"""
B站视频下载器 - Windows 11 风格图形界面
功能：链接下载 / 热门视频下载 / 扫码登录后下载个性化推荐；
      支持「合并音视频」「仅视频」「仅音频」三种保存方式。
"""
import ctypes
import itertools
import json
import os
import queue
import sys
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, font as tkfont

if sys.platform == "win32":  # 高 DPI 下保持清晰
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

try:
    import sv_ttk  # Sun Valley 主题，仿 Windows 11 外观
except ImportError:
    sv_ttk = None

try:
    import qrcode
    from PIL import ImageTk
except ImportError:
    qrcode = None

from bili_core import (
    BiliClient, BiliError, Cancelled, QUALITIES, MODE_MERGE, MODE_VIDEO, MODE_AUDIO,
    MODE_NAMES, QR_OK, QR_EXPIRED, QR_SCANNED, QR_WAITING,
    find_ffmpeg, fmt_count, fmt_duration,
)
from paths import DATA_DIR, CONFIG_FILE, COOKIE_FILE, default_download_dir, describe_permission_error, is_writable

APP_NAME = "B站视频下载器"
APP_DIR = DATA_DIR

DEFAULT_CONFIG = {
    "save_dir": default_download_dir(),
    "mode": MODE_MERGE,
    "quality": "最高可用",
    "theme": "system",
    "ffmpeg": "",
}

THEMES = {"跟随系统": "system", "浅色": "light", "深色": "dark"}
THEME_LABELS = {v: k for k, v in THEMES.items()}
QUALITY_OPTIONS = ["最高可用"] + [name for _, name in QUALITIES]
QUALITY_QN = {name: qn for qn, name in QUALITIES}
QUALITY_QN["最高可用"] = 127

LIGHT = {"bg": "#fafafa", "side": "#f3f3f3", "hover": "#eaeaea", "sel": "#e3e3e3",
         "fg": "#1a1a1a", "sub": "#5f5f5f", "accent": "#005fb8",
         "card": "#ffffff", "border": "#e5e5e5"}
DARK = {"bg": "#1c1c1c", "side": "#202020", "hover": "#2d2d2d", "sel": "#323232",
        "fg": "#ffffff", "sub": "#a6a6a6", "accent": "#57c8ff",
        "card": "#2b2b2b", "border": "#3a3a3a"}

# Segoe Fluent Icons / MDL2 图标码位
ICON = {"link": "\uE71B", "hot": "\uE734", "rcmd": "\uEB51", "tasks": "\uE896",
        "settings": "\uE713", "user": "\uE77B"}

ACTIVE_STATES = ("等待中", "下载中")
DONE_STATES = ("完成", "失败", "已取消")
MB = 1024 * 1024


def system_is_dark() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


def set_titlebar_dark(win, dark: bool):
    """Windows 10 2004+/11：让标题栏跟随深浅色。"""
    if sys.platform != "win32":
        return
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE（新/旧）
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:
        pass


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


class Task:
    def __init__(self, tid, bvid, cid, title, mode, qn, out_dir, ffmpeg):
        self.id, self.bvid, self.cid, self.title = tid, bvid, cid, title
        self.mode, self.qn, self.out_dir, self.ffmpeg = mode, qn, out_dir, ffmpeg
        self.state = "等待中"
        self.cancel = threading.Event()


# ================================================================ 侧边导航项
class NavItem(tk.Frame):
    def __init__(self, app, master, key, icon, text):
        super().__init__(master, height=app.px(40), cursor="hand2")
        self.pack_propagate(False)
        self.app, self.key = app, key
        self.selected = self.hover = False
        self.bar = tk.Frame(self, width=app.px(3), height=app.px(18))
        self.bar.place(x=0, rely=0.5, anchor="w")
        self.icon = None
        if app.icon_font:
            self.icon = tk.Label(self, text=icon, font=(app.icon_font, 12))
            self.icon.pack(side="left", padx=(app.px(16), app.px(12)))
        self.label = tk.Label(self, text=text, font=(app.ui_font, 10), anchor="w")
        self.label.pack(side="left", fill="x", expand=True,
                        padx=(0 if self.icon else app.px(16), app.px(8)))
        for w in (self, self.icon, self.label):
            if w is None:
                continue
            w.bind("<Enter>", lambda e: self._set_hover(True))
            w.bind("<Leave>", lambda e: self._set_hover(False))
            w.bind("<Button-1>", lambda e: app.show_page(key))

    def _set_hover(self, v):
        self.hover = v
        self.refresh()

    def set_text(self, text):
        self.label.configure(text=text)

    def refresh(self):
        pal = self.app.pal
        bg = pal["sel"] if self.selected else pal["hover"] if self.hover else pal["side"]
        self.configure(bg=bg)
        self.label.configure(bg=bg, fg=pal["fg"])
        if self.icon:
            self.icon.configure(bg=bg, fg=pal["fg"])
        self.bar.configure(bg=pal["accent"] if self.selected else bg)


# ================================================================ 扫码登录对话框
class LoginDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("扫码登录")
        self.resizable(False, False)
        self.transient(app.root)
        self.configure(bg=app.pal["bg"])
        self.alive, self.gen, self.key, self.photo = True, 0, None, None

        body = ttk.Frame(self, padding=app.px(28))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="扫码登录哔哩哔哩", style="Subtitle.TLabel").pack(anchor="w")
        ttk.Label(body, text="打开哔哩哔哩 App，扫描下方二维码", style="Sub.TLabel").pack(anchor="w", pady=(app.px(4), app.px(16)))
        self.qr_label = tk.Label(body, bg="#ffffff", width=app.px(220), height=app.px(220), bd=0)
        self.qr_label.pack()
        self.status = tk.StringVar(value="正在获取二维码…")
        ttk.Label(body, textvariable=self.status).pack(pady=(app.px(14), app.px(10)))
        btns = ttk.Frame(body)
        btns.pack(fill="x")
        self.refresh_btn = ttk.Button(btns, text="刷新二维码", command=self.start)
        self.refresh_btn.pack(side="left", expand=True, fill="x", padx=(0, app.px(6)))
        ttk.Button(btns, text="取消", command=self.close).pack(side="left", expand=True, fill="x")

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(30, lambda: set_titlebar_dark(self, app.is_dark))
        self.update_idletasks()
        x = app.root.winfo_rootx() + (app.root.winfo_width() - self.winfo_width()) // 2
        y = app.root.winfo_rooty() + (app.root.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()
        self.start()

    def start(self):
        self.gen += 1
        self.status.set("正在获取二维码…")
        self.refresh_btn.state(["disabled"])
        gen = self.gen
        self.app.run_bg(self.app.client.qr_generate,
                        lambda r: self._on_qr(r, gen), self._on_err)

    def _on_qr(self, res, gen):
        if not self.alive or gen != self.gen:
            return
        url, self.key = res
        qr = qrcode.QRCode(border=2, box_size=max(4, self.app.px(5)))
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        self.photo = ImageTk.PhotoImage(img)
        self.qr_label.configure(image=self.photo, width=img.width, height=img.height)
        self.status.set("请使用哔哩哔哩 App 扫码")
        self.refresh_btn.state(["!disabled"])
        self.after(2000, lambda: self._poll(gen))

    def _poll(self, gen):
        if not self.alive or gen != self.gen:
            return
        key = self.key
        self.app.run_bg(lambda: self.app.client.qr_poll(key),
                        lambda r: self._on_poll(r, gen), self._on_err)

    def _on_poll(self, res, gen):
        if not self.alive or gen != self.gen:
            return
        code, msg = res
        if code == QR_OK:
            err = self.app.client.save_error
            if err:
                self.status.set("登录成功（登录信息无法保存，下次启动需重新扫码）")
                messagebox.showwarning(APP_NAME, "登录成功，但无法保存登录信息，下次启动需要重新扫码。\n\n"
                                       + describe_permission_error(COOKIE_FILE) + f"\n\n详细错误：{err}", parent=self)
            else:
                self.status.set("登录成功")
            self.app.refresh_user()
            self.after(600, self.close)
            return
        if code == QR_EXPIRED:
            self.status.set("二维码已过期，请点击「刷新二维码」")
            return
        if code == QR_SCANNED:
            self.status.set("已扫码，请在手机上确认登录")
        elif code == QR_WAITING:
            self.status.set("请使用哔哩哔哩 App 扫码")
        else:
            self.status.set(msg or f"状态码 {code}")
        self.after(2000, lambda: self._poll(gen))

    def _on_err(self, e):
        if self.alive:
            self.status.set(f"出错：{e}")
            self.refresh_btn.state(["!disabled"])

    def close(self):
        self.alive = False
        self.grab_release()
        self.destroy()


# ================================================================ 主程序
class App:
    def __init__(self):
        self.cfg = load_config()
        self.client = BiliClient(COOKIE_FILE)
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.scale = self.root.winfo_fpixels("1i") / 96
        self.root.geometry(f"{self.px(1120)}x{self.px(740)}")
        self.root.minsize(self.px(920), self.px(620))

        families = set(tkfont.families(self.root))
        self.ui_font = next((f for f in ("Microsoft YaHei UI", "Segoe UI Variable Text", "Segoe UI")
                             if f in families), "TkDefaultFont")
        self.icon_font = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets")
                               if f in families), None)

        self.ui_q = queue.Queue()
        self.task_q = queue.Queue()
        self.tasks = {}
        self.task_seq = itertools.count(1)
        self.list_data = {}
        self.cards, self.card_inners, self.side_labels = [], [], []
        self.user = None
        self.pal = LIGHT
        self.is_dark = False

        self.mode_var = tk.StringVar(value=self.cfg["mode"])
        self.quality_var = tk.StringVar(value=self.cfg["quality"] if self.cfg["quality"] in QUALITY_OPTIONS else "最高可用")
        self.save_dir_var = tk.StringVar(value=self.cfg["save_dir"])
        self.ffmpeg_var = tk.StringVar(value=self.cfg["ffmpeg"])
        self.theme_var = tk.StringVar(value=THEME_LABELS.get(self.cfg["theme"], "跟随系统"))
        self.status_var = tk.StringVar(value="空闲")

        self._apply_ttk_theme()
        self._build()
        self.apply_theme()
        self.show_page("link")

        threading.Thread(target=self._worker, daemon=True).start()
        self.root.after(100, self._poll_ui)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.update_ffmpeg_status()
        self._on_user(None)
        if self.client.has_login_cookie():
            self.refresh_user()
        if sv_ttk is None:
            self.status_var.set("提示：未安装 sv-ttk，界面将使用系统默认样式（pip install sv-ttk）")
        if not is_writable(self.save_dir_var.get().strip()):
            old = self.save_dir_var.get().strip()
            self.save_dir_var.set(default_download_dir())
            self.status_var.set(f"原保存位置不可写入（{old}），已改为：{self.save_dir_var.get()}")
            self.save_config()

    def px(self, n) -> int:
        return int(round(n * self.scale))

    # ------------------------------------------------------------ 主题
    def _apply_ttk_theme(self):
        mode = THEMES.get(self.theme_var.get(), "system")
        dark = system_is_dark() if mode == "system" else mode == "dark"
        if sv_ttk:
            sv_ttk.set_theme("dark" if dark else "light")
        else:
            dark = False
            st = ttk.Style()
            if "vista" in st.theme_names():
                st.theme_use("vista")
        self.is_dark = dark
        self.pal = DARK if dark else LIGHT

        for name in tkfont.names(self.root):
            try:
                tkfont.nametofont(name, root=self.root).configure(family=self.ui_font)
            except tk.TclError:
                pass

        pal, f = self.pal, self.ui_font
        st = ttk.Style()
        st.configure("Title.TLabel", font=(f, 20, "bold"))
        st.configure("Subtitle.TLabel", font=(f, 14, "bold"))
        st.configure("Sub.TLabel", foreground=pal["sub"])
        st.configure("Card.TLabel", background=pal["card"])
        st.configure("CardSub.TLabel", background=pal["card"], foreground=pal["sub"])
        st.configure("CardTitle.TLabel", background=pal["card"], font=(f, 11, "bold"))
        st.configure("Card.TRadiobutton", background=pal["card"])
        st.configure("Treeview", rowheight=self.px(32))

    def apply_theme(self):
        self._apply_ttk_theme()
        pal = self.pal
        self.root.configure(bg=pal["bg"])
        self.sidebar.configure(bg=pal["side"])
        for lbl, kind in self.side_labels:
            lbl.configure(bg=pal["side"], fg=pal["sub"] if kind == "sub" else pal["fg"])
        for item in self.nav.values():
            item.refresh()
        for c in self.cards:
            c.configure(bg=pal["card"], highlightbackground=pal["border"], highlightcolor=pal["border"])
        for c in self.card_inners:
            c.configure(bg=pal["card"])
        set_titlebar_dark(self.root, self.is_dark)

    def on_theme_change(self, _=None):
        self.apply_theme()
        self.save_config()

    # ------------------------------------------------------------ 布局辅助
    def card(self, parent):
        f = tk.Frame(parent, highlightthickness=1, bd=0, padx=self.px(16), pady=self.px(12))
        self.cards.append(f)
        return f

    def card_row(self, card):
        f = tk.Frame(card, bd=0)
        self.card_inners.append(f)
        return f

    def page(self, key, title, subtitle):
        f = ttk.Frame(self.pages_holder)
        f.grid(row=0, column=0, sticky="nsew")
        ttk.Label(f, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, text=subtitle, style="Sub.TLabel").pack(anchor="w", pady=(self.px(2), self.px(14)))
        self.pages[key] = f
        return f

    def make_tree(self, parent, cols):
        box = ttk.Frame(parent)
        tree = ttk.Treeview(box, columns=[c[0] for c in cols], show="headings", selectmode="extended")
        for key, text, width, anchor, stretch in cols:
            tree.heading(key, text=text, anchor=anchor)
            tree.column(key, width=self.px(width), anchor=anchor, stretch=stretch)
        sb = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return box, tree

    def video_tree(self, parent):
        box, tree = self.make_tree(parent, [
            ("no", "#", 50, "center", False), ("title", "标题", 420, "w", True),
            ("owner", "UP主", 140, "w", False), ("view", "播放", 80, "center", False),
            ("dur", "时长", 80, "center", False)])
        tree.bind("<Double-1>", lambda e: self._open_in_browser(tree))
        return box, tree

    def action_row(self, parent, tree):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(self.px(10), self.px(4)))
        ttk.Button(row, text="全选", command=lambda: tree.selection_set(tree.get_children())).pack(side="left")
        ttk.Label(row, text="双击可在浏览器中打开视频", style="Sub.TLabel").pack(side="left", padx=self.px(12))
        ttk.Button(row, text="下载全部", style="Accent.TButton",
                   command=lambda: self.download_from_tree(tree, True)).pack(side="right")
        ttk.Button(row, text="下载所选",
                   command=lambda: self.download_from_tree(tree, False)).pack(side="right", padx=self.px(8))

    # ------------------------------------------------------------ 构建界面
    def _build(self):
        px = self.px
        self.sidebar = tk.Frame(self.root, width=px(240), bd=0)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        title = tk.Label(self.sidebar, text=APP_NAME, font=(self.ui_font, 12, "bold"), anchor="w")
        title.pack(fill="x", padx=px(22), pady=(px(22), px(18)))
        self.side_labels.append((title, "fg"))

        self.nav = {}
        for key, text in (("link", "链接下载"), ("hot", "热门视频"), ("rcmd", "我的推荐"), ("tasks", "下载任务")):
            item = NavItem(self, self.sidebar, key, ICON[key], text)
            item.pack(fill="x", padx=px(6), pady=px(2))
            self.nav[key] = item

        settings = NavItem(self, self.sidebar, "settings", ICON["settings"], "设置")
        settings.pack(side="bottom", fill="x", padx=px(6), pady=(px(2), px(14)))
        self.nav["settings"] = settings
        self.side_user = tk.Label(self.sidebar, text="未登录", font=(self.ui_font, 9), anchor="w")
        self.side_user.pack(side="bottom", fill="x", padx=px(22), pady=px(8))
        self.side_labels.append((self.side_user, "sub"))

        content = ttk.Frame(self.root)
        content.pack(side="left", fill="both", expand=True)
        self._build_bottom_bar(content)

        self.pages_holder = ttk.Frame(content, padding=(px(28), px(22), px(28), 0))
        self.pages_holder.pack(fill="both", expand=True)
        self.pages_holder.rowconfigure(0, weight=1)
        self.pages_holder.columnconfigure(0, weight=1)
        self.pages = {}
        self._build_link_page()
        self._build_hot_page()
        self._build_rcmd_page()
        self._build_tasks_page()
        self._build_settings_page()

    def _build_bottom_bar(self, parent):
        px = self.px
        bar = self.card(parent)
        bar.pack(side="bottom", fill="x", padx=px(28), pady=(px(10), px(20)))

        r1 = self.card_row(bar)
        r1.pack(fill="x")
        ttk.Label(r1, text="保存方式", style="CardTitle.TLabel").pack(side="left", padx=(0, px(12)))
        for mode in (MODE_MERGE, MODE_VIDEO, MODE_AUDIO):
            ttk.Radiobutton(r1, text=MODE_NAMES[mode], value=mode, variable=self.mode_var,
                            style="Card.TRadiobutton", command=self.save_config).pack(side="left", padx=(0, px(14)))
        cb = ttk.Combobox(r1, textvariable=self.quality_var, values=QUALITY_OPTIONS, state="readonly", width=13)
        cb.pack(side="right")
        cb.bind("<<ComboboxSelected>>", lambda e: self.save_config())
        ttk.Label(r1, text="清晰度上限", style="CardTitle.TLabel").pack(side="right", padx=(0, px(10)))

        r2 = self.card_row(bar)
        r2.pack(fill="x", pady=(px(10), 0))
        ttk.Label(r2, text="保存位置", style="CardTitle.TLabel").pack(side="left", padx=(0, px(12)))
        ttk.Button(r2, text="打开", command=self.open_save_dir).pack(side="right")
        ttk.Button(r2, text="更改…", command=self.choose_save_dir).pack(side="right", padx=px(8))
        ttk.Entry(r2, textvariable=self.save_dir_var).pack(side="left", fill="x", expand=True)

        r3 = self.card_row(bar)
        r3.pack(fill="x", pady=(px(10), 0))
        self.progress = ttk.Progressbar(r3, maximum=100, length=px(220))
        self.progress.pack(side="right")
        ttk.Label(r3, textvariable=self.status_var, style="CardSub.TLabel").pack(side="left", fill="x", expand=True)

    def _build_link_page(self):
        px = self.px
        p = self.page("link", "链接下载", "粘贴视频链接、BV 号、av 号或 b23.tv 短链接，解析后选择要下载的分P")
        c = self.card(p)
        c.pack(fill="x")
        self.url_var = tk.StringVar()
        entry = ttk.Entry(c, textvariable=self.url_var)
        entry.pack(side="left", fill="x", expand=True, padx=(0, px(8)))
        entry.bind("<Return>", lambda e: self.parse_link())
        self.parse_btn = ttk.Button(c, text="解析", style="Accent.TButton", command=self.parse_link)
        self.parse_btn.pack(side="right")
        ttk.Button(c, text="粘贴", command=self.paste_link).pack(side="right", padx=(0, px(8)))

        self.link_info = tk.StringVar(value="尚未解析视频")
        ttk.Label(p, textvariable=self.link_info, style="Sub.TLabel", wraplength=px(760),
                  justify="left").pack(anchor="w", pady=(px(12), px(8)))
        box, self.link_tree = self.make_tree(p, [
            ("no", "P", 60, "center", False), ("title", "分P标题", 480, "w", True),
            ("dur", "时长", 90, "center", False)])
        self.link_tree.bind("<Double-1>", lambda e: self._open_in_browser(self.link_tree))
        box.pack(fill="both", expand=True)
        self.action_row(p, self.link_tree)

    def _build_hot_page(self):
        px = self.px
        p = self.page("hot", "热门视频", "获取 B 站当前「综合热门」榜单，自定义数量后批量下载")
        c = self.card(p)
        c.pack(fill="x")
        ttk.Label(c, text="获取数量", style="Card.TLabel").pack(side="left", padx=(0, px(10)))
        self.hot_n = tk.IntVar(value=3)
        ttk.Spinbox(c, from_=1, to=100, textvariable=self.hot_n, width=6).pack(side="left")
        self.hot_btn = ttk.Button(c, text="获取热门", style="Accent.TButton", command=self.fetch_hot)
        self.hot_btn.pack(side="right")
        self.hot_auto = tk.BooleanVar(value=False)
        ttk.Checkbutton(c, text="获取后自动全部下载", variable=self.hot_auto).pack(side="right", padx=px(12))

        box, self.hot_tree = self.video_tree(p)
        box.pack(fill="both", expand=True, pady=(px(14), 0))
        self.action_row(p, self.hot_tree)

    def _build_rcmd_page(self):
        px = self.px
        p = self.page("rcmd", "我的推荐", "扫码登录后获取首页个性化推荐；未登录时为游客推荐")
        c = self.card(p)
        c.pack(fill="x")
        self.rcmd_hint = tk.StringVar()
        ttk.Label(c, textvariable=self.rcmd_hint, style="Card.TLabel").pack(side="left")
        self.rcmd_btn = ttk.Button(c, text="换一批", style="Accent.TButton", command=self.fetch_rcmd)
        self.rcmd_btn.pack(side="right")
        self.rcmd_n = tk.IntVar(value=10)
        ttk.Spinbox(c, from_=1, to=60, textvariable=self.rcmd_n, width=6).pack(side="right", padx=px(8))
        ttk.Label(c, text="数量", style="Card.TLabel").pack(side="right")
        self.rcmd_login_btn = ttk.Button(c, text="扫码登录", command=self.open_login)
        self.rcmd_login_btn.pack(side="right", padx=px(16))

        box, self.rcmd_tree = self.video_tree(p)
        box.pack(fill="both", expand=True, pady=(px(14), 0))
        self.action_row(p, self.rcmd_tree)

    def _build_tasks_page(self):
        px = self.px
        p = self.page("tasks", "下载任务", "任务按顺序逐个下载，可随时取消")
        box, self.task_tree = self.make_tree(p, [
            ("title", "标题", 360, "w", True), ("mode", "保存方式", 100, "center", False),
            ("state", "状态", 80, "center", False), ("detail", "进度 / 信息", 300, "w", False)])
        box.pack(fill="both", expand=True)
        row = ttk.Frame(p)
        row.pack(fill="x", pady=(px(10), px(4)))
        ttk.Button(row, text="取消所选", command=self.cancel_selected).pack(side="left")
        ttk.Button(row, text="取消全部", command=self.cancel_all).pack(side="left", padx=px(8))
        ttk.Button(row, text="清除已结束", command=self.clear_finished).pack(side="left")
        ttk.Button(row, text="打开下载文件夹", style="Accent.TButton", command=self.open_save_dir).pack(side="right")

    def _build_settings_page(self):
        px = self.px
        p = self.page("settings", "设置", "账号、外观与 ffmpeg 配置")

        c = self.card(p)
        c.pack(fill="x", pady=(0, px(8)))
        if self.icon_font:
            ic = ttk.Label(c, text=ICON["user"], font=(self.icon_font, 22), style="Card.TLabel")
            ic.pack(side="left", padx=(0, px(14)))
        info = self.card_row(c)
        info.pack(side="left", fill="x", expand=True)
        self.acct_name = tk.StringVar(value="未登录")
        self.acct_sub = tk.StringVar(value="登录后可获取个性化推荐与更高清晰度")
        ttk.Label(info, textvariable=self.acct_name, style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(info, textvariable=self.acct_sub, style="CardSub.TLabel").pack(anchor="w")
        self.logout_btn = ttk.Button(c, text="退出登录", command=self.logout)
        self.logout_btn.pack(side="right")
        self.login_btn = ttk.Button(c, text="扫码登录", style="Accent.TButton", command=self.open_login)
        self.login_btn.pack(side="right", padx=px(8))

        c = self.card(p)
        c.pack(fill="x", pady=(0, px(8)))
        col = self.card_row(c)
        col.pack(side="left", fill="x", expand=True)
        ttk.Label(col, text="应用主题", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(col, text="选择浅色、深色或跟随 Windows 设置", style="CardSub.TLabel").pack(anchor="w")
        cb = ttk.Combobox(c, textvariable=self.theme_var, values=list(THEMES), state="readonly", width=12)
        cb.pack(side="right")
        cb.bind("<<ComboboxSelected>>", self.on_theme_change)

        c = self.card(p)
        c.pack(fill="x", pady=(0, px(8)))
        ttk.Label(c, text="ffmpeg", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(c, text="用于合并音视频与转封装。留空则自动查找：程序目录下的 ffmpeg.exe 或系统 PATH",
                  style="CardSub.TLabel").pack(anchor="w")
        r = self.card_row(c)
        r.pack(fill="x", pady=(px(8), 0))
        ttk.Button(r, text="下载 ffmpeg", command=lambda: webbrowser.open("https://www.gyan.dev/ffmpeg/builds/")).pack(side="right")
        ttk.Button(r, text="浏览…", command=self.choose_ffmpeg).pack(side="right", padx=px(8))
        e = ttk.Entry(r, textvariable=self.ffmpeg_var)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<FocusOut>", lambda ev: (self.update_ffmpeg_status(), self.save_config()))
        self.ffmpeg_status = tk.StringVar()
        ttk.Label(c, textvariable=self.ffmpeg_status, style="CardSub.TLabel").pack(anchor="w", pady=(px(6), 0))

        c = self.card(p)
        c.pack(fill="x")
        ttk.Button(c, text="查看用户协议", command=self.show_agreement).pack(side="right", anchor="n")
        ttk.Label(c, text="关于", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(c, text="本工具仅供个人学习交流使用。请遵守哔哩哔哩用户协议，尊重创作者版权，下载内容请勿二次传播。",
                  style="CardSub.TLabel", wraplength=px(700), justify="left").pack(anchor="w")
        ttk.Label(c, text=f"数据目录（设置与登录信息）：{APP_DIR}", style="CardSub.TLabel",
                  wraplength=px(700), justify="left").pack(anchor="w", pady=(px(4), 0))

    def show_agreement(self):
        from agreement import AGREEMENT_TEXT
        win = tk.Toplevel(self.root)
        win.title("用户协议与免责声明")
        win.transient(self.root)
        win.geometry(f"{self.px(640)}x{self.px(560)}")
        win.configure(bg=self.pal["bg"])
        body = ttk.Frame(win, padding=self.px(20))
        body.pack(fill="both", expand=True)
        ttk.Button(body, text="关闭", style="Accent.TButton", command=win.destroy).pack(side="bottom", anchor="e",
                                                                                        pady=(self.px(12), 0))
        txt = tk.Text(body, wrap="word", bd=0, highlightthickness=0, font=(self.ui_font, 10),
                      bg=self.pal["card"], fg=self.pal["fg"], padx=self.px(16), pady=self.px(12))
        txt.insert("1.0", AGREEMENT_TEXT)
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True)
        win.after(30, lambda: set_titlebar_dark(win, self.is_dark))

    # ------------------------------------------------------------ 页面切换
    def show_page(self, key):
        self.pages[key].tkraise()
        for k, item in self.nav.items():
            item.selected = k == key
            item.refresh()

    # ------------------------------------------------------------ 线程通信
    def run_bg(self, fn, on_ok=None, on_err=None):
        def runner():
            try:
                res = fn()
                if on_ok:
                    self.ui_q.put(("call", on_ok, res))
            except Exception as e:
                self.ui_q.put(("call", on_err or self.show_error, e))
        threading.Thread(target=runner, daemon=True).start()

    def _poll_ui(self):
        try:
            while True:
                kind, *args = self.ui_q.get_nowait()
                try:
                    if kind == "call":
                        args[0](args[1])
                    elif kind == "task":
                        self._on_task_update(*args)
                    elif kind == "progress":
                        self._on_progress(*args)
                except tk.TclError:
                    pass
        except queue.Empty:
            pass
        self.root.after(100, self._poll_ui)

    def show_error(self, e):
        messagebox.showerror(APP_NAME, str(e), parent=self.root)

    # ------------------------------------------------------------ 链接解析
    def paste_link(self):
        try:
            self.url_var.set(self.root.clipboard_get().strip())
        except tk.TclError:
            return
        self.parse_link()

    def parse_link(self):
        text = self.url_var.get().strip()
        if not text:
            return
        self.parse_btn.state(["disabled"])
        self.link_info.set("正在解析…")

        def ok(info):
            self.parse_btn.state(["!disabled"])
            tree = self.link_tree
            tree.delete(*tree.get_children())
            data, single = {}, len(info["pages"]) == 1
            target = None
            for pg in info["pages"]:
                name = info["title"] if single else f"{info['title']}_P{pg['page']} {pg['part']}"
                iid = tree.insert("", "end", values=(pg["page"], pg["part"] or info["title"], fmt_duration(pg["duration"])))
                data[iid] = {"bvid": info["bvid"], "cid": pg["cid"], "title": name}
                if info["p"] == pg["page"]:
                    target = iid
            self.list_data[tree] = data
            if target:
                tree.selection_set(target)
                tree.see(target)
            self.link_info.set(f"{info['title']}\nUP主：{info['owner']}    播放：{fmt_count(info['view'])}    "
                               f"共 {len(info['pages'])} P    {info['bvid']}")

        def err(e):
            self.parse_btn.state(["!disabled"])
            self.link_info.set(f"解析失败：{e}")

        self.run_bg(lambda: self.client.resolve(text), ok, err)

    # ------------------------------------------------------------ 热门 / 推荐
    def _fill_video_tree(self, tree, videos):
        tree.delete(*tree.get_children())
        data = {}
        for i, v in enumerate(videos, 1):
            iid = tree.insert("", "end", values=(i, v["title"], v["owner"], fmt_count(v["view"]), fmt_duration(v["duration"])))
            data[iid] = v
        self.list_data[tree] = data

    def _read_int(self, var, default, lo, hi):
        try:
            return max(lo, min(hi, int(var.get())))
        except (tk.TclError, ValueError):
            var.set(default)
            return default

    def fetch_hot(self):
        n = self._read_int(self.hot_n, 3, 1, 100)
        self.hot_btn.state(["disabled"])
        self.status_var.set("正在获取热门列表…")

        def ok(videos):
            self.hot_btn.state(["!disabled"])
            self._fill_video_tree(self.hot_tree, videos)
            self.status_var.set(f"已获取 {len(videos)} 个热门视频")
            if self.hot_auto.get():
                self.download_from_tree(self.hot_tree, True)

        def err(e):
            self.hot_btn.state(["!disabled"])
            self.status_var.set("获取热门失败")
            self.show_error(e)

        self.run_bg(lambda: self.client.popular(n), ok, err)

    def fetch_rcmd(self):
        n = self._read_int(self.rcmd_n, 10, 1, 60)
        self.rcmd_btn.state(["disabled"])
        self.status_var.set("正在获取推荐…")

        def ok(videos):
            self.rcmd_btn.state(["!disabled"])
            self._fill_video_tree(self.rcmd_tree, videos)
            self.status_var.set(f"已获取 {len(videos)} 个推荐视频")

        def err(e):
            self.rcmd_btn.state(["!disabled"])
            self.status_var.set("获取推荐失败")
            self.show_error(e)

        self.run_bg(lambda: self.client.recommend(n), ok, err)

    def _open_in_browser(self, tree):
        sel = tree.selection()
        if sel:
            v = self.list_data.get(tree, {}).get(sel[0])
            if v:
                webbrowser.open(f"https://www.bilibili.com/video/{v['bvid']}")

    # ------------------------------------------------------------ 登录
    def open_login(self):
        if qrcode is None:
            messagebox.showerror(APP_NAME, "缺少依赖：请先执行 pip install qrcode pillow", parent=self.root)
            return
        LoginDialog(self)

    def refresh_user(self):
        self.run_bg(self.client.user_info, self._on_user, lambda e: self._on_user(None))

    def _on_user(self, info):
        self.user = info
        if info:
            name = info.get("uname", "")
            lvl = (info.get("level_info") or {}).get("current_level", "?")
            vip = "  ·  大会员" if info.get("vipStatus") == 1 else ""
            self.acct_name.set(name)
            self.acct_sub.set(f"Lv{lvl}{vip}    UID {info.get('mid', '')}")
            self.side_user.configure(text=f"已登录：{name}")
            self.rcmd_hint.set(f"已登录：{name}，将获取个性化推荐")
            self.rcmd_login_btn.pack_forget()
            self.login_btn.state(["disabled"])
            self.logout_btn.state(["!disabled"])
        else:
            self.acct_name.set("未登录")
            self.acct_sub.set("登录后可获取个性化推荐与更高清晰度")
            self.side_user.configure(text="未登录")
            self.rcmd_hint.set("未登录：当前为游客推荐")
            if not self.rcmd_login_btn.winfo_ismapped():
                self.rcmd_login_btn.pack(side="right", padx=self.px(16))
            self.login_btn.state(["!disabled"])
            self.logout_btn.state(["disabled"])

    def logout(self):
        if messagebox.askyesno(APP_NAME, "确定退出登录吗？", parent=self.root):
            self.client.logout()
            self._on_user(None)

    # ------------------------------------------------------------ 下载任务
    def download_from_tree(self, tree, all_items):
        iids = tree.get_children() if all_items else tree.selection()
        data = self.list_data.get(tree, {})
        items = [data[i] for i in iids if i in data]
        if not items:
            messagebox.showinfo(APP_NAME, "请先获取列表并选择要下载的视频", parent=self.root)
            return
        for v in items:
            self.enqueue(v["bvid"], v.get("cid"), v["title"])
        self.status_var.set(f"已添加 {len(items)} 个下载任务")
        self._update_badge()

    def enqueue(self, bvid, cid, title):
        mode = self.mode_var.get()
        qn = QUALITY_QN.get(self.quality_var.get(), 127)
        out_dir = self.save_dir_var.get().strip() or DEFAULT_CONFIG["save_dir"]
        tid = str(next(self.task_seq))
        task = Task(tid, bvid, cid, title, mode, qn, out_dir, self.ffmpeg_var.get().strip())
        self.tasks[tid] = task
        self.task_tree.insert("", "end", iid=tid, values=(title, MODE_NAMES[mode], "等待中", ""))
        self.task_q.put(task)

    def _worker(self):
        while True:
            task = self.task_q.get()
            if task.cancel.is_set():
                continue
            self.ui_q.put(("task", task.id, "下载中", "准备中…"))
            try:
                cid = task.cid
                if not cid:
                    cid = self.client.resolve(task.bvid)["pages"][0]["cid"]
                out, msg = self.client.download(
                    task.bvid, cid, task.title, task.mode, task.qn, task.out_dir,
                    ffmpeg=find_ffmpeg(task.ffmpeg),
                    progress=lambda st, d, t, sp, tid=task.id: self.ui_q.put(("progress", tid, st, d, t, sp)),
                    cancel=task.cancel)
                self.ui_q.put(("task", task.id, "完成", msg))
            except Cancelled:
                self.ui_q.put(("task", task.id, "已取消", ""))
            except PermissionError as e:
                self.ui_q.put(("task", task.id, "失败", "没有写入权限，请更改保存位置"))
                self.ui_q.put(("call", self.show_error,
                               describe_permission_error(e.filename or task.out_dir)))
            except Exception as e:
                self.ui_q.put(("task", task.id, "失败", str(e)))

    def _on_task_update(self, tid, state, detail):
        task = self.tasks.get(tid)
        if task is None:
            return
        if task.state == "已取消" and state != "已取消":
            return
        task.state = state
        if self.task_tree.exists(tid):
            self.task_tree.set(tid, "state", state)
            self.task_tree.set(tid, "detail", detail)
        if state in DONE_STATES:
            self.progress["value"] = 0
            remaining = sum(1 for t in self.tasks.values() if t.state in ACTIVE_STATES)
            self.status_var.set(f"「{task.title[:30]}」{state}" + (f"，剩余 {remaining} 个任务" if remaining else ""))
        self._update_badge()

    def _on_progress(self, tid, stage, done, total, speed):
        task = self.tasks.get(tid)
        if task is None or task.state != "下载中":
            return
        if total:
            pct = done * 100 / total
            text = f"{stage} {pct:.1f}%   {done / MB:.1f}/{total / MB:.1f} MB   {speed / MB:.2f} MB/s"
            self.progress["value"] = pct
        else:
            text = "正在合并 / 处理…" if stage == "处理中" else f"{stage} {done / MB:.1f} MB"
        if self.task_tree.exists(tid):
            self.task_tree.set(tid, "detail", text)
        self.status_var.set(f"{task.title[:30]}   ·   {text}")

    def _update_badge(self):
        n = sum(1 for t in self.tasks.values() if t.state in ACTIVE_STATES)
        self.nav["tasks"].set_text(f"下载任务（{n}）" if n else "下载任务")

    def _cancel(self, tids):
        for tid in tids:
            task = self.tasks.get(tid)
            if task and task.state in ACTIVE_STATES:
                task.cancel.set()
                if task.state == "等待中":
                    self._on_task_update(tid, "已取消", "")

    def cancel_selected(self):
        self._cancel(self.task_tree.selection())

    def cancel_all(self):
        self._cancel(list(self.tasks))

    def clear_finished(self):
        for tid, task in list(self.tasks.items()):
            if task.state in DONE_STATES:
                if self.task_tree.exists(tid):
                    self.task_tree.delete(tid)
                del self.tasks[tid]

    # ------------------------------------------------------------ 设置
    def choose_save_dir(self):
        d = filedialog.askdirectory(initialdir=self.save_dir_var.get() or None, parent=self.root)
        if d:
            d = os.path.normpath(d)
            if not is_writable(d):
                messagebox.showerror(APP_NAME, describe_permission_error(d), parent=self.root)
                return
            self.save_dir_var.set(d)
            self.save_config()

    def open_save_dir(self):
        d = self.save_dir_var.get().strip() or DEFAULT_CONFIG["save_dir"]
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
        except OSError:
            self.show_error(describe_permission_error(d))

    def choose_ffmpeg(self):
        f = filedialog.askopenfilename(filetypes=[("ffmpeg", "ffmpeg.exe"), ("可执行文件", "*.exe")], parent=self.root)
        if f:
            self.ffmpeg_var.set(os.path.normpath(f))
            self.update_ffmpeg_status()
            self.save_config()

    def update_ffmpeg_status(self):
        path = find_ffmpeg(self.ffmpeg_var.get().strip())
        self.ffmpeg_status.set(f"已找到：{path}" if path else
                               "未找到 ffmpeg：「合并音视频」将改为分别保存音频和视频文件")

    def save_config(self):
        self.cfg.update({
            "save_dir": self.save_dir_var.get().strip(),
            "mode": self.mode_var.get(),
            "quality": self.quality_var.get(),
            "theme": THEMES.get(self.theme_var.get(), "system"),
            "ffmpeg": self.ffmpeg_var.get().strip(),
        })
        try:
            os.makedirs(APP_DIR, exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def on_close(self):
        if any(t.state in ACTIVE_STATES for t in self.tasks.values()):
            if not messagebox.askyesno(APP_NAME, "仍有下载任务未完成，确定退出吗？", parent=self.root):
                return
        self.save_config()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
