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

# ── Détection mode silencieux ────────────────────────────────────────────────────
# pythonw.exe n'a pas de console (sys.stdout est None)
SILENT = sys.stdout is None

if not SILENT:
    from rich.console import Console
    from rich.progress import Progress, BarColumn, TextColumn
    from rich.text import Text
    console = Console(highlight=False)

def ln_ok(msg):
    if not SILENT: console.print(Text.assemble(("✔ ", "bold green"), (msg, "green")))

def ln_warn(msg):
    if not SILENT: console.print(Text.assemble(("⚠ ", "bold yellow"), (msg, "yellow")))

def ln_err(msg):
    if not SILENT: console.print(Text.assemble(("✖ ", "bold red"), (msg, "red")))

def separator():
    if not SILENT: console.rule(style="bright_black")

# ── CONFIGURATION ──────────────────────────────────────────────────────────────
WEBHOOK_URL  = "https://discord.com/api/webhooks/1499530486928904312/DvR9lA-bgAXE4omeyDYMP1VHreXcUjD50lhzlVvL5Xei2qmJiJkUDRqfQHV3FYAwD1e1"
FOLDER       = r"f:\Medal\Clips"
FFMPEG_PATH  = r"C:\Users\j-phi\Desktop\Medal a Discord\ffmpeg-8.0.1-full_build\bin\ffmpeg.exe"
FFPROBE_PATH = FFMPEG_PATH.replace("ffmpeg.exe", "ffprobe.exe")

PSEUDO         = "Pablo_G"   # ← Nom affiché sur Discord
LIMIT_MB       = 10
MARGE_SECURITE = 0.95
AUDIO_KBPS     = 128
CLIP_DUREE     = 20
RETRY_UPLOAD   = 3
# ────────────────────────────────────────────────────────────────────────────────

# ── Auto-update depuis GitHub ────────────────────────────────────────────────────
VERSION      = "1.3"
GITHUB_RAW   = "https://raw.githubusercontent.com/PabloooG/Medal_to_discord/main/medal_discord.py"
GITHUB_VER   = "https://raw.githubusercontent.com/PabloooG/Medal_to_discord/main/version.txt"

def check_update():
    """Vérifie si une nouvelle version est disponible sur GitHub et met à jour si besoin."""
    if not SILENT:
        console.print()
        console.print("  [dim]Vérification des mises à jour...[/]")
    try:
        r = requests.get(GITHUB_VER, timeout=5)
        if r.status_code != 200:
            ln_warn(f"Impossible de vérifier la version (HTTP {r.status_code})")
            return
        latest = r.text.strip()
        if latest == VERSION:
            ln_ok(f"Version {VERSION} — à jour.")
            return

        ln_warn(f"Nouvelle version {latest} disponible ! (actuelle : {VERSION})")
        if not SILENT:
            console.print("  [dim cyan]Téléchargement de la mise à jour...[/]")

        r2 = requests.get(GITHUB_RAW, timeout=15)
        if r2.status_code != 200:
            ln_err(f"Échec du téléchargement (HTTP {r2.status_code})")
            return

        script_path = os.path.abspath(__file__)
        # Sauvegarde de l'ancien fichier au cas où
        backup_path = script_path + ".bak"
        try:
            import shutil
            shutil.copy2(script_path, backup_path)
        except Exception:
            pass

        with open(script_path, "w", encoding="utf-8") as f:
            f.write(r2.text)

        ln_ok(f"Mise à jour v{latest} téléchargée ! Redémarrage...")
        time.sleep(2)
        # Redémarre le script avec la nouvelle version
        os.execv(sys.executable, [sys.executable] + sys.argv)

    except requests.exceptions.ConnectionError:
        ln_warn("Pas de connexion internet — vérification update ignorée.")
    except Exception as e:
        ln_warn(f"Vérification update échouée : {e}")
# ────────────────────────────────────────────────────────────────────────────────

stats      = {"traites": 0, "reussis": 0, "echoues": 0, "queue": 0}
stats_lock = Lock()

TEMP_DIR  = os.environ.get("TEMP", os.path.expanduser("~"))
NO_WINDOW = subprocess.CREATE_NO_WINDOW

# ── Affichage ────────────────────────────────────────────────────────────────────
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

# ── File d'attente ───────────────────────────────────────────────────────────────
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

# ── Cleanup ──────────────────────────────────────────────────────────────────────
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

# ── Utilitaires ──────────────────────────────────────────────────────────────────
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

# ── Encodage GPU 2-pass ──────────────────────────────────────────────────────────
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

    # ── Mode avec affichage (console disponible) ─────────────────────────────
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
                    for f in glob.glob(passlog + "*"):
                        try: os.remove(f)
                        except Exception: pass
                    return -1
                prog.update(t, completed=100,
                            status=f"[bold green]OK[/] [dim]{dt:.1f}s[/]")

    # ── Mode silencieux (pythonw / planificateur) ────────────────────────────
    else:
        for cmd in passes:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    cwd=TEMP_DIR, creationflags=NO_WINDOW)
            if result.returncode != 0:
                for f in glob.glob(passlog + "*"):
                    try: os.remove(f)
                    except Exception: pass
                return -1

    for f in glob.glob(passlog + "*"):
        try: os.remove(f)
        except Exception: pass

    return os.path.getsize(output_path) if os.path.exists(output_path) else -1

# ── Upload Discord ───────────────────────────────────────────────────────────────
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

# ── Traitement principal ─────────────────────────────────────────────────────────
def process_clip(file_path: str):
    name = os.path.basename(file_path)
    game = detect_game(file_path)

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
        with stats_lock: stats["echoues"] += 1
    else:
        with stats_lock: stats["reussis"] += 1

    try: os.remove(converted)
    except Exception: pass

    with stats_lock: stats["traites"] += 1

# ── Watchdog ─────────────────────────────────────────────────────────────────────
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

# ── Main ──────────────────────────────────────────────────────────────────────────
def main():
    print_header()
    check_update()   # ← Vérification mise à jour au démarrage
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
        console.print()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        if not SILENT:
            console.print()
            ln_warn("Arrêt demandé.")
        observer.stop()

    observer.join()
    print_stats()
    if not SILENT:
        console.print()


if __name__ == "__main__":
    main()
