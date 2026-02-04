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

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")  # YouTube Data API v3 Key

print("=" * 60)
print("🔍 ENVIRONMENT VARIABLES CHECK")
print(f"API_ID: {API_ID}")
print(f"API_HASH: {'SET ✅' if API_HASH else 'NOT SET ❌'}")
print(f"BOT_TOKEN: {'SET ✅' if BOT_TOKEN else 'NOT SET ❌'}")
print(f"CHANNEL_ID: {CHANNEL_ID}")
print(f"YOUTUBE_API_KEY: {'SET ✅' if YOUTUBE_API_KEY else 'NOT SET ⚠️ (will use fallback methods)'}")
print("=" * 60)

if not API_ID or not API_HASH or not BOT_TOKEN or not CHANNEL_ID:
    print("\n❌ ERROR: MISSING REQUIRED ENVIRONMENT VARIABLES!")
    print("Required: API_ID, API_HASH, BOT_TOKEN, CHANNEL_ID")
    print("Optional: YOUTUBE_API_KEY")
    exit(1)

try:
    API_ID = int(API_ID)
    CHANNEL_ID = int(CHANNEL_ID)
    print("✅ Environment variables validated!\n")
except ValueError:
    print("❌ ERROR: API_ID and CHANNEL_ID must be numbers!")
    exit(1)

INVITE_LINK = "https://t.me/+eooytvOAwjc0NTI1"
DOWNLOAD_DIR = "/tmp/downloads"
THUMB_DIR = "/tmp/thumbnails"
DB_FILE = "/tmp/bot_db.json"
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

async def safe_edit(msg, text, kb=None):
    try:
        return await msg.edit_text(text, reply_markup=kb)
    except:
        return msg

async def is_subscribed(uid: int) -> bool:
    """Check if user is subscribed to channel"""
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
        [types.InlineKeyboardButton("✅ Unban", callback_data="adm_unban")],
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
    """Get video info using YouTube Data API v3"""
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
                    print(f"YouTube API error: {resp.status}")
                    return None
                
                data = await resp.json()
                
                if not data.get("items"):
                    return None
                
                item = data["items"][0]
                snippet = item.get("snippet", {})
                content = item.get("contentDetails", {})
                stats = item.get("statistics", {})
                
                # Parse duration (ISO 8601 format: PT1H2M3S)
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
                    "thumbnail": snippet.get("thumbnails", {}).get("high", {}).get("url"),
                    "views": int(stats.get("viewCount", 0)),
                    "likes": int(stats.get("likeCount", 0)),
                    "description": snippet.get("description", "")[:200]
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

async def download_ytdlp(uid: int, url: str, msg, quality: str = "720", video_info: dict = None):
    """Download using yt-dlp"""
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
    """Download using Invidious API"""
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
    """Download using Cobalt API"""
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
    """Main download function - tries all methods"""
    errors_list = []
    video_info = None
    
    # Get video info using YouTube API if available
    if is_yt(url) and YOUTUBE_API_KEY:
        video_id = extract_video_id(url)
        if video_id:
            video_info = await get_youtube_video_info(video_id)
            if video_info:
                print(f"📺 YouTube API: {video_info['title']}")
    
    # Method 1: Invidious (for YouTube)
    if is_yt(url):
        try:
            return await download_invidious(uid, url, msg, quality, video_info)
        except Exception as e:
            if "CANCELLED" in str(e):
                raise
            errors_list.append(f"Invidious: {str(e)[:40]}")
    
    # Method 2: Cobalt
    try:
        return await download_cobalt(uid, url, msg, quality)
    except Exception as e:
        if "CANCELLED" in str(e):
            raise
        errors_list.append(f"Cobalt: {str(e)[:40]}")
    
    # Method 3: yt-dlp
    try:
        return await download_ytdlp(uid, url, msg, quality, video_info)
    except Exception as e:
        if "CANCELLED" in str(e):
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
        return await m.reply_text("🚫 You are banned from using this bot.")
    
    if not await is_subscribed(uid):
        return await m.reply_text(
            "⚠️ **You must join our channel to use this bot!**\n\n"
            "1️⃣ Click 'Join Channel' button below\n"
            "2️⃣ Join the channel\n"
            "3️⃣ Come back and click 'Verify'\n\n"
            "This helps us keep the bot running! 🙏",
            reply_markup=join_kb()
        )
    
    await m.reply_text(
        f"👋 Hi **{m.from_user.first_name}**!\n\n"
        f"🚀 Send any video link to download:\n"
        f"• YouTube\n"
        f"• Instagram\n"
        f"• Direct links\n\n"
        f"Just paste a link and I'll handle the rest!",
        reply_markup=menu_kb(uid)
    )

@app.on_message(filters.text & filters.private & ~filters.command(["start"]))
async def on_text(_, m):
    uid = m.from_user.id
    user = user_get(uid)
    text = m.text.strip()
    
    if user.get("is_banned"):
        return
    
    if not await is_subscribed(uid):
        return await m.reply_text(
            "⚠️ **You must join our channel first!**\n\n"
            "Click 'Join Channel' and then 'Verify'",
            reply_markup=join_kb()
        )
    
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
    
    status = await m.reply_text("🔍 **Analyzing link...**", reply_markup=cancel_kb())
    session_set(uid, {"url": text, "cancel": False})
    
    # For YouTube links, try to get video info first
    if is_yt(text) and YOUTUBE_API_KEY:
        video_id = extract_video_id(text)
        if video_id:
            video_info = await get_youtube_video_info(video_id)
            if video_info:
                await safe_edit(status,
                    f"🎬 **YouTube Video Found**\n\n"
                    f"📺 **{video_info['title'][:50]}**\n"
                    f"👤 {video_info['channel']}\n"
                    f"⏱️ {video_info['duration_str']} • 👁️ {video_info['views']:,} views\n\n"
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
    
    if not await is_subscribed(uid):
        return await m.reply_text("⚠️ **Join channel first!**", reply_markup=join_kb())
    
    media = m.video or m.document or m.audio
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
    
    if not await is_subscribed(uid):
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
    
    # VERIFY JOIN - Simple button to check subscription
    if data == "verify_join":
        if await is_subscribed(uid):
            await cb.answer("✅ Verified! Welcome!", show_alert=True)
            return await safe_edit(cb.message,
                f"✅ **Verification Successful!**\n\n"
                f"Welcome **{cb.from_user.first_name}**! 🎉\n\n"
                f"🚀 Send any video link to download:\n"
                f"• YouTube\n"
                f"• Instagram\n"
                f"• Direct links",
                menu_kb(uid)
            )
        else:
            await cb.answer("❌ You haven't joined the channel yet!\n\nPlease join first, then click Verify again.", show_alert=True)
            return
    
    # Check subscription for all other actions
    if data not in ["close"]:
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
            return await safe_edit(cb.message,
                f"📊 **Bot Stats**\n\n"
                f"👥 Total Users: {len(DB['users'])}\n"
                f"🔑 YouTube API: {'✅ Active' if YOUTUBE_API_KEY else '❌ Not set'}",
                menu_kb(uid))
        used = user.get("used", 0)
        return await safe_edit(cb.message, f"📊 Used today: {human_size(used)}", menu_kb(uid))
    
    if data == "menu_help":
        return await safe_edit(cb.message,
            "❓ **How to use:**\n\n"
            "1. Send a video link (YouTube, Instagram, etc.)\n"
            "2. Choose quality\n"
            "3. Wait for download\n"
            "4. Upload as file or video\n\n"
            "📸 Send a photo to set custom thumbnail!",
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
        return await safe_edit(cb.message, "✅ Thumbnail deleted!", thumb_kb())
    
    # Admin
    if data == "admin":
        if uid != OWNER_ID:
            return
        return await safe_edit(cb.message, "⚙️ **Admin Panel**", admin_kb())
    
    if data == "adm_stats":
        if uid != OWNER_ID:
            return
        banned_count = sum(1 for u in DB["users"].values() if u.get("is_banned"))
        pro_count = sum(1 for u in DB["users"].values() if u.get("is_pro"))
        return await safe_edit(cb.message,
            f"📊 **Statistics**\n\n"
            f"👥 Total users: {len(DB['users'])}\n"
            f"👑 PRO: {pro_count}\n"
            f"🚫 Banned: {banned_count}\n"
            f"🔑 YouTube API: {'✅' if YOUTUBE_API_KEY else '❌'}",
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
    
    # YouTube quality selection
    if data.startswith("yt_"):
        if not sess or not sess.get("url"):
            return await safe_edit(cb.message, "❌ Session expired! Send the link again.", None)
        
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
    
    # Rename
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
            return await safe_edit(cb.message, "✏️ Send new filename (without extension):", cancel_kb())
    
    if data == "back_up":
        if sess:
            return await safe_edit(cb.message, f"📄 `{sess['name']}`\n📦 {human_size(sess.get('size', 0))}", upload_kb())
    
    # Upload
    if data in ["up_file", "up_video"]:
        if not sess or not sess.get("path") or not os.path.exists(sess["path"]):
            session_clear(uid)
            return await safe_edit(cb.message, "❌ File not found! Try again.", None)
        
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
# HEALTH CHECK & MAIN
# =======================
async def health(_):
    return web.Response(text="OK")

async def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(THUMB_DIR, exist_ok=True)
    db_load()
    
    await app.start()
    print("=" * 60)
    print("✅ BOT STARTED SUCCESSFULLY!")
    print(f"👥 Users: {len(DB['users'])}")
    print(f"🔑 YouTube API: {'Active' if YOUTUBE_API_KEY else 'Not configured'}")
    print("=" * 60)
    
    srv = web.Application()
    srv.add_routes([web.get("/", health)])
    runner = web.AppRunner(srv)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()
    
    await idle()
    await app.stop()

if __name__ == "__main__":
    app.run(main())
