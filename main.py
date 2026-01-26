"""
DL Bot v2.0 - FINAL FIXED VERSION
All features working, syntax errors fixed
"""

import os
import re
import time
import json
import math
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
WATERMARK_TEXT = "channel name"

# =======================
# DATABASE
# =======================
DB = {"users": {}, "active": {}, "cache": {}}

def db_load():
    global DB
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                DB = json.load(f)
        except Exception:
            DB = {"users": {}, "active": {}, "cache": {}}
    DB.setdefault("users", {})
    DB.setdefault("active", {})
    DB.setdefault("cache", {})

def db_save():
    tmp = DB_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(DB, f, ensure_ascii=False)
    os.replace(tmp, DB_FILE)

def ukey(uid: int) -> str: return str(uid)
def today_str() -> str: return date.today().isoformat()

def user_get(uid: int) -> dict:
    k = ukey(uid)
    if k not in DB["users"]:
        DB["users"][k] = {
            "thumb": None, "state": "none", "pending": {}, "used": 0,
            "reset": today_str(), "is_pro": (uid == OWNER_ID), "is_banned": False
        }
    if DB["users"][k].get("reset") != today_str():
        DB["users"][k]["reset"] = today_str()
        DB["users"][k]["used"] = 0
    return DB["users"][k]

def session_get(uid: int) -> dict | None: return DB["active"].get(ukey(uid))
def session_set(uid: int, s: dict): 
    DB["active"][ukey(uid)] = s
    db_save()

def session_clear(uid: int):
    DB["active"].pop(ukey(uid), None)
    u = user_get(uid)
    u["state"] = "none"
    u["pending"] = {}
    db_save()

def url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()

# =======================
# HELPERS
# =======================
def safe_filename(name: str) -> str:
    name = name.strip().replace("\n", " ")
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:180] if name else "file"

def is_youtube(url: str) -> bool:
    u = url.lower()
    return "youtube.com" in u or "youtu.be" in u

def looks_like_playlist(url: str) -> bool:
    return "list=" in url.lower()

def human_size(n: int | float | None) -> str:
    if not n: return "0B"
    n = float(n)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024: return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"

def human_time(seconds: float | None) -> str:
    if not seconds or seconds <= 0: return "—"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h: return f"{h}h {m}m"
    if m: return f"{m}m {s}s"
    return f"{s}s"

async def safe_edit(msg: types.Message, text: str, reply_markup=None):
    try: return await msg.edit_text(text, reply_markup=reply_markup)
    except errors.MessageNotModified: return msg
    except Exception: return msg

async def is_subscribed(uid: int) -> bool:
    if uid == OWNER_ID: return True
    if user_get(uid).get("is_banned"): return False
    try:
        m = await app.get_chat_member(CHANNEL_ID, uid)
        return m.status in (enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER)
    except: return False

# =======================
# UI
# =======================
def cancel_kb():
    return types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Cancel", callback_data="act_cancel")]])

def join_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("➕ Join Channel", url=INVITE_LINK)],
        [types.InlineKeyboardButton("✅ Verify", callback_data="join_verify")]
    ])

def menu_kb(uid: int):
    kb = [
        [types.InlineKeyboardButton("❓ Help", callback_data="menu_help"), types.InlineKeyboardButton("🆔 My ID", callback_data="menu_id")],
        [types.InlineKeyboardButton("🖼 Thumbnail Manager", callback_data="thumb_menu")],
    ]
    if uid == OWNER_ID:
        kb.append([types.InlineKeyboardButton("& Admin Dashboard", callback_data="admin_menu")])
    else:
        kb.append([types.InlineKeyboardButton("📊 Plan", callback_data="menu_plan")])
        kb.append([types.InlineKeyboardButton("💎 Upgrade", url=CONTACT_URL)])
    kb.append([types.InlineKeyboardButton("✖ Exit", callback_data="menu_exit")])
    return types.InlineKeyboardMarkup(kb)

def thumb_menu_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("👁 View", callback_data="thumb_view"), types.InlineKeyboardButton("🗑 Delete", callback_data="thumb_delete")],
        [types.InlineKeyboardButton("✖ Exit", callback_data="thumb_exit")]
    ])

def image_thumb_prompt_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("✅ Set as Thumbnail", callback_data="img_set_thumb"), types.InlineKeyboardButton("Skip", callback_data="img_skip_thumb")]
    ])

def ready_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("✏️ Rename", callback_data="act_rename"), types.InlineKeyboardButton("⬆️ Upload", callback_data="act_upload")],
        [types.InlineKeyboardButton("Cancel", callback_data="act_cancel")]
    ])

def rename_choice_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("✅ Use Default", callback_data="ren_default"), types.InlineKeyboardButton("✏️ Enter New Name", callback_data="ren_custom")],
        [types.InlineKeyboardButton("Cancel", callback_data="act_cancel")]
    ])

def upload_choice_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("▶️ Upload as Video", callback_data="up_video"), types.InlineKeyboardButton("📄 Upload as Document", callback_data="up_doc")],
        [types.InlineKeyboardButton("%reenshots + Upload", callback_data="up_screens")],
        [types.InlineKeyboardButton("Cancel", callback_data="act_cancel")]
    ])

def yt_action_kb(is_playlist: bool):
    rows = [
        [types.InlineKeyboardButton("1080p", callback_data="yt_v_1080"), types.InlineKeyboardButton("720p", callback_data="yt_v_720"), types.InlineKeyboardButton("480p", callback_data="yt_v_480"), types.InlineKeyboardButton("360p", callback_data="yt_v_360")],
        [types.InlineKeyboardButton("MP3", callback_data="yt_a_mp3"), types.InlineKeyboardButton("M4A", callback_data="yt_a_m4a"), types.InlineKeyboardButton("AAC", callback_data="yt_a_aac")],
    ]
    if is_playlist: rows.insert(0, [types.InlineKeyboardButton("📂 Playlist (one-by-one)", callback_data="yt_playlist")])
    rows.append([types.InlineKeyboardButton("Cancel", callback_data="act_cancel")])
    return types.InlineKeyboardMarkup(rows)

def cached_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("▶️ Upload as Video", callback_data="cache_video"), types.InlineKeyboardButton("📄 Upload as Document", callback_data="cache_doc")],
        [types.InlineKeyboardButton("✏️ Rename (Default)", callback_data="cache_ren_def"), types.InlineKeyboardButton("✏️ Rename (Custom)", callback_data="cache_ren_custom")],
        [types.InlineKeyboardButton("⬇️ Download again", callback_data="cache_redownload")],
        [types.InlineKeyboardButton("Cancel", callback_data="act_cancel"), types.InlineKeyboardButton("Exit", callback_data="cache_exit")]
    ])

def admin_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("📊 Reports", callback_data="admin_reports"), types.InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [types.InlineKeyboardButton("➕ Add Pro", callback_data="admin_add_pro")],
        [types.InlineKeyboardButton("🔨 Ban User", callback_data="admin_ban")],
        [types.InlineKeyboardButton("← Back", callback_data="admin_back")]
    ])

def bc_confirm_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("✅ Confirm", callback_data="bc_confirm"), types.InlineKeyboardButton("✖ Stop", callback_data="bc_stop")]
    ])

# =======================
# WATERMARK
# =======================
def apply_watermark(input_path: str) -> str:
    out_path = os.path.splitext(input_path)[0] + "_wm.mp4"
    txt = WATERMARK_TEXT.replace("'", "")
    cmd = f'ffmpeg -y -i "{input_path}" -vf "drawtext=text=\'{txt}\':x=10:y=H-th-10:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.4" -c:a copy "{out_path}"'
    subprocess.call(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_path if os.path.exists(out_path) else input_path

# =======================
# DOWNLOAD PROGRESS
# =======================
def ydl_opts_with_progress(uid: int, msg: types.Message):
    last = {"t": 0.0}
    def hook(d):
        try:
            sess = session_get(uid)
            if sess and sess.get("cancel"): raise Exception("CANCELLED")
            if d.get("status") != "downloading": return
            now = time.time()
            if now - last["t"] < 2: return
            last["t"] = now
            total = d.get("total_bytes") or 0
            done = d.get("downloaded_bytes") or 0
            eta = d.get("eta")
            pct = (done / total * 100) if total else 0.0
            asyncio.get_event_loop().create_task(
                safe_edit(msg, f"⬇️ Downloading… {pct:.1f}% | {human_size(done)}/{human_size(total)} | ETA {human_time(eta)}", reply_markup=cancel_kb())
            )
        except: pass
    opts = {
        "quiet": True, "no_warnings": True, "outtmpl": f"{DOWNLOAD_DIR}/%(title)s.%(ext)s",
        "noplaylist": True, "retries": 5, "fragment_retries": 5, "socket_timeout": 20,
        "concurrent_fragment_downloads": 4, "http_chunk_size": 10 * 1024 * 1024,
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "web_embedded"]}},
        "progress_hooks": [hook],
    }
    if os.path.exists(COOKIES_FILE): opts["cookiefile"] = COOKIES_FILE
    return opts

async def download_http(uid: int, url: str, msg: types.Message) -> tuple[str, str, str, int]:
    r = requests.get(url, stream=True, timeout=20)
    r.raise_for_status()
    name = safe_filename(url.split("/")[-1] or "file.bin")
    path = os.path.join(DOWNLOAD_DIR, name)
    ext = os.path.splitext(name)[1]
    total = int(r.headers.get("content-length") or 0)
    done = 0
    start = time.time()
    last = 0.0
    with open(path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 256):
            sess = session_get(uid)
            if sess and sess.get("cancel"): raise Exception("CANCELLED")
            if not chunk: continue
            f.write(chunk)
            done += len(chunk)
            now = time.time()
            if now - last >= 2:
                last = now
                speed = done / max(1, now - start)
                eta = (total - done) / speed if total and speed > 0 else None
                pct = (done / total * 100) if total else 0.0
                await safe_edit(msg, f"⬇️ Downloading… {pct:.1f}% | {human_size(done)}/{human_size(total)} | ETA {human_time(eta)}", reply_markup=cancel_kb())
    return path, name, ext, done

async def download_any(uid: int, url: str, msg: types.Message) -> tuple[str, str, str, int]:
    opts = ydl_opts_with_progress(uid, msg)
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            name = os.path.basename(path)
            ext = os.path.splitext(name)[1] or ""
            size = os.path.getsize(path) if os.path.exists(path) else 0
            return path, name, ext, size
    except:
        return await download_http(uid, url, msg)

async def download_youtube(uid: int, url: str, kind: str, val: str, msg: types.Message) -> tuple[str, str, str, int]:
    opts = ydl_opts_with_progress(uid, msg)
    if kind == "v":
        h = int(val)
        opts["format"] = f"bestvideo[height<={h}]+bestaudio/best"
    else:
        opts["format"] = "bestaudio/best"
        if val in ("mp3", "m4a", "aac"):
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": val}]
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
        if kind == "a" and val in ("mp3", "m4a", "aac"):
            path = os.path.splitext(path)[0] + "." + val
        name = os.path.basename(path)
        ext = os.path.splitext(name)[1] or ""
        size = os.path.getsize(path) if os.path.exists(path) else 0
        return path, name, ext, size

# =======================
# SCREENSHOTS
# =======================
async def generate_screenshots(video_path: str, uid: int):
    out_dir = os.path.join(DOWNLOAD_DIR, 
