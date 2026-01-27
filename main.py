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
    
    # Also check for any .txt in cookies folder
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
                    # Check if it's a valid Netscape cookie file
                    if '.youtube.com' in content or 'youtube' in content.lower():
                        COOKIES_PATH = path
                        size = os.path.getsize(path)
                        print(f"✅ COOKIES FOUND: {path} ({size} bytes)")
                        # Print first few lines for debug
                        lines = content.split('\n')[:5]
                        for line in lines:
                            print(f"   {line[:80]}")
                        return path
            except Exception as e:
                print(f"❌ Error reading {path}: {e}")
    
    print("❌ NO VALID COOKIES FOUND!")
    print(f"   Searched: {possible_paths}")
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
        [types.InlineKeyboardButton("🎬 720p", callback_data="yt_720"),
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
    """Download file from URL"""
    
    start_time = time.time()
    last_update = 0
    timeout = ClientTimeout(total=600)
    
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
            
            return path, os.path.splitext(filename)[0]

# =======================
# METHOD 1: PIPED API
# =======================
async def download_piped(uid: int, url: str, msg, quality: str = "720"):
    """Download using Piped instances"""
    
    await safe_edit(msg, "🔄 **Method 1: Piped...**", cancel_kb())
    
    video_id = extract_video_id(url)
    if not video_id:
        raise Exception("Invalid URL")
    
    instances = [
        "https://pipedapi.kavin.rocks",
        "https://pipedapi.adminforge.de", 
        "https://api.piped.yt",
        "https://pipedapi.in.projectsegfau.lt",
        "https://pipedapi.moomoo.me"
    ]
    
    timeout = ClientTimeout(total=30)
    
    for instance in instances:
        try:
            async with ClientSession(timeout=timeout) as session:
                async with session.get(f"{instance}/streams/{video_id}") as resp:
                    if resp.status != 200:
                        continue
                    
                    data = await resp.json()
                    title = safe_name(data.get("title", f"video_{video_id}"))
                    
                    if quality == "mp3":
                        streams = data.get("audioStreams", [])
                        if streams:
                            streams.sort(key=lambda x: x.get("bitrate", 0), reverse=True)
                            download_url = streams[0].get("url")
                            return await download_from_url(uid, download_url, msg, f"{title}.mp3", quality)
                    else:
                        streams = data.get("videoStreams", [])
                        target = int(quality) if quality.isdigit() else 720
                        
                        # Find best match
                        best = None
                        for s in streams:
                            q = s.get("quality", "")
                            if str(target) in q:
                                best = s
                                break
                        
                        if not best and streams:
                            best = streams[0]
                        
                        if best:
                            download_url = best.get("url")
                            return await download_from_url(uid, download_url, msg, f"{title}.mp4", quality)
        except:
            continue
    
    raise Exception("Piped failed")

# =======================
# METHOD 2: YT-DLP (WITH COOKIES)
# =======================
async def download_ytdlp(uid: int, url: str, msg, quality: str = "720"):
    """Download using yt-dlp with proper cookies"""
    
    await safe_edit(msg, "🔄 **Method 2: yt-dlp...**", cancel_kb())
    
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
                "player_client": ["ios", "android", "web"],
                "player_skip": ["webpage", "configs"],
            }
        },
        "http_headers": {
            "User-Agent": "com.google.ios.youtube/19.09.3 (iPhone14,3; U; CPU iOS 15_6 like Mac OS X)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-us,en;q=0.5",
            "Sec-Fetch-Mode": "navigate",
        }
    }
    
    # ADD COOKIES - THIS IS CRITICAL!
    if COOKIES_PATH and os.path.exists(COOKIES_PATH):
        opts["cookiefile"] = COOKIES_PATH
        print(f"🍪 yt-dlp using cookies: {COOKIES_PATH}")
    else:
        print("⚠️ yt-dlp: No cookies available!")
    
    # Format selection
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
# METHOD 3: INVIDIOUS
# =======================
async def download_invidious(uid: int, url: str, msg, quality: str = "720"):
    """Download using Invidious"""
    
    await safe_edit(msg, "🔄 **Method 3: Invidious...**", cancel_kb())
    
    video_id = extract_video_id(url)
    if not video_id:
        raise Exception("Invalid URL")
    
    instances = [
        "https://inv.nadeko.net",
        "https://invidious.nerdvpn.de",
        "https://invidious.privacyredirect.com",
        "https://yt.artemislena.eu",
        "https://invidious.protokolla.fi"
    ]
    
    timeout = ClientTimeout(total=30)
    
    for instance in instances:
        try:
            async with ClientSession(timeout=timeout) as session:
                async with session.get(f"{instance}/api/v1/videos/{video_id}") as resp:
                    if resp.status != 200:
                        continue
                    
                    data = await resp.json()
                    title = safe_name(data.get("title", f"video_{video_id}"))
                    
                    if quality == "mp3":
                        formats = data.get("adaptiveFormats", [])
                        audio = [f for f in formats if f.get("type", "").startswith("audio")]
                        if audio:
                            download_url = audio[0].get("url")
                            return await download_from_url(uid, download_url, msg, f"{title}.mp3", quality)
                    else:
                        formats = data.get("formatStreams", [])
                        if formats:
                            download_url = formats[0].get("url")
                            return await download_from_url(uid, download_url, msg, f"{title}.mp4", quality)
        except:
            continue
    
    raise Exception("Invidious failed")

# =======================
# MAIN DOWNLOAD
# =======================
async def download_video(uid: int, url: str, msg, quality: str = "720"):
    """Try all methods"""
    
    errors = []
    
    # Method 1: Piped
    try:
        return await download_piped(uid, url, msg, quality)
    except Exception as e:
        if "CANCELLED" in str(e): raise
        errors.append(f"Piped: {str(e)[:40]}")
    
    # Method 2: yt-dlp
    try:
        return await download_ytdlp(uid, url, msg, quality)
    except Exception as e:
        if "CANCELLED" in str(e): raise
        errors.append(f"yt-dlp: {str(e)[:40]}")
    
    # Method 3: Invidious
    try:
        return await download_invidious(uid, url, msg, quality)
    except Exception as e:
        if "CANCELLED" in str(e): raise
        errors.append(f"Invidious: {str(e)[:40]}")
    
    raise Exception("All failed:\n" + "\n".join(errors))

# =======================
# DIRECT DOWNLOAD
# =======================
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
# BOT
# =======================
app = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start") & filters.private)
async def cmd_start(_, m):
    user_get(m.from_user.id)
    db_save()
    await m.reply_text(
        f"👋 Hi **{m.from_user.first_name}**!\n\n"
        f"🚀 Send any video link to download.",
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
        return await m.reply_text("⚠️ **Join channel!**", reply_markup=join_kb())
    
    status = await m.reply_text("🔍 **Analyzing...**", reply_markup=cancel_kb())
    session_set(uid, {"url": text, "cancel": False})
    
    if is_yt(text):
        return await safe_edit(status, "🎬 **YouTube**\n\nChoose:", yt_kb())
    
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
        await safe_edit(status, f"❌ {str(e)[:200]}" if "CANCELLED" not in str(e) else "❌ Cancelled!", None)

@app.on_message((filters.video | filters.document | filters.audio) & filters.private)
async def on_file(_, m):
    uid = m.from_user.id
    if user_get(uid).get("is_banned"): return
    if not await is_subscribed(uid):
        return await m.reply_text("⚠️ Join!", reply_markup=join_kb())
    
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
    if user_get(uid).get("is_banned"): return
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
    
    if user.get("is_banned"): return
    
    if data == "close":
        try: await cb.message.delete()
        except: pass
        return
    
    if data == "check_join":
        if await is_subscribed(uid):
            return await safe_edit(cb.message, "✅ Verified!", menu_kb(uid))
        return await cb.answer("❌ Not joined!", show_alert=True)
    
    if data == "cancel":
        if sess:
            sess["cancel"] = True
            session_set(uid, sess)
            if sess.get("path") and os.path.exists(sess["path"]):
                try: os.remove(sess["path"])
                except: pass
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
        return await safe_edit(cb.message, "🖼️ Send photo.", thumb_kb())
    
    if data == "menu_stats":
        if uid == OWNER_ID:
            return await safe_edit(cb.message, 
                f"📊 **Stats**\n\n👥 {len(DB['users'])}\n🍪 {'✅' if COOKIES_PATH else '❌'}", 
                menu_kb(uid))
        used = user.get("used", 0)
        return await safe_edit(cb.message, f"📊 Used: {human_size(used)}", menu_kb(uid))
    
    if data == "menu_help":
        return await safe_edit(cb.message, 
            "❓ Send link → Choose quality → Download → Upload", 
            menu_kb(uid))
    
    if data == "thumb_view":
        t = user.get("thumb")
        if t and os.path.exists(t):
            await cb.message.reply_photo(t)
        else:
            await cb.answer("No thumb!", show_alert=True)
        return
    
    if data == "thumb_del":
        t = user.get("thumb")
        if t and os.path.exists(t): os.remove(t)
        user["thumb"] = None
        db_save()
        return await safe_edit(cb.message, "✅ Deleted!", thumb_kb())
    
    # Admin
    if data == "admin":
        if uid != OWNER_ID: return
        return await safe_edit(cb.message, "⚙️ Admin", admin_kb())
    
    if data == "adm_stats":
        if uid != OWNER_ID: return
        return await safe_edit(cb.message, 
            f"📊 Users: {len(DB['users'])}\n🍪 Cookies: {'✅ '+COOKIES_PATH if COOKIES_PATH else '❌ Not found'}", 
            admin_kb())
    
    if data == "adm_cookies":
        if uid != OWNER_ID: return
        if COOKIES_PATH:
            size = os.path.getsize(COOKIES_PATH)
            return await safe_edit(cb.message, f"🍪 **Cookies**\n\n✅ `{COOKIES_PATH}`\n📦 {size} bytes", admin_kb())
        return await safe_edit(cb.message, "🍪 **No cookies found!**\n\nAdd `cookies/cookies.txt`", admin_kb())
    
    if data == "adm_bc":
        if uid != OWNER_ID: return
        user["state"] = "broadcast"
        db_save()
        return await safe_edit(cb.message, "📢 Send message:", cancel_kb())
    
    if data == "bc_yes":
        if uid != OWNER_ID: return
        text = user.get("bc", "")
        if not text: return
        sent = 0
        for u in DB["users"]:
            if not DB["users"][u].get("is_banned"):
                try:
                    await app.send_message(int(u), text)
                    sent += 1
                except: pass
        user["bc"] = ""
        db_save()
        return await safe_edit(cb.message, f"✅ Sent: {sent}", admin_kb())
    
    if data == "bc_cancel":
        user["state"] = "none"
        user["bc"] = ""
        db_save()
        return await safe_edit(cb.message, "❌", admin_kb())
    
    if data == "adm_pro":
        if uid != OWNER_ID: return
        user["state"] = "addpro"
        db_save()
        return await safe_edit(cb.message, "👑 User ID:", cancel_kb())
    
    if data == "adm_ban":
        if uid != OWNER_ID: return
        user["state"] = "ban"
        db_save()
        return await safe_edit(cb.message, "🚫 User ID:", cancel_kb())
    
    if data == "adm_unban":
        if uid != OWNER_ID: return
        user["state"] = "unban"
        db_save()
        return await safe_edit(cb.message, "✅ User ID:", cancel_kb())
    
    # YouTube
    if data.startswith("yt_"):
        if not sess or not sess.get("url"):
            return await safe_edit(cb.message, "❌ Expired!", None)
        
        quality = data.replace("yt_", "")
        
        try:
            await safe_edit(cb.message, f"⬇️ **{quality}...**", cancel_kb())
            path, title = await download_video(uid, sess["url"], cb.message, quality)
            name = os.path.basename(path)
            size = os.path.getsize(path)
            session_set(uid, {"url": sess["url"], "path": path, "name": name, "ext": get_ext(name), "size": size, "cancel": False})
            await safe_edit(cb.message, f"✅ `{name}`\n📦 {human_size(size)}", upload_kb())
        except Exception as e:
            session_clear(uid)
            await safe_edit(cb.message, f"❌ {str(e)[:200]}" if "CANCELLED" not in str(e) else "❌ Cancelled!", None)
        return
    
    # Rename
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
            return await safe_edit(cb.message, "✏️ New name:", cancel_kb())
    
    if data == "back_up":
        if sess:
            return await safe_edit(cb.message, f"📄 `{sess['name']}`", upload_kb())
    
    # Upload
    if data in ["up_file", "up_video"]:
        if not sess or not sess.get("path") or not os.path.exists(sess["path"]):
            session_clear(uid)
            return await safe_edit(cb.message, "❌ Missing!", None)
        
        try:
            await safe_edit(cb.message, "📤 **Uploading...**", cancel_kb())
            await do_upload(uid, cb.message, sess["path"], sess["name"], data == "up_video")
            try: os.remove(sess["path"])
            except: pass
            session_clear(uid)
            await safe_edit(cb.message, "✅ Done!", menu_kb(uid))
        except Exception as e:
            await safe_edit(cb.message, f"❌ {str(e)[:80]}" if "CANCELLED" not in str(e) else "❌", None)

# =======================
# MAIN
# =======================
async def health(_):
    return web.Response(text="OK")

async def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(THUMB_DIR, exist_ok=True)
    db_load()
    
    # FIND COOKIES ON STARTUP
    find_cookies()
    
    await app.start()
    print("✅ Bot started!")
    print(f"👥 Users: {len(DB['users'])}")
    print(f"🍪 Cookies: {COOKIES_PATH or 'NOT FOUND'}")
    
    srv = web.Application()
    srv.add_routes([web.get("/", health)])
    runner = web.AppRunner(srv)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()
    
    await idle()
    await app.stop()

if __name__ == "__main__":
    app.run(main())
