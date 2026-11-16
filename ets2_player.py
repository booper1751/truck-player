# -*- coding: utf-8 -*-
"""卡車播放器 v1  —  勾選資料夾 + 播放清單 + 播放次數排行榜
需要: Python 3 (pygame、keyboard 第一次執行會自動安裝)
全域快捷鍵: F9 上一首 / F10 播放暫停 / F11 下一首
資料: 同資料夾的 player_config.json (勾選的資料夾) 與 player_stats.db (播放次數)
不會修改、搬動你的任何音樂檔。"""
import hashlib, importlib, json, os, sqlite3, subprocess, sys, time, tkinter as tk
from tkinter import filedialog, messagebox, ttk


LAST_ERR = ""


def _pip(pkg):
    global LAST_ERR
    for extra in ([], ["--user"]):
        r = subprocess.run([sys.executable, "-m", "pip", "install", pkg, *extra],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return True
        LAST_ERR = f"[{pkg}] " + (r.stdout + r.stderr).strip()[-600:]
    return False


def ensure_deps():
    """第一次執行時自動安裝缺少的套件 (pygame 必要, keyboard 可選)。"""
    importlib.invalidate_caches()
    if importlib.util.find_spec("pygame") is None:
        print("正在下載安裝 pygame ...")
        if not _pip("pygame"):
            print("pygame 安裝失敗，改試 pygame-ce ...")
            _pip("pygame-ce")
    if importlib.util.find_spec("PIL") is None:
        print("正在下載安裝 pillow (桌布/星空背景用) ...")
        _pip("pillow")
    if importlib.util.find_spec("keyboard") is None:
        print("正在下載安裝 keyboard ...")
        _pip("keyboard")
    importlib.invalidate_caches()
    print("Python 版本:", sys.version.split()[0])


import importlib.util
ensure_deps()
try:
    import pygame
except ImportError:
    pygame = None
try:
    from PIL import Image, ImageEnhance, ImageTk, ImageDraw, ImageFilter
except ImportError:
    Image = None

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "player_config.json")
DB = os.path.join(HERE, "player_stats.db")
EXTS = (".mp3", ".flac", ".wav", ".ogg", ".m4a")
COUNT_AFTER_S = 30      # 聽超過幾秒才算一次
SKIP_BEFORE_S = 10      # 少於幾秒就切歌算跳過


# ---------------- 資料庫 ----------------
class Stats:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS songs(
            fid TEXT PRIMARY KEY, path TEXT, plays INTEGER DEFAULT 0,
            skips INTEGER DEFAULT 0, last_played REAL DEFAULT 0)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS play_log(fid TEXT, ts REAL)""")
        self.db.commit()

    @staticmethod
    def fid(path):
        """用 檔案大小 + 開頭 64KB 的 MD5 當身分，檔案搬家/改名後次數仍在。"""
        try:
            with open(path, "rb") as f:
                head = f.read(65536)
            return hashlib.md5(head + str(os.path.getsize(path)).encode()).hexdigest()
        except OSError:
            return hashlib.md5(path.encode("utf-8", "ignore")).hexdigest()

    def touch(self, fid, path):
        self.db.execute("INSERT OR IGNORE INTO songs(fid,path) VALUES(?,?)", (fid, path))
        self.db.execute("UPDATE songs SET path=? WHERE fid=?", (path, fid))

    def add_play(self, fid):
        now = time.time()
        self.db.execute("UPDATE songs SET plays=plays+1,last_played=? WHERE fid=?", (now, fid))
        self.db.execute("INSERT INTO play_log VALUES(?,?)", (fid, now))
        self.db.commit()

    def add_skip(self, fid):
        self.db.execute("UPDATE songs SET skips=skips+1 WHERE fid=?", (fid,))
        self.db.commit()

    def info(self, fid):
        r = self.db.execute("SELECT plays,skips,last_played FROM songs WHERE fid=?", (fid,)).fetchone()
        return r or (0, 0, 0)

    def recent_count(self, fid, days=30):
        since = time.time() - days * 86400
        return self.db.execute("SELECT COUNT(*) FROM play_log WHERE fid=? AND ts>=?", (fid, since)).fetchone()[0]


def load_cfg():
    try:
        with open(CFG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"folders": {}, "volume": 70, "sort": "常聽榜"}


def save_cfg(c):
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=1)


def scan(folder):
    out = []
    for root, _, files in os.walk(folder):
        for n in files:
            if n.lower().endswith(EXTS):
                out.append(os.path.join(root, n))
    return out


# ---------------- 介面 ----------------
THEMES = {
    "Discord 深色": dict(bg="#313338", panel="#2b2d31", fg="#dbdee1", sub="#949ba4", accent="#5865f2", sel="#404249"),
    "純黑":        dict(bg="#101010", panel="#000000", fg="#e6e6e6", sub="#8a8a8a", accent="#e5a00d", sel="#2a2a2a"),
    "深藍":        dict(bg="#14213d", panel="#0b132b", fg="#e5e5e5", sub="#9aa9c7", accent="#fca311", sel="#22365f"),
    "森林綠":      dict(bg="#1f2d24", panel="#16211a", fg="#e3ede6", sub="#97ad9e", accent="#57cc99", sel="#2f4638"),
    "星空黑":      dict(bg="#070a1a", panel="#0c1030", fg="#e8ecff", sub="#8f9bd0", accent="#7c5cff", sel="#1a2350", stars=True),
    "淺色":        dict(bg="#f0f0f0", panel="#e3e3e3", fg="#202020", sub="#606060", accent="#2b6cb0", sel="#cfe0f5"),
}


class App:
    SORTS = ["常聽榜", "近期熱門(30天)", "最近播放", "從沒聽過", "跳過最多", "資料夾順序"]

    def __init__(self, root):
        self.root = root
        root.title("卡車播放器")
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = min(1400, int(sw * 0.8)), min(800, int(sh * 0.8))
        root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
        root.minsize(900, 500)
        self.cfg = load_cfg()
        self.stats = Stats(DB)
        self.tracks = []          # [(fid, path)]
        self.view = []            # 目前清單順序 [(fid, path)]
        self.cur = None           # 目前播放 (fid, path)
        self.play_start = 0.0     # 累計已聽秒數
        self.last_tick = 0.0
        self.counted = False
        self.paused = False
        self.folder_vars = {}

        if pygame is None:
            messagebox.showerror("缺少 pygame", "自動安裝失敗。\nPython 版本: " + sys.version.split()[0] + "\n請把下面訊息截圖給 Claude:\n\n" + (LAST_ERR or "(無錯誤訊息)"))
            root.destroy()
            return
        pygame.mixer.init()
        pygame.mixer.music.set_volume(self.cfg.get("volume", 70) / 100)

        self._build()
        self._load_folders_ui()
        self.apply_theme()
        if self.frameless_var.get():
            self.root.after(100, self.apply_frameless)
        self.refresh_tracks()
        self._hotkeys()
        root.after(500, self._tick)
        root.protocol("WM_DELETE_WINDOW", self._quit)

    # ---- 建立介面 ----
    def _build(self):
        self.bgc = tk.Canvas(self.root, highlightthickness=0, bd=0)
        self.bgc.place(x=0, y=0, relwidth=1, relheight=1)
        self.bgc.tk.call("lower", self.bgc._w)
        self._bg_img = None
        self._bg_job = None
        self.root.bind("<Configure>", self._on_resize, add="+")
        left = ttk.Frame(self.root, padding=8, width=300)
        left.pack(side="left", fill="y", padx=(12, 6), pady=12)
        left.pack_propagate(False)   # 左側固定寬度，不被長路徑撐開
        ttk.Label(left, text="音樂資料夾 (勾選才會進清單)").pack(anchor="w")
        self.folder_box = ttk.Frame(left)
        self.folder_box.pack(fill="both", expand=True, pady=4)
        bf = ttk.Frame(left)
        bf.pack(fill="x")
        ttk.Button(bf, text="＋加入資料夾", command=self.add_folder).pack(side="left")
        ttk.Button(bf, text="移除未勾選", command=self.remove_unchecked).pack(side="left", padx=4)
        ttk.Button(left, text="重新掃描", command=self.refresh_tracks).pack(fill="x", pady=(6, 0))

        right = ttk.Frame(self.root, padding=8)
        right.pack(side="left", fill="both", expand=True, padx=(6, 12), pady=(12, 6))
        top = ttk.Frame(right)
        top.pack(fill="x")
        ttk.Label(top, text="排序:").pack(side="left")
        self.sort_var = tk.StringVar(value=self.cfg.get("sort", "常聽榜"))
        cb = ttk.Combobox(top, textvariable=self.sort_var, values=self.SORTS, state="readonly", width=14)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.apply_sort())
        self.search_var = tk.StringVar()
        ttk.Label(top, text="搜尋:").pack(side="left", padx=(12, 0))
        se = ttk.Entry(top, textvariable=self.search_var, width=22)
        se.pack(side="left", padx=4)
        self.search_var.trace_add("write", lambda *a: self.apply_sort())
        self.theme_var = tk.StringVar(value=self.cfg.get("theme", "Discord 深色"))
        tc = ttk.Combobox(top, textvariable=self.theme_var, values=list(THEMES), state="readonly", width=12)
        tc.pack(side="right")
        ttk.Label(top, text="背景:").pack(side="right", padx=(8, 2))
        tc.bind("<<ComboboxSelected>>", lambda e: self.apply_theme())
        ttk.Button(top, text="清除桌布", command=self.clear_wallpaper).pack(side="right", padx=(0, 4))
        ttk.Button(top, text="選擇桌布…", command=self.pick_wallpaper).pack(side="right", padx=4)
        self.frameless_var = tk.BooleanVar(value=self.cfg.get("frameless", False))
        ttk.Checkbutton(top, text="無邊框視窗", variable=self.frameless_var,
                        command=self.apply_frameless).pack(side="right", padx=8)

        cols = ("rank", "title", "plays", "skips")
        self.tree = ttk.Treeview(right, columns=cols, show="headings", selectmode="browse")
        for c, t, w in (("rank", "#", 50), ("title", "歌名", 500), ("plays", "次數", 70), ("skips", "跳過", 70)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, minwidth=w if c != "title" else 200, stretch=(c == "title"),
                             anchor="w" if c == "title" else "center")
        sb = ttk.Scrollbar(right, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True, pady=6)
        sb.pack(side="left", fill="y", pady=6)
        self.tree.bind("<Double-1>", lambda e: self.play_selected())
        self.tree.bind("<Return>", lambda e: self.play_selected())

        bar = ttk.Frame(self.root, padding=8)
        bar.pack(side="bottom", fill="x", padx=12, pady=(0, 12))
        # 音量條: 放在歌名上方，整條可拖曳 (滑鼠滾輪也可調)
        self.vol = tk.Scale(bar, from_=0, to=100, orient="horizontal", width=18, label="音量 %",
                            showvalue=True, command=self._set_vol, bd=0, highlightthickness=0)
        self.vol.set(self.cfg.get("volume", 70))
        self.vol.pack(fill="x", pady=(0, 4))
        self.vol.bind("<MouseWheel>", lambda e: self.vol.set(self.vol.get() + (5 if e.delta > 0 else -5)))
        self.now_var = tk.StringVar(value="(未播放)")
        ttk.Label(bar, textvariable=self.now_var, font=("", 11, "bold")).pack(anchor="w")
        ctl = ttk.Frame(bar)
        ctl.pack(fill="x", pady=4)
        ttk.Button(ctl, text="◀◀", width=5, command=self.prev).pack(side="left")
        self.pp_btn = ttk.Button(ctl, text="▶/❚❚", width=7, command=self.toggle)
        self.pp_btn.pack(side="left", padx=4)
        ttk.Button(ctl, text="▶▶", width=5, command=self.next).pack(side="left")
        self.shuffle_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ctl, text="隨機(常聽的較常出現)", variable=self.shuffle_var).pack(side="left", padx=12)
        self.time_var = tk.StringVar(value="0:00")
        ttk.Label(bar, textvariable=self.time_var).pack(anchor="w")

        # 無邊框模式用的自製標題列 (預設不顯示)
        self.titlebar = tk.Frame(self.root, height=30)
        self.title_lbl = tk.Label(self.titlebar, text="  卡車播放器", anchor="w")
        self.title_lbl.pack(side="left", fill="x", expand=True)
        self.close_btn = tk.Label(self.titlebar, text="  ✕  ", cursor="hand2")
        self.close_btn.pack(side="right")
        self.min_btn = tk.Label(self.titlebar, text="  ─  ", cursor="hand2")
        self.min_btn.pack(side="right")
        self.close_btn.bind("<Button-1>", lambda e: self._quit())
        self.min_btn.bind("<Button-1>", lambda e: self._minimize())
        for w in (self.titlebar, self.title_lbl):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
        self.grip = tk.Label(self.root, text="◢", cursor="size_nw_se")
        self.grip.bind("<ButtonPress-1>", self._rs_start)
        self.grip.bind("<B1-Motion>", self._rs_move)

    # ---- 主題 ----
    def apply_theme(self):
        name = self.theme_var.get()
        th = THEMES.get(name, THEMES["Discord 深色"])
        self.th = th
        self.cfg["theme"] = name
        card, inner = th["panel"], th["bg"]      # 卡片底色 / 清單與輸入框底色
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        self.root.configure(bg=inner)
        st.configure(".", background=card, foreground=th["fg"], fieldbackground=inner,
                     bordercolor=card, lightcolor=card, darkcolor=card)
        st.configure("TFrame", background=card)
        st.configure("TLabel", background=card, foreground=th["fg"])
        st.configure("TCheckbutton", background=card, foreground=th["fg"])
        st.map("TCheckbutton", background=[("active", card)])
        st.configure("TButton", background=th["sel"], foreground=th["fg"], borderwidth=0, padding=5)
        st.map("TButton", background=[("active", th["accent"])], foreground=[("active", "#ffffff")])
        st.configure("TEntry", fieldbackground=inner, foreground=th["fg"], insertcolor=th["fg"])
        st.configure("TCombobox", fieldbackground=inner, background=th["sel"], foreground=th["fg"],
                     arrowcolor=th["fg"])
        st.map("TCombobox", fieldbackground=[("readonly", inner)], foreground=[("readonly", th["fg"])])
        self.root.option_add("*TCombobox*Listbox.background", inner)
        self.root.option_add("*TCombobox*Listbox.foreground", th["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", th["accent"])
        st.configure("Treeview", background=inner, foreground=th["fg"], fieldbackground=inner,
                     rowheight=26, borderwidth=0)
        st.map("Treeview", background=[("selected", th["accent"])], foreground=[("selected", "#ffffff")])
        st.configure("Treeview.Heading", background=th["sel"], foreground=th["fg"], relief="flat")
        st.configure("Vertical.TScrollbar", background=th["sel"], troughcolor=inner,
                     arrowcolor=th["fg"], bordercolor=inner)
        self.vol.configure(bg=card, fg=th["fg"], troughcolor=inner, activebackground=th["accent"],
                           highlightbackground=card)
        self.titlebar.configure(bg=card)
        for w in (self.title_lbl, self.close_btn, self.min_btn):
            w.configure(bg=card, fg=th["fg"])
        self.grip.configure(bg=card, fg=th["sub"])
        self._recolor_folders()
        self.render_bg()
        save_cfg(self.cfg)

    # ---- 背景圖 (桌布 / 星空黑) ----
    def pick_wallpaper(self):
        if Image is None:
            messagebox.showinfo("需要 pillow", "桌布功能需要 pillow，請重新啟動程式讓它自動安裝，或手動執行:\npip install pillow")
            return
        f = filedialog.askopenfilename(title="選擇桌布圖片",
                                       filetypes=[("圖片", "*.jpg *.jpeg *.png *.bmp *.webp"), ("全部", "*.*")])
        if f:
            self.cfg["wallpaper"] = f
            save_cfg(self.cfg)
            self.render_bg()

    def clear_wallpaper(self):
        self.cfg["wallpaper"] = ""
        save_cfg(self.cfg)
        self.render_bg()

    def _on_resize(self, e):
        if e.widget is self.root:
            if self._bg_job:
                self.root.after_cancel(self._bg_job)
            self._bg_job = self.root.after(200, self.render_bg)

    @staticmethod
    def _starfield(w, h):
        import random
        rnd = random.Random(7)
        img = Image.new("RGB", (w, h), (5, 7, 20))
        d = ImageDraw.Draw(img)
        for _ in range(int(w * h / 9000)):           # 遠處小星
            x, y = rnd.randrange(w), rnd.randrange(h)
            c = rnd.randrange(90, 200)
            d.point((x, y), fill=(c, c, min(255, c + 40)))
        glow = Image.new("RGB", (w, h), (0, 0, 0))
        g = ImageDraw.Draw(glow)
        for _ in range(int(w * h / 60000)):          # 亮星 (會發光)
            x, y = rnd.randrange(w), rnd.randrange(h)
            r = rnd.choice((1, 1, 2))
            g.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255))
        glow = glow.filter(ImageFilter.GaussianBlur(2))
        from PIL import ImageChops
        img = ImageChops.add(img, glow)
        return img

    def render_bg(self):
        self._bg_job = None
        w, h = self.root.winfo_width(), self.root.winfo_height()
        if Image is None or w < 50 or h < 50:
            self.bgc.configure(bg=self.th["bg"])
            self.bgc.delete("all")
            return
        wp = self.cfg.get("wallpaper", "")
        img = None
        if wp and os.path.exists(wp):
            try:
                if getattr(self, "_wp_path", None) != wp:
                    o = Image.open(wp).convert("RGB")
                    o.thumbnail((2560, 1440))
                    self._wp_src, self._wp_path = o, wp
                src = self._wp_src
                s = max(w / src.width, h / src.height)
                im = src.resize((int(src.width * s) + 1, int(src.height * s) + 1))
                l, tp = (im.width - w) // 2, (im.height - h) // 2
                img = ImageEnhance.Brightness(im.crop((l, tp, l + w, tp + h))).enhance(0.55)
            except Exception as ex:
                self.now_var.set(f"桌布載入失敗: {ex}")
        if img is None and self.th.get("stars"):
            img = self._starfield(w, h)
        self.bgc.delete("all")
        if img is None:
            self.bgc.configure(bg=self.th["bg"])
            return
        self._bg_img = ImageTk.PhotoImage(img)
        self.bgc.create_image(0, 0, image=self._bg_img, anchor="nw")

    def _recolor_folders(self):
        th = getattr(self, "th", THEMES["Discord 深色"])
        for w in self.folder_box.winfo_children():
            w.configure(bg=th["panel"], fg=th["fg"], selectcolor=th["bg"], activebackground=th["panel"],
                        activeforeground=th["fg"])

    # ---- 無邊框視窗 (像 Discord) ----
    def apply_frameless(self):
        on = self.frameless_var.get()
        self.cfg["frameless"] = on
        save_cfg(self.cfg)
        self.root.update_idletasks()
        geo = self.root.geometry()
        if on:
            self.root.overrideredirect(True)
            self.titlebar.pack(side="top", fill="x", before=self.root.winfo_children()[0])
            self.grip.place(relx=1.0, rely=1.0, anchor="se")
        else:
            self.root.overrideredirect(False)
            self.titlebar.pack_forget()
            self.grip.place_forget()
        self.root.geometry(geo)

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _rs_start(self, e):
        self._rx, self._ry = e.x_root, e.y_root
        self._rw, self._rh = self.root.winfo_width(), self.root.winfo_height()

    def _rs_move(self, e):
        w = max(900, self._rw + e.x_root - self._rx)
        h = max(500, self._rh + e.y_root - self._ry)
        self.root.geometry(f"{w}x{h}")

    def _minimize(self):
        # 無邊框視窗無法直接最小化，暫時還原邊框再縮小，還原時再隱藏邊框
        self.root.overrideredirect(False)
        self.root.iconify()
        self.root.bind("<Map>", self._restore_frameless)

    def _restore_frameless(self, e=None):
        if e is not None and e.widget is not self.root:
            return
        self.root.unbind("<Map>")
        if self.frameless_var.get():
            self.root.after(50, lambda: self.root.overrideredirect(True))

    # ---- 資料夾 ----
    def _load_folders_ui(self):
        for w in self.folder_box.winfo_children():
            w.destroy()
        self.folder_vars = {}
        for f, on in self.cfg["folders"].items():
            v = tk.BooleanVar(value=on)
            self.folder_vars[f] = v
            tk.Checkbutton(self.folder_box, text=f, variable=v, command=self._folders_changed,
                           wraplength=270, justify="left", anchor="w").pack(anchor="w", fill="x")
        self._recolor_folders()

    def add_folder(self):
        d = filedialog.askdirectory(title="選擇音樂資料夾")
        if d:
            d = os.path.normpath(d)
            self.cfg["folders"][d] = True
            save_cfg(self.cfg)
            self._load_folders_ui()
            self.refresh_tracks()

    def remove_unchecked(self):
        for f, v in list(self.folder_vars.items()):
            if not v.get():
                self.cfg["folders"].pop(f, None)
        save_cfg(self.cfg)
        self._load_folders_ui()

    def _folders_changed(self):
        for f, v in self.folder_vars.items():
            self.cfg["folders"][f] = v.get()
        save_cfg(self.cfg)
        self.refresh_tracks()

    # ---- 清單 ----
    def refresh_tracks(self):
        self.tracks = []
        for f, on in self.cfg["folders"].items():
            if on and os.path.isdir(f):
                for p in sorted(scan(f)):
                    fid = self.stats.fid(p)
                    self.stats.touch(fid, p)
                    self.tracks.append((fid, p))
        self.stats.db.commit()
        self.apply_sort()

    def apply_sort(self):
        mode = self.sort_var.get()
        self.cfg["sort"] = mode
        q = self.search_var.get().strip().lower()
        items = [t for t in self.tracks if q in os.path.basename(t[1]).lower()]
        info = {fid: self.stats.info(fid) for fid, _ in items}
        if mode == "常聽榜":
            items.sort(key=lambda t: -info[t[0]][0])
        elif mode == "近期熱門(30天)":
            items.sort(key=lambda t: -self.stats.recent_count(t[0]))
        elif mode == "最近播放":
            items.sort(key=lambda t: -info[t[0]][2])
        elif mode == "從沒聽過":
            items = [t for t in items if info[t[0]][0] == 0]
        elif mode == "跳過最多":
            items.sort(key=lambda t: -info[t[0]][1])
        self.view = items
        self.tree.delete(*self.tree.get_children())
        for i, (fid, p) in enumerate(items, 1):
            plays, skips, _ = info[fid]
            self.tree.insert("", "end", iid=str(i - 1),
                             values=(i, os.path.splitext(os.path.basename(p))[0], plays, skips))

    # ---- 播放 ----
    def play_selected(self):
        sel = self.tree.selection()
        if sel:
            self.play_index(int(sel[0]))

    def play_index(self, idx):
        if not (0 <= idx < len(self.view)):
            return
        self._finish_current(skipped=True)
        fid, path = self.view[idx]
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
        except Exception as e:
            self.now_var.set(f"無法播放: {os.path.basename(path)} ({e})")
            return
        self.cur, self.play_start, self.last_tick = (fid, path), 0.0, time.time()
        self.counted, self.paused = False, False
        self.now_var.set("正在播放: " + os.path.splitext(os.path.basename(path))[0])
        self.tree.selection_set(str(idx))
        self.tree.see(str(idx))

    def _finish_current(self, skipped):
        if self.cur and not self.counted and skipped and self.play_start < SKIP_BEFORE_S:
            self.stats.add_skip(self.cur[0])

    def _cur_index(self):
        for i, (fid, _) in enumerate(self.view):
            if self.cur and fid == self.cur[0]:
                return i
        return -1

    def next(self):
        if not self.view:
            return
        if self.shuffle_var.get():
            import random
            w = [1 + self.stats.info(fid)[0] for fid, _ in self.view]
            self.play_index(random.choices(range(len(self.view)), weights=w)[0])
        else:
            self.play_index((self._cur_index() + 1) % len(self.view))

    def prev(self):
        if self.view:
            self.play_index((self._cur_index() - 1) % len(self.view))

    def toggle(self):
        if not self.cur:
            self.play_selected() if self.tree.selection() else self.play_index(0)
            return
        if self.paused:
            pygame.mixer.music.unpause()
            self.last_tick = time.time()
        else:
            pygame.mixer.music.pause()
        self.paused = not self.paused

    def _set_vol(self, v):
        v = float(v)
        pygame.mixer.music.set_volume(v / 100)
        self.cfg["volume"] = int(v)

    def _tick(self):
        now = time.time()
        if self.cur and not self.paused:
            self.play_start += now - self.last_tick
            if not self.counted and self.play_start >= COUNT_AFTER_S:
                self.counted = True
                self.stats.add_play(self.cur[0])
            if not pygame.mixer.music.get_busy():
                self.next()
        self.last_tick = now
        if self.cur:
            s = int(self.play_start)
            self.time_var.set(f"{s // 60}:{s % 60:02d}  (聽滿 {COUNT_AFTER_S} 秒計一次)")
        self.root.after(500, self._tick)

    # ---- 快捷鍵 ----
    def _hotkeys(self):
        try:
            import keyboard
            keyboard.add_hotkey("f9", lambda: self.root.after(0, self.prev))
            keyboard.add_hotkey("f10", lambda: self.root.after(0, self.toggle))
            keyboard.add_hotkey("f11", lambda: self.root.after(0, self.next))
        except Exception:
            pass   # 沒裝 keyboard 或權限不足就略過

    def _quit(self):
        save_cfg(self.cfg)
        try:
            pygame.mixer.quit()
        except Exception:
            pass
        self.root.destroy()


if __name__ == "__main__":
    r = tk.Tk()
    App(r)
    r.mainloop()
