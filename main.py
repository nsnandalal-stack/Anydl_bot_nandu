import os
import re
import time
import json
import shutil
import asyncio
from datetime import date, datetime
from aiohttp import web, ClientSession, ClientTimeout

from pyrogram import Client, filters, types, enums, idle, errors
from yt_dlp import YoutubeDL

# =======================
# LOAD ALL FROM ENVIRONMENT
# =======================
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
OWNER_ID = os.getenv("OWNER_ID")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
COOKIES = os.getenv("COOKIES")
INVITE_LINK = os.getenv("INVITE_LINK", "https://t.me/+eooytvOAwjc0NTI1")

# =======================
# VALIDATE ENVIRONMENT
# =======================
print("=" * 60)
print("🔍 ENVIRONMENT VARIABLES CHECK")
print("=" * 60)
print(f"API_ID: {'SET ✅' if API_ID else 'NOT SET ❌'}")
print(f"API_HASH: {'SET ✅' if API_HASH else 'NOT SET ❌'}")
print(f"BOT_TOKEN: {'SET ✅' if BOT_TOKEN else 'NOT SET ❌'}")
print(f"CHANNEL_ID: {'SET ✅' if CHANNEL_ID else 'NOT SET ❌'}")
print(f"OWNER_ID: {'SET ✅' if OWNER_ID else 'NOT SET ❌'}")
print(f"YOUTUBE_API_KEY: {'SET ✅' if YOUTUBE_API_KEY else 'NOT SET ⚠️'}")
print(f"COOKIES: {'SET ✅' if COOKIES else 'NOT SET ⚠️'}")
print("=" * 60)

missing = []
if not API_ID:
    missing.append("API_ID")
if not API_HASH:
    missing.append("API_HASH")
if not BOT_TOKEN:
    missing.append("BOT_TOKEN")
if not CHANNEL_ID:
    missing.append("CHANNEL_ID")
if not OWNER_ID:
    missing.append("OWNER_ID")

if missing:
    print(f"\n❌ ERROR: Missing required variables: {', '.join(missing)}")
    exit(1)

try:
    API_ID = int(API_ID)
    CHANNEL_ID = int(CHANNEL_ID)
    OWNER_ID = int(OWNER_ID)
    print(f"✅ Variables validated! Owner: {OWNER_ID}")
except ValueError:
    print("❌ ERROR: API_ID, CHANNEL_ID, OWNER_ID must be numbers!")
    exit(1)

# =======================
# CONFIG
# =======================
DOWNLOAD_DIR = "/tmp/downloads"
THUMB_DIR = "/tmp/thumbnails"
DB_FILE = "/tmp/bot_db.json"
DAILY_LIMIT = 5 * 1024 * 1024 * 1024  # 5GB

# =======================
# COOKIES MANAGEMENT
# =======================
COOKIES_PATH = None
COOKIES_EXPIRED = False
COOKIES_EXPIRY_DATE = None
COOKIE_WARNING_SENT = False

def parse_cookie_expiry(cookies_content: str) -> datetime:
    """Parse cookies and find earliest expiry date"""
    if not cookies_content:
        return None
    
    earliest_expiry = None
    
    try:
        for line in cookies_content.strip().split('\n'):
            if line.startswith('#') or not line.strip():
                continue
            
            parts = line.split('\t')
            if len(parts) >= 5:
                try:
                    expiry_timestamp = int(parts[4])
                    if expiry_timestamp > 0:
                        expiry_date = datetime.fromtimestamp(expiry_timestamp)
                        if earliest_expiry is None or expiry_date < earliest_expiry:
                            earliest_expiry = expiry_date
                except (ValueError, IndexError):
                    continue
    except Exception as e:
        print(f"Error parsing cookies: {e}")
    
    return earliest_expiry

def check_cookies_expiry() -> tuple:
    """Check if cookies are expired or expiring soon"""
    global COOKIES_EXPIRED, COOKIES_EXPIRY_DATE
    
    if not COOKIES:
        COOKIES_EXPIRED = True
        return True, None, "No cookies configured"
    
    expiry = parse_cookie_expiry(COOKIES)
    COOKIES_EXPIRY_DATE = expiry
    
    if expiry is None:
        return False, None, "Could not parse expiry"
    
    now = datetime.now()
    
    if expiry <= now:
        COOKIES_EXPIRED = True
        return True, expiry, "Cookies have EXPIRED!"
    
    days_left = (expiry - now).days
    hours_left = (expiry - now).seconds // 3600
    
    if days_left <= 0 and hours_left <= 12:
        COOKIES_EXPIRED = True
        return True, expiry, f"Cookies expiring in {hours_left} hours!"
    elif days_left <= 1:
        return False, expiry, f"⚠️ Cookies expiring in {days_left} day {hours_left} hours!"
    elif days_left <= 3:
        return False, expiry, f"⚠️ Cookies expiring in {days_left} days"
    
    COOKIES_EXPIRED = False
    return False, expiry, f"Cookies valid for {days_left} days"

def setup_cookies():
    """Write cookies from env variable to temp file"""
    global COOKIES_PATH, COOKIES_EXPIRED, COOKIES_EXPIRY_DATE
    
    if not COOKIES:
        print("⚠️ No COOKIES variable set")
        COOKIES_EXPIRED = True
        return
    
    try:
        COOKIES_PATH = "/tmp/cookies.txt"
        with open(COOKIES_PATH, "w") as f:
            f.write(COOKIES)
        
        expired, expiry, message = check_cookies_expiry()
        COOKIES_EXPIRY_DATE = expiry
        
        if expiry:
            print(f"🍪 Cookies written. Expiry: {expiry.strftime('%Y-%m-%d %H:%M')}")
            print(f"   Status: {message}")
        else:
            print(f"🍪 Cookies written ({len(COOKIES)} chars)")
        
        if expired:
            print("⚠️ WARNING: Cookies are expired or expiring soon!")
            COOKIES_EXPIRED = True
        
    except Exception as e:
        print(f"❌ Failed to write cookies: {e}")
        COOKIES_PATH = None
        COOKIES_EXPIRED = True

# =======================
# DATABASE
# =======================
DB = {"users": {}, "sessions": {}, "cookie_warning_sent": False}

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
    elif "/embed/" in url:
        return url.split("/embed/")[1].split("?")[0]
    return ""

def human_size(n) -> str:
    if not n:
        return "0B"
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"

def human_time(seconds) -> str:
    if not seconds or seconds <= 0:
        return "..."
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"

def progress_bar(pct: float) -> str:
    filled = int(pct / 10)
    return "█" * filled + "░" * (10 - filled)

def check_daily_limit(uid: int, file_size: int = 0) -> tuple:
    """Check if user has exceeded daily limit. Returns (allowed, remaining)"""
    if uid == OWNER_ID:
        return True, float('inf')
    
    user = user_get(uid)
    if user.get("is_pro"):
        return True, float('inf')
    
    used = user.get("used", 0)
    remaining = DAILY_LIMIT - used
    
    if file_size > 0:
        return (used + file_size) <= DAILY_LIMIT, remaining
    
    return used < DAILY_LIMIT, remaining

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

async def notify_owner_cookies_expiry():
    """Notify owner about cookie expiry"""
    global COOKIE_WARNING_SENT
    
    if COOKIE_WARNING_SENT or DB.get("cookie_warning_sent"):
        return
    
    expired, expiry, message = check_cookies_expiry()
    
    if expired or (expiry and (expiry - datetime.now()).days <= 3):
        try:
            expiry_str = expiry.strftime('%Y-%m-%d %H:%M') if expiry else "Unknown"
            await app.send_message(
                OWNER_ID,
                f"🚨 **COOKIE ALERT**\n\n"
                f"{'❌ Cookies have EXPIRED!' if expired else '⚠️ Cookies expiring soon!'}\n\n"
                f"📅 Expiry: `{expiry_str}`\n"
                f"📌 Status: {message}\n\n"
                f"{'🔴 **YouTube downloads are PAUSED!**' if expired else '🟡 Please update soon.'}\n\n"
                f"**To fix:**\n"
                f"1. Export fresh cookies from YouTube\n"
                f"2. Update `COOKIES` variable in Koyeb\n"
                f"3. Redeploy the service"
            )
            COOKIE_WARNING_SENT = True
            DB["cookie_warning_sent"] = True
            db_save()
            print(f"📧 Cookie warning sent to owner")
        except Exception as e:
            print(f"Failed to notify owner: {e}")

# =======================
# KEYBOARDS
# =======================
def join_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("📢 Join Channel", url=INVITE_LINK)],
        [types.InlineKeyboardButton("✅ Verify", callback_data="verify_join")]
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
# YOUTUBE DATA API v3
# =======================
async def get_youtube_video_info(video_id: str) -> dict:
    if not YOUTUBE_API_KEY:
        return None
    
    try:
        url = (
            f"https://www.googleapis.com/youtube/v3/videos"
            f"?part=snippet,contentDetails,statistics"
            f"&id={video_id}"
            f"&key={YOUTUBE_API_KEY}"
        )
        
        timeout = ClientTimeout(total=10)
        async with ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                
                data = await resp.json()
                if not data.get("items"):
                    return None
                
                item = data["items"][0]
                snippet = item.get("snippet", {})
                content = item.get("contentDetails", {})
                stats = item.get("statistics", {})
                
                duration_str = content.get("duration", "PT0S")
                duration = 0
                if "H" in duration_str:
                    hours = int(duration_str.split("H")[0].split("T")[1])
                    duration += hours * 3600
                    duration_str = duration_str.split("H")[1]
                else:
                    duration_str = duration_str.split("T")[1] if "T" in duration_str else duration_str
                if "M" in duration_str:
                    minutes = int(duration_str.split("M")[0])
                    duration += minutes * 60
                    duration_str = duration_str.split("M")[1]
                if "S" in duration_str:
                    seconds = int(duration_str.replace("S", ""))
                    duration += seconds
                
                return {
                    "id": video_id,
                    "title": snippet.get("title", "Unknown"),
                    "channel": snippet.get("channelTitle", "Unknown"),
                    "duration": duration,
                    "duration_str": human_time(duration),
                    "views": int(stats.get("viewCount", 0)),
                }
    except Exception as e:
        print(f"YouTube API error: {e}")
        return None

# =======================
# DOWNLOAD FUNCTIONS
# =======================
async def download_from_url(uid: int, url: str, msg, filename: str = None, quality: str = "720"):
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
                    
                    # Check daily limit before downloading
                    allowed, remaining = check_daily_limit(uid, total)
                    if not allowed:
                        raise Exception(f"Daily limit exceeded! Remaining: {human_size(remaining)}")
                    
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
                                    f"⬇️ **Downloading...**\n\n"
                                    f"`[{progress_bar(pct)}]` {pct:.1f}%\n\n"
                                    f"📦 {human_size(done)} / {human_size(total)}\n"
                                    f"⚡ {human_size(speed)}/s • ⏱️ {human_time(eta)}",
                                    cancel_kb()
                                )
                    
                    if os.path.getsize(path) < 1000:
                        raise Exception("File too small")
                    
                    return path, os.path.splitext(filename)[0]
        
        except Exception as e:
            if "CANCELLED" in str(e) or "limit" in str(e).lower():
                raise
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
                continue
            raise

async def download_ytdlp(uid: int, url: str, msg, quality: str = "720", video_info: dict = None):
    global COOKIES_EXPIRED
    
    # Check if cookies expired for YouTube
    if is_yt(url) and COOKIES_EXPIRED:
        await notify_owner_cookies_expiry()
        raise Exception("⏸️ YouTube downloads paused - cookies expired!\n\nAdmin has been notified.")
    
    title = video_info.get("title", "Video") if video_info else "Video"
    await safe_edit(msg, f"🔄 **Downloading:** {title[:50]}...", cancel_kb())
    
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
                "player_client": ["android", "web"],
                "player_skip": ["webpage", "configs"],
            }
        },
        "http_headers": {
            "User-Agent": "com.google.android.youtube/17.36.4 (Linux; U; Android 12; GB) gzip",
        }
    }
    
    if COOKIES_PATH and os.path.exists(COOKIES_PATH):
        opts["cookiefile"] = COOKIES_PATH
    
    if quality.startswith("mp3"):
        bitrate = "320" if quality == "mp3_320" else "192"
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": bitrate
        }]
    else:
        target = int(quality) if quality.isdigit() else 720
        opts["format"] = f"bestvideo[height<={target}]+bestaudio/best[height<={target}]/best"
        opts["merge_output_format"] = "mp4"
    
    loop = asyncio.get_event_loop()
    
    def do_dl():
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            if quality.startswith("mp3"):
                path = os.path.splitext(path)[0] + ".mp3"
            return path, info.get("title", "video")
    
    return await loop.run_in_executor(None, do_dl)

async def download_invidious(uid: int, url: str, msg, quality: str = "720", video_info: dict = None):
    video_id = extract_video_id(url)
    if not video_id:
        raise Exception("Invalid YouTube URL")
    
    title = video_info.get("title", "Video") if video_info else "Video"
    await safe_edit(msg, f"🔄 **Fetching:** {title[:50]}...", cancel_kb())
    
    instances = [
        "https://inv.nadeko.net",
        "https://invidious.nerdvpn.de",
        "https://invidious.jing.rocks",
        "https://yt.artemislena.eu",
    ]
    
    timeout = ClientTimeout(total=30)
    is_audio = quality.startswith("mp3")
    
    for instance in instances:
        try:
            api_url = f"{instance}/api/v1/videos/{video_id}"
            
            async with ClientSession(timeout=timeout) as session:
                async with session.get(api_url) as resp:
                    if resp.status != 200:
                        continue
                    
                    data = await resp.json()
                    title = data.get("title", "video")
                    
                    if is_audio:
                        streams = data.get("adaptiveFormats", [])
                        audio_streams = [s for s in streams if s.get("type", "").startswith("audio")]
                        if audio_streams:
                            audio_streams.sort(key=lambda x: x.get("bitrate", 0), reverse=True)
                            stream_url = audio_streams[0].get("url")
                            if stream_url:
                                filename = f"{safe_name(title)}.mp3"
                                return await download_from_url(uid, stream_url, msg, filename, quality)
                    else:
                        target = int(quality) if quality.isdigit() else 720
                        streams = data.get("formatStreams", [])
                        
                        best_stream = None
                        for s in streams:
                            res = s.get("resolution", "")
                            if "p" in res:
                                height = int(res.replace("p", ""))
                                if height <= target:
                                    best_stream = s
                                    break
                        
                        if not best_stream and streams:
                            best_stream = streams[0]
                        
                        if best_stream:
                            stream_url = best_stream.get("url")
                            if stream_url:
                                filename = f"{safe_name(title)}.mp4"
                                return await download_from_url(uid, stream_url, msg, filename, quality)
        except:
            continue
    
    raise Exception("Invidious failed")

async def download_cobalt(uid: int, url: str, msg, quality: str = "720"):
    await safe_edit(msg, "🔄 **Method: Cobalt...**", cancel_kb())
    
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
                    
                    if data.get("status") in ["redirect", "stream"]:
                        download_url = data.get("url")
                        if download_url:
                            filename = f"video_{int(time.time())}"
                            filename += ".mp3" if is_audio_only else ".mp4"
                            return await download_from_url(uid, download_url, msg, filename, quality)
        except:
            continue
    
    raise Exception("Cobalt failed")

async def download_video(uid: int, url: str, msg, quality: str = "720"):
    errors_list = []
    video_info = None
    
    # Check daily limit first
    allowed, remaining = check_daily_limit(uid)
    if not allowed:
        raise Exception(f"❌ Daily limit (5GB) exceeded!\n\nRemaining: {human_size(remaining)}\nResets at midnight.")
    
    if is_yt(url) and YOUTUBE_API_KEY:
        video_id = extract_video_id(url)
        if video_id:
            video_info = await get_youtube_video_info(video_id)
    
    if is_yt(url):
        try:
            return await download_invidious(uid, url, msg, quality, video_info)
        except Exception as e:
            if "CANCELLED" in str(e) or "limit" in str(e).lower():
                raise
            errors_list.append(f"Invidious: {str(e)[:40]}")
    
    try:
        return await download_cobalt(uid, url, msg, quality)
    except Exception as e:
        if "CANCELLED" in str(e) or "limit" in str(e).lower():
            raise
        errors_list.append(f"Cobalt: {str(e)[:40]}")
    
    try:
        return await download_ytdlp(uid, url, msg, quality, video_info)
    except Exception as e:
        if "CANCELLED" in str(e) or "limit" in str(e).lower() or "paused" in str(e).lower():
            raise
        errors_list.append(f"yt-dlp: {str(e)[:40]}")
    
    raise Exception("Download failed:\n" + "\n".join(errors_list))

async def download_direct(uid: int, url: str, msg):
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
    
    # Check daily limit before upload
    allowed, remaining = check_daily_limit(uid, size)
    if not allowed:
        raise Exception(f"Daily limit exceeded! Remaining: {human_size(remaining)}")
    
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
            try:
                await app.send_media_group(uid, [types.InputMediaPhoto(s) for s in ss])
            except:
                pass
        shutil.rmtree(ss_dir, ignore_errors=True)
    else:
        await app.send_document(uid, path, caption=f"📄 `{name}`", file_name=name, thumb=thumb, progress=prog)
    
    # Update usage for non-owner, non-pro users
    if uid != OWNER_ID and not user.get("is_pro"):
        user["used"] = user.get("used", 0) + size
        db_save()

# =======================
# BOT CLIENT
# =======================
app = Client(
    name="bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workdir="/tmp"
)

# =======================
# HANDLERS
# =======================
@app.on_message(filters.command("start") & filters.private)
async def cmd_start(_, m):
    uid = m.from_user.id
    user = user_get(uid)
    db_save()
    
    if user.get("is_banned"):
        return await m.reply_text("🚫 You are banned.")
    
    # Owner bypasses everything
    if uid == OWNER_ID:
        # Check cookies and notify if needed
        await notify_owner_cookies_expiry()
        
        return await m.reply_text(
            f"👑 **Welcome Boss!**\n\n"
            f"🚀 Send any video link to download.\n\n"
            f"🍪 Cookies: {'✅ Valid' if not COOKIES_EXPIRED else '❌ Expired'}\n"
            f"🔑 YT API: {'✅' if YOUTUBE_API_KEY else '❌'}",
            reply_markup=menu_kb(uid)
        )
    
    # Regular users - check subscription
    if not await is_subscribed(uid):
        return await m.reply_text(
            "⚠️ **You must join our channel to use this bot!**\n\n"
            "1️⃣ Click 'Join Channel'\n"
            "2️⃣ Join the channel\n"
            "3️⃣ Click 'Verify'",
            reply_markup=join_kb()
        )
    
    # Show usage info
    used = user.get("used", 0)
    remaining = DAILY_LIMIT - used
    
    await m.reply_text(
        f"👋 Hi **{m.from_user.first_name}**!\n\n"
        f"🚀 Send any video link to download:\n"
        f"• YouTube\n"
        f"• Instagram\n"
        f"• Direct links\n\n"
        f"📊 Daily limit: {human_size(remaining)} remaining",
        reply_markup=menu_kb(uid)
    )

@app.on_message(filters.text & filters.private & ~filters.command(["start"]))
async def on_text(_, m):
    uid = m.from_user.id
    user = user_get(uid)
    text = m.text.strip()
    
    if user.get("is_banned"):
        return
    
    # Owner bypasses subscription check
    if uid != OWNER_ID and not await is_subscribed(uid):
        return await m.reply_text("⚠️ **Join channel first!**", reply_markup=join_kb())
    
    # Handle states
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
            return await m.reply_text("✅ PRO added!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid ID!", reply_markup=admin_kb())
    
    if user.get("state") == "ban" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            user_get(int(text))["is_banned"] = True
            db_save()
            return await m.reply_text("✅ Banned!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid ID!", reply_markup=admin_kb())
    
    if user.get("state") == "unban" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            user_get(int(text))["is_banned"] = False
            db_save()
            return await m.reply_text("✅ Unbanned!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid ID!", reply_markup=admin_kb())
    
    if not text.startswith("http"):
        return
    
    # Check daily limit before processing
    allowed, remaining = check_daily_limit(uid)
    if not allowed:
        return await m.reply_text(
            f"❌ **Daily limit (5GB) exceeded!**\n\n"
            f"📊 Remaining: {human_size(remaining)}\n"
            f"🕐 Resets at midnight\n\n"
            f"💡 Contact admin for PRO access (unlimited)"
        )
    
    status = await m.reply_text("🔍 **Analyzing link...**", reply_markup=cancel_kb())
    session_set(uid, {"url": text, "cancel": False})
    
    if is_yt(text) and YOUTUBE_API_KEY:
        video_id = extract_video_id(text)
        if video_id:
            video_info = await get_youtube_video_info(video_id)
            if video_info:
                await safe_edit(status,
                    f"🎬 **YouTube Video**\n\n"
                    f"📺 **{video_info['title'][:50]}**\n"
                    f"👤 {video_info['channel']}\n"
                    f"⏱️ {video_info['duration_str']} • 👁️ {video_info['views']:,}\n\n"
                    f"Choose quality:",
                    yt_kb()
                )
                return
    
    if is_yt(text) or is_instagram(text):
        platform = "🎬 YouTube" if is_yt(text) else "📸 Instagram"
        return await safe_edit(status, f"{platform}\n\nChoose quality:", yt_kb())
    
    try:
        await safe_edit(status, "⬇️ **Downloading...**", cancel_kb())
        path, title = await download_direct(uid, text, status)
        
        name = os.path.basename(path)
        size = os.path.getsize(path)
        session_set(uid, {"url": text, "path": path, "name": name, "ext": get_ext(name), "size": size, "cancel": False})
        await safe_edit(status, f"✅ **Done!**\n\n📄 `{name}`\n📦 {human_size(size)}", upload_kb())
    except Exception as e:
        session_clear(uid)
        if "CANCELLED" in str(e):
            await safe_edit(status, "❌ Cancelled!", None)
        else:
            await safe_edit(status, f"❌ {str(e)[:200]}", None)

@app.on_message((filters.video | filters.document | filters.audio) & filters.private)
async def on_file(_, m):
    uid = m.from_user.id
    user = user_get(uid)
    
    if user.get("is_banned"):
        return
    if uid != OWNER_ID and not await is_subscribed(uid):
        return await m.reply_text("⚠️ **Join channel first!**", reply_markup=join_kb())
    
    media = m.video or m.document or m.audio
    file_size = getattr(media, "file_size", 0) or 0
    
    # Check daily limit
    allowed, remaining = check_daily_limit(uid, file_size)
    if not allowed:
        return await m.reply_text(
            f"❌ **Daily limit exceeded!**\n\n"
            f"📦 File size: {human_size(file_size)}\n"
            f"📊 Remaining: {human_size(remaining)}"
        )
    
    status = await m.reply_text("⬇️ **Downloading...**", reply_markup=cancel_kb())
    session_set(uid, {"cancel": False})
    
    try:
        name = safe_name(getattr(media, "file_name", None) or f"file_{int(time.time())}")
        path = os.path.join(DOWNLOAD_DIR, name)
        await m.download(path)
        size = os.path.getsize(path)
        session_set(uid, {"path": path, "name": name, "ext": get_ext(name), "size": size, "cancel": False})
        await safe_edit(status, f"✅ `{name}`\n📦 {human_size(size)}", upload_kb())
    except Exception as e:
        session_clear(uid)
        await safe_edit(status, f"❌ {str(e)[:80]}", None)

@app.on_message(filters.photo & filters.private)
async def on_photo(_, m):
    uid = m.from_user.id
    user = user_get(uid)
    
    if user.get("is_banned"):
        return
    if uid != OWNER_ID and not await is_subscribed(uid):
        return await m.reply_text("⚠️ **Join channel first!**", reply_markup=join_kb())
    
    path = os.path.join(THUMB_DIR, f"{uid}.jpg")
    await m.download(path)
    user["thumb"] = path
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
    
    if data == "verify_join":
        if uid == OWNER_ID:
            await cb.answer("👑 You're the owner!", show_alert=True)
            return await safe_edit(cb.message, "👑 **Welcome Boss!**", menu_kb(uid))
        
        if await is_subscribed(uid):
            await cb.answer("✅ Verified!", show_alert=True)
            used = user.get("used", 0)
            remaining = DAILY_LIMIT - used
            return await safe_edit(cb.message,
                f"✅ **Welcome {cb.from_user.first_name}!**\n\n"
                f"🚀 Send any video link to download.\n\n"
                f"📊 Daily limit: {human_size(remaining)} remaining",
                menu_kb(uid)
            )
        else:
            await cb.answer("❌ You haven't joined yet!", show_alert=True)
            return
    
    # Owner bypasses subscription for all actions
    if data not in ["close"] and uid != OWNER_ID:
        if not await is_subscribed(uid):
            await cb.answer("⚠️ Join channel first!", show_alert=True)
            return await safe_edit(cb.message, "⚠️ **Join channel first!**", join_kb())
    
    if data == "close":
        try:
            await cb.message.delete()
        except:
            pass
        return
    
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
        return await safe_edit(cb.message, "❌ Cancelled!", None)
    
    if data == "back":
        user["state"] = "none"
        db_save()
        return await safe_edit(cb.message, "📋 Menu", menu_kb(uid))
    
    if data == "menu_thumb":
        return await safe_edit(cb.message, "🖼️ Send a photo to set as thumbnail", thumb_kb())
    
    if data == "menu_stats":
        if uid == OWNER_ID:
            expired, expiry, message = check_cookies_expiry()
            expiry_str = expiry.strftime('%Y-%m-%d') if expiry else "Unknown"
            return await safe_edit(cb.message,
                f"📊 **Bot Stats**\n\n"
                f"👥 Total Users: {len(DB['users'])}\n"
                f"🔑 YT API: {'✅' if YOUTUBE_API_KEY else '❌'}\n"
                f"🍪 Cookies: {'❌ EXPIRED' if expired else '✅ Valid'}\n"
                f"📅 Cookie Expiry: `{expiry_str}`\n"
                f"📝 Status: {message}",
                menu_kb(uid))
        
        used = user.get("used", 0)
        remaining = DAILY_LIMIT - used
        is_pro = user.get("is_pro", False)
        return await safe_edit(cb.message,
            f"📊 **Your Stats**\n\n"
            f"📦 Used today: {human_size(used)}\n"
            f"📊 Remaining: {'∞ Unlimited' if is_pro else human_size(remaining)}\n"
            f"👑 Status: {'PRO ⭐' if is_pro else 'Free'}",
            menu_kb(uid))
    
    if data == "menu_help":
        return await safe_edit(cb.message,
            "❓ **How to use:**\n\n"
            "1. Send a video link\n"
            "2. Choose quality\n"
            "3. Wait for download\n"
            "4. Upload as file or video\n\n"
            "📸 Send photo to set thumbnail!\n\n"
            f"📊 Daily limit: 5GB (PRO: unlimited)",
            menu_kb(uid))
    
    if data == "thumb_view":
        t = user.get("thumb")
        if t and os.path.exists(t):
            await cb.message.reply_photo(t)
        else:
            await cb.answer("No thumbnail set!", show_alert=True)
        return
    
    if data == "thumb_del":
        t = user.get("thumb")
        if t and os.path.exists(t):
            os.remove(t)
        user["thumb"] = None
        db_save()
        return await safe_edit(cb.message, "✅ Deleted!", thumb_kb())
    
    # Admin Panel
    if data == "admin":
        if uid != OWNER_ID:
            return
        return await safe_edit(cb.message, "⚙️ **Admin Panel**", admin_kb())
    
    if data == "adm_stats":
        if uid != OWNER_ID:
            return
        banned = sum(1 for u in DB["users"].values() if u.get("is_banned"))
        pro = sum(1 for u in DB["users"].values() if u.get("is_pro"))
        expired, expiry, message = check_cookies_expiry()
        expiry_str = expiry.strftime('%Y-%m-%d %H:%M') if expiry else "Unknown"
        return await safe_edit(cb.message,
            f"📊 **Admin Statistics**\n\n"
            f"👥 Total: {len(DB['users'])}\n"
            f"👑 PRO: {pro}\n"
            f"🚫 Banned: {banned}\n\n"
            f"🔑 YT API: {'✅' if YOUTUBE_API_KEY else '❌'}\n"
            f"🍪 Cookies: {'❌ EXPIRED' if expired else '✅ Valid'}\n"
            f"📅 Expiry: `{expiry_str}`\n"
            f"📝 {message}",
            admin_kb())
    
    if data == "adm_cookies":
        if uid != OWNER_ID:
            return
        expired, expiry, message = check_cookies_expiry()
        expiry_str = expiry.strftime('%Y-%m-%d %H:%M') if expiry else "Unknown"
        
        status_icon = "❌" if expired else "✅"
        yt_status = "🔴 PAUSED" if expired else "🟢 Active"
        
        return await safe_edit(cb.message,
            f"🍪 **Cookie Status**\n\n"
            f"{status_icon} Status: {'EXPIRED' if expired else 'Valid'}\n"
            f"📅 Expiry: `{expiry_str}`\n"
            f"📝 {message}\n\n"
            f"🎬 YouTube Downloads: {yt_status}\n\n"
            f"**To update cookies:**\n"
            f"1. Export from YouTube (logged in)\n"
            f"2. Update `COOKIES` in Koyeb\n"
            f"3. Redeploy service",
            admin_kb())
    
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
        sent = 0
        for u in DB["users"]:
            if not DB["users"][u].get("is_banned"):
                try:
                    await app.send_message(int(u), text)
                    sent += 1
                    await asyncio.sleep(0.05)
                except:
                    pass
        user["bc"] = ""
        db_save()
        return await safe_edit(cb.message, f"✅ Sent to {sent} users!", admin_kb())
    
    if data == "bc_cancel":
        user["state"] = "none"
        user["bc"] = ""
        db_save()
        return await safe_edit(cb.message, "❌ Cancelled", admin_kb())
    
    if data == "adm_pro":
        if uid != OWNER_ID:
            return
        user["state"] = "addpro"
        db_save()
        return await safe_edit(cb.message, "👑 Send user ID to add PRO:", cancel_kb())
    
    if data == "adm_ban":
        if uid != OWNER_ID:
            return
        user["state"] = "ban"
        db_save()
        return await safe_edit(cb.message, "🚫 Send user ID to ban:", cancel_kb())
    
    if data == "adm_unban":
        if uid != OWNER_ID:
            return
        user["state"] = "unban"
        db_save()
        return await safe_edit(cb.message, "✅ Send user ID to unban:", cancel_kb())
    
    # Quality selection
    if data.startswith("yt_"):
        if not sess or not sess.get("url"):
            return await safe_edit(cb.message, "❌ Session expired!", None)
        
        # Check daily limit
        allowed, remaining = check_daily_limit(uid)
        if not allowed:
            return await safe_edit(cb.message,
                f"❌ **Daily limit exceeded!**\n\n"
                f"📊 Remaining: {human_size(remaining)}",
                None)
        
        quality = data.replace("yt_", "")
        quality_display = quality.upper()
        if quality == "mp3":
            quality_display = "MP3 192kbps"
        elif quality == "mp3_320":
            quality_display = "MP3 320kbps"
        elif quality.isdigit():
            quality_display = f"{quality}p"
        
        try:
            await safe_edit(cb.message, f"⬇️ **Downloading {quality_display}...**", cancel_kb())
            path, title = await download_video(uid, sess["url"], cb.message, quality)
            name = os.path.basename(path)
            size = os.path.getsize(path)
            session_set(uid, {"url": sess["url"], "path": path, "name": name, "ext": get_ext(name), "size": size, "cancel": False})
            await safe_edit(cb.message, f"✅ **Done!**\n\n📄 `{name}`\n📦 {human_size(size)}", upload_kb())
        except Exception as e:
            session_clear(uid)
            if "CANCELLED" in str(e):
                await safe_edit(cb.message, "❌ Cancelled!", None)
            else:
                await safe_edit(cb.message, f"❌ **Failed**\n\n{str(e)[:200]}", None)
        return
    
    # Rename options
    if data == "rename":
        if sess:
            return await safe_edit(cb.message, f"✏️ Current: `{sess['name']}`", rename_kb())
    
    if data == "ren_def":
        if sess:
            return await safe_edit(cb.message, f"📝 `{sess['name']}`", upload_kb())
    
    if data == "ren_cust":
        if sess:
            user["state"] = "rename"
            db_save()
            return await safe_edit(cb.message, "✏️ Send new filename:", cancel_kb())
    
    if data == "back_up":
        if sess:
            return await safe_edit(cb.message, f"📄 `{sess['name']}`\n📦 {human_size(sess.get('size', 0))}", upload_kb())
    
    # Upload
    if data in ["up_file", "up_video"]:
        if not sess or not sess.get("path") or not os.path.exists(sess["path"]):
            session_clear(uid)
            return await safe_edit(cb.message, "❌ File not found!", None)
        
        try:
            await safe_edit(cb.message, "📤 **Uploading...**", cancel_kb())
            await do_upload(uid, cb.message, sess["path"], sess["name"], data == "up_video")
            try:
                os.remove(sess["path"])
            except:
                pass
            session_clear(uid)
            await safe_edit(cb.message, "✅ **Upload complete!**", menu_kb(uid))
        except Exception as e:
            if "CANCELLED" not in str(e):
                await safe_edit(cb.message, f"❌ {str(e)[:100]}", None)
            else:
                await safe_edit(cb.message, "❌ Cancelled!", None)

# =======================
# COOKIE EXPIRY CHECKER (Background Task)
# =======================
async def cookie_expiry_checker():
    """Background task to check cookie expiry periodically"""
    global COOKIE_WARNING_SENT
    
    while True:
        await asyncio.sleep(3600)  # Check every hour
        
        expired, expiry, message = check_cookies_expiry()
        
        if expired and not COOKIE_WARNING_SENT:
            await notify_owner_cookies_expiry()
        
        # Reset warning flag at midnight for daily reminder
        if datetime.now().hour == 0:
            COOKIE_WARNING_SENT = False
            DB["cookie_warning_sent"] = False
            db_save()

# =======================
# HEALTH CHECK & MAIN
# =======================
async def health(_):
    return web.Response(text="OK")

async def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(THUMB_DIR, exist_ok=True)
    db_load()
    setup_cookies()
    
    await app.start()
    
    # Notify owner if cookies are expired on startup
    if COOKIES_EXPIRED:
        await notify_owner_cookies_expiry()
    
    print("=" * 60)
    print("✅ BOT STARTED SUCCESSFULLY!")
    print(f"👑 Owner: {OWNER_ID}")
    print(f"👥 Users: {len(DB['users'])}")
    print(f"🔑 YT API: {'Active' if YOUTUBE_API_KEY else 'Not set'}")
    print(f"🍪 Cookies: {'EXPIRED ⚠️' if COOKIES_EXPIRED else 'Valid ✅'}")
    if COOKIES_EXPIRY_DATE:
        print(f"📅 Cookie Expiry: {COOKIES_EXPIRY_DATE.strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    # Start background cookie checker
    asyncio.create_task(cookie_expiry_checker())
    
    srv = web.Application()
    srv.add_routes([web.get("/", health)])
    runner = web.AppRunner(srv)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()
    
    await idle()
    await app.stop()

if __name__ == "__main__":
    app.run(main())
