# -*- coding: utf-8 -*-
"""卡車播放器 (Qt 版)  —  勾選資料夾 + 播放清單 + 播放次數排行榜 + 桌布背景(面板半透明)
第一次執行會自動安裝: PySide6 + PySide6-Addons (介面 / 影片播放)、pygame (音樂播放)、keyboard (全域快捷鍵，可選)
全域快捷鍵: F9 上一首 / F10 播放暫停 / F11 下一首
資料: 同資料夾的 player_config.json 與 player_stats.db (與舊 Tk 版共用，次數會保留)
不會修改、搬動你的任何音樂檔。"""
import ast, ctypes, datetime, hashlib, importlib, importlib.util, io, json, os, random, re, shutil, sqlite3, subprocess, sys, threading, time, urllib.parse, urllib.request, zipfile
import logging, logging.handlers, unicodedata


def _setup_ssl():
    """讓所有 urllib 連線 (下載 ffmpeg / yt-dlp、天氣、更新) 同時信任「Windows 憑證庫」和 certifi 內建的憑證。
    有些電腦的 Windows 憑證庫不完整 (沒更新過、或防毒軟體換過憑證)，只靠系統憑證會出現 CERTIFICATE_VERIFY_FAILED。
    憑證驗證本身仍然開著，沒有關掉。"""
    try:
        import ssl
        ctx = ssl.create_default_context()
        try:
            import certifi
            ctx.load_verify_locations(cafile=certifi.where())
        except Exception:
            pass
        urllib.request.install_opener(urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)))
    except Exception:
        pass


_setup_ssl()

VERSION = 38     # 版本號 (整數)。每次發新版請把這個數字加 1，自動更新就是靠它判斷有沒有新版
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


MM_ERR = ""


def _can_import(mod):
    """真的在另一個行程 import 一次 (find_spec 只看檔案在不在，版本不合或缺 DLL 還是會失敗)"""
    r = subprocess.run([sys.executable, "-c", f"import {mod}"], capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or "").strip()[-400:]


def _pyside_version():
    from importlib import metadata
    for n in ("PySide6-Essentials", "PySide6"):
        try:
            return metadata.version(n)
        except Exception:
            pass
    return ""


def ensure_multimedia():
    global MM_ERR
    ok, err = _can_import("PySide6.QtMultimedia")
    if ok:
        return
    ver = _pyside_version()
    pin = f"=={ver}" if ver else ""
    print("正在安裝 / 修復 PySide6-Addons (影片播放要用) ...")
    _pip(f"PySide6-Addons{pin}")
    ok, err = _can_import("PySide6.QtMultimedia")
    if not ok and ver:        # 還是不行: 三個套件用同一版本強制重裝
        print("版本可能不一致，正在強制重裝 PySide6 全套 ...")
        for extra in ([], ["--user"]):
            r = subprocess.run([sys.executable, "-m", "pip", "install", "--force-reinstall",
                                f"PySide6=={ver}", f"PySide6-Essentials=={ver}", f"PySide6-Addons=={ver}", *extra],
                               capture_output=True, text=True)
            if r.returncode == 0:
                break
        ok, err = _can_import("PySide6.QtMultimedia")
    if not ok:
        MM_ERR = err


def ensure_deps():
    importlib.invalidate_caches()
    if importlib.util.find_spec("PySide6") is None:
        print("正在下載安裝 PySide6 (介面，約 100MB，請稍候) ...")
        _pip("PySide6-Essentials") or _pip("PySide6")
    ensure_multimedia()
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
    from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPointF, QRect, QSize, Qt, QTimer, QVariantAnimation, Signal
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtGui import QAction, QColor, QFont, QIcon, QImageReader, QIntValidator, QImage, QPainter, QPen, QPixmap, QPolygonF, QRadialGradient, QLinearGradient, QBrush, QCursor
    from PySide6.QtWidgets import (QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
                                   QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSizeGrip, QSlider,
                                   QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QHeaderView, QMessageBox, QStyle, QProgressBar, QGraphicsDropShadowEffect, QMenu, QSystemTrayIcon, QBoxLayout, QSizePolicy, QInputDialog)
except ImportError as e:
    print("PySide6 安裝失敗:", e, "\n", LAST_ERR)
    input("按 Enter 結束")
    sys.exit(1)
try:
    import pygame
except ImportError:
    pygame = None
try:                                    # 影片播放: Qt 的 QtMultimedia (在 PySide6-Addons 裡，ensure_deps 會自動安裝)
    from PySide6.QtCore import QUrl
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    VIDEO_OK = True
except Exception as _e:
    VIDEO_OK = False
    MM_ERR = MM_ERR or str(_e)

SCRIPT = os.path.abspath(sys.executable if FROZEN else __file__)     # EXE 版: 這個 EXE 本身
HERE = os.path.dirname(SCRIPT)
CFG = os.path.join(HERE, "player_config.json")
DB = os.path.join(HERE, "player_stats.db")
EXTS = (".mp3", ".flac", ".wav", ".ogg", ".m4a")
VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".flv", ".m4v",
              ".ts", ".m2ts", ".mts", ".mpg", ".mpeg", ".3gp", ".ogv", ".vob", ".asf", ".divx", ".rmvb")
COUNT_AFTER_S = 30
SKIP_BEFORE_S = 10
COLD_SORT = "冷門榜(次數少→多)"
WP_MODES = ["每首歌換一張", "每 30 分鐘換一張", "每 1 小時換一張", "不換 (維持目前這張)"]
WP_INTERVAL = {"每 30 分鐘換一張": 1800, "每 1 小時換一張": 3600}
RECENT_SORT = "最近新增(7天)"
RECENT_DAYS = 7
SORTS = ["常聽榜", "近期熱門(30天)", "最近播放", RECENT_SORT, "從沒聽過", "跳過最多", COLD_SORT, "資料夾順序"]
V_SORTS = [("name", "名稱 (預設)"), ("size_desc", "檔案大小：大 → 小"), ("size_asc", "檔案大小：小 → 大"),
           ("ctime_desc", "建立日期：新 → 舊"), ("ctime_asc", "建立日期：舊 → 新")]     # 影片清單的排序方式


MARK_KEEP_DAYS = 30       # 「記住位置」最多保留幾天 (從最後一次更新算起)


def human_size(n):
    n = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{int(n)} B" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


# ---------------- 記錄檔 ----------------
LOG_DIR = os.path.join(HERE, "logs")
LOG_FILE = os.path.join(LOG_DIR, "player.log")
LOG = logging.getLogger("truck")


def setup_logging():
    """記錄檔: logs/player.log。超過 1MB 自動換新檔，只保留最近 5 份舊檔 (最多約 6MB)，UTF-8 編碼。
    原則: 只記「發生了什麼事」(啟動、下載結果、錯誤)，不記密碼、Cookie、完整網址。
    如果資料夾寫不進去 (唯讀、被防毒擋)，就安靜略過，絕不影響播放器本身。"""
    if LOG.handlers:
        return
    LOG.setLevel(logging.INFO)
    LOG.propagate = False
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s [%(threadName)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
        LOG.addHandler(h)
    except Exception:
        LOG.addHandler(logging.NullHandler())
        return

    def _hook(t, v, tb):
        LOG.critical("未處理的錯誤", exc_info=(t, v, tb))
        try:
            sys.__excepthook__(t, v, tb)
        except Exception:
            pass
    sys.excepthook = _hook
    if hasattr(threading, "excepthook"):
        def _thook(a):
            LOG.critical("背景執行緒 %s 發生未處理的錯誤", getattr(a.thread, "name", "?"),
                         exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
        threading.excepthook = _thook
    LOG.info("==== 啟動 v%s  Python %s  %s ====", VERSION, sys.version.split()[0], "EXE" if FROZEN else "原始碼")


setup_logging()
try:                                                   # 版本資訊寫進記錄檔: 別人回報「影片比較暗 / 卡」時，一眼看出他用哪一版 Qt
    import platform
    import PySide6
    from PySide6.QtCore import qVersion
    LOG.info("版本: PySide6 %s｜Qt %s｜%s｜QT_MEDIA_BACKEND=%s", PySide6.__version__, qVersion(), platform.platform(),
             os.environ.get("QT_MEDIA_BACKEND", "(預設)"))
except Exception as _e:
    LOG.info("版本資訊讀取失敗: %s", _e)


class _Lap:
    """量一段流程每一步花了幾毫秒，結束時寫一行到記錄檔 (查「為什麼卡」用)"""
    def __init__(self, tag):
        self.tag, self.t0, self.last, self.parts = tag, time.perf_counter(), time.perf_counter(), []

    def mark(self, name):
        now = time.perf_counter()
        self.parts.append(f"{name} {int((now - self.last) * 1000)}ms")
        self.last = now

    def done(self):
        LOG.info("%s 共 %d ms｜%s", self.tag, int((time.perf_counter() - self.t0) * 1000), " | ".join(self.parts))

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
    env["TRUCK_RESTART"] = "1"          # 新程式要等舊程式離開，不要誤以為「已經有一個在跑」
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

    _FID_CACHE = {}          # (路徑, 大小, 修改時間) -> 辨識值。同一個檔案沒變，重掃時就不用再讀 64KB (外接硬碟特別有感)

    @staticmethod
    def fid(path):
        try:
            st = os.stat(path)
            key = (path, st.st_size, st.st_mtime_ns)
            v = Stats._FID_CACHE.get(key)
            if v:
                return v
            with open(path, "rb") as f:
                head = f.read(65536)
            v = Stats._FID_CACHE[key] = hashlib.md5(head + str(st.st_size).encode()).hexdigest()
            return v
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
    c.setdefault("video_marks", {})        # 「記住位置」的影片: {影片編號: {"pos": 秒, "ts": 最後更新時間, "name": 檔名}}，超過 30 天沒動就自動清掉
    c.setdefault("preload_audio", True)    # 播放前先把整首歌讀進記憶體 (≤100MB)，避免硬碟忙的時候音樂頓；想關掉就改成 false
    c.setdefault("shuffle_mode", "even")   # 亂跳怎麼抽: even=每首一樣 / hot=常聽的較常抽到 / cold=不常聽的較常抽到
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
    c.setdefault("wx_daily", False)        # 每天早上7點、中午12點、晚上7點固定報天氣與降雨機率
    c.setdefault("close_to_tray", False)   # 按 ✕ 時是否縮到系統匣
    c.setdefault("tray_tip_shown", False)  # 第一次縮到系統匣時的說明氣泡是否已顯示
    c.setdefault("voice", "")              # 報時女聲 (空白 = 自動)
    c.setdefault("voice_rate", 0)          # 語速 -5 (慢) ~ +5 (快)
    c.setdefault("folders_open", True)     # 左側資料夾面板是否展開
    c.setdefault("tools_open", True)       # 資料夾清單下面那一排按鈕是否展開 (可收成一條橫桿，讓清單變高)
    c.setdefault("repeat", 0)              # 0=不循環 1=全部循環 2=單曲循環
    if c["repeat"] not in (0, 1, 2):
        c["repeat"] = 0
    c.setdefault("mode", "music")          # music = 音樂播放器 / video = 影片播放器
    c.setdefault("video_folders", [])      # 影片模式: 拖進來 / 加入的資料夾
    c.setdefault("video_files", [])        # 影片模式: 單獨加入的影片檔
    c.setdefault("video_off", [])          # 影片模式: 沒有勾選的影片 (預設全部勾選)
    c.setdefault("dl_vdir", "")            # 下載影片的存放位置
    c.setdefault("dl_vq", "1080p")         # 下載影片的畫質上限
    c.setdefault("search_open", True)      # 搜尋列是否展開 (可收成一個 🔍 按鈕)
    c.setdefault("auto_pause", True)       # 影片模式: 切到別的視窗就自動暫停
    c.setdefault("video_tools_open", True) # 影片清單下面的小抽屜 (下載影片…) 是否展開
    c.setdefault("lock", {})               # 資料夾鎖: 密碼雜湊、被鎖的資料夾、鎖之前硬碟上的隱藏狀態
    if not isinstance(c["lock"], dict):
        c["lock"] = {}
    c["lock"].setdefault("salt", "")
    c["lock"].setdefault("hash", "")
    c["lock"].setdefault("folders", [])
    c["lock"].setdefault("orig", {})
    c.setdefault("video_list_on", True)    # 影片模式: 右邊的播放清單是否顯示
    c.setdefault("video_open", None)       # 影片模式: 展開中的資料夾 (None = 第一次，頂層資料夾預設展開)
    c.setdefault("video_sort", "name")     # 影片模式: 清單排序 name=名稱 / size_desc=大→小 / size_asc=小→大
    if c["mode"] not in ("music", "video"):
        c["mode"] = "music"
    if not isinstance(c["video_marks"], dict):
        c["video_marks"] = {}
    _cut = time.time() - MARK_KEEP_DAYS * 86400
    c["video_marks"] = {k: v for k, v in c["video_marks"].items() if isinstance(v, dict) and v.get("ts", 0) >= _cut}
    if c["shuffle_mode"] not in ("even", "hot", "cold", "explore"):
        c["shuffle_mode"] = "even"
    if c["video_sort"] not in [k for k, _ in V_SORTS]:
        c["video_sort"] = "name"
    if c["theme"] not in THEMES:
        c["theme"] = "Discord 深色"
    return c


def save_cfg(c):
    try:
        with open(CFG, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def added_time(path):
    """檔案是什麼時候「加進電腦」的: 用檔案建立時間 (Windows 上複製 / 下載進來的檔案，建立時間就是那一刻)"""
    try:
        st = os.stat(path)
        return float(getattr(st, "st_birthtime", st.st_ctime))
    except OSError:
        return 0.0


HIDE_BITS = 0x2 | 0x4          # Windows 檔案屬性: 隱藏 (H) + 系統 (S)


def _win_attr_api():
    k = ctypes.windll.kernel32
    k.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
    k.GetFileAttributesW.restype = ctypes.c_uint32
    k.SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    k.SetFileAttributesW.restype = ctypes.c_int
    return k


def hide_on_disk(path):
    """把資料夾在硬碟上設成「隱藏 + 系統」(檔案總管預設看不到，檔案內容不動)。回傳 (成功?, 原本的隱藏位元)"""
    if not IS_WIN:
        return False, 0
    try:
        k = _win_attr_api()
        a = k.GetFileAttributesW(str(path))
        if a == 0xFFFFFFFF:
            return False, 0
        return bool(k.SetFileAttributesW(str(path), a | HIDE_BITS)), a & HIDE_BITS
    except Exception:
        return False, 0


def restore_on_disk(path, orig_bits=0):
    """解除鎖定時，把硬碟上的隱藏狀態恢復成鎖之前的樣子"""
    if not IS_WIN:
        return False
    try:
        k = _win_attr_api()
        a = k.GetFileAttributesW(str(path))
        if a == 0xFFFFFFFF:
            return False
        return bool(k.SetFileAttributesW(str(path), (a & ~HIDE_BITS) | (int(orig_bits) & HIDE_BITS)))
    except Exception:
        return False


def _pw_hash(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, 200000).hex()


def scan(folder, exts=EXTS):
    out = []
    for root, _, files in os.walk(folder):
        for n in files:
            if n.lower().endswith(exts):
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
    if s >= 3600:                                      # 滿一小時: 1:05:09 (時:分:秒)；不到一小時維持 5:09
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
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


# ---------------- 縮短 YouTube 標題 (只用在畫面顯示，檔名與下載記錄不受影響) ----------------
DL_TITLE_W = 44          # 下載視窗清單裡，歌名最多顯示幾格寬 (中文字算 2 格、英文算 1 格)
_NOISE_WORDS = ["official", "mv", "m/v", "music video", "lyric", "lyrics", "audio", "video", "hd", "hq", "4k", "8k",
                "1080p", "720p", "remastered", "visualizer", "官方", "歌詞", "歌词", "動態歌詞", "动态歌词", "完整版",
                "高音質", "高音质", "字幕", "中字", "音樂", "音乐", "純享", "纯享", "無損", "无损", "mp3", "downloads?"]
_NOISE = "|".join(_NOISE_WORDS)
_BRACKETS = [("(", ")"), ("[", "]"), ("【", "】"), ("（", "）"), ("「", "」"), ("『", "』"), ("<", ">"), ("《", "》")]


def _char_w(ch):
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def truncate_width(s, max_width, ellipsis="…"):
    """依顯示寬度截斷 (中日韓字寬 2)，太長就加「…」。"""
    if sum(_char_w(c) for c in s) <= max_width:
        return s
    room, out, used = max_width - _char_w(ellipsis), [], 0
    for c in s:
        w = _char_w(c)
        if used + w > room:
            break
        out.append(c)
        used += w
    return "".join(out).rstrip(" -–—|,，、") + ellipsis


def short_title(title, max_width=DL_TITLE_W):
    """YouTube 標題 → 重點: 刪掉 (Official MV)、【歌詞】、4K 這類雜訊，只留 ' | ' 前面的主體，太長再截斷。"""
    if not title:
        return ""
    s = title.strip()
    for lo, hi in _BRACKETS:
        lo_e, hi_e = re.escape(lo), re.escape(hi)
        inner = "[^" + lo_e + hi_e + "]*?"
        s = re.compile(lo_e + inner + "(?:" + _NOISE + ")" + inner + hi_e, re.I).sub(" ", s)
    head = re.split(r"\s+[|｜]\s+", s)[0]
    if head.strip():
        s = head
    pat = re.compile(r"[\s\-–—|:：]*\b(?:" + _NOISE + r")\b[\w\s]*$", re.I)
    prev = None
    while prev != s:
        prev, s = s, pat.sub("", s)
    s = re.sub(r"[\s\-–—|:：]*(?:官方MV|官方版|官方音訊|歌詞版|完整版|高音質|動態歌詞)\s*$", "", s)
    s = re.sub(r"\s+", " ", s).strip(" -–—|:：,，、")
    if not s:
        s = re.sub(r"\s+", " ", title).strip()
    return truncate_width(s, max_width)


FOLDER_MAX_W = 50        # 合集資料夾名稱最多幾格寬 (中文字算 2 格、英文算 1 格，50 格 ≈ 25 個中文字)


def pick_folder_name(base, full_name):
    """合集資料夾名稱 = YouTube 清單標題。名稱太長 (超過 FOLDER_MAX_W 格寬) 才縮短成重點。
    如果舊版已經用完整的長名字建過資料夾，就繼續用它，這樣裡面的下載記錄才接得上，不會整份重新下載。"""
    if os.path.isdir(os.path.join(base, full_name)):
        return full_name
    if sum(_char_w(c) for c in full_name) <= FOLDER_MAX_W:
        return full_name
    return sanitize_folder_name(short_title(full_name, FOLDER_MAX_W)) or full_name


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
            self.lst.addItem(f"{i + 1}. {c['title']}  [{fmt(d)}]")
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

    def __init__(self, pl, kind="music"):
        super().__init__(pl)
        self.pl = pl
        self.setWindowTitle("下載音樂")
        self.resize(800, 760)
        self.ui.connect(lambda fn: fn())
        self.running = self.tools_busy = False
        self._stop = False
        self._job_kind = "music"
        self.paths = {}
        self.stats = {"ok": 0, "skip": 0, "fail": 0, "total": 0}
        c = pl.cfg
        v = QVBoxLayout(self)
        self.title_lbl = QLabel("⬇ 下載音樂")
        self.title_lbl.setObjectName("dtitle")
        v.addWidget(self.title_lbl)
        r = QHBoxLayout()
        r.addWidget(QLabel("類型:"))
        self.kind_cb = QComboBox()
        self.kind_cb.addItems(["🎵 音樂 (mp3)", "🎬 影片 (mp4)"])
        r.addWidget(self.kind_cb)
        r.addStretch(1)
        v.addLayout(r)
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
        self.br_lbl = QLabel("音質(kbps):")
        r.addWidget(self.br_lbl)
        self.br = QComboBox(); self.br.addItems(["320", "256", "192", "128"]); self.br.setCurrentText(str(c["dl_br"]))
        r.addWidget(self.br)
        self.vq_lbl = QLabel("畫質:")
        r.addWidget(self.vq_lbl)
        self.vq = QComboBox(); self.vq.addItems(["最高", "1080p", "720p", "480p"]); self.vq.setCurrentText(str(c["dl_vq"]))
        self.vq.setToolTip("影片畫質上限 (實際會挑不超過這個的最高畫質)")
        r.addWidget(self.vq)
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
        self._dsts = {"music": self.dst.text(),
                      "video": c["dl_vdir"] or (c["video_folders"][0] if c["video_folders"] else os.path.join(HERE, "下載影片"))}
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

        self.list_lbl = QLabel("歌曲清單（雙擊可在資料夾中定位檔案）:")
        v.addWidget(self.list_lbl)
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
        self._vid_prev = False
        self.kind_cb.setCurrentIndex(1 if kind == "video" else 0)
        self._kind_changed()
        self.kind_cb.currentIndexChanged.connect(self._kind_changed)
        self.refresh_tools()

    # ---- 音樂 / 影片 ----
    def set_kind(self, kind):
        if self.running:
            return
        self.kind_cb.setCurrentIndex(1 if kind == "video" else 0)
        self._kind_changed()

    def _kind_changed(self, *_):
        vid = self.kind_cb.currentIndex() == 1
        if vid != self._vid_prev:                      # 音樂和影片各記各的存放位置
            self._dsts["video" if self._vid_prev else "music"] = self.dst.text()
            self.dst.setText(self._dsts["video" if vid else "music"])
            self._vid_prev = vid
        for w in (self.br_lbl, self.br):
            w.setVisible(not vid)
        for w in (self.vq_lbl, self.vq):
            w.setVisible(vid)
        self.title_lbl.setText("⬇ 下載影片" if vid else "⬇ 下載音樂")
        self.setWindowTitle("下載影片" if vid else "下載音樂")
        self.list_lbl.setText(("影片" if vid else "歌曲") + "清單（雙擊可在資料夾中定位檔案）:")

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
            tip = "\n\n請檢查網路連線，再按上方的「重新下載」按鈕重試。"
            if any("CERTIFICATE" in m or "SSL" in m for m in msgs):
                tip = ("\n\n這是電腦上的憑證不完整造成的。可以試試:\n"
                       "1. 先到 Windows Update 更新、重新開機，再按「重新下載」\n"
                       "2. 暫時關閉防毒軟體的「HTTPS / 網頁防護掃描」，再按「重新下載」\n"
                       "3. 自己下載 ffmpeg，把 ffmpeg.exe 放進這個資料夾:\n" + TOOLS_DIR)
            QMessageBox.warning(self, "元件安裝失敗", "\n".join(msgs) + tip)

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
             "start": self.start.value(), "aria": self.aria_cb.isChecked(),
             "kind": "video" if self.kind_cb.currentIndex() == 1 else "music", "vq": self.vq.currentText()}
        c = self.pl.cfg
        c.update(dl_seg=o["seg"], dl_playlist=o["playlist"], dl_aria2=o["aria"])
        if o["kind"] == "video":
            c.update(dl_vdir=base, dl_vq=o["vq"])
        else:
            c.update(dl_dir=base, dl_br=o["br"])
        save_cfg(c)
        self._job_kind = o["kind"]
        self.kind_cb.setEnabled(False)
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
        self.kind_cb.setEnabled(True)
        self.status.setText("準備就緒")
        self.bar.setValue(0)
        self.refresh_tools()
        if save_path and os.path.isdir(save_path):
            self.pl.after_download(save_path, self._job_kind)

    def _run_job(self, url, base, o):
        st = self.stats
        save_path = None
        try:
            yt = get_ytdlp()
            LOG.info("下載工作開始：%s｜網站=%s｜整份清單=%s｜起點=%s｜音質=%s｜同時片段=%s｜aria2=%s｜yt-dlp %s",
                     o["kind"], urllib.parse.urlsplit(url).netloc or "?", o["playlist"], o["start"], o["br"], o["seg"],
                     o["aria"], ytdlp_version() or "?")

            def status_cb(text, color=None):
                if color == "orange":                     # 掃描清單時的警告 (分頁中斷、重試) 也記下來，查原因很有用
                    LOG.warning("掃描：%s", text)
                self.ui.emit(lambda: self.status.setText(text))
            folder_name, entries = scan_playlist_all(yt, url, o["playlist"], status_cb, o["start"], lambda: self._stop)
            if self._stop:
                LOG.info("使用者在掃描階段按了停止")
                return
            if folder_name is None:
                LOG.error("無法解析連結")
                self._ask(lambda: QMessageBox.critical(self, "失敗", "無法解析連結，請確認網址是否正確。"))
                return
            if not entries:
                LOG.error("掃描結果為空 (資料夾「%s」)", folder_name)
                self._ask(lambda: QMessageBox.critical(self, "失敗", "掃描結果為空，請確認連結或網路狀態。"))
                return
            LOG.info("掃描完成：資料夾「%s」，共 %d 首", folder_name, len(entries))
            full_folder = folder_name
            folder_name = pick_folder_name(base, full_folder)         # 資料夾名稱 = YouTube 清單標題，太長才縮短
            if folder_name != full_folder:
                LOG.info("資料夾名稱太長，已縮短：「%s」→「%s」", full_folder, folder_name)
            save_path = os.path.join(base, folder_name)
            os.makedirs(save_path, exist_ok=True)
            history_file = os.path.join(save_path, "download_history_video.txt" if o["kind"] == "video" else "download_history.txt")
            archived = load_archive(history_file)
            st["total"] = len(entries)
            for i, e in enumerate(entries):
                title = short_title(e.get("title") or f"(未知 {i + 1})")      # 清單只顯示重點，檔名仍是完整標題
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
            if o["kind"] == "video":                # 影片: 下載最好的影像 + 聲音，用 ffmpeg 合併成 mp4 (不轉成 mp3)
                h = {"1080p": 1080, "720p": 720, "480p": 480}.get(o["vq"])
                opts["format"] = f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b" if h else "bv*+ba/b"
                opts["format_sort"] = ["res", "ext:mp4:m4a"]       # 同畫質時優先挑 mp4 / m4a，各種播放器都能放
                opts["merge_output_format"] = "mp4"
                opts.pop("postprocessors", None)
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
                vid, full_title = e.get("id", ""), e.get("title") or f"未知 {i + 1}"
                title = short_title(full_title)               # 畫面顯示用的重點標題；比對檔案、搜尋替代版本、寫記錄都用完整的 full_title
                self.ui.emit(lambda: self._row(i, f"🚀 [{i + 1}] {title}  (準備中...)"))
                ph, pp = hooks(i, title)
                o2 = dict(opts, progress_hooks=[ph], postprocessor_hooks=[pp])
                ok, err, err_full = False, "", ""
                try:
                    with yt.YoutubeDL(o2) as y:
                        ok = (y.download([f"https://www.youtube.com/watch?v={vid}"]) == 0)
                except Cancel:
                    LOG.info("使用者停止下載 [%d] %s", i + 1, full_title)
                    return "stop"
                except Exception as ex:
                    err = (str(ex).strip().splitlines() or [ex.__class__.__name__])[-1][:90]
                    err_full = f"{ex.__class__.__name__}: {str(ex).strip()[:600]}"
                if self._stop:
                    return "stop"
                if not ok:                                   # 轉檔階段才出錯，但檔案其實已經產生的情況
                    exts = {".mp3", ".mp4", ".m4a", ".flac", ".wav", ".ogg", ".aac", ".opus", ".wma", ".mkv", ".webm"}
                    key = full_title.lower()[:15]
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
                    LOG.info("成功 [%d/%d] %s (%s)", i + 1, st["total"], full_title, vid)
                    self.ui.emit(lambda: self._row(i, f"✅ [{i + 1}] {title}"))
                else:
                    st["fail"] += 1
                    LOG.warning("失敗 [%d/%d] %s (%s)｜%s", i + 1, st["total"], full_title, vid, err_full or "沒有錯誤訊息")
                    failed.append({"index": i, "title": full_title})
                    self.ui.emit(lambda: self._row(i, f"⚠️ [{i + 1}] {title}  (無法下載" + (f": {err}" if err else "") + ")"))
                self.ui.emit(self._summary)
                return ok

            for i, e in enumerate(entries):
                if in_archive(archived, e.get("id", "")):
                    continue
                if self._stop or download_one(i, e) == "stop":
                    break
            if self._stop:
                LOG.info("下載已停止：成功 %d / 跳過 %d / 失敗 %d / 共 %d", st["ok"], st["skip"], st["fail"], st["total"])
                self.ui.emit(lambda: self.status.setText("⏹ 已停止"))
                return
            LOG.info("下載迴圈結束：成功 %d / 跳過 %d / 失敗 %d / 共 %d", st["ok"], st["skip"], st["fail"], st["total"])

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
                f"────────────────\n📋 合計掃描：{s['total']} 首\n\n新下載的{'影片' if o['kind'] == 'video' else '歌'}已加進播放清單。"))
        except Exception as err:
            LOG.exception("下載工作整個失敗")
            msg = str(err)
            self.ui.emit(lambda: QMessageBox.critical(self, "失敗", f"下載失敗！\n如果是 yt-dlp 的問題，請按「重新下載 yt-dlp」再試。\n\n錯誤：{msg}"))
        finally:
            LOG.info("下載工作結束")
            self.ui.emit(lambda: self._job_done(save_path))

    def _alt_search(self, yt, failed, opts, hooks):
        st = self.stats
        self.ui.emit(lambda: self.status.setText("🔍 搜尋替代版本中..."))

        def alt_one(song):
            i, full_title = song["index"], song["title"]
            title = short_title(full_title)                    # 畫面顯示用；搜尋替代版本要用完整標題
            self.ui.emit(lambda: self._row(i, f"🔍 [{i + 1}] {title}  (搜尋替代中...)"))
            LOG.info("搜尋替代版本 [%d] %s", i + 1, full_title)
            try:
                q = re.sub(r"[(（][^)）]*[)）]", "", full_title).strip() or full_title
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
                    d = AltPicker(self, full_title, cands)
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
                    LOG.info("替代版本下載成功 [%d] %s", i + 1, full_title)
                    self.ui.emit(lambda: self._row(i, f"✅ [{i + 1}] {title}  (替代版本)"))
                    self.ui.emit(self._summary)
                else:
                    self.ui.emit(lambda: self._row(i, f"⚠️ [{i + 1}] {title}  (替代也失敗)"))
            except Exception:
                LOG.exception("替代版本失敗 [%d] %s", i + 1, full_title)
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
                exts = {".mp3", ".mp4", ".m4a", ".flac", ".wav", ".ogg", ".aac", ".opus", ".wma", ".mkv", ".webm"}
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
            if not self.on.isChecked():
                self.on.setChecked(True)            # 挑了圖就自動啟用幻燈片，不用再另外勾
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


_CWA_CACHE = {"ts": 0.0, "data": None, "etag": None}


_SRC_HINT = [""]       # 使用者在「檢查更新」裡填的來源 (程式旁沒有 update_source.json 時，天氣也用它)


def cwa_url():
    """weather.json 的網址 (由 GitHub Actions 定時更新，授權碼只存在 GitHub Secrets，不在這個程式裡)。"""
    if os.environ.get("TRUCK_WEATHER_URL"):          # 測試用
        return os.environ["TRUCK_WEATHER_URL"]
    rp = parse_repo(default_src()) or parse_repo(_SRC_HINT[0])
    if not rp:
        return ""
    return f"https://raw.githubusercontent.com/{rp[0]}/{rp[1]}/weather-data/weather.json"


def fetch_cwa_json(url, timeout=20, bust=False):
    """讀 weather.json。省流量: 要求壓縮傳輸 (約 8KB)，並帶上次的 ETag，資料沒變時伺服器只回「沒變」(幾乎 0 流量)。"""
    hdr = {"User-Agent": "ets2-player-updater", "Accept-Encoding": "gzip"}
    if bust:
        url += ("&" if "?" in url else "?") + "t=%d" % time.time()      # 手動更新: 換網址，避開 CDN 快取
    elif _CWA_CACHE.get("etag") and _CWA_CACHE["data"]:
        hdr["If-None-Match"] = _CWA_CACHE["etag"]
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=timeout) as r:
            raw = r.read(3 * 1024 * 1024 + 1)
            etag, enc = r.headers.get("ETag"), (r.headers.get("Content-Encoding") or "").lower()
    except urllib.error.HTTPError as e:
        if e.code == 304 and _CWA_CACHE["data"]:
            return _CWA_CACHE["data"], _CWA_CACHE.get("etag")
        raise
    if len(raw) > 3 * 1024 * 1024:
        raise ValueError("檔案異常大")
    if enc == "gzip":
        import gzip
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8")), etag


def cwa_data(force=False):
    if not force and _CWA_CACHE["data"] and time.time() - _CWA_CACHE["ts"] < 600:
        return _CWA_CACHE["data"]
    url = cwa_url()
    if not url:
        raise ValueError("沒有設定氣象資料來源")
    d, etag = fetch_cwa_json(url, 20, bust=force)
    if not d.get("stations") or not d.get("counties"):
        raise ValueError("氣象資料內容不完整")
    _CWA_CACHE.update(ts=time.time(), data=d, etag=etag)
    return d


def _norm_place(t):
    return (t or "").replace("台", "臺").replace(" ", "").strip()


def _km(lat1, lon1, lat2, lon2):
    import math
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def _parse_t(t):
    try:
        return datetime.datetime.strptime((t or "")[:16].replace("T", " "), "%Y-%m-%d %H:%M")
    except Exception:
        return None


def period_label(p, now=None):
    """氣象署 36 小時預報的一個時段 → 「今天白天」「今晚」「明天白天」"""
    now = now or datetime.datetime.now()
    st = _parse_t(p.get("s"))
    if st is None:
        return p.get("label", "")
    day = (st.date() - now.date()).days
    if not (5 <= st.hour < 17):
        return "今晚" if day <= 0 else ("明晚" if day == 1 else "後天晚上")
    return "今天白天" if day <= 0 else ("明天白天" if day == 1 else "後天白天")


def wx_items(wx, now=None):
    """要輪流顯示的幾段預報 (最多 3 段)。有時段資料就用時段，否則退回「今天/明天/後天」。"""
    now = now or datetime.datetime.now()
    out = []
    for p in (wx.get("periods") or []):
        e = _parse_t(p.get("e"))
        if e is None or e > now:
            out.append(dict(p, label=period_label(p, now)))
    if not out:
        for i, d in enumerate((wx.get("days") or [])[:3]):
            out.append(dict(d, label=("今天", "明天", "後天")[i]))
    return out[:3]


def fetch_weather_cwa(lat, lon, force=False):
    d = cwa_data(force)
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
    return {"ts": time.time(), "data_ts": d.get("ts"), "periods": cd.get("periods") or [],
            "temp": best.get("temp"), "code": best.get("code") if best.get("wx") else d0.get("code"),
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


def _deg(x):
    return "?" if x is None else f"{round(x)}°"


def fetch_weather(lat, lon, force=False):
    """台灣地點用中央氣象署資料；境外或讀不到時用 Open-Meteo 備援。"""
    try:
        return fetch_weather_cwa(lat, lon, force)
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
                await asyncio.wait_for(edge_tts.Communicate(text, short, rate="%+d%%" % (rate * 10)).save(path), 15)
            asyncio.run(go())
            if os.path.getsize(path) < 500:
                raise RuntimeError("線上語音沒有回傳聲音")
            snd = pygame.mixer.Sound(path)
            ch = snd.play()
            t0 = time.time()
            while ch is not None and ch.get_busy() and time.time() - t0 < 40:
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
        try:
            return _ps_run(ps, 40).returncode
        except subprocess.TimeoutExpired:
            return -2

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


class PwDialog(QDialog):
    """輸入密碼的小視窗 (左下角多一個「忘記密碼」按鈕)"""

    def __init__(self, parent, title, text):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        self.forgot = False
        v = QVBoxLayout(self)
        v.addWidget(QLabel(text))
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.returnPressed.connect(self.accept)
        v.addWidget(self.edit)
        r = QHBoxLayout()
        fg = QPushButton("🆘 忘記密碼")
        fg.clicked.connect(self._forgot)
        ok = QPushButton("確定")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        r.addWidget(fg)
        r.addStretch(1)
        r.addWidget(ok)
        r.addWidget(cancel)
        v.addLayout(r)

    def _forgot(self):
        self.forgot = True
        self.reject()

    def text(self):
        return self.edit.text()


class LockDialog(QDialog):
    """選擇要鎖起來的資料夾 (打勾 = 鎖定)"""

    def __init__(self, pl, paths, locked, change_pw):
        super().__init__(pl)
        self.setWindowTitle("資料夾鎖定設定")
        self.resize(660, 480)
        v = QVBoxLayout(self)
        t = QLabel("🔒 資料夾鎖定")
        t.setObjectName("dtitle")
        v.addWidget(t)
        tip = QLabel("打勾的資料夾會被鎖起來：沒解鎖的時候，播放器的清單裡看不到它們，而且在硬碟上會被設成隱藏 (檔案總管預設看不到)。"
                     "取消打勾 = 解除鎖定，硬碟上的隱藏也會恢復。\n"
                     "注意：這是「藏起來 + 密碼」，不是加密。懂電腦的人打開「顯示隱藏的檔案」或直接輸入路徑，還是進得去。")
        tip.setObjectName("sub")
        tip.setWordWrap(True)
        v.addWidget(tip)
        self.lw = QListWidget()
        for p in paths:
            it = QListWidgetItem(p)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if os.path.normcase(os.path.normpath(p)) in locked else Qt.Unchecked)
            it.setData(Qt.UserRole, p)
            it.setToolTip(p)
            self.lw.addItem(it)
        v.addWidget(self.lw, 1)
        r = QHBoxLayout()
        pw = QPushButton("🔑 更改密碼…")
        pw.clicked.connect(change_pw)
        ok = QPushButton("確定")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        r.addWidget(pw)
        r.addStretch(1)
        r.addWidget(ok)
        r.addWidget(cancel)
        v.addLayout(r)

    def checked(self):
        return [self.lw.item(i).data(Qt.UserRole) for i in range(self.lw.count()) if self.lw.item(i).checkState() == Qt.Checked]


class VideoTree(QTreeWidget):
    """影片清單: 可以從檔案總管把資料夾或影片檔直接拖進來"""
    dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            self.dropped.emit(paths)
        else:
            e.ignore()


# ---------------- 跑馬燈 (歌名太長時捲動顯示) ----------------
MARQUEE_GAP = "     ·     "     # 一圈跑完接回開頭時，中間隔的符號
MARQUEE_MS = 160               # 每隔多少毫秒往前捲一個字
MARQUEE_WAIT = 6               # 剛開始 (或一圈跑完) 先停幾格再跑，約 1 秒，方便先看清楚開頭


def marquee_frame(fm, text, step, width, cache=None):
    """跑馬燈第 step 格的畫面。text 放得下 (不用跑) 就回傳 None。
    做法: 把「歌名 + 間隔」接成一圈，從第 step 個字開始，取到塞滿 width 像素為止。
    fm 是 QFontMetrics (只用到 horizontalAdvance)；cache 是字寬快取。"""
    cache = {} if cache is None else cache

    def cw(ch):
        v = cache.get(ch)
        if v is None:
            v = cache[ch] = fm.horizontalAdvance(ch)
        return v
    if width <= 0 or sum(cw(c) for c in text) <= width:
        return None
    ring = text + MARQUEE_GAP
    s = step % len(ring)
    out, used = [], 0
    for ch in ring[s:] + ring[:s] + ring:
        w = cw(ch)
        if used + w > width:
            break
        out.append(ch)
        used += w
    return "".join(out)


class MarqueeLabel(QLabel):
    """正在播放的歌名: 放得下就靜止不動，太長就像跑馬燈一樣循環捲動。
    文字還是由 QLabel 自己畫，所以主題的顏色、字型、粗體都照舊。text() 永遠回傳完整歌名。"""

    def __init__(self, text=""):
        super().__init__("")
        self._full, self._step, self._wait, self._cache = "", 0, 0, {}
        self._timer = QTimer(self)
        self._timer.setInterval(MARQUEE_MS)
        self._timer.timeout.connect(self._tick)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)     # 不要讓長歌名把整個視窗撐寬
        self.setMinimumWidth(40)
        self.setText(text)

    def text(self):
        return self._full

    def setText(self, t):
        t = str(t)
        if t == self._full:
            return                      # 同一句重複設定 (有些地方會定時重設) 不要讓跑馬燈從頭來
        self._full, self._step, self._wait = t, 0, MARQUEE_WAIT
        self.setToolTip(t)
        self._refresh()

    def _refresh(self):
        frame = marquee_frame(self.fontMetrics(), self._full, self._step, self.contentsRect().width() - 6, self._cache)
        if frame is None:
            QLabel.setText(self, self._full)
            self._timer.stop()
        else:
            QLabel.setText(self, frame)
            if self.isVisible() and not self._timer.isActive():
                self._timer.start()

    def _tick(self):
        if self._wait > 0:
            self._wait -= 1
            return
        self._step += 1
        if self._step % (len(self._full) + len(MARQUEE_GAP)) == 0:
            self._wait = MARQUEE_WAIT
        self._refresh()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refresh()

    def showEvent(self, e):
        super().showEvent(e)
        self._refresh()

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def changeEvent(self, e):
        if e.type() == QEvent.FontChange:
            self._cache.clear()
            self._refresh()
        super().changeEvent(e)

    def sizeHint(self):
        return QSize(240, max(QLabel.sizeHint(self).height(), self.fontMetrics().height() + 8))

    def minimumSizeHint(self):
        return QSize(40, max(QLabel.minimumSizeHint(self).height(), self.fontMetrics().height() + 8))


class HoverMarquee(QObject):
    """清單的跑馬燈: 滑鼠指到的那一列，名字太長就開始捲動，移開就恢復原樣。
    用定時器偵測滑鼠位置，不攔截任何滑鼠事件，所以不會影響點擊、勾選、拖曳。
    捲動時是暫時改那一列的文字 (改的時候擋住 itemChanged 訊號，不會觸發任何動作)。"""

    def __init__(self, tree, cols):
        super().__init__(tree)
        self.tree, self.cols, self.cache = tree, tuple(cols), {}
        self._item, self._col, self._full, self._avail = None, 0, "", 0
        self._step = self._wait = 0
        self._timer = QTimer(self)
        self._timer.setInterval(MARQUEE_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _put(self, it, col, text):
        self.tree.blockSignals(True)
        try:
            it.setText(col, text)
        except RuntimeError:               # 那一列已經被清單重建刪掉了
            pass
        finally:
            self.tree.blockSignals(False)

    def reset(self):
        """還原目前正在捲動的那一列 (清單要改文字、重建之前先呼叫)"""
        if self._item is not None:
            self._put(self._item, self._col, self._full)
        self._item = None

    def _avail_width(self, it, idx):
        w = self.tree.visualRect(idx).width() - 20            # 扣掉左右留白
        if it.flags() & Qt.ItemIsUserCheckable:
            w -= 26                                            # 再扣掉勾選框
        return w

    def _tick(self):
        t = self.tree
        try:
            if not t.isVisible() or not t.underMouse():
                self.reset()
                return
            idx = t.indexAt(t.viewport().mapFromGlobal(QCursor.pos()))
            it = t.itemFromIndex(idx) if idx.isValid() else None
            if it is None or idx.column() not in self.cols:
                self.reset()
                return
            if it is not self._item or idx.column() != self._col:
                self.reset()
                self._item, self._col, self._full = it, idx.column(), it.text(idx.column())
                self._avail, self._step, self._wait = self._avail_width(it, idx), 0, MARQUEE_WAIT
            if marquee_frame(t.fontMetrics(), self._full, 0, self._avail, self.cache) is None:
                return                                         # 名字放得下，不用捲
            if self._wait > 0:
                self._wait -= 1
                return
            self._step += 1
            if self._step % (len(self._full) + len(MARQUEE_GAP)) == 0:
                self._wait = MARQUEE_WAIT
            self._put(it, self._col, marquee_frame(t.fontMetrics(), self._full, self._step, self._avail, self.cache) or self._full)
        except RuntimeError:                                   # 清單剛被重建，舊的那一列已不存在
            self._item = None


if VIDEO_OK:
    class VideoView(QVideoWidget):
        """影片畫面 (像 MPC-HC): 雙擊 = 全螢幕 / 還原，滑鼠滾輪 = 音量，空白鍵 = 播放 / 暫停，
        也可以把影片檔或資料夾直接拖進畫面來播"""
        dropped = Signal(list)
        moved = Signal()

        def __init__(self):
            super().__init__()
            self.on_toggle_fs = self.on_key = self.on_wheel = None
            self.setMinimumHeight(160)
            self.setFocusPolicy(Qt.StrongFocus)
            self.setMouseTracking(True)
            self.setAcceptDrops(True)
            self.setStyleSheet("background-color: black;")

        def mouseDoubleClickEvent(self, e):
            if self.on_toggle_fs:
                self.on_toggle_fs()
            self.setFocus()
            e.accept()

        def mouseMoveEvent(self, e):
            self.moved.emit()
            super().mouseMoveEvent(e)

        def keyPressEvent(self, e):
            if self.on_key and self.on_key(e):
                e.accept()
                return
            super().keyPressEvent(e)

        def wheelEvent(self, e):
            if self.on_wheel and self.on_wheel(e.angleDelta().y()):
                e.accept()
                return
            super().wheelEvent(e)

        def dragEnterEvent(self, e):
            if e.mimeData().hasUrls():
                e.acceptProposedAction()
            else:
                e.ignore()

        def dragMoveEvent(self, e):
            if e.mimeData().hasUrls():
                e.acceptProposedAction()
            else:
                e.ignore()

        def dropEvent(self, e):
            paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
            if paths:
                e.acceptProposedAction()
                QTimer.singleShot(0, lambda p=paths: self.dropped.emit(p))
            else:
                e.ignore()


class FsBar(QFrame):
    """全螢幕時浮在畫面下方的控制列 (像 MPC-HC: 滑鼠動一下才出現，不動就自動收起)。
    它是獨立的小視窗，不搶鍵盤焦點，所以空白鍵 / Esc 還是由影片畫面接收。"""

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setObjectName("fsbar")
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedHeight(92)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 8, 18, 10)
        v.setSpacing(4)
        sr = QHBoxLayout()
        self.cur_lbl = QLabel("0:00")
        self.cur_lbl.setFixedWidth(70)
        self.cur_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sr.addWidget(self.cur_lbl)
        self.seek = SeekSlider()
        sr.addWidget(self.seek, 1)
        self.tot_lbl = QLabel("0:00")
        self.tot_lbl.setFixedWidth(70)
        sr.addWidget(self.tot_lbl)
        v.addLayout(sr)
        cr = QHBoxLayout()
        cr.setSpacing(6)

        def mk(txt, tip, w=46):
            b = QPushButton(txt)
            b.setFixedSize(w, 32)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            cr.addWidget(b)
            return b

        self.prev_btn = mk("◀◀", "上一部")
        self.play_btn = mk("▶", "播放 / 暫停")
        self.stop_btn = mk("⏹", "停止")
        self.next_btn = mk("▶▶", "下一部")
        cr.addSpacing(14)
        self.vol_lbl = QLabel("🔊")
        cr.addWidget(self.vol_lbl)
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setFixedWidth(120)
        self.vol.setFocusPolicy(Qt.NoFocus)
        cr.addWidget(self.vol)
        cr.addSpacing(14)
        self.title = QLabel("")
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)      # 片名太長就自動被截掉，不會把控制列撐寬
        cr.addWidget(self.title, 1)
        self.exit_btn = mk("⛶ 離開全螢幕", "離開全螢幕 (Esc)", 120)
        v.addLayout(cr)
        self.style_it("#5865f2")

    def style_it(self, acc):
        self.setStyleSheet(f"""
        QFrame#fsbar {{ background-color: rgba(18,19,23,235); }}
        QLabel {{ color: #f2f3f5; background: transparent; font-size: 13px; font-family: "Microsoft JhengHei UI", "Noto Sans CJK TC", sans-serif; }}
        QPushButton {{ color: #f2f3f5; background-color: rgba(255,255,255,28); border: none; border-radius: 6px; font-size: 14px; font-family: "Microsoft JhengHei UI", "Noto Sans CJK TC", sans-serif; }}
        QPushButton:hover {{ background-color: {acc}; color: #ffffff; }}
        QSlider::groove:horizontal {{ height: 6px; background: rgba(255,255,255,60); border-radius: 3px; }}
        QSlider::sub-page:horizontal {{ background: {acc}; border-radius: 3px; }}
        QSlider::handle:horizontal {{ background: #ffffff; width: 14px; height: 14px; margin: -4px 0; border-radius: 7px; }}
        """)


class CycleButton(QPushButton):
    """可以亮 / 不亮的按鈕，但按下去不會自己切換，下一個狀態由程式決定 (亂跳按鈕用)"""
    def nextCheckState(self):
        pass


class Player(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = load_cfg()
        self.stats = Stats(DB)
        self.tracks, self.view, self.all_tracks = [], [], []
        self._scan_sig_ok = {}          # 各模式「上次掃描完成時的設定指紋」: 設定沒變就不用重掃
        self._fl_open, self._fl_busy, self._fl_last = set(), 0, (None, 0.0)      # 左邊資料夾樹: 展開中的資料夾 / 程式正在改文字 (別當成使用者操作) / 上次點擊
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
        self.mode = self.cfg["mode"] if VIDEO_OK else "music"
        self._vdrawer_open = False    # 影片模式下，右邊「定時 / 報時」抽屜預設收起來 (只在這次執行期間記得，不存檔)
        self._added_cache = {}        # 路徑 -> 檔案建立時間 (「最近新增」用，這次執行期間快取)
        self._unlocked = False        # 資料夾鎖: 每次開程式都是「上鎖」狀態 (解鎖只在這次執行期間有效，不會存起來)

        self.setWindowTitle(f"卡車播放器 v{VERSION}")
        self.resize(1280, 760)
        self.setMinimumSize(900, 520)
        self.setAttribute(Qt.WA_StyledBackground, False)

        if pygame is None:
            QMessageBox.critical(self, "缺少 pygame", "自動安裝失敗。\n" + (LAST_ERR or ""))
            sys.exit(1)
        try:
            pygame.mixer.pre_init(44100, -16, 2, 8192)      # 緩衝加大 (約 0.19 秒)，避免電腦忙的時候音樂卡頓；再大按暫停 / 調音量的延遲會變明顯
        except Exception:
            pass
        pygame.mixer.init()
        pygame.mixer.music.set_volume(self.cfg["volume"] / 100)

        self.vplayer = self.vaudio = None          # 影片播放器 (跟音樂用的 pygame 是分開的兩套)
        if VIDEO_OK:
            self.vplayer = QMediaPlayer(self)
            self.vaudio = QAudioOutput(self)
            self.vaudio.setVolume(self.cfg["volume"] / 100)
            self.vplayer.setAudioOutput(self.vaudio)
            self.vplayer.durationChanged.connect(self._v_duration)
            self.vplayer.mediaStatusChanged.connect(self._v_status)
            self.vplayer.metaDataChanged.connect(self._v_meta)
            self.vplayer.errorOccurred.connect(self._v_error)

        self._build()
        QApplication.instance().installEventFilter(self)        # 影片快捷鍵: 不管游標 / 焦點在清單、按鈕還是畫面，都收得到 (見 _global_video_key)
        if self.vplayer:
            self.vplayer.setVideoOutput(self.video_view)
        self.set_mode(self.mode, first=True)
        self._apply_folders_open()
        self._apply_tools(animate=False)
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
        QApplication.instance().applicationStateChanged.connect(self._app_state)
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
        # 資料夾樹: 頂層 = 你加入的音樂資料夾 (有勾選框，勾了才進右邊清單)；點資料夾名稱展開，裡面是子資料夾和歌；雙擊歌 = 播放
        self.folder_list = QTreeWidget()
        self.folder_list.setObjectName("folderlist")
        self.folder_list.setHeaderHidden(True)
        self.folder_list.setRootIsDecorated(False)              # 展開箭頭用文字 ▸ ▾ (跟影片清單一樣)
        self.folder_list.setUniformRowHeights(True)
        self.folder_list.setExpandsOnDoubleClick(False)
        self.folder_list.setIndentation(18)
        self.folder_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)       # 捲軸一直顯示，才知道還有更多資料夾
        self.folder_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)    # 太長的名字滑鼠指上去會跑馬燈，不用左右捲
        self.folder_list.setTextElideMode(Qt.ElideRight)
        self.folder_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.folder_list.itemChanged.connect(self._folder_toggled)
        self.folder_list.itemClicked.connect(self._fl_clicked)
        self.folder_list.itemDoubleClicked.connect(self._fl_double)
        self.folder_list.itemExpanded.connect(self._fl_expand)
        self.folder_list.itemCollapsed.connect(self._fl_collapse)
        lv.addWidget(self.folder_list, 1)
        self.tools_bar = QPushButton("▼")
        self.tools_bar.setObjectName("drawerbar")
        self.tools_bar.setFixedHeight(22)
        self.tools_bar.setToolTip("收起 / 展開下面的按鈕，讓資料夾清單變高")
        self.tools_bar.clicked.connect(self._toggle_tools)
        lv.addWidget(self.tools_bar)
        self.tools_box = QWidget()
        self.tools_box.setObjectName("toolsbox")
        tl = QVBoxLayout(self.tools_box)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(lv.spacing())
        hb = QHBoxLayout()
        b1, b2 = QPushButton("＋加入資料夾"), QPushButton("移除未勾選")
        b1.clicked.connect(self.add_folder)
        b2.clicked.connect(self.remove_unchecked)
        hb.addWidget(b1)
        hb.addWidget(b2)
        tl.addLayout(hb)
        b6 = QPushButton("⬇ 下載音樂…")
        b6.clicked.connect(lambda: self.open_download("music"))
        tl.addWidget(b6)
        b4 = QPushButton("🔍 找重複歌曲…")
        b4.clicked.connect(self.find_dups)
        tl.addWidget(b4)
        self.hid_btn = QPushButton("🙈 隱藏清單 (0)")
        self.hid_btn.clicked.connect(self.open_hidden)
        tl.addWidget(self.hid_btn)
        b3 = QPushButton("重新掃描")
        b3.clicked.connect(self._manual_rescan)
        tl.addWidget(b3)
        b5 = QPushButton(f"🔄 檢查更新 (v{VERSION})")
        b5.clicked.connect(self.open_update)
        tl.addWidget(b5)
        hl = QHBoxLayout()
        self.lock_toggle_m = QPushButton()
        self.lock_toggle_m.clicked.connect(self._lock_toggle)
        bls = QPushButton("⚙ 鎖定設定…")
        bls.clicked.connect(self.lock_manage)
        hl.addWidget(self.lock_toggle_m)
        hl.addWidget(bls)
        tl.addLayout(hl)
        lv.addWidget(self.tools_box)
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
        self.mode_btn = QPushButton()
        self.mode_btn.setObjectName("modebtn")
        self.mode_btn.setMinimumWidth(96)
        self.mode_btn.setFixedHeight(32)
        self.mode_btn.clicked.connect(self._toggle_mode)
        r1.addWidget(self.mode_btn)
        r1.addSpacing(6)
        self.sort_lbl = QLabel("排序:")
        r1.addWidget(self.sort_lbl)
        self.sort_cb = QComboBox()
        self.sort_cb.addItems(SORTS)
        self.sort_cb.setCurrentText(self.cfg["sort"])
        self.sort_cb.currentTextChanged.connect(self._sort_changed)
        r1.addWidget(self.sort_cb)
        r1.addSpacing(12)
        self.search_box = QWidget()
        sl = QHBoxLayout(self.search_box)
        sl.setContentsMargins(0, 0, 0, 0)
        self.search_btn = QPushButton("🔍")
        self.search_btn.setObjectName("foldbtn")
        self.search_btn.setFixedSize(40, 32)
        self.search_btn.clicked.connect(self._toggle_search)
        sl.addWidget(self.search_btn)
        self.search_lbl = QLabel("搜尋:")
        sl.addWidget(self.search_lbl)
        self.search = QLineEdit()
        self.search.setPlaceholderText("輸入歌名關鍵字")
        self.search.textChanged.connect(lambda *_: self.apply_sort())
        sl.addWidget(self.search, 1)
        r1.addWidget(self.search_box, 1)
        self._apply_search()
        self.clock_btn = QPushButton("⏱")
        self.clock_btn.setObjectName("clockbtn")
        self.clock_btn.setMinimumWidth(96)
        self.clock_btn.setFixedHeight(32)
        self.clock_btn.setToolTip("定時停止 / 語音報時 (往右展開)")
        self.clock_btn.clicked.connect(self._toggle_drawer)
        r1.addWidget(self.clock_btn)
        tv.addLayout(r1)
        r2 = QHBoxLayout()
        r2.setContentsMargins(0, 0, 0, 0)
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
        self.tool_r2w = QWidget()          # 影片模式時整排收起來，把空間讓給畫面
        self.tool_r2w.setLayout(r2)
        tv.addWidget(self.tool_r2w)
        rv.addWidget(tool)

        self.video_panel = self._panel()
        vpl = QVBoxLayout(self.video_panel)
        vpl.setContentsMargins(8, 8, 8, 8)
        if VIDEO_OK:
            self.video_view = VideoView()
        else:
            self.video_view = QLabel("這台電腦的 PySide6 沒有 QtMultimedia，無法播放影片")
            self.video_view.setAlignment(Qt.AlignCenter)
        vpl.addWidget(self.video_view, 1)
        if VIDEO_OK:
            self.video_view.on_toggle_fs = self._toggle_fullscreen
            self.video_view.on_key = self._video_key
            self.video_view.on_wheel = self._video_wheel
            self.video_view.moved.connect(self._fs_motion)
            self.video_view.dropped.connect(self._v_drop_play)
        self.video_panel.hide()

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
        self._marq_tree = HoverMarquee(self.tree, (1,))              # 右邊清單: 滑鼠指到太長的歌名就跑馬燈
        self._marq_fl = HoverMarquee(self.folder_list, (0,))         # 左邊資料夾樹: 同上

        # 影片模式的清單: 資料夾可展開、每部影片可勾選、可把資料夾拖進來
        self._v_items, self._v_last, self._v_sz = {}, (None, 0.0), {}
        self._v_open = set(self.cfg["video_open"] or [])
        self._v_save_t = QTimer(self)
        self._v_save_t.setSingleShot(True)
        self._v_save_t.setInterval(150)
        self._v_save_t.timeout.connect(self._v_commit)
        self.video_box = QWidget()
        vb = QVBoxLayout(self.video_box)
        vb.setContentsMargins(0, 0, 0, 0)
        vr = QHBoxLayout()
        for txt, fn in (("＋ 資料夾", self._v_add_dialog), ("＋ 影片檔", self._v_add_files_dialog),
                        ("全選", lambda: self._v_set_all(True)), ("全不選", lambda: self._v_set_all(False))):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            vr.addWidget(b)
        self.v_sort_btn = QPushButton()
        self.v_sort_btn.setToolTip("影片清單的排序: 名稱 / 檔案大小 (大→小、小→大)")
        self.v_sort_btn.clicked.connect(self._v_sort_menu)
        vr.addWidget(self.v_sort_btn)
        self._v_sort_label()
        vb.addLayout(vr)
        self.v_count_lbl = QLabel("")
        self.v_count_lbl.setObjectName("sub")
        vb.addWidget(self.v_count_lbl)
        self.vtree = VideoTree()
        self.vtree.setHeaderHidden(True)
        self.vtree.setRootIsDecorated(False)
        self.vtree.setUniformRowHeights(True)
        self.vtree.setExpandsOnDoubleClick(False)
        self.vtree.setIndentation(22)
        self.vtree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.vtree.dropped.connect(self._v_add_paths)
        self.vtree.itemClicked.connect(self._v_clicked)
        self.vtree.itemChanged.connect(lambda *_: self._v_save_t.start())
        self.vtree.itemExpanded.connect(self._v_expand_changed)
        self.vtree.itemCollapsed.connect(self._v_expand_changed)
        self.vtree.customContextMenuRequested.connect(self._v_menu)
        vb.addWidget(self.vtree, 1)
        self.v_tools_bar = QPushButton("▼")
        self.v_tools_bar.setObjectName("drawerbar")
        self.v_tools_bar.setFixedHeight(22)
        self.v_tools_bar.clicked.connect(self._toggle_vtools)
        vb.addWidget(self.v_tools_bar)
        self.v_tools_box = QWidget()
        self.v_tools_box.setObjectName("toolsbox")
        vt = QVBoxLayout(self.v_tools_box)
        vt.setContentsMargins(0, 0, 0, 0)
        vt.setSpacing(6)
        hb2 = QHBoxLayout()
        bdl = QPushButton("⬇ 下載影片…")
        bdl.clicked.connect(lambda: self.open_download("video"))
        brs = QPushButton("重新掃描")
        brs.clicked.connect(self._v_reload)
        hb2.addWidget(bdl)
        hb2.addWidget(brs)
        vt.addLayout(hb2)
        hl2 = QHBoxLayout()
        self.lock_toggle_v = QPushButton()
        self.lock_toggle_v.clicked.connect(self._lock_toggle)
        bls2 = QPushButton("⚙ 鎖定設定…")
        bls2.clicked.connect(self.lock_manage)
        hl2.addWidget(self.lock_toggle_v)
        hl2.addWidget(bls2)
        vt.addLayout(hl2)
        self._lock_btns = [self.lock_toggle_m, self.lock_toggle_v]
        self._lock_ui()
        self.autopause_cb = QCheckBox("切到別的視窗就自動暫停")
        self.autopause_cb.setChecked(bool(self.cfg["auto_pause"]))
        self.autopause_cb.toggled.connect(self._autopause_changed)
        vt.addWidget(self.autopause_cb)
        vb.addWidget(self.v_tools_box)
        self._apply_vtools()
        lv2.addWidget(self.video_box, 1)
        self.video_box.hide()
        self.listp = listp
        self.mid = QBoxLayout(QBoxLayout.TopToBottom)        # 音樂模式: 上下排；影片模式: 左邊畫面、右邊清單
        self.mid.setContentsMargins(0, 0, 0, 0)
        self.mid.setSpacing(12)
        self.mid.addWidget(self.video_panel, 1)
        self.mid.addWidget(listp, 1)
        rv.addLayout(self.mid, 1)

        # 下: 音量條(歌名上方) + 歌名 + 控制
        bottom = self._panel()
        self.bottom_panel = bottom
        bv = QVBoxLayout(bottom)
        sr = QHBoxLayout()
        self.cur_lbl = QLabel("0:00")
        self.cur_lbl.setFixedWidth(70)
        self.cur_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sr.addWidget(self.cur_lbl)
        self.seek = SeekSlider()
        self.seek.setToolTip(f"可點擊或拖曳跳轉 (聽滿 {COUNT_AFTER_S} 秒才會計一次播放)")
        self.seek.valueChanged.connect(self._seek_moved)
        self.seek.seeked.connect(self._seek_done)
        sr.addWidget(self.seek, 1)
        self.tot_lbl = QLabel("0:00")
        self.tot_lbl.setFixedWidth(70)
        sr.addWidget(self.tot_lbl)
        bv.addLayout(sr)
        self.now_lbl = MarqueeLabel("(未播放)")          # 歌名太長會像跑馬燈一樣捲動
        f = self.now_lbl.font()
        f.setPointSize(12)
        f.setBold(True)
        self.now_lbl.setFont(f)
        bv.addWidget(self.now_lbl)
        # 一排: 左邊是「播放」+ 亂跳模式(上一首 / 播放暫停 / 停止 / 下一首)，右邊是「顯示」(音量 / 循環 / 清單 / 全螢幕)
        cr = QHBoxLayout()
        cr.setSpacing(8)
        for txt, fn, attr in (("◀◀", self.prev, ""), ("▶ / ❚❚", self.toggle, ""), ("⏹", self.stop_play, "stop_btn"), ("▶▶", self.next, "")):
            b = QPushButton(txt)
            b.setMinimumWidth(52 if attr else 70)
            b.clicked.connect(fn)
            if attr:
                setattr(self, attr, b)
                b.setToolTip("停止 (畫面和進度條歸零)")
            cr.addWidget(b)
        cr.addSpacing(10)
        self.shuffle_cb = CycleButton()                   # 一顆按鈕輪流切換: 照順序 → 平均亂跳 → 常聽優先 → 冷門優先 → 探索 (右鍵有選單)
        self.shuffle_cb.setObjectName("shufbtn")
        self.shuffle_cb.setCheckable(True)
        self.shuffle_cb.setMinimumWidth(150)
        self.shuffle_cb.setChecked(self.cfg["shuffle"])
        self.shuffle_cb.setContextMenuPolicy(Qt.CustomContextMenu)
        self.shuffle_cb.customContextMenuRequested.connect(self._shuf_menu)
        self.shuffle_cb.clicked.connect(self._shuf_cycle)
        self.shuffle_cb.toggled.connect(self._shuffle_toggled)
        cr.addWidget(self.shuffle_cb)
        self._shuf_label()
        cr.addStretch(1)
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
        self.mark_btn = QPushButton("🔖 記住位置")
        self.mark_btn.setObjectName("shufbtn")
        self.mark_btn.setCheckable(True)
        self.mark_btn.setMinimumWidth(110)
        self.mark_btn.clicked.connect(self._mark_click)
        cr.addWidget(self.mark_btn)
        self.list_btn = QPushButton("📋 清單")
        self.list_btn.setObjectName("shufbtn")
        self.list_btn.setCheckable(True)
        self.list_btn.setMinimumWidth(80)
        self.list_btn.setChecked(bool(self.cfg["video_list_on"]))
        self.list_btn.setToolTip("顯示 / 隱藏右邊的播放清單 (快捷鍵 L)")
        self.list_btn.toggled.connect(self._toggle_vlist)
        cr.addWidget(self.list_btn)
        self.fs_btn = QPushButton("⛶ 全螢幕")
        self.fs_btn.setMinimumWidth(100)
        self.fs_btn.setToolTip("全螢幕 (也可以雙擊畫面或按 F，Esc 離開；全螢幕時滑鼠一動就會跳出控制列)")
        self.fs_btn.clicked.connect(self._toggle_fullscreen)
        cr.addWidget(self.fs_btn)
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

        # 全螢幕時浮在畫面下方的控制列 (獨立小視窗)
        self.fsbar = FsBar()
        self.fsbar.prev_btn.clicked.connect(self.prev)
        self.fsbar.play_btn.clicked.connect(self.toggle)
        self.fsbar.stop_btn.clicked.connect(self.stop_play)
        self.fsbar.next_btn.clicked.connect(self.next)
        self.fsbar.exit_btn.clicked.connect(lambda: self._set_fullscreen(False))
        self.fsbar.seek.valueChanged.connect(self._fs_seek_moved)
        self.fsbar.seek.seeked.connect(self._seek_done)
        self.fsbar.vol.setValue(self.cfg["volume"])
        self.fsbar.vol.valueChanged.connect(self.vol.setValue)
        self._fs_timer = QTimer(self)
        self._fs_timer.setSingleShot(True)
        self._fs_timer.setInterval(2500)
        self._fs_timer.timeout.connect(self._fs_idle)
        self.setAcceptDrops(True)          # 影片檔拖到視窗任何地方都會加進清單並開始播
        self._hook_video_children()

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
        #folderlist QScrollBar:vertical {{ background: rgba(255,255,255,28); width: 16px; margin: 0; border-radius: 8px; }}
        #folderlist QScrollBar::handle:vertical {{ background: {acc}; border-radius: 8px; min-height: 36px; }}
        #folderlist QScrollBar::handle:vertical:disabled {{ background: rgba(255,255,255,45); }}
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
        QTreeWidget::indicator:indeterminate {{ background: {sub}; border-color: {acc}; }}
        QLabel#dtitle {{ font-size: 16px; font-weight: bold; }}
        QLabel#bigclock {{ font-size: 36px; font-weight: bold; }}
        QLabel#wxplace {{ font-size: 15px; font-weight: bold; }}
        QLabel#wxnow {{ font-size: 24px; font-weight: bold; }}
        QPushButton#clockbtn[rep="1"] {{ background-color: {acc}; color: #ffffff; }}
        QPushButton#shufbtn:checked {{ background-color: {acc}; color: #ffffff; }}
        QPushButton#modebtn[vid="1"] {{ background-color: {acc}; color: #ffffff; }}
        QPushButton#foldbtn {{ font-size: 18px; padding: 0; }}
        QPushButton#drawerbar {{ background-color: rgba(255,255,255,22); border: none; border-radius: 6px; font-size: 13px; padding: 0; min-height: 0; }}
        QPushButton#drawerbar:hover {{ background-color: {acc}; color: #ffffff; }}
        QWidget#toolsbox {{ background: transparent; }}
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
    # 左邊資料夾樹。每一列存的資料: UserRole = 真正的路徑，UserRole+1 = 種類 (root 頂層資料夾 / dir 子資料夾 / song 歌 / ph 佔位)，
    # UserRole+2 = 顯示用的名字。子資料夾和歌是「展開時才掃」，所以很大的資料夾也不會一開始就卡住。
    def _fl_marq_reset(self):
        m = getattr(self, "_marq_fl", None)
        if m is not None:
            m.reset()

    def _fl_make(self, kind, path, name, hidden=False):
        it = QTreeWidgetItem()
        it.setData(0, Qt.UserRole, path)
        it.setData(0, Qt.UserRole + 1, kind)
        it.setData(0, Qt.UserRole + 2, name)
        it.setToolTip(0, path)
        if kind in ("root", "dir"):
            ph = QTreeWidgetItem(["…"])                 # 佔位，讓它可以展開；真正展開時才換成裡面的內容
            ph.setData(0, Qt.UserRole + 1, "ph")
            ph.setFlags(Qt.NoItemFlags)
            it.addChild(ph)
            warn = "   ⚠ 找不到這個資料夾" if kind == "root" and not os.path.isdir(path) else ""
            it.setText(0, f"▸ 📁 {name}{warn}")
        else:
            it.setText(0, ("🙈 " if hidden else "🎵 ") + name)
        return it

    def _fl_label(self, it):
        """更新資料夾那一列的箭頭 ▸ / ▾"""
        kind = it.data(0, Qt.UserRole + 1)
        if kind in ("root", "dir"):
            warn = "   ⚠ 找不到這個資料夾" if kind == "root" and not os.path.isdir(it.data(0, Qt.UserRole)) else ""
            it.setText(0, f"{'▾' if it.isExpanded() else '▸'} 📁 {it.data(0, Qt.UserRole + 2)}{warn}")

    def _fl_fill(self, parent):
        """掃這個資料夾裡面: 子資料夾在前，歌在後，各自照名字排序"""
        path = parent.data(0, Qt.UserRole)
        parent.takeChildren()
        try:
            ents = sorted(os.scandir(path), key=lambda e: e.name.lower())
        except OSError:
            bad = QTreeWidgetItem(["⚠ 讀不到這個資料夾"])
            bad.setFlags(Qt.NoItemFlags)
            parent.addChild(bad)
            return
        exts, hidden = self._exts(), set(self.cfg["hidden_paths"])
        dirs, songs = [], []
        for e in ents:
            try:
                if e.is_dir(follow_symlinks=False):
                    if not self._is_locked_path(e.path):
                        dirs.append(e)
                elif e.name.lower().endswith(exts) and e.is_file() and not self._is_locked_path(e.path):
                    songs.append(e)
            except OSError:
                pass
        for e in dirs:
            parent.addChild(self._fl_make("dir", e.path, e.name))
        for e in songs:
            parent.addChild(self._fl_make("song", e.path, os.path.splitext(e.name)[0], e.path in hidden))
        if not dirs and not songs:
            empty = QTreeWidgetItem(["(這裡面沒有音樂檔)"])
            empty.setFlags(Qt.NoItemFlags)
            parent.addChild(empty)
        for i in range(parent.childCount()):             # 上次展開過的子資料夾，順便恢復展開
            ch = parent.child(i)
            if ch.data(0, Qt.UserRole + 1) == "dir" and ch.data(0, Qt.UserRole) in self._fl_open:
                ch.setExpanded(True)

    def _load_folders(self):
        t = self.folder_list
        self._fl_marq_reset()
        self._fl_busy += 1                                # 重建期間的文字變動都不算是你在勾選 (用 try/finally 確保一定會還原)
        try:
            t.blockSignals(True)
            try:
                t.clear()
                roots = []
                for f, on in self.cfg["folders"].items():
                    if self._is_locked_path(f):
                        continue
                    it = self._fl_make("root", f, os.path.basename(f.rstrip("\\/")) or f)
                    it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                    it.setCheckState(0, Qt.Checked if on else Qt.Unchecked)
                    t.addTopLevelItem(it)
                    roots.append(it)
            finally:
                t.blockSignals(False)
            for it in roots:                              # 上次展開過的資料夾，重建後恢復展開
                if it.data(0, Qt.UserRole) in self._fl_open:
                    it.setExpanded(True)
        finally:
            self._fl_busy -= 1

    def _fl_expand(self, it):
        """資料夾展開: 第一次展開才去掃裡面的內容"""
        if it.data(0, Qt.UserRole + 1) not in ("root", "dir"):
            return
        self._fl_marq_reset()
        self._fl_busy += 1
        try:
            self._fl_open.add(it.data(0, Qt.UserRole))
            if it.childCount() == 1 and it.child(0).data(0, Qt.UserRole + 1) == "ph":
                self._fl_fill(it)
            self._fl_label(it)
        finally:
            self._fl_busy -= 1

    def _fl_collapse(self, it):
        if it.data(0, Qt.UserRole + 1) not in ("root", "dir"):
            return
        self._fl_marq_reset()
        self._fl_busy += 1
        try:
            self._fl_open.discard(it.data(0, Qt.UserRole))
            self._fl_label(it)
        finally:
            self._fl_busy -= 1

    def _fl_clicked(self, it, _c):
        """點資料夾名稱 = 展開 / 收合；點最左邊的勾選框 = 只勾選，不展開 (跟影片清單一樣)"""
        kind = it.data(0, Qt.UserRole + 1)
        if kind not in ("root", "dir"):
            return
        if kind == "root":
            pos = self.folder_list.viewport().mapFromGlobal(QCursor.pos())
            if pos.x() <= self.folder_list.visualItemRect(it).left() + 30:
                return
        now = time.time()
        last_it, last_t = self._fl_last
        self._fl_last = (it, now)
        if last_it is it and now - last_t < QApplication.doubleClickInterval() / 1000:
            return                                       # 雙擊的第二下，不要重複動作
        it.setExpanded(not it.isExpanded())

    def _fl_double(self, it, _c):
        """雙擊歌 = 播放 (這首要在右邊的播放清單裡才播得到)"""
        if it.data(0, Qt.UserRole + 1) != "song":
            return
        p = os.path.normcase(it.data(0, Qt.UserRole))
        for i, (_, vp) in enumerate(self.view):
            if os.path.normcase(vp) == p:
                self.play_index(i)
                return
        self.now_lbl.setText("這首歌不在目前的播放清單裡 (資料夾沒勾選、歌被隱藏，或被搜尋條件濾掉了)")

    def _folder_toggled(self, it=None, col=0):
        if self._fl_busy:
            return                                       # 是程式自己在改文字 (展開、箭頭)，不是你勾選
        if it is not None and (it.parent() is not None or col != 0):
            return
        for i in range(self.folder_list.topLevelItemCount()):
            r = self.folder_list.topLevelItem(i)
            self.cfg["folders"][r.data(0, Qt.UserRole)] = r.checkState(0) == Qt.Checked
        save_cfg(self.cfg)
        self.refresh_tracks()

    def _manual_rescan(self):
        """「重新掃描」按鈕: 資料夾樹裡面的內容 (子資料夾、歌) 也一起重新讀"""
        self._scan_sig_ok.clear()                         # 兩個模式的清單都作廢，下次切換也會重掃
        self._load_folders()
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
        if self.mode == "video":
            self._v_reload()
            return
        self.all_tracks = []
        for f, on in self.cfg["folders"].items():
            if on and os.path.isdir(f) and not self._is_locked_path(f):
                for p in sorted(scan(f, self._exts())):
                    if self._is_locked_path(p):
                        continue
                    fid = self.stats.fid(p)
                    self.stats.touch(fid, p)
                    self.all_tracks.append((fid, p))
        self.stats.db.commit()
        self._filter_tracks()
        self._scan_sig_ok["music"] = self._scan_sig("music")

    def _scan_sig(self, mode):
        """這個模式的清單，是由哪些設定掃出來的。之後只要有任何一項不一樣 (資料夾、鎖、排序…)，就一定重掃"""
        lock = (bool(self._unlocked), tuple(self.cfg["lock"]["folders"]))
        if mode == "video":
            return ("video", tuple(self.cfg["video_folders"]), tuple(self.cfg["video_files"]), self.cfg["video_sort"], lock)
        return ("music", tuple(self.cfg["folders"].items()), lock)

    def _switch_refresh(self):
        """切換模式後更新清單: 掃過、而且設定沒變，就直接用記憶體裡的清單；否則才真的去掃硬碟。
        (在播放器開著的時候，用別的程式新增 / 刪除了檔案，要按「重新掃描」才會看到)"""
        if self._scan_sig_ok.get(self.mode) == self._scan_sig(self.mode):
            LOG.info("切換模式: 沿用上次掃描的%s清單，不重掃", "影片" if self.mode == "video" else "音樂")
            if self.mode == "video":
                self._v_filter()
            else:
                self._filter_tracks()
            return
        LOG.info("切換模式: 需要掃描%s清單", "影片" if self.mode == "video" else "音樂")
        self.refresh_tracks()

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
    def open_download(self, kind=None):
        kind = kind or ("video" if self.mode == "video" else "music")
        if not self.cfg.get("dl_ack"):
            ok = QMessageBox.question(
                self, "下載音樂 / 影片 (進階功能)",
                "這是進階功能，不保證在每一台電腦都能用 (需要另外下載 yt-dlp 和 ffmpeg，可能被防毒或網路擋住)。\n\n"
                "下載內容是否符合網站規定與著作權，請自行判斷，使用上的後果由使用者自己負責。\n\n"
                "要繼續嗎？", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes
            if not ok:
                return
            self.cfg["dl_ack"] = True
            save_cfg(self.cfg)
        if self._dl_dlg is None:
            self._dl_dlg = DownloadDialog(self, kind)
        else:
            self._dl_dlg.set_kind(kind)
        self._dl_dlg.show()
        self._dl_dlg.raise_()
        self._dl_dlg.activateWindow()
        QTimer.singleShot(300, self._dl_dlg.ensure_tools)      # 缺 yt-dlp / ffmpeg 就自動下載

    def after_download(self, path, kind="music"):
        """下載完成: 資料夾還沒在清單裡就加進去 (已被勾選的上層資料夾涵蓋就不用)，然後重新掃描"""
        path = os.path.normpath(path)
        if kind == "video":
            if self.mode == "video":
                self._v_add_paths([path])
            elif not any(path == f or self._v_under(path, f) for f in self.cfg["video_folders"]):
                self.cfg["video_folders"].append(path)      # 目前在音樂模式: 先記下來，切到影片就看得到
                save_cfg(self.cfg)
            return
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
        if self.mode == "video":
            self._v_filter()
            return
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
        elif mode == RECENT_SORT:
            cutoff = time.time() - RECENT_DAYS * 86400                     # 只留最近 7 天內新增的，新的排前面；超過一週自動消失
            added = {p: self._added_cache.setdefault(p, added_time(p)) for _, p in items}      # 用路徑判斷 (內容一樣的兩個檔案也各算各的)
            items = [t for t in items if added[t[1]] >= cutoff]
            items.sort(key=lambda t: -added[t[1]])
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
        if self.mode == "video":
            self._save_mark("換影片")                      # 換影片之前，先記下上一部看到哪
            self._v_resume, self._v_resume_n, self._v_resume_t0 = None, 0, time.time()
        try:
            if self.mode == "video":
                url = QUrl.fromLocalFile(path)
                if self.vplayer.source() == url:          # 同一部再播一次 (單曲循環): 回到開頭
                    self.vplayer.setPosition(0)
                else:
                    self.vplayer.setSource(url)
                    mk = self.cfg["video_marks"].get(fid)
                    if mk and mk.get("pos", 0) > 5:
                        self._v_resume = mk["pos"]
                self.vplayer.play()
            else:
                self._music_load(path)
                pygame.mixer.music.play()
        except Exception as e:
            self.now_lbl.setText(f"無法播放: {os.path.basename(path)} ({e})")
            return
        self.cur, self.play_start, self.last_tick = (fid, path), 0.0, time.time()
        self._mark_ui()
        h = self.__dict__.setdefault("_play_hist", [])            # 播放紀錄: 亂跳時「上一首」要回到剛剛聽的那首，不是清單裡的鄰居
        if not h or h[-1] != fid:
            h.append(fid)
            del h[:-300]
        self.counted = self.paused = False
        self.pos = 0.0
        self.length = 0.0 if self.mode == "video" else track_length(path)      # 影片長度等播放器讀到再填
        self.tot_lbl.setText(fmt(self.length) if self.length else "--:--")
        self.seek.blockSignals(True)
        self.seek.setValue(0)
        self.seek.blockSignals(False)
        self.now_lbl.setText("正在播放: " + os.path.splitext(os.path.basename(path))[0])
        if getattr(self, "tray", None):
            self.tray.setToolTip(os.path.splitext(os.path.basename(path))[0][:60])
        self._slide_on_song()
        if self.mode == "video":
            self._v_highlight(path)
            self._hook_video_children()
            if VIDEO_OK and not self.search.hasFocus():
                self.video_view.setFocus()
        else:
            it = self.tree.topLevelItem(idx)
            if it:
                self.tree.setCurrentItem(it)
                self.tree.scrollToItem(it)

    def _music_load(self, path):
        """載入歌曲。預設先把整首讀進記憶體再播 (≤100MB)，播放中就不用再讀硬碟，
        遊戲讀地圖、下載、掃描搶硬碟的時候，音樂才不會頓。讀不進來就退回原本的邊讀邊播。"""
        if self.cfg.get("preload_audio", True):
            try:
                if os.path.getsize(path) <= 100 * 1024 * 1024:
                    with open(path, "rb") as f:
                        buf = io.BytesIO(f.read())
                    pygame.mixer.music.load(buf, os.path.splitext(path)[1].lstrip("."))
                    self._music_buf = buf                    # 要一直拿著，不然會被回收掉
                    return
            except Exception as e:
                LOG.warning("整首讀進記憶體失敗，改用邊讀邊播: %s (%s)", os.path.basename(path), e)
        self._music_buf = None
        pygame.mixer.music.load(path)

    def _finish_current(self, skipped):
        if self.cur and self.mode != "video" and not self.counted and skipped and self.play_start < SKIP_BEFORE_S:
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
        self._wx_pi = 0                       # 目前輪到第幾段預報
        self._wx_rot = QTimer(self)
        self._wx_rot.timeout.connect(self._wx_rotate)
        self._wx_rot.start(5000)             # 每 5 秒換下一段
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
            cb.toggled.connect(lambda on, k=key: (self.cfg.__setitem__(k, on), save_cfg(self.cfg)))
            v.addWidget(cb)
        self.cb_daily = QCheckBox("7點/12點/19點 報天氣與降雨機率")
        self.cb_daily.setToolTip("每天早上7點、中午12點、晚上7點，報時間後接著報氣溫、天氣狀況、降雨機率。需要先在「☁ 天氣設定」選好地區；就算「整點報時」沒勾也會報。")
        self.cb_daily.setChecked(self.cfg["wx_daily"])
        self.cb_daily.toggled.connect(lambda on: (self.cfg.__setitem__("wx_daily", on), save_cfg(self.cfg)))
        v.addWidget(self.cb_daily)
        self.cb_wx = QCheckBox("報時時一併報氣溫")
        self.cb_wx.setChecked(self.cfg["chime_weather"])
        self.cb_wx.toggled.connect(lambda on: (self.cfg.__setitem__("chime_weather", on), save_cfg(self.cfg)))
        v.addWidget(self.cb_wx)
        v.addWidget(self._sec("聲音 (只列女聲)"))
        self.voice_cb = QComboBox()
        self.voice_cb.addItem(VOICE_AUTO)
        if edge_available():
            for k in EDGE_VOICES:
                self.voice_cb.addItem(k)
        self.voice_cb.currentTextChanged.connect(lambda t: (self.cfg.__setitem__("voice", "" if t == VOICE_AUTO else t), save_cfg(self.cfg)))
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
        self._last_chime = (now.date(), now.hour, 0 if now.minute < 30 else 30)     # 啟動當下那個時段不報
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
        if self.mode == "video":                       # 影片模式和音樂模式各自記各自的抽屜開關
            self._vdrawer_open = not self._vdrawer_open
        else:
            self.cfg["drawer_open"] = not self.cfg["drawer_open"]
        self._apply_drawer()

    def _apply_drawer(self, animate=True):
        on = self._vdrawer_open if self.mode == "video" else self.cfg["drawer_open"]
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
        if self.vplayer:
            self.vplayer.stop()
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
                txt = f"剩餘 {fmt(left)}"
                (self.st_min if self.btn_min.text() == "取消" else self.st_at).setText(txt)
        if self.stop_after_songs:
            self.st_cnt.setText(f"還會播完 {self.stop_after_songs} 首")
        # 整點 / 半點報時
        # 每個「整點/半點」只報一次；就算那一秒剛好被卡住，1 分鐘內補上也照報
        slot_min = 0 if dt.minute in (0, 1) else 30 if dt.minute in (30, 31) else None
        if slot_min is not None:
            key = (dt.date(), dt.hour, slot_min)
            if key != self._last_chime:
                self._last_chime = key
                daily = bool(self.cfg["wx_daily"]) and slot_min == 0 and dt.hour in (7, 12, 19)
                if daily or (slot_min == 0 and self.cfg["chime_hour"]) or (slot_min == 30 and self.cfg["chime_half"]):
                    self._announce(daily=daily)

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

    def _announce(self, test=False, daily=False):
        if self._speaking:
            if time.time() - getattr(self, "_speak_t0", 0) < 45:
                return
            # 上一次播報卡住超過 45 秒 (線上語音沒回應等): 當作失敗，這次照常播
        self._speaking = True
        self._speak_t0 = time.time()
        # 報時時把音樂壓低，講完再恢復
        try:
            self._set_play_vol(self.cfg["volume"] / 100 * 0.25)
        except Exception:
            pass
        text = self._time_text(datetime.datetime.now()) + self._weather_phrase(full=daily)
        note = ""
        if (self.cfg["chime_weather"] or daily) and not self._weather_phrase(full=daily):
            if not (self.cfg["weather_name"] or "").strip():
                note = "\n(還沒設定地區名稱，所以只報時間)" if self._has_place() else "\n(還沒設定地區，所以只報時間；可按「☁ 天氣設定」)"
            elif not self.weather:
                note = "\n(天氣還沒讀到，這次只報時間)"
            else:
                note = "\n(氣象資料太舊，這次只報時間)"
        self.speak_lbl.setText("播報: " + text + note)
        self.speaker.speak(text, self.cfg.get("voice", ""), int(self.cfg.get("voice_rate", 0)))

    def _speak_done(self, code):
        self._speaking = False
        try:
            self._set_play_vol(self.cfg["volume"] / 100)
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
        _SRC_HINT[0] = (self.cfg.get("update_src") or "").strip()
        self._wx_busy = True
        self.net.run("wx", fetch_weather, float(self.cfg["weather_lat"]), float(self.cfg["weather_lon"]), bool(force))

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

        t = wx.get("temp")
        warn = ""
        dts = wx.get("data_ts")
        if dts and time.time() - dts > 90 * 60:           # 倉庫那邊的資料超過 1.5 小時沒更新: 直接標出資料時間，不要讓人以為是即時的
            warn = f"<br><span style='font-size:12px; font-weight:normal'>⚠ 資料時間 {datetime.datetime.fromtimestamp(dts).strftime('%m/%d %H:%M')} (較舊)</span>"
        self.wx_now.setText(f"{wx_icon(wx.get('code'))} {('?' if t is None else round(t))}°C"
                            f"<br><span style='font-size:13px; font-weight:normal'>{wx.get('text') or wx_text(wx.get('code'))}</span>" + warn)
        self._wx_pi = 0
        self._render_wx_range()
        if wx.get("periods"):
            self.wx_days.setText("")                       # 時段已經包含明天，不重複顯示
        else:
            names = ("今天", "明天", "後天")
            self.wx_days.setText("\n".join(
                f"{names[i]} {_deg(d['tmin'])}~{_deg(d['tmax'])}　{wx_icon(d['code'])} {d.get('text') or wx_text(d['code'])}"
                for i, d in enumerate(wx["days"][1:3], start=1)))

    def _render_wx_range(self):
        wx = self.weather
        items = wx_items(wx) if wx else []
        if not items:
            return
        i = self._wx_pi % len(items)
        it = items[i]
        line = f"{it['label']}　{wx_icon(it.get('code'))} {it.get('text') or wx_text(it.get('code'))}\n{_deg(it.get('tmin'))} ~ {_deg(it.get('tmax'))}"
        if it.get("pop") is not None:
            line += f"　降雨機率 {round(it['pop'])}%"
        if len(items) > 1:
            line += "　" + "".join("●" if k == i else "○" for k in range(len(items)))
        self.wx_range.setText(line)

    def _wx_rotate(self):
        if self.weather and self._has_place():
            self._wx_pi += 1
            self._render_wx_range()

    @staticmethod
    def _deg_words(n):
        return f"零下{-n}度" if n < 0 else f"{n}度"

    def _weather_phrase(self, full=False):
        """報時後面接的氣溫那一句，例如「，二崙鄉今天氣溫22度到28度」。沒設定地區、或沒讀到天氣就回傳空字串。"""
        if not (self.cfg["chime_weather"] or full):
            return ""
        wx, name = self.weather, (self.cfg["weather_name"] or "").strip()
        if not wx or not name or time.time() - wx["ts"] > 7200:
            return ""
        if wx.get("data_ts") and time.time() - wx["data_ts"] > 6 * 3600:      # 倉庫資料超過 6 小時沒更新，不報以免報到舊資料
            return ""
        if full:
            its = wx_items(wx)
            p = its[0] if its else None
            if p and wx.get("periods") and p.get("tmin") is not None and p.get("tmax") is not None:
                lo, hi = round(p["tmin"]), round(p["tmax"])
                rng = self._deg_words(hi) if lo == hi else f"{self._deg_words(lo)}到{self._deg_words(hi)}"
                s = f"，{name}{p['label']}氣溫{rng}，{p.get('text') or wx_text(p.get('code'))}"
                if p.get("pop") is not None:
                    s += f"，降雨機率百分之{round(p['pop'])}"
                return s
        d = wx["days"][0]
        if d["tmin"] is not None and d["tmax"] is not None:
            lo, hi = round(d["tmin"]), round(d["tmax"])
            rng = self._deg_words(hi) if lo == hi else f"{self._deg_words(lo)}到{self._deg_words(hi)}"
        elif wx.get("temp") is not None:
            rng = self._deg_words(round(wx["temp"]))
        else:
            return ""
        s = f"，{name}今天氣溫{rng}"
        if full or self.cfg["chime_weather_extra"]:
            s += f"，{d.get('text') or wx_text(d['code'])}"
            if d.get("pop") is not None and (full or d["pop"] >= 20):       # 每天固定報天氣時，降雨機率一定要報 (0% 也報)
                s += f"，降雨機率百分之{round(d['pop'])}"
        return s

    def _toggle_tools(self):
        self.cfg["tools_open"] = not self.cfg["tools_open"]
        save_cfg(self.cfg)
        self._apply_tools(animate=True)

    def _apply_tools(self, animate=False):
        on = bool(self.cfg["tools_open"])
        box = self.tools_box
        self.tools_bar.setText("▼" if on else "▲")
        self.tools_bar.setToolTip("收起下面的按鈕，讓資料夾清單變高" if on else "展開下面的按鈕")
        a = getattr(self, "_tools_anim", None)
        if a is not None:
            a.stop()
        if not animate:
            box.setMaximumHeight(16777215 if on else 0)
            box.setVisible(on)
            return
        full = max(box.layout().sizeHint().height(), 1)
        self._tools_anim = a = QVariantAnimation(self)
        a.setDuration(220)
        a.setEasingCurve(QEasingCurve.OutCubic)
        a.valueChanged.connect(lambda v: box.setMaximumHeight(int(v)))
        if on:
            box.setMaximumHeight(0)
            box.setVisible(True)
            a.setStartValue(0)
            a.setEndValue(full)
            a.finished.connect(lambda: box.setMaximumHeight(16777215))
        else:
            a.setStartValue(box.height())
            a.setEndValue(0)
            a.finished.connect(lambda: box.setVisible(False))
        a.start()

    def _toggle_folders(self):
        self.cfg["folders_open"] = not self.cfg["folders_open"]
        self._apply_folders_open()

    def _apply_folders_open(self):
        on = self.cfg["folders_open"] and self.mode != "video"
        self.fold_btn.setVisible(self.mode != "video")
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
            self._set_fullscreen(False)
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
            self.play_index(self._shuffle_pick())
        else:
            self.play_index((self._cur_index() + 1) % len(self.view))

    # 亂跳的狀態: off=照順序 (按鈕不亮)；其餘 = 亮起，各種抽法
    SHUF_NAMES = {"off": "➡ 照順序", "even": "🔀 平均亂跳", "hot": "🔥 常聽優先", "cold": "🧊 冷門優先", "explore": "🧭 探索"}
    SHUF_TIPS = {"off": "照清單順序播", "even": "每首機率一樣，整份清單播過一輪才重來",
                 "hot": "常聽的歌比較容易被抽到", "cold": "不常聽的歌比較容易被抽到",
                 "explore": "平常偏常聽，但每隔幾首就穿插一首冷門 / 沒聽過的歌"}

    def _shuf_state(self):
        """目前的亂跳狀態。影片模式只有 照順序 / 平均亂跳"""
        if not self.cfg["shuffle"]:
            return "off"
        return "even" if self.mode == "video" else self.cfg["shuffle_mode"]

    def _shuf_states(self):
        return ["off", "even"] if self.mode == "video" else ["off", "even", "hot", "cold", "explore"]

    def _shuf_label(self):
        st = self._shuf_state()
        self.shuffle_cb.setText(self.SHUF_NAMES[st])
        self.shuffle_cb.setToolTip(
            "目前狀態：" + self.SHUF_NAMES[st] + "\n" + self.SHUF_TIPS[st] + "\n按一下換下一種" + ("" if self.mode == "video" else "，按右鍵可以直接選 / 從冷門歌開始播") +
            "\n亮起 = 亂跳：先點選一首歌再按，就從那首開始；什麼都沒選就直接隨機挑一首")

    def _shuf_set(self, st):
        """切到某個狀態 (按鈕、右鍵選單共用)"""
        on = st != "off"
        if on:
            self.cfg["shuffle_mode"] = st
        self._shuf_bag, self._shuf_recent = [], []
        save_cfg(self.cfg)
        if self.shuffle_cb.isChecked() != on:
            self.shuffle_cb.setChecked(on)              # 會觸發 _shuffle_toggled (沒在播時順便開始播)
        self._shuf_label()

    def _shuf_cycle(self):
        sts = self._shuf_states()
        cur = self._shuf_state()
        self._shuf_set(sts[(sts.index(cur) + 1) % len(sts)] if cur in sts else sts[1])

    def _shuf_menu(self, pos):
        m = QMenu(self)
        cur = self._shuf_state()
        for st in self._shuf_states():
            m.addAction(("✔ " if st == cur else "\u3000") + self.SHUF_NAMES[st] + "　" + self.SHUF_TIPS[st], lambda k=st: self._shuf_set(k))
        if self.mode != "video":
            m.addSeparator()
            m.addAction("📉 從最冷門的歌開始依序播", self.play_cold)
        m.exec(self.shuffle_cb.mapToGlobal(pos))

    def _shuffle_pick(self):
        """亂跳挑下一首 (回傳在 self.view 裡的位置)。
        even = 洗牌袋: 每首機率一樣，整份清單每首都播過一輪才重來。
        hot / cold = 加權抽籤 (常聽 / 不常聽的比較容易中)，但最近播過的幾首先排除，才不會一直回到同一首。"""
        cur = self._cur_index()
        ids = [fid for fid, _ in self.view]
        cur_id = self.view[cur][0] if cur >= 0 else None
        mode = "even" if self.mode == "video" else self.cfg["shuffle_mode"]     # 影片模式一律平均抽
        if mode == "even" or len(ids) < 3:
            have = set(ids)
            bag = [f for f in getattr(self, "_shuf_bag", []) if f in have]     # 清單變了就把不在清單裡的拿掉
            if not bag:
                bag = [f for f in ids if f != cur_id] or ids[:]
                random.shuffle(bag)
            self._shuf_bag = bag
            fid = bag.pop()
        else:
            recent = getattr(self, "_shuf_recent", [])
            keep = min(len(ids) // 4, 30)                                       # 最近播過的這幾首先不抽
            block = set(recent[-keep:] if keep else []) | {cur_id}
            pool = [f for f in ids if f not in block] or [f for f in ids if f != cur_id] or ids
            plays = {f: self.stats.info(f)[0] for f in pool}
            if mode == "explore":                                               # 探索: 大約 3 首裡有 1 首走冷門路線，其餘偏常聽
                mode = "cold" if random.random() < 0.34 else "hot"
            if mode == "hot":
                w = [1 + plays[f] for f in pool]
            else:
                w = [1.0 / (1 + plays[f]) for f in pool]
            fid = random.choices(pool, weights=w)[0]
            self._shuf_recent = (recent + [fid])[-60:]
        for i, (f, _) in enumerate(self.view):
            if f == fid:
                return i
        return 0

    def _shuffle_toggled(self, on):
        """亂跳按亮 = 隨機播放。正在播就繼續播、下一首起隨機；還沒播的話: 有選歌就從那首開始，沒選就隨機挑一首"""
        self.cfg["shuffle"] = on
        self._shuf_bag, self._shuf_recent = [], []
        self._shuf_label()
        if not on or self.cur or not self.view:
            return
        if self.mode == "video":
            it = self.vtree.currentItem()
            picked = it is not None and it.data(0, Qt.UserRole + 2) == "file"
        else:
            picked = self.tree.currentItem() is not None
        if picked:
            self.toggle()                 # 從選到的那首開始
        else:
            self.next()                   # 沒選歌: 直接隨機挑一首

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
        if not self.view:
            return
        if self.shuffle_cb.isChecked():
            h = getattr(self, "_play_hist", [])
            if h and self.cur and h[-1] == self.cur[0]:
                h.pop()                                           # 目前這首
            while h:
                fid = h.pop()                                     # 往回找最近一首還在清單裡的 (play_index 會再把它記回去)
                for i, (f, _) in enumerate(self.view):
                    if f == fid:
                        self.play_index(i)
                        return
        self.play_index((self._cur_index() - 1) % len(self.view))

    def toggle(self):
        if not self.cur:
            if self.mode == "video":
                it = self.vtree.currentItem()
                p = it.data(0, Qt.UserRole) if it is not None and it.data(0, Qt.UserRole + 2) == "file" else None
                self.play_index(next((i for i, (_, vp) in enumerate(self.view) if vp == p), 0))
                return
            it = self.tree.currentItem()
            self.play_index(it.data(0, Qt.UserRole) if it else 0)
            return
        if self.paused:
            if self.mode == "video":
                self.vplayer.play()
            else:
                pygame.mixer.music.unpause()
            self.last_tick = time.time()
        else:
            if self.mode == "video":
                self.vplayer.pause()
                self._save_mark("暫停")
            else:
                pygame.mixer.music.pause()
        self.paused = not self.paused

    # ---------------- 影片清單 (資料夾可展開、可勾選、可拖進來) ----------------
    # ---------------- 影片排序 (名稱 / 檔案大小 大→小 / 小→大) ----------------
    def _v_size(self, p):
        v = self._v_sz.get(p)
        if v is None:
            try:
                v = os.path.getsize(p)
            except OSError:
                v = 0
            self._v_sz[p] = v
        return v

    def _v_ctime(self, p):
        """檔案的「建立日期」(Windows: 這個檔案出現在這顆硬碟的時間；複製 / 下載進來的檔案就是那一刻)"""
        k = ("ct", p)
        v = self._v_sz.get(k)
        if v is None:
            v = self._v_sz[k] = added_time(p)
        return v

    def _v_sorted(self, vids):
        """vids 是 [(檔名, 路徑)]。名稱模式維持原本順序；大小模式依檔案大小排 (大小一樣的再依名稱)"""
        mode = self.cfg["video_sort"]
        if mode == "name":
            return list(vids)
        if mode.startswith("ctime"):
            return sorted(vids, key=lambda v: (self._v_ctime(v[1]), v[0].lower()), reverse=(mode == "ctime_desc"))
        return sorted(vids, key=lambda v: (self._v_size(v[1]), v[0].lower()), reverse=(mode == "size_desc"))

    def _v_sort_label(self):
        short = {"name": "名稱", "size_desc": "大小(大→小)", "size_asc": "大小(小→大)",
                 "ctime_desc": "建立日期(新→舊)", "ctime_asc": "建立日期(舊→新)"}
        self.v_sort_btn.setText("排序：" + short[self.cfg["video_sort"]] + " ▾")

    def _v_sort_menu(self):
        m = QMenu(self)
        for key, label in V_SORTS:
            m.addAction(("✔ " if self.cfg["video_sort"] == key else "\u3000") + label, lambda k=key: self._v_set_sort(k))
        m.exec(self.v_sort_btn.mapToGlobal(QPoint(0, self.v_sort_btn.height())))

    def _v_set_sort(self, key):
        if key == self.cfg["video_sort"]:
            return
        self.cfg["video_sort"] = key
        save_cfg(self.cfg)
        self._v_sort_label()
        self._v_reload()                      # 重建清單 (勾選、展開狀態都會保留)；播放順序也跟著新的排列走

    def _v_fid(self, p):
        return hashlib.md5(p.encode("utf-8", "ignore")).hexdigest()       # 影片很大，不讀檔內容，用路徑當編號

    def _v_make(self, kind, path, name, count=0):
        it = QTreeWidgetItem()
        it.setData(0, Qt.UserRole, path)
        it.setData(0, Qt.UserRole + 1, name)
        it.setData(0, Qt.UserRole + 2, kind)          # file / dir / root / files
        it.setData(0, Qt.UserRole + 3, count)
        if not path.startswith("::"):
            it.setToolTip(0, path)
        fl = it.flags() | Qt.ItemIsUserCheckable
        if kind == "file":
            it.setText(0, name)
        else:
            fl |= Qt.ItemIsAutoTristate               # 勾資料夾 = 底下全勾，只勾一部分 = 半勾
        it.setFlags(fl)
        it.setCheckState(0, Qt.Checked)
        return it

    def _v_collect(self, folder, depth=0):
        """掃資料夾: 回傳 (子資料夾們, 影片們, 影片總數)；裡面完全沒有影片就回 None"""
        try:
            ents = sorted(os.scandir(folder), key=lambda e: e.name.lower())
        except OSError:
            return None
        dirs, vids, total = [], [], 0
        for e in ents:
            try:
                if e.is_dir(follow_symlinks=False):
                    if self._is_locked_path(e.path):
                        continue
                    sub = self._v_collect(e.path, depth + 1) if depth < 12 else None
                    if sub:
                        dirs.append((e.name, e.path, sub))
                        total += sub[2]
                elif e.name.lower().endswith(VIDEO_EXTS) and e.is_file():
                    vids.append((e.name, e.path))
            except OSError:
                pass
        total += len(vids)
        return (dirs, vids, total) if total else None

    def _v_leaf(self, parent, path, name, off):
        f = self._v_make("file", path, name)
        mode = self.cfg["video_sort"]                       # 依大小 / 建立日期排序時，名字後面顯示大小 / 日期，才看得出排得對不對
        if mode.startswith("size"):
            f.setText(0, f"{name}   ·  {human_size(self._v_size(path))}")
        elif mode.startswith("ctime"):
            f.setText(0, f"{name}   ·  {time.strftime('%Y-%m-%d %H:%M', time.localtime(self._v_ctime(path)))}")
        parent.addChild(f)
        if path in off:
            f.setCheckState(0, Qt.Unchecked)
        self._v_items[path] = f

    def _v_fill(self, parent, data, off):
        dirs, vids, _ = data
        for name, path, sub in dirs:
            d = self._v_make("dir", path, name, sub[2])
            parent.addChild(d)
            self._v_fill(d, sub, off)
        for name, path in self._v_sorted(vids):
            self._v_leaf(parent, path, os.path.splitext(name)[0], off)

    def _v_dirs(self):
        st = [self.vtree.topLevelItem(i) for i in range(self.vtree.topLevelItemCount())]
        while st:
            it = st.pop()
            if it.data(0, Qt.UserRole + 2) != "file":
                yield it
                st.extend(it.child(i) for i in range(it.childCount()))

    def _v_label(self, it):
        kind = it.data(0, Qt.UserRole + 2)
        if kind == "file":
            return
        arrow = "▾" if it.isExpanded() else "▸"
        warn = "   ⚠ 找不到這個資料夾" if kind == "root" and not os.path.isdir(it.data(0, Qt.UserRole)) else ""
        it.setText(0, f"{arrow} 📁 {it.data(0, Qt.UserRole + 1)}  ({it.data(0, Qt.UserRole + 3)}){warn}")

    def _v_reload(self):
        """重新掃描影片資料夾，重建清單 (勾選狀態、展開狀態都會保留)"""
        if self.cfg["video_open"] is None:
            self._v_open |= set(self.cfg["video_folders"])        # 第一次: 頂層資料夾預設展開
            self.cfg["video_open"] = sorted(self._v_open)
        t = self.vtree
        t.blockSignals(True)
        t.clear()
        self._v_items, self._v_sz = {}, {}
        off = set(self.cfg["video_off"])
        for f in self.cfg["video_folders"]:
            if self._is_locked_path(f):
                continue
            data = self._v_collect(f) if os.path.isdir(f) else None
            r = self._v_make("root", f, f, data[2] if data else 0)
            t.addTopLevelItem(r)
            if data:
                self._v_fill(r, data, off)
        files = [p for p in self.cfg["video_files"] if os.path.isfile(p) and not self._is_locked_path(p)]
        if files:
            r = self._v_make("files", "::files", "單獨加入的影片", len(files))
            t.addTopLevelItem(r)
            for _n, p in self._v_sorted([(os.path.basename(q), q) for q in files]):
                self._v_leaf(r, p, os.path.splitext(_n)[0], off)
        t.blockSignals(False)
        t.blockSignals(True)                      # 展開時不要觸發存檔，最後統一整理
        for it in self._v_dirs():
            it.setExpanded(it.data(0, Qt.UserRole) in self._v_open)
            self._v_label(it)
        t.blockSignals(False)
        self._v_filter()
        self._scan_sig_ok["video"] = self._scan_sig("video")

    def _v_filter(self):
        """搜尋框: 只留下名稱有符合的影片 (資料夾自動展開)；然後重新算「要播的清單」"""
        q = self.search.text().strip().lower()

        def walk(it):
            if it.data(0, Qt.UserRole + 2) == "file":
                ok = (not q) or q in os.path.basename(it.data(0, Qt.UserRole)).lower()
            else:
                ok = False
                for i in range(it.childCount()):
                    if walk(it.child(i)):
                        ok = True
                if q and ok:
                    it.setExpanded(True)
                    self._v_label(it)
                ok = ok or not q
            it.setHidden(not ok)
            return ok

        self.vtree.setUpdatesEnabled(False)
        for i in range(self.vtree.topLevelItemCount()):
            walk(self.vtree.topLevelItem(i))
        self.vtree.setUpdatesEnabled(True)
        self._v_commit()

    def _v_commit(self):
        """依照勾選狀態重算播放清單 (self.view)，並把「沒勾的影片」存起來"""
        view, off, total = [], [], [0]

        def walk(it):
            if it.data(0, Qt.UserRole + 2) == "file":
                total[0] += 1
                p = it.data(0, Qt.UserRole)
                if it.checkState(0) != Qt.Checked:
                    off.append(p)
                elif not it.isHidden():
                    view.append((self._v_fid(p), p))
            else:
                for i in range(it.childCount()):
                    walk(it.child(i))

        for i in range(self.vtree.topLevelItemCount()):
            walk(self.vtree.topLevelItem(i))
        self.view = view
        self.cfg["video_off"] = off
        save_cfg(self.cfg)
        if total[0]:
            self.v_count_lbl.setText(f"　已勾選 {total[0] - len(off)} / 共 {total[0]} 部")
        else:
            self.v_count_lbl.setText("　把影片資料夾拖進這個清單，或按「＋ 加入資料夾」")

    def _v_set_all(self, on):
        for i in range(self.vtree.topLevelItemCount()):
            self.vtree.topLevelItem(i).setCheckState(0, Qt.Checked if on else Qt.Unchecked)
        self._v_commit()

    def _v_expand_changed(self, it):
        p = it.data(0, Qt.UserRole)
        (self._v_open.add if it.isExpanded() else self._v_open.discard)(p)
        self.cfg["video_open"] = sorted(self._v_open)
        self._v_label(it)

    def _v_clicked(self, it, _c):
        """點影片名稱 = 播放；點資料夾 = 展開 / 收合；點最左邊的勾選框 = 只勾選，不播放"""
        pos = self.vtree.viewport().mapFromGlobal(QCursor.pos())
        if pos.x() <= self.vtree.visualItemRect(it).left() + 30:
            return
        now = time.time()
        last_it, last_t = self._v_last
        self._v_last = (it, now)
        if last_it is it and now - last_t < QApplication.doubleClickInterval() / 1000:
            return                                  # 雙擊的第二下，不要重複動作
        if it.data(0, Qt.UserRole + 2) != "file":
            it.setExpanded(not it.isExpanded())
            return
        if it.checkState(0) != Qt.Checked:          # 點到沒勾的影片: 順便幫你勾起來
            it.setCheckState(0, Qt.Checked)
            self._v_commit()
        p = it.data(0, Qt.UserRole)
        for i, (_, vp) in enumerate(self.view):
            if vp == p:
                self.play_index(i)
                return

    def _v_highlight(self, path):
        it = self._v_items.get(path)
        if it is None:
            return
        p = it.parent()
        while p is not None:
            p.setExpanded(True)
            p = p.parent()
        self.vtree.setCurrentItem(it)
        self.vtree.scrollToItem(it)

    @staticmethod
    def _v_under(p, folder):
        a, b = os.path.normcase(p), os.path.normcase(folder).rstrip("\\/")
        return a.startswith(b + os.sep)

    def _v_add_paths(self, paths, expand=True):
        """把拖進來 / 選到的資料夾與影片檔加進清單 (資料夾會自動展開)"""
        if self.cfg["video_open"] is None:
            self._v_open |= set(self.cfg["video_folders"])
        for p in paths:
            p = os.path.normpath(p)
            if os.path.isdir(p):
                if any(p == f or self._v_under(p, f) for f in self.cfg["video_folders"]):
                    pass                                      # 已經在清單裡 (或在某個已加入的資料夾裡面)
                else:
                    self.cfg["video_folders"].append(p)
                if expand:
                    self._v_open.add(p)
            elif p.lower().endswith(VIDEO_EXTS) and os.path.isfile(p):
                if p not in self.cfg["video_files"] and not any(self._v_under(p, f) for f in self.cfg["video_folders"]):
                    self.cfg["video_files"].append(p)
                self.cfg["video_off"] = [x for x in self.cfg["video_off"] if x != p]
        self.cfg["video_open"] = sorted(self._v_open)
        save_cfg(self.cfg)
        self._v_reload()

    def _v_add_dialog(self):
        d = QFileDialog.getExistingDirectory(self, "選擇影片資料夾")
        if d:
            self._v_add_paths([d])

    def _v_add_files_dialog(self):
        fs, _ = QFileDialog.getOpenFileNames(self, "選擇影片檔", "", "影片 (" + " ".join("*" + e for e in VIDEO_EXTS) + ")")
        if fs:
            self._v_add_paths(fs)

    def _v_menu(self, pos):
        it = self.vtree.itemAt(pos)
        if it is None:
            return
        kind, path = it.data(0, Qt.UserRole + 2), it.data(0, Qt.UserRole)
        m = QMenu(self)
        if kind == "root":
            m.addAction("從清單移除這個資料夾 (不會刪檔案)", lambda: self._v_remove("video_folders", path))
        elif kind == "files":
            m.addAction("清除所有單獨加入的影片 (不會刪檔案)", lambda: self._v_remove("video_files", None))
        elif kind == "file" and path in self.cfg["video_files"]:
            m.addAction("從清單移除這部影片 (不會刪檔案)", lambda: self._v_remove("video_files", path))
        if m.isEmpty():
            return
        m.exec(self.vtree.viewport().mapToGlobal(pos))

    def _v_remove(self, key, path):
        self.cfg[key] = [] if path is None else [x for x in self.cfg[key] if x != path]
        save_cfg(self.cfg)
        self._v_reload()

    def _v_meta(self):
        """播放器讀到影片資料後，把解析度顯示在歌名那一行"""
        if self.mode != "video" or not self.cur:
            return
        try:
            from PySide6.QtMultimedia import QMediaMetaData
            r = self.vplayer.metaData().value(QMediaMetaData.Key.Resolution)
            if r is not None and r.width() > 0:
                self.now_lbl.setText("正在播放: " + os.path.splitext(os.path.basename(self.cur[1]))[0] + f"   [{r.width()}×{r.height()}]")
        except Exception:
            pass

    # ---------------- 音樂 / 影片 模式 ----------------
    def _exts(self):
        return VIDEO_EXTS if self.mode == "video" else EXTS

    def _toggle_mode(self):
        self.set_mode("music" if self.mode == "video" else "video")

    def set_mode(self, mode, first=False):
        """切換音樂 / 影片模式: 先停掉現在播的，換清單 (同樣的勾選資料夾)，影片模式會多出畫面。"""
        if mode == "video" and not VIDEO_OK:
            if FROZEN:
                why = "這個 EXE 打包時沒有包進 QtMultimedia。\n請用裝好 PySide6-Addons 的環境重新打包，並加上 --hidden-import PySide6.QtMultimedia --hidden-import PySide6.QtMultimediaWidgets。"
            else:
                why = "已嘗試自動安裝但還是載入失敗，請關閉播放器後重新開啟一次。"
            msg = why + (f"\n\n錯誤原因:\n{MM_ERR}" if MM_ERR else "")
            msg += "\n\n如果你用的是 Windows N / KN 版，要先到「設定 > 應用程式 > 選用功能」安裝「媒體功能套件 (Media Feature Pack)」。"
            QMessageBox.information(self, "無法播放影片", msg)
            mode = "music"
        if not first and mode == self.mode:
            return
        lap = _Lap("切換模式 → " + mode)
        if not first:
            self._stop_all()
            self.cur, self.paused, self.pos, self.length = None, False, 0.0, 0.0
        lap.mark("停止播放")
        self.mode = mode
        self.cfg["mode"] = mode
        vid = mode == "video"
        self.video_panel.setVisible(vid)
        self.mode_btn.setText("🎬 影片" if vid else "🎵 音樂")
        self.mode_btn.setToolTip("目前是影片播放器，點一下切回音樂" if vid else "目前是音樂播放器，點一下切換成影片")
        self.mode_btn.setProperty("vid", "1" if vid else "0")
        self.mode_btn.style().unpolish(self.mode_btn)
        self.mode_btn.style().polish(self.mode_btn)
        self.tree.headerItem().setText(1, "影片名稱" if vid else "歌名")
        self.tree.setVisible(not vid)                    # 影片模式沒有排行榜，改用可勾選的資料夾清單
        self.video_box.setVisible(vid)
        self.sort_cb.setVisible(not vid)
        self.sort_lbl.setVisible(not vid)
        self._shuf_label()                           # 影片模式: 只有 照順序 / 平均亂跳
        self.stop_btn.setVisible(vid)
        self.list_btn.setVisible(vid)
        self.mark_btn.setVisible(vid)
        self._mark_ui()
        self.fs_btn.setVisible(vid and VIDEO_OK)
        self.tool_r2w.setVisible(not vid)            # 影片模式: 收起背景 / 桌布那一排，畫面更大
        lap.mark("切換元件")
        self._apply_video_layout()
        lap.mark("版面")
        self._apply_drawer(animate=not first)             # 進影片模式: 抽屜自動收起來，把空間讓給畫面；回音樂模式: 恢復原本的開關
        lap.mark("抽屜")
        if vid:
            self._hook_video_children()
        self._apply_folders_open()
        lap.mark("資料夾面板")
        self.search.setPlaceholderText("輸入影片名稱關鍵字" if vid else "輸入歌名關鍵字")
        self.now_lbl.setText("(未播放)")
        if not first:
            self.search.blockSignals(True)
            self.search.setText("")
            self.search.blockSignals(False)
            save_cfg(self.cfg)
            lap.mark("存設定")
            self._switch_refresh()                    # 這個模式的清單掃過、設定也沒變，就直接用 (不重掃硬碟)
            lap.mark("清單")
        lap.done()

    # ---------------- 影片介面 (像 MPC-HC) ----------------
    def _apply_video_layout(self):
        """影片模式: 左邊大畫面、右邊清單 (可收合)；音樂模式: 清單佔滿"""
        vid = self.mode == "video"
        self.mid.setDirection(QBoxLayout.LeftToRight if vid else QBoxLayout.TopToBottom)
        if vid:
            self.listp.setFixedWidth(max(340, self.listp.minimumSizeHint().width() + 20))     # 清單窄一點 (但不小於上面那排按鈕需要的寬度)
            self.listp.setVisible(bool(self.cfg["video_list_on"]))
        else:
            self.listp.setMinimumWidth(0)
            self.listp.setMaximumWidth(16777215)
            self.listp.setVisible(True)

    def _toggle_vlist(self, on):
        self.cfg["video_list_on"] = bool(on)
        save_cfg(self.cfg)
        self._apply_video_layout()

    # ---------------- 資料夾鎖 (播放器內密碼鎖 + 硬碟隱藏) ----------------
    def _lock_norm(self):
        return [os.path.normcase(os.path.normpath(p)) for p in self.cfg["lock"]["folders"]]

    def _is_locked_path(self, p):
        """這個路徑在被鎖的資料夾裡 (或就是它)，而且目前沒有解鎖 → True，掃描時要略過"""
        if self._unlocked or not self.cfg["lock"]["folders"]:
            return False
        k = os.path.normcase(os.path.normpath(p))
        for f in self._lock_norm():
            if k == f or k.startswith(f.rstrip("\\/") + os.sep):
                return True
        return False

    def _pw_ok(self, pw):
        import hmac
        L = self.cfg["lock"]
        if not L["salt"] or not L["hash"]:
            return False
        return hmac.compare_digest(_pw_hash(pw, bytes.fromhex(L["salt"])), L["hash"])

    def _ask_pw(self, title, text):
        pw, ok = QInputDialog.getText(self, title, text, QLineEdit.EchoMode.Password)
        return pw if ok else None

    def _lock_new_pw(self):
        a = self._ask_pw("設定密碼", "請設定一組密碼 (之後解鎖都要用它，忘記了沒辦法找回):")
        if not a:
            return False
        b = self._ask_pw("設定密碼", "再輸入一次確認:")
        if b is None:
            return False
        if a != b:
            QMessageBox.warning(self, "密碼不一樣", "兩次輸入的密碼不一樣，沒有設定。")
            return False
        salt = os.urandom(16)
        self.cfg["lock"]["salt"] = salt.hex()
        self.cfg["lock"]["hash"] = _pw_hash(a, salt)
        save_cfg(self.cfg)
        return True

    def _lock_ask_verify(self):
        """問密碼。對了才回傳 True；按「忘記密碼」會走強制解除 (做完一律回傳 False，呼叫的人不用再往下做)"""
        dlg = PwDialog(self, "輸入密碼", "請輸入密碼:")
        ok = dlg.exec()
        if dlg.forgot:
            self._lock_force_reset()
            return False
        if not ok:
            return False
        if self._pw_ok(dlg.text()):
            return True
        QMessageBox.warning(self, "密碼不對", "密碼不正確。\n如果忘記了，輸入密碼時按左下角的「🆘 忘記密碼」就可以解除鎖定。")
        return False

    def _lock_force_reset(self):
        """忘記密碼: 不用密碼，直接解除全部鎖定、清掉密碼，硬碟上的隱藏也恢復"""
        L = self.cfg["lock"]
        n = len(L["folders"])
        ans = QMessageBox.question(
            self, "忘記密碼",
            (f"要解除全部 {n} 個資料夾的鎖定，並且清掉密碼嗎？\n\n" if n else "目前沒有鎖定任何資料夾，要把密碼清掉嗎？\n\n")
            + "• 被鎖的資料夾會重新出現在清單裡\n• 硬碟上的隱藏會恢復\n• 之後想再鎖，要重新設一組新密碼\n\n"
              "注意：這樣任何人打開播放器都能解除鎖定。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        for p in list(L["folders"]):
            if os.path.isdir(p):
                restore_on_disk(p, L["orig"].get(p, 0))
        L["folders"], L["orig"], L["salt"], L["hash"] = [], {}, "", ""
        save_cfg(self.cfg)
        self._unlocked = False
        self._lock_refresh()
        QMessageBox.information(self, "資料夾鎖", "已解除全部鎖定，密碼也清掉了。")

    def _lock_change_pw(self):
        if self._lock_ask_verify():
            self._lock_new_pw()

    def _lock_auth(self):
        """要管理鎖之前: 還沒設密碼就先設一個；已經設了就要先輸入密碼 (這次已解鎖就免)"""
        if not self.cfg["lock"]["hash"]:
            return self._lock_new_pw()
        return True if self._unlocked else self._lock_ask_verify()

    def _lock_ui(self):
        n = len(self.cfg["lock"]["folders"])
        for b in getattr(self, "_lock_btns", []):
            b.setText("🔒 重新上鎖" if self._unlocked else f"🔓 解鎖 ({n})")
            b.setEnabled(n > 0 or self._unlocked)
            b.setToolTip("按一下立刻把鎖定的資料夾藏起來" if self._unlocked else "輸入密碼，這次執行期間看得到被鎖的資料夾")

    def _lock_refresh(self):
        if self.cur and self._is_locked_path(self.cur[1]):
            self.stop_play()                       # 正在播的就是被鎖的內容: 先停掉
        self._load_folders()
        self.refresh_tracks()
        self._lock_ui()

    def _lock_toggle(self):
        if self._unlocked:
            self._unlocked = False
            self._lock_refresh()
            return
        if not self.cfg["lock"]["folders"]:
            QMessageBox.information(self, "資料夾鎖", "還沒有鎖定任何資料夾。\n請先按「⚙ 鎖定設定…」，打勾要鎖的資料夾。")
            return
        if self._lock_ask_verify():
            self._unlocked = True
            self._lock_refresh()

    def lock_manage(self):
        if not self._lock_auth():
            return
        seen, paths = set(), []
        for p in list(self.cfg["folders"]) + list(self.cfg["video_folders"]) + list(self.cfg["lock"]["folders"]):
            p = os.path.normpath(p)
            if os.path.normcase(p) not in seen:
                seen.add(os.path.normcase(p))
                paths.append(p)
        if not paths:
            QMessageBox.information(self, "資料夾鎖", "清單裡還沒有資料夾，請先把音樂或影片資料夾加進清單。")
            return
        dlg = LockDialog(self, paths, set(self._lock_norm()), self._lock_change_pw)
        if dlg.exec():
            self._lock_apply(dlg.checked())

    def _lock_apply(self, checked):
        L = self.cfg["lock"]
        old = {os.path.normcase(os.path.normpath(p)): os.path.normpath(p) for p in L["folders"]}
        new = {os.path.normcase(os.path.normpath(p)): os.path.normpath(p) for p in checked}
        added = [new[k] for k in new if k not in old]
        removed = [old[k] for k in old if k not in new]
        bad = []
        for p in added:                                  # 新鎖的: 硬碟上設成隱藏，記下原本的狀態
            if os.path.isdir(p):
                ok, bits = hide_on_disk(p)
                L["orig"][p] = bits
                if not ok:
                    bad.append(p)
        for p in removed:                                # 解除的: 硬碟上的隱藏恢復成原本的樣子
            if os.path.isdir(p):
                restore_on_disk(p, L["orig"].get(p, 0))
            L["orig"].pop(p, None)
        L["folders"] = list(new.values())
        save_cfg(self.cfg)
        if added or not L["folders"]:
            self._unlocked = False                       # 剛鎖好的資料夾，馬上就藏起來
        self._lock_refresh()
        msg = []
        if added:
            msg.append(f"已鎖定 {len(added)} 個資料夾。需要看的時候按「🔓 解鎖」輸入密碼。")
        if removed:
            msg.append(f"已解除 {len(removed)} 個資料夾的鎖定。")
        if bad and IS_WIN:
            msg.append("這幾個資料夾無法在硬碟上設成隱藏 (可能權限不足)，但在播放器裡一樣是鎖住的:\n" + "\n".join(bad))
        if msg:
            QMessageBox.information(self, "資料夾鎖", "\n\n".join(msg))

    def _toggle_search(self):
        self.cfg["search_open"] = not self.cfg["search_open"]
        save_cfg(self.cfg)
        if not self.cfg["search_open"]:
            self.search.setText("")                 # 收起來的時候一併清掉關鍵字，清單恢復完整
        self._apply_search()
        if self.cfg["search_open"]:
            self.search.setFocus()

    def _apply_search(self):
        on = bool(self.cfg["search_open"])
        self.search_lbl.setVisible(on)
        self.search.setVisible(on)
        self.search_btn.setToolTip("收起搜尋列" if on else "展開搜尋列")
        self.search_btn.setProperty("rep", "1" if on else "0")
        self.search_btn.style().unpolish(self.search_btn)
        self.search_btn.style().polish(self.search_btn)

    def _toggle_vtools(self):
        self.cfg["video_tools_open"] = not self.cfg["video_tools_open"]
        save_cfg(self.cfg)
        self._apply_vtools()

    def _apply_vtools(self):
        on = bool(self.cfg["video_tools_open"])
        self.v_tools_bar.setText("▼" if on else "▲")
        self.v_tools_bar.setToolTip("收起下面的按鈕" if on else "展開下面的按鈕 (下載影片…)")
        self.v_tools_box.setVisible(on)

    def _autopause_changed(self, on):
        self.cfg["auto_pause"] = bool(on)
        save_cfg(self.cfg)

    def _app_state(self, st):
        """切到別的程式 / 縮小到工作列 / 縮進系統匣: 正在放的影片自動暫停 (回來後要自己按播放)"""
        if st == Qt.ApplicationActive:
            return
        if self.mode == "video" and self.cur and not self.paused and self.cfg["auto_pause"]:
            self.toggle()

    def stop_play(self):
        """⏹ 停止: 停掉現在播的，畫面與進度條歸零 (清單不動)"""
        self._stop_all()
        self.cur, self.paused, self.pos, self.length = None, False, 0.0, 0.0
        self.now_lbl.setText("(未播放)")
        self._mark_ui()

    def _toggle_fullscreen(self):
        if VIDEO_OK and self.mode == "video":
            self._set_fullscreen(not self.video_view.isFullScreen())

    def _set_fullscreen(self, on):
        if not VIDEO_OK:
            return
        vv = self.video_view
        if on and self.mode != "video":
            return
        if vv.isFullScreen() != on:
            vv.setFullScreen(on)
        if on:
            vv.setFocus()
            self._fs_show_bar()
            self._fs_timer.start()
        else:
            self._fs_timer.stop()
            self.fsbar.hide()
            vv.unsetCursor()
            if self.mode == "video":
                vv.setFocus()

    def _fs_show_bar(self):
        scr = self.video_view.screen() or QApplication.primaryScreen()
        g = scr.geometry()
        b = self.fsbar
        b.style_it(getattr(self, "th", {}).get("accent", "#5865f2"))
        b.setGeometry(g.x(), g.y() + g.height() - b.height(), g.width(), b.height())
        b.show()
        b.raise_()
        self._fs_sync()

    def _fs_motion(self):
        """全螢幕時滑鼠一動: 叫出控制列和滑鼠游標，一陣子沒動再收起來"""
        if not (VIDEO_OK and self.video_view.isFullScreen()):
            return
        self.video_view.unsetCursor()
        if not self.fsbar.isVisible():
            self._fs_show_bar()
        self._fs_timer.start()

    def _fs_idle(self):
        if not (VIDEO_OK and self.video_view.isFullScreen()):
            return
        if self.fsbar.underMouse() or self.fsbar.seek.dragging:
            self._fs_timer.start()                  # 滑鼠還在控制列上，不收
            return
        self.fsbar.hide()
        self.video_view.setCursor(Qt.BlankCursor)

    def _fs_seek_moved(self, v):
        if self.fsbar.seek.dragging and self.length:
            self.fsbar.cur_lbl.setText(fmt(v / 1000 * self.length))

    def _fs_sync(self):
        b = getattr(self, "fsbar", None)
        if b is None or not b.isVisible():
            return
        b.play_btn.setText("▶" if (self.paused or not self.cur) else "❚❚")
        b.title.setText(os.path.splitext(os.path.basename(self.cur[1]))[0] if self.cur else "(未播放)")
        if not b.seek.dragging:
            b.seek.blockSignals(True)
            if self.cur:
                b.cur_lbl.setText(fmt(self.pos))
                b.tot_lbl.setText(fmt(self.length))
                b.seek.setValue(int(min(1.0, self.pos / self.length) * 1000) if self.length else 0)
            else:
                b.cur_lbl.setText("0:00")
                b.tot_lbl.setText("0:00")
                b.seek.setValue(0)
            b.seek.blockSignals(False)

    def _video_key(self, e):
        """影片畫面的鍵盤 (跟 MPC-HC 一樣): 空白=播放/暫停  F/Enter=全螢幕  Esc=離開全螢幕
        ←/→=倒退/快轉10秒 (Shift 5秒)  ↑/↓=音量  M=靜音  PgUp/PgDn=上一部/下一部  L=清單"""
        k = e.key()
        small = bool(e.modifiers() & Qt.ShiftModifier)
        if k == Qt.Key_Escape:
            if self.video_view.isFullScreen():
                self._set_fullscreen(False)
                return True
            return False
        if k == Qt.Key_Space:
            self.toggle()
        elif k in (Qt.Key_F, Qt.Key_Return, Qt.Key_Enter):
            self._toggle_fullscreen()
        elif k == Qt.Key_Left:
            self._seek_rel(-5 if small else -10)
        elif k == Qt.Key_Right:
            self._seek_rel(5 if small else 10)
        elif k == Qt.Key_Up:
            self.vol.setValue(self.vol.value() + 5)
        elif k == Qt.Key_Down:
            self.vol.setValue(self.vol.value() - 5)
        elif k == Qt.Key_M:
            if self.vol.value() > 0:
                self._mute_prev = self.vol.value()
                self.vol.setValue(0)
            else:
                self.vol.setValue(getattr(self, "_mute_prev", 70) or 70)
        elif k == Qt.Key_PageUp:
            self.prev()
        elif k == Qt.Key_PageDown:
            self.next()
        elif k == Qt.Key_L and not self.video_view.isFullScreen():
            self.list_btn.toggle()
        else:
            return False
        return True

    def _video_wheel(self, dy):
        self.vol.setValue(self.vol.value() + (5 if dy > 0 else -5))
        return True

    # ---- 直接把影片拖進播放器 ----
    def _v_play_path(self, path):
        """播這一部影片: 先在清單裡找 (大小寫不分)；找不到 (被濾掉、副檔名不在清單支援內…) 就臨時加到清單最前面照播"""
        path = os.path.normpath(path)
        key = os.path.normcase(path)
        for i, (_, vp) in enumerate(self.view):
            if os.path.normcase(vp) == key:
                self.play_index(i)
                return True
        if not os.path.isfile(path):
            return False
        self.view.insert(0, (self._v_fid(path), path))
        self.play_index(0)
        return True

    def _v_drop_play(self, paths):
        """影片檔 / 資料夾拖進來: 加進清單，並且馬上開始播第一部"""
        try:
            if self.mode != "video":
                self.set_mode("video")
                if self.mode != "video":
                    return
            paths = [os.path.normpath(p) for p in paths if p]
            if self.search.text():
                self.search.setText("")             # 清掉搜尋，免得新加的影片被濾掉
            self._v_add_paths(paths)
            first = next((p for p in paths if os.path.isfile(p) and p.lower().endswith(VIDEO_EXTS)), None)
            if first is None:
                for p in paths:
                    if os.path.isdir(p):
                        first = next((vp for _, vp in self.view if self._v_under(vp, p)), None)
                        if first:
                            break
            if first is None:
                first = next((p for p in paths if os.path.isfile(p)), None)     # 副檔名不認得: 照樣試著播，交給解碼器決定
            if first is None:
                self.now_lbl.setText("拖進來的東西裡找不到影片檔")
                return
            if not self._v_play_path(first):
                self.now_lbl.setText("無法播放: " + os.path.basename(first))
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.now_lbl.setText(f"拖進來播放失敗: {e}")

    def _hook_video_children(self):
        """QVideoWidget 裡面還有自己的畫面元件蓋在上面，拖放有時會被它吃掉。
        把畫面和它底下所有元件都登記成可拖放，並用事件過濾器接手，確保把影片拖到黑框框上一定會播"""
        if not VIDEO_OK:
            return
        vv = self.video_view
        for ch in [vv] + vv.findChildren(QWidget):
            if not ch.property("_drophook"):
                ch.setProperty("_drophook", True)
                ch.setAcceptDrops(True)
                ch.installEventFilter(self)

    _VK_LETTERS = (Qt.Key_F, Qt.Key_M, Qt.Key_L, Qt.Key_J, Qt.Key_K, Qt.Key_T)

    def _global_video_key(self, e):
        """影片模式的快捷鍵。以前要「影片畫面有焦點」才有用，一點到清單或按鈕就按 F 沒反應 (清單還會當成找檔名)。
        現在整個視窗都有效；打字的輸入框完全不攔；清單 / 拉桿有焦點時只攔字母和數字鍵，方向鍵留給它們自己用。"""
        try:
            if QApplication.activeWindow() not in (self, self.video_view):
                return False
            if e.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
                return False
            fw = QApplication.focusWidget()
            if fw is not None:
                if any(fw.inherits(n) for n in ("QLineEdit", "QTextEdit", "QPlainTextEdit", "QAbstractSpinBox", "QComboBox")):
                    return False
                if fw.inherits("QAbstractItemView") or fw.inherits("QAbstractSlider"):
                    k = int(e.key())
                    if not (k in [int(x) for x in self._VK_LETTERS] or int(Qt.Key_0) <= k <= int(Qt.Key_9)):
                        return False
            return bool(self._video_key(e))
        except Exception as ex:
            LOG.warning("影片快捷鍵處理失敗: %s", ex)
            return False

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.KeyPress and VIDEO_OK and self.mode == "video" and self._global_video_key(ev):
            return True
        if t in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop) and obj.property("_drophook"):
            md = ev.mimeData()
            if md is not None and md.hasUrls():
                ev.acceptProposedAction()
                if t == QEvent.Drop:
                    paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
                    if paths:
                        QTimer.singleShot(0, lambda p=paths: self._v_drop_play(p))      # 先讓檔案總管放手，再處理
                return True
        return super().eventFilter(obj, ev)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if not paths:
            e.ignore()
            return
        e.acceptProposedAction()
        QTimer.singleShot(0, lambda p=paths: self._handle_drop(p))

    def _handle_drop(self, paths):
        if self.mode == "video" or any(p.lower().endswith(VIDEO_EXTS) for p in paths):
            self._v_drop_play(paths)
        else:
            self.open_external(paths)

    # ---------------- 影片「記住位置」: 只有按了 🔖 的影片才會記，下次播就接著看 ----------------
    def _mark_ui(self):
        marks = self.cfg["video_marks"]
        on = bool(self.cur) and self.mode == "video" and self.cur[0] in marks
        self.mark_btn.blockSignals(True)
        self.mark_btn.setChecked(on)
        self.mark_btn.blockSignals(False)
        if on:
            m = marks[self.cur[0]]
            self.mark_btn.setToolTip(f"這部影片已記住位置 ({fmt(m.get('pos', 0))})。暫停、停止、關閉程式、切到音樂、換影片時會自動更新，"
                                     f"下次播放自動接著看。再按一次 = 取消記住。\n超過 {MARK_KEEP_DAYS} 天沒看會自動清掉")
        else:
            self.mark_btn.setToolTip("按一下: 記住這部影片現在看到的位置。之後暫停、關閉、切到音樂都會自動更新，下次播放自動接著看。\n"
                                     "沒按的影片不會記。")

    def _mark_click(self):
        marks = self.cfg["video_marks"]
        if not (self.cur and self.mode == "video"):
            self.mark_btn.setChecked(False)
            self.now_lbl.setText("先播一部影片，再按「記住位置」")
            return
        fid = self.cur[0]
        if fid in marks:
            del marks[fid]
            save_cfg(self.cfg)
            LOG.info("取消記住位置: %s", os.path.basename(self.cur[1]))
        else:
            marks[fid] = {"pos": 0, "ts": time.time(), "name": os.path.basename(self.cur[1])}
            self._save_mark("按下標記")
            LOG.info("記住位置: %s", os.path.basename(self.cur[1]))
        self._mark_ui()

    def _save_mark(self, why=""):
        """把目前影片的位置存起來 (只對有按 🔖 的影片)。看到快結尾就當作看完，下次從頭"""
        try:
            if self.mode != "video" or not self.cur or not self.vplayer:
                return
            m = self.cfg["video_marks"].get(self.cur[0])
            if m is None:
                return
            pos = self.vplayer.position() / 1000
            dur = self.vplayer.duration() / 1000
            if dur and pos >= dur - 10:                   # 快看完了 = 看完: 下次從頭
                m["pos"], m["ts"] = 0, time.time()
            elif pos > 0:
                m["pos"], m["ts"] = int(pos), time.time()
            else:
                if why:
                    LOG.info("記住位置(%s): 位置讀到 0，不更新 (%s)", why, m.get("name", ""))
                return                                    # 位置讀到 0 (剛換片 / 剛停止): 不要蓋掉之前記的
            save_cfg(self.cfg)
            if why:
                LOG.info("記住位置(%s): %s → %s", why, m.get("name", ""), fmt(m["pos"]))
        except Exception as e:
            LOG.warning("記住位置失敗: %s", e)

    def _apply_resume(self):
        """影片載入後，跳到上次記住的位置。載入前播放器可能還不能跳 (isSeekable 為假)，
        所以不是跳一次就算數: _tick 會一直確認，沒跳到就再跳，最多等 15 秒"""
        sec = getattr(self, "_v_resume", None)
        if not sec:
            return
        try:
            if not self.vplayer.isSeekable():
                return
            self._v_resume_n = getattr(self, "_v_resume_n", 0) + 1
            self.vplayer.setPosition(int(sec * 1000))
            LOG.info("接續位置: 第 %d 次跳到 %s", self._v_resume_n, fmt(sec))
            if self._v_resume_n == 1:
                self.now_lbl.setText(self.now_lbl.text() + f"　(已接續上次位置 {fmt(sec)})")
        except Exception as e:
            LOG.warning("接續位置失敗: %s", e)

    def _resume_check(self):
        """_tick 呼叫: 確認真的跳到了就收工；還沒跳到就再試"""
        sec = getattr(self, "_v_resume", None)
        if not sec:
            return
        if abs(self.pos - sec) < 5:
            self._v_resume = None
            LOG.info("接續位置: 已確認在 %s", fmt(self.pos))
        elif getattr(self, "_v_resume_n", 0) >= 10 or time.time() - getattr(self, "_v_resume_t0", 0) > 15:
            LOG.warning("接續位置: 放棄 (試了 %d 次，目前在 %s，目標 %s)", getattr(self, "_v_resume_n", 0), fmt(self.pos), fmt(sec))
            self._v_resume = None
        else:
            self._apply_resume()

    def _stop_all(self):
        self._save_mark("停止/切換")
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
        if self.vplayer:
            self._set_fullscreen(False)
            self.vplayer.stop()
            self.vplayer.setSource(QUrl())

    def _set_play_vol(self, f):
        """音量 (0~1): 音樂與影片兩邊一起設，報時壓低音量也一樣"""
        try:
            pygame.mixer.music.set_volume(f)
        except Exception:
            pass
        if self.vaudio:
            self.vaudio.setVolume(f)

    def _v_duration(self, ms):
        if self.mode == "video" and ms > 0:
            self.length = ms / 1000
            self.tot_lbl.setText(fmt(self.length))
            self._apply_resume()

    def _v_status(self, st):
        if st in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            self._apply_resume()
        if self.mode == "video" and self.cur and st == QMediaPlayer.MediaStatus.EndOfMedia:
            QTimer.singleShot(0, self._auto_next)

    def _v_error(self, err, msg):
        if self.mode == "video" and self.cur:
            self.now_lbl.setText(f"無法播放: {os.path.basename(self.cur[1])} ({msg or err})")

    def _update_vol_icon(self, v):
        self.vol_btn.setText("🔇" if v == 0 else "🔈" if v < 34 else "🔉" if v < 67 else "🔊")

    def _set_vol(self, v):
        self._set_play_vol(v / 100)
        self.vol_lbl.setText(f"{v}%")
        self._update_vol_icon(v)
        self.cfg["volume"] = v
        fb = getattr(self, "fsbar", None)
        if fb is not None:
            fb.vol.blockSignals(True)
            fb.vol.setValue(v)
            fb.vol.blockSignals(False)
            fb.vol_lbl.setText("🔇" if v == 0 else "🔈" if v < 34 else "🔉" if v < 67 else "🔊")

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
        self._seek_to(v / 1000 * self.length)

    def _seek_rel(self, sec):
        """快轉 / 倒退 sec 秒 (影片模式的左右鍵)"""
        if self.cur and self.length:
            self._seek_to(self.pos + sec)

    def _seek_to(self, target):
        if not self.cur or not self.length:
            return
        target = max(0.0, min(self.length - 0.5, target))
        try:
            if self.mode == "video":
                self.vplayer.setPosition(int(target * 1000))
            else:
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
            if not self.counted and self.mode != "video" and self.play_start >= COUNT_AFTER_S:
                self.counted = True
                self.stats.add_play(self.cur[0])
            if self.mode == "video":
                self.pos = self.vplayer.position() / 1000      # 影片的進度直接問播放器 (播完由 _v_status 通知)
                if getattr(self, "_v_resume", None):
                    self._resume_check()
                elif now - getattr(self, "_mark_t", 0) > 15:
                    self._mark_t = now
                    self._save_mark()                   # (接續位置還沒完成時不存，免得把舊位置蓋成 0)
            elif not pygame.mixer.music.get_busy():
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
        self._fs_sync()

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

    # ---------------- 從外面開啟歌曲 / 只開一個播放器 ----------------
    def open_external(self, paths):
        """在檔案總管對一首歌按兩下(或「開啟檔案」)時: 直接播那一首；不在目前清單裡也照播。"""
        files = [os.path.normpath(p) for p in paths if p and p.lower().endswith(EXTS + VIDEO_EXTS) and os.path.isfile(p)]
        if not files:
            return False
        p = files[0]
        want = "video" if p.lower().endswith(VIDEO_EXTS) else "music"
        if want != self.mode:
            self.set_mode(want)
            if self.mode != want:
                return False
        if self.mode == "video":
            self._v_add_paths([p], expand=False)
            return self._v_play_path(p)
        fid = self.stats.fid(p)
        self.stats.touch(fid, p)
        self.stats.db.commit()
        if p in self.cfg["hidden_paths"]:
            self.cfg["hidden_paths"] = [x for x in self.cfg["hidden_paths"] if x != p]      # 你親自點的歌，就不再隱藏
        if not any(t[1] == p for t in self.all_tracks):
            self.all_tracks.append((fid, p))                  # 不在勾選的資料夾裡: 這次執行期間臨時加進清單
        self.search.setText("")
        self._filter_tracks()
        for i, (_, vp) in enumerate(self.view):
            if vp == p:
                self.play_index(i)
                return True
        return False

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
        self._save_mark("關閉程式")
        save_cfg(self.cfg)
        try:
            if self.tray:
                self.tray.hide()
        except Exception:
            pass
        try:
            self.fsbar.close()
        except Exception:
            pass
        try:
            if self.vplayer:
                self.vplayer.stop()
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
    SRV = "TruckPlayer-single-instance"
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    restarting = os.environ.pop("TRUCK_RESTART", "") == "1"
    deadline = time.time() + (10 if restarting else 0)
    while True:
        sock = QLocalSocket()
        sock.connectToServer(SRV)
        if sock.waitForConnected(400):
            if restarting and time.time() < deadline:      # 剛更新完重開: 舊的還沒關掉，等它離開
                sock.abort()
                time.sleep(0.4)
                continue
            sock.write(("\n".join(args) or "\x00SHOW").encode("utf-8"))       # 已經有一個在跑: 交給它，自己離開
            sock.flush()
            sock.waitForBytesWritten(1500)
            sock.disconnectFromServer()
            sys.exit(0)
        break
    w = Player()
    QLocalServer.removeServer(SRV)
    server = QLocalServer()
    server.listen(SRV)

    def _on_msg():
        c = server.nextPendingConnection()
        if c is None:
            return
        def _read():
            try:
                data = bytes(c.readAll()).decode("utf-8", "ignore")
            except Exception:
                data = ""
            lst = [x for x in data.split("\n") if x and not x.startswith("\x00")]
            if lst:
                w.open_external(lst)
            w._tray_show()
            c.disconnectFromServer()
        c.readyRead.connect(_read)
        if c.bytesAvailable():
            _read()
    server.newConnection.connect(_on_msg)
    w.show()
    if args:
        QTimer.singleShot(300, lambda: w.open_external(args))
    sys.exit(app.exec())
