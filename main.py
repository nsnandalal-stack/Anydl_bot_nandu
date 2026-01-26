import os
import re
import time
import json
import shutil
import asyncio
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
DOWNLOAD_DIR = "/tmp/downloads"
THUMB_DIR = "/tmp/thumbnails"
DB_FILE = "/tmp/bot_db.json"
COOKIES_FILE = "/tmp/cookies.txt"
DAILY_LIMIT = 5 * 1024 * 1024 * 1024

# =======================
# DATABASE
# =======================
DB = {"users": {}, "sessions": {}}

def db_load():
    global DB
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                DB = json.load(f)
        except:
            pass

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
    return name[:180] if name else "file"

def get_ext(filename: str) -> str:
    return os.path.splitext(filename)[1]

def is_youtube(url: str) -> bool:
    return "youtube.com" in url.lower() or "youtu.be" in url.lower()

def human_size(n) -> str:
    if not n:
        return "0B"
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"

def human_time(s) -> str:
    if not s or s <= 0:
        return "—"
    s = int(s)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}h{m}m" if h else f"{m}m{s}s" if m else f"{s}s"

def progress_text(pct, speed, eta, total, done, action="Downloading"):
    bar = "█" * int(15 * pct / 100) + "░" * (15 - int(15 * pct / 100))
    return (
        f"{'📥' if 'Down' in action else '📤'} **{action}...**\n\n"
        f"`[{bar}]` {pct:.1f}%\n"
        f"📦 {human_size(done)}/{human_size(total)}\n"
        f"⚡ {human_size(speed)}/s • ⏱️ {human_time(eta)}"
    )

async def safe_edit(msg, text, kb=None):
    try:
        return await msg.edit_text(text, reply_markup=kb)
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
    return types.InlineKeyboardMarkup([[types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])

def upload_kb():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("✏️ Rename", callback_data="rename"),
            types.InlineKeyboardButton("📄 File", callback_data="up_file"),
            types.InlineKeyboardButton("🎬 Video", callback_data="up_video")
        ],
        [types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

def rename_kb():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("📝 Default", callback_data="ren_default"),
            types.InlineKeyboardButton("✏️ Custom", callback_data="ren_custom")
        ],
        [types.InlineKeyboardButton("🔙 Back", callback_data="back")]
    ])

def yt_kb():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("1080p", callback_data="yt_1080"),
            types.InlineKeyboardButton("720p", callback_data="yt_720"),
            types.InlineKeyboardButton("480p", callback_data="yt_480")
        ],
        [
            types.InlineKeyboardButton("360p", callback_data="yt_360"),
            types.InlineKeyboardButton("🎵 MP3", callback_data="yt_mp3")
        ],
        [types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

def menu_kb(uid):
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("🖼️ Thumbnail", callback_data="thumb"), types.InlineKeyboardButton("📊 Stats", callback_data="stats")],
        [types.InlineKeyboardButton("❓ Help", callback_data="help")]
    ])

def thumb_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("👁️ View", callback_data="thumb_view"), types.InlineKeyboardButton("🗑️ Delete", callback_data="thumb_del")],
        [types.InlineKeyboardButton("🔙 Back", callback_data="main")]
    ])

# =======================
# DOWNLOAD
# =======================
async def download_file(uid: int, url: str, msg, quality=None):
    last = {"t": 0}
    
    def hook(d):
        if session_get(uid) and session_get(uid).get("cancel"):
            raise Exception("CANCELLED")
        if d.get("status") != "downloading":
            return
        now = time.time()
        if now - last["t"] < 2:
            return
        last["t"] = now
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes") or 0
        speed = d.get("speed") or 0
        eta = d.get("eta") or 0
        pct = (done / total * 100) if total else 0
        asyncio.get_event_loop().create_task(
            safe_edit(msg, progress_text(pct, speed, eta, total, done), cancel_kb())
        )
    
    opts = {
        "quiet": True,
        "outtmpl": f"{DOWNLOAD_DIR}/%(title)s.%(ext)s",
        "noplaylist": True,
        "progress_hooks": [hook],
    }
    
    if os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    
    if quality:
        if quality in ["1080", "720", "480", "360"]:
            opts["format"] = f"bestvideo[height<={quality}]+bestaudio/best"
        elif quality == "mp3":
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}]
    
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            if quality == "mp3":
                path = os.path.splitext(path)[0] + ".mp3"
            return path, info.get("title", "file")
    except Exception as e:
        if "CANCELLED" in str(e):
            raise
        return await download_direct(uid, url, msg)

async def download_direct(uid: int, url: str, msg):
    r = requests.get(url, stream=True, timeout=30)
    r.raise_for_status()
    name = safe_filename(url.split("/")[-1].split("?")[0] or "file")
    path = os.path.join(DOWNLOAD_DIR, name)
    total = int(r.headers.get("content-length", 0))
    done = 0
    start = time.time()
    last = 0
    
    with open(path, "wb") as f:
        for chunk in r.iter_content(256 * 1024):
            if session_get(uid) and session_get(uid).get("cancel"):
                raise Exception("CANCELLED")
            if chunk:
                f.write(chunk)
                done += len(chunk)
                now = time.time()
                if now - last >= 2:
                    last = now
                    speed = done / max(1, now - start)
                    eta = (total - done) / speed if speed and total else 0
                    pct = (done / total * 100) if total else 0
                    await safe_edit(msg, progress_text(pct, speed, eta, total, done), cancel_kb())
    
    return path, os.path.splitext(name)[0]

# =======================
# SCREENSHOTS
# =======================
async def make_screenshots(path: str, count: int = 5):
    screens = []
    out_dir = os.path.join(DOWNLOAD_DIR, f"ss_{int(time.time())}")
    os.makedirs(out_dir, exist_ok=True)
    
    try:
        cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{path}"'
        dur = float(subprocess.check_output(cmd, shell=True).decode().strip() or "0")
        if dur <= 0:
            return [], out_dir
        
        interval = dur / (count + 1)
        for i in range(1, count + 1):
            out = os.path.join(out_dir, f"{i}.jpg")
            subprocess.run(
                ["ffmpeg", "-ss", str(interval * i), "-i", path, "-vframes", "1", "-q:v", "2", "-y", out],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            if os.path.exists(out):
                screens.append(out)
        return screens, out_dir
    except:
        return [], out_dir

# =======================
# UPLOAD
# =======================
async def upload(uid: int, msg, path: str, name: str, as_video: bool):
    user = user_get(uid)
    thumb = user.get("thumb")
    if thumb and not os.path.exists(thumb):
        thumb = None
    
    start = time.time()
    last = {"t": 0}
    size = os.path.getsize(path)
    
    async def prog(done, total):
        if session_get(uid) and session_get(uid).get("cancel"):
            raise Exception("CANCELLED")
        now = time.time()
        if now - last["t"] < 2:
            return
        last["t"] = now
        speed = done / max(1, now - start)
        eta = (total - done) / speed if speed else 0
        pct = (done / total * 100) if total else 0
        await safe_edit(msg, progress_text(pct, speed, eta, total, done, "Uploading"), cancel_kb())
    
    if as_video:
        await app.send_video(uid, path, caption=f"🎬 `{name}`", file_name=name, supports_streaming=True, thumb=thumb, progress=prog)
        await safe_edit(msg, "📸 Generating screenshots...", None)
        screens, ss_dir = await make_screenshots(path, 5)
        if screens:
            media = [types.InputMediaPhoto(s) for s in screens]
            await app.send_media_group(uid, media)
        shutil.rmtree(ss_dir, ignore_errors=True)
    else:
        await app.send_document(uid, path, caption=f"📄 `{name}`", file_name=name, thumb=thumb, progress=prog)
    
    if uid != OWNER_ID and not user.get("is_pro"):
        user["used"] += size
        db_save()

# =======================
# BOT
# =======================
app = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start") & filters.private)
async def cmd_start(_, m):
    user_get(m.from_user.id)
    db_save()
    await m.reply_text(
        f"👋 **Hi {m.from_user.first_name}!**\n\nSend me any link to download.",
        reply_markup=menu_kb(m.from_user.id)
    )

@app.on_message(filters.text & filters.private & ~filters.command(["start"]))
async def on_text(_, m):
    uid = m.from_user.id
    user = user_get(uid)
    text = m.text.strip()
    
    if user.get("is_banned"):
        return
    
    # Custom rename input
    if user.get("state") == "awaiting_rename":
        sess = session_get(uid)
        if not sess:
            user["state"] = "none"
            db_save()
            return await m.reply_text("❌ Session expired.")
        
        new_name = safe_filename(text) + sess.get("ext", "")
        sess["name"] = new_name
        session_set(uid, sess)
        user["state"] = "none"
        db_save()
        
        return await m.reply_text(f"✅ Renamed to: `{new_name}`\n\nChoose format:", reply_markup=upload_kb())
    
    # Check URL
    if not text.startswith("http"):
        return
    
    if not await is_subscribed(uid):
        return await m.reply_text("⚠️ Join channel first!", reply_markup=join_kb())
    
    status = await m.reply_text("🔍 Analyzing...", reply_markup=cancel_kb())
    session_set(uid, {"url": text, "cancel": False})
    
    if is_youtube(text):
        return await safe_edit(status, "🎬 **YouTube detected!**\n\nChoose quality:", yt_kb())
    
    try:
        await safe_edit(status, "⬇️ Downloading...", cancel_kb())
        path, title = await download_file(uid, text, status)
        
        name = os.path.basename(path)
        ext = get_ext(name)
        size = os.path.getsize(path)
        
        session_set(uid, {"url": text, "path": path, "name": name, "ext": ext, "size": size, "cancel": False})
        
        await safe_edit(status, f"✅ **Downloaded!**\n\n📄 `{name}`\n📦 {human_size(size)}", upload_kb())
    except Exception as e:
        session_clear(uid)
        msg = "❌ Cancelled." if "CANCELLED" in str(e) else f"❌ Error: {str(e)[:80]}"
        await safe_edit(status, msg, None)

@app.on_message((filters.video | filters.document | filters.audio) & filters.private)
async def on_file(_, m):
    uid = m.from_user.id
    if not await is_subscribed(uid):
        return await m.reply_text("⚠️ Join channel first!", reply_markup=join_kb())
    
    media = m.video or m.document or m.audio
    status = await m.reply_text("⬇️ Downloading...", reply_markup=cancel_kb())
    session_set(uid, {"cancel": False})
    
    try:
        name = safe_filename(getattr(media, "file_name", None) or f"file_{int(time.time())}")
        path = os.path.join(DOWNLOAD_DIR, name)
        await m.download(path)
        
        ext = get_ext(name)
        size = os.path.getsize(path)
        session_set(uid, {"path": path, "name": name, "ext": ext, "size": size, "cancel": False})
        
        await safe_edit(status, f"✅ **Downloaded!**\n\n📄 `{name}`\n📦 {human_size(size)}", upload_kb())
    except Exception as e:
        session_clear(uid)
        await safe_edit(status, f"❌ Error: {str(e)[:80]}", None)

@app.on_message(filters.photo & filters.private)
async def on_photo(_, m):
    uid = m.from_user.id
    path = os.path.join(THUMB_DIR, f"{uid}.jpg")
    await m.download(path)
    user_get(uid)["thumb"] = path
    db_save()
    await m.reply_text("✅ Thumbnail saved!")

@app.on_callback_query()
async def on_cb(_, cb):
    uid = cb.from_user.id
    data = cb.data
    user = user_get(uid)
    sess = session_get(uid)
    
    await cb.answer()
    
    # Join check
    if data == "check_join":
        if await is_subscribed(uid):
            return await safe_edit(cb.message, "✅ Verified! Send a link.", menu_kb(uid))
        return await safe_edit(cb.message, "❌ Not joined yet!", join_kb())
    
    # Menu
    if data == "main":
        return await safe_edit(cb.message, "🏠 Main Menu", menu_kb(uid))
    
    if data == "help":
        return await safe_edit(cb.message, "**How to use:**\n\n1. Send link\n2. Choose Rename/File/Video\n3. Get file + screenshots!", menu_kb(uid))
    
    if data == "stats":
        used = user.get("used", 0)
        return await safe_edit(cb.message, f"📊 Used: {human_size(used)} / {human_size(DAILY_LIMIT)}", menu_kb(uid))
    
    if data == "thumb":
        return await safe_edit(cb.message, "🖼️ Thumbnail", thumb_kb())
    
    if data == "thumb_view":
        t = user.get("thumb")
        if t and os.path.exists(t):
            await cb.message.reply_photo(t)
        else:
            await cb.answer("No thumbnail!", show_alert=True)
        return
    
    if data == "thumb_del":
        t = user.get("thumb")
        if t and os.path.exists(t):
            os.remove(t)
        user["thumb"] = None
        db_save()
        return await safe_edit(cb.message, "✅ Deleted!", thumb_kb())
    
    # Cancel
    if data == "cancel":
        if sess:
            sess["cancel"] = True
            if sess.get("path") and os.path.exists(sess["path"]):
                try:
                    os.remove(sess["path"])
                except:
                    pass
        session_clear(uid)
        return await safe_edit(cb.message, "❌ Cancelled.", None)
    
    # YouTube
    if data.startswith("yt_"):
        q = data[3:]
        if not sess or not sess.get("url"):
            return await safe_edit(cb.message, "❌ Session expired.", None)
        
        try:
            await safe_edit(cb.message, f"⬇️ Downloading {q}...", cancel_kb())
            path, title = await download_file(uid, sess["url"], cb.message, q)
            
            name = os.path.basename(path)
            ext = get_ext(name)
            size = os.path.getsize(path)
            
            session_set(uid, {"url": sess["url"], "path": path, "name": name, "ext": ext, "size": size, "cancel": False})
            await safe_edit(cb.message, f"✅ **Downloaded!**\n\n📄 `{name}`\n📦 {human_size(size)}", upload_kb())
        except Exception as e:
            session_clear(uid)
            msg = "❌ Cancelled." if "CANCELLED" in str(e) else f"❌ Error: {str(e)[:80]}"
            await safe_edit(cb.message, msg, None)
        return
    
    # Rename
    if data == "rename":
        return await safe_edit(cb.message, "✏️ **Rename:**", rename_kb())
    
    if data == "ren_default":
        return await safe_edit(cb.message, f"📝 Using: `{sess['name']}`\n\nChoose format:", upload_kb())
    
    if data == "ren_custom":
        user["state"] = "awaiting_rename"
        db_save()
        return await safe_edit(cb.message, f"📝 Current: `{sess['name']}`\n\nSend new name (without extension):", cancel_kb())
    
    if data == "back":
        return await safe_edit(cb.message, f"📄 `{sess['name']}`\n📦 {human_size(sess.get('size', 0))}", upload_kb())
    
    # Upload
    if data in ["up_file", "up_video"]:
        if not sess or not sess.get("path") or not os.path.exists(sess["path"]):
            return await safe_edit(cb.message, "❌ File missing.", None)
        
        try:
            await safe_edit(cb.message, "📤 Uploading...", cancel_kb())
            await upload(uid, cb.message, sess["path"], sess["name"], data == "up_video")
            
            try:
                os.remove(sess["path"])
            except:
                pass
            session_clear(uid)
            
            await safe_edit(cb.message, "✅ **Done!**", menu_kb(uid))
        except Exception as e:
            msg = "❌ Cancelled." if "CANCELLED" in str(e) else f"❌ Error: {str(e)[:80]}"
            await safe_edit(cb.message, msg, None)

# =======================
# MAIN
# =======================
async def health(_):
    return web.Response(text="OK")

async def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(THUMB_DIR, exist_ok=True)
    db_load()
    
    await app.start()
    print("✅ Bot started!")
    
    srv = web.Application()
    srv.add_routes([web.get("/", health)])
    runner = web.AppRunner(srv)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()
    
    await idle()
    await app.stop()

if __name__ == "__main__":
    app.run(main())
