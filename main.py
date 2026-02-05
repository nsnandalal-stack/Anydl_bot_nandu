import os
import re
import time
import json
import shutil
import asyncio
import base64
from datetime import date
from aiohttp import web, ClientSession, ClientTimeout

from pyrogram import Client, filters, types, enums, idle, errors
from yt_dlp import YoutubeDL

# =======================
# CONFIG
# =======================
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
OWNER_ID = os.getenv("OWNER_ID")

# Cookies - multiple sources
COOKIES_BASE64 = os.getenv("COOKIES_BASE64")
COOKIES_TXT = os.getenv("COOKIES_TXT")
COOKIES = os.getenv("COOKIES")

print("=" * 60)
print("🔍 ENVIRONMENT CHECK")
print("=" * 60)
print(f"API_ID: {'✅' if API_ID else '❌'}")
print(f"API_HASH: {'✅' if API_HASH else '❌'}")
print(f"BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'}")
print(f"CHANNEL_ID: {'✅' if CHANNEL_ID else '❌'}")
print(f"OWNER_ID: {'✅' if OWNER_ID else '❌'}")
print(f"YOUTUBE_API_KEY: {'✅' if YOUTUBE_API_KEY else '⚠️'}")
print(f"COOKIES_BASE64: {'✅' if COOKIES_BASE64 else '⚠️'}")
print("=" * 60)

if not all([API_ID, API_HASH, BOT_TOKEN, CHANNEL_ID, OWNER_ID]):
    print("❌ Missing required variables!")
    exit(1)

try:
    API_ID = int(API_ID)
    CHANNEL_ID = int(CHANNEL_ID)
    OWNER_ID = int(OWNER_ID)
    print(f"✅ Validated! Owner: {OWNER_ID}\n")
except ValueError:
    print("❌ IDs must be numbers!")
    exit(1)

INVITE_LINK = "https://t.me/+eooytvOAwjc0NTI1"
DOWNLOAD_DIR = "/tmp/downloads"
THUMB_DIR = "/tmp/thumbnails"
DB_FILE = "/tmp/bot_db.json"
DAILY_LIMIT = 5 * 1024 * 1024 * 1024

COOKIES_PATH = None
COOKIES_EXPIRED = False
COOKIES_EXPIRY_NOTIFIED = False

# =======================
# KEEP-ALIVE
# =======================
async def keep_alive_ping():
    await asyncio.sleep(60)
    koyeb_url = os.getenv("KOYEB_APP_URL")
    
    while True:
        try:
            if koyeb_url:
                async with ClientSession() as session:
                    try:
                        async with session.get(koyeb_url, timeout=ClientTimeout(total=10)) as resp:
                            if resp.status == 200:
                                print(f"⏰ Keep-alive: OK")
                    except:
                        pass
            await asyncio.sleep(300)
        except:
            await asyncio.sleep(300)

# =======================
# COOKIE SETUP
# =======================
def setup_cookies():
    global COOKIES_PATH
    
    cookie_content = None
    source = None
    
    # Try base64 first (most reliable)
    if COOKIES_BASE64:
        try:
            cookie_content = base64.b64decode(COOKIES_BASE64).decode('utf-8')
            source = "COOKIES_BASE64"
            print(f"📝 Using COOKIES_BASE64")
        except Exception as e:
            print(f"⚠️ Base64 decode failed: {e}")
    
    # Fallback to direct text
    if not cookie_content and COOKIES_TXT:
        cookie_content = COOKIES_TXT
        source = "COOKIES_TXT"
        print(f"📝 Using COOKIES_TXT")
    
    if not cookie_content and COOKIES:
        cookie_content = COOKIES
        source = "COOKIES"
        print(f"📝 Using COOKIES variable")
    
    if not cookie_content:
        print("⚠️ No cookies - age-restricted videos may fail\n")
        return None
    
    try:
        os.makedirs("/tmp/cookies", exist_ok=True)
        COOKIES_PATH = "/tmp/cookies/cookies.txt"
        
        # Parse lines
        if '\\n' in cookie_content and '\n' not in cookie_content:
            raw_lines = cookie_content.split('\\n')
        elif '\r\n' in cookie_content:
            raw_lines = cookie_content.split('\r\n')
        elif '\n' in cookie_content:
            raw_lines = cookie_content.split('\n')
        else:
            raw_lines = [cookie_content]
        
        lines_to_write = []
        cookie_count = 0
        
        for line in raw_lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith('#'):
                lines_to_write.append(line)
                continue
            if '\t' in line:
                parts = line.split('\t')
                if len(parts) >= 6:
                    lines_to_write.append(line)
                    cookie_count += 1
        
        # Add header if missing
        if not any('Netscape HTTP Cookie File' in l for l in lines_to_write):
            lines_to_write.insert(0, "# Netscape HTTP Cookie File")
            lines_to_write.insert(1, "# This file is generated")
        
        # Write file
        with open(COOKIES_PATH, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines_to_write))
            f.write('\n')
        
        file_size = os.path.getsize(COOKIES_PATH)
        
        print("=" * 60)
        print(f"✅ COOKIES CREATED")
        print(f"📂 {COOKIES_PATH}")
        print(f"📝 Entries: {cookie_count}")
        print(f"📦 Size: {file_size} bytes")
        print(f"🔗 Source: {source}")
        
        if cookie_count > 0:
            with open(COOKIES_PATH, 'r') as f:
                for line in f:
                    if line.strip() and not line.startswith('#') and '\t' in line:
                        parts = line.split('\t')
                        print(f"🍪 Sample: {parts[0]} - {parts[5] if len(parts) > 5 else 'unknown'}")
                        break
        else:
            print("\n❌ NO VALID COOKIES!")
            print("Use: base64 -w 0 cookies.txt")
            print("Set: COOKIES_BASE64 in Koyeb")
        
        print("=" * 60)
        print()
        
        return COOKIES_PATH if cookie_count > 0 else None
        
    except Exception as e:
        print(f"❌ Cookie setup failed: {e}")
        return None

# =======================
# COOKIE EXPIRATION
# =======================
async def check_cookie_expiration():
    global COOKIES_EXPIRED
    
    if not COOKIES_PATH or not os.path.exists(COOKIES_PATH):
        return False
    
    try:
        current_time = int(time.time())
        with open(COOKIES_PATH, 'r') as f:
            lines = f.readlines()
        
        expired_count = 0
        total_cookies = 0
        
        for line in lines:
            if line.startswith('#') or not line.strip() or '\t' not in line:
                continue
            parts = line.strip().split('\t')
            if len(parts) >= 5:
                total_cookies += 1
                try:
                    expiry = int(parts[4])
                    if expiry < current_time + (7 * 24 * 3600):
                        expired_count += 1
                except:
                    continue
        
        if total_cookies == 0:
            return False
        
        if expired_count > (total_cookies * 0.5):
            COOKIES_EXPIRED = True
            return True
        
        COOKIES_EXPIRED = False
        return False
    except:
        return False

async def notify_admin_cookies_expired():
    global COOKIES_EXPIRY_NOTIFIED
    if COOKIES_EXPIRY_NOTIFIED:
        return
    try:
        await app.send_message(
            OWNER_ID,
            "🚨 **Cookies Expired!**\n\n"
            "Update COOKIES_BASE64 in Koyeb and restart."
        )
        COOKIES_EXPIRY_NOTIFIED = True
    except:
        pass

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
            "thumb": None,
            "state": "none",
            "used": 0,
            "reset": date.today().isoformat(),
            "is_pro": (uid == OWNER_ID),
            "is_banned": False,
            "verified": (uid == OWNER_ID)
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
    if uid == OWNER_ID:
        return True
    try:
        member = await app.get_chat_member(CHANNEL_ID, uid)
        return member.status in (
            enums.ChatMemberStatus.MEMBER,
            enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.OWNER
        )
    except:
        return True

# =======================
# YOUTUBE API
# =======================
async def get_youtube_info(video_id: str):
    if not YOUTUBE_API_KEY:
        return None
    try:
        url = "https://www.googleapis.com/youtube/v3/videos"
        params = {
            "part": "snippet,contentDetails,statistics",
            "id": video_id,
            "key": YOUTUBE_API_KEY
        }
        timeout = ClientTimeout(total=10)
        async with ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if not data.get("items"):
                    return None
                item = data["items"][0]
                snippet = item.get("snippet", {})
                stats = item.get("statistics", {})
                return {
                    "title": snippet.get("title", "Unknown"),
                    "duration": item.get("contentDetails", {}).get("duration", ""),
                    "views": stats.get("viewCount", "0"),
                    "channel": snippet.get("channelTitle", "Unknown"),
                }
    except:
        return None

def parse_youtube_duration(duration: str) -> int:
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds

# =======================
# KEYBOARDS
# =======================
def join_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("📢 Join Channel", url=INVITE_LINK)],
        [types.InlineKeyboardButton("✅ I've Joined", callback_data="check_join")]
    ])

def verification_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("✅ I'm Human", callback_data="verify_human")],
        [types.InlineKeyboardButton("🔄 Cancel", callback_data="close")]
    ])

def maintenance_kb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("🔄 Try Again", callback_data="retry_maintenance")]
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
# DOWNLOAD
# =======================
async def download_from_url(uid, url, msg, filename=None, quality="720"):
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
            if "CANCELLED" in str(e):
                raise
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
                continue
            raise

async def download_ytdlp(uid, url, msg, quality="720"):
    await safe_edit(msg, "🔄 **Downloading...**", cancel_kb())
    
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
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
                "player_skip": ["webpage"],
            }
        },
        "http_headers": {
            "User-Agent": "com.google.android.youtube/17.36.4 (Linux; U; Android 12; GB) gzip",
        }
    }
    
    if COOKIES_PATH and os.path.exists(COOKIES_PATH):
        opts["cookiefile"] = COOKIES_PATH
        print(f"🍪 Using cookies")
    
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
            opts["format"] = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        else:
            opts["format"] = f"bestvideo[height<={target}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={target}]+bestaudio/best[height<={target}]/best"
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

async def download_cobalt(uid, url, msg, quality="720"):
    await safe_edit(msg, "🔄 **Trying Cobalt...**", cancel_kb())
    
    cobalt_instances = ["https://co.wuk.sh", "https://cobalt-api.kwiatekmiki.com"]
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
        except Exception as e:
            print(f"Cobalt {instance} failed: {e}")
            continue
    
    raise Exception("Cobalt failed")

async def download_video(uid, url, msg, quality="720"):
    errors = []
    
    try:
        return await download_cobalt(uid, url, msg, quality)
    except Exception as e:
        if "CANCELLED" in str(e): raise
        errors.append(f"Cobalt: {str(e)[:50]}")
    
    try:
        return await download_ytdlp(uid, url, msg, quality)
    except Exception as e:
        if "CANCELLED" in str(e): raise
        errors.append(f"yt-dlp: {str(e)[:50]}")
    
    raise Exception("Failed:\n" + "\n".join(errors[:2]))

async def download_direct(uid, url, msg):
    return await download_from_url(uid, url, msg, None, "720")

# =======================
# SCREENSHOTS
# =======================
async def make_ss(path, count=5):
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
        await safe_edit(msg, "📸 Screenshots...", None)
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
        remaining = DAILY_LIMIT - user["used"]
        if remaining > 0:
            await app.send_message(uid, f"📊 {human_size(user['used'])}/5GB used")

# =======================
# BOT
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
        f"Send a video link to start.",
        reply_markup=menu_kb(m.from_user.id)
    )

@app.on_message(filters.command("cookies") & filters.private)
async def cmd_cookies(_, m):
    if m.from_user.id != OWNER_ID:
        return
    
    msg = "🍪 **Cookie Debug**\n\n"
    
    if COOKIES_BASE64:
        msg += f"✅ COOKIES_BASE64: SET\n"
    if COOKIES_TXT:
        msg += f"✅ COOKIES_TXT: SET ({len(COOKIES_TXT)} chars)\n"
    if COOKIES:
        msg += f"✅ COOKIES: SET ({len(COOKIES)} chars)\n"
    
    if not any([COOKIES_BASE64, COOKIES_TXT, COOKIES]):
        msg += "❌ NO cookie variables\n"
    
    msg += "\n"
    
    if COOKIES_PATH and os.path.exists(COOKIES_PATH):
        size = os.path.getsize(COOKIES_PATH)
        msg += f"✅ File: {COOKIES_PATH}\n"
        msg += f"📦 Size: {size} bytes\n"
        
        with open(COOKIES_PATH, 'r') as f:
            content = f.read()
            lines = [l for l in content.split('\n') if l.strip() and not l.startswith('#') and '\t' in l]
            msg += f"📝 Valid cookies: {len(lines)}\n\n"
            
            if lines:
                first = lines[0].split('\t')
                msg += f"**First cookie:**\n"
                msg += f"Domain: `{first[0]}`\n"
                if len(first) > 5:
                    msg += f"Name: `{first[5]}`\n"
    else:
        msg += "❌ No cookie file\n"
    
    await m.reply_text(msg)

@app.on_message(filters.text & filters.private & ~filters.command(["start", "cookies"]))
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
        return await m.reply_text(f"✅ `{new}`", reply_markup=upload_kb())
    
    if user.get("state") == "broadcast" and uid == OWNER_ID:
        user["state"] = "none"
        user["bc"] = text
        db_save()
        return await m.reply_text(f"📢 Preview:\n\n{text}", reply_markup=bc_kb())
    
    if user.get("state") == "addpro" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            target_user = user_get(int(text))
            target_user["is_pro"] = True
            db_save()
            return await m.reply_text(f"✅ PRO!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid!", reply_markup=admin_kb())
    
    if user.get("state") == "ban" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            target_user = user_get(int(text))
            target_user["is_banned"] = True
            db_save()
            return await m.reply_text(f"✅ Banned!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid!", reply_markup=admin_kb())
    
    if user.get("state") == "unban" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            target_user = user_get(int(text))
            target_user["is_banned"] = False
            db_save()
            return await m.reply_text(f"✅ Unbanned!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid!", reply_markup=admin_kb())
    
    if not text.startswith("http"):
        return
    
    # Owner bypass
    if uid != OWNER_ID:
        if await check_cookie_expiration():
            await notify_admin_cookies_expired()
            return await m.reply_text("🔧 Maintenance mode", reply_markup=maintenance_kb())
        
        if not await is_subscribed(uid):
            return await m.reply_text("⚠️ Join channel!", reply_markup=join_kb())
        
        if not user.get("verified"):
            return await m.reply_text("🤖 Verify first!", reply_markup=verification_kb())
        
        if not user.get("is_pro"):
            if user.get("used", 0) >= DAILY_LIMIT:
                return await m.reply_text(f"📊 Limit: {human_size(user.get('used', 0))}/5GB", reply_markup=menu_kb(uid))
    
    status = await m.reply_text("🔍 Analyzing...", reply_markup=cancel_kb())
    session_set(uid, {"url": text, "cancel": False})
    
    # YouTube
    if is_yt(text):
        video_id = extract_video_id(text)
        
        if video_id and YOUTUBE_API_KEY:
            info = await get_youtube_info(video_id)
            
            if info:
                duration_sec = parse_youtube_duration(info.get("duration", ""))
                duration_str = human_time(duration_sec)
                
                await safe_edit(status,
                    f"🎬 **YouTube**\n\n"
                    f"📺 {info['title'][:50]}...\n"
                    f"👤 {info['channel']}\n"
                    f"⏱️ {duration_str}\n"
                    f"👁️ {int(info['views']):,} views\n\n"
                    f"Choose quality:",
                    yt_kb()
                )
                return
        
        return await safe_edit(status, "🎬 YouTube - Choose quality:", yt_kb())
    
    # Instagram
    if is_instagram(text):
        return await safe_edit(status, "📸 Instagram - Choose quality:", yt_kb())
    
    # Direct
    try:
        await safe_edit(status, "⬇️ Downloading...", cancel_kb())
        path, title = await download_direct(uid, text, status)
        
        name = os.path.basename(path)
        size = os.path.getsize(path)
        
        if uid != OWNER_ID and not user.get("is_pro"):
            if user.get("used", 0) + size > DAILY_LIMIT:
                os.remove(path)
                session_clear(uid)
                return await safe_edit(status, f"❌ Too large! Remaining: {human_size(DAILY_LIMIT - user.get('used', 0))}", None)
        
        session_set(uid, {"url": text, "path": path, "name": name, "ext": get_ext(name), "size": size, "cancel": False})
        await safe_edit(status, f"✅ Done!\n\n📄 `{name}`\n📦 {human_size(size)}", upload_kb())
        
    except Exception as e:
        session_clear(uid)
        await safe_edit(status, f"❌ {str(e)[:200]}" if "CANCELLED" not in str(e) else "❌ Cancelled!", None)

@app.on_message((filters.video | filters.document | filters.audio) & filters.private)
async def on_file(_, m):
    uid = m.from_user.id
    user = user_get(uid)
    
    if user.get("is_banned"):
        return
    
    if uid != OWNER_ID:
        if await check_cookie_expiration():
            await notify_admin_cookies_expired()
            return await m.reply_text("🔧 Maintenance", reply_markup=maintenance_kb())
        
        if not await is_subscribed(uid):
            return await m.reply_text("⚠️ Join channel!", reply_markup=join_kb())
        
        if not user.get("verified"):
            return await m.reply_text("🤖 Verify!", reply_markup=verification_kb())
        
        if not user.get("is_pro"):
            if user.get("used", 0) >= DAILY_LIMIT:
                return await m.reply_text(f"📊 Limit reached", reply_markup=menu_kb(uid))
    
    media = m.video or m.document or m.audio
    file_size = getattr(media, "file_size", 0)
    
    if uid != OWNER_ID and not user.get("is_pro"):
        if user.get("used", 0) + file_size > DAILY_LIMIT:
            return await m.reply_text(f"❌ Too large!", reply_markup=menu_kb(uid))
    
    status = await m.reply_text("⬇️ Downloading...", reply_markup=cancel_kb())
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
    if user_get(uid).get("is_banned"):
        return
    
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
            if user.get("verified"):
                await cb.answer("✅ Already verified!", show_alert=True)
                return await safe_edit(cb.message, "✅ All set!", menu_kb(uid))
            await cb.answer("✅ Now verify!", show_alert=True)
            return await safe_edit(cb.message, "✅ Joined! Now verify:", verification_kb())
        else:
            await cb.answer("❌ Not joined!", show_alert=True)
            return
    
    if data == "verify_human":
        if uid != OWNER_ID and not await is_subscribed(uid):
            await cb.answer("❌ Join first!", show_alert=True)
            return await safe_edit(cb.message, "⚠️ Join channel!", join_kb())
        
        user["verified"] = True
        db_save()
        await cb.answer("🎉 Verified!", show_alert=True)
        return await safe_edit(cb.message, "🎉 Verified! Send a link.", menu_kb(uid))
    
    if data == "retry_maintenance":
        if await check_cookie_expiration():
            await cb.answer("Still under maintenance", show_alert=True)
            return
        await cb.answer("✅ Back online!", show_alert=True)
        return await safe_edit(cb.message, "✅ Bot is back!", menu_kb(uid))
    
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
        return await safe_edit(cb.message, "🖼️ Send photo for thumbnail", thumb_kb())
    
    if data == "menu_stats":
        if uid == OWNER_ID:
            cookie_status = "✅" if COOKIES_PATH and not COOKIES_EXPIRED else "❌" if COOKIES_PATH else "⚠️"
            verified = sum(1 for u in DB['users'].values() if u.get('verified'))
            pro = sum(1 for u in DB['users'].values() if u.get('is_pro'))
            return await safe_edit(cb.message,
                f"📊 **Stats**\n\n"
                f"👥 Users: {len(DB['users'])}\n"
                f"✅ Verified: {verified}\n"
                f"👑 PRO: {pro}\n"
                f"🍪 Cookies: {cookie_status}",
                menu_kb(uid))
        else:
            used = user.get("used", 0)
            remaining = DAILY_LIMIT - used
            percentage = (used / DAILY_LIMIT * 100) if DAILY_LIMIT > 0 else 0
            
            if user.get("is_pro"):
                status_text = f"📊 **PRO**\n\n👑 Unlimited!\n📈 {human_size(used)}"
            else:
                status_text = (
                    f"📊 **Usage**\n\n"
                    f"📈 {human_size(used)}/5GB\n"
                    f"📉 Remaining: {human_size(remaining)}\n"
                    f"`[{progress_bar(percentage)}]` {percentage:.1f}%"
                )
            return await safe_edit(cb.message, status_text, menu_kb(uid))
    
    if data == "menu_help":
        return await safe_edit(cb.message,
            "❓ **Help**\n\n"
            "1. Send link\n"
            "2. Choose quality\n"
            "3. Upload\n\n"
            "Supports: YouTube, Instagram, direct links",
            menu_kb(uid))
    
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
    
    if data == "admin":
        if uid != OWNER_ID:
            return
        return await safe_edit(cb.message, "⚙️ Admin", admin_kb())
    
    if data == "adm_stats":
        if uid != OWNER_ID:
            return
        cookie_status = "✅" if COOKIES_PATH and not COOKIES_EXPIRED else "❌" if COOKIES_PATH else "⚠️"
        verified = sum(1 for u in DB['users'].values() if u.get('verified'))
        pro = sum(1 for u in DB['users'].values() if u.get('is_pro'))
        banned = sum(1 for u in DB['users'].values() if u.get('is_banned'))
        return await safe_edit(cb.message,
            f"📊 **Stats**\n\n"
            f"👥 {len(DB['users'])}\n"
            f"✅ Verified: {verified}\n"
            f"👑 PRO: {pro}\n"
            f"🚫 Banned: {banned}\n"
            f"🍪 {cookie_status}",
            admin_kb())
    
    if data == "adm_cookies":
        if uid != OWNER_ID:
            return
        if not COOKIES_PATH:
            return await safe_edit(cb.message, "🍪 No cookies", admin_kb())
        is_expired = await check_cookie_expiration()
        if is_expired:
            return await safe_edit(cb.message, "🍪 **EXPIRED!**\n\nUpdate COOKIES_BASE64 in Koyeb.", admin_kb())
        else:
            with open(COOKIES_PATH, 'r') as f:
                count = sum(1 for line in f if line.strip() and not line.startswith('#') and '\t' in line)
            return await safe_edit(cb.message, f"🍪 **Valid**\n\n{count} cookies loaded", admin_kb())
    
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
                    await asyncio.sleep(0.05)
                except:
                    pass
        user["bc"] = ""
        db_save()
        return await safe_edit(cb.message, f"✅ Sent to {sent}!", admin_kb())
    
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
    
    if data.startswith("yt_"):
        if not sess or not sess.get("url"):
            return await safe_edit(cb.message, "❌ Expired!", None)
        
        quality = data.replace("yt_", "")
        quality_display = quality.upper()
        if quality == "mp3":
            quality_display = "MP3 192kbps"
        elif quality == "mp3_320":
            quality_display = "MP3 320kbps"
        elif quality.isdigit():
            quality_display = f"{quality}p"
        
        try:
            await safe_edit(cb.message, f"⬇️ {quality_display}...", cancel_kb())
            path, title = await download_video(uid, sess["url"], cb.message, quality)
            name = os.path.basename(path)
            size = os.path.getsize(path)
            
            if uid != OWNER_ID and not user.get("is_pro"):
                if user.get("used", 0) + size > DAILY_LIMIT:
                    os.remove(path)
                    session_clear(uid)
                    return await safe_edit(cb.message, f"❌ Too large!", None)
            
            session_set(uid, {"url": sess["url"], "path": path, "name": name, "ext": get_ext(name), "size": size, "cancel": False})
            await safe_edit(cb.message, f"✅ Done!\n\n📄 `{name}`\n📦 {human_size(size)}", upload_kb())
        except Exception as e:
            session_clear(uid)
            error_msg = str(e)
            if "CANCELLED" in error_msg:
                await safe_edit(cb.message, "❌ Cancelled!", None)
            else:
                await safe_edit(cb.message, f"❌ Failed\n\n{error_msg[:200]}", None)
        return
    
    if data == "rename":
        if sess:
            return await safe_edit(cb.message, f"✏️ `{sess['name']}`", rename_kb())
    
    if data == "ren_def":
        if sess:
            return await safe_edit(cb.message, f"📝 `{sess['name']}`", upload_kb())
    
    if data == "ren_cust":
        if sess:
            user["state"] = "rename"
            db_save()
            return await safe_edit(cb.message, "✏️ Send new name:", cancel_kb())
    
    if data == "back_up":
        if sess:
            return await safe_edit(cb.message, f"📄 `{sess['name']}`\n📦 {human_size(sess.get('size', 0))}", upload_kb())
    
    if data in ["up_file", "up_video"]:
        if not sess or not sess.get("path") or not os.path.exists(sess["path"]):
            session_clear(uid)
            return await safe_edit(cb.message, "❌ File not found!", None)
        
        try:
            await safe_edit(cb.message, "📤 Uploading...", cancel_kb())
            await do_upload(uid, cb.message, sess["path"], sess["name"], data == "up_video")
            try:
                os.remove(sess["path"])
            except:
                pass
            session_clear(uid)
            await safe_edit(cb.message, "✅ Complete!", menu_kb(uid))
        except Exception as e:
            error_msg = str(e)
            if "CANCELLED" not in error_msg:
                await safe_edit(cb.message, f"❌ {error_msg[:100]}", None)
            else:
                await safe_edit(cb.message, "❌ Cancelled!", None)

# =======================
# MAIN
# =======================
async def health(_):
    return web.Response(text="OK")

async def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(THUMB_DIR, exist_ok=True)
    db_load()
    
    setup_cookies()
    
    cookie_status = "Not Set"
    if COOKIES_PATH:
        is_expired = await check_cookie_expiration()
        cookie_status = "Valid ✅" if not is_expired else "Expiring ⚠️"
    
    print()
    print("=" * 50)
    print("✅ BOT STARTED!")
    print(f"👑 Owner: {OWNER_ID}")
    print(f"🔑 YT API: {'Active' if YOUTUBE_API_KEY else 'Disabled'}")
    print(f"🍪 Cookies: {cookie_status}")
    
    await app.start()
    
    srv = web.Application()
    srv.add_routes([web.get("/", health)])
    runner = web.AppRunner(srv)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()
    
    koyeb_url = os.getenv("KOYEB_APP_URL")
    if koyeb_url:
        print(f"⏰ Keep-alive: {koyeb_url}")
        asyncio.create_task(keep_alive_ping())
    else:
        print("⚠️ Set KOYEB_APP_URL for keep-alive")
    
    print("=" * 50)
    print()
    
    await idle()
    await app.stop()

if __name__ == "__main__":
    app.run(main())
