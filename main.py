import os
import re
import time
import json
import shutil
import asyncio
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
COOKIES_FILE = "/app/cookies/cookies.txt"

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

def extract_video_id(url: str) -> str:
    """Extract YouTube video ID from URL"""
    if "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0].split("/")[0]
    elif "v=" in url:
        return url.split("v=")[1].split("&")[0]
    elif "/shorts/" in url:
        return url.split("/shorts/")[1].split("?")[0]
    elif "/embed/" in url:
        return url.split("/embed/")[1].split("?")[0]
    return ""

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
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"

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
    return types.InlineKeyboardMarkup([[types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])

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
        [types.InlineKeyboardButton("📊 Bot Stats", callback_data="adm_stats"),
         types.InlineKeyboardButton("📢 Broadcast", callback_data="adm_bc")],
        [types.InlineKeyboardButton("👑 Add Pro", callback_data="adm_pro"),
         types.InlineKeyboardButton("🚫 Ban", callback_data="adm_ban")],
        [types.InlineKeyboardButton("✅ Unban", callback_data="adm_unban")],
        [types.InlineKeyboardButton("🔙 Back", callback_data="back")]
    ])

def bc_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("✅ Send", callback_data="bc_yes"),
         types.InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel")]
    ])

# =======================
# DOWNLOAD FROM URL
# =======================
async def download_from_url(uid: int, url: str, msg, filename: str = None, quality: str = "720"):
    """Download file from URL with progress"""
    
    start_time = time.time()
    last_update = 0
    timeout = ClientTimeout(total=600)
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    async with ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status}")
            
            # Get filename
            if not filename:
                cd = resp.headers.get("Content-Disposition", "")
                if "filename=" in cd:
                    filename = cd.split("filename=")[1].strip('"\'').split(";")[0]
                    try:
                        filename = filename.encode('latin-1').decode('utf-8', errors='ignore')
                    except:
                        pass
                else:
                    ext = ".mp3" if quality == "mp3" else ".mp4"
                    filename = f"video_{int(time.time())}{ext}"
            
            filename = safe_name(filename)
            if not any(filename.endswith(e) for e in ['.mp4', '.mp3', '.webm', '.mkv', '.m4a']):
                filename += ".mp3" if quality == "mp3" else ".mp4"
            
            path = os.path.join(DOWNLOAD_DIR, filename)
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
            
            return path, os.path.splitext(filename)[0]

# =======================
# METHOD 1: PIPED API
# =======================
async def download_piped(uid: int, url: str, msg, quality: str = "720"):
    """Download using Piped API"""
    
    await safe_edit(msg, "🔄 **Method 1: Piped API...**", cancel_kb())
    
    video_id = extract_video_id(url)
    if not video_id:
        raise Exception("Invalid YouTube URL")
    
    # Working Piped instances
    instances = [
        "https://pipedapi.kavin.rocks",
        "https://pipedapi.adminforge.de",
        "https://api.piped.yt",
        "https://pipedapi.in.projectsegfau.lt"
    ]
    
    timeout = ClientTimeout(total=30)
    
    for instance in instances:
        try:
            async with ClientSession(timeout=timeout) as session:
                api_url = f"{instance}/streams/{video_id}"
                
                async with session.get(api_url) as resp:
                    if resp.status != 200:
                        continue
                    
                    data = await resp.json()
                    title = safe_name(data.get("title", f"video_{video_id}"))
                    
                    # Get download URL
                    if quality == "mp3":
                        streams = data.get("audioStreams", [])
                        if streams:
                            # Get highest quality audio
                            streams.sort(key=lambda x: x.get("bitrate", 0), reverse=True)
                            download_url = streams[0].get("url")
                            filename = f"{title}.mp3"
                        else:
                            continue
                    else:
                        streams = data.get("videoStreams", [])
                        target = int(quality) if quality.isdigit() else 720
                        
                        # Filter by quality
                        suitable = [s for s in streams if s.get("quality", "").startswith(str(target))]
                        if not suitable:
                            suitable = [s for s in streams if "720" in s.get("quality", "")]
                        if not suitable and streams:
                            suitable = streams
                        
                        if suitable:
                            download_url = suitable[0].get("url")
                            filename = f"{title}.mp4"
                        else:
                            continue
                    
                    if download_url:
                        return await download_from_url(uid, download_url, msg, filename, quality)
        except Exception as e:
            continue
    
    raise Exception("Piped API failed")

# =======================
# METHOD 2: COBALT API (NEW FORMAT)
# =======================
async def download_cobalt(uid: int, url: str, msg, quality: str = "720"):
    """Download using Cobalt API v2"""
    
    await safe_edit(msg, "🔄 **Method 2: Cobalt API...**", cancel_kb())
    
    api_url = "https://api.cobalt.tools/api/json"
    
    # Quality mapping
    vquality = "720" if quality in ["720", "mp3"] else quality
    
    payload = {
        "url": url,
        "vCodec": "h264",
        "vQuality": vquality,
        "aFormat": "mp3",
        "filenamePattern": "basic",
        "isAudioOnly": quality == "mp3",
        "disableMetadata": False
    }
    
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
    }
    
    timeout = ClientTimeout(total=30)
    
    async with ClientSession(timeout=timeout) as session:
        async with session.post(api_url, json=payload, headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"Cobalt error: {resp.status}")
            
            data = await resp.json()
            
            status = data.get("status")
            
            if status == "error":
                raise Exception(data.get("text", "Cobalt error"))
            
            download_url = None
            
            if status in ["redirect", "stream"]:
                download_url = data.get("url")
            elif status == "picker":
                items = data.get("picker", [])
                if items:
                    # Get video (not audio) from picker
                    for item in items:
                        if item.get("type") == "video" or "video" in str(item.get("url", "")):
                            download_url = item.get("url")
                            break
                    if not download_url and items:
                        download_url = items[0].get("url")
            
            if not download_url:
                raise Exception("No download URL")
            
            return await download_from_url(uid, download_url, msg, None, quality)

# =======================
# METHOD 3: YT-DLP WITH COOKIES
# =======================
async def download_ytdlp(uid: int, url: str, msg, quality: str = "720"):
    """Download using yt-dlp with cookies"""
    
    await safe_edit(msg, "🔄 **Method 3: yt-dlp...**", cancel_kb())
    
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
        "retries": 3,
        "socket_timeout": 30,
        "nocheckcertificate": True,
        "geo_bypass": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "android"],
            }
        },
        "http_headers": {
            "User-Agent": "com.google.ios.youtube/19.09.3 (iPhone14,3; U; CPU iOS 15_6 like Mac OS X)",
        }
    }
    
    # Add cookies if exists
    for cookie_path in [COOKIES_FILE, "cookies/cookies.txt", "cookies.txt", "/app/cookies.txt"]:
        if os.path.exists(cookie_path):
            opts["cookiefile"] = cookie_path
            print(f"✅ Using cookies: {cookie_path}")
            break
    
    # Format
    if quality == "mp3":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
    else:
        target = int(quality) if quality.isdigit() else 720
        opts["format"] = f"bestvideo[height<={target}]+bestaudio/best[height<={target}]/best"
    
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
# METHOD 4: Y2MATE STYLE
# =======================
async def download_y2mate(uid: int, url: str, msg, quality: str = "720"):
    """Download using Y2Mate style API"""
    
    await safe_edit(msg, "🔄 **Method 4: Y2Mate...**", cancel_kb())
    
    video_id = extract_video_id(url)
    if not video_id:
        raise Exception("Invalid URL")
    
    # Use a public API
    api_url = f"https://yt1s.com/api/ajaxSearch/index"
    
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
    }
    
    timeout = ClientTimeout(total=30)
    
    async with ClientSession(timeout=timeout) as session:
        # Step 1: Get video info
        data = f"q={url}&vt=mp4" if quality != "mp3" else f"q={url}&vt=mp3"
        
        async with session.post(api_url, data=data, headers=headers) as resp:
            if resp.status != 200:
                raise Exception("Y2Mate error")
            
            result = await resp.json()
            
            if result.get("status") != "ok":
                raise Exception("Y2Mate failed")
            
            # Get the links
            links = result.get("links", {})
            
            if quality == "mp3":
                mp3_links = links.get("mp3", {})
                if mp3_links:
                    first_key = list(mp3_links.keys())[0]
                    download_url = mp3_links[first_key].get("url")
                    if download_url:
                        return await download_from_url(uid, download_url, msg, None, quality)
            else:
                mp4_links = links.get("mp4", {})
                target = int(quality) if quality.isdigit() else 720
                
                for key, val in mp4_links.items():
                    if str(target) in val.get("q", ""):
                        download_url = val.get("url")
                        if download_url:
                            return await download_from_url(uid, download_url, msg, None, quality)
    
    raise Exception("Y2Mate failed")

# =======================
# MAIN DOWNLOAD FUNCTION
# =======================
async def download_video(uid: int, url: str, msg, quality: str = "720"):
    """Try all methods in order"""
    
    methods = [
        ("Piped", download_piped),
        ("Cobalt", download_cobalt),
        ("yt-dlp", download_ytdlp),
    ]
    
    errors = []
    
    for name, method in methods:
        try:
            sess = session_get(uid)
            if sess and sess.get("cancel"):
                raise Exception("CANCELLED")
            
            result = await method(uid, url, msg, quality)
            return result
            
        except Exception as e:
            error_msg = str(e)
            if "CANCELLED" in error_msg:
                raise
            errors.append(f"{name}: {error_msg[:50]}")
            print(f"❌ {name} failed: {error_msg}")
            continue
    
    # All methods failed - show errors
    error_text = "\n".join(errors)
    raise Exception(f"All methods failed:\n{error_text}")

# =======================
# DIRECT DOWNLOAD
# =======================
async def download_direct(uid: int, url: str, msg):
    """Direct URL download"""
    return await download_from_url(uid, url, msg, None, "720")

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
    
    # States
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
    
    if user.get("state") == "broadcast" and uid == OWNER_ID:
        user["state"] = "none"
        user["bc"] = text
        db_save()
        count = len([u for u in DB["users"] if not DB["users"][u].get("is_banned")])
        return await m.reply_text(f"📢 **Preview:**\n\n{text}\n\n👥 {count} users", reply_markup=bc_kb())
    
    if user.get("state") == "addpro" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            user_get(int(text))["is_pro"] = True
            db_save()
            return await m.reply_text(f"✅ `{text}` is PRO!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid!", reply_markup=admin_kb())
    
    if user.get("state") == "ban" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            user_get(int(text))["is_banned"] = True
            db_save()
            return await m.reply_text(f"✅ `{text}` banned!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid!", reply_markup=admin_kb())
    
    if user.get("state") == "unban" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            user_get(int(text))["is_banned"] = False
            db_save()
            return await m.reply_text(f"✅ `{text}` unbanned!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid!", reply_markup=admin_kb())
    
    if not text.startswith("http"):
        return
    
    if not await is_subscribed(uid):
        return await m.reply_text("⚠️ **Join our channel first!**", reply_markup=join_kb())
    
    status = await m.reply_text("🔍 **Analyzing link...**", reply_markup=cancel_kb())
    session_set(uid, {"url": text, "cancel": False})
    
    if is_yt(text):
        return await safe_edit(status, "🎬 **YouTube detected!**\n\nChoose quality:", yt_kb())
    
    try:
        await safe_edit(status, "⬇️ **Downloading...**", cancel_kb())
        try:
            path, title = await download_video(uid, text, status, "720")
        except:
            path, title = await download_direct(uid, text, status)
        
        name = os.path.basename(path)
        size = os.path.getsize(path)
        session_set(uid, {"url": text, "path": path, "name": name, "ext": get_ext(name), "size": size, "cancel": False})
        await safe_edit(status, f"✅ **Done!**\n\n📄 `{name}`\n📦 {human_size(size)}", upload_kb())
    except Exception as e:
        session_clear(uid)
        msg = "❌ **Cancelled!**" if "CANCELLED" in str(e) else f"❌ **Error:** {str(e)[:200]}"
        await safe_edit(status, msg, None)

@app.on_message((filters.video | filters.document | filters.audio) & filters.private)
async def on_file(_, m):
    uid = m.from_user.id
    if user_get(uid).get("is_banned"):
        return
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
        await safe_edit(status, f"❌ {str(e)[:80]}", None)

@app.on_message(filters.photo & filters.private)
async def on_photo(_, m):
    uid = m.from_user.id
    if user_get(uid).get("is_banned"):
        return
    path = os.path.join(THUMB_DIR, f"{uid}.jpg")
    await m.download(path)
    user_get(uid)["thumb"] = path
    db_save()
    await m.reply_text("✅ **Thumbnail saved!**")

@app.on_callback_query()
async def on_cb(_, cb):
    uid = cb.from_user.id
    data = cb.data
    user = user_get(uid)
    sess = session_get(uid)
    
    await cb.answer()
    
    if user.get("is_banned"):
        return
    
    if data == "close":
        try:
            await cb.message.delete()
        except:
            pass
        return
    
    if data == "check_join":
        if await is_subscribed(uid):
            return await safe_edit(cb.message, "✅ **Verified!**", menu_kb(uid))
        return await cb.answer("❌ Not joined!", show_alert=True)
    
    if data == "cancel":
        if sess:
            sess["cancel"] = True
            session_set(uid, sess)
            if sess.get("path") and os.path.exists(sess["path"]):
                try:
                    os.remove(sess["path"])
                except:
                    pass
        session_clear(uid)
        user["state"] = "none"
        db_save()
        return await safe_edit(cb.message, "❌ **Cancelled!**", None)
    
    if data == "back":
        user["state"] = "none"
        db_save()
        return await safe_edit(cb.message, "📋 **Menu**", menu_kb(uid))
    
    # Menu
    if data == "menu_thumb":
        return await safe_edit(cb.message, "🖼️ **Thumbnail**\n\nSend a photo.", thumb_kb())
    
    if data == "menu_stats":
        if uid == OWNER_ID:
            total = len(DB["users"])
            pro = len([u for u in DB["users"].values() if u.get("is_pro")])
            banned = len([u for u in DB["users"].values() if u.get("is_banned")])
            return await safe_edit(cb.message, f"📊 **Bot Stats**\n\n👥 Users: {total}\n👑 Pro: {pro}\n🚫 Banned: {banned}", menu_kb(uid))
        else:
            used = user.get("used", 0)
            rem = max(0, DAILY_LIMIT - used)
            return await safe_edit(cb.message, f"📊 **Your Stats**\n\n📦 Used: {human_size(used)}\n📉 Left: {human_size(rem)}", menu_kb(uid))
    
    if data == "menu_help":
        return await safe_edit(cb.message, "❓ **How to use:**\n\n1️⃣ Send video link\n2️⃣ Choose quality\n3️⃣ Wait for download\n4️⃣ Upload as File/Video\n\n💡 Send photo for thumbnail.", menu_kb(uid))
    
    # Thumb
    if data == "thumb_view":
        t = user.get("thumb")
        if t and os.path.exists(t):
            await cb.message.reply_photo(t)
        else:
            await cb.answer("No thumb!", show_alert=True)
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
        return await safe_edit(cb.message, "⚙️ **Admin**", admin_kb())
    
    if data == "adm_stats":
        if uid != OWNER_ID:
            return
        total = len(DB["users"])
        pro = len([u for u in DB["users"].values() if u.get("is_pro")])
        banned = len([u for u in DB["users"].values() if u.get("is_banned")])
        return await safe_edit(cb.message, f"📊 **Stats**\n\n👥 {total}\n👑 {pro}\n🚫 {banned}", admin_kb())
    
    if data == "adm_bc":
        if uid != OWNER_ID:
            return
        user["state"] = "broadcast"
        db_save()
        return await safe_edit(cb.message, "📢 Send message:", cancel_kb())
    
    if data == "bc_yes":
        if uid != OWNER_ID:
            return
        text = user.get("bc", "")
        if not text:
            return
        sent = 0
        for u in DB["users"]:
            if not DB["users"][u].get("is_banned"):
                try:
                    await app.send_message(int(u), text)
                    sent += 1
                except:
                    pass
        user["bc"] = ""
        db_save()
        return await safe_edit(cb.message, f"✅ Sent to {sent}!", admin_kb())
    
    if data == "bc_cancel":
        user["state"] = "none"
        user["bc"] = ""
        db_save()
        return await safe_edit(cb.message, "❌ Cancelled!", admin_kb())
    
    if data == "adm_pro":
        if uid != OWNER_ID:
            return
        user["state"] = "addpro"
        db_save()
        return await safe_edit(cb.message, "👑 Send user ID:", cancel_kb())
    
    if data == "adm_ban":
        if uid != OWNER_ID:
            return
        user["state"] = "ban"
        db_save()
        return await safe_edit(cb.message, "🚫 Send user ID:", cancel_kb())
    
    if data == "adm_unban":
        if uid != OWNER_ID:
            return
        user["state"] = "unban"
        db_save()
        return await safe_edit(cb.message, "✅ Send user ID:", cancel_kb())
    
    # YouTube
    if data.startswith("yt_"):
        if not sess or not sess.get("url"):
            return await safe_edit(cb.message, "❌ Expired!", None)
        
        quality = data.replace("yt_", "")
        
        try:
            await safe_edit(cb.message, f"⬇️ **Downloading {quality}...**", cancel_kb())
            path, title = await download_video(uid, sess["url"], cb.message, quality)
            name = os.path.basename(path)
            size = os.path.getsize(path)
            session_set(uid, {"url": sess["url"], "path": path, "name": name, "ext": get_ext(name), "size": size, "cancel": False})
            await safe_edit(cb.message, f"✅ **Done!**\n\n📄 `{name}`\n📦 {human_size(size)}", upload_kb())
        except Exception as e:
            session_clear(uid)
            msg = "❌ Cancelled!" if "CANCELLED" in str(e) else f"❌ {str(e)[:200]}"
            await safe_edit(cb.message, msg, None)
        return
    
    # Rename
    if data == "rename":
        if sess:
            return await safe_edit(cb.message, f"✏️ **Rename**\n\n`{sess['name']}`", rename_kb())
        return
    
    if data == "ren_def":
        if sess:
            return await safe_edit(cb.message, f"📝 `{sess['name']}`", upload_kb())
        return
    
    if data == "ren_cust":
        if sess:
            user["state"] = "rename"
            db_save()
            return await safe_edit(cb.message, "✏️ Send new name:", cancel_kb())
        return
    
    if data == "back_up":
        if sess:
            return await safe_edit(cb.message, f"📄 `{sess['name']}`", upload_kb())
        return
    
    # Upload
    if data in ["up_file", "up_video"]:
        if not sess or not sess.get("path") or not os.path.exists(sess["path"]):
            session_clear(uid)
            return await safe_edit(cb.message, "❌ File missing!", None)
        
        try:
            await safe_edit(cb.message, "📤 **Uploading...**", cancel_kb())
            await do_upload(uid, cb.message, sess["path"], sess["name"], data == "up_video")
            try:
                os.remove(sess["path"])
            except:
                pass
            session_clear(uid)
            await safe_edit(cb.message, "✅ **Done!**", menu_kb(uid))
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
    
    # Check cookies
    for p in [COOKIES_FILE, "cookies/cookies.txt", "cookies.txt"]:
        if os.path.exists(p):
            print(f"✅ Cookies found: {p}")
            break
    else:
        print("⚠️ No cookies (optional)")
    
    await app.start()
    print("✅ Bot started!")
    print(f"👥 Users: {len(DB['users'])}")
    
    srv = web.Application()
    srv.add_routes([web.get("/", health)])
    runner = web.AppRunner(srv)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()
    
    await idle()
    await app.stop()

if __name__ == "__main__":
    app.run(main())
