"""
Medal → Discord  |  GPU h264_nvenc  |  720p  |  20s  |  10 MB max qualité optimale
Dépendances : pip install rich watchdog requests
"""

import time
import requests
import subprocess
import os
import re
import glob
import atexit
import sys
from datetime import datetime
from queue import Queue
from threading import Thread, Lock
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ── Log file ─────────────────────────────────────────────────────────────────────
LOG_FILE      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "medal_discord.log")
LOG_MAX_LINES = 500

def log_write(level: str, msg: str):
    """Ecrit une ligne dans le fichier log avec rotation automatique."""
    try:
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

# ── Détection mode silencieux ────────────────────────────────────────────────────
# pythonw.exe n'a pas de console (sys.stdout est None)
SILENT = sys.stdout is None

if not SILENT:
    from rich.console import Console
    from rich.progress import Progress, BarColumn, TextColumn
    from rich.text import Text
    console = Console(highlight=False)

def ln_ok(msg):
    log_write("OK  ", msg)
    if not SILENT: console.print(Text.assemble(("✔ ", "bold green"), (msg, "green")))

def ln_warn(msg):
    log_write("WARN", msg)
    if not SILENT: console.print(Text.assemble(("⚠ ", "bold yellow"), (msg, "yellow")))

def ln_err(msg):
    log_write("ERR ", msg)
    if not SILENT: console.print(Text.assemble(("✖ ", "bold red"), (msg, "red")))

def separator():
    if not SILENT: console.rule(style="bright_black")

# ── CONFIGURATION ────────────────────────────────────────────────────────────────
WEBHOOK_URL  = "https://discord.com/api/webhooks/1499530486928904312/DvR9lA-bgAXE4omeyDYMP1VHreXcUjD50lhzlVvL5Xei2qmJiJkUDRqfQHV3FYAwD1e1"
FOLDER       = r"F:\Medal\Clips"
FFMPEG_PATH  = r"C:\\Users\\j-phi\\Desktop\\Medal_to_Discord\\ffmpeg\\bin\ffmpeg.exe"
FFPROBE_PATH = FFMPEG_PATH.replace("ffmpeg.exe", "ffprobe.exe")

# ── Détection automatique FFmpeg si chemin configuré introuvable ─────────────────
def _resolve_ffmpeg():
    global FFMPEG_PATH, FFPROBE_PATH
    if os.path.isfile(FFMPEG_PATH):
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

PSEUDO       = "Pablo_G"
NOTIF_TYPE   = "windows"   # "overlay" | "sound" | "windows"
LIMIT_MB       = 10
MARGE_SECURITE = 0.95
AUDIO_KBPS     = 128
CLIP_DUREE     = 20
RETRY_UPLOAD   = 3

# ── Auto-update depuis GitHub ─────────────────────────────────────────────────────
VERSION     = "3.2"
PATCH_NOTES = [
    "v3.0 : Banque de phrases droles apres chaque clip envoye (15 phrases en rotation aleatoire)",
    "v2.9 : Touche [H] pour cacher completement la fenetre (script toujours actif en arriere-plan)",
    "v2.9 : Notification locale au demarrage",
    "v2.8 : Correction definitive bad escape backslash lors des mises a jour et du menu config",
    "v2.6 : Notification locale apres chaque clip envoye (overlay, son systeme, toast Windows)",
    "v2.4 : Preservation dynamique webhook/dossier/pseudo/ffmpeg via variables",
]
GITHUB_RAW  = "https://raw.githubusercontent.com/PabloooG/Medal_to_discord/main/medal_discord.py"

def notify_update_discord(old_version: str, new_version: str, patch_notes: list):
    """Envoie un message de succes de mise a jour sur Discord."""
    try:
        lines = []
        lines.append(f"🔄 **Mise à jour**  v{old_version} → v{new_version}  ✅")
        lines.append(f"👤 {PSEUDO}")
        lines.append("")
        lines.append("📋 **Patch-note**")
        for note in patch_notes:
            lines.append(f"  ▸ {note}")
        lines.append("")
        lines.append("⏭️ Prochaine vérif au redémarrage...")
        msg = "\n".join(lines)
        requests.post(WEBHOOK_URL, json={"content": msg}, timeout=10)
    except Exception:
        pass

def notify_error_discord(old_version: str, new_version: str, error_msg: str):
    """Envoie un message d erreur sur Discord si la mise a jour echoue."""
    try:
        lines = []
        lines.append(f"❌ **Echec mise à jour**  v{old_version} → v{new_version}")
        lines.append(f"👤 {PSEUDO}")
        lines.append("")
        lines.append("⚠️ **Erreur**")
        lines.append(f"  ▸ {error_msg}")
        msg = "\n".join(lines)
        requests.post(WEBHOOK_URL, json={"content": msg}, timeout=10)
    except Exception:
        pass

def check_update():
    """Verifie si une nouvelle version est disponible sur GitHub et met a jour si besoin."""
    if not SILENT:
        console.print()
        console.print("  [dim]Verification des mises a jour...[/]")
    log_write("INFO", "Verification des mises a jour...")
    try:
        r = requests.get(GITHUB_RAW, timeout=10)
        if r.status_code != 200:
            ln_warn(f"Impossible de verifier la version (HTTP {r.status_code})")
            return
        latest = "?"
        patch_notes_github = []
        for line in r.text.splitlines():
            if line.strip().startswith("VERSION"):
                latest = line.split("=")[1].strip().strip('"')
                break
        import ast
        for line in r.text.splitlines():
            if line.strip().startswith("PATCH_NOTES"):
                try:
                    patch_notes_github = ast.literal_eval(line.split("=", 1)[1].strip())
                except Exception:
                    pass
                break
        if latest == VERSION:
            ln_ok(f"Version {VERSION} — a jour.")
            return

        ln_warn(f"Nouvelle version {latest} disponible ! (actuelle : {VERSION})")
        if not SILENT:
            console.print("  [dim cyan]Mise a jour en cours...[/]")
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
            import re as _re
            _webhook    = WEBHOOK_URL
            _folder     = FOLDER
            _pseudo     = PSEUDO
            _ffmpeg     = FFMPEG_PATH
            _notif      = NOTIF_TYPE
            new_script = r.text
            new_script = _re.sub(r'WEBHOOK_URL\s*=\s*"[^"]*"',  lambda m: f'WEBHOOK_URL  = "{_webhook}"',  new_script)
            new_script = _re.sub(r'FOLDER\s*=\s*r"[^"]*"',       lambda m: f'FOLDER       = r"{_folder}"',  new_script)
            new_script = _re.sub(r'PSEUDO\s*=\s*"[^"]*"',         lambda m: f'PSEUDO       = "{_pseudo}"',   new_script)
            new_script = _re.sub(r'FFMPEG_PATH\s*=\s*r"[^"]*"',   lambda m: f'FFMPEG_PATH  = r"{_ffmpeg}"',  new_script)
            new_script = _re.sub(r'NOTIF_TYPE\s*=\s*"[^"]*"',     lambda m: f'NOTIF_TYPE   = "{_notif}"',    new_script)
            # ─────────────────────────────────────────────────────────────
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(new_script)
            ln_ok(f"Mise a jour v{latest} telechargee !")
            flag_path = script_path + ".updated"
            with open(flag_path, "w", encoding="utf-8") as _f:
                _f.write(f"{VERSION}→{latest}")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            err = str(e)
            ln_err(f"Echec ecriture du script : {err}")
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

# ── Affichage ─────────────────────────────────────────────────────────────────────
def print_header():
    if SILENT: return
    console.print()
    t = Text()
    t.append("Medal", style="bold magenta")
    t.append(" → ", style="white")
    t.append("Discord", style="bold cyan")
    t.append("  |  GPU h264_nvenc  |  720p  |  ", style="dim white")
    t.append(f"{CLIP_DUREE}s", style="bold yellow")
    t.append(f" fixes  |  max ", style="dim white")
    t.append(f"{LIMIT_MB} MB", style="bold yellow")
    t.append(f"  qualité optimale 2-pass  |  v{VERSION}", style="dim white")
    console.print(t)
    console.rule(style="bright_black")

def print_stats():
    if SILENT: return
    separator()
    t = Text()
    t.append("clips traités ", style="dim")
    t.append(str(stats["traites"]), style="bold white")
    t.append("    réussis ", style="dim")
    t.append(str(stats["reussis"]), style="bold green")
    t.append("    échoués ", style="dim")
    t.append(str(stats["echoues"]), style="bold red" if stats["echoues"] else "bold white")
    t.append("    en attente ", style="dim")
    t.append(str(stats["queue"]), style="bold white")
    console.print(t)

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
            print_stats()

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
        ln_ok(f"Nettoyage : {cnt} fichier(s) temporaire(s) supprimé(s)")

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

# ── Encodage GPU 2-pass ───────────────────────────────────────────────────────────
def encode_2pass(input_path: str, output_path: str,
                 trim_start: float, duration: float,
                 video_bps: int) -> int:
    passlog   = os.path.join(TEMP_DIR, f"medal2pass-{os.getpid()}")
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
        "-spatial_aq", "1",
        "-temporal_aq", "1",
        "-aq-strength", "8",
        "-rc-lookahead", "32",
        "-bf", "4",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-vf", "scale=1280:-2:flags=lanczos,unsharp=3:3:0.5:3:3:0.3",
        "-passlogfile", passlog,
    ]
    passes = [
        base_args + ["-pass", "1", "-an", "-f", "null", "NUL"],
        base_args + ["-pass", "2",
                     "-acodec", "aac", "-b:a", f"{AUDIO_KBPS}k",
                     "-movflags", "+faststart", output_path],
    ]
    labels = ["passe 1/2 (analyse)", "passe 2/2 (encodage)"]

    if not SILENT:
        with Progress(
            TextColumn("    {task.description}", style="dim"),
            BarColumn(bar_width=44, style="yellow", complete_style="green"),
            TextColumn(" {task.fields[status]}"),
            console=console,
            transient=False,
        ) as prog:
            for cmd, label in zip(passes, labels):
                t = prog.add_task(label, total=100, status="[dim]en cours...[/]")
                t0     = time.time()
                result = subprocess.run(cmd, capture_output=True, text=True,
                                        cwd=TEMP_DIR, creationflags=NO_WINDOW)
                dt     = time.time() - t0
                if result.returncode != 0:
                    prog.update(t, completed=100,
                                status=f"[bold red]ERREUR[/] [dim]({dt:.1f}s)[/]")
                    console.print(f"    [dim red]{result.stderr[-400:]}[/]")
                    log_write("ERR ", f"Encodage echoue ({label}) : {result.stderr[-200:]}")
                    for f in glob.glob(passlog + "*"):
                        try: os.remove(f)
                        except Exception: pass
                    return -1
                prog.update(t, completed=100,
                            status=f"[bold green]OK[/] [dim]{dt:.1f}s[/]")
    else:
        for cmd in passes:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    cwd=TEMP_DIR, creationflags=NO_WINDOW)
            if result.returncode != 0:
                log_write("ERR ", f"Encodage echoue (silencieux) : {result.stderr[-200:]}")
                for f in glob.glob(passlog + "*"):
                    try: os.remove(f)
                    except Exception: pass
                return -1

    for f in glob.glob(passlog + "*"):
        try: os.remove(f)
        except Exception: pass

    return os.path.getsize(output_path) if os.path.exists(output_path) else -1

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
    if not SILENT:
        console.print()
        console.print("  [dim]envoi Discord...[/]")

    for attempt in range(1, RETRY_UPLOAD + 1):
        upload_done   = [False]
        result_holder = [None]

        def do_upload():
            try:
                with open(result_path, "rb") as f:
                    r = requests.post(
                        WEBHOOK_URL,
                        files={"file": (os.path.basename(result_path), f, "video/mp4")},
                        data={"content": message},
                        timeout=120,
                    )
                result_holder[0] = r
            except Exception as e:
                result_holder[0] = e
            finally:
                upload_done[0] = True

        Thread(target=do_upload, daemon=True).start()

        if not SILENT:
            with Progress(
                TextColumn(f"  [dim]upload (tentative {attempt}/{RETRY_UPLOAD})[/]"),
                BarColumn(bar_width=46, style="blue", complete_style="cyan"),
                TextColumn(" {task.percentage:>3.0f}%", style="bold cyan"),
                console=console,
                transient=False,
            ) as prog:
                task = prog.add_task("", total=100)
                pct  = 0
                while not upload_done[0]:
                    if pct < 90: pct = min(pct + 2, 90)
                    prog.update(task, completed=pct)
                    time.sleep(0.25)
                prog.update(task, completed=100)
        else:
            while not upload_done[0]:
                time.sleep(0.5)

        resp = result_holder[0]
        if isinstance(resp, requests.Response) and resp.status_code in (200, 201):
            return resp
        if isinstance(resp, requests.Response) and resp.status_code == 413:
            return resp

        wait = 2 ** attempt
        ln_warn(f"Tentative {attempt} échouée ({resp}) — retry dans {wait}s...")
        time.sleep(wait)

    return resp

# ── Traitement principal ──────────────────────────────────────────────────────────
def process_clip(file_path: str):
    name = os.path.basename(file_path)
    game = detect_game(file_path)

    log_write("INFO", f"Clip detecte : {name}")
    separator()
    if not SILENT:
        title = Text()
        title.append("● ", style="bold yellow")
        title.append(name, style="bold white")
        console.print(title)

    if not wait_for_file(file_path):
        ln_err("Fichier toujours verrouillé après timeout — abandon.")
        with stats_lock: stats["echoues"] += 1
        return

    total_dur = get_duration(file_path)
    file_mb   = os.path.getsize(file_path) / 1024 / 1024
    keep       = min(CLIP_DUREE, total_dur)
    trim_start = max(0.0, total_dur - keep)

    if not SILENT:
        sl = Text()
        sl.append("  source ", style="dim")
        sl.append(f"{total_dur:.1f}s  {file_mb:.1f} MB", style="bold white")
        sl.append(f"  → clip de ", style="dim")
        sl.append(f"{keep:.0f}s", style="bold cyan")
        sl.append(f"  (coupe {trim_start:.0f}s début)", style="dim")
        console.print(sl)

    base      = os.path.splitext(file_path)[0]
    converted = f"{base}_discord.mp4"

    video_bps  = calcul_bitrate_video(keep)
    video_kbps = video_bps // 1000

    if not SILENT:
        bl = Text()
        bl.append("  bitrate vidéo ciblé ", style="dim")
        bl.append(f"{video_kbps} kbps", style="bold cyan")
        bl.append(f"  (budget {LIMIT_MB} MB × {int(MARGE_SECURITE*100)}%)", style="dim")
        console.print(bl)

    size = encode_2pass(file_path, converted, trim_start, keep, video_bps)

    if size == -1:
        ln_err("Encodage échoué.")
        with stats_lock: stats["echoues"] += 1
        return

    size_mb     = size / 1024 / 1024
    limit_bytes = LIMIT_MB * 1024 * 1024

    if not SILENT:
        rl = Text()
        rl.append("  résultat ", style="dim")
        if size <= limit_bytes:
            rl.append(f"{size_mb:.2f} MB ", style="bold green")
            rl.append(" OK ", style="bold white on dark_green")
        else:
            rl.append(f"{size_mb:.2f} MB ", style="bold red")
            rl.append(f" dépasse {LIMIT_MB} MB ", style="bold white on dark_red")
        console.print(rl)

    if size > limit_bytes:
        ln_warn("Dépassement — ré-encodage avec marge plus stricte (88%)...")
        video_bps = calcul_bitrate_video(keep, marge=0.88)
        size      = encode_2pass(file_path, converted, trim_start, keep, video_bps)
        if size == -1:
            ln_err("Ré-encodage échoué.")
            with stats_lock: stats["echoues"] += 1
            return
        size_mb = size / 1024 / 1024

    message = build_discord_message(game, size_mb, keep, video_bps // 1000)
    t0      = time.time()
    resp    = upload_discord(converted, message)
    dt      = time.time() - t0

    if not SILENT:
        fl = Text()
        if isinstance(resp, Exception):
            fl.append("✖ ", style="bold red")
            fl.append(f"Erreur réseau : {resp}", style="red")
        elif resp.status_code in (200, 201):
            fl.append("✔ ", style="bold green")
            fl.append("Envoyé !  ", style="bold green")
            fl.append(f"720p 2-pass  {keep:.0f}s  {size_mb:.2f} MB  {dt:.1f}s", style="dim")
        elif resp.status_code == 413:
            fl.append("✖ ", style="bold red")
            fl.append(f"Discord refuse ({size_mb:.2f} MB) — 413", style="red")
        else:
            fl.append("✖ ", style="bold red")
            fl.append(f"Erreur Discord {resp.status_code}", style="red")
        console.print(fl)

    if isinstance(resp, Exception) or resp.status_code not in (200, 201):
        log_write("ERR ", f"Upload echoue : {resp}")
        with stats_lock: stats["echoues"] += 1
    else:
        log_write("OK  ", f"Clip envoye : {name}  {size_mb:.2f} MB  {keep:.0f}s  {dt:.1f}s")
        with stats_lock: stats["reussis"] += 1
        Thread(target=notify_clip_sent, args=(game, size_mb, keep), daemon=True).start()

    try: os.remove(converted)
    except Exception: pass

    with stats_lock: stats["traites"] += 1

# ── Banque de phrases drôles ──────────────────────────────────────────────────────
import random
PHRASES_CLIP = [
    "Un autre chef-d'oeuvre livre.",
    "Discord vient de recevoir un banger.",
    "Clip poste. Gloire eternelle en approche.",
    "Les gars vont kiffer.",
    "Vas-y, fais semblant que c'etait prevu.",
    "Certifie clip de ouf.",
    "Envoye plus vite que ton ping.",
    "Historique. Tout simplement.",
    "T'as encore fait ca toi.",
    "On encadre et on accroche au mur.",
    "Tes ennemis ont vu ca. Ils pleurent.",
    "Clip recu 5 etoiles sur Discord.",
    "Le serveur a fremi a la reception.",
    "Quelqu'un quelque part est jaloux.",
    "Clip livre. Signature en bas a droite.",
]

PHRASES_HIDE = [
    "Cache mais toujours la. Comme un ninja.",
    "Mode fantome active. Tes clips sont en securite.",
    "Invisible mais sur le coup.",
    "Tu me vois plus mais je te vois.",
    "Disparu des radars. Pas des clips.",
    "Mode discret ON. Tes highlights sont entre de bonnes mains.",
    "Je suis la, t'inquiete. Comme ton instinct de gamer.",
    "Fenetre fermee. Concentration maximale.",
    "Je suis dans les murs maintenant.",
    "Meme ta mere sait pas que je tourne.",
    "Cache comme tes skills au debut de la partie.",
    "J'existe toujours. Philosophiquement parlant.",
    "Je surveille tes clips depuis les ombres.",
    "Tu peux pas m'arreter. T'as meme pas essaye.",
    "Processus 4327 te salue bien.",
]


def notify_clip_sent(game: str, size_mb: float, duration: float):
    """Declenche la notification locale selon NOTIF_TYPE apres un envoi reussi."""
    try:
        if NOTIF_TYPE == "sound":
            _notif_sound()
        elif NOTIF_TYPE == "windows":
            _notif_windows_toast(game, size_mb, duration)
        else:
            _notif_overlay(game, size_mb, duration)
    except Exception as e:
        log_write("WARN", f"Notification locale echouee : {e}")

def _notif_sound():
    """Bip audio systeme Windows."""
    import winsound
    winsound.MessageBeep(winsound.MB_ICONASTERISK)

def _notif_windows_toast(game: str, size_mb: float, duration: float):
    """Notification native Windows via PowerShell (centre de notifications)."""
    title = f"Medal → Discord  ✔"
    body  = random.choice(PHRASES_CLIP)
    ps_cmd = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "[System.Windows.Forms.Application]::EnableVisualStyles();"
        "$n=New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon=[System.Drawing.SystemIcons]::Application;"
        "$n.Visible=$true;"
        f"$n.ShowBalloonTip(3000,'{title}','{body}',"
        "[System.Windows.Forms.ToolTipIcon]::Info);"
        "Start-Sleep -Milliseconds 3500;"
        "$n.Dispose()"
    )
    subprocess.Popen(
        ["powershell", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
        creationflags=subprocess.CREATE_NO_WINDOW
    )

def _notif_overlay(game: str, size_mb: float, duration: float):
    """Overlay coin haut-droit — fenetre WinForms transparente, 3 secondes."""
    line1 = "✔  Clip envoyé sur Discord"
    line2 = random.choice(PHRASES_CLIP)
    # Script PowerShell inline pour l'overlay
    ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$screen = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$W = 320; $H = 68

$form = New-Object System.Windows.Forms.Form
$form.FormBorderStyle = 'None'
$form.ShowInTaskbar   = $false
$form.TopMost         = $true
$form.Width  = $W; $form.Height = $H
$form.Left   = $screen.Right  - $W - 16
$form.Top    = $screen.Top    + 16
$form.BackColor   = [System.Drawing.Color]::FromArgb(12,12,24)
$form.Opacity     = 0.0
$form.StartPosition = 'Manual'

# Barre accent gauche (neon cyan)
$accent = New-Object System.Windows.Forms.Panel
$accent.Location = '0,0'; $accent.Size = '3,{H}'
$accent.BackColor = [System.Drawing.Color]::FromArgb(0,230,255)
$form.Controls.Add($accent)

# Ligne 1 : titre
$l1 = New-Object System.Windows.Forms.Label
$l1.Text      = '{line1}'
$l1.Location  = '14,10'; $l1.Size = '300,22'
$l1.Font      = New-Object System.Drawing.Font('Consolas',10,[System.Drawing.FontStyle]::Bold)
$l1.ForeColor = [System.Drawing.Color]::FromArgb(0,230,255)
$l1.BackColor = [System.Drawing.Color]::Transparent
$form.Controls.Add($l1)

# Ligne 2 : details
$l2 = New-Object System.Windows.Forms.Label
$l2.Text      = '{line2}'
$l2.Location  = '14,34'; $l2.Size = '300,18'
$l2.Font      = New-Object System.Drawing.Font('Consolas',8)
$l2.ForeColor = [System.Drawing.Color]::FromArgb(100,160,180)
$l2.BackColor = [System.Drawing.Color]::Transparent
$form.Controls.Add($l2)

$form.Show()

# Fade in
for ($i=0; $i -le 10; $i++) {{
    $form.Opacity = $i / 10.0
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 30
}}

Start-Sleep -Milliseconds 2600

# Fade out
for ($i=10; $i -ge 0; $i--) {{
    $form.Opacity = $i / 10.0
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 30
}}

$form.Close()
$form.Dispose()
"""
    subprocess.Popen(
        ["powershell", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
        creationflags=subprocess.CREATE_NO_WINDOW
    )

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
            ln_warn(f"Clip détecté : {os.path.basename(event.src_path)}")

# ── Desinstalleur ─────────────────────────────────────────────────────────────────
def create_uninstaller():
    """Genere desinstaller.bat dans le dossier d'installation au demarrage."""
    try:
        install_dir = os.path.dirname(os.path.abspath(__file__))
        uninst_path = os.path.join(install_dir, "desinstaller.bat")
        startup_vbs = os.path.join(
            os.environ.get("APPDATA", ""),
            "Microsoft", "Windows", "Start Menu",
            "Programs", "Startup", "Medal Discord.vbs"
        )
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
            "echo    - Le dossier Medal_to_Discord sur le bureau\n",
            "echo    - Le demarrage automatique Windows\n",
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
            f'if exist "{startup_vbs}" (\n',
            f'    del "{startup_vbs}" >nul 2>&1\n',
            "    echo        OK : demarrage automatique supprime.\n",
            ") else (\n",
            "    echo        Deja absent.\n",
            ")\n",
            "echo.\n",
            "echo  [2/2] Suppression du dossier Medal_to_Discord...\n",
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
            "echo  ^|   Medal to Discord a ete retire de ce PC.           ^|\n",
            "echo  +------------------------------------------------------+\n",
            "echo.\n",
            "pause\n",
        ]
        with open(uninst_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        log_write("INFO", f"desinstaller.bat cree : {uninst_path}")
    except Exception as e:
        log_write("WARN", f"Impossible de creer desinstaller.bat : {e}")

# ── Main ──────────────────────────────────────────────────────────────────────────
def main():
    ffmpeg_original = FFMPEG_PATH
    _resolve_ffmpeg()
    print_header()
    create_uninstaller()
    if FFMPEG_PATH != ffmpeg_original:
        ln_warn(f"FFmpeg reconfiguré automatiquement : {FFMPEG_PATH}")
    check_update()

    flag_path = os.path.abspath(__file__) + ".updated"
    if os.path.exists(flag_path):
        try:
            with open(flag_path, encoding="utf-8") as _f:
                _old, _new = _f.read().strip().split("→")
            os.remove(flag_path)
            notify_update_discord(_old, _new, PATCH_NOTES)
            ln_ok(f"Notification mise a jour v{_old} -> v{_new} envoyee sur Discord.")
        except Exception:
            pass

    separator()

    errors = False

    if not os.path.isfile(FFMPEG_PATH):
        ln_err(f"FFmpeg introuvable : {FFMPEG_PATH}")
        errors = True
    else:
        r     = subprocess.run([FFMPEG_PATH, "-encoders"], capture_output=True, text=True,
                               creationflags=NO_WINDOW)
        nvenc = "h264_nvenc" in r.stdout
        ver   = ""
        for line in r.stderr.splitlines():
            if "ffmpeg version" in line.lower():
                parts = line.split()
                ver = parts[2] if len(parts) > 2 else "OK"
                break
        if not SILENT:
            t = Text()
            t.append("✔ ", style="bold green")
            t.append("FFmpeg : ", style="green")
            t.append(ver or "OK", style="bold cyan")
            t.append("  h264_nvenc ", style="green")
            t.append("disponible" if nvenc else "ABSENT",
                     style="bold cyan" if nvenc else "bold red")
            console.print(t)
        if not nvenc:
            ln_err("h264_nvenc requis — vérifiez vos drivers NVIDIA.")
            errors = True

    if not os.path.isdir(FOLDER):
        ln_err(f"Dossier Medal introuvable : {FOLDER}")
        errors = True
    else:
        ln_ok(f"Dossier : {FOLDER}")

    if errors:
        if not SILENT:
            input("\nEntrée pour fermer...")
        return

    cleanup_leftover_tmps()

    observer = Observer()
    observer.schedule(MedalHandler(), FOLDER, recursive=True)
    observer.start()

    if not SILENT:
        active = Text()
        active.append("▶ ", style="bold green")
        active.append("Surveillance active — nouveaux clips seulement...", style="bold green")
        console.print(active)
        _print_shortcuts()

    Thread(target=notify_startup, daemon=True).start()

    try:
        while True:
            if not SILENT and _kbhit():
                key = _getch().lower()
                if key == "c":
                    observer.stop()
                    observer.join()
                    print_stats()
                    config_menu()
                    # Redémarre l'observer après config
                    observer = Observer()
                    observer.schedule(MedalHandler(), FOLDER, recursive=True)
                    observer.start()
                    if not SILENT:
                        separator()
                        active2 = Text()
                        active2.append("▶ ", style="bold green")
                        active2.append("Surveillance reprise...", style="bold green")
                        console.print(active2)
                        _print_shortcuts()
                elif key == "h":
                    _hide_window()
                elif key == "q" or key == "\x03":
                    raise KeyboardInterrupt
            time.sleep(0.1)
    except KeyboardInterrupt:
        if not SILENT:
            console.print()
            ln_warn("Arrêt demandé.")
        observer.stop()

    observer.join()
    print_stats()
    if not SILENT:
        console.print()


# ── Lecture clavier non-bloquante (Windows) ───────────────────────────────────────
def _kbhit() -> bool:
    try:
        import msvcrt
        return msvcrt.kbhit()
    except Exception:
        return False

def _getch() -> str:
    try:
        import msvcrt
        return msvcrt.getwch()
    except Exception:
        return ""

def _print_shortcuts():
    if SILENT: return
    t = Text()
    t.append("  [C] ", style="bold cyan")
    t.append("Config  ", style="dim")
    t.append("[H] ", style="bold yellow")
    t.append("Cacher  ", style="dim")
    t.append("[Q] ", style="bold red")
    t.append("Quitter", style="dim")
    console.print(t)
    console.print()


def _hide_window():
    """Cache complètement la fenêtre console (barre des tâches incluse)."""
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            # Retire le bouton de la barre des tâches avant de cacher
            GWL_EXSTYLE    = -20
            WS_EX_APPWINDOW  = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style & ~WS_EX_APPWINDOW) | WS_EX_TOOLWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE = 0
        # Toast de confirmation
        phrase = random.choice(PHRASES_HIDE)
        ps_cmd = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "[System.Windows.Forms.Application]::EnableVisualStyles();"
            "$n=New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon=[System.Drawing.SystemIcons]::Application;"
            "$n.Visible=$true;"
            f"$n.ShowBalloonTip(3000,'Medal → Discord  \U0001f47b','{phrase}',"
            "[System.Windows.Forms.ToolTipIcon]::Info);"
            "Start-Sleep -Milliseconds 3500;"
            "$n.Dispose()"
        )
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            creationflags=subprocess.CREATE_NO_WINDOW
        )
    except Exception:
        pass

def notify_startup():
    """Notification locale au démarrage selon NOTIF_TYPE."""
    try:
        if NOTIF_TYPE == "sound":
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        elif NOTIF_TYPE == "windows":
            title = "Medal → Discord  ▶"
            body  = "Surveillance active"
            ps_cmd = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "[System.Windows.Forms.Application]::EnableVisualStyles();"
                "$n=New-Object System.Windows.Forms.NotifyIcon;"
                "$n.Icon=[System.Drawing.SystemIcons]::Application;"
                "$n.Visible=$true;"
                f"$n.ShowBalloonTip(3000,'{title}','{body}',"
                "[System.Windows.Forms.ToolTipIcon]::Info);"
                "Start-Sleep -Milliseconds 3500;"
                "$n.Dispose()"
            )
            subprocess.Popen(
                ["powershell", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        else:  # overlay
            line1 = "▶  Medal → Discord actif"
            line2 = "Surveillance active"
            ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$screen = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$W = 320; $H = 68
$form = New-Object System.Windows.Forms.Form
$form.FormBorderStyle = 'None'
$form.ShowInTaskbar   = $false
$form.TopMost         = $true
$form.Width  = $W; $form.Height = $H
$form.Left   = $screen.Right  - $W - 16
$form.Top    = $screen.Top    + 16
$form.BackColor   = [System.Drawing.Color]::FromArgb(12,12,24)
$form.Opacity     = 0.0
$form.StartPosition = 'Manual'
$accent = New-Object System.Windows.Forms.Panel
$accent.Location = '0,0'; $accent.Size = '3,68'
$accent.BackColor = [System.Drawing.Color]::FromArgb(0,230,255)
$form.Controls.Add($accent)
$l1 = New-Object System.Windows.Forms.Label
$l1.Text      = '{line1}'
$l1.Location  = '14,10'; $l1.Size = '300,22'
$l1.Font      = New-Object System.Drawing.Font('Consolas',10,[System.Drawing.FontStyle]::Bold)
$l1.ForeColor = [System.Drawing.Color]::FromArgb(0,230,255)
$l1.BackColor = [System.Drawing.Color]::Transparent
$form.Controls.Add($l1)
$l2 = New-Object System.Windows.Forms.Label
$l2.Text      = '{line2}'
$l2.Location  = '14,34'; $l2.Size = '300,18'
$l2.Font      = New-Object System.Drawing.Font('Consolas',8)
$l2.ForeColor = [System.Drawing.Color]::FromArgb(100,160,180)
$l2.BackColor = [System.Drawing.Color]::Transparent
$form.Controls.Add($l2)
$form.Show()
for ($i=0; $i -le 10; $i++) {{
    $form.Opacity = $i / 10.0
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 30
}}
Start-Sleep -Milliseconds 2600
for ($i=10; $i -ge 0; $i--) {{
    $form.Opacity = $i / 10.0
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 30
}}
$form.Close()
$form.Dispose()
"""
            subprocess.Popen(
                ["powershell", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
                creationflags=subprocess.CREATE_NO_WINDOW
            )
    except Exception as e:
        log_write("WARN", f"Notification démarrage échouée : {e}")


def config_menu():
    """Menu interactif pour modifier les variables sans réinstaller."""
    if SILENT: return

    global WEBHOOK_URL, FOLDER, PSEUDO, FFMPEG_PATH, FFPROBE_PATH, NOTIF_TYPE

    while True:
        separator()
        console.print()
        t = Text()
        t.append("  ⚙  Configuration", style="bold cyan")
        console.print(t)
        console.print()

        # Affiche les valeurs actuelles
        _cfg_row("1", "Pseudo Discord",      PSEUDO)
        _cfg_row("2", "Dossier clips Medal", FOLDER)
        _cfg_row("3", "Webhook Discord",     WEBHOOK_URL[:52] + "..." if len(WEBHOOK_URL) > 52 else WEBHOOK_URL)
        _cfg_row("4", "Notification locale", f"{NOTIF_TYPE}  "
                 + {"overlay": "(overlay coin haut-droit)",
                    "sound":   "(bip audio systeme)",
                    "windows": "(toast Windows)"}
                 .get(NOTIF_TYPE, ""))
        console.print()

        t2 = Text()
        t2.append("  Choix (1-4) ou ", style="dim")
        t2.append("[Entrée]", style="bold")
        t2.append(" pour quitter : ", style="dim")
        console.print(t2, end="")

        choix = input().strip()

        if choix == "":
            break
        elif choix == "1":
            console.print(f"  Pseudo actuel : [bold]{PSEUDO}[/]  (Entrée pour garder)")
            console.print("  Nouveau pseudo : ", end="")
            val = input().strip()
            if val:
                PSEUDO = val
                _save_variable("PSEUDO", f'PSEUDO       = "{PSEUDO}"', r'PSEUDO\s*=\s*"[^"]*"')
                ln_ok(f"Pseudo mis à jour : {PSEUDO}")
        elif choix == "2":
            console.print(f"  Dossier actuel : [bold]{FOLDER}[/]  (Entrée pour garder)")
            console.print("  Nouveau dossier : ", end="")
            val = input().strip()
            if val:
                if not os.path.isdir(val):
                    ln_err(f"Dossier introuvable : {val}")
                else:
                    FOLDER = val
                    _save_variable("FOLDER", f'FOLDER       = r"{FOLDER}"', r'FOLDER\s*=\s*r"[^"]*"')
                    ln_ok(f"Dossier mis à jour : {FOLDER}")
        elif choix == "3":
            console.print("  Webhook actuel (Entrée pour garder) :")
            console.print(f"  [dim]{WEBHOOK_URL}[/]")
            console.print("  Nouveau webhook : ", end="")
            val = input().strip()
            if val:
                WEBHOOK_URL = val
                _save_variable("WEBHOOK_URL", f'WEBHOOK_URL  = "{WEBHOOK_URL}"', r'WEBHOOK_URL\s*=\s*"[^"]*"')
                ln_ok("Webhook mis à jour.")
        elif choix == "4":
            _menu_notif()
        else:
            ln_warn("Choix invalide.")

    separator()


def _cfg_row(key: str, label: str, value: str):
    """Affiche une ligne de config formatée."""
    t = Text()
    t.append(f"  [{key}] ", style="bold cyan")
    t.append(f"{label:<22}", style="dim")
    t.append(value, style="bold white")
    console.print(t)


def _menu_notif():
    """Sous-menu pour choisir le type de notification."""
    global NOTIF_TYPE
    console.print()
    notif_options = [
        ("1", "overlay",  "Overlay coin haut-droit  (recommandé)"),
        ("2", "sound",    "Bip audio système Windows"),
        ("3", "windows",  "Toast natif Windows"),
    ]
    for key, val, desc in notif_options:
        active = " ◀ actuel" if val == NOTIF_TYPE else ""
        style = "bold cyan" if val == NOTIF_TYPE else "dim"
        t = Text()
        t.append(f"  [{key}] ", style="bold cyan")
        t.append(desc, style=style)
        t.append(active, style="bold green")
        console.print(t)
    console.print()
    console.print("  Choix (1-3) ou Entrée pour annuler : ", end="")
    choix = input().strip()
    mapping = {"1": "overlay", "2": "sound", "3": "windows"}
    if choix in mapping:
        NOTIF_TYPE = mapping[choix]
        _save_variable("NOTIF_TYPE", f'NOTIF_TYPE   = "{NOTIF_TYPE}"', r'NOTIF_TYPE\s*=\s*"[^"]*"')
        ln_ok(f"Notification mise à jour : {NOTIF_TYPE}")
        # Test immédiat
        console.print("  [dim]Test de la notification...[/]")
        Thread(target=notify_clip_sent, args=("Test", 2.4, 20), daemon=True).start()
        time.sleep(0.5)


def _save_variable(name: str, new_line: str, pattern: str):
    """Réécrit une variable dans le fichier .py courant."""
    try:
        script_path = os.path.abspath(__file__)
        with open(script_path, "r", encoding="utf-8") as f:
            content = f.read()
        import re as _re
        _nl = new_line
        new_content = _re.sub(pattern, lambda m: _nl, content)
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        log_write("INFO", f"{name} sauvegarde dans le script.")
    except Exception as e:
        ln_err(f"Impossible de sauvegarder {name} : {e}")


if __name__ == "__main__":
    log_write("INFO", f"=== Script demarre v{VERSION} ===")
    try:
        main()
    except KeyboardInterrupt:
        log_write("INFO", "Script arrete par l'utilisateur")
    except Exception as e:
        import traceback
        err_detail = traceback.format_exc()
        log_write("ERR ", f"CRASH : {e}\n{err_detail}")
        try:
            lines = []
            lines.append(f"⚠️ **Crash du script**  v{VERSION}")
            lines.append(f"👤 {PSEUDO}")
            lines.append("")
            lines.append("📋 **Erreur**")
            lines.append(f"  ▸ {e}")
            lines.append("")
            lines.append("🔧 Verifiez le fichier `medal_discord.log` pour plus de details.")
            msg = "\n".join(lines)
            requests.post(WEBHOOK_URL, json={"content": msg}, timeout=10)
        except Exception:
            pass
