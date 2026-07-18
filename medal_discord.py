"""
Medal -> Discord  |  GPU h264_nvenc (repli CPU auto)  |  720p  |  20s  |  10 MB max qualite optimale
Mode 100% fantome : aucune fenetre, aucune console, aucune icone tray.
Toute la configuration est faite une fois pour toutes par l'installeur.
Dependances : pip install requests watchdog
"""

import time
import requests
import subprocess
import os
import re
import glob
import atexit
import sys
import ctypes
from datetime import datetime
from queue import Queue
from threading import Thread, Lock
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ── Fichier de log (seule trace laissee par l'application) ───────────────────────
LOG_FILE      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "medal_discord.log")
LOG_MAX_LINES = 500
_log_lock     = Lock()

def log_write(level: str, msg: str):
    """Ecrit une ligne dans le fichier log avec rotation automatique. Jamais de sortie console."""
    try:
        with _log_lock:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            line = f"[{timestamp}] [{level}] {msg}\n"
            lines = []
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            lines.append(line)
            if len(lines) > LOG_MAX_LINES:
                lines = lines[-LOG_MAX_LINES:]
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines)
    except Exception:
        pass

def ln_ok(msg):   log_write("OK  ", msg)
def ln_warn(msg): log_write("WARN", msg)
def ln_err(msg):  log_write("ERR ", msg)

# ── CONFIGURATION ─────────────────────────────────────────────────────────────────
# Ces valeurs sont vides par defaut : l'installeur les remplit automatiquement
# lors de l'installation (pseudo / dossier clips / webhook(s)).
WEBHOOK_URL  = ""
WEBHOOK_URL2 = ""
WEBHOOK_URL3 = ""
FOLDER       = r""
FFMPEG_PATH  = r""
FFPROBE_PATH = FFMPEG_PATH.replace("ffmpeg.exe", "ffprobe.exe") if FFMPEG_PATH else ""
PSEUDO       = ""

# ── Detection automatique FFmpeg si le chemin configure est introuvable ──────────
def _resolve_ffmpeg():
    global FFMPEG_PATH, FFPROBE_PATH
    if FFMPEG_PATH and os.path.isfile(FFMPEG_PATH):
        return

    fallback = os.path.join(
        os.path.expanduser("~"), "Desktop",
        "Medal_to_Discord", "ffmpeg", "bin", "ffmpeg.exe"
    )
    if os.path.isfile(fallback):
        FFMPEG_PATH  = fallback
        FFPROBE_PATH = fallback.replace("ffmpeg.exe", "ffprobe.exe")
        return

    import shutil
    found = shutil.which("ffmpeg")
    if found:
        FFMPEG_PATH  = found
        FFPROBE_PATH = found.replace("ffmpeg.exe", "ffprobe.exe")

def get_active_webhooks() -> list:
    """Retourne la liste des webhooks non-vides dans l'ordre."""
    return [w for w in [WEBHOOK_URL, WEBHOOK_URL2, WEBHOOK_URL3] if w.strip()]

# ── Encodage : GPU NVIDIA si dispo, sinon repli CPU automatique ──────────────────
ENCODER = None  # resolu par _resolve_encoder() : "h264_nvenc" ou "libx264"

def _resolve_encoder():
    """Detecte les encodeurs disponibles dans ce build de FFmpeg et choisit le
    meilleur : GPU NVIDIA (rapide) si present, sinon CPU (libx264) en repli
    automatique pour que le script fonctionne sur n'importe quelle machine."""
    global ENCODER
    try:
        r = subprocess.run([FFMPEG_PATH, "-encoders"], capture_output=True, text=True,
                            creationflags=NO_WINDOW)
        available = r.stdout
    except Exception as e:
        log_write("ERR ", f"Impossible d'interroger FFmpeg pour les encodeurs : {e}")
        ENCODER = None
        return

    if "h264_nvenc" in available:
        ENCODER = "h264_nvenc"
        ln_ok("Encodage GPU NVIDIA (h264_nvenc) disponible.")
    elif "libx264" in available:
        ENCODER = "libx264"
        ln_warn("h264_nvenc absent (pas de carte NVIDIA compatible) — repli sur l'encodage CPU (libx264).")
    else:
        ENCODER = None
        ln_err("Aucun encodeur H.264 disponible (ni h264_nvenc, ni libx264) dans ce build de FFmpeg.")

LIMIT_MB       = 10
MARGE_SECURITE = 0.95
AUDIO_KBPS     = 128
CLIP_DUREE     = 20
RETRY_UPLOAD   = 3

# ── Auto-update depuis GitHub ─────────────────────────────────────────────────────
VERSION     = "5.3"
PATCH_NOTES = [
    "v5.3 : Message Discord enrichi avec emojis (jeu, pseudo, qualite, taille, duree, bitrate, heure)",
    "v5.2 : Repli CPU automatique par clip si l'encodage GPU echoue en cours de route (ex. driver NVIDIA trop ancien pour ffmpeg) — plus jamais de clip bloque",
    "v5.2 : Correction des options NVENC invalides (spatial-aq/temporal-aq) qui faisaient echouer tous les encodages GPU",
    "v5.1 : Repli CPU (libx264) automatique si aucune carte NVIDIA compatible n'est detectee",
    "v5.0 : Mode fantome permanent — plus de tray, plus de console, plus de raccourcis clavier",
    "v5.0 : Toute la config se fait a l'installation (pseudo/dossier/webhooks) — plus de menu runtime",
    "v5.0 : Auto-update securise — verification de syntaxe avant redemarrage, plus de crash silencieux",
    "v5.0 : Desinstalleur nettoye — ne reference plus le .vbs obsolete",
]
GITHUB_RAW = "https://raw.githubusercontent.com/PabloooG/Medal_to_discord/main/medal_discord.py"

def notify_update_discord(old_version: str, new_version: str, patch_notes: list):
    try:
        lines = [f"[MAJ] v{old_version} -> v{new_version}", f"Utilisateur : {PSEUDO}", ""]
        lines.append("Patch-note :")
        for note in patch_notes:
            lines.append(f"  - {note}")
        msg = "\n".join(lines)
        for wh in get_active_webhooks():
            try:
                requests.post(wh, json={"content": msg}, timeout=10)
            except Exception:
                pass
    except Exception:
        pass

def notify_error_discord(old_version: str, new_version: str, error_msg: str):
    try:
        lines = [
            f"[Echec MAJ] v{old_version} -> v{new_version}",
            f"Utilisateur : {PSEUDO}",
            "",
            f"Erreur : {error_msg}",
        ]
        msg = "\n".join(lines)
        for wh in get_active_webhooks():
            try:
                requests.post(wh, json={"content": msg}, timeout=10)
            except Exception:
                pass
    except Exception:
        pass

def _is_valid_python(source: str) -> bool:
    """Verifie que le code source compile avant de l'ecrire sur disque.
    Evite qu'une reponse GitHub partielle/corrompue ne casse l'installation."""
    try:
        compile(source, "<update-check>", "exec")
        return True
    except SyntaxError:
        return False

def check_update():
    """Verifie si une nouvelle version est disponible sur GitHub et met a jour si besoin."""
    log_write("INFO", "Verification des mises a jour...")
    try:
        r = requests.get(GITHUB_RAW, timeout=10)
        if r.status_code != 200:
            ln_warn(f"Impossible de verifier la version (HTTP {r.status_code})")
            return
        latest = "?"
        for line in r.text.splitlines():
            if line.strip().startswith("VERSION"):
                latest = line.split("=")[1].strip().strip('"')
                break
        import ast
        patch_notes_github = []
        for line in r.text.splitlines():
            if line.strip().startswith("PATCH_NOTES"):
                try:
                    patch_notes_github = ast.literal_eval(line.split("=", 1)[1].strip())
                except Exception:
                    pass
                break
        def _v_tuple(v: str):
            try:
                return tuple(int(x) for x in v.split("."))
            except Exception:
                return None

        cur_t, latest_t = _v_tuple(VERSION), _v_tuple(latest)
        if latest == "?" or latest_t is None or cur_t is None or latest_t <= cur_t:
            ln_ok(f"Version {VERSION} — a jour.")
            return

        ln_warn(f"Nouvelle version {latest} disponible (actuelle : {VERSION})")
        log_write("INFO", f"Mise a jour {VERSION} -> {latest} en cours...")

        script_path = os.path.abspath(__file__)
        backup_path = script_path + ".bak"
        try:
            import shutil
            shutil.copy2(script_path, backup_path)
        except Exception:
            pass

        try:
            # ── Preservation des variables du client ──────────────────────
            _webhook  = WEBHOOK_URL
            _webhook2 = globals().get("WEBHOOK_URL2", "")
            _webhook3 = globals().get("WEBHOOK_URL3", "")
            _folder   = FOLDER
            _pseudo   = PSEUDO
            _ffmpeg   = FFMPEG_PATH
            new_script = r.text
            _q = chr(34)
            _lines_out = []
            for _ln in new_script.splitlines(keepends=True):
                _s = _ln.lstrip()
                if _s.startswith("WEBHOOK" + "_URL2") and "=" in _s and not _ln.startswith(" "):
                    _lines_out.append("WEBHOOK_URL2 = " + _q + _webhook2 + _q + chr(10))
                elif _s.startswith("WEBHOOK" + "_URL3") and "=" in _s and not _ln.startswith(" "):
                    _lines_out.append("WEBHOOK_URL3 = " + _q + _webhook3 + _q + chr(10))
                elif _s.startswith("WEBHOOK" + "_URL") and "=" in _s and not _ln.startswith(" "):
                    _lines_out.append("WEBHOOK_URL  = " + _q + _webhook + _q + chr(10))
                elif _s.startswith("FOL" + "DER") and "=" in _s and "r" + _q in _s:
                    _indent = _ln[: len(_ln) - len(_ln.lstrip())]
                    _lines_out.append(_indent + "FOLDER       = r" + _q + _folder + _q + chr(10))
                elif _s.startswith("PS" + "EUDO") and "=" in _s:
                    _indent = _ln[: len(_ln) - len(_ln.lstrip())]
                    _lines_out.append(_indent + "PSEUDO       = " + _q + _pseudo + _q + chr(10))
                elif _s.startswith("FFMPEG" + "_PATH") and "=" in _s and "r" + _q in _s:
                    _indent = _ln[: len(_ln) - len(_ln.lstrip())]
                    _lines_out.append(_indent + "FFMPEG_PATH  = r" + _q + _ffmpeg + _q + chr(10))
                else:
                    _lines_out.append(_ln)
            final_script = "".join(_lines_out)

            if not _is_valid_python(final_script):
                raise ValueError("script telecharge invalide (echec de compilation) — mise a jour annulee")

            with open(script_path, "w", encoding="utf-8") as f:
                f.write(final_script)
            ln_ok(f"Mise a jour v{latest} telechargee et validee.")
            flag_path = script_path + ".updated"
            with open(flag_path, "w", encoding="utf-8") as _f:
                _f.write(f"{VERSION}→{latest}")
            log_write("INFO", f"Redemarrage vers v{latest}...")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            err = str(e)
            ln_err(f"Echec mise a jour : {err}")
            notify_error_discord(VERSION, latest, err)
            try:
                import shutil
                shutil.copy2(backup_path, script_path)
                ln_warn("Ancien script restaure depuis le backup.")
            except Exception:
                pass

    except requests.exceptions.ConnectionError:
        ln_warn("Pas de connexion internet — verification update ignoree.")
    except Exception as e:
        err = str(e)
        ln_warn(f"Verification update echouee : {err}")
        notify_error_discord(VERSION, "?", err)

# ─────────────────────────────────────────────────────────────────────────────────

stats      = {"traites": 0, "reussis": 0, "echoues": 0, "queue": 0}
stats_lock = Lock()

TEMP_DIR  = os.environ.get("TEMP", os.path.expanduser("~"))
NO_WINDOW = subprocess.CREATE_NO_WINDOW

# ── File d'attente ────────────────────────────────────────────────────────────────
clip_queue = Queue()

def worker():
    while True:
        path = clip_queue.get()
        with stats_lock:
            stats["queue"] = clip_queue.qsize()
        try:
            process_clip(path)
        except Exception as e:
            ln_err(f"Erreur inattendue : {e}")
            with stats_lock:
                stats["echoues"] += 1
        finally:
            clip_queue.task_done()
            with stats_lock:
                stats["queue"] = clip_queue.qsize()

Thread(target=worker, daemon=True).start()

# ── Cleanup ───────────────────────────────────────────────────────────────────────
def cleanup_leftover_tmps():
    cnt = 0
    for f in glob.glob(os.path.join(FOLDER, "**/*_discord*.mp4"), recursive=True):
        try: os.remove(f); cnt += 1
        except Exception: pass
    for f in glob.glob(os.path.join(TEMP_DIR, "medal2pass-*.log*")):
        try: os.remove(f)
        except Exception: pass
    if cnt:
        ln_ok(f"Nettoyage : {cnt} fichier(s) temporaire(s) supprime(s)")

atexit.register(cleanup_leftover_tmps)

# ── Utilitaires ───────────────────────────────────────────────────────────────────
def wait_for_file(path: str, timeout: int = 60, stable_secs: int = 3) -> bool:
    deadline     = time.time() + timeout
    last_size    = -1
    stable_since = None
    while time.time() < deadline:
        try:
            size = os.path.getsize(path)
        except OSError:
            time.sleep(1)
            continue
        if size != last_size:
            last_size    = size
            stable_since = time.time()
        elif time.time() - stable_since >= stable_secs:
            return True
        time.sleep(1)
    return False

def get_duration(path: str) -> float:
    r = subprocess.run([
        FFPROBE_PATH, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path
    ], capture_output=True, text=True, creationflags=NO_WINDOW)
    try:    return float(r.stdout.strip())
    except: return 0.0

GAME_ALIASES = {
    "apexlegends":     "Apex Legends",
    "valorant":        "Valorant",
    "warzone":         "Warzone",
    "fortnite":        "Fortnite",
    "minecraft":       "Minecraft",
    "leagueoflegends": "League of Legends",
    "csgo":            "CS:GO",
    "cs2":             "CS2",
    "overwatch":       "Overwatch",
    "rocketleague":    "Rocket League",
}

def detect_game(filename: str) -> str:
    name = os.path.basename(filename).replace("MedalTV", "").replace(".mp4", "")
    key  = re.sub(r'[\d_\-]+$', '', name).strip().lower().replace(" ", "")
    return GAME_ALIASES.get(key, re.sub(r'\d+$', '', name).strip() or "Clip")

def calcul_bitrate_video(duration_s: float, limit_mb: float = LIMIT_MB,
                          marge: float = MARGE_SECURITE) -> int:
    budget_bits = limit_mb * 1024 * 1024 * 8 * marge
    audio_bits  = AUDIO_KBPS * 1000 * duration_s
    video_bits  = max(budget_bits - audio_bits, 500_000 * duration_s)
    return int(video_bits / duration_s)

# ── Encodage 2-pass (GPU NVIDIA si dispo, sinon CPU en repli automatique) ────────
def encode_2pass(input_path: str, output_path: str,
                 trim_start: float, duration: float,
                 video_bps: int, encoder: str = None) -> int:
    if encoder is None:
        encoder = ENCODER
    passlog = os.path.join(TEMP_DIR, f"medal2pass-{os.getpid()}")

    if encoder == "h264_nvenc":
        base_args = [
            FFMPEG_PATH, "-y",
            "-hwaccel", "cuda",
            "-ss", f"{trim_start:.2f}",
            "-t",  f"{duration:.2f}",
            "-i", input_path,
            "-vcodec", "h264_nvenc",
            "-preset", "p7",
            "-rc", "vbr",
            "-b:v", f"{video_bps}",
            "-maxrate", f"{int(video_bps * 1.5)}",
            "-bufsize", f"{int(video_bps * 2)}",
            "-spatial-aq", "1",
            "-temporal-aq", "1",
            "-aq-strength", "8",
            "-rc-lookahead", "32",
            "-bf", "4",
            "-profile:v", "high",
            "-pix_fmt", "yuv420p",
            "-vf", "scale=1280:-2:flags=lanczos,unsharp=3:3:0.5:3:3:0.3",
        ]
    else:
        # Repli CPU (libx264) : pas de carte NVIDIA compatible detectee.
        # "veryfast" garde un temps d'encodage raisonnable en arriere-plan.
        base_args = [
            FFMPEG_PATH, "-y",
            "-ss", f"{trim_start:.2f}",
            "-t",  f"{duration:.2f}",
            "-i", input_path,
            "-vcodec", "libx264",
            "-preset", "veryfast",
            "-b:v", f"{video_bps}",
            "-maxrate", f"{int(video_bps * 1.5)}",
            "-bufsize", f"{int(video_bps * 2)}",
            "-bf", "4",
            "-profile:v", "high",
            "-pix_fmt", "yuv420p",
            "-vf", "scale=1280:-2:flags=lanczos,unsharp=3:3:0.5:3:3:0.3",
            "-passlogfile", passlog,
        ]

    if encoder == "h264_nvenc":
        # h264_nvenc ne supporte pas le mecanisme generique -pass 1/2 de ffmpeg
        # (il utilise son propre VBR interne) : un seul passage suffit et evite
        # l'erreur "terminating thread with return code -22 (Invalid argument)".
        passes = [
            base_args + ["-acodec", "aac", "-b:a", f"{AUDIO_KBPS}k",
                         "-movflags", "+faststart", output_path],
        ]
    else:
        passes = [
            base_args + ["-pass", "1", "-an", "-f", "null", "NUL"],
            base_args + ["-pass", "2",
                         "-acodec", "aac", "-b:a", f"{AUDIO_KBPS}k",
                         "-movflags", "+faststart", output_path],
        ]

    for cmd in passes:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                cwd=TEMP_DIR, creationflags=NO_WINDOW)
        if result.returncode != 0:
            log_write("ERR ", f"Encodage echoue : {result.stderr[-300:]}")
            for f in glob.glob(passlog + "*"):
                try: os.remove(f)
                except Exception: pass
            return -1

    for f in glob.glob(passlog + "*"):
        try: os.remove(f)
        except Exception: pass

    return os.path.getsize(output_path) if os.path.exists(output_path) else -1

def encode_2pass_auto(input_path: str, output_path: str,
                      trim_start: float, duration: float,
                      video_bps: int) -> int:
    """Enveloppe encode_2pass avec repli CPU automatique par clip.

    Si l'encodage GPU echoue (ex. driver NVIDIA trop ancien pour la version
    de ffmpeg utilisee), on bascule immediatement sur libx264 pour CE clip
    et on desactive le GPU pour les clips suivants afin d'eviter de refaire
    l'echec a chaque fois.
    """
    global ENCODER
    result = encode_2pass(input_path, output_path, trim_start, duration, video_bps)
    if result == -1 and ENCODER == "h264_nvenc":
        ln_warn("Encodage GPU echoue (driver NVIDIA incompatible ?) — repli CPU (libx264) pour ce clip.")
        ENCODER = "libx264"
        result = encode_2pass(input_path, output_path, trim_start, duration, video_bps,
                              encoder="libx264")
    return result

# ── Upload Discord ────────────────────────────────────────────────────────────────
def build_discord_message(game: str, size_mb: float,
                           duration: float, video_kbps: int) -> str:
    heure = datetime.now().strftime("%H:%M")
    return (
        f"🎮 **{game}**\n"
        f"👤 {PSEUDO}\n"
        f"📐 720p 2-pass  |  "
        f"⚖️ {size_mb:.1f} MB  |  "
        f"⏱️ {duration:.0f}s  |  "
        f"📡 {video_kbps} kbps  |  "
        f"⏰ {heure}"
    )

def upload_discord(result_path: str, message: str) -> object:
    webhooks = get_active_webhooks()
    last_resp = None

    for wh_url in webhooks:
        for attempt in range(1, RETRY_UPLOAD + 1):
            try:
                with open(result_path, "rb") as f:
                    resp = requests.post(
                        wh_url,
                        files={"file": (os.path.basename(result_path), f, "video/mp4")},
                        data={"content": message},
                        timeout=120,
                    )
            except Exception as e:
                resp = e

            if isinstance(resp, requests.Response) and resp.status_code in (200, 201):
                last_resp = resp
                break
            if isinstance(resp, requests.Response) and resp.status_code == 413:
                last_resp = resp
                break

            wait = 2 ** attempt
            ln_warn(f"Upload tentative {attempt} echouee ({resp}) — retry dans {wait}s...")
            time.sleep(wait)
        else:
            last_resp = resp

    return last_resp

# ── Traitement principal ──────────────────────────────────────────────────────────
def process_clip(file_path: str):
    name = os.path.basename(file_path)
    game = detect_game(file_path)

    log_write("INFO", f"Clip detecte : {name}")

    if not wait_for_file(file_path):
        ln_err("Fichier toujours verrouille apres timeout — abandon.")
        with stats_lock: stats["echoues"] += 1
        return

    total_dur = get_duration(file_path)
    keep       = min(CLIP_DUREE, total_dur)
    trim_start = max(0.0, total_dur - keep)

    base      = os.path.splitext(file_path)[0]
    converted = f"{base}_discord.mp4"

    video_bps = calcul_bitrate_video(keep)

    size = encode_2pass_auto(file_path, converted, trim_start, keep, video_bps)

    if size == -1:
        ln_err("Encodage echoue.")
        with stats_lock: stats["echoues"] += 1
        return

    limit_bytes = LIMIT_MB * 1024 * 1024

    if size > limit_bytes:
        ln_warn("Depassement — re-encodage avec marge plus stricte (88%)...")
        video_bps = calcul_bitrate_video(keep, marge=0.88)
        size      = encode_2pass_auto(file_path, converted, trim_start, keep, video_bps)
        if size == -1:
            ln_err("Re-encodage echoue.")
            with stats_lock: stats["echoues"] += 1
            return

    size_mb = size / 1024 / 1024
    message = build_discord_message(game, size_mb, keep, video_bps // 1000)
    t0      = time.time()
    resp    = upload_discord(converted, message)
    dt      = time.time() - t0

    if isinstance(resp, Exception) or resp is None or resp.status_code not in (200, 201):
        log_write("ERR ", f"Upload echoue : {resp}")
        with stats_lock: stats["echoues"] += 1
    else:
        log_write("OK  ", f"Clip envoye : {name}  {size_mb:.2f} MB  {keep:.0f}s  {dt:.1f}s")
        with stats_lock: stats["reussis"] += 1

    try: os.remove(converted)
    except Exception: pass

    with stats_lock: stats["traites"] += 1

# ── Watchdog ──────────────────────────────────────────────────────────────────────
class MedalHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory: return
        p = event.src_path.lower()
        if "_discord" in p: return
        if p.endswith((".mp4", ".mov", ".avi", ".mkv")):
            clip_queue.put(event.src_path)
            with stats_lock:
                stats["queue"] = clip_queue.qsize()
            ln_warn(f"Clip detecte : {os.path.basename(event.src_path)}")

# ── Desinstalleur ─────────────────────────────────────────────────────────────────
def create_uninstaller():
    """Genere desinstaller.bat dans le dossier d'installation au demarrage."""
    try:
        install_dir = os.path.dirname(os.path.abspath(__file__))
        uninst_path = os.path.join(install_dir, "desinstaller.bat")
        task_name = "MedalToDiscord"
        lines = [
            "@echo off\n",
            "chcp 65001 >nul\n",
            "color 0C\n",
            "title Medal to Discord - Desinstalleur\n",
            "echo.\n",
            "echo  +------------------------------------------------------+\n",
            "echo  ^|       Medal to Discord  -  Desinstalleur            ^|\n",
            "echo  +------------------------------------------------------+\n",
            "echo.\n",
            "echo  Cette action va supprimer :\n",
            "echo.\n",
            "echo    - Le dossier d'installation\n",
            "echo    - La tache planifiee de demarrage automatique\n",
            "echo.\n",
            "echo  Tes clips Medal ne seront PAS supprimes.\n",
            "echo.\n",
            'set /p "CONFIRM=  Confirmer la desinstallation ? (O/N) > "\n',
            'if /i not "%CONFIRM%"=="O" (\n',
            "    echo.\n",
            "    echo  Desinstallation annulee.\n",
            "    timeout /t 2 >nul\n",
            "    exit /b\n",
            ")\n",
            "echo.\n",
            "echo  [1/2] Suppression du demarrage automatique...\n",
            f'schtasks /query /tn "{task_name}" >nul 2>&1\n',
            "if %errorlevel%==0 (\n",
            f'    schtasks /delete /tn "{task_name}" /f >nul 2>&1\n',
            "    echo        OK : tache planifiee supprimee.\n",
            ") else (\n",
            "    echo        Deja absent.\n",
            ")\n",
            "echo.\n",
            "echo  [2/2] Suppression du dossier d'installation...\n",
            'cd /d "%USERPROFILE%\\Desktop"\n',
            f'if exist "{install_dir}" (\n',
            f'    rmdir /s /q "{install_dir}"\n',
            "    echo        OK : dossier supprime.\n",
            ") else (\n",
            "    echo        Deja absent.\n",
            ")\n",
            "echo.\n",
            "echo  +------------------------------------------------------+\n",
            "echo  ^|   Desinstallation terminee.                         ^|\n",
            "echo  +------------------------------------------------------+\n",
            "echo.\n",
            "pause\n",
        ]
        with open(uninst_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        log_write("INFO", f"desinstaller.bat cree : {uninst_path}")
    except Exception as e:
        log_write("WARN", f"Impossible de creer desinstaller.bat : {e}")

# ── Mode fantome (aucune fenetre, jamais) ─────────────────────────────────────────
def _ghost_hide():
    """Cache totalement la fenetre console si une console existe (double-clic manuel
    sur python.exe par ex.). Sous pythonw.exe il n'y a de toute facon pas de console."""
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            GWL_EXSTYLE      = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_APPWINDOW  = 0x00040000
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            ctypes.windll.user32.ShowWindow(hwnd, 0)
        log_write("INFO", "Mode fantome actif (aucune fenetre, aucun tray).")
    except Exception as e:
        log_write("WARN", f"Ghost hide echoue : {e}")

# ── Main ──────────────────────────────────────────────────────────────────────────
def main():
    _resolve_ffmpeg()
    create_uninstaller()
    _ghost_hide()
    check_update()

    flag_path = os.path.abspath(__file__) + ".updated"
    if os.path.exists(flag_path):
        try:
            with open(flag_path, encoding="utf-8") as _f:
                _old, _new = _f.read().strip().split("→")
            os.remove(flag_path)
            notify_update_discord(_old, _new, PATCH_NOTES)
            ln_ok(f"Notification mise a jour v{_old} -> v{_new} envoyee.")
        except Exception:
            pass

    errors = False

    if not FFMPEG_PATH or not os.path.isfile(FFMPEG_PATH):
        ln_err(f"FFmpeg introuvable : {FFMPEG_PATH}")
        errors = True
    else:
        _resolve_encoder()
        if ENCODER is None:
            ln_err("Aucun encodeur H.264 disponible dans ce build de FFmpeg.")
            errors = True

    if not FOLDER or not os.path.isdir(FOLDER):
        ln_err(f"Dossier Medal introuvable : {FOLDER}")
        errors = True
    else:
        ln_ok(f"Dossier surveille : {FOLDER}")

    if not get_active_webhooks():
        ln_err("Aucun webhook Discord configure.")
        errors = True

    if errors:
        # Le disque de clips (souvent un disque externe/reseau) peut ne pas
        # encore etre monte au demarrage de Windows : on retente quelques fois.
        for tentative in range(3):
            time.sleep(10)
            _resolve_ffmpeg()
            if FFMPEG_PATH and os.path.isfile(FFMPEG_PATH):
                _resolve_encoder()
            errors = (
                not FFMPEG_PATH or not os.path.isfile(FFMPEG_PATH)
                or ENCODER is None
                or not FOLDER or not os.path.isdir(FOLDER)
                or not get_active_webhooks()
            )
            if not errors:
                log_write("INFO", f"OK au bout de {tentative + 1} essai(s).")
                break
        if errors:
            log_write("ERR ", "Demarrage abandonne apres 3 essais.")
            try:
                missing = []
                if not FFMPEG_PATH or not os.path.isfile(FFMPEG_PATH):
                    missing.append(f"FFmpeg introuvable : {FFMPEG_PATH}")
                elif ENCODER is None:
                    missing.append("Aucun encodeur H.264 disponible dans ce build de FFmpeg")
                if not FOLDER or not os.path.isdir(FOLDER):
                    missing.append(f"Dossier Medal introuvable : {FOLDER}")
                if not get_active_webhooks():
                    missing.append("Aucun webhook Discord configure")
                msg_lines = [
                    f"[Script non demarre] v{VERSION}",
                    f"Utilisateur : {PSEUDO}",
                    "",
                    "Ressource(s) manquante(s) apres 3 tentatives (x10s) :",
                ]
                for m in missing:
                    msg_lines.append(f"  - {m}")
                msg = "\n".join(msg_lines)
                for wh in get_active_webhooks():
                    try:
                        requests.post(wh, json={"content": msg}, timeout=10)
                    except Exception:
                        pass
            except Exception:
                pass
            return

    cleanup_leftover_tmps()

    observer = Observer()
    observer.schedule(MedalHandler(), FOLDER, recursive=True)
    observer.start()

    log_write("INFO", "Surveillance active — fonctionnement 100% fantome.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log_write("INFO", "Arret demande.")
        observer.stop()

    observer.join()


if __name__ == "__main__":
    log_write("INFO", f"=== Script demarre v{VERSION} (mode fantome) ===")
    try:
        main()
    except Exception as e:
        import traceback
        err_detail = traceback.format_exc()
        log_write("ERR ", f"CRASH : {e}\n{err_detail}")
        try:
            lines = [
                f"[Crash du script] v{VERSION}",
                f"Utilisateur : {PSEUDO}",
                "",
                f"Erreur : {e}",
                "",
                "Voir medal_discord.log pour le detail.",
            ]
            msg = "\n".join(lines)
            for wh in get_active_webhooks():
                try:
                    requests.post(wh, json={"content": msg}, timeout=10)
                except Exception:
                    pass
        except Exception:
            pass
