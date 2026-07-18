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
VERSION     = "5.1"
PATCH_NOTES = [
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
        if latest == "?" or latest_t is None or cur_t is None or latest_t <=
