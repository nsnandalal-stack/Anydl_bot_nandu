dimport os
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

# Get environment variables
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

# Debug: Print what we got
print("=" * 60)
print("🔍 ENVIRONMENT VARIABLES CHECK")
print(f"API_ID: {API_ID}")
print(f"API_HASH: {'*' * len(API_HASH) if API_HASH else 'NOT SET'}")
print(f"BOT_TOKEN: {'SET ✅' if BOT_TOKEN else 'NOT SET ❌'}")
print(f"CHANNEL_ID: {CHANNEL_ID}")
print("=" * 60)

# Validate required variables
if not API_ID or not API_HASH or not BOT_TOKEN or not CHANNEL_ID:
    print("\n❌ ERROR: MISSING ENVIRONMENT VARIABLES!")
    print("\nRequired variables:")
    print("  • API_ID      (from https://my.telegram.org)")
    print("  • API_HASH    (from https://my.telegram.org)")
    print("  • BOT_TOKEN   (from @BotFather)")
    print("  • CHANNEL_ID  (your channel ID)")
    print("\nAdd these in your hosting dashboard (Emergent/Railway)")
    print("Then redeploy the application.\n")
    exit(1)

# Convert to correct types
try:
    API_ID = int(API_ID)
    CHANNEL_ID = int(CHANNEL_ID)
    print("✅ Environment variables validated successfully!\n")
except ValueError as e:
    print(f"❌ ERROR: API_ID and CHANNEL_ID must be numbers!")
    print(f"Current values - API_ID: {API_ID}, CHANNEL_ID: {CHANNEL_ID}\n")
    exit(1)

INVITE_LINK = "https://t.me/+eooytvOAwjc0NTI1"
DOWNLOAD_DIR = "/tmp/downloads"
THUMB_DIR = "/tmp/thumbnails"
DB_FILE = "/tmp/bot_db.json"

DAILY_LIMIT = 5 * 1024 * 1024 * 1024

# Global cookies path
COOKIES_PATH = None

# =======================
# FIND COOKIES
# =======================
def find_cookies():
    """Find and validate cookies file"""
    global COOKIES_PATH
    
    possible_paths = [
        "/app/cookies/cookies.txt",
        "/app/cookies.txt",
        "cookies/cookies.txt",
        "cookies.txt",
        os.path.join(os.getcwd(), "cookies", "cookies.txt"),
        os.path.join(os.getcwd(), "cookies.txt"),
    ]
    
    # Check for any .txt in cookies folder
    for folder in ["/app/cookies", "cookies"]:
        if os.path.isdir(folder):
            for f in os.listdir(folder):
                if f.endswith(".txt"):
                    possible_paths.insert(0, os.path.join(folder, f))
    
    for path in possible_paths:
        if os.path.exists(path) and os.path.isfile(path):
            try:
                with open(path, 'r') as f:
                    content = f.read()
                    # Check if it's a valid cookie file
                    if '.youtube.com' in content or '.instagram.com' in content or 'Netscape' in content:
                        COOKIES_PATH = path
                        size = os.path.getsize(path)
                        print(f"✅ COOKIES FOUND: {path} ({size} bytes)")
                        return path
            except Exception as e:
                print(f"❌ Error reading {path}: {e}")
    
    print("⚠️  NO COOKIES FOUND - Some downloads may fail")
    print("   Add cookies.txt to enable restricted content downloads")
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

def is_instagram(url: str) -> bool:
    return "instagram.com" in url.lower()

def extract_video_id(url: str) -> str:
    if "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0].split("/")[0]
    elif "v=" in url:
        return url.split("v=")[1].split("&")[0]
    elif "/shorts/" in url:
        return url.split("/shorts/")[1].split("?")[0]
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
       [types.InlineKeyboardButton(✅ I've Joined, callback_data=check_join)]
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
        [types.InlineKeyboardButton("🎬 1080p", callback_data="yt_1080"),
         types.InlineKeyboardButton("🎬 720p", callback_data="yt_720"),
         types.InlineKeyboardButton("📹 480p", callback_data="yt_480")],
        [types.InlineKeyboardButton("📹 360p", callback_data="yt_360"),
         types.InlineKeyboardButton("🎵 MP3 320k", callback_data="yt_mp3_320"),
         types.InlineKeyboardButton("🎵 MP3 192k", callback_data="yt_mp3")],
        [types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

def admin_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("📊 Stats", callback_data="adm_stats"),
         types.InlineKeyboardButton("📢 Broadcast", callback_data="adm_bc")],
        [types.InlineKeyboardButton("👑 Pro", callback_data="adm_pro"),
         types.InlineKeyboardButton("🚫 Ban", callback_data="adm_ban")],
        [types.InlineKeyboardButton("✅ Unban", callback_data="adm_unban"),
         types.InlineKeyboardButton("🍪 Cookies", callback_data="adm_cookies")],
        [types.InlineKeyboardButton("🔙 Back", callback_data="back")]
    ])

def bc_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("✅ Send", callback_data="bc_yes"),
         types.InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel")]
    ])

# =======================
# DOWNLOAD HELPER
# =======================
async def download_from_url(uid: int, url: str, msg, filename: str = None, quality: str = "720"):
    """Download file from URL with retry logic"""
    
    start_time = time.time()
    last_update = 0
    timeout = ClientTimeout(total=600)
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            async with ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        raise Exception(f"HTTP {resp.status}")
                    
                    if not filename:
                        cd = resp.headers.get("Content-Disposition", "")
                        if "filename=" in cd:
                            filename = cd.split("filename=")[1].strip('"\'').split(";")[0]
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
                                
                                await safe_edit(msg, 
                                    f"⬇️ **Downloading... (Attempt {attempt + 1}/{max_retries})**\n\n"
                                    f"`[{progress_bar(pct)}]` {pct:.1f}%\n\n"
                                    f"📦 {human_size(done)} / {human_size(total)}\n"
                                    f"⚡ {human_size(speed)}/s • ⏱️ {human_time(eta)}", 
                                    cancel_kb()
                                )
                    
                    # Validate file
                    if os.path.getsize(path) < 1000:
                        raise Exception("File too small - download failed")
                    
                    return path, os.path.splitext(filename)[0]
        
        except Exception as e:
            if "CANCELLED" in str(e):
                raise
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
                continue
            raise

# =======================
# IMPROVED YT-DLP
# =======================
async def download_ytdlp(uid: int, url: str, msg, quality: str = "720"):
    """Download using yt-dlp with IMPROVED cookie and config handling"""
    
    await safe_edit(msg, "🔄 **Downloading with yt-dlp...**", cancel_kb())
    
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
        
        asyncio.get_event_loop().create_task(safe_edit(msg,
            f"⬇️ **Downloading...**\n\n"
            f"`[{progress_bar(pct)}]` {pct:.1f}%\n\n"
            f"📦 {human_size(done)} / {human_size(total)}\n"
            f"⚡ {human_size(speed)}/s • ⏱️ {human_time(eta)}",
            cancel_kb()
        ))
    
    opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": f"{DOWNLOAD_DIR}/%(title).70s.%(ext)s",
        "noplaylist": True,
        "progress_hooks": [hook],
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 30,
        "nocheckcertificate": True,
        "geo_bypass": True,
        "geo_bypass_country": "US",
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "android", "web", "tv_embedded"],
                "player_skip": ["webpage"],
                "skip": ["dash", "hls"],
            },
            "instagram": {
                "include_stories": False,
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }
    }
    
    # ADD COOKIES IF AVAILABLE
    if COOKIES_PATH and os.path.exists(COOKIES_PATH):
        opts["cookiefile"] = COOKIES_PATH
        print(f"🍪 Using cookies: {COOKIES_PATH}")
    
    # Format selection
    if quality.startswith("mp3"):
        bitrate = "320" if quality == "mp3_320" else "192"
        opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": bitrate
        }]
    else:
        target = int(quality) if quality.isdigit() else 720
        if target >= 1080:
            opts["format"] = (
                "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
                "bestvideo[height<=1080]+bestaudio/"
                "best[height<=1080]/"
                "best"
            )
        else:
            opts["format"] = (
                f"bestvideo[height<={target}][ext=mp4]+bestaudio[ext=m4a]/"
                f"bestvideo[height<={target}]+bestaudio/"
                f"best[height<={target}]/"
                "best"
            )
        opts["merge_output_format"] = "mp4"
    
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
# COBALT API
# =======================
async def download_cobalt(uid: int, url: str, msg, quality: str = "720"):
    """Download using Cobalt API - works without cookies!"""
    
    await safe_edit(msg, "🔄 **Method: Cobalt API...**", cancel_kb())
    
    cobalt_instances = [
        "https://co.wuk.sh",
        "https://cobalt-api.kwiatekmiki.com",
    ]
    
    timeout = ClientTimeout(total=60)
    
    is_audio_only = quality.startswith("mp3")
    audio_bitrate = "320" if quality == "mp3_320" else "192"
    
    for instance in cobalt_instances:
        try:
            payload = {
                "url": url,
                "vCodec": "h264",
                "vQuality": "max" if quality == "1080" else quality if quality.isdigit() else "720",
                "aFormat": "mp3" if is_audio_only else "best",
                "filenamePattern": "basic",
                "isAudioOnly": is_audio_only,
                "audioBitrate": audio_bitrate if is_audio_only else "best"
            }
            
            async with ClientSession(timeout=timeout) as session:
                async with session.post(f"{instance}/api/json", json=payload) as resp:
                    if resp.status != 200:
                        continue
                    
                    data = await resp.json()
                    
                    if data.get("status") == "redirect" or data.get("status") == "stream":
                        download_url = data.get("url")
                        if download_url:
                            filename = f"video_{int(time.time())}"
                            filename += ".mp3" if is_audio_only else ".mp4"
                            return await download_from_url(uid, download_url, msg, filename, quality)
        
        except Exception as e:
            print(f"Cobalt {instance} failed: {e}")
            continue
    
    raise Exception("Cobalt API failed")

# =======================
# MAIN DOWNLOAD
# =======================
async def download_video(uid: int, url: str, msg, quality: str = "720"):
    """Try all methods in order"""
    
    errors = []
    
    # Method 1: Cobalt
    try:
        return await download_cobalt(uid, url, msg, quality)
    except Exception as e:
        if "CANCELLED" in str(e): raise
        errors.append(f"Cobalt: {str(e)[:50]}")
    
    # Method 2: yt-dlp
    try:
        return await download_ytdlp(uid, url, msg, quality)
    except Exception as e:
        if "CANCELLED" in str(e): raise
        errors.append(f"yt-dlp: {str(e)[:50]}")
    
    raise Exception("All methods failed:\n" + "\n".join(errors[:3]))

# =======================
# DIRECT DOWNLOAD
# =======================
async def download_direct(uid: int, url: str, msg):
    """Direct download for non-YouTube/Instagram links"""
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
        if dur <= 0: return [], out
        
        interval = dur / (count + 1)
        for i in range(1, count + 1):
            o = os.path.join(out, f"{i}.jpg")
            c = f'ffmpeg -ss {interval * i} -i "{path}" -vframes 1 -q:v 5 -y "{o}" 2>/dev/null'
            p = await asyncio.create_subprocess_shell(c)
            await p.wait()
            if os.path.exists(o): screens.append(o)
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
        if sess and sess.get("cancel"): raise Exception("CANCELLED")
        now = time.time()
        if now - last["t"] < 2: return
        last["t"] = now
        elapsed = now - start_time
        speed = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / speed if speed > 0 and total > 0 else 0
        pct = (done / total * 100) if total > 0 else 0
        await safe_edit(msg,
            f"📤 **Uploading...**\n\n"
            f"`[{progress_bar(pct)}]` {pct:.1f}%\n\n"
            f"📦 {human_size(done)} / {human_size(total)}\n"
            f"⚡ {human_size(speed)}/s • ⏱️ {human_time(eta)}",
            cancel_kb()
        )
    
    if as_video:
        await app.send_video(uid, path, caption=f"🎬 `{name}`", file_name=name, supports_streaming=True, thumb=thumb, progress=prog)
        await safe_edit(msg, "📸 **Screenshots...**", None)
        ss, ss_dir = await make_ss(path, 5)
        if ss:
            try: await app.send_media_group(uid, [types.InputMediaPhoto(s) for s in ss])
            except: pass
        shutil.rmtree(ss_dir, ignore_errors=True)
    else:
        await app.send_document(uid, path, caption=f"📄 `{name}`", file_name=name, thumb=thumb, progress=prog)
    
    if uid != OWNER_ID and not user.get("is_pro"):
        user["used"] = user.get("used", 0) + size
        db_save()

# =======================
# BOT INITIALIZATION
# =======================
app = Client(
    name="bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workdir="/app"
)

@app.on_message(filters.command("start") & filters.private)
async def cmd_start(_, m):
    user_get(m.from_user.id)
    db_save()
    await m.reply_text(
        f"👋 Hi **{m.from_user.first_name}**!\n\n"
        f"🚀 Send any video link (YouTube, Instagram, etc.)",
        reply_markup=menu_kb(m.from_user.id)
    )

@app.on_message(filters.text & filters.private & ~filters.command(["start"]))
async def on_text(_, m):
    uid = m.from_user.id
    user = user_get(uid)
    text = m.text.strip()
    
    if user.get("is_banned"): return
    
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
        return await m.reply_text(f"✅ `{new}`", reply_markup=upload_kb())
    
    if user.get("state") == "broadcast" and uid == OWNER_ID:
        user["state"] = "none"
        user["bc"] = text
        db_save()
        return await m.reply_text(f"📢 **Preview:**\n\n{text}", reply_markup=bc_kb())
    
    if user.get("state") == "addpro" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            user_get(int(text))["is_pro"] = True
            db_save()
            return await m.reply_text(f"✅ PRO!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid!", reply_markup=admin_kb())
    
    if user.get("state") == "ban" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            user_get(int(text))["is_banned"] = True
            db_save()
            return await m.reply_text(f"✅ Banned!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid!", reply_markup=admin_kb())
    
    if user.get("state") == "unban" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            user_get(int(text))["is_banned"] = False
            db_save()
            return await m.reply_text(f"✅ Unbanned!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid!", reply_markup=admin_kb())
    
    if not text.startswith("http"): return
    
    if not await is_subscribed(uid):
        return await m.reply_text("⚠️ **Join 
