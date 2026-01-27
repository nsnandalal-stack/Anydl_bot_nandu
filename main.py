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

# =======================
# COOKIES
# =======================
COOKIES_PATH = None

def find_cookies():
    global COOKIES_PATH
    paths = ["/app/cookies/cookies.txt", "/app/cookies.txt", "cookies/cookies.txt", "cookies.txt"]
    for p in paths:
        if os.path.exists(p):
            COOKIES_PATH = p
            print(f"✅ Cookies: {p}")
            return p
    print("⚠️ No cookies found")
    return None

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
    if not n:
        return "0B"
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"

def human_time(seconds) -> str:
    """Convert seconds to human readable time"""
    if not seconds or seconds <= 0:
        return "calculating..."
    
    seconds = int(seconds)
    
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        mins = seconds // 60
        secs = seconds % 60
        return f"{mins}m {secs}s"
    else:
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        return f"{hours}h {mins}m"

def progress_bar(percent: float) -> str:
    """Create a visual progress bar"""
    filled = int(percent / 10)
    empty = 10 - filled
    return "█" * filled + "░" * empty

def format_progress(done: int, total: int, speed: float, eta: float, action: str = "Downloading") -> str:
    """Format progress message with ETA"""
    if total > 0:
        pct = done / total * 100
        bar = progress_bar(pct)
        
        return (
            f"{'⬇️' if 'Down' in action else '📤'} **{action}...**\n\n"
            f"`[{bar}]` {pct:.1f}%\n\n"
            f"📦 **Size:** {human_size(done)} / {human_size(total)}\n"
            f"⚡ **Speed:** {human_size(speed)}/s\n"
            f"⏱️ **ETA:** {human_time(eta)}"
        )
    else:
        return (
            f"{'⬇️' if 'Down' in action else '📤'} **{action}...**\n\n"
            f"📦 **Downloaded:** {human_size(done)}\n"
            f"⚡ **Speed:** {human_size(speed)}/s"
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
        [types.InlineKeyboardButton("📢 Join", url=INVITE_LINK)],
        [types.InlineKeyboardButton("✅ Done", callback_data="check_join")]
    ])

def cancel_kb():
    return types.InlineKeyboardMarkup([[types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])

def menu_kb(uid):
    kb = [
        [types.InlineKeyboardButton("🖼️ Thumb", callback_data="menu_thumb"),
         types.InlineKeyboardButton("📊 Stats", callback_data="menu_stats")],
        [types.InlineKeyboardButton("❓ Help", callback_data="menu_help")]
    ]
    if uid == OWNER_ID:
        kb.append([types.InlineKeyboardButton("⚙️ Admin", callback_data="admin")])
    return types.InlineKeyboardMarkup(kb)

def thumb_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("👁️ View", callback_data="thumb_view"),
         types.InlineKeyboardButton("🗑️ Del", callback_data="thumb_del")],
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
        [types.InlineKeyboardButton("🎬 Best Video", callback_data="yt_best"),
         types.InlineKeyboardButton("🎵 MP3", callback_data="yt_mp3")],
        [types.InlineKeyboardButton("📹 720p", callback_data="yt_720"),
         types.InlineKeyboardButton("📹 480p", callback_data="yt_480"),
         types.InlineKeyboardButton("📹 360p", callback_data="yt_360")],
        [types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

def admin_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("📊 Stats", callback_data="adm_stats"),
         types.InlineKeyboardButton("📢 Broadcast", callback_data="adm_bc")],
        [types.InlineKeyboardButton("👑 Pro", callback_data="adm_pro"),
         types.InlineKeyboardButton("🚫 Ban", callback_data="adm_ban")],
        [types.InlineKeyboardButton("🔙 Back", callback_data="back")]
    ])

def bc_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("✅ Send", callback_data="bc_yes"),
         types.InlineKeyboardButton("❌ No", callback_data="bc_no")]
    ])

# =======================
# YOUTUBE DOWNLOAD WITH ETA
# =======================
async def download_yt(uid: int, url: str, msg, fmt: str = "best"):
    """Download YouTube with ETA display"""
    
    start_time = time.time()
    last = {"t": 0, "done": 0}
    
    def hook(d):
        sess = session_get(uid)
        if sess and sess.get("cancel"):
            raise Exception("CANCELLED")
        
        if d["status"] != "downloading":
            return
        
        now = time.time()
        if now - last["t"] < 2:  # Update every 2 seconds
            return
        
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes") or 0
        
        # Calculate speed (bytes per second)
        elapsed = now - start_time
        speed = done / elapsed if elapsed > 0 else 0
        
        # Calculate ETA
        if speed > 0 and total > 0:
            remaining_bytes = total - done
            eta = remaining_bytes / speed
        else:
            eta = 0
        
        last["t"] = now
        last["done"] = done
        
        text = format_progress(done, total, speed, eta, "Downloading")
        asyncio.get_event_loop().create_task(safe_edit(msg, text, cancel_kb()))
    
    # YT-DLP OPTIONS
    opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": f"{DOWNLOAD_DIR}/%(title).70s.%(ext)s",
        "noplaylist": True,
        "progress_hooks": [hook],
        "concurrent_fragment_downloads": 4,
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 30,
        "nocheckcertificate": True,
        "geo_bypass": True,
        "geo_bypass_country": "US",
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "android"],
                "player_skip": ["webpage", "configs"],
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-us,en;q=0.5",
        }
    }
    
    if COOKIES_PATH and os.path.exists(COOKIES_PATH):
        opts["cookiefile"] = COOKIES_PATH
    
    # Format
    if fmt == "mp3":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
    elif fmt == "720":
        opts["format"] = "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
    elif fmt == "480":
        opts["format"] = "bestvideo[height<=480]+bestaudio/best[height<=480]/best"
    elif fmt == "360":
        opts["format"] = "bestvideo[height<=360]+bestaudio/best[height<=360]/best"
    else:
        opts["format"] = "b/bv*+ba/best"
    
    loop = asyncio.get_event_loop()
    
    async def try_download(options, client_name):
        def do_dl():
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
                path = ydl.prepare_filename(info)
                if fmt == "mp3":
                    path = os.path.splitext(path)[0] + ".mp3"
                return path, info.get("title", "video")
        return await loop.run_in_executor(None, do_dl)
    
    # Try different clients
    clients = [
        (["ios"], "iOS"),
        (["android"], "Android"),
        (["web_embedded"], "Web"),
        (["mweb"], "Mobile"),
        (["tv_embedded"], "TV")
    ]
    
    for client, name in clients:
        try:
            await safe_edit(msg, f"🔄 **Trying {name} client...**", cancel_kb())
            opts["extractor_args"]["youtube"]["player_client"] = client
            return await try_download(opts, name)
        except Exception as e:
            if "CANCELLED" in str(e):
                raise
            print(f"❌ {name} failed: {e}")
            continue
    
    raise Exception("All methods failed. Update cookies or try later.")

# =======================
# DIRECT DOWNLOAD WITH ETA
# =======================
async def download_direct(uid: int, url: str, msg):
    """Direct download with ETA display"""
    
    start_time = time.time()
    timeout = ClientTimeout(total=600)
    
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
            last_update = 0
            
            with open(path, "wb") as f:
                async for chunk in resp.content.iter_chunked(524288):
                    sess = session_get(uid)
                    if sess and sess.get("cancel"):
                        raise Exception("CANCELLED")
                    
                    f.write(chunk)
                    done += len(chunk)
                    
                    now = time.time()
                    if now - last_update >= 2:  # Update every 2 seconds
                        last_update = now
                        
                        # Calculate speed and ETA
                        elapsed = now - start_time
                        speed = done / elapsed if elapsed > 0 else 0
                        
                        if speed > 0 and total > 0:
                            eta = (total - done) / speed
                        else:
                            eta = 0
                        
                        text = format_progress(done, total, speed, eta, "Downloading")
                        await safe_edit(msg, text, cancel_kb())
            
            return path, os.path.splitext(name)[0]

# =======================
# SCREENSHOTS WITH ETA
# =======================
async def make_ss(path: str, count: int = 5):
    """Generate screenshots"""
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
# UPLOAD WITH ETA
# =======================
async def do_upload(uid, msg, path, name, as_video):
    """Upload with ETA display"""
    
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
        if now - last["t"] < 2:  # Update every 2 seconds
            return
        last["t"] = now
        
        # Calculate speed and ETA
        elapsed = now - start_time
        speed = done / elapsed if elapsed > 0 else 0
        
        if speed > 0 and total > 0:
            eta = (total - done) / speed
        else:
            eta = 0
        
        text = format_progress(done, total, speed, eta, "Uploading")
        await safe_edit(msg, text, cancel_kb())
    
    if as_video:
        await app.send_video(
            uid, path,
            caption=f"🎬 `{name}`",
            file_name=name,
            supports_streaming=True,
            thumb=thumb,
            progress=prog
        )
        
        # Screenshots
        await safe_edit(msg, "📸 **Generating 5 screenshots...**\n⏱️ ETA: ~10-15 seconds", None)
        ss, ss_dir = await make_ss(path, 5)
        if ss:
            try:
                await app.send_media_group(uid, [types.InputMediaPhoto(s) for s in ss])
            except:
                pass
        shutil.rmtree(ss_dir, ignore_errors=True)
    else:
        await app.send_document(
            uid, path,
            caption=f"📄 `{name}`",
            file_name=name,
            thumb=thumb,
            progress=prog
        )
    
    # Update usage
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
        f"🚀 Send any video link to download.\n\n"
        f"**Supported:**\n"
        f"• YouTube, Instagram, Twitter\n"
        f"• TikTok, Facebook, Reddit\n"
        f"• Any direct URL",
        reply_markup=menu_kb(m.from_user.id)
    )

@app.on_message(filters.text & filters.private & ~filters.command(["start"]))
async def on_text(_, m):
    uid = m.from_user.id
    user = user_get(uid)
    text = m.text.strip()
    
    if user.get("is_banned"):
        return
    
    # Rename
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
    
    # Broadcast
    if user.get("state") == "broadcast" and uid == OWNER_ID:
        user["state"] = "none"
        user["bc"] = text
        db_save()
        count = len([u for u in DB["users"] if not DB["users"][u].get("is_banned")])
        return await m.reply_text(f"📢 **Preview:**\n\n{text}\n\n👥 Will send to: {count} users", reply_markup=bc_kb())
    
    # Add Pro
    if user.get("state") == "addpro" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            user_get(int(text))["is_pro"] = True
            db_save()
            return await m.reply_text(f"✅ `{text}` is PRO!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid!", reply_markup=admin_kb())
    
    # Ban
    if user.get("state") == "ban" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            user_get(int(text))["is_banned"] = True
            db_save()
            return await m.reply_text(f"✅ `{text}` banned!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid!", reply_markup=admin_kb())
    
    # URL
    if not text.startswith("http"):
        return
    
    if not await is_subscribed(uid):
        return await m.reply_text("⚠️ Join channel first!", reply_markup=join_kb())
    
    status = await m.reply_text("🔍 **Analyzing link...**", reply_markup=cancel_kb())
    session_set(uid, {"url": text, "cancel": False})
    
    if is_yt(text):
        return await safe_edit(status, "🎬 **YouTube detected!**\n\nChoose format:", yt_kb())
    
    try:
        await safe_edit(status, "⬇️ **Starting download...**\n\n⏱️ Calculating ETA...", cancel_kb())
        try:
            path, title = await download_yt(uid, text, status, "best")
        except:
            path, title = await download_direct(uid, text, status)
        
        name = os.path.basename(path)
        size = os.path.getsize(path)
        session_set(uid, {"url": text, "path": path, "name": name, "ext": get_ext(name), "size": size, "cancel": False})
        await safe_edit(status, f"✅ **Download Complete!**\n\n📄 `{name}`\n📦 {human_size(size)}", upload_kb())
    except Exception as e:
        session_clear(uid)
        msg = "❌ Cancelled!" if "CANCELLED" in str(e) else f"❌ Error: {str(e)[:100]}"
        await safe_edit(status, msg, None)

@app.on_message((filters.video | filters.document | filters.audio) & filters.private)
async def on_file(_, m):
    uid = m.from_user.id
    if not await is_subscribed(uid):
        return await m.reply_text("⚠️ Join first!", reply_markup=join_kb())
    
    media = m.video or m.document or m.audio
    file_size = getattr(media, "file_size", 0)
    
    # Estimate download time (assume ~5MB/s from Telegram)
    est_time = file_size / (5 * 1024 * 1024) if file_size else 0
    
    status = await m.reply_text(
        f"⬇️ **Downloading from Telegram...**\n\n"
        f"📦 Size: {human_size(file_size)}\n"
        f"⏱️ ETA: ~{human_time(est_time)}",
        reply_markup=cancel_kb()
    )
    session_set(uid, {"cancel": False})
    
    try:
        name = safe_name(getattr(media, "file_name", None) or f"file_{int(time.time())}")
        path = os.path.join(DOWNLOAD_DIR, name)
        
        start = time.time()
        await m.download(path)
        elapsed = time.time() - start
        
        size = os.path.getsize(path)
        session_set(uid, {"path": path, "name": name, "ext": get_ext(name), "size": size, "cancel": False})
        await safe_edit(status, f"✅ **Downloaded in {human_time(elapsed)}!**\n\n📄 `{name}`\n📦 {human_size(size)}", upload_kb())
    except Exception as e:
        session_clear(uid)
        await safe_edit(status, f"❌ {str(e)[:80]}", None)

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
    
    if data == "check_join":
        if await is_subscribed(uid):
            return await safe_edit(cb.message, "✅ Verified!", menu_kb(uid))
        return await cb.answer("❌ Not joined!", show_alert=True)
    
    if data == "cancel":
        if sess:
            sess["cancel"] = True
            if sess.get("path") and os.path.exists(sess["path"]):
                try:
                    os.remove(sess["path"])
                except:
                    pass
        session_clear(uid)
        user["state"] = "none"
        db_save()
        return await safe_edit(cb.message, "❌ Cancelled!", None)
    
    if data == "back":
        user["state"] = "none"
        db_save()
        return await safe_edit(cb.message, "📋 Menu", menu_kb(uid))
    
    # Menu
    if data == "menu_thumb":
        return await safe_edit(cb.message, "🖼️ **Thumbnail**\n\nSend a photo to set as thumbnail.", thumb_kb())
    
    if data == "menu_stats":
        used = user.get("used", 0)
        limit = DAILY_LIMIT
        remaining = max(0, limit - used)
        return await safe_edit(
            cb.message,
            f"📊 **Your Stats**\n\n"
            f"📦 Used today: {human_size(used)}\n"
            f"📉 Remaining: {human_size(remaining)}\n"
            f"👑 Pro: {'Yes ✅' if user.get('is_pro') else 'No ❌'}",
            menu_kb(uid)
        )
    
    if data == "menu_help":
        return await safe_edit(
            cb.message,
            "❓ **How to use:**\n\n"
            "1️⃣ Send any video link\n"
            "2️⃣ Choose quality (for YouTube)\n"
            "3️⃣ Wait for download (see ETA)\n"
            "4️⃣ Rename or Upload\n"
            "5️⃣ Get video + 5 screenshots!",
            menu_kb(uid)
        )
    
    # Thumb
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
    
    # Admin
    if data == "admin":
        if uid != OWNER_ID:
            return
        return await safe_edit(cb.message, "⚙️ **Admin Panel**", admin_kb())
    
    if data == "adm_stats":
        if uid != OWNER_ID:
            return
        total, used, free = shutil.disk_usage("/")
        cookies = "✅ Found" if COOKIES_PATH else "❌ Missing"
        return await safe_edit(
            cb.message,
            f"📊 **Bot Stats**\n\n"
            f"👥 Users: {len(DB['users'])}\n"
            f"💾 Disk: {human_size(used)}/{human_size(total)}\n"
            f"🍪 Cookies: {cookies}",
            admin_kb()
        )
    
    if data == "adm_bc":
        if uid != OWNER_ID:
            return
        user["state"] = "broadcast"
        db_save()
        return await safe_edit(cb.message, "📢 Send broadcast message:", cancel_kb())
    
    if data == "bc_yes":
        if uid != OWNER_ID:
            return
        text = user.get("bc", "")
        if not text:
            return
        
        await safe_edit(cb.message, "📢 **Broadcasting...**\n\n⏱️ This may take a while...", None)
        
        sent = 0
        failed = 0
        total_users = len([u for u in DB["users"] if not DB["users"][u].get("is_banned")])
        
        for u in DB["users"]:
            if DB["users"][u].get("is_banned"):
                continue
            try:
                await app.send_message(int(u), text)
                sent += 1
            except:
                failed += 1
            await asyncio.sleep(0.05)
        
        user["bc"] = ""
        db_save()
        return await safe_edit(cb.message, f"✅ **Broadcast Complete!**\n\n📤 Sent: {sent}\n❌ Failed: {failed}", admin_kb())
    
    if data == "bc_no":
        user["state"] = "none"
        user["bc"] = ""
        db_save()
        return await safe_edit(cb.message, "❌ Cancelled!", admin_kb())
    
    if data == "adm_pro":
        if uid != OWNER_ID:
            return
        user["state"] = "addpro"
        db_save()
        return await safe_edit(cb.message, "👑 Send user ID to make PRO:", cancel_kb())
    
    if data == "adm_ban":
        if uid != OWNER_ID:
            return
        user["state"] = "ban"
        db_save()
        return await safe_edit(cb.message, "🚫 Send user ID to ban:", cancel_kb())
    
    # YouTube
    if data.startswith("yt_"):
        if not sess or not sess.get("url"):
            return await safe_edit(cb.message, "❌ Session expired!", None)
        
        fmt_map = {
            "yt_best": "best",
            "yt_mp3": "mp3",
            "yt_720": "720",
            "yt_480": "480",
            "yt_360": "360"
        }
        fmt = fmt_map.get(data, "best")
        fmt_name = {"best": "Best Quality", "mp3": "MP3 Audio", "720": "720p", "480": "480p", "360": "360p"}.get(fmt, fmt)
        
        try:
            await safe_edit(
                cb.message,
                f"⬇️ **Downloading {fmt_name}...**\n\n"
                f"🔄 Connecting to YouTube...\n"
                f"⏱️ Calculating ETA...",
                cancel_kb()
            )
            path, title = await download_yt(uid, sess["url"], cb.message, fmt)
            name = os.path.basename(path)
            size = os.path.getsize(path)
            session_set(uid, {"url": sess["url"], "path": path, "name": name, "ext": get_ext(name), "size": size, "cancel": False})
            await safe_edit(cb.message, f"✅ **Download Complete!**\n\n📄 `{name}`\n📦 {human_size(size)}", upload_kb())
        except Exception as e:
            session_clear(uid)
            msg = "❌ Cancelled!" if "CANCELLED" in str(e) else f"❌ {str(e)[:120]}"
            await safe_edit(cb.message, msg, None)
        return
    
    # Rename
    if data == "rename":
        if not sess:
            return await safe_edit(cb.message, "❌ Expired!", None)
        return await safe_edit(cb.message, f"✏️ **Rename**\n\nCurrent: `{sess['name']}`", rename_kb())
    
    if data == "ren_def":
        if sess:
            return await safe_edit(cb.message, f"📝 Using default: `{sess['name']}`", upload_kb())
        return
    
    if data == "ren_cust":
        if not sess:
            return
        user["state"] = "rename"
        db_save()
        return await safe_edit(cb.message, f"📝 Send new name (without extension):\n\nExtension `{sess.get('ext', '')}` will be added automatically.", cancel_kb())
    
    if data == "back_up":
        if sess:
            return await safe_edit(cb.message, f"📄 `{sess['name']}`\n📦 {human_size(sess.get('size', 0))}", upload_kb())
        return
    
    # Upload
    if data in ["up_file", "up_video"]:
        if not sess or not sess.get("path") or not os.path.exists(sess["path"]):
            return await safe_edit(cb.message, "❌ File not found!", None)
        
        size = sess.get("size", 0)
        # Estimate upload time (~3MB/s for Telegram)
        est_time = size / (3 * 1024 * 1024) if size else 0
        
        try:
            await safe_edit(
                cb.message,
                f"📤 **Starting upload...**\n\n"
                f"📦 Size: {human_size(size)}\n"
                f"⏱️ ETA: ~{human_time(est_time)}",
                cancel_kb()
            )
            await do_upload(uid, cb.message, sess["path"], sess["name"], data == "up_video")
            
            try:
                os.remove(sess["path"])
            except:
                pass
            session_clear(uid)
            await safe_edit(cb.message, "✅ **Upload Complete!**", menu_kb(uid))
        except Exception as e:
            msg = "❌ Cancelled!" if "CANCELLED" in str(e) else f"❌ {str(e)[:80]}"
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
    find_cookies()
    
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
