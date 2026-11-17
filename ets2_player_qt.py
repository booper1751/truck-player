# -*- coding: utf-8 -*-
"""卡車播放器 (Qt 版)  —  勾選資料夾 + 播放清單 + 播放次數排行榜 + 桌布背景(面板半透明)
第一次執行會自動安裝: PySide6 (介面)、pygame (播放)、keyboard (全域快捷鍵，可選)
全域快捷鍵: F9 上一首 / F10 播放暫停 / F11 下一首
資料: 同資料夾的 player_config.json 與 player_stats.db (與舊 Tk 版共用，次數會保留)
不會修改、搬動你的任何音樂檔。"""
import ast, ctypes, datetime, hashlib, importlib, importlib.util, io, json, os, random, re, shutil, sqlite3, subprocess, sys, threading, time, urllib.parse, urllib.request, zipfile

VERSION = 15     # 版本號 (整數)。每次發新版請把這個數字加 1，自動更新就是靠它判斷有沒有新版
UPDATE_DEFAULT_FILE = "ets2_player_qt.py"     # 只填「帳號/倉庫」時，預設去倉庫抓的檔名
UPDATE_SRC_DEFAULT = ""     # 這裡請維持空白！帳號與網址放在程式旁邊的 update_source.json (不要上傳到 GitHub、不要給別人)，程式碼裡就看不到你的帳號
UPDATE_CHECK_MIN = 30       # 程式開著時，每隔幾分鐘再檢查一次有沒有新版

FROZEN = bool(getattr(sys, "frozen", False))      # True = 打包成 EXE 後執行
APP_NAME = "卡車播放器"
EXE_NAME = "TruckPlayer.exe"
INSTALL_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "TruckPlayer")
GITHUB_API = os.environ.get("TRUCK_GITHUB_API", "https://api.github.com")
ASSET_NAME_HINT = ".exe"

LAST_ERR = ""


def _pip(pkg):
    global LAST_ERR
    for extra in ([], ["--user"]):
        r = subprocess.run([sys.executable, "-m", "pip", "install", pkg, *extra], capture_output=True, text=True)
        if r.returncode == 0:
            return True
        LAST_ERR = f"[{pkg}] " + (r.stdout + r.stderr).strip()[-600:]
    return False


def ensure_deps():
    importlib.invalidate_caches()
    if importlib.util.find_spec("PySide6") is None:
        print("正在下載安裝 PySide6 (介面，約 100MB，請稍候) ...")
        _pip("PySide6-Essentials") or _pip("PySide6")
    if importlib.util.find_spec("pygame") is None:
        print("正在下載安裝 pygame ...")
        _pip("pygame") or _pip("pygame-ce")
    if importlib.util.find_spec("mutagen") is None:
        print("正在下載安裝 mutagen (讀取歌曲長度，進度條與隱藏清單比對要用) ...")
        _pip("mutagen")
    if importlib.util.find_spec("keyboard") is None:
        print("正在下載安裝 keyboard ...")
        _pip("keyboard")
    if importlib.util.find_spec("edge_tts") is None:
        print("正在下載安裝 edge-tts (線上女聲，可選) ...")
        _pip("edge-tts")
    importlib.invalidate_caches()
    print("Python 版本:", sys.version.split()[0])


if not FROZEN:
    ensure_deps()      # EXE 版已經把套件都包好了，不需要再安裝
try:
    from PySide6.QtCore import QEasingCurve, QObject, QPoint, QPointF, QRect, QSize, Qt, QTimer, QVariantAnimation, Signal
    from PySide6.QtGui import QAction, QColor, QFont, QIcon, QImageReader, QIntValidator, QImage, QPainter, QPen, QPixmap, QPolygonF, QRadialGradient, QLinearGradient, QBrush
    from PySide6.QtWidgets import (QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
                                   QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSizeGrip, QSlider,
                                   QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QHeaderView, QMessageBox, QStyle, QProgressBar, QGraphicsDropShadowEffect, QMenu, QSystemTrayIcon)
except ImportError as e:
    print("PySide6 安裝失敗:", e, "\n", LAST_ERR)
    input("按 Enter 結束")
    sys.exit(1)
try:
    import pygame
except ImportError:
    pygame = None

SCRIPT = os.path.abspath(sys.executable if FROZEN else __file__)     # EXE 版: 這個 EXE 本身
HERE = os.path.dirname(SCRIPT)
CFG = os.path.join(HERE, "player_config.json")
DB = os.path.join(HERE, "player_stats.db")
EXTS = (".mp3", ".flac", ".wav", ".ogg", ".m4a")
COUNT_AFTER_S = 30
SKIP_BEFORE_S = 10
COLD_SORT = "冷門榜(次數少→多)"
WP_MODES = ["每首歌換一張", "每 30 分鐘換一張", "每 1 小時換一張", "不換 (維持目前這張)"]
WP_INTERVAL = {"每 30 分鐘換一張": 1800, "每 1 小時換一張": 3600}
SORTS = ["常聽榜", "近期熱門(30天)", "最近播放", "從沒聽過", "跳過最多", COLD_SORT, "資料夾順序"]

# tint = 面板底色(RGB)；text = 文字色；accent = 強調色；base = 沒有桌布時的背景色
THEMES = {
    "Discord 深色": dict(tint=(30, 31, 34), text="#f2f3f5", sub="#b5bac1", accent="#5865f2", base=(49, 51, 56)),
    "純黑":        dict(tint=(0, 0, 0), text="#f0f0f0", sub="#a0a0a0", accent="#e5a00d", base=(16, 16, 16)),
    "深藍":        dict(tint=(8, 16, 40), text="#e8eefc", sub="#9db0d8", accent="#fca311", base=(20, 33, 61)),
    "森林綠":      dict(tint=(12, 30, 20), text="#e6f2ea", sub="#a3bdaa", accent="#57cc99", base=(31, 45, 36)),
    "星空黑":      dict(tint=(6, 8, 28), text="#eef0ff", sub="#9aa5d8", accent="#8b6cff", base=(7, 10, 26), stars=True),
    "淺色":        dict(tint=(245, 245, 247), text="#1c1c1e", sub="#555560", accent="#2b6cb0", base=(230, 230, 235)),
}


# ---------------- 圖示 (播放鍵) ----------------
def draw_icon_pixmap(size):
    """程式自己畫的圖示: 圓角深色底 + 紫色漸層圓 + 白色播放鍵。視窗、工作列、系統匣、EXE 檔案圖示都用它。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = float(size)
    p.setPen(Qt.NoPen)
    bg = QLinearGradient(0, 0, 0, s)
    bg.setColorAt(0, QColor(54, 56, 66))
    bg.setColorAt(1, QColor(24, 25, 30))
    p.setBrush(QBrush(bg))
    p.drawRoundedRect(QRect(0, 0, size, size), s * 0.22, s * 0.22)
    g = QLinearGradient(s * 0.2, s * 0.15, s * 0.8, s * 0.85)
    g.setColorAt(0, QColor(139, 108, 255))
    g.setColorAt(1, QColor(88, 101, 242))
    p.setBrush(QBrush(g))
    m = s * 0.16
    p.drawEllipse(QRect(int(m), int(m), int(s - 2 * m), int(s - 2 * m)))
    p.setBrush(QColor(255, 255, 255))
    cx, cy, h = s * 0.5, s * 0.5, s * 0.17
    p.drawPolygon(QPolygonF([QPointF(cx - h * 0.62, cy - h), QPointF(cx - h * 0.62, cy + h), QPointF(cx + h * 1.1, cy)]))
    p.end()
    return pm


def app_icon():
    ic = QIcon()
    for n in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(draw_icon_pixmap(n))
    return ic


# ---------------- EXE 自我安裝 (資料夾 + 桌面捷徑) ----------------
def _ps_quote(t):
    return "'" + str(t).replace("'", "''") + "'"


def shortcut_script(target, workdir, name=APP_NAME):
    """產生 PowerShell 指令: 在桌面與開始功能表各建一個捷徑 (桌面位置被 OneDrive 搬走也能抓對)。"""
    return (
        "$ErrorActionPreference='Stop';"
        "$w=New-Object -ComObject WScript.Shell;"
        f"foreach($d in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))){{"
        f"if(-not $d){{continue}};"
        f"$s=$w.CreateShortcut((Join-Path $d ({_ps_quote(name)}+'.lnk')));"
        f"$s.TargetPath={_ps_quote(target)};$s.WorkingDirectory={_ps_quote(workdir)};"
        f"$s.IconLocation=({_ps_quote(target)}+',0');$s.Description={_ps_quote(name)};$s.Save()}}"
    )


def make_shortcuts(target, workdir):
    import base64
    enc = base64.b64encode(shortcut_script(target, workdir).encode("utf-16-le")).decode()
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
                       capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "PowerShell 失敗").strip()[-300:])


def spawn_clean(exe, args=()):
    """啟動另一個 EXE。PyInstaller 單檔版要清掉環境標記，新程式才會自己重新解壓縮，不會誤用舊的暫存資料夾。"""
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for k in [k for k in env if k.startswith("_PYI") or k == "_MEIPASS2"]:
        env.pop(k, None)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([exe, *args], env=env, cwd=os.path.dirname(exe), close_fds=True, creationflags=flags)


def swap_file(new_path, target):
    """把 new_path 換成 target。Windows 不能覆蓋執行中的 EXE，但可以改名: 舊的先改成 .old，新的放到原名。失敗會還原。"""
    old = target + ".old"
    had = os.path.exists(target)
    if os.path.exists(old):
        try:
            os.remove(old)
        except OSError:
            os.replace(old, old + str(int(time.time())))
    if had:
        os.replace(target, old)
    try:
        os.replace(new_path, target)
    except Exception:
        if had:
            os.replace(old, target)
        raise


def cleanup_old():
    """啟動時順手刪掉上一次更新留下的 .old。"""
    if not FROZEN:
        return
    try:
        for n in os.listdir(HERE):
            if n.startswith(EXE_NAME) and (n.endswith(".old") or ".old" in n or n.endswith(".new")):
                try:
                    os.remove(os.path.join(HERE, n))
                except OSError:
                    pass
    except OSError:
        pass


def _flag(d, name):
    return os.path.exists(os.path.join(d, name))


def _write_flag(d, name):
    try:
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write("1")
    except OSError:
        pass


def install_self_if_needed():
    """EXE 第一次執行時詢問: 安裝到預設資料夾 / 自己選資料夾 / 不安裝直接在原地執行。
    選過之後 (資料夾裡會有標記檔) 就不會再問。回傳 True 表示已改由新位置啟動，這個行程要結束。"""
    if not FROZEN or os.environ.get("TRUCKPLAYER_NOINSTALL"):
        return False
    if _flag(HERE, "installed.flag") or _flag(HERE, "no_install.flag"):
        return False
    if os.path.normcase(os.path.abspath(HERE)) == os.path.normcase(os.path.abspath(INSTALL_DIR)):
        return False
    box = QMessageBox(QMessageBox.Question, APP_NAME, f"要把「{APP_NAME}」安裝起來嗎？\n\n"
                      f"・預設位置: {INSTALL_DIR}\n・也可以自己選資料夾 (例如 D 槽)\n"
                      "・會在桌面與開始功能表放上捷徑，之後更新會自動更新那一份\n・不會動你的任何音樂檔")
    b_def = box.addButton("安裝到預設位置", QMessageBox.AcceptRole)
    b_pick = box.addButton("選擇其他資料夾…", QMessageBox.AcceptRole)
    b_here = box.addButton("不安裝，就在目前這個資料夾執行", QMessageBox.RejectRole)
    box.setWindowIcon(app_icon())
    box.exec()
    clicked = box.clickedButton()
    if clicked is b_here:
        _write_flag(HERE, "no_install.flag")        # 之後不再詢問；設定檔、資料都放在這個 EXE 旁邊
        return False
    dest = INSTALL_DIR
    if clicked is b_pick:
        d = QFileDialog.getExistingDirectory(None, "選擇要安裝到哪個資料夾 (會在裡面建立 TruckPlayer 資料夾)")
        if not d:
            return False                              # 取消: 這次直接執行，下次再問
        dest = os.path.join(os.path.normpath(d), "TruckPlayer")
    elif clicked is not b_def:
        return False
    target = os.path.join(dest, EXE_NAME)
    try:
        os.makedirs(dest, exist_ok=True)
        tmp = target + ".new"
        shutil.copy2(SCRIPT, tmp)
        swap_file(tmp, target)
        _write_flag(dest, "installed.flag")
        try:
            make_shortcuts(target, dest)
        except Exception as e:
            QMessageBox.warning(None, APP_NAME, f"程式已安裝，但桌面捷徑沒建成功:\n{e}\n\n可以到 {dest} 自己對 {EXE_NAME} 按右鍵建立捷徑。")
        spawn_clean(target)
        return True
    except Exception as e:
        QMessageBox.critical(None, APP_NAME, f"安裝失敗，改為直接執行:\n{e}")
        return False


# ---------------- 資料庫 ----------------
class Stats:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS songs(
            fid TEXT PRIMARY KEY, path TEXT, plays INTEGER DEFAULT 0,
            skips INTEGER DEFAULT 0, last_played REAL DEFAULT 0)""")
        self.db.execute("CREATE TABLE IF NOT EXISTS play_log(fid TEXT, ts REAL)")
        self.db.commit()

    @staticmethod
    def fid(path):
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
        return self.db.execute("SELECT plays,skips,last_played FROM songs WHERE fid=?", (fid,)).fetchone() or (0, 0, 0)

    def recent_count(self, fid, days=30):
        since = time.time() - days * 86400
        return self.db.execute("SELECT COUNT(*) FROM play_log WHERE fid=? AND ts>=?", (fid, since)).fetchone()[0]


def load_cfg():
    try:
        with open(CFG, encoding="utf-8") as f:
            c = json.load(f)
    except (OSError, ValueError):
        c = {}
    c.setdefault("folders", {})
    c.setdefault("volume", 70)
    c.setdefault("sort", "常聽榜")
    c.setdefault("theme", "Discord 深色")
    c.setdefault("panel_alpha", 45)        # 面板深度 0~100 (越小越透明，桌布越明顯)
    c.setdefault("wallpaper", "")
    c.setdefault("frameless", False)
    c.setdefault("wp_slide", False)        # 桌布幻燈片是否啟用
    c.setdefault("wp_dir", "")             # 幻燈片圖片資料夾
    c.setdefault("wp_source", "folder")    # 圖片來源: folder=整個資料夾 / picked=自己一張一張挑
    c.setdefault("wp_files", [])           # 自己挑的圖片 (依序)
    c.setdefault("wp_mode", WP_MODES[1])   # 換圖時機
    if c["wp_mode"] not in WP_MODES:
        c["wp_mode"] = WP_MODES[1]
    c.setdefault("wp_shuffle", False)      # 隨機換圖
    c.setdefault("wp_fade", True)          # 淡入淡出
    c.setdefault("dl_ack", False)          # 第一次開下載功能時，是否已按過「繼續」(進階功能警告)
    c.setdefault("dl_dir", "")             # 下載位置
    c.setdefault("dl_br", "192")
    c.setdefault("dl_seg", "8")
    c.setdefault("dl_playlist", True)
    c.setdefault("dl_aria2", True)
    c.setdefault("shuffle", False)         # 亂跳(隨機)模式是否開啟
    c.setdefault("update_src", "")         # 更新來源: GitHub 檔案網址 或 帳號/倉庫
    c.setdefault("update_auto", True)      # 啟動時自動檢查更新
    c.setdefault("update_skip", 0)         # 使用者按「稍後」略過的版本號 (不再自動提示這一版)
    c.setdefault("hidden_paths", [])       # 使用者選擇從清單隱藏的重複檔案 (不會刪檔)
    c.setdefault("drawer_open", False)     # 右側「定時/報時」抽屜是否展開
    c.setdefault("chime_hour", True)       # 整點報時
    c.setdefault("chime_half", True)       # 半點報時
    c.setdefault("chime_ampm", True)       # 報時加上「上午/下午」
    c.setdefault("weather_name", "")       # 報時時唸的地區名稱
    c.setdefault("weather_lat", None)      # 天氣地點座標
    c.setdefault("weather_lon", None)
    c.setdefault("chime_weather", True)    # 報時時一併報氣溫
    c.setdefault("chime_weather_extra", False)   # 也報天氣狀況與降雨機率
    c.setdefault("close_to_tray", False)   # 按 ✕ 時是否縮到系統匣
    c.setdefault("tray_tip_shown", False)  # 第一次縮到系統匣時的說明氣泡是否已顯示
    c.setdefault("voice", "")              # 報時女聲 (空白 = 自動)
    c.setdefault("voice_rate", 0)          # 語速 -5 (慢) ~ +5 (快)
    c.setdefault("folders_open", True)     # 左側資料夾面板是否展開
    c.setdefault("repeat", 0)              # 0=不循環 1=全部循環 2=單曲循環
    if c["repeat"] not in (0, 1, 2):
        c["repeat"] = 0
    if c["theme"] not in THEMES:
        c["theme"] = "Discord 深色"
    return c


def save_cfg(c):
    try:
        with open(CFG, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def scan(folder):
    out = []
    for root, _, files in os.walk(folder):
        for n in files:
            if n.lower().endswith(EXTS):
                out.append(os.path.join(root, n))
    return out


def norm_title(path):
    """歌名正規化: 去掉括號內容、開頭曲序、複製字樣、標點空白，用來找「名稱相似」的重複"""
    n = os.path.splitext(os.path.basename(path))[0].lower().replace("_", " ")
    n = re.sub(r"[\(\[（【\{].*?[\)\]）】\}]", "", n)
    n = re.sub(r"^\s*\d{1,3}\s*[\.\-、\s]\s*", "", n)
    n = re.sub(r"[\s\-]*(copy|複製|副本)\s*\d*$", "", n)
    return re.sub(r"[^\w]", "", n)


def build_groups(tracks):
    """把 (fid, path) 清單分成重複群組: 內容相同(fid 相同) 或 正規化歌名相同 的歸同一組"""
    parent = list(range(len(tracks)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        parent[find(a)] = find(b)

    seen_fid, seen_name = {}, {}
    for i, (fid, p) in enumerate(tracks):
        if fid in seen_fid:
            union(i, seen_fid[fid])
        seen_fid[fid] = i
        nm = norm_title(p)
        if len(nm) >= 2:
            if nm in seen_name:
                union(i, seen_name[nm])
            seen_name[nm] = i
    g = {}
    for i, t in enumerate(tracks):
        g.setdefault(find(i), []).append(t)
    return [v for v in g.values() if len(v) >= 2]


class DupDialog(QDialog):
    """找重複歌曲: 勾選的檔案會從播放清單隱藏 (只是隱藏，不會刪除或搬動任何檔案)"""
    def __init__(self, parent, tracks, hidden, stats):
        super().__init__(parent)
        self.setWindowTitle("找重複歌曲")
        self.resize(1000, 620)
        self.stats = stats
        self.groups = build_groups(tracks)
        self.size = {}
        v = QVBoxLayout(self)
        self.info = QLabel("")
        v.addWidget(self.info)
        tip = QLabel("勾選 = 從播放清單隱藏。只是隱藏，不會刪除或搬動檔案；之後隨時可以回來取消勾選。")
        tip.setObjectName("sub")
        tip.setWordWrap(True)
        v.addWidget(tip)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["檔案 (勾選=隱藏)", "大小", "次數", "位置"])
        hd = self.tree.header()
        hd.setSectionResizeMode(0, QHeaderView.Stretch)
        hd.setSectionResizeMode(1, QHeaderView.Fixed)
        hd.setSectionResizeMode(2, QHeaderView.Fixed)
        hd.setSectionResizeMode(3, QHeaderView.Stretch)
        self.tree.setColumnWidth(1, 90)
        self.tree.setColumnWidth(2, 60)
        self.tree.setRootIsDecorated(False)
        v.addWidget(self.tree, 1)
        self.items = {}      # path -> item
        for grp in self.groups:
            best = self._best(grp)
            same = len({f for f, _ in grp}) == 1
            title = os.path.splitext(os.path.basename(best[1]))[0]
            kind = "內容完全相同" if same else "名稱相似，內容不同 (可能是不同音質/版本，也可能只是同名)"
            gi = QTreeWidgetItem([f"{title}  —  {len(grp)} 個檔案，{kind}"])
            f = gi.font(0); f.setBold(True); gi.setFont(0, f)
            gi.setFlags(Qt.ItemIsEnabled)
            self.tree.addTopLevelItem(gi)
            gi.setFirstColumnSpanned(True)
            gi.setExpanded(True)
            for fid, path in sorted(grp, key=lambda t: self._rank(t)):
                mb = self._size(path) / 1048576
                it = QTreeWidgetItem([os.path.basename(path), f"{mb:.1f} MB", str(stats.info(fid)[0]), os.path.dirname(path)])
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Checked if path in hidden else Qt.Unchecked)
                it.setData(0, Qt.UserRole, path)
                it.setData(0, Qt.UserRole + 1, fid)
                it.setTextAlignment(1, Qt.AlignCenter)
                it.setTextAlignment(2, Qt.AlignCenter)
                gi.addChild(it)
                self.items[path] = it
        self.tree.itemChanged.connect(lambda *_: self._summary())

        row = QHBoxLayout()
        for txt, fn in (("勾選「內容完全相同」的多餘檔案", self.pick_same), ("每組只留一個 (勾選其餘)", self.pick_one),
                        ("全部取消勾選", self.pick_none), ("開啟檔案位置", self.open_loc)):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            row.addWidget(b)
        v.addLayout(row)
        row2 = QHBoxLayout()
        row2.addStretch(1)
        ok, no = QPushButton("套用"), QPushButton("取消")
        ok.clicked.connect(self.accept)
        no.clicked.connect(self.reject)
        row2.addWidget(ok)
        row2.addWidget(no)
        v.addLayout(row2)
        self._summary()

    def _size(self, path):
        if path not in self.size:
            try:
                self.size[path] = os.path.getsize(path)
            except OSError:
                self.size[path] = 0
        return self.size[path]

    def _rank(self, t):
        """排序/保留依據: 次數多 > 檔案大(通常音質好) > 路徑短"""
        return (-self.stats.info(t[0])[0], -self._size(t[1]), len(t[1]))

    def _best(self, grp):
        return min(grp, key=self._rank)

    def _set(self, path, on):
        self.items[path].setCheckState(0, Qt.Checked if on else Qt.Unchecked)

    def pick_same(self):
        self.tree.blockSignals(True)
        for grp in self.groups:
            by = {}
            for t in grp:
                by.setdefault(t[0], []).append(t)
            for same in by.values():
                if len(same) >= 2:
                    keep = self._best(same)
                    for t in same:
                        self._set(t[1], t is not keep)
        self.tree.blockSignals(False)
        self._summary()

    def pick_one(self):
        self.tree.blockSignals(True)
        for grp in self.groups:
            keep = self._best(grp)
            for t in grp:
                self._set(t[1], t is not keep)
        self.tree.blockSignals(False)
        self._summary()

    def pick_none(self):
        self.tree.blockSignals(True)
        for p in self.items:
            self._set(p, False)
        self.tree.blockSignals(False)
        self._summary()

    def open_loc(self):
        it = self.tree.currentItem()
        path = it.data(0, Qt.UserRole) if it else None
        if not path:
            return
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except Exception:
            pass

    def checked(self):
        return [p for p, it in self.items.items() if it.checkState(0) == Qt.Checked]

    def _summary(self):
        n = sum(len(g) for g in self.groups)
        self.info.setText(f"找到 {len(self.groups)} 組疑似重複，共 {n} 個檔案；目前勾選 {len(self.checked())} 個要隱藏。")


def move_to_trash(path):
    """移到 Windows 資源回收筒 (可從回收筒還原)。失敗回傳 False，絕不會改成永久刪除。"""
    if os.name != "nt":
        return False
    try:
        from ctypes import wintypes

        class OP(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                        ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
        op = OP()
        op.wFunc = 3                                  # FO_DELETE
        op.pFrom = os.path.normpath(path) + "\0\0"
        op.fFlags = 0x40 | 0x10 | 0x4 | 0x400         # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI
        r = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return r == 0 and not op.fAnyOperationsAborted and not os.path.exists(path)
    except Exception:
        return False


def audio_length(path):
    try:
        import mutagen
        m = mutagen.File(path)
        return float(m.info.length) if m is not None and m.info is not None else None   # 沒有標籤的檔案 m 會是空的 (為假)，所以要用 is not None
    except Exception:
        return None


def compare_hidden(hidden, visible):
    """比對每個隱藏檔案在播放清單(未隱藏)裡有沒有同一首。
    回傳 {path: (等級, 說明, 對照檔路徑)}  等級 0=內容完全相同 1=名稱同且長度相近 2=名稱同但長度不同/無法比對 3=清單裡沒有保留版本"""
    vis_fid, vis_name = {}, {}
    for fid, p in visible:
        vis_fid.setdefault(fid, p)
        nm = norm_title(p)
        if len(nm) >= 2:
            vis_name.setdefault(nm, []).append(p)
    out = {}
    for fid, p in hidden:
        if fid in vis_fid:
            m = vis_fid[fid]
            out[p] = (0, f"✅ 與「{os.path.basename(m)}」內容完全相同", m)
            continue
        cands = vis_name.get(norm_title(p), [])
        if not cands:
            out[p] = (3, "🔴 清單裡沒有保留版本 (刪了就沒有這首了)", "")
            continue
        l0 = audio_length(p)
        best = None
        for c in cands:
            l1 = audio_length(c)
            d = abs(l0 - l1) if (l0 is not None and l1 is not None) else None
            if best is None or (d is not None and (best[1] is None or d < best[1])):
                best = (c, d)
        c, d = best
        name = os.path.basename(c)
        if d is None:
            out[p] = (2, f"🟠 名稱同「{name}」，但無法比對長度", c)
        elif d <= 3:
            out[p] = (1, f"🟡 疑似與「{name}」同一首 (長度只差 {d:.1f} 秒)", c)
        else:
            out[p] = (2, f"🟠 名稱同「{name}」，但長度差 {d:.0f} 秒，可能是不同版本", c)
    return out


class HiddenDialog(QDialog):
    """隱藏清單: 檢視被隱藏的歌、比對是否和清單裡的某首是同一首、取消隱藏，或移到資源回收筒"""
    def __init__(self, pl):
        super().__init__(pl)
        self.pl = pl
        self.setWindowTitle("隱藏清單")
        self.resize(1080, 620)
        v = QVBoxLayout(self)
        self.info = QLabel("")
        v.addWidget(self.info)
        tip = QLabel("這裡可以比對隱藏的歌跟播放清單裡的是不是同一首。刪除會移到「資源回收筒」，可以從回收筒還原。")
        tip.setObjectName("sub")
        tip.setWordWrap(True)
        v.addWidget(tip)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["隱藏的檔案", "比對結果 (和播放清單中的歌比)", "大小", "次數", "位置"])
        hd = self.tree.header()
        hd.setSectionResizeMode(0, QHeaderView.Interactive)
        hd.setSectionResizeMode(1, QHeaderView.Stretch)
        hd.setSectionResizeMode(2, QHeaderView.Fixed)
        hd.setSectionResizeMode(3, QHeaderView.Fixed)
        hd.setSectionResizeMode(4, QHeaderView.Interactive)
        self.tree.setColumnWidth(0, 230)
        self.tree.setColumnWidth(4, 200)
        self.tree.setColumnWidth(2, 80)
        self.tree.setColumnWidth(3, 56)
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        v.addWidget(self.tree, 1)
        row = QHBoxLayout()
        for txt, fn in (("選取所有 ✅完全相同", self.sel_same), ("取消隱藏選取的歌", self.unhide), ("開啟檔案位置", self.open_loc)):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        self.del_btn = QPushButton("🗑 刪除選取的檔案…")
        self.del_btn.clicked.connect(self.delete_sel)
        row.addWidget(self.del_btn)
        close = QPushButton("關閉")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        v.addLayout(row)
        self._fill()

    def _fill(self):
        pl = self.pl
        hidden = set(pl.cfg["hidden_paths"])
        hid = [t for t in pl.all_tracks if t[1] in hidden]
        vis = [t for t in pl.all_tracks if t[1] not in hidden]
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = compare_hidden(hid, vis)
        finally:
            QApplication.restoreOverrideCursor()
        self.tree.clear()
        for fid, path in sorted(hid, key=lambda t: (res[t[1]][0], os.path.basename(t[1]).lower())):
            lv, txt, match = res[path]
            try:
                mb = os.path.getsize(path) / 1048576
            except OSError:
                mb = 0
            it = QTreeWidgetItem([os.path.basename(path), txt, f"{mb:.1f} MB", str(pl.stats.info(fid)[0]), os.path.dirname(path)])
            it.setData(0, Qt.UserRole, path)
            it.setData(0, Qt.UserRole + 1, lv)
            if match:
                it.setToolTip(1, "對照的檔案:\n" + match)
            it.setTextAlignment(2, Qt.AlignCenter)
            it.setTextAlignment(3, Qt.AlignCenter)
            self.tree.addTopLevelItem(it)
        n = len(hid)
        self.info.setText(f"隱藏了 {n} 首。" if n else "目前沒有隱藏的歌曲。")
        self.del_btn.setEnabled(n > 0)

    def _selected(self):
        return [(it.data(0, Qt.UserRole), it.data(0, Qt.UserRole + 1)) for it in self.tree.selectedItems()]

    def sel_same(self):
        self.tree.clearSelection()
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.data(0, Qt.UserRole + 1) == 0:
                it.setSelected(True)

    def unhide(self):
        sel = {p for p, _ in self._selected()}
        if not sel:
            return
        self.pl.cfg["hidden_paths"] = [p for p in self.pl.cfg["hidden_paths"] if p not in sel]
        save_cfg(self.pl.cfg)
        self.pl._filter_tracks()
        self._fill()

    def open_loc(self):
        it = self.tree.currentItem()
        path = it.data(0, Qt.UserRole) if it else None
        if not path:
            return
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except Exception:
            pass

    def delete_sel(self):
        sel = self._selected()
        if not sel:
            QMessageBox.information(self, "刪除", "請先在清單中選取要刪除的檔案 (可按住 Ctrl 或 Shift 多選)。")
            return
        playing = self.pl.cur[1] if self.pl.cur else None
        if playing and any(p == playing for p, _ in sel):
            QMessageBox.warning(self, "刪除", "選取的檔案裡有正在播放的歌，請先停止播放再刪除。")
            return
        risky = sum(1 for _, lv in sel if lv == 3)
        unsure = sum(1 for _, lv in sel if lv in (1, 2))
        msg = f"要把選取的 {len(sel)} 個檔案移到資源回收筒嗎？(可從回收筒還原)"
        if unsure:
            msg += f"\n\n其中 {unsure} 個只是「名稱相似」，不一定是同一首。"
        if risky:
            msg += f"\n\n⚠ 其中 {risky} 個在播放清單裡沒有保留版本，刪除後播放器裡就沒有這些歌了。"
        if QMessageBox.question(self, "確認刪除", msg, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        done, failed = set(), 0
        for path, _ in sel:
            if move_to_trash(path):
                done.add(path)
            else:
                failed += 1
        if done:
            pl = self.pl
            pl.all_tracks = [t for t in pl.all_tracks if t[1] not in done]
            pl.cfg["hidden_paths"] = [p for p in pl.cfg["hidden_paths"] if p not in done]
            save_cfg(pl.cfg)
            pl._filter_tracks()
            self._fill()
        if failed:
            QMessageBox.warning(self, "刪除", f"有 {failed} 個檔案沒能刪除 (可能正被其他程式使用，或沒有權限)，已保留原樣。")


# ---------------- 自動更新 (GitHub) ----------------
def file_update_src():
    """讀程式旁邊的 update_source.json: {"source": "帳號/倉庫"} (也可填完整檔案網址)。沒有這個檔就回傳空字串。"""
    try:
        with open(os.path.join(HERE, "update_source.json"), encoding="utf-8") as f:
            return str(json.load(f).get("source", "")).strip()
    except Exception:
        return ""


def default_src():
    return UPDATE_SRC_DEFAULT.strip() or file_update_src()


def parse_update_source(text):
    """把使用者貼的網址轉成要嘗試的 raw 網址清單。支援:
    1) https://github.com/帳號/倉庫/blob/分支/檔案.py   2) https://raw.githubusercontent.com/帳號/倉庫/分支/檔案.py
    3) https://github.com/帳號/倉庫  或  帳號/倉庫  (預設抓 main、master 分支的 UPDATE_DEFAULT_FILE)"""
    t = re.sub(r"[?#].*$", "", (text or "").strip()).rstrip("/")
    t = re.sub(r"^https?://", "", t)
    m = re.match(r"^raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)$", t)
    if m:
        return ["https://raw.githubusercontent.com/%s/%s/%s/%s" % m.groups()]
    m = re.match(r"^(?:www\.)?github\.com/([^/]+)/([^/]+)/(?:blob|raw)/([^/]+)/(.+)$", t)
    if m:
        return ["https://raw.githubusercontent.com/%s/%s/%s/%s" % m.groups()]
    m = re.match(r"^(?:(?:www\.)?github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?$", t)
    if m:
        u, r = m.groups()
        return [f"https://raw.githubusercontent.com/{u}/{r}/{b}/{UPDATE_DEFAULT_FILE}" for b in ("main", "master")]
    return []


def fetch_text(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "ets2-player-updater", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(3 * 1024 * 1024 + 1)
    if len(data) > 3 * 1024 * 1024:
        raise ValueError("檔案異常大")
    return data.decode("utf-8")


def read_version(text):
    m = re.search(r"^VERSION\s*=\s*(\d+)", text, re.M)
    return int(m.group(1)) if m else None


def parse_repo(src):
    """從任何寫法 (網址或 帳號/倉庫) 取出 (帳號, 倉庫)。"""
    t = re.sub(r"[?#].*$", "", (src or "").strip()).rstrip("/")
    t = re.sub(r"^https?://", "", t)
    m = re.match(r"^raw\.githubusercontent\.com/([^/]+)/([^/]+)/", t)
    if not m:
        m = re.match(r"^(?:www\.)?github\.com/([^/]+)/([^/]+)", t)
    if not m:
        m = re.match(r"^([\w.-]+)/([\w.-]+?)(?:\.git)?$", t)
    return (m.group(1), re.sub(r"\.git$", "", m.group(2))) if m else None


def check_release(src):
    """EXE 版: 去 GitHub Releases 查最新版 (版本號取自標籤，例如 v13 → 13)，回傳 exe 的下載網址。"""
    rp = parse_repo(src)
    if not rp:
        return {"ok": False, "error": "沒有設定更新來源。"}
    try:
        req = urllib.request.Request(f"{GITHUB_API}/repos/{rp[0]}/{rp[1]}/releases/latest",
                                     headers={"User-Agent": "truck-player-updater", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read(2 * 1024 * 1024).decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"連不上 GitHub 或還沒有發佈任何版本 ({e})"}
    m = re.search(r"(\d+)", str(data.get("tag_name", "")))
    asset = next((a for a in data.get("assets", []) if str(a.get("name", "")).lower().endswith(ASSET_NAME_HINT)), None)
    if not m or not asset:
        return {"ok": False, "error": "最新的發佈裡找不到版本號或 .exe 檔。"}
    return {"ok": True, "version": int(m.group(1)), "url": asset["browser_download_url"], "exe": True,
            "size": int(asset.get("size") or 0), "digest": str(asset.get("digest") or "")}


def check_remote(src):
    """回傳 dict: ok, version, text, url 或 error"""
    if FROZEN:
        return check_release(src)
    urls = parse_update_source(src)
    if not urls:
        return {"ok": False, "error": "看不懂這個網址。請貼 GitHub 上檔案的網址，或「帳號/倉庫」。"}
    last = ""
    for u in urls:
        try:
            text = fetch_text(u)
        except Exception as e:
            last = f"{e}"
            continue
        v = read_version(text)
        if v is None:
            return {"ok": False, "error": "抓到了檔案，但裡面找不到 VERSION = 數字，可能不是這個播放器的檔案。"}
        return {"ok": True, "version": v, "text": text, "url": u}
    return {"ok": False, "error": f"連不上或找不到檔案 ({last})。請確認倉庫是公開的、網址和檔名正確。"}


class Updater(QObject):
    result = Signal(str, object)       # (tag, 結果 dict)

    def check(self, src, tag):
        threading.Thread(target=lambda: self.result.emit(tag, check_remote(src)), daemon=True).start()


class UpdateDialog(QDialog):
    def __init__(self, pl):
        super().__init__(pl)
        self.pl = pl
        self.res = None
        self.setWindowTitle("檢查更新")
        self.resize(640, 360)
        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"目前版本：v{VERSION}"))
        v.addWidget(QLabel("更新來源 (GitHub 檔案網址，或 帳號/倉庫):"))
        self.src = QLineEdit(pl.cfg["update_src"] or default_src())
        self.src.setPlaceholderText("例如 https://github.com/帳號/倉庫/blob/main/ets2_player_qt.py")
        v.addWidget(self.src)
        hint = QLabel(f"只填「帳號/倉庫」時，會抓 main (或 master) 分支裡的 {UPDATE_DEFAULT_FILE}。倉庫需要是公開的。")
        hint.setObjectName("sub")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.auto = QCheckBox(f"自動檢查更新 (啟動時，以及每 {UPDATE_CHECK_MIN} 分鐘)")
        self.auto.setChecked(pl.cfg["update_auto"])
        v.addWidget(self.auto)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        v.addWidget(self.status, 1)
        row = QHBoxLayout()
        self.chk = QPushButton("檢查更新")
        self.chk.clicked.connect(self.check)
        row.addWidget(self.chk)
        self.go = QPushButton("下載並更新")
        self.go.clicked.connect(self.install)
        self.go.hide()
        row.addWidget(self.go)
        row.addStretch(1)
        close = QPushButton("關閉")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        v.addLayout(row)

    def _save(self):
        self.pl.cfg["update_src"] = self.src.text().strip()
        self.pl.cfg["update_auto"] = self.auto.isChecked()
        save_cfg(self.pl.cfg)

    def check(self):
        self._save()
        self.go.hide()
        self.status.setText("檢查中…")
        self.chk.setEnabled(False)
        self.pl.updater.check(self.pl.cfg["update_src"] or default_src(), "manual")

    def on_result(self, res):
        self.chk.setEnabled(True)
        self.res = res
        if not res["ok"]:
            self.status.setText("⚠ " + res["error"])
        elif res["version"] > VERSION:
            self.status.setText(f"發現新版本 v{res['version']} (目前 v{VERSION})。\n按「下載並更新」會下載、換掉目前的程式檔，然後自動重新啟動。\n" +
                                ("舊版會暫時改名保留；" if FROZEN else "舊版會備份成同名的 .bak 檔；") + "設定、播放次數都不會變。")
            self.go.setText(f"下載並更新到 v{res['version']}")
            self.go.show()
        elif res["version"] == VERSION:
            self.status.setText(f"✅ 已經是最新版 (v{VERSION})。")
        else:
            self.status.setText(f"GitHub 上的版本 (v{res['version']}) 比你現在的 (v{VERSION}) 還舊，不更新。")

    def install(self):
        if self.res and self.res.get("ok"):
            self.pl.install_update(self.res, self)


class SeekSlider(QSlider):
    """播放進度條: 點哪裡就跳到哪裡，也可拖曳；放開時發出 seeked。"""
    seeked = Signal(int)

    def __init__(self):
        super().__init__(Qt.Horizontal)
        self.setRange(0, 1000)
        self.dragging = False

    def _val_at(self, x):
        return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), int(x), max(1, self.width()))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.dragging = True
            self.setValue(self._val_at(e.position().x()))
            e.accept()

    def mouseMoveEvent(self, e):
        if self.dragging:
            self.setValue(self._val_at(e.position().x()))
            e.accept()

    def mouseReleaseEvent(self, e):
        if self.dragging:
            self.dragging = False
            self.setValue(self._val_at(e.position().x()))
            self.seeked.emit(self.value())
            e.accept()


def fmt(s):
    s = int(max(0, s))
    return f"{s // 60}:{s % 60:02d}"


def track_length(path):
    """歌曲長度(秒)。優先 mutagen (audio_length)，不行再用 pygame 載入量長度 (大檔案不嘗試)。失敗回 0"""
    n = audio_length(path)
    if n:
        return n
    try:
        if os.path.getsize(path) < 60 * 1024 * 1024:
            return float(pygame.mixer.Sound(path).get_length())
    except Exception:
        pass
    return 0.0


def fetch_stream(url, cb, timeout=20):
    """分段下載並回報進度 cb(已下載位元組, 總位元組或0)。回傳文字。"""
    req = urllib.request.Request(url, headers={"User-Agent": "ets2-player-updater", "Cache-Control": "no-cache"})
    buf = bytearray()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        total = int(r.headers.get("Content-Length") or 0)
        while True:
            chunk = r.read(8192)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 3 * 1024 * 1024:
                raise ValueError("檔案異常大")
            cb(len(buf), total)
    return bytes(buf).decode("utf-8")


class UpdateWorker(QObject):
    progress = Signal(int, str)        # (目標百分比, 目前步驟文字)
    done = Signal(bool, str)           # (成功與否, 訊息)

    def __init__(self, res):
        super().__init__()
        self.res = res

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        res = self.res
        if res.get("exe"):
            return self._run_exe(res)
        try:
            self.progress.emit(2, "正在連線到 GitHub …")

            def cb(got, total):
                pct = 5 + int(65 * got / total) if total else 40
                self.progress.emit(min(pct, 70), f"下載最新版 … {got // 1024} KB")
            text = fetch_stream(res["url"], cb)
            self.progress.emit(75, "檢查檔案是否完整 …")
            ast.parse(text)                                   # 語法檢查: 壞掉的檔案絕不換上去
            if "class Player" not in text or read_version(text) != res["version"]:
                raise ValueError("下載到的檔案內容不對")
            self.progress.emit(85, "備份目前的版本 …")
            tmp = SCRIPT + ".new"
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            shutil.copy2(SCRIPT, SCRIPT + ".bak")
            self.progress.emit(95, "安裝新版 …")
            os.replace(tmp, SCRIPT)
            self.progress.emit(100, "更新完成！")
            self.done.emit(True, "")
        except Exception as e:
            self.done.emit(False, str(e))


def _worker_run_exe(self, res):
    """EXE 版更新: 下載新的 exe → 核對 → 舊的改名成 .old → 新的放到原名。失敗會還原，目前版本不受影響。"""
    tmp = SCRIPT + ".new"
    try:
        self.progress.emit(2, "正在連線到 GitHub …")
        req = urllib.request.Request(res["url"], headers={"User-Agent": "truck-player-updater"})
        h = hashlib.sha256()
        got = 0
        with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or res.get("size") or 0)
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                pct = 5 + int(80 * got / total) if total else 40
                self.progress.emit(min(pct, 85), f"下載最新版 … {got / 1048576:.1f} MB" + (f" / {total / 1048576:.1f} MB" if total else ""))
        self.progress.emit(88, "檢查檔案是否完整 …")
        with open(tmp, "rb") as f:
            if f.read(2) != b"MZ" or got < 1_000_000:
                raise ValueError("下載到的不是正常的程式檔")
        dg = res.get("digest", "")
        if dg.startswith("sha256:") and dg[7:].lower() != h.hexdigest().lower():
            raise ValueError("檔案核對碼不符，可能下載不完整")
        self.progress.emit(94, "安裝新版 …")
        swap_file(tmp, SCRIPT)
        self.progress.emit(100, "更新完成！")
        self.done.emit(True, "")
    except Exception as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        self.done.emit(False, str(e))


UpdateWorker._run_exe = _worker_run_exe


class UpdateProgress(QDialog):
    """更新進度視窗: 圓角卡片、漸層進度條、百分比、步驟文字。"""
    def __init__(self, pl, res):
        super().__init__(pl)
        self.pl = pl
        self.res = res
        self.ok = False
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        th = pl.th
        r, g, b = th["tint"]
        acc, txt, sub = th["accent"], th["text"], th["sub"]
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        card = QFrame()
        card.setObjectName("upcard")
        outer.addWidget(card)
        sh = QGraphicsDropShadowEffect(self)
        sh.setBlurRadius(40)
        sh.setOffset(0, 8)
        sh.setColor(QColor(0, 0, 0, 170))
        card.setGraphicsEffect(sh)
        self.setStyleSheet(f"""
            QFrame#upcard {{ background-color: rgb({min(255,r+16)},{min(255,g+16)},{min(255,b+16)}); border: 1px solid rgba(255,255,255,45); border-radius: 18px; }}
            QLabel {{ background: transparent; color: {txt}; font-family: "Microsoft JhengHei UI", "Noto Sans CJK TC", sans-serif; }}
            QLabel#uptitle {{ font-size: 22px; font-weight: bold; }}
            QLabel#upver {{ font-size: 15px; color: {acc}; font-weight: bold; }}
            QLabel#upstep {{ font-size: 13px; color: {sub}; }}
            QLabel#uppct {{ font-size: 40px; font-weight: bold; color: {txt}; }}
            QLabel#upicon {{ font-size: 34px; }}
            QProgressBar {{ background-color: rgba(255,255,255,30); border: none; border-radius: 7px; height: 14px; }}
            QProgressBar::chunk {{ border-radius: 7px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {acc}, stop:1 #ffffff); }}
            QPushButton {{ background-color: rgba(255,255,255,34); border: none; border-radius: 9px; padding: 8px 22px; color: {txt}; font-size: 13px; }}
            QPushButton:hover {{ background-color: {acc}; color: #ffffff; }}
        """)
        v = QVBoxLayout(card)
        v.setContentsMargins(32, 26, 32, 26)
        v.setSpacing(10)
        top = QHBoxLayout()
        ic = QLabel("🚚")
        ic.setObjectName("upicon")
        top.addWidget(ic)
        tt = QVBoxLayout()
        tt.setSpacing(2)
        self.title = QLabel("正在更新播放器")
        self.title.setObjectName("uptitle")
        tt.addWidget(self.title)
        ver = QLabel(f"v{VERSION}   →   v{res['version']}")
        ver.setObjectName("upver")
        tt.addWidget(ver)
        top.addLayout(tt, 1)
        self.pct = QLabel("0%")
        self.pct.setObjectName("uppct")
        top.addWidget(self.pct)
        v.addLayout(top)
        v.addSpacing(8)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(14)
        v.addWidget(self.bar)
        self.step = QLabel("準備中 …")
        self.step.setObjectName("upstep")
        self.step.setWordWrap(True)
        v.addWidget(self.step)
        self.btnrow = QHBoxLayout()
        self.btnrow.addStretch(1)
        self.close_btn = QPushButton("關閉")
        self.close_btn.clicked.connect(self.reject)
        self.close_btn.hide()
        self.btnrow.addWidget(self.close_btn)
        v.addLayout(self.btnrow)
        self.setFixedSize(520, 260)

        # 進度條平滑動畫: 目標值由工作執行緒給，畫面上緩緩追上去 (下載很快時也看得清楚)
        self.target = 0
        self.shown = 0.0
        self.smooth = QTimer(self)
        self.smooth.timeout.connect(self._anim)
        self.smooth.start(16)
        self.worker = UpdateWorker(res)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._on_done)
        QTimer.singleShot(250, self.worker.start)

    def _on_progress(self, pct, text):
        self.target = max(self.target, pct)
        self.step.setText(text)

    def _anim(self):
        if self.shown < self.target:
            self.shown = min(self.target, self.shown + max(0.35, (self.target - self.shown) * 0.08))
            v = int(self.shown)
            self.bar.setValue(v)
            self.pct.setText(f"{v}%")
        if self.ok and self.shown >= 100:
            self.smooth.stop()
            self.title.setText("更新完成")
            self.step.setText("正在重新啟動播放器 …")
            QTimer.singleShot(900, self.accept)

    def _on_done(self, ok, msg):
        if ok:
            self.ok = True
            self.target = 100
        else:
            self.smooth.stop()
            self.title.setText("更新失敗")
            self.step.setText(f"目前版本不受影響。\n原因: {msg}")
            self.close_btn.show()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and not self.ok and self.close_btn.isVisible():
            self.reject()


class UpdateToast(QFrame):
    """左下角的小提示框: 有新版時浮在畫面上，不擋住操作。"""
    def __init__(self, pl):
        super().__init__(pl)
        self.pl = pl
        self.res = None
        self.setObjectName("toast")
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(8)
        self.lbl = QLabel("")
        self.lbl.setObjectName("toasttxt")
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)
        row = QHBoxLayout()
        self.yes = QPushButton("是，現在更新")
        self.no = QPushButton("稍後")
        self.yes.clicked.connect(self._yes)
        self.no.clicked.connect(self._no)
        row.addWidget(self.yes)
        row.addWidget(self.no)
        row.addStretch(1)
        v.addLayout(row)
        self.setFixedWidth(300)
        self.hide()

    def show_for(self, res):
        self.res = res
        self.lbl.setText(f"發現新版本 v{res['version']} (目前 v{VERSION})\n要更新嗎？更新後會自動重新啟動。")
        self.yes.setEnabled(True)
        self.adjustSize()
        self.place()
        self.show()
        self.raise_()

    def place(self):
        self.adjustSize()
        top = self.pl.bottom_panel.mapTo(self.pl, QPoint(0, 0)).y()      # 放在下方控制面板的上緣，不蓋住按鈕
        self.move(26, max(8, top - self.height() - 8))

    def _yes(self):
        self.yes.setEnabled(False)
        self.lbl.setText("更新中，請稍候…")
        QApplication.processEvents()
        self.pl.install_update(self.res, self.pl, quiet=True)
        if self.pl.isVisible():              # 成功的話程式已重新啟動並關閉；還在代表失敗，把提示框放回來
            self.show_for(self.res)

    def _no(self):
        self.pl.cfg["update_skip"] = self.res["version"]      # 這一版不再自動提示，仍可手動檢查
        save_cfg(self.pl.cfg)
        self.hide()


# ---------------- 下載工具 (yt-dlp / ffmpeg 自動準備) ----------------
IS_WIN = os.name == "nt"
TOOLS_DIR = os.path.join(HERE, "tools")
YTDLP_DIR = os.path.join(TOOLS_DIR, "yt_dlp_update")        # 自動下載的 yt-dlp 放這裡 (優先於系統內建的)
FFMPEG_EXE = os.path.join(TOOLS_DIR, "ffmpeg.exe")
FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
IMG_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


class DownloadStop(Exception):
    pass


def _tools_path():
    if os.path.isdir(YTDLP_DIR) and YTDLP_DIR not in sys.path:
        sys.path.insert(0, YTDLP_DIR)


def have_ytdlp():
    """真的載入一次才算「有」: 檔案在但壞掉(載入失敗)也算沒有，這樣會自動重新下載修復"""
    try:
        get_ytdlp()
        return True
    except Exception:
        return False


def get_ytdlp():
    _tools_path()
    import yt_dlp          # 一定要在函式裡 import，重新下載後才會拿到新載入的版本
    return yt_dlp


def reload_ytdlp():
    """重新下載 yt-dlp 後，不用重開程式: 把舊的模組從記憶體清掉再載入新的"""
    for k in [k for k in sys.modules if k == "yt_dlp" or k.startswith("yt_dlp.")]:
        del sys.modules[k]
    importlib.invalidate_caches()
    _tools_path()
    import yt_dlp
    return yt_dlp


def ytdlp_version():
    try:
        get_ytdlp()
        import yt_dlp.version as v
        return v.__version__
    except Exception:
        return ""


def ffmpeg_path():
    for p in (FFMPEG_EXE, os.path.join(HERE, "ffmpeg.exe")):
        if os.path.exists(p):
            return p
    return shutil.which("ffmpeg") or ""


def aria2c_path():
    p = os.path.join(TOOLS_DIR, "aria2c.exe")
    return p if os.path.exists(p) else (shutil.which("aria2c") or "")


def install_ytdlp(cb=None):
    """從 PyPI 下載最新版 yt-dlp (.whl 其實就是 zip)，核對雜湊值後解壓到 tools/yt_dlp_update。
    先解壓到暫存資料夾，成功了才換掉舊版，所以失敗時舊版不會被弄壞。回傳 (成功?, 版本或錯誤訊息)"""
    cb = cb or (lambda m: None)
    new = YTDLP_DIR + ".new"
    try:
        cb("連線 PyPI 查詢最新版本...")
        with urllib.request.urlopen("https://pypi.org/pypi/yt-dlp/json", timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        ver = data["info"]["version"]
        files = data.get("urls") or data.get("releases", {}).get(ver, [])
        whl = next((f for f in files if f.get("filename", "").endswith(".whl")), None)
        if not whl:
            return False, "在 PyPI 上找不到 yt-dlp 的安裝檔 (.whl)"
        cb(f"下載 yt-dlp {ver} 中...")
        with urllib.request.urlopen(whl["url"], timeout=90) as r:
            blob = r.read()
        want = (whl.get("digests") or {}).get("sha256")
        if want and hashlib.sha256(blob).hexdigest() != want:
            return False, "下載的檔案雜湊值對不上 (檔案可能損毀)，已取消安裝"
        cb("安裝新版中...")
        shutil.rmtree(new, ignore_errors=True)
        os.makedirs(new, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            z.extractall(new)
        shutil.rmtree(YTDLP_DIR, ignore_errors=True)
        os.replace(new, YTDLP_DIR)
        return True, ver
    except Exception as e:
        shutil.rmtree(new, ignore_errors=True)
        return False, str(e)


def install_ffmpeg(cb=None):
    """下載 Windows 版 ffmpeg (BtbN 官方靜態編譯)，只取出 ffmpeg.exe 放到 tools 資料夾"""
    cb = cb or (lambda m: None)
    if not IS_WIN:
        return False, "自動下載 ffmpeg 目前只支援 Windows，請自行安裝 ffmpeg 並加入 PATH"
    tmp = os.path.join(TOOLS_DIR, "ffmpeg_download.tmp")
    part = FFMPEG_EXE + ".part"
    try:
        os.makedirs(TOOLS_DIR, exist_ok=True)
        cb("下載 ffmpeg 中 (檔案較大，請耐心等候)...")
        req = urllib.request.Request(FFMPEG_URL, headers={"User-Agent": "ets2-player"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                cb(f"下載 ffmpeg 中... {got / 1048576:.0f}" + (f" / {total / 1048576:.0f} MB ({got * 100 // total}%)" if total else " MB"))
        cb("解壓縮 ffmpeg.exe ...")
        with zipfile.ZipFile(tmp) as z:
            name = next((n for n in z.namelist() if n.replace("\\", "/").endswith("bin/ffmpeg.exe")), None)
            if not name:
                return False, "壓縮檔裡找不到 ffmpeg.exe，來源檔案格式可能變了"
            with z.open(name) as src, open(part, "wb") as dst:
                shutil.copyfileobj(src, dst)
        os.replace(part, FFMPEG_EXE)
        return True, "ffmpeg 已安裝完成"
    except Exception as e:
        return False, str(e)
    finally:
        for p in (tmp, part):
            try:
                os.remove(p)
            except OSError:
                pass


def sanitize_folder_name(name):
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_ ")


def load_archive(history_file):
    ids = set()
    if os.path.exists(history_file):
        with open(history_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    ids.add(line.strip())
    return ids


def save_to_archive(history_file, vid):
    with open(history_file, "a", encoding="utf-8") as f:
        f.write(f"youtube {vid}\n")


def in_archive(archived, vid):
    return f"youtube {vid}" in archived


SCAN_BATCH_SIZE = 100     # 掃描播放清單時，每批掃幾首
MAX_RETRY_PER_GAP = 5     # 疑似分頁中斷時，同一個卡點最多重試幾次
MAX_STALL = 3             # 整批請求失敗時，最多連續重試幾次


def scan_playlist_all(yt, url, is_playlist, status_cb, from_index=1, stop=lambda: False):
    """掃描播放清單，回傳 (資料夾名稱, 歌曲清單)。
    YouTube 大型清單的分頁有時會中途斷掉，這裡用清單總數判斷是否真的到底，不夠就換起點重試。"""
    if not is_playlist:
        status_cb("🔍 解析單首中...", "blue")
        opts = {"quiet": True, "extract_flat": True, "noplaylist": True, "ignoreerrors": True}
        with yt.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if info is None:
            return None, None
        entries = [info] if info.get("id") else []
        status_cb(f"✅ 共掃到 {len(entries)} 首", "green")
        return "單曲下載", entries

    BATCH = SCAN_BATCH_SIZE
    all_entries, folder_name, count_hint = [], None, None
    start, retry_count, stall_count, no_progress = from_index, 0, 0, 0
    while not stop():
        end = start + BATCH - 1
        status_cb(f"🔍 掃描第 {start}~{end} 首中... 已掃 {len(all_entries)} 首"
                  + (f" / 共{count_hint}首" if count_hint else ""), "blue")
        opts = {"quiet": True, "extract_flat": "in_playlist", "noplaylist": False, "ignoreerrors": True,
                "playliststart": start, "playlistend": end,
                "extractor_args": {"youtubetab": {"skip": ["webpage"]}}}
        try:
            with yt.YoutubeDL(opts) as ydl:
                res = ydl.extract_info(url, download=False)
        except Exception:
            res = None
        if res is None:
            stall_count += 1
            if stall_count >= MAX_STALL:
                break
            status_cb(f"⚠️ 第 {start}~{end} 首抓取失敗，重試中...（第{stall_count}次）", "orange")
            continue
        if folder_name is None:
            folder_name = sanitize_folder_name(res.get("title") or "YouTube下載")
        if count_hint is None:
            count_hint = res.get("playlist_count")
        batch = [e for e in (res.get("entries") or []) if e and e.get("id")]
        before = len(all_entries)
        seen = {e["id"] for e in all_entries}
        all_entries.extend(e for e in batch if e["id"] not in seen)
        total = len(all_entries)
        if len(batch) >= BATCH:                       # 這批是滿的 → 繼續下一批
            stall_count = retry_count = no_progress = 0
            start = end + 1
            continue
        # 這批不足: 可能真的到底，也可能是分頁中斷
        suspect = (total < count_hint - 2) if count_hint is not None else True
        if not suspect:
            break
        if retry_count >= MAX_RETRY_PER_GAP:
            status_cb(f"⚠️ 已重試{MAX_RETRY_PER_GAP}次仍無法繼續，目前掃到{total}首"
                      + (f"（清單共{count_hint}首）" if count_hint else "") + "，已掃到的部分仍會繼續下載", "orange")
            break
        if total == before:
            no_progress += 1
            if no_progress >= MAX_STALL:
                break
        else:
            no_progress = 0
        retry_count += 1
        start = total + 1
        status_cb(f"🔁 偵測到清單可能中斷（已掃{total}" + (f"/{count_hint}" if count_hint else "")
                  + f"首），改從第{start}首重新嘗試...（第{retry_count}次重試）", "orange")

    seen, unique = set(), []
    for e in all_entries:
        if e["id"] not in seen:
            seen.add(e["id"])
            unique.append(e)
    status_cb(f"✅ 共掃到 {len(unique)} 首，開始下載...", "green")
    return folder_name or "YouTube下載", unique


class AltPicker(QDialog):
    """下載失敗時，從搜尋到的替代版本裡選一首"""
    def __init__(self, parent, title, cands):
        super().__init__(parent)
        self.setWindowTitle(f"選擇替代版本：{title}")
        self.resize(640, 400)
        self.cands, self.url = cands, None
        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"原曲下載失敗：{title}"))
        hint = QLabel("以下是搜尋到的替代版本，請選一首或按「跳過」：")
        hint.setObjectName("sub")
        v.addWidget(hint)
        self.lst = QListWidget()
        for i, c in enumerate(cands):
            d = int(c["dur"] or 0)
            self.lst.addItem(f"{i + 1}. {c['title']}  [{d // 60}:{d % 60:02d}]")
        v.addWidget(self.lst, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        ok, sk = QPushButton("✅ 下載這首"), QPushButton("⏭ 跳過")
        ok.clicked.connect(self._pick)
        sk.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(sk)
        v.addLayout(row)

    def _pick(self):
        r = self.lst.currentRow()
        if r < 0:
            QMessageBox.warning(self, "提示", "請先點選一首歌！")
            return
        self.url = self.cands[r]["url"]
        self.accept()


class DownloadDialog(QDialog):
    """下載音樂 (YouTube 等網站，使用 yt-dlp)。下載完的歌自動加進播放器清單。"""
    ui = Signal(object)          # 背景執行緒用: 丟一個函式回主執行緒執行

    def __init__(self, pl):
        super().__init__(pl)
        self.pl = pl
        self.setWindowTitle("下載音樂")
        self.resize(800, 760)
        self.ui.connect(lambda fn: fn())
        self.running = self.tools_busy = False
        self._stop = False
        self.paths = {}
        self.stats = {"ok": 0, "skip": 0, "fail": 0, "total": 0}
        c = pl.cfg
        v = QVBoxLayout(self)
        t = QLabel("⬇ 下載音樂")
        t.setObjectName("dtitle")
        v.addWidget(t)
        v.addWidget(QLabel("連結（單首或合輯/播放清單皆可）:"))
        r = QHBoxLayout()
        self.url = QLineEdit()
        self.url.setPlaceholderText("貼上 YouTube 等網站的連結")
        r.addWidget(self.url, 1)
        pb = QPushButton("貼上")
        pb.clicked.connect(lambda: self.url.setText(QApplication.clipboard().text().strip()))
        r.addWidget(pb)
        v.addLayout(r)

        r = QHBoxLayout()
        r.addWidget(QLabel("音質(kbps):"))
        self.br = QComboBox(); self.br.addItems(["320", "256", "192", "128"]); self.br.setCurrentText(str(c["dl_br"]))
        r.addWidget(self.br)
        r.addSpacing(10)
        r.addWidget(QLabel("多段下載:"))
        self.seg = QComboBox(); self.seg.addItems(["1", "4", "8", "16"]); self.seg.setCurrentText(str(c["dl_seg"]))
        self.seg.setToolTip("同時抓幾個片段 (對分片式串流有效)，1 = 不使用多段")
        r.addWidget(self.seg)
        r.addSpacing(10)
        self.pl_cb = QCheckBox("合輯 / 播放清單模式")
        self.pl_cb.setChecked(c["dl_playlist"])
        r.addWidget(self.pl_cb)
        r.addSpacing(10)
        r.addWidget(QLabel("從第幾首開始:"))
        self.start = NumBox(1, 1, 99999)
        self.start.setToolTip("前段已下載時填入，例如填 109 從第109首繼續")
        r.addWidget(self.start)
        r.addStretch(1)
        v.addLayout(r)

        r = QHBoxLayout()
        self.aria_cb = QCheckBox("aria2c 多線程 (單檔 16 條連線)")
        self.aria_lbl = QLabel("")
        self.aria_lbl.setObjectName("sub")
        r.addWidget(self.aria_cb)
        r.addWidget(self.aria_lbl, 1)
        v.addLayout(r)

        r = QHBoxLayout()
        r.addWidget(QLabel("存到:"))
        first = next((f for f, on in c["folders"].items() if on), "")
        self.dst = QLineEdit(c["dl_dir"] or first or os.path.join(HERE, "下載"))
        r.addWidget(self.dst, 1)
        db = QPushButton("選擇資料夾…")
        db.clicked.connect(self._pick_dst)
        r.addWidget(db)
        v.addLayout(r)

        r = QHBoxLayout()
        self.tools_lbl = QLabel("")
        r.addWidget(self.tools_lbl, 1)
        self.fix_y = QPushButton("🔧 重新下載 yt-dlp")
        self.fix_y.setToolTip("yt-dlp 壞掉或過期時按這個，只會重抓 yt-dlp，不用動其他東西")
        self.fix_y.clicked.connect(self.repair_ytdlp)
        self.fix_f = QPushButton("⬇ 重新安裝 ffmpeg")
        self.fix_f.clicked.connect(self.repair_ffmpeg)
        r.addWidget(self.fix_y)
        r.addWidget(self.fix_f)
        v.addLayout(r)
        self.tool_bar = QProgressBar()
        self.tool_bar.setRange(0, 0)
        self.tool_bar.setTextVisible(False)
        self.tool_bar.hide()
        v.addWidget(self.tool_bar)

        v.addWidget(QLabel("歌曲清單（雙擊可在資料夾中定位檔案）:"))
        self.listw = QListWidget()
        self.listw.itemDoubleClicked.connect(self._open_loc)
        v.addWidget(self.listw, 1)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        v.addWidget(self.bar)
        self.status = QLabel("準備就緒")
        v.addWidget(self.status)
        self.summary = QLabel("")
        self.summary.setObjectName("sub")
        v.addWidget(self.summary)
        tip = QLabel("每個合輯資料夾都有獨立的 download_history.txt，重複的歌會自動跳過。請只下載你有權下載的內容，並遵守網站規定。")
        tip.setObjectName("sub")
        tip.setWordWrap(True)
        v.addWidget(tip)

        r = QHBoxLayout()
        self.go = QPushButton("🚀 開始下載（自動跳過已有）")
        self.go.clicked.connect(self.start_download)
        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._do_stop)
        self.hist_btn = QPushButton("🔧 修復下載記錄")
        self.hist_btn.setToolTip("資料夾裡已經有檔案、但沒有記錄時，用原本的清單網址把記錄補齊")
        self.hist_btn.clicked.connect(self.fix_history)
        close = QPushButton("關閉")
        close.clicked.connect(self.hide)
        for b in (self.go, self.stop_btn, self.hist_btn):
            r.addWidget(b)
        r.addStretch(1)
        r.addWidget(close)
        v.addLayout(r)
        self._summary()
        self.refresh_tools()

    # ---- 小工具 ----
    def _pick_dst(self):
        d = QFileDialog.getExistingDirectory(self, "選擇下載位置", self.dst.text())
        if d:
            self.dst.setText(os.path.normpath(d))

    def _row(self, i, text):
        it = self.listw.item(i)
        if it:
            it.setText(text)
            self.listw.scrollToItem(it)

    def _summary(self):
        s = self.stats
        self.summary.setText(f"✅ 新下載: {s['ok']}  |  ⏭️ 已有跳過: {s['skip']}  |  ⚠️ 無法下載: {s['fail']}  |  共: {s['total']}")

    def _ask(self, fn):
        """背景執行緒要問使用者時: 在主執行緒執行 fn，等結果回來"""
        box, ev = {}, threading.Event()

        def run():
            try:
                box["r"] = fn()
            finally:
                ev.set()
        self.ui.emit(run)
        ev.wait()
        return box.get("r")

    def _open_loc(self, item):
        p = self.paths.get(self.listw.row(item))
        if p and os.path.exists(p):
            try:
                if IS_WIN:
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(p)])
                else:
                    subprocess.Popen(["xdg-open", os.path.dirname(p)])
            except Exception:
                pass
        else:
            QMessageBox.information(self, "提示", "找不到這首歌的實際檔案。可能還沒下載完成、下載失敗被跳過，或檔案之後被移動/刪除了。")

    def _do_stop(self):
        self._stop = True
        self.status.setText("⏹ 停止中...")

    # ---- 元件 (yt-dlp / ffmpeg) ----
    def refresh_tools(self):
        has_y = have_ytdlp()
        v = ytdlp_version() if has_y else ""
        ff = ffmpeg_path()
        self.tools_lbl.setText(f"yt-dlp: {('✅ ' + v) if has_y else '❌ 未安裝或壞掉'}　　ffmpeg: {'✅ 已就緒' if ff else '❌ 未安裝'}")
        a = aria2c_path()
        self.aria_cb.setEnabled(bool(a))
        self.aria_cb.setChecked(bool(a) and self.pl.cfg["dl_aria2"])
        self.aria_lbl.setText("✅ 已偵測到 aria2c" if a else "未偵測到 aria2c (把 aria2c.exe 放進 tools 資料夾即可啟用)")
        busy = self.running or self.tools_busy
        self.go.setEnabled(has_y and bool(ff) and not busy)
        self.fix_y.setEnabled(not busy)
        self.fix_f.setEnabled(not busy)
        self.hist_btn.setEnabled(has_y and not busy)

    def ensure_tools(self):
        """打開視窗時檢查: 缺什麼就自動下載什麼"""
        if self.tools_busy or self.running:
            return
        need_y, need_f = not have_ytdlp(), not ffmpeg_path()
        if need_y or need_f:
            self._run_tools(need_y, need_f)

    def repair_ytdlp(self):
        if self.running:
            QMessageBox.information(self, "提示", "下載進行中，請先按「停止」再重新下載 yt-dlp。")
            return
        self._run_tools(True, False)

    def repair_ffmpeg(self):
        if self.running:
            QMessageBox.information(self, "提示", "下載進行中，請先按「停止」再重新安裝 ffmpeg。")
            return
        self._run_tools(False, True)

    def _run_tools(self, do_y, do_f):
        self.tools_busy = True
        self.tool_bar.show()
        self.refresh_tools()

        def cb(m):
            self.ui.emit(lambda: self.status.setText("🔄 " + m))

        def work():
            msgs = []
            if do_y:
                ok, info = install_ytdlp(cb)
                if ok:
                    try:
                        reload_ytdlp()
                    except Exception as e:
                        msgs.append(f"yt-dlp 安裝了，但載入失敗: {e}")
                else:
                    msgs.append(f"yt-dlp: {info}")
            if do_f:
                ok, info = install_ffmpeg(cb)
                if not ok:
                    msgs.append(f"ffmpeg: {info}")
            self.ui.emit(lambda: self._tools_done(msgs))
        threading.Thread(target=work, daemon=True).start()

    def _tools_done(self, msgs):
        self.tools_busy = False
        self.tool_bar.hide()
        self.status.setText("準備就緒" if not msgs else "⚠️ 元件安裝失敗")
        self.refresh_tools()
        if msgs:
            QMessageBox.warning(self, "元件安裝失敗", "\n".join(msgs) + "\n\n請檢查網路連線，再按上方的「重新下載」按鈕重試。")

    # ---- 下載 ----
    def start_download(self):
        url = self.url.text().strip()
        base = self.dst.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "請貼上網址！")
            return
        if not base:
            QMessageBox.warning(self, "提示", "請選擇下載位置！")
            return
        try:
            os.makedirs(base, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "提示", f"無法建立資料夾:\n{e}")
            return
        o = {"br": self.br.currentText(), "seg": int(self.seg.currentText()), "playlist": self.pl_cb.isChecked(),
             "start": self.start.value(), "aria": self.aria_cb.isChecked()}
        c = self.pl.cfg
        c.update(dl_dir=base, dl_br=o["br"], dl_seg=o["seg"], dl_playlist=o["playlist"], dl_aria2=o["aria"])
        save_cfg(c)
        self.running, self._stop = True, False
        self.go.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.hist_btn.setEnabled(False)
        self.fix_y.setEnabled(False)
        self.fix_f.setEnabled(False)
        self.listw.clear()
        self.paths.clear()
        self.stats.update(ok=0, skip=0, fail=0, total=0)
        self._summary()
        threading.Thread(target=self._run_job, args=(url, base, o), daemon=True).start()

    def _job_done(self, save_path):
        self.running = False
        self.stop_btn.setEnabled(False)
        self.status.setText("準備就緒")
        self.bar.setValue(0)
        self.refresh_tools()
        if save_path and os.path.isdir(save_path):
            self.pl.after_download(save_path)

    def _run_job(self, url, base, o):
        st = self.stats
        save_path = None
        try:
            yt = get_ytdlp()

            def status_cb(text, color=None):
                self.ui.emit(lambda: self.status.setText(text))
            folder_name, entries = scan_playlist_all(yt, url, o["playlist"], status_cb, o["start"], lambda: self._stop)
            if self._stop:
                return
            if folder_name is None:
                self._ask(lambda: QMessageBox.critical(self, "失敗", "無法解析連結，請確認網址是否正確。"))
                return
            if not entries:
                self._ask(lambda: QMessageBox.critical(self, "失敗", "掃描結果為空，請確認連結或網路狀態。"))
                return
            save_path = os.path.join(base, folder_name)
            os.makedirs(save_path, exist_ok=True)
            history_file = os.path.join(save_path, "download_history.txt")
            archived = load_archive(history_file)
            st["total"] = len(entries)
            for i, e in enumerate(entries):
                title = e.get("title") or f"(未知 {i + 1})"
                if in_archive(archived, e.get("id", "")):
                    st["skip"] += 1
                    txt = f"⏭️ [{i + 1}] {title}  (已有)"
                else:
                    txt = f"⏳ [{i + 1}] {title}"
                self.ui.emit(lambda t=txt: self.listw.addItem(t))
            self.ui.emit(self._summary)

            opts = {
                "outtmpl": os.path.join(save_path, "%(title).80s.%(ext)s"),   # 標題截 80 字，避免超過 Windows 260 字元路徑限制
                "windowsfilenames": True,
                "ffmpeg_location": ffmpeg_path(),
                "noplaylist": True, "continuedl": True, "quiet": True, "no_warnings": True,
                "concurrent_fragment_downloads": o["seg"],
                "format": "bestaudio/best",
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": o["br"]}],
            }
            aria = aria2c_path() if o["aria"] else ""
            if aria:
                os.environ["PATH"] = os.path.dirname(aria) + os.pathsep + os.environ.get("PATH", "")
                opts["external_downloader"] = "aria2c"
                opts["external_downloader_args"] = {"aria2c": ["-x16", "-s16", "-k1M"]}
            Cancel = getattr(getattr(yt, "utils", None), "DownloadCancelled", DownloadStop)
            failed, done = [], {"n": 0}

            def hooks(i, title):
                last = {"p": -1}

                def ph(d):
                    if self._stop:
                        raise Cancel()
                    if d["status"] == "downloading":
                        tot = d.get("total_bytes") or d.get("total_bytes_estimate")
                        if tot:
                            p = d.get("downloaded_bytes", 0) * 100 / tot
                            if int(p * 10) != last["p"]:
                                last["p"] = int(p * 10)
                                self.ui.emit(lambda: self._row(i, f"🚀 [{i + 1}] {title}  ({p:.1f}%)"))
                    elif d["status"] == "finished":
                        if d.get("filename"):
                            self.paths[i] = d["filename"]
                        self.ui.emit(lambda: self._row(i, f"⚙️ [{i + 1}] {title}  (轉碼中...)"))

                def pp(d):
                    if d.get("status") == "finished":          # 轉成 mp3 後，這裡拿到的才是最終檔案路徑
                        info = d.get("info_dict") or {}
                        fp = info.get("filepath") or info.get("_filename")
                        if fp:
                            self.paths[i] = fp
                return ph, pp

            def download_one(i, e):
                vid, title = e.get("id", ""), e.get("title") or f"未知 {i + 1}"
                self.ui.emit(lambda: self._row(i, f"🚀 [{i + 1}] {title}  (準備中...)"))
                ph, pp = hooks(i, title)
                o2 = dict(opts, progress_hooks=[ph], postprocessor_hooks=[pp])
                ok, err = False, ""
                try:
                    with yt.YoutubeDL(o2) as y:
                        ok = (y.download([f"https://www.youtube.com/watch?v={vid}"]) == 0)
                except Cancel:
                    return "stop"
                except Exception as ex:
                    err = (str(ex).strip().splitlines() or [ex.__class__.__name__])[-1][:90]
                if self._stop:
                    return "stop"
                if not ok:                                   # 轉檔階段才出錯，但檔案其實已經產生的情況
                    exts = {".mp3", ".mp4", ".m4a", ".flac", ".wav", ".ogg", ".aac", ".opus", ".wma"}
                    key = title.lower()[:15]
                    try:
                        ok = any(key in f.lower() for f in os.listdir(save_path) if os.path.splitext(f)[1].lower() in exts)
                    except OSError:
                        ok = False
                done["n"] += 1
                pct = int(done["n"] * 100 / max(1, st["total"]))
                self.ui.emit(lambda: self.bar.setValue(pct))
                if ok:
                    save_to_archive(history_file, vid)
                    archived.add(f"youtube {vid}")
                    st["ok"] += 1
                    self.ui.emit(lambda: self._row(i, f"✅ [{i + 1}] {title}"))
                else:
                    st["fail"] += 1
                    failed.append({"index": i, "title": title})
                    self.ui.emit(lambda: self._row(i, f"⚠️ [{i + 1}] {title}  (無法下載" + (f": {err}" if err else "") + ")"))
                self.ui.emit(self._summary)
                return ok

            for i, e in enumerate(entries):
                if in_archive(archived, e.get("id", "")):
                    continue
                if self._stop or download_one(i, e) == "stop":
                    break
            if self._stop:
                self.ui.emit(lambda: self.status.setText("⏹ 已停止"))
                return

            if failed:
                go = self._ask(lambda: QMessageBox.question(
                    self, "下載完成 — 有歌曲失敗",
                    f"✅ 本次新下載：{st['ok']} 首\n⏭️ 已有自動跳過：{st['skip']} 首\n⚠️ 無法下載：{st['fail']} 首\n\n"
                    f"有 {len(failed)} 首下載失敗，要自動搜尋替代版本嗎？",
                    QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes)
                if go:
                    self._alt_search(yt, failed, opts, hooks)
            s = st
            self.ui.emit(lambda: QMessageBox.information(
                self, "全部完成",
                f"🎉 全部處理完畢！\n\n✅ 成功下載：{s['ok']} 首\n⏭️ 已有自動跳過：{s['skip']} 首\n⚠️ 無法下載：{s['fail']} 首\n"
                f"────────────────\n📋 合計掃描：{s['total']} 首\n\n新下載的歌已加進播放清單。"))
        except Exception as err:
            msg = str(err)
            self.ui.emit(lambda: QMessageBox.critical(self, "失敗", f"下載失敗！\n如果是 yt-dlp 的問題，請按「重新下載 yt-dlp」再試。\n\n錯誤：{msg}"))
        finally:
            self.ui.emit(lambda: self._job_done(save_path))

    def _alt_search(self, yt, failed, opts, hooks):
        st = self.stats
        self.ui.emit(lambda: self.status.setText("🔍 搜尋替代版本中..."))

        def alt_one(song):
            i, title = song["index"], song["title"]
            self.ui.emit(lambda: self._row(i, f"🔍 [{i + 1}] {title}  (搜尋替代中...)"))
            try:
                q = re.sub(r"[(（][^)）]*[)）]", "", title).strip() or title
                cands = []

                def fetch(query):
                    with yt.YoutubeDL({"quiet": True, "noplaylist": True, "ignoreerrors": True,
                                       "extract_flat": False, "skip_download": True}) as ydl:
                        res = ydl.extract_info(query, download=False)
                    if not res:
                        return
                    for en in (res.get("entries") or ([res] if res.get("id") else [])):
                        if not en:
                            continue
                        vid, ttl = en.get("id", ""), en.get("title", "")
                        if vid and ttl:
                            cands.append({"id": vid, "title": f"[{en.get('extractor_key', 'YouTube')}] {ttl}",
                                          "dur": en.get("duration") or 0,
                                          "url": en.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}"})
                for query in (f"ytsearch5:{q}", f"ytsearch3:{q} 歌詞", f"bilisearch5:{q}"):
                    try:
                        fetch(query)
                    except Exception:
                        pass
                seen, uniq = set(), []
                for c in cands:
                    if c["id"] not in seen:
                        seen.add(c["id"])
                        uniq.append(c)
                cands = uniq[:10]
                if not cands:
                    self.ui.emit(lambda: self._row(i, f"⚠️ [{i + 1}] {title}  (找不到替代)"))
                    return

                def pick():
                    d = AltPicker(self, title, cands)
                    return d.url if d.exec() else None
                url = self._ask(pick)
                if not url:
                    self.ui.emit(lambda: self._row(i, f"⏭️ [{i + 1}] {title}  (跳過)"))
                    return
                self.ui.emit(lambda: self._row(i, f"🚀 [{i + 1}] {title}  (下載替代版本...)"))
                ph, pp = hooks(i, title)
                with yt.YoutubeDL(dict(opts, progress_hooks=[ph], postprocessor_hooks=[pp])) as y:
                    ok = (y.download([url]) == 0)
                if ok:
                    st["ok"] += 1
                    st["fail"] -= 1
                    self.ui.emit(lambda: self._row(i, f"✅ [{i + 1}] {title}  (替代版本)"))
                    self.ui.emit(self._summary)
                else:
                    self.ui.emit(lambda: self._row(i, f"⚠️ [{i + 1}] {title}  (替代也失敗)"))
            except Exception:
                self.ui.emit(lambda: self._row(i, f"⚠️ [{i + 1}] {title}  (替代也失敗)"))

        for song in sorted(failed, key=lambda x: x["index"]):
            if self._stop:
                return
            alt_one(song)

    # ---- 修復下載記錄 ----
    def fix_history(self):
        url = self.url.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "請先在上方貼上原本的清單網址！")
            return
        target = QFileDialog.getExistingDirectory(self, "選擇要修復記錄的下載資料夾（清單資料夾）", self.dst.text())
        if not target:
            return
        self.running = True
        self.refresh_tools()

        def work():
            try:
                yt = get_ytdlp()
                exts = {".mp3", ".mp4", ".m4a", ".flac", ".wav", ".ogg", ".aac", ".opus", ".wma"}
                existing = {os.path.splitext(f)[0].lower().strip() for f in os.listdir(target) if os.path.splitext(f)[1].lower() in exts}

                def status_cb(text, color=None):
                    self.ui.emit(lambda: self.status.setText("🔧 " + text))
                _, entries = scan_playlist_all(yt, url, True, status_cb)
                if not entries:
                    self.ui.emit(lambda: QMessageBox.critical(self, "失敗", "無法解析連結！"))
                    return
                hf = os.path.join(target, "download_history.txt")
                archived, matched = load_archive(hf), 0
                for e in entries:
                    vid, title = e.get("id", ""), (e.get("title") or "").lower().strip()
                    if not vid or in_archive(archived, vid):
                        continue
                    key = title[:10] if len(title) >= 6 else title
                    if key and any(key in f for f in existing):
                        save_to_archive(hf, vid)
                        archived.add(f"youtube {vid}")
                        matched += 1
                self.ui.emit(lambda: QMessageBox.information(self, "修復完成", f"🔧 修復完成！\n\n✅ 補寫記錄：{matched} 首\n\n下次下載同一個清單，這些歌就會自動跳過了！"))
            except Exception as err:
                msg = str(err)
                self.ui.emit(lambda: QMessageBox.critical(self, "失敗", f"修復失敗！\n\n錯誤：{msg}"))
            finally:
                def fin():
                    self.running = False
                    self.status.setText("準備就緒")
                    self.refresh_tools()
                self.ui.emit(fin)
        threading.Thread(target=work, daemon=True).start()


# ---------------- 桌布幻燈片 ----------------
def scan_images(folder):
    out = []
    for root, _, files in os.walk(folder):
        for n in files:
            if n.lower().endswith(IMG_EXTS):
                out.append(os.path.join(root, n))
    return sorted(out, key=lambda p: p.lower())


def make_thumb(path, w=96, h=54):
    """縮圖 (用 QImageReader 直接讀縮小版，大圖也不會卡)"""
    r = QImageReader(path)
    sz = r.size()
    if sz.isValid() and sz.width() > 0:
        r.setScaledSize(sz.scaled(w, h, Qt.KeepAspectRatio))
    img = r.read()
    return QIcon(QPixmap.fromImage(img)) if not img.isNull() else QIcon()


class SlideshowDialog(QDialog):
    def __init__(self, pl):
        super().__init__(pl)
        self.pl = pl
        c = pl.cfg
        self.setWindowTitle("桌布幻燈片")
        self.resize(620, 560)
        v = QVBoxLayout(self)
        self.on = QCheckBox("啟用桌布幻燈片")
        self.on.setChecked(c["wp_slide"])
        v.addWidget(self.on)

        r = QHBoxLayout()
        r.addWidget(QLabel("圖片來源:"))
        self.src = QComboBox()
        self.src.addItems(["圖片資料夾 (自動抓裡面所有圖片)", "自己一張一張挑"])
        self.src.setCurrentIndex(1 if c["wp_source"] == "picked" else 0)
        r.addWidget(self.src, 1)
        v.addLayout(r)

        # --- 資料夾模式 ---
        self.folder_box = QWidget()
        fv = QVBoxLayout(self.folder_box)
        fv.setContentsMargins(0, 0, 0, 0)
        fv.addWidget(QLabel("圖片資料夾 (會連子資料夾一起找，依檔名排序):"))
        r = QHBoxLayout()
        self.dir = QLineEdit(c["wp_dir"])
        self.dir.setReadOnly(True)
        r.addWidget(self.dir, 1)
        b = QPushButton("選擇資料夾…")
        b.clicked.connect(self._pick)
        r.addWidget(b)
        fv.addLayout(r)
        v.addWidget(self.folder_box)

        # --- 一張一張挑模式 ---
        self.pick_box = QWidget()
        pv = QVBoxLayout(self.pick_box)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.addWidget(QLabel("挑好的圖片 (「按順序」時照這裡由上到下的順序換):"))
        self.files = QListWidget()
        self.files.setIconSize(QSize(96, 54))
        self.files.setSelectionMode(QAbstractItemView.ExtendedSelection)
        pv.addWidget(self.files, 1)
        r = QHBoxLayout()
        for txt, fn in (("＋加入圖片…", self._add_dialog), ("移除選取", self._remove), ("↑ 上移", lambda: self._move(-1)),
                        ("↓ 下移", lambda: self._move(1)), ("清空", self._clear)):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            r.addWidget(b)
        r.addStretch(1)
        pv.addLayout(r)
        v.addWidget(self.pick_box, 1)
        for p in c["wp_files"]:
            self._add_item(p)

        self.count = QLabel("")
        self.count.setObjectName("sub")
        v.addWidget(self.count)
        r = QHBoxLayout()
        r.addWidget(QLabel("換圖時機:"))
        self.mode = QComboBox()
        self.mode.addItems(WP_MODES)
        self.mode.setCurrentText(c["wp_mode"])
        r.addWidget(self.mode, 1)
        v.addLayout(r)
        r = QHBoxLayout()
        r.addWidget(QLabel("順序:"))
        self.order = QComboBox()
        self.order.addItems(["按順序", "隨機"])
        self.order.setCurrentIndex(1 if c["wp_shuffle"] else 0)
        r.addWidget(self.order, 1)
        v.addLayout(r)
        self.fade = QCheckBox("換圖時淡入淡出")
        self.fade.setChecked(c["wp_fade"])
        v.addWidget(self.fade)
        r = QHBoxLayout()
        nb = QPushButton("立即換下一張")
        nb.clicked.connect(self._next)
        r.addWidget(nb)
        r.addStretch(1)
        cl = QPushButton("關閉")
        cl.clicked.connect(self.accept)
        r.addWidget(cl)
        v.addLayout(r)
        self._show_source()
        for w in (self.on, self.fade):
            w.toggled.connect(lambda *_: self._apply())
        for w in (self.mode, self.order):
            w.currentIndexChanged.connect(lambda *_: self._apply())
        self.src.currentIndexChanged.connect(lambda *_: (self._show_source(), self._apply()))
        self._count()

    def _show_source(self):
        picked = self.src.currentIndex() == 1
        self.pick_box.setVisible(picked)
        self.folder_box.setVisible(not picked)

    # ---- 一張一張挑 ----
    def _paths(self):
        return [self.files.item(i).data(Qt.UserRole) for i in range(self.files.count())]

    def _add_item(self, path):
        it = QListWidgetItem(os.path.basename(path))
        it.setData(Qt.UserRole, path)
        it.setToolTip(path)
        if os.path.isfile(path):
            it.setIcon(make_thumb(path))
        else:
            it.setText(os.path.basename(path) + "  (找不到檔案)")
        self.files.addItem(it)

    def add_files(self, paths):
        have = set(self._paths())
        added = False
        for p in paths:
            p = os.path.normpath(p)
            if p not in have and p.lower().endswith(IMG_EXTS):
                self._add_item(p)
                have.add(p)
                added = True
        if added:
            self._apply()

    def _add_dialog(self):
        fs, _ = QFileDialog.getOpenFileNames(self, "挑桌布圖片 (可按住 Ctrl / Shift 一次選多張)", "",
                                             "圖片 (*.jpg *.jpeg *.png *.bmp *.webp)")
        if fs:
            self.add_files(fs)

    def _remove(self):
        for it in self.files.selectedItems():
            self.files.takeItem(self.files.row(it))
        self._apply()

    def _clear(self):
        self.files.clear()
        self._apply()

    def _move(self, d):
        rows = sorted((self.files.row(i) for i in self.files.selectedItems()), reverse=(d > 0))
        if not rows:
            return
        for r in rows:
            nr = r + d
            if 0 <= nr < self.files.count() and nr not in rows:
                self.files.insertItem(nr, self.files.takeItem(r))
        for r in rows:
            n = r + d
            if 0 <= n < self.files.count():
                self.files.item(n).setSelected(True)
        self._apply()

    # ---- 共用 ----
    def _count(self):
        n = len(self.pl._wp_list)
        if self.src.currentIndex() == 1:
            miss = self.files.count() - n
            self.count.setText(f"已挑 {n} 張" + (f"（另有 {miss} 張找不到檔案，會自動略過）" if miss > 0 else "")
                               if self.files.count() else "還沒挑圖片，按「＋加入圖片…」")
        else:
            self.count.setText(f"找到 {n} 張圖片" if self.dir.text() else "還沒選擇資料夾")

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "選擇放桌布圖片的資料夾", self.dir.text())
        if d:
            self.dir.setText(os.path.normpath(d))
            self._apply()

    def _apply(self):
        c = self.pl.cfg
        c["wp_source"] = "picked" if self.src.currentIndex() == 1 else "folder"
        c["wp_files"] = self._paths()
        c["wp_dir"] = self.dir.text()
        c["wp_slide"] = self.on.isChecked()
        c["wp_mode"] = self.mode.currentText()
        c["wp_shuffle"] = self.order.currentIndex() == 1
        c["wp_fade"] = self.fade.isChecked()
        save_cfg(c)
        self.pl._slideshow_changed()
        self._count()
        if c["wp_slide"] and self.pl._wp_list and c["wallpaper"] not in self.pl._wp_list:
            self.pl.next_wallpaper()            # 剛開啟(或換了來源)時，馬上換成清單裡的圖

    def _next(self):
        if not self.pl.next_wallpaper():
            QMessageBox.information(self, "桌布幻燈片", "請先選擇圖片資料夾，或挑幾張圖片。")


# ---------------- 天氣 (中央氣象署資料，經 GitHub 自動更新；境外地點用 Open-Meteo 備援) ----------------
WX_CODE = {0: "晴天", 1: "大致晴朗", 2: "多雲", 3: "陰天", 45: "有霧", 48: "霧淞", 51: "毛毛雨", 53: "毛毛雨", 55: "濃密毛毛雨",
           56: "凍毛毛雨", 57: "凍毛毛雨", 61: "小雨", 63: "中雨", 65: "大雨", 66: "凍雨", 67: "凍雨", 71: "小雪", 73: "中雪",
           75: "大雪", 77: "雪粒", 80: "陣雨", 81: "陣雨", 82: "強陣雨", 85: "陣雪", 86: "強陣雪", 95: "雷雨",
           96: "雷雨夾小冰雹", 99: "雷雨夾大冰雹"}


def wx_text(code):
    return WX_CODE.get(code, "天氣不明") if code is not None else "天氣不明"


def wx_icon(code):
    if code is None:
        return "❔"
    if code == 0:
        return "☀️"
    if code == 1:
        return "🌤️"
    if code == 2:
        return "⛅"
    if code == 3:
        return "☁️"
    if code in (45, 48):
        return "🌫️"
    if code in (51, 53, 55, 56, 57, 80, 81, 82):
        return "🌦️"
    if code in (61, 63, 65, 66, 67):
        return "🌧️"
    if code in (71, 73, 75, 77, 85, 86):
        return "🌨️"
    if code in (95, 96, 99):
        return "⛈️"
    return "🌡️"


def link_label(prefix, url, text):
    """可點的連結標籤。顏色要用 <span> 包在連結裡才有效 (樣式表會蓋掉調色盤)，深色背景上才看得清楚。"""
    lb = QLabel(f'{prefix}<a href="{url}"><span style="color:#8ab4ff; text-decoration: underline;">{text}</span></a>')
    lb.setOpenExternalLinks(True)
    return lb


def fetch_json(url, timeout=15):
    return json.loads(fetch_text(url, timeout))


def geocode_openmeteo(name):
    """城市/鄉鎮名 → 座標清單 (Open-Meteo 地名查詢，支援中文)"""
    q = urllib.parse.quote(name.strip())
    data = fetch_json(f"https://geocoding-api.open-meteo.com/v1/search?name={q}&count=8&language=zh&format=json")
    out = []
    for r in data.get("results") or []:
        if r.get("latitude") is None or r.get("longitude") is None:
            continue
        admin = " ".join(x for x in (r.get("admin2"), r.get("admin1")) if x and x != r.get("name"))
        out.append({"name": r.get("name", ""), "admin": admin, "country": r.get("country", ""),
                    "lat": float(r["latitude"]), "lon": float(r["longitude"])})
    return out


def fetch_weather_openmeteo(lat, lon):
    """目前氣溫 + 今天起 3 天的預報 (最高/最低溫、降雨機率、天氣代碼)"""
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
           "&current=temperature_2m,weather_code"
           "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"
           "&timezone=auto&forecast_days=3") % (lat, lon)
    d = fetch_json(url)
    cur, dy = d.get("current") or {}, d.get("daily") or {}

    def pick(key, i):
        arr = dy.get(key) or []
        return arr[i] if i < len(arr) else None
    days = [{"date": day, "tmax": pick("temperature_2m_max", i), "tmin": pick("temperature_2m_min", i),
             "pop": pick("precipitation_probability_max", i), "code": pick("weather_code", i)}
            for i, day in enumerate(dy.get("time") or [])]
    if not days:
        raise ValueError("沒有收到預報資料")
    return {"ts": time.time(), "temp": cur.get("temperature_2m"), "code": cur.get("weather_code"), "days": days}


_CWA_CACHE = {"ts": 0.0, "data": None}


def cwa_url():
    """weather.json 的網址 (由 GitHub Actions 定時更新，授權碼只存在 GitHub Secrets，不在這個程式裡)。"""
    if os.environ.get("TRUCK_WEATHER_URL"):          # 測試用
        return os.environ["TRUCK_WEATHER_URL"]
    rp = parse_repo(default_src())
    if not rp:
        return ""
    return f"https://raw.githubusercontent.com/{rp[0]}/{rp[1]}/weather-data/weather.json"


def cwa_data(force=False):
    if not force and _CWA_CACHE["data"] and time.time() - _CWA_CACHE["ts"] < 600:
        return _CWA_CACHE["data"]
    url = cwa_url()
    if not url:
        raise ValueError("沒有設定氣象資料來源")
    d = fetch_json(url, 20)
    if not d.get("stations") or not d.get("counties"):
        raise ValueError("氣象資料內容不完整")
    _CWA_CACHE.update(ts=time.time(), data=d)
    return d


def _norm_place(t):
    return (t or "").replace("台", "臺").replace(" ", "").strip()


def _km(lat1, lon1, lat2, lon2):
    import math
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def fetch_weather_cwa(lat, lon):
    d = cwa_data()
    best, bd = None, 1e9
    for st in d["stations"]:
        k = _km(lat, lon, st["la"], st["lo"])
        if k < bd:
            best, bd = st, k
    if best is None or bd > 40:
        raise ValueError("這個位置離台灣的氣象站太遠")
    cd = d["counties"].get(best["c"]) or {}
    days = cd.get("days") or []
    if not days:
        raise ValueError("沒有收到預報資料")
    d0 = days[0]
    return {"ts": time.time(), "temp": best.get("temp"), "code": best.get("code") if best.get("wx") else d0.get("code"),
            "text": best.get("wx") or d0.get("text"), "days": days, "src": "cwa",
            "place": (best["c"] + best.get("t", "")) if bd <= 5 else best["c"], "updated": d.get("updated", "")}


_PLACE_SUF = ("區", "鄉", "鎮", "市", "縣")


def _strip_suf(t):
    return t[:-1] if len(t) >= 3 and t[-1] in _PLACE_SUF else t


def geocode_cwa(name):
    d = cwa_data()
    q = _norm_place(name)
    if not q:
        return []
    qs = _strip_suf(q)
    seen, out = set(), []
    for st in d["stations"]:
        key = (st["c"], st.get("t", ""))
        town = _norm_place(st.get("t", ""))
        cty = _norm_place(st["c"])
        hit = (q in town or q in cty or q in _norm_place(st["n"]) or (town and town in q) or (q.startswith(cty) and q[len(cty):] in town)
               or (qs != q and (qs in town or qs in cty or qs in _norm_place(st["n"]))))
        if hit and key not in seen:
            seen.add(key)
            out.append({"name": st.get("t") or st["c"], "admin": st["c"], "country": "台灣", "lat": st["la"], "lon": st["lo"]})
    return out[:12]


def geocode(name):
    """先用中央氣象署資料找台灣的縣市/鄉鎮；找不到或讀不到才用 Open-Meteo (可搜尋國外)。"""
    local, err = [], ""
    try:
        local = geocode_cwa(name)
    except Exception as e:
        err = str(e)
    if local:
        return local
    tries = [name.strip()]
    s2 = _strip_suf(_norm_place(name))
    if s2 and s2 not in tries:
        tries.append(s2)
    last = None
    for t in tries:
        try:
            r = geocode_openmeteo(t)
            if r:
                return r
        except Exception as e:
            last = e
    if last is not None:
        if err:
            raise RuntimeError(f"氣象資料讀取失敗 ({err})；備援查詢也失敗 ({last})")
        raise last
    return []


def fetch_weather(lat, lon):
    """台灣地點用中央氣象署資料；境外或讀不到時用 Open-Meteo 備援。"""
    try:
        return fetch_weather_cwa(lat, lon)
    except Exception as e1:
        try:
            r = fetch_weather_openmeteo(lat, lon)
            r["src"] = "om"
            return r
        except Exception as e2:
            raise RuntimeError(f"{e1}；備援也失敗 ({e2})")


def locate_windows():
    """用 Windows 的定位服務取得目前位置 (緯度, 經度)。失敗丟 RuntimeError(說明)。"""
    if not IS_WIN:
        raise RuntimeError("自動定位目前只支援 Windows，請改用搜尋地區名稱。")
    ps = ("Add-Type -AssemblyName System.Device;"
          "$w=New-Object System.Device.Location.GeoCoordinateWatcher([System.Device.Location.GeoPositionAccuracy]::Default);"
          "$w.Start();$t=0;"
          "while($w.Status -ne 'Ready' -and $w.Permission -ne 'Denied' -and $t -lt 150){Start-Sleep -Milliseconds 100;$t++};"
          "if($w.Permission -eq 'Denied'){exit 5};"
          "$l=$w.Position.Location;if($l.IsUnknown){exit 6};"
          "[string]::Format([Globalization.CultureInfo]::InvariantCulture,'{0},{1}',$l.Latitude,$l.Longitude)")
    r = _ps_run(ps, 45)
    if r.returncode == 5:
        raise RuntimeError("Windows 沒有允許定位。請到「設定 > 隱私權與安全性 > 位置」開啟位置服務，並允許桌面應用程式存取位置。")
    if r.returncode == 6:
        raise RuntimeError("抓不到位置 (電腦可能沒有 Wi-Fi 或 GPS，或位置服務沒開)。請改用搜尋地區名稱。")
    try:
        lat, lon = (float(x) for x in r.stdout.decode("utf-8", "ignore").strip().splitlines()[-1].split(","))
        return lat, lon
    except Exception:
        raise RuntimeError("定位失敗，請改用搜尋地區名稱。")


class Net(QObject):
    """背景執行緒做網路工作，結果用訊號送回主執行緒: res(標籤, ("ok", 結果) 或 ("err", 錯誤訊息))"""
    res = Signal(str, object)

    def run(self, tag, fn, *args):
        def work():
            try:
                out = ("ok", fn(*args))
            except Exception as e:
                out = ("err", str(e) or e.__class__.__name__)
            self.res.emit(tag, out)
        threading.Thread(target=work, daemon=True).start()


class WeatherDialog(QDialog):
    """天氣與定位設定"""
    def __init__(self, pl):
        super().__init__(pl)
        self.pl = pl
        c = pl.cfg
        self.setWindowTitle("天氣與定位")
        self.resize(520, 560)
        v = QVBoxLayout(self)
        v.addWidget(QLabel("找地區 (城市、鄉鎮名都可以，中文英文皆可):"))
        r = QHBoxLayout()
        self.q = QLineEdit()
        self.q.setPlaceholderText("例如 二崙、台北市、Tokyo")
        self.q.returnPressed.connect(self.search)
        r.addWidget(self.q, 1)
        sb = QPushButton("🔍 搜尋")
        sb.clicked.connect(self.search)
        r.addWidget(sb)
        v.addLayout(r)
        self.results = QListWidget()
        self.results.setMaximumHeight(150)
        self.results.itemDoubleClicked.connect(lambda *_: self.choose())
        v.addWidget(self.results)
        r = QHBoxLayout()
        cb = QPushButton("✔ 用選到的地區")
        cb.clicked.connect(self.choose)
        r.addWidget(cb)
        lb = QPushButton("📍 自動定位 (Windows 定位服務)")
        lb.clicked.connect(self.locate)
        r.addWidget(lb)
        v.addLayout(r)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setObjectName("sub")
        v.addWidget(self.status)

        v.addSpacing(8)
        v.addWidget(QLabel("報時時唸的地區名稱 (可以自己改，例如改成 家裡、二崙鄉):"))
        self.name = QLineEdit(c["weather_name"])
        self.name.textChanged.connect(self._name_changed)
        v.addWidget(self.name)
        self.coord = QLabel("")
        self.coord.setObjectName("sub")
        v.addWidget(self.coord)
        self.extra = QCheckBox("報氣溫時，也報天氣狀況與降雨機率 (例如「多雲，降雨機率百分之30」)")
        self.extra.setChecked(c["chime_weather_extra"])
        self.extra.toggled.connect(lambda on: c.__setitem__("chime_weather_extra", on))
        v.addWidget(self.extra)
        v.addStretch(1)
        v.addWidget(link_label("天氣資料來源: ", "https://opendata.cwa.gov.tw/", "中央氣象署開放資料平臺"))
        v.addWidget(link_label("國外地點備援: ", "https://open-meteo.com/", "Weather data by Open-Meteo.com"))
        note = QLabel("隱私說明: 台灣的天氣是從你的 GitHub 倉庫讀取一份定時更新的資料，不會把座標送出去 (座標只在你的電腦上比對)；只有查國外地點時才會把座標送到 Open-Meteo；用線上女聲報時時，會把要唸的那句話 (時間、地區名稱、氣溫) 送到 Microsoft 的線上語音服務。座標只存在你自己的設定檔裡。")
        note.setObjectName("sub")
        note.setWordWrap(True)
        v.addWidget(note)
        r = QHBoxLayout()
        tb = QPushButton("🔊 試聽播報")
        tb.clicked.connect(lambda: pl._announce(test=True))
        r.addWidget(tb)
        r.addStretch(1)
        cl = QPushButton("關閉")
        cl.clicked.connect(self.accept)
        r.addWidget(cl)
        v.addLayout(r)
        self._coord()

    def _coord(self):
        c = self.pl.cfg
        self.coord.setText(f"目前座標: {c['weather_lat']:.3f}, {c['weather_lon']:.3f}" if c["weather_lat"] is not None else "還沒有設定地區")

    def _name_changed(self, t):
        self.pl.cfg["weather_name"] = t.strip()
        self.pl._show_weather()

    def search(self):
        t = self.q.text().strip()
        if not t:
            return
        self.status.setText("搜尋中...")
        self.pl.net.run("geo", geocode, t)

    def locate(self):
        self.status.setText("定位中... (最多等 15 秒；第一次可能會跳出 Windows 的位置權限詢問)")
        self.pl.net.run("loc", locate_windows)

    def on_net(self, tag, out):
        kind, val = out
        if tag == "geo":
            self.results.clear()
            if kind == "err":
                self.status.setText("⚠️ 搜尋失敗: " + val)
                return
            if not val:
                self.status.setText("找不到這個地區，換個名字試試 (例如加上縣市名)。")
                return
            for r in val:
                it = QListWidgetItem("　".join(x for x in (r["name"], r["admin"], r["country"]) if x))
                it.setData(Qt.UserRole, r)
                self.results.addItem(it)
            self.results.setCurrentRow(0)
            self.status.setText(f"找到 {len(val)} 個，請選一個 (雙擊或按「用選到的地區」)。")
        elif tag == "loc":
            if kind == "err":
                self.status.setText("⚠️ " + val)
                return
            lat, lon = val
            self.pl.cfg["weather_name"] = ""
            self.name.setText("")
            self._set_place(None, lat, lon)
            self.status.setText("✅ 已定位，正在取得地區名稱。Windows 只提供座標，名稱取自附近的氣象站 (台灣以外要自己填)；不對可以直接改下方「報時時唸的地區名稱」。")

    def choose(self):
        it = self.results.currentItem()
        if not it:
            self.status.setText("請先搜尋並選一個地區。")
            return
        r = it.data(Qt.UserRole)
        self._set_place(r["name"], r["lat"], r["lon"])
        self.status.setText(f"✅ 已設定為 {r['name']}。")

    def _set_place(self, name, lat, lon):
        c = self.pl.cfg
        c["weather_lat"], c["weather_lon"] = lat, lon
        if name:
            self.name.setText(name)
            c["weather_name"] = name
        save_cfg(c)
        self._coord()
        self.pl.weather = None
        self.pl.refresh_weather()
        self.pl._show_weather()


class Hot(QObject):
    prev = Signal()
    toggle = Signal()
    next = Signal()


EDGE_VOICES = {          # Edge 線上女聲 (需要網路；不是微軟正式開放的服務，有一天可能失效，失效時自動改用 Windows 內建女聲)
    "曉臻 (台灣・線上)": "zh-TW-HsiaoChenNeural",
    "曉雨 (台灣・線上)": "zh-TW-HsiaoYuNeural",
    "曉曉 (中文・線上)": "zh-CN-XiaoxiaoNeural",
    "曉伊 (中文・線上)": "zh-CN-XiaoyiNeural",
}
VOICE_AUTO = "自動 (優先用最好聽的女聲)"


def edge_available():
    try:
        return importlib.util.find_spec("edge_tts") is not None
    except Exception:
        return False


def _ps_run(ps, timeout=60):
    flags = 0x08000000 if os.name == "nt" else 0      # CREATE_NO_WINDOW，不跳出黑視窗
    ps = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + ps
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                          creationflags=flags, capture_output=True, timeout=timeout)


class Speaker(QObject):
    """語音報時 (只用女聲)。Windows 內建語音 (System.Speech) 不需安裝套件；另可用 Edge 線上女聲 (edge-tts，要網路)。
    done(code): 0=成功  3=找不到女聲  5=線上語音失敗、已改用內建女聲  其他=失敗"""
    done = Signal(int)
    voices = Signal(list)

    def list_voices(self):
        def run():
            names = []
            try:
                r = _ps_run("Add-Type -AssemblyName System.Speech;"
                            "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices()|"
                            "Where-Object{$_.Enabled -and $_.VoiceInfo.Gender -eq 'Female' -and $_.VoiceInfo.Culture.Name -like 'zh-*'}|"
                            "ForEach-Object{$_.VoiceInfo.Name}", 30)
                names = [x.strip() for x in r.stdout.decode("utf-8", "ignore").splitlines() if x.strip()]
            except Exception:
                pass
            self.voices.emit(names)
        threading.Thread(target=run, daemon=True).start()

    def speak(self, text, voice="", rate=0):
        threading.Thread(target=self._run, args=(text, voice, rate), daemon=True).start()

    def _edge(self, text, short, rate):
        import asyncio, tempfile, edge_tts
        fd, path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        try:
            async def go():
                await edge_tts.Communicate(text, short, rate="%+d%%" % (rate * 10)).save(path)
            asyncio.run(go())
            if os.path.getsize(path) < 500:
                raise RuntimeError("線上語音沒有回傳聲音")
            snd = pygame.mixer.Sound(path)
            ch = snd.play()
            while ch is not None and ch.get_busy():
                time.sleep(0.1)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def _sapi(self, text, voice, rate):
        want = (voice or "").replace("'", "''")
        t = text.replace("'", "''")
        ps = (
            "Add-Type -AssemblyName System.Speech;"
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            "$vs=$s.GetInstalledVoices()|Where-Object{$_.Enabled -and $_.VoiceInfo.Gender -eq 'Female'};"
            f"$v=$vs|Where-Object{{$_.VoiceInfo.Name -eq '{want}'}}|Select-Object -First 1;"
            "if(-not $v){$v=$vs|Where-Object{$_.VoiceInfo.Culture.Name -eq 'zh-TW'}|Select-Object -First 1};"
            "if(-not $v){$v=$vs|Where-Object{$_.VoiceInfo.Culture.Name -like 'zh-*'}|Select-Object -First 1};"
            "if(-not $v){exit 3};"
            f"$s.SelectVoice($v.VoiceInfo.Name);$s.Volume=100;$s.Rate={int(rate)};"
            f"$s.Speak('{t}')"
        )
        return _ps_run(ps).returncode

    def _run(self, text, voice, rate):
        try:
            used_edge_fail = False
            short = EDGE_VOICES.get(voice)
            if short is None and (not voice or voice == VOICE_AUTO) and edge_available() and pygame is not None:
                short = EDGE_VOICES["曉臻 (台灣・線上)"]
            if short and edge_available() and pygame is not None:
                try:
                    self._edge(text, short, rate)
                    self.done.emit(0)
                    return
                except Exception:
                    used_edge_fail = True
            code = self._sapi(text, "" if voice in EDGE_VOICES or voice == VOICE_AUTO else voice, rate)
            self.done.emit(5 if (code == 0 and used_edge_fail) else code)
        except Exception:
            self.done.emit(-1)


class NumBox(QLineEdit):
    """只能輸入數字的小輸入框 (QSpinBox 套樣式後文字會消失，所以自己做)"""
    def __init__(self, v, lo, hi):
        super().__init__(str(v))
        self.lo, self.hi = lo, hi
        self.setValidator(QIntValidator(lo, hi))
        self.setFixedWidth(64)
        self.setAlignment(Qt.AlignCenter)

    def value(self):
        try:
            return max(self.lo, min(self.hi, int(self.text())))
        except ValueError:
            return self.lo

    def setValue(self, v):
        self.setText(str(v))


class Drawer(QWidget):
    """右側可滑出的抽屜: 外框寬度做動畫，內容固定寬度、被裁切"""
    def __init__(self, inner, inner_w):
        super().__init__()
        self.inner, self.inner_w = inner, inner_w
        inner.setParent(self)
        self.setFixedWidth(0)
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(220)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.valueChanged.connect(lambda v: self.setFixedWidth(int(v)))
        self.anim.finished.connect(self._finished)

    def resizeEvent(self, e):
        self.inner.setGeometry(0, 0, self.inner_w, self.height())
        super().resizeEvent(e)

    def _finished(self):
        if self.width() == 0:
            self.hide()

    def slide(self, opening, animate=True):
        self.anim.stop()
        end = self.inner_w if opening else 0
        if opening:
            self.show()
        if not animate:
            self.setFixedWidth(end)
            self.setVisible(opening)
            return
        self.anim.setStartValue(self.width())
        self.anim.setEndValue(end)
        self.anim.start()


WEEK = "一二三四五六日"


def starfield(w, h):
    rnd = random.Random(7)
    img = QImage(w, h, QImage.Format_RGB32)
    p = QPainter(img)
    g = QLinearGradient(0, 0, 0, h)
    g.setColorAt(0, QColor(4, 6, 20))
    g.setColorAt(1, QColor(14, 12, 44))
    p.fillRect(0, 0, w, h, QBrush(g))
    for _ in range(int(w * h / 7000)):
        c = rnd.randrange(80, 210)
        p.setPen(QColor(c, c, min(255, c + 45), rnd.randrange(120, 255)))
        p.drawPoint(rnd.randrange(w), rnd.randrange(h))
    for _ in range(int(w * h / 50000)):
        x, y, r = rnd.randrange(w), rnd.randrange(h), rnd.choice((5, 7, 10))
        rg = QRadialGradient(x, y, r)
        rg.setColorAt(0, QColor(255, 255, 255, 230))
        rg.setColorAt(0.25, QColor(190, 200, 255, 120))
        rg.setColorAt(1, QColor(120, 140, 255, 0))
        p.setBrush(QBrush(rg))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPoint(x, y), r, r)
    p.end()
    return img


class Player(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = load_cfg()
        self.stats = Stats(DB)
        self.tracks, self.view, self.all_tracks = [], [], []
        self._rk = {}
        self.cur = None
        self.play_start = self.last_tick = 0.0
        self.pos = 0.0          # 目前播放位置(秒)，跳轉時會改變
        self.length = 0.0       # 歌曲長度(秒)
        self.counted = self.paused = False
        self._wp_src = None
        self._wp_path = None
        self._wp_prev, self._fade = None, 1.0              # 淡入淡出: 上一張背景 / 目前進度 (0~1)
        self._fade_anim = QVariantAnimation(self)
        self._fade_anim.setDuration(900)
        self._fade_anim.valueChanged.connect(self._on_fade)
        self._fade_anim.finished.connect(self._fade_done)
        self._wp_list, self._wp_idx, self._next_slide = [], -1, 0.0
        self._dl_dlg = None
        self._bg_cache = None

        self.setWindowTitle(f"卡車播放器 v{VERSION}")
        self.resize(1280, 760)
        self.setMinimumSize(900, 520)
        self.setAttribute(Qt.WA_StyledBackground, False)

        if pygame is None:
            QMessageBox.critical(self, "缺少 pygame", "自動安裝失敗。\n" + (LAST_ERR or ""))
            sys.exit(1)
        pygame.mixer.init()
        pygame.mixer.music.set_volume(self.cfg["volume"] / 100)

        self._build()
        self._apply_folders_open()
        self._apply_drawer(animate=False)
        self._slideshow_changed()
        self.apply_theme()
        self._load_folders()
        self.refresh_tracks()
        if self.cfg["frameless"]:
            self._set_frameless(True, first=True)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(500)
        self._clock_tick()
        self._hotkeys()
        self._quitting = False
        self.setWindowIcon(app_icon())
        self._setup_tray()
        self.updater = Updater()
        self.updater.result.connect(self._update_result)
        self._upd_dlg = None
        self.toast = UpdateToast(self)
        QTimer.singleShot(4000, self._auto_check)
        self.upd_timer = QTimer(self)
        self.upd_timer.timeout.connect(self._auto_check)
        self.upd_timer.start(UPDATE_CHECK_MIN * 60 * 1000)

    # ---------------- 介面 ----------------
    def _panel(self):
        f = QFrame()
        f.setObjectName("panel")
        return f

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 無邊框模式的標題列
        self.titlebar = QFrame()
        self.titlebar.setObjectName("titlebar")
        tb = QHBoxLayout(self.titlebar)
        tb.setContentsMargins(14, 4, 6, 4)
        self.title_lbl = QLabel("卡車播放器")
        tb.addWidget(self.title_lbl, 1)
        for txt, fn, tip in (("▼", self._to_tray_or_min, "縮到系統匣 (右下角圖示盒，音樂繼續播)"),
                             ("─", self._min_taskbar, "縮到工作列"),
                             ("✕", self.close, "關閉")):
            b = QPushButton(txt)
            b.setObjectName("winbtn")
            b.setFixedSize(40, 26)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            tb.addWidget(b)
        self.titlebar.hide()
        root.addWidget(self.titlebar)

        body = QHBoxLayout()
        body.setContentsMargins(14, 14, 14, 8)
        body.setSpacing(12)
        root.addLayout(body, 1)

        # 左: 資料夾
        left = self._panel()
        self.left_panel = left
        left.setFixedWidth(300)
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("音樂資料夾 (勾選才會進清單)"))
        self.folder_list = QListWidget()
        self.folder_list.itemChanged.connect(self._folder_toggled)
        lv.addWidget(self.folder_list, 1)
        hb = QHBoxLayout()
        b1, b2 = QPushButton("＋加入資料夾"), QPushButton("移除未勾選")
        b1.clicked.connect(self.add_folder)
        b2.clicked.connect(self.remove_unchecked)
        hb.addWidget(b1)
        hb.addWidget(b2)
        lv.addLayout(hb)
        b6 = QPushButton("⬇ 下載音樂…")
        b6.clicked.connect(self.open_download)
        lv.addWidget(b6)
        b4 = QPushButton("🔍 找重複歌曲…")
        b4.clicked.connect(self.find_dups)
        lv.addWidget(b4)
        self.hid_btn = QPushButton("🙈 隱藏清單 (0)")
        self.hid_btn.clicked.connect(self.open_hidden)
        lv.addWidget(self.hid_btn)
        b3 = QPushButton("重新掃描")
        b3.clicked.connect(self.refresh_tracks)
        lv.addWidget(b3)
        b5 = QPushButton(f"🔄 檢查更新 (v{VERSION})")
        b5.clicked.connect(self.open_update)
        lv.addWidget(b5)
        body.addWidget(left)

        # 右: 工具列 + 清單
        rv = QVBoxLayout()
        rv.setSpacing(12)
        body.addLayout(rv, 1)

        # 最右: 定時 / 報時 抽屜
        self.drawer = Drawer(self._build_drawer(), 290)
        body.addWidget(self.drawer)

        tool = self._panel()
        tv = QVBoxLayout(tool)
        r1 = QHBoxLayout()
        self.fold_btn = QPushButton("☰")
        self.fold_btn.setObjectName("foldbtn")
        self.fold_btn.setFixedSize(40, 32)
        self.fold_btn.clicked.connect(self._toggle_folders)
        r1.addWidget(self.fold_btn)
        r1.addSpacing(6)
        r1.addWidget(QLabel("排序:"))
        self.sort_cb = QComboBox()
        self.sort_cb.addItems(SORTS)
        self.sort_cb.setCurrentText(self.cfg["sort"])
        self.sort_cb.currentTextChanged.connect(self._sort_changed)
        r1.addWidget(self.sort_cb)
        r1.addSpacing(12)
        r1.addWidget(QLabel("搜尋:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("輸入歌名關鍵字")
        self.search.textChanged.connect(lambda *_: self.apply_sort())
        r1.addWidget(self.search, 1)
        self.clock_btn = QPushButton("⏱")
        self.clock_btn.setObjectName("clockbtn")
        self.clock_btn.setMinimumWidth(96)
        self.clock_btn.setFixedHeight(32)
        self.clock_btn.setToolTip("定時停止 / 語音報時 (往右展開)")
        self.clock_btn.clicked.connect(self._toggle_drawer)
        r1.addWidget(self.clock_btn)
        tv.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("背景:"))
        self.theme_cb = QComboBox()
        self.theme_cb.addItems(list(THEMES))
        self.theme_cb.setCurrentText(self.cfg["theme"])
        self.theme_cb.currentTextChanged.connect(self._theme_changed)
        r2.addWidget(self.theme_cb)
        bwp = QPushButton("🖼 桌布 ▾")
        bwp.setToolTip("選單張桌布 / 清除桌布 / 桌布幻燈片")
        wm = QMenu(bwp)
        wm.addAction("選擇單張桌布…", self.pick_wallpaper)
        wm.addAction("桌布幻燈片…  (資料夾輪流換圖)", self.open_slideshow)
        wm.addSeparator()
        wm.addAction("清除桌布", self.clear_wallpaper)
        bwp.setMenu(wm)
        self._wp_menu = wm
        r2.addWidget(bwp)
        r2.addSpacing(8)
        r2.addWidget(QLabel("深度"))
        self.alpha_sl = QSlider(Qt.Horizontal)
        self.alpha_sl.setRange(0, 100)
        self.alpha_sl.setValue(self.cfg["panel_alpha"])
        self.alpha_sl.setMinimumWidth(70)
        self.alpha_sl.setMaximumWidth(130)
        self.alpha_sl.valueChanged.connect(self._alpha_changed)
        r2.addWidget(self.alpha_sl)
        r2.addStretch(1)
        self.frameless_cb = QCheckBox("無邊框視窗")
        self.frameless_cb.setChecked(self.cfg["frameless"])
        self.frameless_cb.toggled.connect(lambda on: self._set_frameless(on))
        r2.addWidget(self.frameless_cb)
        tv.addLayout(r2)
        rv.addWidget(tool)

        listp = self._panel()
        lv2 = QVBoxLayout(listp)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["#", "歌名", "次數", "跳過"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(False)
        hd = self.tree.header()
        hd.setStretchLastSection(False)
        hd.setDefaultAlignment(Qt.AlignCenter)
        hd.setSectionResizeMode(0, QHeaderView.Fixed)
        hd.setSectionResizeMode(1, QHeaderView.Stretch)
        hd.setSectionResizeMode(2, QHeaderView.Fixed)
        hd.setSectionResizeMode(3, QHeaderView.Fixed)
        self.tree.setColumnWidth(0, 56)
        self.tree.setColumnWidth(2, 70)
        self.tree.setColumnWidth(3, 70)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self.play_index(it.data(0, Qt.UserRole)))
        lv2.addWidget(self.tree)
        rv.addWidget(listp, 1)

        # 下: 音量條(歌名上方) + 歌名 + 控制
        bottom = self._panel()
        self.bottom_panel = bottom
        bv = QVBoxLayout(bottom)
        sr = QHBoxLayout()
        self.cur_lbl = QLabel("0:00")
        self.cur_lbl.setFixedWidth(48)
        self.cur_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sr.addWidget(self.cur_lbl)
        self.seek = SeekSlider()
        self.seek.setToolTip(f"可點擊或拖曳跳轉 (聽滿 {COUNT_AFTER_S} 秒才會計一次播放)")
        self.seek.valueChanged.connect(self._seek_moved)
        self.seek.seeked.connect(self._seek_done)
        sr.addWidget(self.seek, 1)
        self.tot_lbl = QLabel("0:00")
        self.tot_lbl.setFixedWidth(48)
        sr.addWidget(self.tot_lbl)
        bv.addLayout(sr)
        self.now_lbl = QLabel("(未播放)")
        f = self.now_lbl.font()
        f.setPointSize(12)
        f.setBold(True)
        self.now_lbl.setFont(f)
        bv.addWidget(self.now_lbl)
        cr = QHBoxLayout()
        for txt, fn in (("◀◀", self.prev), ("▶ / ❚❚", self.toggle), ("▶▶", self.next)):
            b = QPushButton(txt)
            b.setMinimumWidth(70)
            b.clicked.connect(fn)
            cr.addWidget(b)
        self.cold_btn = QPushButton("📉 從冷門歌開始播")
        self.cold_btn.setMinimumWidth(130)
        self.cold_btn.setToolTip("從「不常聽的歌」開始，照次數由少到多依序播 (會順便把排序切到冷門榜、關閉亂跳)")
        self.cold_btn.clicked.connect(self.play_cold)
        cr.addWidget(self.cold_btn)
        self.rand_btn = QPushButton("🎲 隨機播放")
        self.rand_btn.setMinimumWidth(100)
        self.rand_btn.setToolTip("立刻隨機挑一首播放，之後持續隨機播下去")
        self.rand_btn.clicked.connect(self.random_play)
        cr.addWidget(self.rand_btn)
        self.rep_btn = QPushButton()
        self.rep_btn.setObjectName("repbtn")
        self.rep_btn.setMinimumWidth(96)
        self.rep_btn.clicked.connect(self._cycle_repeat)
        cr.addWidget(self.rep_btn)
        self._update_repeat_ui()
        self.vol_btn = QPushButton("🔊")
        self.vol_btn.setObjectName("volbtn")
        self.vol_btn.setFixedSize(52, 36)
        self.vol_btn.setToolTip("音量 (點一下展開，滑鼠滾輪也可調)")
        self.vol_btn.clicked.connect(self._show_vol_popup)
        cr.addWidget(self.vol_btn)
        self.shuffle_cb = QPushButton("🔀 亂跳")          # 可切換的按鈕 (用法同核取方塊: isChecked / setChecked)
        self.shuffle_cb.setObjectName("shufbtn")
        self.shuffle_cb.setCheckable(True)
        self.shuffle_cb.setMinimumWidth(80)
        self.shuffle_cb.setChecked(self.cfg["shuffle"])
        self.shuffle_cb.setToolTip("亮起時：不照順序播，從你選的那首開始後，下一首會隨機亂跳 (常聽的歌較常被抽到)")
        self.shuffle_cb.toggled.connect(lambda on: self.cfg.__setitem__("shuffle", on))
        cr.addWidget(self.shuffle_cb)
        cr.addStretch(1)
        bv.addLayout(cr)
        wrap = QHBoxLayout()
        wrap.setContentsMargins(14, 0, 14, 14)
        wrap.addWidget(bottom, 1)
        self.grip = QSizeGrip(self)
        self.grip.hide()
        wrap.addWidget(self.grip, 0, Qt.AlignBottom | Qt.AlignRight)
        root.addLayout(wrap)

        # 點喇叭才彈出的垂直音量條 (上=大聲, 下=小聲)
        self.pop = QFrame(self, Qt.Popup)
        self.pop.setObjectName("volpop")
        pv = QVBoxLayout(self.pop)
        pv.setContentsMargins(10, 10, 10, 10)
        self.vol_lbl = QLabel(f"{self.cfg['volume']}%")
        self.vol_lbl.setAlignment(Qt.AlignCenter)
        pv.addWidget(self.vol_lbl)
        self.vol = QSlider(Qt.Vertical)
        self.vol.setRange(0, 100)
        self.vol.setValue(self.cfg["volume"])
        self.vol.valueChanged.connect(self._set_vol)
        pv.addWidget(self.vol, 1, Qt.AlignHCenter)
        self.pop.setFixedSize(64, 210)
        self._update_vol_icon(self.cfg["volume"])

    # ---------------- 外觀 ----------------
    def apply_theme(self):
        th = THEMES[self.cfg["theme"]]
        self.th = th
        r, g, b = th["tint"]
        a = int(255 * self.cfg["panel_alpha"] / 100)
        txt, sub, acc = th["text"], th["sub"], th["accent"]
        self.setStyleSheet(f"""
        * {{ color: {txt}; font-family: "Microsoft JhengHei UI", "Noto Sans CJK TC", "PingFang TC", sans-serif; font-size: 13px; }}
        QFrame#panel {{ background-color: rgba({r},{g},{b},{a}); border-radius: 14px; }}
        QFrame#titlebar {{ background-color: rgba({r},{g},{b},{min(255, a + 60)}); }}
        QLabel {{ background: transparent; }}
        QPushButton {{ background-color: rgba(255,255,255,30); border: none; border-radius: 8px; padding: 7px 14px; }}
        QPushButton:hover {{ background-color: {acc}; color: #ffffff; }}
        QPushButton::menu-indicator {{ image: none; width: 0px; }}
        QPushButton#winbtn {{ padding: 0; border-radius: 6px; }}
        QLineEdit, QComboBox {{ background-color: rgba(0,0,0,70); border: 1px solid rgba(255,255,255,40); border-radius: 8px; padding: 5px 8px; }}
        QComboBox::drop-down {{ border: none; width: 22px; }}
        QComboBox QAbstractItemView {{ background-color: rgb({min(255,r+25)},{min(255,g+25)},{min(255,b+25)}); selection-background-color: {acc}; border: 1px solid rgba(255,255,255,50); color: {txt}; }}
        QMenu {{ background-color: rgb({min(255,r+25)},{min(255,g+25)},{min(255,b+25)}); border: 1px solid rgba(255,255,255,60); padding: 4px; }}
        QMenu::item {{ background: transparent; color: {txt}; padding: 7px 24px; border-radius: 6px; }}
        QMenu::item:selected {{ background-color: {acc}; color: #ffffff; }}
        QMenu::separator {{ height: 1px; background: rgba(255,255,255,50); margin: 4px 8px; }}
        QCheckBox {{ background: transparent; spacing: 8px; }}
        QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {sub}; background: rgba(0,0,0,60); }}
        QCheckBox::indicator:checked {{ background: {acc}; border-color: {acc}; }}
        QListWidget, QTreeWidget {{ background: transparent; border: none; outline: none; }}
        QListWidget::item {{ padding: 6px 4px; }}
        QListWidget::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {sub}; background: rgba(0,0,0,60); }}
        QListWidget::indicator:checked {{ background: {acc}; border-color: {acc}; }}
        QTreeWidget::item {{ padding: 5px 2px; }}
        QTreeWidget::item:selected {{ background-color: {acc}; color: #ffffff; }}
        QTreeWidget::item:hover:!selected {{ background-color: rgba(255,255,255,28); }}
        QHeaderView {{ background: transparent; border: none; }}
        QHeaderView::section {{ background-color: rgba(255,255,255,26); border: none; padding: 6px; color: {sub}; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
        QScrollBar::handle:vertical {{ background: rgba(255,255,255,70); border-radius: 5px; min-height: 30px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
        QFrame#toast {{ background-color: rgb({min(255,r+30)},{min(255,g+30)},{min(255,b+30)}); border: 1px solid {acc}; border-radius: 12px; }}
        QLabel#toasttxt {{ background: transparent; font-weight: bold; }}
        QFrame#volpop {{ background-color: rgb({min(255,r+22)},{min(255,g+22)},{min(255,b+22)}); border: 1px solid rgba(255,255,255,70); border-radius: 12px; }}
        QPushButton#volbtn {{ font-size: 18px; padding: 0; }}
        QLabel#sub {{ color: {sub}; }}
        QProgressBar {{ background: rgba(255,255,255,40); border: none; border-radius: 4px; max-height: 8px; }}
        QProgressBar::chunk {{ background: {acc}; border-radius: 4px; }}
        QDialog {{ background-color: rgb({min(255,r+22)},{min(255,g+22)},{min(255,b+22)}); }}
        QTreeWidget::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {sub}; background: rgba(0,0,0,60); }}
        QTreeWidget::indicator:checked {{ background: {acc}; border-color: {acc}; }}
        QLabel#dtitle {{ font-size: 16px; font-weight: bold; }}
        QLabel#bigclock {{ font-size: 36px; font-weight: bold; }}
        QLabel#wxplace {{ font-size: 15px; font-weight: bold; }}
        QLabel#wxnow {{ font-size: 24px; font-weight: bold; }}
        QPushButton#clockbtn[rep="1"] {{ background-color: {acc}; color: #ffffff; }}
        QPushButton#shufbtn:checked {{ background-color: {acc}; color: #ffffff; }}
        QPushButton#foldbtn {{ font-size: 18px; padding: 0; }}
        QPushButton#foldbtn[rep="1"] {{ background-color: {acc}; color: #ffffff; }}
        QPushButton#repbtn[rep="1"], QPushButton#repbtn[rep="2"] {{ background-color: {acc}; color: #ffffff; }}
        QSlider::groove:vertical {{ width: 6px; background: rgba(255,255,255,50); border-radius: 3px; }}
        QSlider::add-page:vertical {{ background: {acc}; border-radius: 3px; }}
        QSlider::sub-page:vertical {{ background: rgba(255,255,255,0); }}
        QSlider::handle:vertical {{ background: #ffffff; width: 16px; height: 16px; margin: 0 -5px; border-radius: 8px; }}
        QSlider::groove:horizontal {{ height: 6px; background: rgba(255,255,255,50); border-radius: 3px; }}
        QSlider::sub-page:horizontal {{ background: {acc}; border-radius: 3px; }}
        QSlider::handle:horizontal {{ background: #ffffff; width: 16px; height: 16px; margin: -5px 0; border-radius: 8px; }}
        """)
        self._bg_cache = None
        self.update()

    def _theme_changed(self, name):
        self.cfg["theme"] = name
        save_cfg(self.cfg)
        self.apply_theme()

    def _alpha_changed(self, v):
        self.cfg["panel_alpha"] = v
        save_cfg(self.cfg)
        self.apply_theme()

    def _on_fade(self, v):
        self._fade = float(v)
        self.update()

    def _fade_done(self):
        self._fade, self._wp_prev = 1.0, None
        self.update()

    def pick_wallpaper(self):
        f, _ = QFileDialog.getOpenFileName(self, "選擇桌布圖片", "", "圖片 (*.jpg *.jpeg *.png *.bmp *.webp);;全部 (*.*)")
        if f:
            self.cfg["wallpaper"] = f
            self.cfg["wp_slide"] = False                   # 自己挑了單張，就停掉幻燈片
            save_cfg(self.cfg)
            self._bg_cache = None
            self.update()

    def clear_wallpaper(self):
        self.cfg["wallpaper"] = ""
        self.cfg["wp_slide"] = False
        save_cfg(self.cfg)
        self._bg_cache = None
        self.update()

    def resizeEvent(self, e):
        self._bg_cache = None
        super().resizeEvent(e)
        if hasattr(self, "toast") and self.toast.isVisible():
            self.toast.place()

    def _background(self):
        w, h = self.width(), self.height()
        if self._bg_cache is not None and self._bg_cache.size() == QSize(w, h):
            return self._bg_cache
        wp = self.cfg.get("wallpaper", "")
        img = None
        if wp and os.path.exists(wp):
            if self._wp_path != wp:
                self._wp_src = QPixmap(wp)
                self._wp_path = wp
            if self._wp_src is not None and not self._wp_src.isNull():
                sc = self._wp_src.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                x, y = (sc.width() - w) // 2, (sc.height() - h) // 2
                img = sc.copy(x, y, w, h).toImage().convertToFormat(QImage.Format_RGB32)
        if img is None and self.th.get("stars"):
            img = starfield(w, h)
        if img is None:
            img = QImage(w, h, QImage.Format_RGB32)
            img.fill(QColor(*self.th["base"]))
        self._bg_cache = img
        return img

    def paintEvent(self, e):
        p = QPainter(self)
        bg = self._background()
        if self._wp_prev is not None and self._fade < 1.0 and self._wp_prev.size() == bg.size():
            p.drawImage(0, 0, self._wp_prev)               # 淡入淡出: 先畫舊圖，再用漸增的不透明度疊上新圖
            p.setOpacity(self._fade)
            p.drawImage(0, 0, bg)
            p.setOpacity(1.0)
        else:
            p.drawImage(0, 0, bg)
        if self.cfg.get("wallpaper"):
            p.fillRect(self.rect(), QColor(0, 0, 0, 40))   # 輕微壓暗，讓字清楚
        p.end()

    # ---- 無邊框 ----
    def _set_frameless(self, on, first=False):
        self.cfg["frameless"] = on
        save_cfg(self.cfg)
        geo = self.geometry()
        self.setWindowFlag(Qt.FramelessWindowHint, on)
        self.titlebar.setVisible(on)
        self.grip.setVisible(on)
        self.setGeometry(geo)
        self.show()

    def mousePressEvent(self, e):
        if self.cfg["frameless"] and e.button() == Qt.LeftButton and e.position().y() < self.titlebar.height() + 6:
            h = self.windowHandle()
            if h:
                h.startSystemMove()
        super().mousePressEvent(e)

    # ---------------- 資料夾 / 清單 ----------------
    def _load_folders(self):
        self.folder_list.blockSignals(True)
        self.folder_list.clear()
        for f, on in self.cfg["folders"].items():
            it = QListWidgetItem(f + ('   ⚠ 路徑損毀，請移除後重新加入' if '\ufffd' in f else ''))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if on else Qt.Unchecked)
            it.setToolTip(f)
            it.setData(Qt.UserRole, f)          # 真正的路徑 (畫面上的文字可能多了警告)
            self.folder_list.addItem(it)
        self.folder_list.blockSignals(False)

    def _folder_toggled(self, _it):
        for i in range(self.folder_list.count()):
            it = self.folder_list.item(i)
            self.cfg["folders"][it.data(Qt.UserRole) or it.text()] = it.checkState() == Qt.Checked
        save_cfg(self.cfg)
        self.refresh_tracks()

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "選擇音樂資料夾")
        if d:
            self.cfg["folders"][os.path.normpath(d)] = True
            save_cfg(self.cfg)
            self._load_folders()
            self.refresh_tracks()

    def remove_unchecked(self):
        for f, on in list(self.cfg["folders"].items()):
            if not on:
                del self.cfg["folders"][f]
        save_cfg(self.cfg)
        self._load_folders()
        self.refresh_tracks()

    def refresh_tracks(self):
        self.all_tracks = []
        for f, on in self.cfg["folders"].items():
            if on and os.path.isdir(f):
                for p in sorted(scan(f)):
                    fid = self.stats.fid(p)
                    self.stats.touch(fid, p)
                    self.all_tracks.append((fid, p))
        self.stats.db.commit()
        self._filter_tracks()

    def _filter_tracks(self):
        hidden = set(self.cfg["hidden_paths"])
        self.tracks = [t for t in self.all_tracks if t[1] not in hidden]
        n = len(self.all_tracks) - len(self.tracks)
        self.hid_btn.setText(f"🙈 隱藏清單 ({n})")
        self.apply_sort()

    def _update_src(self):
        return (self.cfg["update_src"] or default_src()).strip()

    def _auto_check(self):
        src = self._update_src()
        if self.cfg["update_auto"] and src and not self.toast.isVisible():
            self.updater.check(src, "auto")

    def open_update(self):
        self._upd_dlg = UpdateDialog(self)
        self._upd_dlg.exec()
        self._upd_dlg = None

    def _update_result(self, tag, res):
        if tag == "manual":
            if self._upd_dlg:
                self._upd_dlg.on_result(res)
            return
        # 啟動時自動檢查: 有新版才打擾，失敗就靜靜略過
        if res.get("ok") and res["version"] > VERSION and res["version"] != self.cfg["update_skip"]:
            self.toast.show_for(res)          # 左下角小提示框 (按「是」才更新)

    def install_update(self, res, parent, quiet=False):
        self.toast.hide()
        dlg = UpdateProgress(self, res)
        if dlg.exec() == QDialog.Accepted and dlg.ok:
            self._restart()
        elif self._upd_dlg:
            self._upd_dlg.status.setText("更新失敗，目前版本不受影響。")

    def _restart(self):
        save_cfg(self.cfg)
        try:
            if FROZEN:
                spawn_clean(SCRIPT)
            else:
                subprocess.Popen([sys.executable, SCRIPT])
        except Exception:
            pass
        self._quitting = True
        self.close()

    # ---------------- 下載音樂 ----------------
    def open_download(self):
        if not self.cfg.get("dl_ack"):
            ok = QMessageBox.question(
                self, "下載音樂 (進階功能)",
                "這是進階功能，不保證在每一台電腦都能用 (需要另外下載 yt-dlp 和 ffmpeg，可能被防毒或網路擋住)。\n\n"
                "下載內容是否符合網站規定與著作權，請自行判斷，使用上的後果由使用者自己負責。\n\n"
                "要繼續嗎？", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes
            if not ok:
                return
            self.cfg["dl_ack"] = True
            save_cfg(self.cfg)
        if self._dl_dlg is None:
            self._dl_dlg = DownloadDialog(self)
        self._dl_dlg.show()
        self._dl_dlg.raise_()
        self._dl_dlg.activateWindow()
        QTimer.singleShot(300, self._dl_dlg.ensure_tools)      # 缺 yt-dlp / ffmpeg 就自動下載

    def after_download(self, path):
        """下載完成: 資料夾還沒在清單裡就加進去 (已被勾選的上層資料夾涵蓋就不用)，然後重新掃描"""
        path = os.path.normpath(path)
        key = os.path.normcase(path)
        covered = any(on and (key == os.path.normcase(f) or key.startswith(os.path.normcase(f) + os.sep))
                      for f, on in self.cfg["folders"].items())
        if not covered:
            self.cfg["folders"][path] = True
            save_cfg(self.cfg)
            self._load_folders()
        self.refresh_tracks()

    # ---------------- 桌布幻燈片 ----------------
    def open_slideshow(self):
        SlideshowDialog(self).exec()

    def set_wallpaper(self, path, fade=True):
        prev = self._background() if (fade and self.cfg["wp_fade"] and self.isVisible()) else None
        self.cfg["wallpaper"] = path
        self._bg_cache = None
        self._fade_anim.stop()
        if prev is not None:
            self._wp_prev = prev
            self._fade = 0.0
            self._fade_anim.setStartValue(0.0)
            self._fade_anim.setEndValue(1.0)
            self._fade_anim.start()
        else:
            self._wp_prev, self._fade = None, 1.0
        self.update()

    def _slideshow_changed(self):
        """設定改變或啟動時: 重新掃描圖片、重設計時"""
        d = self.cfg["wp_dir"]
        if self.cfg["wp_source"] == "picked":
            self._wp_list = [p for p in self.cfg["wp_files"] if os.path.isfile(p)]     # 照使用者排的順序
        else:
            self._wp_list = scan_images(d) if d and os.path.isdir(d) else []
        cur = self.cfg.get("wallpaper", "")
        self._wp_idx = self._wp_list.index(cur) if cur in self._wp_list else -1
        self._reset_slide_timer()

    def _reset_slide_timer(self):
        sec = WP_INTERVAL.get(self.cfg["wp_mode"])
        self._next_slide = time.time() + sec if sec else 0.0

    def next_wallpaper(self):
        if not self._wp_list:
            return False
        n = len(self._wp_list)
        if self.cfg["wp_shuffle"] and n > 1:
            i = random.choice([k for k in range(n) if k != self._wp_idx])
        else:
            i = (self._wp_idx + 1) % n
        self._wp_idx = i
        self.set_wallpaper(self._wp_list[i])
        self._reset_slide_timer()
        save_cfg(self.cfg)
        return True

    def _slide_tick(self):
        if self.cfg["wp_slide"] and self._next_slide and time.time() >= self._next_slide:
            self.next_wallpaper()

    def _slide_on_song(self):
        if self.cfg["wp_slide"] and self.cfg["wp_mode"] == WP_MODES[0]:
            self.next_wallpaper()

    def open_hidden(self):
        HiddenDialog(self).exec()

    def find_dups(self):
        dlg = DupDialog(self, self.all_tracks, set(self.cfg["hidden_paths"]), self.stats)
        if not dlg.groups:
            QMessageBox.information(self, "找重複歌曲", "沒有找到重複的歌曲。")
            return
        if dlg.exec():
            self.cfg["hidden_paths"] = dlg.checked()
            save_cfg(self.cfg)
            self._filter_tracks()

    def _sort_changed(self, m):
        self.cfg["sort"] = m
        save_cfg(self.cfg)
        self.apply_sort()

    def apply_sort(self):
        mode = self.cfg["sort"]
        q = self.search.text().strip().lower()
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
        elif mode == COLD_SORT:
            # 次數少的在前；次數一樣的，這次開程式期間用固定的隨機順序，避免每次都從同幾首開始
            items.sort(key=lambda t: (info[t[0]][0], self._rk.setdefault(t[0], random.random())))
        self.view = items
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        for i, (fid, p) in enumerate(items):
            plays, skips, _ = info[fid]
            it = QTreeWidgetItem([str(i + 1), os.path.splitext(os.path.basename(p))[0], str(plays), str(skips)])
            it.setData(0, Qt.UserRole, i)
            for c in (0, 2, 3):
                it.setTextAlignment(c, Qt.AlignCenter)
            self.tree.addTopLevelItem(it)
        self.tree.setUpdatesEnabled(True)

    # ---------------- 播放 ----------------
    def play_index(self, idx, auto=False):
        if not (isinstance(idx, int) and 0 <= idx < len(self.view)):
            return
        if not auto:
            self._finish_current(skipped=True)
        fid, path = self.view[idx]
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
        except Exception as e:
            self.now_lbl.setText(f"無法播放: {os.path.basename(path)} ({e})")
            return
        self.cur, self.play_start, self.last_tick = (fid, path), 0.0, time.time()
        self.counted = self.paused = False
        self.pos = 0.0
        self.length = track_length(path)
        self.tot_lbl.setText(fmt(self.length) if self.length else "--:--")
        self.seek.blockSignals(True)
        self.seek.setValue(0)
        self.seek.blockSignals(False)
        self.now_lbl.setText("正在播放: " + os.path.splitext(os.path.basename(path))[0])
        if getattr(self, "tray", None):
            self.tray.setToolTip(os.path.splitext(os.path.basename(path))[0][:60])
        self._slide_on_song()
        it = self.tree.topLevelItem(idx)
        if it:
            self.tree.setCurrentItem(it)
            self.tree.scrollToItem(it)

    def _finish_current(self, skipped):
        if self.cur and not self.counted and skipped and self.play_start < SKIP_BEFORE_S:
            self.stats.add_skip(self.cur[0])

    def _build_drawer(self):
        inner = self._panel()
        outer = QVBoxLayout(inner)
        outer.setContentsMargins(0, 4, 0, 4)
        scroll = QScrollArea()                       # 內容變多時可以上下捲動，不會把視窗最小高度撐大
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;} QScrollArea>QWidget>QWidget{background:transparent;}")
        content = QWidget()
        content.setObjectName("dcontent")
        scroll.setWidget(content)
        outer.addWidget(scroll)
        v = QVBoxLayout(content)
        v.setContentsMargins(14, 8, 14, 8)
        v.setSpacing(6)
        t = QLabel("定時 / 報時")
        t.setObjectName("dtitle")
        v.addWidget(t)

        self.big_clock = QLabel("--:--:--")
        self.big_clock.setAlignment(Qt.AlignCenter)
        self.big_clock.setObjectName("bigclock")
        v.addWidget(self.big_clock)
        self.date_lbl = QLabel("")
        self.date_lbl.setAlignment(Qt.AlignCenter)
        self.date_lbl.setObjectName("sub")
        v.addWidget(self.date_lbl)
        v.addSpacing(8)

        # --- 天氣 ---
        v.addWidget(self._sec("天氣"))
        self.wx_place = QLabel("📍 尚未設定地區")
        self.wx_place.setObjectName("wxplace")
        self.wx_now = QLabel("")
        self.wx_now.setObjectName("wxnow")
        self.wx_now.setTextFormat(Qt.RichText)
        self.wx_range = QLabel("")
        self.wx_range.setWordWrap(True)
        self.wx_days = QLabel("")
        self.wx_days.setObjectName("sub")
        for w in (self.wx_place, self.wx_now, self.wx_range, self.wx_days):
            v.addWidget(w)
        rw = QHBoxLayout()
        wb = QPushButton("☁ 天氣設定…")
        wb.clicked.connect(self.open_weather)
        rf = QPushButton("⟳")
        rf.setFixedWidth(40)
        rf.setToolTip("立即更新天氣")
        rf.clicked.connect(lambda: (setattr(self, "_wx_err", ""), self.refresh_weather(True)))
        rw.addWidget(wb, 1)
        rw.addWidget(rf)
        v.addLayout(rw)
        v.addWidget(link_label("", "https://opendata.cwa.gov.tw/", "資料: 中央氣象署"))
        v.addSpacing(8)


        # --- 定時停止 ---
        v.addWidget(self._sec("定時停止播放"))
        self.sl_min = NumBox(30, 1, 720)
        self.sl_at = QLineEdit((datetime.datetime.now() + datetime.timedelta(hours=1)).strftime("%H:00"))
        self.sl_at.setInputMask("00:00"); self.sl_at.setFixedWidth(70)
        self.sl_cnt = NumBox(3, 1, 999)
        self.btn_min, self.st_min = self._timer_row(v, self.sl_min, "分鐘後停止", self._toggle_min_timer)
        self.btn_at, self.st_at = self._timer_row(v, QLabel("到"), self.sl_at, self._toggle_at_timer, "停止")
        self.btn_cnt, self.st_cnt = self._timer_row(v, self.sl_cnt, "首播完後停止", self._toggle_cnt_timer)
        self.stop_at = None          # epoch 秒: 倒數/指定時間 到就停止
        self.stop_after_songs = 0    # 剩餘首數 (0=未啟用)
        v.addSpacing(8)

        # --- 語音報時 ---
        v.addWidget(self._sec("語音報時"))
        self.cb_hour = QCheckBox("整點報時")
        self.cb_half = QCheckBox("半點報時")
        self.cb_ampm = QCheckBox("加上 上午／下午／晚上")
        self.cb_hour.setChecked(self.cfg["chime_hour"])
        self.cb_half.setChecked(self.cfg["chime_half"])
        self.cb_ampm.setChecked(self.cfg["chime_ampm"])
        for cb, key in ((self.cb_hour, "chime_hour"), (self.cb_half, "chime_half"), (self.cb_ampm, "chime_ampm")):
            cb.toggled.connect(lambda on, k=key: self.cfg.__setitem__(k, on))
            v.addWidget(cb)
        self.cb_wx = QCheckBox("報時時一併報氣溫")
        self.cb_wx.setChecked(self.cfg["chime_weather"])
        self.cb_wx.toggled.connect(lambda on: self.cfg.__setitem__("chime_weather", on))
        v.addWidget(self.cb_wx)
        v.addWidget(self._sec("聲音 (只列女聲)"))
        self.voice_cb = QComboBox()
        self.voice_cb.addItem(VOICE_AUTO)
        if edge_available():
            for k in EDGE_VOICES:
                self.voice_cb.addItem(k)
        self.voice_cb.currentTextChanged.connect(lambda t: self.cfg.__setitem__("voice", "" if t == VOICE_AUTO else t))
        v.addWidget(self.voice_cb)
        rr = QHBoxLayout()
        rr.addWidget(QLabel("語速"))
        self.rate_sl = QSlider(Qt.Horizontal)
        self.rate_sl.setRange(-5, 5)
        self.rate_sl.setValue(int(self.cfg["voice_rate"]))
        self.rate_lbl = QLabel("")
        self.rate_lbl.setObjectName("sub")
        self.rate_sl.valueChanged.connect(self._rate_changed)
        rr.addWidget(self.rate_sl, 1)
        rr.addWidget(self.rate_lbl)
        v.addLayout(rr)
        self._rate_changed(self.rate_sl.value())
        b = QPushButton("🔊 試聽目前時間")
        b.clicked.connect(lambda: self._announce(test=True))
        v.addWidget(b)
        self.speak_lbl = QLabel("")
        self.speak_lbl.setObjectName("sub")
        self.speak_lbl.setWordWrap(True)
        v.addWidget(self.speak_lbl)
        v.addStretch(1)

        self.speaker = Speaker()
        self.speaker.done.connect(self._speak_done)
        self.speaker.voices.connect(self._voices_loaded)
        self.speaker.list_voices()
        self._speaking = False
        self._duck_restore = None
        now = datetime.datetime.now()
        self._last_chime = (now.hour, now.minute)     # 啟動當下那一分鐘不報
        self.net = Net()
        self.net.res.connect(self._net_result)
        self.weather, self._wx_busy, self._wx_err, self._wx_dlg = None, False, "", None
        self._wx_next = time.time() + 3               # 開啟後幾秒就先抓一次天氣
        self._show_weather()
        return inner

    def _rate_changed(self, val):
        self.cfg["voice_rate"] = int(val)
        self.rate_lbl.setText("標準" if val == 0 else (f"快 {val}" if val > 0 else f"慢 {-val}"))

    def _voices_loaded(self, names):
        """Windows 內建女聲偵測完成: 加進選單，並選回上次用的聲音。"""
        for n in names:
            if self.voice_cb.findText(n) < 0:
                self.voice_cb.addItem(n)
        cur = self.cfg.get("voice", "")
        i = self.voice_cb.findText(cur) if cur else 0
        self.voice_cb.blockSignals(True)
        self.voice_cb.setCurrentIndex(max(i, 0))
        self.voice_cb.blockSignals(False)
        self._have_sapi_female = bool(names)

    def _sec(self, txt):
        l = QLabel(txt)
        l.setObjectName("sub")
        return l

    def _timer_row(self, v, w1, w2, fn, suffix=None):
        row = QHBoxLayout()
        row.addWidget(w1)
        if isinstance(w2, str):
            suffix, w2 = w2, None
        if w2:
            row.addWidget(w2)
        if suffix:
            row.addWidget(QLabel(suffix))
        row.addStretch(1)
        b = QPushButton("開始")
        b.setFixedWidth(64)
        b.clicked.connect(fn)
        row.addWidget(b)
        v.addLayout(row)
        st = QLabel("")
        st.setObjectName("sub")
        v.addWidget(st)
        return b, st

    def _toggle_drawer(self):
        self.cfg["drawer_open"] = not self.cfg["drawer_open"]
        self._apply_drawer()

    def _apply_drawer(self, animate=True):
        on = self.cfg["drawer_open"]
        self.drawer.slide(on, animate)
        self.clock_btn.setProperty("rep", "1" if on else "0")
        self.clock_btn.style().unpolish(self.clock_btn)
        self.clock_btn.style().polish(self.clock_btn)

    # ---- 定時停止 ----
    def _cancel_timers(self):
        self.stop_at = None
        self.stop_after_songs = 0
        for b, st in ((self.btn_min, self.st_min), (self.btn_at, self.st_at), (self.btn_cnt, self.st_cnt)):
            b.setText("開始")
            st.setText("")

    def _toggle_min_timer(self):
        if self.btn_min.text() == "取消":
            self._cancel_timers()
            return
        self._cancel_timers()
        self.stop_at = time.time() + self.sl_min.value() * 60
        self.btn_min.setText("取消")

    def _toggle_at_timer(self):
        if self.btn_at.text() == "取消":
            self._cancel_timers()
            return
        try:
            hh, mm = (int(x) for x in self.sl_at.text().split(":"))
            assert 0 <= hh < 24 and 0 <= mm < 60
        except Exception:
            self.st_at.setText("請輸入 00:00 ~ 23:59")
            return
        self._cancel_timers()
        now = datetime.datetime.now()
        tgt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if tgt <= now:
            tgt += datetime.timedelta(days=1)
        self.stop_at = tgt.timestamp()
        self.btn_at.setText("取消")

    def _toggle_cnt_timer(self):
        if self.btn_cnt.text() == "取消":
            self._cancel_timers()
            return
        self._cancel_timers()
        self.stop_after_songs = self.sl_cnt.value()
        self.btn_cnt.setText("取消")

    def _timed_stop(self, why):
        """時間到 / 首數到: 淡出後停止播放"""
        self._cancel_timers()
        try:
            pygame.mixer.music.fadeout(3000)
        except Exception:
            pass
        self.cur = None
        self.paused = False
        self.now_lbl.setText(f"({why}，已停止播放)")

    # ---- 時鐘 / 報時 ----
    def _clock_tick(self):
        self._slide_tick()
        self._wx_tick()
        dt = datetime.datetime.now()
        self.clock_btn.setText("⏱ " + dt.strftime("%H:%M"))
        self.big_clock.setText(dt.strftime("%H:%M:%S"))
        self.date_lbl.setText(dt.strftime("%Y-%m-%d") + " 週" + WEEK[dt.weekday()])
        # 定時停止: 倒數 / 指定時間
        if self.stop_at is not None:
            left = int(self.stop_at - time.time())
            if left <= 0:
                self._timed_stop("時間到")
            else:
                txt = f"剩餘 {left // 60}:{left % 60:02d}"
                (self.st_min if self.btn_min.text() == "取消" else self.st_at).setText(txt)
        if self.stop_after_songs:
            self.st_cnt.setText(f"還會播完 {self.stop_after_songs} 首")
        # 整點 / 半點報時
        key = (dt.hour, dt.minute)
        if dt.minute in (0, 30) and key != self._last_chime:
            self._last_chime = key
            if (dt.minute == 0 and self.cfg["chime_hour"]) or (dt.minute == 30 and self.cfg["chime_half"]):
                self._announce()

    def _time_text(self, dt):
        h12 = dt.hour % 12 or 12
        pre = ""
        if self.cfg["chime_ampm"]:
            h = dt.hour
            pre = "凌晨" if h < 5 else "上午" if h < 12 else "中午" if h == 12 else "下午" if h < 18 else "晚上"
        if dt.minute == 0:
            return f"現在是{pre}{h12}點"
        if dt.minute == 30:
            return f"現在是{pre}{h12}點半"
        return f"現在是{pre}{h12}點{dt.minute}分"        # 只有「試聽」會走到這裡

    def _announce(self, test=False):
        if self._speaking:
            return
        self._speaking = True
        # 報時時把音樂壓低，講完再恢復
        try:
            pygame.mixer.music.set_volume(self.cfg["volume"] / 100 * 0.25)
        except Exception:
            pass
        text = self._time_text(datetime.datetime.now()) + self._weather_phrase()
        note = ""
        if self.cfg["chime_weather"] and not self._weather_phrase():
            if not (self.cfg["weather_name"] or "").strip():
                note = "\n(還沒設定地區名稱，所以只報時間)" if self._has_place() else "\n(還沒設定地區，所以只報時間；可按「☁ 天氣設定」)"
            elif not self.weather:
                note = "\n(天氣還沒讀到，這次只報時間)"
        self.speak_lbl.setText("播報: " + text + note)
        self.speaker.speak(text, self.cfg.get("voice", ""), int(self.cfg.get("voice_rate", 0)))

    def _speak_done(self, code):
        self._speaking = False
        try:
            pygame.mixer.music.set_volume(self.cfg["volume"] / 100)
        except Exception:
            pass
        if code == 5:
            self.speak_lbl.setText("線上女聲連不上，這次改用 Windows 內建女聲。")
        elif code == 3:
            self.speak_lbl.setText("找不到中文女聲。請到 Windows 設定 > 時間與語言 > 語音，安裝「中文(台灣)」語音；或安裝 edge-tts 使用線上女聲。")
        elif code != 0:
            self.speak_lbl.setText("語音播報失敗 (需要 Windows 內建 PowerShell 與語音功能)。")

    # ---------------- 天氣 ----------------
    def open_weather(self):
        self._wx_dlg = WeatherDialog(self)
        self._wx_dlg.exec()
        self._wx_dlg = None

    def _net_result(self, tag, out):
        if tag == "wx":
            self._weather_done(out)
        elif self._wx_dlg is not None:
            self._wx_dlg.on_net(tag, out)

    def _has_place(self):
        return self.cfg["weather_lat"] is not None and self.cfg["weather_lon"] is not None

    def refresh_weather(self, force=False):
        if not self._has_place():
            self._show_weather()
            return
        if self._wx_busy:
            return
        self._wx_busy = True
        self.net.run("wx", fetch_weather, float(self.cfg["weather_lat"]), float(self.cfg["weather_lon"]))

    def _weather_done(self, out):
        self._wx_busy = False
        kind, val = out
        if kind == "ok":
            self.weather, self._wx_err = val, ""
            if not (self.cfg["weather_name"] or "").strip() and val.get("place"):      # 自動定位後名稱是空的: 用氣象站的縣市/鄉鎮補上
                self.cfg["weather_name"] = val["place"]
                save_cfg(self.cfg)
                if self._wx_dlg is not None:
                    self._wx_dlg.name.setText(val["place"])
            self._wx_next = time.time() + 1800          # 每 30 分鐘更新一次
        else:
            self._wx_err = val
            self._wx_next = time.time() + 300           # 失敗: 5 分鐘後再試
        self._show_weather()

    def _wx_tick(self):
        if self._has_place() and not self._wx_busy and time.time() >= self._wx_next:
            self._wx_next = time.time() + 60            # 先擋一下，避免重複觸發；結果回來會重設
            self.refresh_weather()

    def _show_weather(self):
        name = (self.cfg["weather_name"] or "").strip()
        if not self._has_place():
            self.wx_place.setText("📍 尚未設定地區")
            self.wx_now.setText("")
            self.wx_range.setText("按下面「☁ 天氣設定」選擇地區")
            self.wx_days.setText("")
            return
        self.wx_place.setText("📍 " + (name or "(未命名地區)"))
        wx = self.weather
        if not wx:
            self.wx_now.setText("")
            self.wx_range.setText(("⚠️ 讀取失敗: " + self._wx_err + "\n稍後會自動重試") if self._wx_err else "讀取天氣中...")
            self.wx_days.setText("")
            return

        def deg(x):
            return "?" if x is None else f"{round(x)}°"
        d0 = wx["days"][0]
        t = wx.get("temp")
        self.wx_now.setText(f"{wx_icon(wx.get('code'))} {('?' if t is None else round(t))}°C"
                            f"<br><span style='font-size:13px; font-weight:normal'>{wx.get('text') or wx_text(wx.get('code'))}</span>")
        rng = f"今天 {deg(d0['tmin'])} ~ {deg(d0['tmax'])}"
        if d0.get("pop") is not None:
            rng += f"　降雨機率 {round(d0['pop'])}%"
        self.wx_range.setText(rng)
        names = ("今天", "明天", "後天")
        self.wx_days.setText("\n".join(
            f"{names[i]} {deg(d['tmin'])}~{deg(d['tmax'])}　{wx_icon(d['code'])} {d.get('text') or wx_text(d['code'])}"
            for i, d in enumerate(wx["days"][1:3], start=1)))

    @staticmethod
    def _deg_words(n):
        return f"零下{-n}度" if n < 0 else f"{n}度"

    def _weather_phrase(self):
        """報時後面接的氣溫那一句，例如「，二崙鄉今天氣溫22度到28度」。沒設定地區、或沒讀到天氣就回傳空字串。"""
        if not self.cfg["chime_weather"]:
            return ""
        wx, name = self.weather, (self.cfg["weather_name"] or "").strip()
        if not wx or not name or time.time() - wx["ts"] > 7200:
            return ""
        d = wx["days"][0]
        if d["tmin"] is not None and d["tmax"] is not None:
            lo, hi = round(d["tmin"]), round(d["tmax"])
            rng = self._deg_words(hi) if lo == hi else f"{self._deg_words(lo)}到{self._deg_words(hi)}"
        elif wx.get("temp") is not None:
            rng = self._deg_words(round(wx["temp"]))
        else:
            return ""
        s = f"，{name}今天氣溫{rng}"
        if self.cfg["chime_weather_extra"]:
            s += f"，{d.get('text') or wx_text(d['code'])}"
            if d.get("pop") is not None and d["pop"] >= 20:
                s += f"，降雨機率百分之{round(d['pop'])}"
        return s

    def _toggle_folders(self):
        self.cfg["folders_open"] = not self.cfg["folders_open"]
        self._apply_folders_open()

    def _apply_folders_open(self):
        on = self.cfg["folders_open"]
        self.left_panel.setVisible(on)      # 收起後，右邊的區塊會自動往左擴張填滿
        self.fold_btn.setToolTip("收起資料夾面板" if on else "展開資料夾面板")
        self.fold_btn.setProperty("rep", "1" if on else "0")
        self.fold_btn.style().unpolish(self.fold_btn)
        self.fold_btn.style().polish(self.fold_btn)

    def _cycle_repeat(self):
        # 不循環 -> 按1下:全部循環 -> 按2下:單曲循環 -> 按3下:回到不循環
        self.cfg["repeat"] = (self.cfg["repeat"] + 1) % 3
        self._update_repeat_ui()

    def _update_repeat_ui(self):
        m = self.cfg["repeat"]
        self.rep_btn.setText(("🔁 不循環", "🔁 全部循環", "🔂 單曲循環")[m])
        self.rep_btn.setToolTip("重複播放: 按1下=全部循環，按2下=單曲循環，再按=關閉")
        self.rep_btn.setProperty("rep", str(m))
        self.rep_btn.style().unpolish(self.rep_btn)
        self.rep_btn.style().polish(self.rep_btn)

    def _auto_next(self):
        """歌曲自然播完時呼叫 (手動按上一首/下一首不受單曲循環影響)"""
        if self.stop_after_songs:
            self.stop_after_songs -= 1
            if self.stop_after_songs == 0:
                self._timed_stop("已播完設定的首數")
                return
        idx = self._cur_index()
        m = self.cfg["repeat"]
        if m == 2 and idx >= 0:
            self.play_index(idx, auto=True)
            return
        if m == 0 and idx == len(self.view) - 1 and not self.shuffle_cb.isChecked():
            self.cur = None            # 清單播完，停止
            self.now_lbl.setText("(清單播放完畢)")
            return
        self.next()

    def _cur_index(self):
        for i, (fid, _) in enumerate(self.view):
            if self.cur and fid == self.cur[0]:
                return i
        return -1

    def next(self):
        if not self.view:
            return
        if self.shuffle_cb.isChecked():
            cur = self._cur_index()
            pool = [i for i in range(len(self.view)) if i != cur] or [0]   # 盡量不連續抽到同一首
            w = [1 + self.stats.info(self.view[i][0])[0] for i in pool]
            self.play_index(random.choices(pool, weights=w)[0])
        else:
            self.play_index((self._cur_index() + 1) % len(self.view))

    def play_cold(self):
        """從不常聽的歌開始播: 切到冷門榜、關掉亂跳，從最冷門的第一首依序播下去"""
        if not self.tracks:
            self.now_lbl.setText("清單是空的，請先勾選音樂資料夾")
            return
        self.search.setText("")
        self.shuffle_cb.setChecked(False)
        if self.sort_cb.currentText() != COLD_SORT:
            self.sort_cb.setCurrentText(COLD_SORT)      # 會觸發 _sort_changed -> 重新排序
        else:
            self.apply_sort()
        self.play_index(0)

    def random_play(self):
        """按下去: 開啟隨機模式並立刻隨機挑一首播，之後自動一直隨機播"""
        if not self.view:
            self.now_lbl.setText("清單是空的，請先勾選音樂資料夾")
            return
        self.shuffle_cb.setChecked(True)
        self.next()

    def prev(self):
        if self.view:
            self.play_index((self._cur_index() - 1) % len(self.view))

    def toggle(self):
        if not self.cur:
            it = self.tree.currentItem()
            self.play_index(it.data(0, Qt.UserRole) if it else 0)
            return
        if self.paused:
            pygame.mixer.music.unpause()
            self.last_tick = time.time()
        else:
            pygame.mixer.music.pause()
        self.paused = not self.paused

    def _update_vol_icon(self, v):
        self.vol_btn.setText("🔇" if v == 0 else "🔈" if v < 34 else "🔉" if v < 67 else "🔊")

    def _set_vol(self, v):
        pygame.mixer.music.set_volume(v / 100)
        self.vol_lbl.setText(f"{v}%")
        self._update_vol_icon(v)
        self.cfg["volume"] = v

    def _show_vol_popup(self):
        b = self.vol_btn
        pos = b.mapToGlobal(QPoint(b.width() // 2 - self.pop.width() // 2, -self.pop.height() - 6))
        self.pop.move(pos)
        self.pop.show()

    def wheelEvent(self, e):
        if self.vol_btn.underMouse():
            self.vol.setValue(self.vol.value() + (5 if e.angleDelta().y() > 0 else -5))
            e.accept()
            return
        super().wheelEvent(e)

    def _seek_moved(self, v):
        if self.seek.dragging and self.length:
            self.cur_lbl.setText(fmt(v / 1000 * self.length))

    def _seek_done(self, v):
        if not self.cur or not self.length:
            return
        target = max(0.0, min(self.length - 0.5, v / 1000 * self.length))
        try:
            pygame.mixer.music.play(start=target)
            if self.paused:
                pygame.mixer.music.pause()
            self.pos = target
            self.last_tick = time.time()
        except Exception as e:
            self.now_lbl.setText(f"這首歌無法跳轉: {e}")

    def _tick(self):
        now = time.time()
        if self.cur and not self.paused:
            dt = now - self.last_tick
            self.play_start += dt
            self.pos += dt
            if not self.counted and self.play_start >= COUNT_AFTER_S:
                self.counted = True
                self.stats.add_play(self.cur[0])
            if not pygame.mixer.music.get_busy():
                self._auto_next()
        self.last_tick = now
        self._clock_tick()
        if not self.seek.dragging:
            self.seek.blockSignals(True)
            if self.cur:
                self.cur_lbl.setText(fmt(self.pos))
                self.seek.setValue(int(min(1.0, self.pos / self.length) * 1000) if self.length else 0)
            else:                                   # 停止播放/清單播完: 進度條歸零
                self.cur_lbl.setText("0:00")
                self.tot_lbl.setText("0:00")
                self.seek.setValue(0)
            self.seek.blockSignals(False)

    def _hotkeys(self):
        self.hot = Hot()
        self.hot.prev.connect(self.prev)
        self.hot.toggle.connect(self.toggle)
        self.hot.next.connect(self.next)
        try:
            import keyboard
            keyboard.add_hotkey("f9", self.hot.prev.emit)
            keyboard.add_hotkey("f10", self.hot.toggle.emit)
            keyboard.add_hotkey("f11", self.hot.next.emit)
        except Exception:
            pass

    # ---------------- 系統匣 (最小化後收進右下角的圖示盒) ----------------
    def _setup_tray(self):
        self._quitting = getattr(self, "_quitting", False)
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        t = QSystemTrayIcon(app_icon(), self)
        t.setToolTip(APP_NAME)
        m = QMenu()
        m.addAction("顯示播放器", self._tray_show)
        m.addSeparator()
        m.addAction("⏮ 上一首", self.prev)
        m.addAction("⏯ 播放 / 暫停", self.toggle)
        m.addAction("⏭ 下一首", self.next)
        m.addSeparator()
        self._act_c2t = QAction("按 ✕ 時縮到系統匣 (不關閉)", m)
        self._act_c2t.setCheckable(True)
        self._act_c2t.setChecked(bool(self.cfg["close_to_tray"]))
        self._act_c2t.toggled.connect(lambda on: self.cfg.__setitem__("close_to_tray", on))
        m.addAction(self._act_c2t)
        m.addAction("結束", self._tray_quit)
        t.setContextMenu(m)
        t.activated.connect(lambda r: self._tray_show() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        t.show()
        self.tray, self._tray_menu = t, m

    def _tray_show(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _tray_quit(self):
        self._quitting = True
        self.close()

    def _min_taskbar(self):
        """標題列的「─」: 一般縮到工作列 (不進系統匣)。"""
        self._skip_tray = True
        self.showMinimized()
        QTimer.singleShot(800, lambda: setattr(self, "_skip_tray", False))

    def _to_tray_or_min(self):
        """標題列的「▼」: 直接縮到系統匣；這台電腦沒有系統匣就退回一般最小化。"""
        if getattr(self, "tray", None):
            self._to_tray()
        else:
            self._min_taskbar()

    def _to_tray(self):
        if not self.tray:
            return
        self.hide()
        if not self.cfg["tray_tip_shown"]:
            self.cfg["tray_tip_shown"] = True
            self.tray.showMessage(APP_NAME, "播放器縮到右下角的圖示了，音樂繼續播放。點一下圖示就能叫出來。", QSystemTrayIcon.Information, 5000)

    def changeEvent(self, e):
        if e.type() == e.Type.WindowStateChange and self.isMinimized() and getattr(self, "tray", None) and not self._quitting and not getattr(self, "_skip_tray", False):
            QTimer.singleShot(0, self._to_tray)
        super().changeEvent(e)

    def closeEvent(self, e):
        if getattr(self, "tray", None) and self.cfg["close_to_tray"] and not self._quitting:
            e.ignore()
            self._to_tray()
            return
        self._quitting = True
        save_cfg(self.cfg)
        try:
            if self.tray:
                self.tray.hide()
        except Exception:
            pass
        try:
            pygame.mixer.quit()
        except Exception:
            pass
        super().closeEvent(e)
        QApplication.quit()


if __name__ == "__main__":
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TruckPlayer.App")     # 工作列用自己的播放鍵圖示
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)         # 縮到系統匣時不要因為視窗隱藏就結束
    cleanup_old()
    if install_self_if_needed():
        sys.exit(0)
    w = Player()
    w.show()
    sys.exit(app.exec())
