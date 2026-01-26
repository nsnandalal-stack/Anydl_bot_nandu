import os
import re
import time
import json
import shutil
import asyncio
import subprocess
from datetime import date
from aiohttp import web, ClientSession, ClientTimeout

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
    try:
        with open(DB_FILE, "w") as f:
            json.dump(DB, f)
    except:
        pass

def user_get(uid: int) -> dict:
    k = str(uid)
    if k not in DB["users"]:
        DB["users"][k] = {"thumb": None, "state": "none", "is_banned": False}
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
def safe_name(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name.strip())[:150] or "file"

def get_ext(name: str) -> str:
    return os.path.splitext(name)[1]

def is_yt(url: str) -> bool:
    return any(x in url.lower() for x in ["youtube.com", "youtu.be"])

def human_size(n) -> str:
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"

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
        [types.InlineKeyboardButton("📢 Join", url=INVITE_LINK)],
        [types.InlineKeyboardButton("✅ Done", callback_data="check")]
    ])

def cancel_kb():
    return types.InlineKeyboardMarkup([[types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])

def upload_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("✏️ Rename", callback_data="rename"),
         types.InlineKeyboardButton("📄 File", callback_data="up_file"),
         types.InlineKeyboardButton("🎬 Video", callback_data="up_video")],
        [types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

def rename_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("📝 Default", callback_data="ren_def"),
         types.InlineKeyboardButton("✏️ Custom", callback_data="ren_cust")],
        [types.InlineKeyboardButton("🔙 Back", callback_data="back")]
    ])

def yt_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("🎬 Video", callback_data="yt_vid"),
         types.InlineKeyboardButton("🎵 Audio", callback_data="yt_aud")],
        [types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

# =======================
# FAST YOUTUBE DOWNLOAD
# =======================
async def download_yt(uid: int, url: str, msg, audio_only: bool = False):
    """Optimized YouTube download"""
    
    last_update = {"t": 0, "text": ""}
    
    def progress_hook(d):
        # Check cancel
        sess = session_get(uid)
        if sess and sess.get("cancel"):
            raise Exception("CANCELLED")
        
        if d["status"] != "downloading":
            return
        
        # Update every 3 seconds only
        now = time.time()
        if now - last_update["t"] < 3:
            return
        
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes") or 0
        speed = d.get("speed") or 0
        
        if total > 0:
            pct = done / total * 100
            text = f"⬇️ **Downloading...**\n\n📦 {human_size(done)}/{human_size(total)} ({pct:.0f}%)\n⚡ {human_size(speed)}/s"
            
            if text != last_update["text"]:
                last_update["t"] = now
                last_update["text"] = text
                asyncio.get_event_loop().create_task(safe_edit(msg, text, cancel_kb()))
    
    # OPTIMIZED OPTIONS FOR SPEED
    opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": f"{DOWNLOAD_DIR}/%(title).100s.%(ext)s",
        "noplaylist": True,
        "progress_hooks": [progress_hook],
        
        # SPEED OPTIMIZATIONS
        "concurrent_fragment_downloads": 8,
        "buffersize": 1024 * 64,
        "http_chunk_size": 10485760,  # 10MB chunks
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        
        # Use pre-merged formats (FASTER - no FFmpeg merge needed)
        "format": "b" if not audio_only else "ba",
        
        # YouTube specific
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
                "skip": ["dash", "hls"]  # Prefer direct downloads
            }
        }
    }
    
    # Audio conversion
    if audio_only:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192"
        }]
    
    # Cookies if available
    if os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    
    # Run in thread to not block
    loop = asyncio.get_event_loop()
    
    def do_download():
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            if audio_only:
                path = os.path.splitext(path)[0] + ".mp3"
            return path, info.get("title", "video")
    
    path, title = await loop.run_in_executor(None, do_download)
    return path, title

# =======================
# FAST DIRECT DOWNLOAD
# =======================
async def download_direct(uid: int, url: str, msg):
    """Fast async download for direct links"""
    
    timeout = ClientTimeout(total=600, connect=30)
    
    async with ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status}")
            
            # Get filename
            cd = resp.headers.get("Content-Disposition", "")
            if "filename=" in cd:
                name = cd.split("filename=")[1].strip('"\'').split(";")[0]
            else:
                name = url.split("/")[-1].split("?")[0] or "file"
            
            name = safe_name(name)
            path = os.path.join(DOWNLOAD_DIR, name)
            total = int(resp.headers.get("Content-Length", 0))
            done = 0
            start = time.time()
            last_update = 0
            
            with open(path, "wb") as f:
                async for chunk in resp.content.iter_chunked(512 * 1024):  # 512KB chunks
                    # Check cancel
                    sess = session_get(uid)
                    if sess and sess.get("cancel"):
                        raise Exception("CANCELLED")
                    
                    f.write(chunk)
                    done += len(chunk)
                    
                    # Update every 3 seconds
                    now = time.time()
                    if now - last_update >= 3:
                        last_update = now
                        speed = done / max(1, now - start)
                        if total > 0:
                            pct = done / total * 100
                            text = f"⬇️ **Downloading...**\n\n📦 {human_size(done)}/{human_size(total)} ({pct:.0f}%)\n⚡ {human_size(speed)}/s"
                        else:
                            text = f"⬇️ **Downloading...**\n\n📦 {human_size(done)}\n⚡ {human_size(speed)}/s"
                        await safe_edit(msg, text, cancel_kb())
            
            return path, os.path.splitext(name)[0]

# =======================
# SCREENSHOTS (FAST)
# =======================
async def make_screenshots(path: str, count: int = 5):
    """Generate screenshots quickly"""
    screens = []
    out_dir = os.path.join(DOWNLOAD_DIR, f"ss_{int(time.time())}")
    os.makedirs(out_dir, exist_ok=True)
    
    try:
        # Get duration
        cmd = f'ffprobe -v error -show_entries format=duration -of csv=p=0 "{path}"'
        result = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await result.communicate()
        dur = float(stdout.decode().strip() or "0")
        
        if dur <= 0:
            return [], out_dir
        
        # Generate all screenshots in parallel
        interval = dur / (count + 1)
        tasks = []
        
        for i in range(1, count + 1):
            t = interval * i
            out = os.path.join(out_dir, f"{i}.jpg")
            cmd = f'ffmpeg -ss {t} -i "{path}" -vframes 1 -q:v 5 -y "{out}"'
            tasks.append(asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL))
        
        await asyncio.gather(*[t for t in tasks])
        
        # Collect results
        for i in range(1, count + 1):
            out = os.path.join(out_dir, f"{i}.jpg")
            if os.path.exists(out):
                screens.append(out)
        
        return screens, out_dir
    except:
        return [], out_dir

# =======================
# UPLOAD
# =======================
async def do_upload(uid: int, msg, path: str, name: str, as_video: bool):
    """Upload file"""
    user = user_get(uid)
    thumb = user.get("thumb") if user.get("thumb") and os.path.exists(user.get("thumb")) else None
    
    start = time.time()
    last = {"t": 0}
    
    async def prog(done, total):
        sess = session_get(uid)
        if sess and sess.get("cancel"):
            raise Exception("CANCELLED")
        
        now = time.time()
        if now - last["t"] < 3:
            return
        last["t"] = now
        
        speed = done / max(1, now - start)
        pct = (done / total * 100) if total else 0
        await safe_edit(msg, f"📤 **Uploading...**\n\n📦 {human_size(done)}/{human_size(total)} ({pct:.0f}%)\n⚡ {human_size(speed)}/s", cancel_kb())
    
    if as_video:
        await app.send_video(uid, path, caption=f"🎬 `{name}`", file_name=name, supports_streaming=True, thumb=thumb, progress=prog)
        
        # Screenshots
        await safe_edit(msg, "📸 **Screenshots...**", None)
        screens, ss_dir = await make_screenshots(path, 5)
        if screens:
            media = [types.InputMediaPhoto(s) for s in screens]
            try:
                await app.send_media_group(uid, media)
            except:
                pass
        shutil.rmtree(ss_dir, ignore_errors=True)
    else:
        await app.send_document(uid, path, caption=f"📄 `{name}`", file_name=name, thumb=thumb, progress=prog)

# =======================
# BOT
# =======================
app = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start") & filters.private)
async def cmd_start(_, m):
    user_get(m.from_user.id)
    db_save()
    await m.reply_text(f"👋 **Hi {m.from_user.first_name}!**\n\nSend me any link to download fast! 🚀")

@app.on_message(filters.text & filters.private & ~filters.command(["start"]))
async def on_text(_, m):
    uid = m.from_user.id
    user = user_get(uid)
    text = m.text.strip()
    
    if user.get("is_banned"):
        return
    
    # Custom rename
    if user.get("state") == "rename":
        sess = session_get(uid)
        if not sess:
            user["state"] = "none"
            return await m.reply_text("❌ Expired.")
        
        new_name = safe_name(text) + sess.get("ext", "")
        sess["name"] = new_name
        session_set(uid, sess)
        user["state"] = "none"
        db_save()
        return await m.reply_text(f"✅ Renamed: `{new_name}`", reply_markup=upload_kb())
    
    # Check URL
    if not text.startswith("http"):
        return
    
    if not await is_subscribed(uid):
        return await m.reply_text("⚠️ Join first!", reply_markup=join_kb())
    
    status = await m.reply_text("🔍 **Analyzing...**", reply_markup=cancel_kb())
    session_set(uid, {"url": text, "cancel": False})
    
    # YouTube
    if is_yt(text):
        return await safe_edit(status, "🎬 **YouTube detected!**\n\nChoose format:", yt_kb())
    
    # Direct download
    try:
        await safe_edit(status, "⬇️ **Starting download...**", cancel_kb())
        
        # Try yt-dlp first (handles many sites)
        try:
            path, title = await download_yt(uid, text, status, False)
        except:
            # Fallback to direct download
            path, title = await download_direct(uid, text, status)
        
        if not os.path.exists(path):
            raise Exception("Download failed")
        
        name = os.path.basename(path)
        size = os.path.getsize(path)
        
        session_set(uid, {"url": text, "path": path, "name": name, "ext": get_ext(name), "size": size, "cancel": False})
        await safe_edit(status, f"✅ **Done!**\n\n📄 `{name}`\n📦 {human_size(size)}", upload_kb())
        
    except Exception as e:
        session_clear(uid)
        msg = "❌ Cancelled." if "CANCELLED" in str(e) else f"❌ Error: {str(e)[:100]}"
        await safe_edit(status, msg, None)

@app.on_message((filters.video | filters.document | filters.audio) & filters.private)
async def on_file(_, m):
    uid = m.from_user.id
    if not await is_subscribed(uid):
        return await m.reply_text("⚠️ Join first!", reply_markup=join_kb())
    
    media = m.video or m.document or m.audio
    status = await m.reply_text("⬇️ **Downloading...**", reply_markup=cancel_kb())
    session_set(uid, {"cancel": False})
    
    try:
        name = safe_name(getattr(media, "file_name", None) or f"file_{int(time.time())}")
        path = os.path.join(DOWNLOAD_DIR, name)
        await m.download(path)
        
        size = os.path.getsize(path)
        session_set(uid, {"path": path, "name": name, "ext": get_ext(name), "size": size, "cancel": False})
        await safe_edit(status, f"✅ **Done!**\n\n📄 `{name}`\n📦 {human_size(size)}", upload_kb())
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
    if data == "check":
        if await is_subscribed(uid):
            return await safe_edit(cb.message, "✅ Done! Send a link.", None)
        return await safe_edit(cb.message, "❌ Not joined!", join_kb())
    
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
    
    # YouTube video
    if data == "yt_vid":
        if not sess or not sess.get("url"):
            return await safe_edit(cb.message, "❌ Expired.", None)
        try:
            await safe_edit(cb.message, "⬇️ **Downloading video...**", cancel_kb())
            path, title = await download_yt(uid, sess["url"], cb.message, False)
            
            name = os.path.basename(path)
            size = os.path.getsize(path)
            session_set(uid, {"url": sess["url"], "path": path, "name": name, "ext": get_ext(name), "size": size, "cancel": False})
            await safe_edit(cb.message, f"✅ **Done!**\n\n📄 `{name}`\n📦 {human_size(size)}", upload_kb())
        except Exception as e:
            session_clear(uid)
            msg = "❌ Cancelled." if "CANCELLED" in str(e) else f"❌ Error: {str(e)[:80]}"
            await safe_edit(cb.message, msg, None)
        return
    
    # YouTube audio
    if data == "yt_aud":
        if not sess or not sess.get("url"):
            return await safe_edit(cb.message, "❌ Expired.", None)
        try:
            await safe_edit(cb.message, "⬇️ **Downloading audio...**", cancel_kb())
            path, title = await download_yt(uid, sess["url"], cb.message, True)
            
            name = os.path.basename(path)
            size = os.path.getsize(path)
            session_set(uid, {"url": sess["url"], "path": path, "name": name, "ext": get_ext(name), "size": size, "cancel": False})
            await safe_edit(cb.message, f"✅ **Done!**\n\n📄 `{name}`\n📦 {human_size(size)}", upload_kb())
        except Exception as e:
            session_clear(uid)
            msg = "❌ Cancelled." if "CANCELLED" in str(e) else f"❌ Error: {str(e)[:80]}"
            await safe_edit(cb.message, msg, None)
        return
    
    # Rename
    if data == "rename":
        return await safe_edit(cb.message, "✏️ **Rename:**", rename_kb())
    
    if data == "ren_def":
        if not sess:
            return await safe_edit(cb.message, "❌ Expired.", None)
        return await safe_edit(cb.message, f"📝 Using: `{sess['name']}`", upload_kb())
    
    if data == "ren_cust":
        if not sess:
            return await safe_edit(cb.message, "❌ Expired.", None)
        user["state"] = "rename"
        db_save()
        return await safe_edit(cb.message, f"📝 Current: `{sess['name']}`\n\nSend new name:", cancel_kb())
    
    if data == "back":
        if not sess:
            return await safe_edit(cb.message, "❌ Expired.", None)
        return await safe_edit(cb.message, f"📄 `{sess['name']}`\n📦 {human_size(sess.get('size', 0))}", upload_kb())
    
    # Upload
    if data in ["up_file", "up_video"]:
        if not sess or not sess.get("path") or not os.path.exists(sess["path"]):
            return await safe_edit(cb.message, "❌ File missing.", None)
        
        try:
            await safe_edit(cb.message, "📤 **Uploading...**", cancel_kb())
            await do_upload(uid, cb.message, sess["path"], sess["name"], data == "up_video")
            
            try:
                os.remove(sess["path"])
            except:
                pass
            session_clear(uid)
            await safe_edit(cb.message, "✅ **Complete!**", None)
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
