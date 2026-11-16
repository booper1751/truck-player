# -*- coding: utf-8 -*-
"""卡車播放器 (Qt 版)  —  勾選資料夾 + 播放清單 + 播放次數排行榜 + 桌布背景(面板半透明)
.py 執行: 第一次會自動安裝 PySide6 (介面)、pygame (播放)、mutagen、keyboard (全域快捷鍵，可選)
EXE 執行: 套件已包在裡面。第一次執行會自動安裝到 %LOCALAPPDATA%\\TruckPlayer、在桌面建立捷徑；
          最小化時收進系統匣；更新走 GitHub Releases。
全域快捷鍵: F9 上一首 / F10 播放暫停 / F11 下一首
資料: 同資料夾的 player_config.json 與 player_stats.db (與舊 Tk 版共用，次數會保留)
不會修改、搬動你的任何音樂檔。"""
import ast, ctypes, datetime, hashlib, importlib, importlib.util, json, os, random, re, shutil, sqlite3, subprocess, sys, threading, time, urllib.error, urllib.request

VERSION = 12     # 版本號 (整數)。每次發新版請把這個數字加 1，自動更新就是靠它判斷有沒有新版
UPDATE_DEFAULT_FILE = "ets2_player_qt.py"     # (.py 版) 只填「帳號/倉庫」時，預設去倉庫抓的檔名
UPDATE_SRC_DEFAULT = ""     # 這裡請維持空白！帳號與網址放在程式旁邊的 update_source.json (不要上傳到 GitHub、不要給別人)，程式碼裡就看不到你的帳號
UPDATE_CHECK_MIN = 30       # 程式開著時，每隔幾分鐘再檢查一次有沒有新版
RELEASE_ASSET = "TruckPlayer.exe"             # (EXE 版) Releases 裡要下載的檔名，要跟 build.yml 打包出來的檔名一致

FROZEN = bool(getattr(sys, "frozen", False))  # True = 已打包成 EXE
SCRIPT = sys.executable if FROZEN else os.path.abspath(__file__)
HERE = os.path.dirname(SCRIPT)

APP_NAME = "TruckPlayer"
INSTALL_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), APP_NAME)
INSTALL_EXE = os.path.join(INSTALL_DIR, APP_NAME + ".exe")
SHORTCUT_NAME = "卡車播放器.lnk"
MIGRATE_FILES = ("player_config.json", "player_stats.db", "update_source.json")   # 第一次安裝時，順便帶過去 (如果 EXE 旁邊本來就有)

LAST_ERR = ""


def message_box(text, title="卡車播放器"):
    """Windows 原生訊息框 (不需要 Qt，打包成沒有黑視窗的 EXE 時也能用)"""
    try:
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
        else:
            print(title + ":", text)
    except Exception:
        pass


def spawn_self(path=None):
    """另外開一個獨立的新程式 (更新後重啟、首次安裝後開啟安裝好的那份)。
    EXE 是「單檔版」時，子程式不能沿用舊的暫存解壓目錄，所以要清掉 PyInstaller 的環境變數。"""
    if FROZEN:
        env = os.environ.copy()
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        for k in [k for k in env if k.startswith("_PYI") or k == "_MEIPASS2"]:
            env.pop(k, None)
        flags = (0x00000008 | 0x00000200) if os.name == "nt" else 0      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([path or SCRIPT], env=env, creationflags=flags, close_fds=True, cwd=os.path.dirname(path or SCRIPT))
    else:
        subprocess.Popen([sys.executable, SCRIPT])


def desktop_dir():
    try:
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(0, 0x10, 0, 0, buf) == 0 and buf.value:     # CSIDL_DESKTOPDIRECTORY
            return buf.value
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def make_desktop_shortcut(target=INSTALL_EXE):
    """在桌面建立捷徑 (用 Windows 內建 PowerShell，不需要任何套件)。成功回傳捷徑路徑，失敗回傳 None。"""
    lnk = os.path.join(desktop_dir(), SHORTCUT_NAME)
    if os.path.exists(lnk):
        return lnk
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:TP_LNK);"
          "$s.TargetPath=$env:TP_TARGET;$s.WorkingDirectory=$env:TP_DIR;$s.IconLocation=$env:TP_TARGET+',0';$s.Save()")
    env = dict(os.environ, TP_LNK=lnk, TP_TARGET=target, TP_DIR=os.path.dirname(target))
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], env=env,
                       creationflags=0x08000000 if os.name == "nt" else 0, capture_output=True, timeout=30)
    except Exception:
        return None
    return lnk if os.path.exists(lnk) else None


def maybe_install():
    """EXE 第一次執行 (不在安裝資料夾裡) 時: 建資料夾 → 把自己複製進去 → 桌面建捷徑 → 開啟安裝好的那份 → 結束這一份。
    已經安裝過了就不覆蓋 (版本由程式內的自動更新處理)。複製失敗時，就直接在原地執行。"""
    if os.path.normcase(os.path.abspath(HERE)) == os.path.normcase(os.path.abspath(INSTALL_DIR)):
        return
    fresh = not os.path.exists(INSTALL_EXE)
    try:
        os.makedirs(INSTALL_DIR, exist_ok=True)
        if fresh:
            tmp = INSTALL_EXE + ".tmp"
            shutil.copy2(SCRIPT, tmp)
            os.replace(tmp, INSTALL_EXE)
            for fn in MIGRATE_FILES:
                src, dst = os.path.join(HERE, fn), os.path.join(INSTALL_DIR, fn)
                if os.path.exists(src) and not os.path.exists(dst):
                    shutil.copy2(src, dst)
    except OSError as e:
        message_box(f"無法安裝到:\n{INSTALL_DIR}\n\n原因: {e}\n\n這次會直接在目前的位置執行。")
        return
    lnk = make_desktop_shortcut()
    try:
        spawn_self(INSTALL_EXE)
    except Exception as e:
        message_box(f"安裝好了，但無法自動開啟:\n{INSTALL_EXE}\n\n原因: {e}")
        sys.exit(0)
    if fresh:
        msg = f"已安裝到:\n{INSTALL_DIR}\n\n" + ("桌面已建立「卡車播放器」捷徑，之後請從桌面開啟。" if lnk else "桌面捷徑建立失敗，請到上面的資料夾直接開啟。")
        message_box(msg + "\n\n你下載的這個檔案已經用不到，可以刪除。")
    sys.exit(0)


def cleanup_old_files():
    """清掉上次更新留下的舊檔 (.old / .new)；刪不掉就留著，下次再試"""
    base = os.path.basename(SCRIPT)
    try:
        for fn in os.listdir(HERE):
            if fn.startswith(base + ".old") or fn == base + ".new":
                try:
                    os.remove(os.path.join(HERE, fn))
                except OSError:
                    pass
    except OSError:
        pass


if FROZEN and os.name == "nt" and "--no-install" not in sys.argv:
    maybe_install()


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
    importlib.invalidate_caches()
    print("Python 版本:", sys.version.split()[0])


if not FROZEN:
    ensure_deps()
try:
    from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
    from PySide6.QtGui import (QAction, QColor, QFont, QIcon, QIntValidator, QImage, QPainter, QPixmap, QPolygonF, QRadialGradient,
                               QLinearGradient, QBrush)
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import (QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
                                   QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QSizeGrip, QSlider, QSystemTrayIcon,
                                   QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QHeaderView, QMessageBox, QStyle, QProgressBar, QGraphicsDropShadowEffect)
except ImportError as e:
    print("PySide6 安裝失敗:", e, "\n", LAST_ERR)
    if FROZEN:
        message_box(f"程式缺少必要元件，無法啟動:\n{e}")
    else:
        input("按 Enter 結束")
    sys.exit(1)
try:
    import pygame
except ImportError:
    pygame = None

CFG = os.path.join(HERE, "player_config.json")
DB = os.path.join(HERE, "player_stats.db")
EXTS = (".mp3", ".flac", ".wav", ".ogg", ".m4a")
COUNT_AFTER_S = 30
SKIP_BEFORE_S = 10
COLD_SORT = "冷門榜(次數少→多)"
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


# ---------------- 圖示 (播放鍵，用程式畫出來，不需要圖檔) ----------------
# 設計 (和 make_icon.py 產生 EXE 檔案圖示 icon.ico 的數字一致): 藍紫圓角方塊 + 白色播放三角形
ICON_BG = "#5865f2"
ICON_RADIUS = 0.22
ICON_TRI = ((0.36, 0.25), (0.36, 0.75), (0.76, 0.50))


def play_pixmap(size):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(ICON_BG))
    p.drawRoundedRect(QRectF(0, 0, size, size), size * ICON_RADIUS, size * ICON_RADIUS)
    p.setBrush(QColor("#ffffff"))
    p.drawPolygon(QPolygonF([QPointF(x * size, y * size) for x, y in ICON_TRI]))
    p.end()
    return pm


def app_icon():
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(play_pixmap(s))
    return ic


# ---------------- 單一執行個體 (已經開著就把它叫出來，不要開第二個) ----------------
SINGLE_NAME = "TruckPlayer.SingleInstance"


def single_instance():
    """回傳 QLocalServer (這是第一個執行個體)；回傳 None 代表已經有一個在跑，已通知它顯示視窗，這個應該直接結束"""
    sock = QLocalSocket()
    sock.connectToServer(SINGLE_NAME)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return None
    QLocalServer.removeServer(SINGLE_NAME)          # 清掉上次異常結束留下的殘骸
    srv = QLocalServer()
    srv.listen(SINGLE_NAME)
    return srv


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
    c.setdefault("shuffle", False)         # 亂跳(隨機)模式是否開啟
    c.setdefault("update_src", "")         # 更新來源: GitHub 檔案網址 或 帳號/倉庫
    c.setdefault("update_auto", True)      # 啟動時自動檢查更新
    c.setdefault("update_skip", 0)         # 使用者按「稍後」略過的版本號 (不再自動提示這一版)
    c.setdefault("hidden_paths", [])       # 使用者選擇從清單隱藏的重複檔案 (不會刪檔)
    c.setdefault("drawer_open", False)     # 右側「定時/報時」抽屜是否展開
    c.setdefault("chime_hour", True)       # 整點報時
    c.setdefault("chime_half", True)       # 半點報時
    c.setdefault("chime_ampm", False)      # 報時加上「上午/下午」
    c.setdefault("folders_open", True)     # 左側資料夾面板是否展開
    c.setdefault("tray_min", True)         # 視窗最小化時收進系統匣 (右下角小圖示)
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


def bundled_repo():
    """EXE 版: 打包時由 GitHub Actions 寫進 EXE 裡的 build_info.json {"repo": "帳號/倉庫"}，所以原始碼裡不會有帳號"""
    try:
        with open(os.path.join(getattr(sys, "_MEIPASS", HERE), "build_info.json"), encoding="utf-8") as f:
            return str(json.load(f).get("repo", "")).strip()
    except Exception:
        return ""


def default_src():
    return UPDATE_SRC_DEFAULT.strip() or file_update_src() or (bundled_repo() if FROZEN else "")


def parse_repo(text):
    """從 「帳號/倉庫」 或任何 github.com / raw.githubusercontent.com 網址取出 帳號/倉庫 (EXE 版用)"""
    t = re.sub(r"[?#].*$", "", (text or "").strip()).rstrip("/")
    t = re.sub(r"^https?://", "", t)
    t = re.sub(r"^(?:www\.)?(?:github\.com|raw\.githubusercontent\.com)/", "", t)
    m = re.match(r"^([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/.*)?$", t)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def check_release(src):
    """EXE 版: 查倉庫最新 Release。回傳 dict: ok, version, url(EXE 下載網址), size 或 error"""
    repo = parse_repo(src)
    if not repo:
        return {"ok": False, "error": "看不懂這個更新來源。請填「帳號/倉庫」。"}
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                 headers={"User-Agent": "truck-player-updater", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read(2 * 1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"ok": False, "error": "找不到已發布的版本 (Releases)。請確認倉庫是公開的，而且已經打包出第一個版本。"}
        if e.code in (403, 429):
            return {"ok": False, "error": "GitHub 暫時限制查詢次數，請稍後再試。"}
        return {"ok": False, "error": f"連不上 GitHub (HTTP {e.code})。"}
    except Exception as e:
        return {"ok": False, "error": f"連不上 GitHub ({e})。請確認網路連線。"}
    m = re.search(r"\d+", str(data.get("tag_name", "")))
    if not m:
        return {"ok": False, "error": "最新的 Release 沒有版本號 (標籤要像 v12)。"}
    asset = next((a for a in data.get("assets", []) if str(a.get("name", "")).lower() == RELEASE_ASSET.lower()), None)
    if not asset or not asset.get("browser_download_url"):
        return {"ok": False, "error": f"最新的 Release 裡找不到 {RELEASE_ASSET}。"}
    return {"ok": True, "version": int(m.group()), "url": asset["browser_download_url"], "size": int(asset.get("size") or 0)}


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


def check_remote(src):
    """回傳 dict: ok, version, text, url 或 error  (EXE 版改查 Releases，見 check_release)"""
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
        v.addWidget(QLabel("更新來源 (帳號/倉庫):" if FROZEN else "更新來源 (GitHub 檔案網址，或 帳號/倉庫):"))
        self.src = QLineEdit(pl.cfg["update_src"] or default_src())
        self.src.setPlaceholderText("例如 帳號/倉庫" if FROZEN else "例如 https://github.com/帳號/倉庫/blob/main/ets2_player_qt.py")
        v.addWidget(self.src)
        hint = QLabel(f"會到這個倉庫的 Releases 找最新版的 {RELEASE_ASSET}。倉庫需要是公開的。" if FROZEN else
                      f"只填「帳號/倉庫」時，會抓 main (或 master) 分支裡的 {UPDATE_DEFAULT_FILE}。倉庫需要是公開的。")
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
            self.status.setText(f"發現新版本 v{res['version']} (目前 v{VERSION})。\n按「下載並更新」會下載、換掉目前的程式檔，然後自動重新啟動。\n"
                                ("舊版會暫時改名成 .old，重新開啟後自動清掉；" if FROZEN else "舊版會備份成同名的 .bak 檔；") + "設定、播放次數都不會變。")
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


def fetch_file(url, dest, cb, timeout=30, limit=400 * 1024 * 1024):
    """分段下載成檔案並回報進度 cb(已下載位元組, 總位元組或0)"""
    req = urllib.request.Request(url, headers={"User-Agent": "truck-player-updater", "Cache-Control": "no-cache"})
    got = 0
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if got > limit:
                raise ValueError("檔案異常大")
            cb(got, total)


class UpdateWorker(QObject):
    progress = Signal(int, str)        # (目標百分比, 目前步驟文字)
    done = Signal(bool, str)           # (成功與否, 訊息)

    def __init__(self, res):
        super().__init__()
        self.res = res

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run_exe(self, res):
        """EXE 版: 下載新 EXE → 驗證 → 把正在執行的舊 EXE 改名成 .old (Windows 允許改名但不允許覆蓋) → 新檔放到原本的名字"""
        tmp = SCRIPT + ".new"
        try:
            self.progress.emit(2, "正在連線到 GitHub …")

            def cb(got, total):
                total = total or res.get("size") or 0
                pct = 5 + int(75 * got / total) if total else 40
                self.progress.emit(min(pct, 80), f"下載最新版 … {got / 1048576:.1f} MB" + (f" / {total / 1048576:.1f} MB" if total else ""))
            fetch_file(res["url"], tmp, cb)
            self.progress.emit(82, "檢查檔案是否完整 …")
            with open(tmp, "rb") as f:
                head = f.read(2)
            if head != b"MZ" or (res.get("size") and os.path.getsize(tmp) != res["size"]):
                raise ValueError("下載到的檔案不完整")
            self.progress.emit(88, "備份目前的版本 …")
            old = SCRIPT + ".old"
            if os.path.exists(old):
                try:
                    os.remove(old)
                except OSError:
                    old = SCRIPT + f".old{int(time.time())}"      # 舊的 .old 刪不掉 (還被占用)，改用不重複的名字，下次啟動再清
            os.replace(SCRIPT, old)
            self.progress.emit(95, "安裝新版 …")
            try:
                os.replace(tmp, SCRIPT)
            except OSError:
                os.replace(old, SCRIPT)                           # 放不上去就把舊版改回來，保證程式還在
                raise
            self.progress.emit(100, "更新完成！")
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    def _run(self):
        res = self.res
        try:
            if FROZEN:
                self._run_exe(res)
                self.done.emit(True, "")
                return
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


class Hot(QObject):
    prev = Signal()
    toggle = Signal()
    next = Signal()


class Speaker(QObject):
    """語音報時: 呼叫 Windows 內建語音 (System.Speech)，不需安裝任何套件。
    done(code): 0=成功  3=找不到中文語音  其他=失敗"""
    done = Signal(int)

    def speak(self, text):
        threading.Thread(target=self._run, args=(text,), daemon=True).start()

    def _run(self, text):
        ps = (
            "Add-Type -AssemblyName System.Speech;"
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            "$vs=$s.GetInstalledVoices()|Where-Object{$_.Enabled};"
            "$v=$vs|Where-Object{$_.VoiceInfo.Culture.Name -eq 'zh-TW'}|Select-Object -First 1;"
            "if(-not $v){$v=$vs|Where-Object{$_.VoiceInfo.Culture.Name -like 'zh-*'}|Select-Object -First 1};"
            "if(-not $v){exit 3};"
            "$s.SelectVoice($v.VoiceInfo.Name);$s.Volume=100;"
            f"$s.Speak('{text}')"
        )
        try:
            flags = 0x08000000 if os.name == "nt" else 0      # CREATE_NO_WINDOW，不跳出黑視窗
            r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                               creationflags=flags, capture_output=True, timeout=60)
            self.done.emit(r.returncode)
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
        self._bg_cache = None

        self.setWindowTitle(f"卡車播放器 v{VERSION}")
        self.resize(1280, 760)
        self.setMinimumSize(900, 520)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setWindowIcon(app_icon())
        self.single = None              # 單一執行個體的 QLocalServer (由 main 設定)
        self._tray_hinted = False       # 第一次收進系統匣時，跳一次提示
        self._tray_tip = ""

        if pygame is None:
            QMessageBox.critical(self, "缺少 pygame", "自動安裝失敗。\n" + (LAST_ERR or ""))
            sys.exit(1)
        pygame.mixer.init()
        pygame.mixer.music.set_volume(self.cfg["volume"] / 100)

        self._build()
        self._make_tray()
        self._apply_folders_open()
        self._apply_drawer(animate=False)
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
        for txt, fn in (("─", self.showMinimized), ("✕", self.close)):
            b = QPushButton(txt)
            b.setObjectName("winbtn")
            b.setFixedSize(40, 26)
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
        bw, bc = QPushButton("選擇桌布…"), QPushButton("清除桌布")
        bw.clicked.connect(self.pick_wallpaper)
        bc.clicked.connect(self.clear_wallpaper)
        r2.addWidget(bw)
        r2.addWidget(bc)
        r2.addSpacing(8)
        r2.addWidget(QLabel("面板深度:"))
        self.alpha_sl = QSlider(Qt.Horizontal)
        self.alpha_sl.setRange(0, 100)
        self.alpha_sl.setValue(self.cfg["panel_alpha"])
        self.alpha_sl.setFixedWidth(130)
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

    # ---------------- 系統匣 (右下角小圖示) ----------------
    def _make_tray(self):
        self.tray_ok = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu()
        self._tray_menu = menu                      # 要留著參照，不然會被回收
        for txt, fn in (("顯示視窗", self._show_from_tray), (None, None), ("◀◀ 上一首", self.prev), ("▶ / ❚❚ 播放暫停", self.toggle),
                        ("▶▶ 下一首", self.next), (None, None)):
            if txt is None:
                menu.addSeparator()
                continue
            act = QAction(txt, self)
            act.triggered.connect(fn)
            menu.addAction(act)
        self.tray_min_act = QAction("最小化時收進系統匣", self)
        self.tray_min_act.setCheckable(True)
        self.tray_min_act.setChecked(self.cfg["tray_min"])
        self.tray_min_act.toggled.connect(lambda on: (self.cfg.__setitem__("tray_min", on), save_cfg(self.cfg)))
        menu.addAction(self.tray_min_act)
        menu.addSeparator()
        quit_act = QAction("結束", self)
        quit_act.triggered.connect(self._quit)
        menu.addAction(quit_act)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("卡車播放器")
        self.tray.activated.connect(self._tray_clicked)
        if self.tray_ok:
            self.tray.show()

    def _tray_clicked(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_from_tray()

    def changeEvent(self, e):
        if e.type() == QEvent.WindowStateChange and self.isMinimized() and getattr(self, "tray_ok", False) and self.cfg["tray_min"]:
            QTimer.singleShot(0, self._hide_to_tray)      # 等這一輪事件處理完再隱藏，比較穩
        super().changeEvent(e)

    def _hide_to_tray(self):
        self.hide()
        if not self._tray_hinted:
            self._tray_hinted = True
            self.tray.showMessage("卡車播放器", "已收進系統匣，音樂會繼續播放。\n點圖示可以還原；在圖示上按右鍵可以結束，或改回一般最小化。",
                                  self.tray.icon(), 6000)

    def _show_from_tray(self):
        self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_single(self):
        """又有人想開第二個播放器: 把這個叫出來就好"""
        if self.single:
            s = self.single.nextPendingConnection()
            if s:
                s.readAll()
                s.disconnectFromServer()
        self._show_from_tray()

    def _release_single(self):
        if self.single:
            self.single.close()
            self.single = None

    def _quit(self):
        self.close()

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
        QPushButton#winbtn {{ padding: 0; border-radius: 6px; }}
        QLineEdit, QComboBox {{ background-color: rgba(0,0,0,70); border: 1px solid rgba(255,255,255,40); border-radius: 8px; padding: 5px 8px; }}
        QComboBox::drop-down {{ border: none; width: 22px; }}
        QComboBox QAbstractItemView {{ background-color: rgb({min(255,r+25)},{min(255,g+25)},{min(255,b+25)}); selection-background-color: {acc}; border: 1px solid rgba(255,255,255,50); color: {txt}; }}
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
        QDialog {{ background-color: rgb({min(255,r+22)},{min(255,g+22)},{min(255,b+22)}); }}
        QTreeWidget::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {sub}; background: rgba(0,0,0,60); }}
        QTreeWidget::indicator:checked {{ background: {acc}; border-color: {acc}; }}
        QLabel#dtitle {{ font-size: 16px; font-weight: bold; }}
        QLabel#bigclock {{ font-size: 36px; font-weight: bold; }}
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

    def pick_wallpaper(self):
        f, _ = QFileDialog.getOpenFileName(self, "選擇桌布圖片", "", "圖片 (*.jpg *.jpeg *.png *.bmp *.webp);;全部 (*.*)")
        if f:
            self.cfg["wallpaper"] = f
            save_cfg(self.cfg)
            self._bg_cache = None
            self.update()

    def clear_wallpaper(self):
        self.cfg["wallpaper"] = ""
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
        p.drawImage(0, 0, self._background())
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
            it = QListWidgetItem(f)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if on else Qt.Unchecked)
            it.setToolTip(f)
            self.folder_list.addItem(it)
        self.folder_list.blockSignals(False)

    def _folder_toggled(self, _it):
        for i in range(self.folder_list.count()):
            it = self.folder_list.item(i)
            self.cfg["folders"][it.text()] = it.checkState() == Qt.Checked
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
            if not self.isVisible() and self.tray_ok:      # 視窗收在系統匣時，改用氣泡通知
                self.tray.showMessage("卡車播放器", f"發現新版本 v{res['version']}，點圖示打開視窗就可以更新。", self.tray.icon(), 6000)

    def install_update(self, res, parent, quiet=False):
        self.toast.hide()
        dlg = UpdateProgress(self, res)
        if dlg.exec() == QDialog.Accepted and dlg.ok:
            self._restart()
        elif self._upd_dlg:
            self._upd_dlg.status.setText("更新失敗，目前版本不受影響。")

    def _restart(self):
        save_cfg(self.cfg)
        self._release_single()            # 先放掉「單一執行個體」的占用，不然新開的那個會以為已經有人在跑而直接結束
        try:
            spawn_self()
        except Exception:
            pass
        self.close()

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
        it = self.tree.topLevelItem(idx)
        if it:
            self.tree.setCurrentItem(it)
            self.tree.scrollToItem(it)

    def _finish_current(self, skipped):
        if self.cur and not self.counted and skipped and self.play_start < SKIP_BEFORE_S:
            self.stats.add_skip(self.cur[0])

    def _build_drawer(self):
        inner = self._panel()
        v = QVBoxLayout(inner)
        v.setContentsMargins(14, 12, 14, 12)
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
        self._speaking = False
        self._duck_restore = None
        now = datetime.datetime.now()
        self._last_chime = (now.hour, now.minute)     # 啟動當下那一分鐘不報
        return inner

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
            return f"現在時間{pre}{h12}點"
        if dt.minute == 30:
            return f"現在時間{pre}{h12}點半"
        return f"現在時間{pre}{h12}點{dt.minute}分"        # 只有「試聽」會走到這裡

    def _announce(self, test=False):
        if self._speaking:
            return
        self._speaking = True
        # 報時時把音樂壓低，講完再恢復
        try:
            pygame.mixer.music.set_volume(self.cfg["volume"] / 100 * 0.25)
        except Exception:
            pass
        text = self._time_text(datetime.datetime.now())
        self.speak_lbl.setText("播報: " + text)
        self.speaker.speak(text)

    def _speak_done(self, code):
        self._speaking = False
        try:
            pygame.mixer.music.set_volume(self.cfg["volume"] / 100)
        except Exception:
            pass
        if code == 3:
            self.speak_lbl.setText("找不到中文語音。請到 Windows 設定 > 時間與語言 > 語音，安裝「中文(台灣)」語音。")
        elif code != 0:
            self.speak_lbl.setText("語音播報失敗 (需要 Windows 內建 PowerShell 與語音功能)。")

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
        tip = "卡車播放器"
        if self.cur:
            tip += ("  (暫停) " if self.paused else "  ▶ ") + os.path.splitext(os.path.basename(self.cur[1]))[0]
        tip = tip[:120]                   # Windows 提示文字最長約 127 字
        if tip != self._tray_tip:
            self._tray_tip = tip
            self.tray.setToolTip(tip)
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

    def closeEvent(self, e):
        save_cfg(self.cfg)
        self._release_single()
        try:
            self.tray.hide()              # 不隱藏的話，程式結束後系統匣會殘留一個死掉的圖示
        except Exception:
            pass
        try:
            pygame.mixer.quit()
        except Exception:
            pass
        super().closeEvent(e)
        QApplication.instance().quit()    # 視窗可以收在系統匣不結束程式，所以關閉視窗時要明確結束


if __name__ == "__main__":
    if FROZEN:
        cleanup_old_files()
    if os.name == "nt":
        try:    # 讓工作列用我們自己的播放鍵圖示，而不是 Python 的
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TruckPlayer.App")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 視窗收進系統匣時不要結束程式
    app.setWindowIcon(app_icon())
    srv = single_instance()
    if srv is None:                       # 已經有一個在跑了，剛剛已經請它把視窗叫出來
        sys.exit(0)
    w = Player()
    w.single = srv
    srv.newConnection.connect(w._on_single)
    w.show()
    sys.exit(app.exec())
