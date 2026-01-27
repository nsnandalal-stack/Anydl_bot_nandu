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

DAILY_LIMIT = 5 * 1024 * 1024 * 1024
COBALT_API = "https://api.cobalt.tools/api/json"

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
        DB["users"][k] = {
            "thumb": None, "state": "none", "used": 0,
            "reset": date.today().isoformat(),
            "is_pro": (uid == OWNER_ID), "is_banned": False
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
def safe_name(n: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", n.strip())[:150] or "file"

def get_ext(n: str) -> str:
    return os.path.splitext(n)[1]

def is_yt(url: str) -> bool:
    return any(x in url.lower() for x in ["youtube.com", "youtu.be"])

def human_size(n) -> str:
    if not n: return "0B"
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024: return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"

def human_time(seconds) -> str:
    if not seconds or seconds <= 0: return "..."
    seconds = int(seconds)
    if seconds < 60: return f"{seconds}s"
    elif seconds < 3600: return f"{seconds // 60}m {seconds % 60}s"
    else: return f"{seconds // 3600}h {(seconds % 3600) // 60}m"

def progress_bar(pct: float) -> str:
    filled = int(pct / 10)
    return "█" * filled + "░" * (10 - filled)

async def safe_edit(msg, text, kb=None):
    try:
        return await msg.edit_text(text, reply_markup=kb)
    except:
        return msg

async def is_subscribed(uid: int) -> bool:
    if uid == OWNER_ID: return True
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

def menu_kb(uid):
    kb = [
        [types.InlineKeyboardButton("🖼️ Thumbnail", callback_data="menu_thumb"),
         types.InlineKeyboardButton("📊 Stats", callback_data="menu_stats")],
        [types.InlineKeyboardButton("❓ Help", callback_data="menu_help")]
    ]
    if uid == OWNER_ID:
        kb.append([types.InlineKeyboardButton("⚙️ Admin", callback_data="admin")])
    kb.append([types.InlineKeyboardButton("✖️ Close", callback_data="close")])
    return types.InlineKeyboardMarkup(kb)

def thumb_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("👁️ View", callback_data="thumb_view"),
         types.InlineKeyboardButton("🗑️ Delete", callback_data="thumb_del")],
        [types.InlineKeyboardButton("🔙 Back", callback_data="back")]
    ])

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
        [types.InlineKeyboardButton("🔙 Back", callback_data="back_up")]
    ])

def yt_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("🎬 Video (720p)", callback_data="yt_720"),
         types.InlineKeyboardButton("🎵 MP3", callback_data="yt_mp3")],
        [types.InlineKeyboardButton("📹 1080p", callback_data="yt_1080"),
         types.InlineKeyboardButton("📹 480p", callback_data="yt_480"),
         types.InlineKeyboardButton("📹 360p", callback_data="yt_360")],
        [types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

def admin_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("📊 Stats", callback_data="adm_stats"),
         types.InlineKeyboardButton("📢 Broadcast", callback_data="adm_bc")],
        [types.InlineKeyboardButton("👑 Add Pro", callback_data="adm_pro"),
         types.InlineKeyboardButton("🚫 Ban", callback_data="adm_ban")],
        [types.InlineKeyboardButton("🔙 Back", callback_data="back")]
    ])

def bc_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("✅ Send", callback_data="bc_yes"),
         types.InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel")]
    ])

# =======================
# COBALT API DOWNLOAD
# =======================
async def download_cobalt(uid: int, url: str, msg, quality: str = "720"):
    """Download using Cobalt API"""
    
    await safe_edit(msg, "🔄 **Connecting to server...**", cancel_kb())
    
    payload = {
        "url": url,
        "vQuality": quality,
        "filenamePattern": "basic",
        "isAudioOnly": quality == "mp3",
        "aFormat": "mp3" if quality == "mp3" else "best"
    }
    
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    timeout = ClientTimeout(total=60)
    
    async with ClientSession(timeout=timeout) as session:
        async with session.post(COBALT_API, json=payload, headers=headers) as resp:
            if resp.status != 200:
                raise Exception(f"API error: {resp.status}")
            
            data = await resp.json()
            
            if data.get("status") == "error":
                raise Exception(data.get("text", "API failed"))
            
            if data.get("status") in ["redirect", "stream"]:
                download_url = data.get("url")
            elif data.get("status") == "picker":
                items = data.get("picker", [])
                download_url = items[0].get("url") if items else None
            else:
                raise Exception(f"Unknown: {data.get('status')}")
        
        if not download_url:
            raise Exception("No URL returned")
        
        await safe_edit(msg, "⬇️ **Downloading...**", cancel_kb())
        
        start_time = time.time()
        last_update = 0
        
        async with session.get(download_url) as resp:
            if resp.status != 200:
                raise Exception(f"Download failed: {resp.status}")
            
            cd = resp.headers.get("Content-Disposition", "")
            if "filename=" in cd:
                name = cd.split("filename=")[1].strip('"\'').split(";")[0]
                try:
                    name = name.encode('latin-1').decode('utf-8', errors='ignore')
                except:
                    pass
            else:
                ext = ".mp3" if quality == "mp3" else ".mp4"
                name = f"video_{int(time.time())}{ext}"
            
            name = safe_name(name)
            if not name.endswith(('.mp4', '.mp3', '.webm', '.mkv')):
                name += ".mp3" if quality == "mp3" else ".mp4"
            
            path = os.path.join(DOWNLOAD_DIR, name)
            total = int(resp.headers.get("Content-Length", 0))
            done = 0
            
            with open(path, "wb") as f:
                async for chunk in resp.content.iter_chunked(524288):
                    sess = session_get(uid)
                    if sess and sess.get("cancel"):
                        raise Exception("CANCELLED")
                    
                    f.write(chunk)
                    done += len(chunk)
                    
                    now = time.time()
                    if now - last_update >= 2:
                        last_update = now
                        elapsed = now - start_time
                        speed = done / elapsed if elapsed > 0 else 0
                        eta = (total - done) / speed if speed > 0 and total > 0 else 0
                        pct = (done / total * 100) if total > 0 else 0
                        
                        text = (
                            f"⬇️ **Downloading...**\n\n"
                            f"`[{progress_bar(pct)}]` {pct:.1f}%\n\n"
                            f"📦 {human_size(done)} / {human_size(total)}\n"
                            f"⚡ {human_size(speed)}/s • ⏱️ {human_time(eta)}"
                        )
                        await safe_edit(msg, text, cancel_kb())
        
        return path, os.path.splitext(name)[0]

# =======================
# YT-DLP DOWNLOAD (FALLBACK)
# =======================
async def download_ytdlp(uid: int, url: str, msg, quality: str = "720"):
    """Download using yt-dlp"""
    
    start_time = time.time()
    last = {"t": 0}
    
    def hook(d):
        sess = session_get(uid)
        if sess and sess.get("cancel"):
            raise Exception("CANCELLED")
        if d["status"] != "downloading":
            return
        now = time.time()
        if now - last["t"] < 2:
            return
        last["t"] = now
        
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes") or 0
        elapsed = now - start_time
        speed = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / speed if speed > 0 and total > 0 else 0
        pct = (done / total * 100) if total > 0 else 0
        
        text = (
            f"⬇️ **Downloading...**\n\n"
            f"`[{progress_bar(pct)}]` {pct:.1f}%\n\n"
            f"📦 {human_size(done)} / {human_size(total)}\n"
            f"⚡ {human_size(speed)}/s • ⏱️ {human_time(eta)}"
        )
        asyncio.get_event_loop().create_task(safe_edit(msg, text, cancel_kb()))
    
    opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": f"{DOWNLOAD_DIR}/%(title).70s.%(ext)s",
        "noplaylist": True,
        "progress_hooks": [hook],
        "concurrent_fragment_downloads": 4,
        "retries": 5,
        "socket_timeout": 30,
        "nocheckcertificate": True,
        "geo_bypass": True,
        "extractor_args": {"youtube": {"player_client": ["ios", "android", "web"]}},
        "http_headers": {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"}
    }
    
    if quality == "mp3":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
    elif quality == "1080":
        opts["format"] = "bestvideo[height<=1080]+bestaudio/best"
    elif quality == "720":
        opts["format"] = "bestvideo[height<=720]+bestaudio/best"
    elif quality == "480":
        opts["format"] = "bestvideo[height<=480]+bestaudio/best"
    else:
        opts["format"] = "bestvideo[height<=360]+bestaudio/best"
    
    loop = asyncio.get_event_loop()
    
    def do_dl():
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            if quality == "mp3":
                path = os.path.splitext(path)[0] + ".mp3"
            return path, info.get("title", "video")
    
    return await loop.run_in_executor(None, do_dl)

# =======================
# MAIN DOWNLOAD
# =======================
async def download_video(uid: int, url: str, msg, quality: str = "720"):
    """Try Cobalt first, then yt-dlp"""
    
    # Method 1: Cobalt
    try:
        await safe_edit(msg, "🔄 **Method 1: Cobalt API...**", cancel_kb())
        return await download_cobalt(uid, url, msg, quality)
    except Exception as e:
        if "CANCELLED" in str(e):
            raise
        print(f"Cobalt failed: {e}")
    
    # Method 2: yt-dlp
    try:
        await safe_edit(msg, "🔄 **Method 2: yt-dlp...**", cancel_kb())
        return await download_ytdlp(uid, url, msg, quality)
    except Exception as e:
        if "CANCELLED" in str(e):
            raise
        print(f"yt-dlp failed: {e}")
    
    raise Exception("Download failed. Try again later.")

# =======================
# DIRECT DOWNLOAD
# =======================
async def download_direct(uid: int, url: str, msg):
    start_time = time.time()
    timeout = ClientTimeout(total=600)
    
    async with ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status}")
            
            cd = resp.headers.get("Content-Disposition", "")
            if "filename=" in cd:
                name = cd.split("filename=")[1].strip('"\'').split(";")[0]
            else:
                name = url.split("/")[-1].split("?")[0] or "file"
            
            name = safe_name(name)
            path = os.path.join(DOWNLOAD_DIR, name)
            total = int(resp.headers.get("Content-Length", 0))
            done = 0
            last_update = 0
            
            with open(path, "wb") as f:
                async for chunk in resp.content.iter_chunked(524288):
                    sess = session_get(uid)
                    if sess and sess.get("cancel"):
                        raise Exception("CANCELLED")
                    
                    f.write(chunk)
                    done += len(chunk)
                    
                    now = time.time()
                    if now - last_update >= 2:
                        last_update = now
                        elapsed = now - start_time
                        speed = done / elapsed if elapsed > 0 else 0
                        eta = (total - done) / speed if speed > 0 and total > 0 else 0
                        pct = (done / total * 100) if total > 0 else 0
                        
                        text = (
                            f"⬇️ **Downloading...**\n\n"
                            f"`[{progress_bar(pct)}]` {pct:.1f}%\n\n"
                            f"📦 {human_size(done)} / {human_size(total)}\n"
                            f"⚡ {human_size(speed)}/s • ⏱️ {human_time(eta)}"
                        )
                        await safe_edit(msg, text, cancel_kb())
            
            return path, os.path.splitext(name)[0]

# =======================
# SCREENSHOTS
# =======================
async def make_ss(path: str, count: int = 5):
    screens = []
    out = os.path.join(DOWNLOAD_DIR, f"ss_{int(time.time())}")
    os.makedirs(out, exist_ok=True)
    try:
        cmd = f'ffprobe -v error -show_entries format=duration -of csv=p=0 "{path}"'
        proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        dur = float(stdout.decode().strip() or "0")
        if dur <= 0:
            return [], out
        interval = dur / (count + 1)
        for i in range(1, count + 1):
            o = os.path.join(out, f"{i}.jpg")
            c = f'ffmpeg -ss {interval * i} -i "{path}" -vframes 1 -q:v 5 -y "{o}" 2>/dev/null'
            p = await asyncio.create_subprocess_shell(c)
            await p.wait()
            if os.path.exists(o):
                screens.append(o)
        return screens, out
    except:
        return [], out

# =======================
# UPLOAD
# =======================
async def do_upload(uid, msg, path, name, as_video):
    user = user_get(uid)
    thumb = user.get("thumb") if user.get("thumb") and os.path.exists(user.get("thumb")) else None
    start_time = time.time()
    last = {"t": 0}
    size = os.path.getsize(path)
    
    async def prog(done, total):
        sess = session_get(uid)
        if sess and sess.get("cancel"):
            raise Exception("CANCELLED")
        now = time.time()
        if now - last["t"] < 2:
            return
        last["t"] = now
        elapsed = now - start_time
        speed = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / speed if speed > 0 and total > 0 else 0
        pct = (done / total * 100) if total > 0 else 0
        text = (
            f"📤 **Uploading...**\n\n"
            f"`[{progress_bar(pct)}]` {pct:.1f}%\n\n"
            f"📦 {human_size(done)} / {human_size(total)}\n"
            f"⚡ {human_size(speed)}/s • ⏱️ {human_time(eta)}"
        )
        await safe_edit(msg, text, cancel_kb())
    
    if as_video:
        await app.send_video(uid, path, caption=f"🎬 `{name}`", file_name=name, supports_streaming=True, thumb=thumb, progress=prog)
        await safe_edit(msg, "📸 **Generating screenshots...**", None)
        ss, ss_dir = await make_ss(path, 5)
        if ss:
            try:
                await app.send_media_group(uid, [types.InputMediaPhoto(s) for s in ss])
            except:
                pass
        shutil.rmtree(ss_dir, ignore_errors=True)
    else:
        await app.send_document(uid, path, caption=f"📄 `{name}`", file_name=name, thumb=thumb, progress=prog)
    
    if uid != OWNER_ID and not user.get("is_pro"):
        user["used"] = user.get("used", 0) + size
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
        f"👋 Hi **{m.from_user.first_name}**!\n\n"
        f"🚀 Send me any video link to download.\n\n"
        f"**Supported:** YouTube, Instagram, Twitter, TikTok & more!",
        reply_markup=menu_kb(m.from_user.id)
    )

@app.on_message(filters.text & filters.private & ~filters.command(["start"]))
async def on_text(_, m):
    uid = m.from_user.id
    user = user_get(uid)
    text = m.text.strip()
    
    if user.get("is_banned"):
        return
    
    # Rename state
    if user.get("state") == "rename":
        sess = session_get(uid)
        if not sess:
            user["state"] = "none"
            db_save()
            return
        new = safe_name(text) + sess.get("ext", "")
        sess["name"] = new
        session_set(uid, sess)
        user["state"] = "none"
        db_save()
        return await m.reply_text(f"✅ Renamed: `{new}`", reply_markup=upload_kb())
    
    # Broadcast state
    if user.get("state") == "broadcast" and uid == OWNER_ID:
        user["state"] = "none"
        user["bc"] = text
        db_save()
        count = len([u for u in DB["users"] if not DB["users"][u].get("is_banned")])
        return await m.reply_text(f"📢 **Preview:**\n\n{text}\n\n👥 Will send to {count} users", reply_markup=bc_kb())
    
    # Add Pro state
    if user.get("state") == "addpro" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            user_get(int(text))["is_pro"] = True
            db_save()
            return await m.reply_text(f"✅ User `{text}` is now PRO!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid user ID!", reply_markup=admin_kb())
    
    # Ban state
    if user.get("state") == "ban" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            user_get(int(text))["is_banned"] = True
            db_save()
            return await m.reply_text(f"✅ User `{text}` banned!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid user ID!", reply_markup=admin_kb())
    
    # Check if URL
    if not text.startswith("http"):
        return
    
    # Check subscription
    if not await is_subscribed(uid):
        return await m.reply_text("⚠️ **Join our channel first!**", reply_markup=join_kb())
    
    status = await m.reply_text("🔍 **Analyzing link...**", reply_markup=cancel_kb())
    session_set(uid, {"url": text, "cancel": False})
    
    # YouTube
    if is_yt(text):
        return await safe_edit(status, "🎬 **YouTube detected!**\n\nChoose quality:", yt_kb())
    
    # Other URLs
    try:
        await safe_edit(status, "⬇️ **Downloading...**", cancel_kb())
        try:
            path, title = await download_video(uid, text, status, "720")
        except:
            path, title = await download_direct(uid, text, status)
        
        name = os.path.basename(path)
        size = os.path.getsize(path)
        session_set(uid, {"url": text, "path": path, "name": name, "ext": get_ext(name), "size": size, "cancel": False})
        await safe_edit(status, f"✅ **Download Complete!**\n\n📄 `{name}`\n📦 {human_size(size)}", upload_kb())
    except Exception as e:
        session_clear(uid)
        msg = "❌ **Cancelled!**" if "CANCELLED" in str(e) else f"❌ **Error:** {str(e)[:100]}"
        await safe_edit(status, msg, None)

@app.on_message((filters.video | filters.document | filters.audio) & filters.private)
async def on_file(_, m):
    uid = m.from_user.id
    if user_get(uid).get("is_banned"):
        return
    if not await is_subscribed(uid):
        return await m.reply_text("⚠️ Join channel first!", reply_markup=join_kb())
    
    media = m.video or m.document or m.audio
    status = await m.reply_text("⬇️ **Downloading...**", reply_markup=cancel_kb())
    session_set(uid, {"cancel": False})
    
    try:
        name = safe_name(getattr(media, "file_name", None) or f"file_{int(time.time())}")
        path = os.path.join(DOWNLOAD_DIR, name)
        await m.download(path)
        size = os.path.getsize(path)
        session_set(uid, {"path": path, "name": name, "ext": get_ext(name), "size": size, "cancel": False})
        await safe_edit(status, f"✅ **Download Complete!**\n\n📄 `{name}`\n📦 {human_size(size)}", upload_kb())
    except Exception as e:
        session_clear(uid)
        await safe_edit(status, f"❌ **Error:** {str(e)[:80]}", None)

@app.on_message(filters.photo & filters.private)
async def on_photo(_, m):
    uid = m.from_user.id
    if user_get(uid).get("is_banned"):
        return
    path = os.path.join(THUMB_DIR, f"{uid}.jpg")
    await m.download(path)
    user_get(uid)["thumb"] = path
    db_save()
    await m.reply_text("✅ **Thumbnail saved!**\n\nThis will be used for all your uploads.")

# =======================
# CALLBACK HANDLER
# =======================
@app.on_callback_query()
async def on_cb(_, cb):
    uid = cb.from_user.id
    data = cb.data
    user = user_get(uid)
    sess = session_get(uid)
    
    await cb.answer()
    
    if user.get("is_banned"):
        return await cb.answer("❌ You are banned!", show_alert=True)
    
    # ===== CLOSE BUTTON =====
    if data == "close":
        try:
            await cb.message.delete()
        except:
            pass
        return
    
    # ===== JOIN CHECK =====
    if data == "check_join":
        if await is_subscribed(uid):
            return await safe_edit(cb.message, "✅ **Verified!** Now send me a link.", menu_kb(uid))
        return await cb.answer("❌ You haven't joined yet!", show_alert=True)
    
    # ===== CANCEL =====
    if data == "cancel":
        if sess:
            sess["cancel"] = True
            session_set(uid, sess)
            # Clean up file if exists
            if sess.get("path") and os.path.exists(sess["path"]):
                try:
                    os.remove(sess["path"])
                except:
                    pass
        session_clear(uid)
        user["state"] = "none"
        db_save()
        return await safe_edit(cb.message, "❌ **Cancelled!**", None)
    
    # ===== BACK TO MENU =====
    if data == "back":
        user["state"] = "none"
        db_save()
        return await safe_edit(cb.message, "📋 **Main Menu**", menu_kb(uid))
    
    # ===== MENU OPTIONS =====
    if data == "menu_thumb":
        return await safe_edit(cb.message, "🖼️ **Thumbnail**\n\nSend any photo to set as thumbnail.", thumb_kb())
    
    if data == "menu_stats":
        used = user.get("used", 0)
        remaining = max(0, DAILY_LIMIT - used)
        return await safe_edit(
            cb.message,
            f"📊 **Your Stats**\n\n"
            f"📦 Used today: {human_size(used)}\n"
            f"📉 Remaining: {human_size(remaining)}\n"
            f"👑 Pro: {'Yes ✅' if user.get('is_pro') else 'No'}",
            menu_kb(uid)
        )
    
    if data == "menu_help":
        return await safe_edit(
            cb.message,
            "❓ **How to use:**\n\n"
            "1️⃣ Send any video link\n"
            "2️⃣ Choose quality (for YouTube)\n"
            "3️⃣ Wait for download\n"
            "4️⃣ Choose Rename / File / Video\n"
            "5️⃣ Get your file + screenshots!\n\n"
            "💡 **Tip:** Send a photo to set custom thumbnail.",
            menu_kb(uid)
        )
    
    # ===== THUMBNAIL =====
    if data == "thumb_view":
        t = user.get("thumb")
        if t and os.path.exists(t):
            await cb.message.reply_photo(t, caption="🖼️ Your thumbnail")
        else:
            await cb.answer("No thumbnail set!", show_alert=True)
        return
    
    if data == "thumb_del":
        t = user.get("thumb")
        if t and os.path.exists(t):
            os.remove(t)
        user["thumb"] = None
        db_save()
        return await safe_edit(cb.message, "✅ **Thumbnail deleted!**", thumb_kb())
    
    # ===== ADMIN =====
    if data == "admin":
        if uid != OWNER_ID:
            return await cb.answer("❌ Not authorized!", show_alert=True)
        return await safe_edit(cb.message, "⚙️ **Admin Panel**", admin_kb())
    
    if data == "adm_stats":
        if uid != OWNER_ID:
            return
        total_users = len(DB["users"])
        pro_users = len([u for u in DB["users"].values() if u.get("is_pro")])
        banned = len([u for u in DB["users"].values() if u.get("is_banned")])
        return await safe_edit(
            cb.message,
            f"📊 **Bot Stats**\n\n"
            f"👥 Total users: {total_users}\n"
            f"👑 Pro users: {pro_users}\n"
            f"🚫 Banned: {banned}",
            admin_kb()
        )
    
    if data == "adm_bc":
        if uid != OWNER_ID:
            return
        user["state"] = "broadcast"
        db_save()
        return await safe_edit(cb.message, "📢 **Broadcast**\n\nSend me the message:", cancel_kb())
    
    if data == "bc_yes":
        if uid != OWNER_ID:
            return
        text = user.get("bc", "")
        if not text:
            return await cb.answer("No message!", show_alert=True)
        
        await safe_edit(cb.message, "📢 **Sending broadcast...**", None)
        sent = 0
        for u in DB["users"]:
            if DB["users"][u].get("is_banned"):
                continue
            try:
                await app.send_message(int(u), text)
                sent += 1
                await asyncio.sleep(0.05)
            except:
                pass
        user["bc"] = ""
        db_save()
        return await safe_edit(cb.message, f"✅ **Sent to {sent} users!**", admin_kb())
    
    if data == "bc_cancel":
        user["state"] = "none"
        user["bc"] = ""
        db_save()
        return await safe_edit(cb.message, "❌ **Cancelled!**", admin_kb())
    
    if data == "adm_pro":
        if uid != OWNER_ID:
            return
        user["state"] = "addpro"
        db_save()
        return await safe_edit(cb.message, "👑 **Add Pro**\n\nSend user ID:", cancel_kb())
    
    if data == "adm_ban":
        if uid != OWNER_ID:
            return
        user["state"] = "ban"
        db_save()
        return await safe_edit(cb.message, "🚫 **Ban User**\n\nSend user ID:", cancel_kb())
    
    # ===== YOUTUBE =====
    if data.startswith("yt_"):
        if not sess or not sess.get("url"):
            return await safe_edit(cb.message, "❌ **Session expired!** Send link again.", None)
        
        quality = data.replace("yt_", "")
        quality_names = {"1080": "1080p", "720": "720p", "480": "480p", "360": "360p", "mp3": "MP3"}
        
        try:
            await safe_edit(cb.message, f"⬇️ **Downloading {quality_names.get(quality, quality)}...**", cancel_kb())
            path, title = await download_video(uid, sess["url"], cb.message, quality)
            name = os.path.basename(path)
            size = os.path.getsize(path)
            session_set(uid, {"url": sess["url"], "path": path, "name": name, "ext": get_ext(name), "size": size, "cancel": False})
            await safe_edit(cb.message, f"✅ **Download Complete!**\n\n📄 `{name}`\n📦 {human_size(size)}", upload_kb())
        except Exception as e:
            session_clear(uid)
            msg = "❌ **Cancelled!**" if "CANCELLED" in str(e) else f"❌ **Error:** {str(e)[:100]}"
            await safe_edit(cb.message, msg, None)
        return
    
    # ===== RENAME =====
    if data == "rename":
        if not sess:
            return await safe_edit(cb.message, "❌ **Session expired!**", None)
        return await safe_edit(cb.message, f"✏️ **Rename**\n\nCurrent: `{sess['name']}`", rename_kb())
    
    if data == "ren_def":
        if not sess:
            return
        return await safe_edit(cb.message, f"📝 Using: `{sess['name']}`", upload_kb())
    
    if data == "ren_cust":
        if not sess:
            return
        user["state"] = "rename"
        db_save()
        return await safe_edit(cb.message, f"✏️ **Enter new name**\n\nExtension `{sess.get('ext', '')}` will be added automatically.", cancel_kb())
    
    if data == "back_up":
        if not sess:
            return await safe_edit(cb.message, "❌ **Session expired!**", None)
        return await safe_edit(cb.message, f"📄 `{sess['name']}`\n📦 {human_size(sess.get('size', 0))}", upload_kb())
    
    # ===== UPLOAD =====
    if data in ["up_file", "up_video"]:
        if not sess or not sess.get("path") or not os.path.exists(sess["path"]):
            session_clear(uid)
            return await safe_edit(cb.message, "❌ **File not found!** Send link again.", None)
        
        try:
            await safe_edit(cb.message, "📤 **Uploading...**", cancel_kb())
            await do_upload(uid, cb.message, sess["path"], sess["name"], data == "up_video")
            
            try:
                os.remove(sess["path"])
            except:
                pass
            session_clear(uid)
            await safe_edit(cb.message, "✅ **Upload Complete!**", menu_kb(uid))
        except Exception as e:
            msg = "❌ **Cancelled!**" if "CANCELLED" in str(e) else f"❌ **Error:** {str(e)[:80]}"
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
