import os
import re
import time
import json
import shutil
import asyncio
import hashlib
import subprocess
from datetime import date
from aiohttp import web

import requests
from pyrogram import Client, filters, types, enums, idle, errors
from yt_dlp import YoutubeDL

# =======================
# CONFIG
# =======================
OWNER_ID = 519459195

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

INVITE_LINK = "https://t.me/+eooytvOAwjc0NTI1"
CONTACT_URL = "https://t.me/poocha"

DOWNLOAD_DIR = "/tmp/downloads"
THUMB_DIR = "/tmp/thumbnails"
DB_FILE = "/tmp/bot_db.json"
COOKIES_FILE = "/tmp/cookies.txt"

DAILY_LIMIT = 5 * 1024 * 1024 * 1024  # 5GB/day

# =======================
# DATABASE
# =======================
DB = {"users": {}, "sessions": {}, "cache": {}}

def db_load():
    global DB
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                DB = json.load(f)
        except:
            pass
    DB.setdefault("users", {})
    DB.setdefault("sessions", {})
    DB.setdefault("cache", {})

def db_save():
    with open(DB_FILE, "w") as f:
        json.dump(DB, f)

def user_get(uid: int) -> dict:
    k = str(uid)
    if k not in DB["users"]:
        DB["users"][k] = {
            "thumb": None,
            "state": "none",
            "used": 0,
            "reset": date.today().isoformat(),
            "is_pro": (uid == OWNER_ID),
            "is_banned": False
        }
    # Reset daily quota
    if DB["users"][k].get("reset") != date.today().isoformat():
        DB["users"][k]["reset"] = date.today().isoformat()
        DB["users"][k]["used"] = 0
    return DB["users"][k]

def session_get(uid: int):
    return DB["sessions"].get(str(uid))

def session_set(uid: int, data: dict):
    DB["sessions"][str(uid)] = data
    db_save()

def session_clear(uid: int):
    DB["sessions"].pop(str(uid), None)
    user_get(uid)["state"] = "none"
    db_save()

# =======================
# HELPERS
# =======================
def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "", name.strip())
    name = re.sub(r"\s+", " ", name).strip()
    return name[:180] if name else "file"

def get_extension(filename: str) -> str:
    """Extract extension from filename"""
    ext = os.path.splitext(filename)[1]
    return ext if ext else ""

def is_youtube(url: str) -> bool:
    u = url.lower()
    return "youtube.com" in u or "youtu.be" in u

def human_size(n) -> str:
    if not n:
        return "0B"
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"

def human_time(sec) -> str:
    if not sec or sec <= 0:
        return "—"
    sec = int(sec)
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"

def progress_bar(pct: float, speed: float, eta: float, total: int, done: int) -> str:
    filled = int(15 * pct / 100)
    bar = "█" * filled + "░" * (15 - filled)
    return (
        f"📥 **Downloading...**\n\n"
        f"`[{bar}]` {pct:.1f}%\n\n"
        f"📦 {human_size(done)} / {human_size(total)}\n"
        f"⚡ {human_size(speed)}/s | ⏱️ {human_time(eta)}"
    )

def upload_progress_text(pct: float, speed: float, eta: float, total: int, done: int) -> str:
    filled = int(15 * pct / 100)
    bar = "█" * filled + "░" * (15 - filled)
    return (
        f"📤 **Uploading...**\n\n"
        f"`[{bar}]` {pct:.1f}%\n\n"
        f"📦 {human_size(done)} / {human_size(total)}\n"
        f"⚡ {human_size(speed)}/s | ⏱️ {human_time(eta)}"
    )

async def safe_edit(msg, text: str, reply_markup=None):
    try:
        return await msg.edit_text(text, reply_markup=reply_markup)
    except errors.MessageNotModified:
        return msg
    except errors.FloodWait as e:
        await asyncio.sleep(e.value)
        return await safe_edit(msg, text, reply_markup)
    except:
        return msg

async def is_subscribed(uid: int) -> bool:
    if uid == OWNER_ID:
        return True
    try:
        m = await app.get_chat_member(CHANNEL_ID, uid)
        return m.status in (enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER)
    except:
        return False

# =======================
# KEYBOARDS
# =======================
def join_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("📢 Join Channel", url=INVITE_LINK)],
        [types.InlineKeyboardButton("✅ I've Joined", callback_data="check_join")]
    ])

def cancel_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

def upload_options_kb():
    """Main upload options after download"""
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("✏️ Rename", callback_data="rename"),
            types.InlineKeyboardButton("📄 File", callback_data="upload_file"),
            types.InlineKeyboardButton("🎬 Video", callback_data="upload_video")
        ],
        [types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

def rename_options_kb():
    """Rename options"""
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("📝 Default Name", callback_data="rename_default"),
            types.InlineKeyboardButton("✏️ Custom Name", callback_data="rename_custom")
        ],
        [types.InlineKeyboardButton("🔙 Back", callback_data="back_to_upload")]
    ])

def youtube_quality_kb():
    """YouTube quality selection"""
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("1080p", callback_data="yt_1080"),
            types.InlineKeyboardButton("720p", callback_data="yt_720"),
            types.InlineKeyboardButton("480p", callback_data="yt_480")
        ],
        [
            types.InlineKeyboardButton("360p", callback_data="yt_360"),
            types.InlineKeyboardButton("🎵 MP3", callback_data="yt_mp3"),
            types.InlineKeyboardButton("🎵 M4A", callback_data="yt_m4a")
        ],
        [types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

def menu_kb(uid: int):
    kb = [
        [
            types.InlineKeyboardButton("📊 My Stats", callback_data="stats"),
            types.InlineKeyboardButton("🖼️ Thumbnail", callback_data="thumb_menu")
        ],
        [types.InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    if uid == OWNER_ID:
        kb.append([types.InlineKeyboardButton("⚙️ Admin", callback_data="admin")])
    return types.InlineKeyboardMarkup(kb)

def thumb_kb():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("👁️ View", callback_data="thumb_view"),
            types.InlineKeyboardButton("🗑️ Delete", callback_data="thumb_delete")
        ],
        [types.InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
    ])

# =======================
# DOWNLOAD FUNCTIONS
# =======================
def ydl_progress_hook(uid: int, msg, last_update: dict):
    def hook(d):
        sess = session_get(uid)
        if sess and sess.get("cancel"):
            raise Exception("CANCELLED")
        
        if d.get("status") != "downloading":
            return
        
        now = time.time()
        if now - last_update.get("t", 0) < 2:
            return
        
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes") or 0
        speed = d.get("speed") or 0
        eta = d.get("eta") or 0
        pct = (done / total * 100) if total else 0
        
        last_update["t"] = now
        
        asyncio.get_event_loop().create_task(
            safe_edit(msg, progress_bar(pct, speed, eta, total, done), cancel_kb())
        )
    return hook

async def download_with_ytdlp(uid: int, url: str, msg, quality: str = None):
    """Download using yt-dlp"""
    last_update = {"t": 0}
    
    opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": f"{DOWNLOAD_DIR}/%(title)s.%(ext)s",
        "noplaylist": True,
        "retries": 5,
        "socket_timeout": 30,
        "progress_hooks": [ydl_progress_hook(uid, msg, last_update)],
    }
    
    if os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    
    # Set format based on quality
    if quality:
        if quality in ["1080", "720", "480", "360"]:
            opts["format"] = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
        elif quality == "mp3":
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}]
        elif quality == "m4a":
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}]
    
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
        
        # Handle audio conversion path change
        if quality in ["mp3", "m4a"]:
            path = os.path.splitext(path)[0] + f".{quality}"
        
        title = info.get("title", "video")
        return path, title

async def download_direct(uid: int, url: str, msg):
    """Download direct links"""
    r = requests.get(url, stream=True, timeout=30)
    r.raise_for_status()
    
    # Get filename from URL or headers
    filename = url.split("/")[-1].split("?")[0] or "file"
    if "content-disposition" in r.headers:
        cd = r.headers["content-disposition"]
        if "filename=" in cd:
            filename = cd.split("filename=")[1].strip('"\'')
    
    filename = safe_filename(filename)
    path = os.path.join(DOWNLOAD_DIR, filename)
    total = int(r.headers.get("content-length", 0))
    done = 0
    start = time.time()
    last_update = 0
    
    with open(path, "wb") as f:
        for chunk in r.iter_content(chunk_size=256 * 1024):
            sess = session_get(uid)
            if sess and sess.get("cancel"):
                raise Exception("CANCELLED")
            
            if chunk:
                f.write(chunk)
                done += len(chunk)
                
                now = time.time()
                if now - last_update >= 2:
                    last_update = now
                    speed = done / max(1, now - start)
                    eta = (total - done) / speed if speed and total else 0
                    pct = (done / total * 100) if total else 0
                    await safe_edit(msg, progress_bar(pct, speed, eta, total, done), cancel_kb())
    
    return path, os.path.splitext(filename)[0]

# =======================
# SCREENSHOT GENERATOR
# =======================
async def generate_screenshots(video_path: str, count: int = 5):
    """Generate screenshots from video"""
    screens = []
    out_dir = os.path.join(DOWNLOAD_DIR, f"screens_{int(time.time())}")
    os.makedirs(out_dir, exist_ok=True)
    
    try:
        # Get video duration
        cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{video_path}"'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        duration = float(result.stdout.strip() or "0")
        
        if duration <= 0:
            return [], out_dir
        
        # Generate screenshots at intervals
        interval = duration / (count + 1)
        
        for i in range(1, count + 1):
            timestamp = interval * i
            output_path = os.path.join(out_dir, f"screen_{i}.jpg")
            
            cmd = [
                "ffmpeg", "-ss", str(timestamp),
                "-i", video_path,
                "-vframes", "1",
                "-q:v", "2",
                "-y", output_path
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            if os.path.exists(output_path):
                screens.append(output_path)
        
        return screens, out_dir
    except Exception as e:
        print(f"Screenshot error: {e}")
        return [], out_dir

# =======================
# UPLOAD FUNCTION
# =======================
async def upload_file(uid: int, msg, path: str, filename: str, as_video: bool):
    """Upload file with progress"""
    start = time.time()
    last_update = {"t": 0}
    file_size = os.path.getsize(path)
    
    # Get user thumbnail
    user = user_get(uid)
    thumb = user.get("thumb")
    if thumb and not os.path.exists(thumb):
        thumb = None
    
    async 
