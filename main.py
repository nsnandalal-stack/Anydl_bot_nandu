import os
import re
import time
import json
import shutil
import asyncio
import base64
from datetime import date, datetime
from aiohttp import web, ClientSession, ClientTimeout

from pyrogram import Client, filters, types, enums, idle
from yt_dlp import YoutubeDL

# =======================
# ENVIRONMENT VARIABLES
# =======================
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
OWNER_ID = os.getenv("OWNER_ID")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
COOKIES = os.getenv("COOKIES")
COOKIES_BASE64 = os.getenv("COOKIES_BASE64")
INVITE_LINK = os.getenv("INVITE_LINK", "https://t.me/+eooytvOAwjc0NTI1")

# Decode base64 cookies if provided
if COOKIES_BASE64 and not COOKIES:
    try:
        COOKIES = base64.b64decode(COOKIES_BASE64).decode('utf-8')
    except:
        pass

# =======================
# VALIDATE
# =======================
print("=" * 50)
print("🔍 CHECKING ENVIRONMENT")
print("=" * 50)

missing = []
for var in ["API_ID", "API_HASH", "BOT_TOKEN", "CHANNEL_ID", "OWNER_ID"]:
    if not os.getenv(var):
        missing.append(var)
    else:
        print(f"✅ {var}")

print(f"{'✅' if YOUTUBE_API_KEY else '⚠️'} YOUTUBE_API_KEY {'(set)' if YOUTUBE_API_KEY else '(optional)'}")
print(f"{'✅' if COOKIES or COOKIES_BASE64 else '⚠️'} COOKIES {'(set)' if COOKIES or COOKIES_BASE64 else '(optional)'}")

if missing:
    print(f"\n❌ Missing: {', '.join(missing)}")
    exit(1)

API_ID = int(API_ID)
CHANNEL_ID = int(CHANNEL_ID)
OWNER_ID = int(OWNER_ID)
print(f"\n✅ Owner: {OWNER_ID}")
print("=" * 50)

# =======================
# CONFIG
# =======================
DOWNLOAD_DIR = "/tmp/downloads"
THUMB_DIR = "/tmp/thumbnails"
DB_FILE = "/tmp/bot_db.json"
DAILY_LIMIT = 5 * 1024 * 1024 * 1024

# =======================
# COOKIES
# =======================
COOKIES_PATH = None
COOKIES_EXPIRY = None

def fix_cookies(content):
    if not content:
        return None
    content = content.replace('\\n', '\n').replace('\\t', '\t')
    lines = []
    for line in content.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        if line.startswith('#'):
            lines.append(line)
            continue
        parts = line.split()
        if len(parts) >= 7:
            lines.append('\t'.join(parts))
        elif '\t' in line:
            lines.append(line)
    result = '\n'.join(lines)
    if '# Netscape' not in result:
        result = '# Netscape HTTP Cookie File\n\n' + result
    return result

def get_cookie_expiry(content):
    if not content:
        return None
    try:
        for line in content.split('\n'):
            if line.startswith('#') or not line.strip():
                continue
            parts = line.split('\t')
            if len(parts) >= 5:
                ts = int(parts[4])
                if ts > 0:
                    return datetime.fromtimestamp(ts)
    except:
        pass
    return None

def setup_cookies():
    global COOKIES_PATH, COOKIES_EXPIRY
    if not COOKIES:
        print("⚠️ No cookies configured")
        return
    try:
        fixed = fix_cookies(COOKIES)
        if fixed:
            COOKIES_PATH = "/tmp/cookies.txt"
            with open(COOKIES_PATH, 'w') as f:
                f.write(fixed)
            COOKIES_EXPIRY = get_cookie_expiry(fixed)
            exp = COOKIES_EXPIRY.strftime('%Y-%m-%d') if COOKIES_EXPIRY else "unknown"
            print(f"🍪 Cookies ready (expires: {exp})")
    except Exception as e:
        print(f"❌ Cookie error: {e}")

def cookies_status():
    if not COOKIES_PATH:
        return False, None, "Not configured"
    if COOKIES_EXPIRY:
        now = datetime.now()
        if COOKIES_EXPIRY <= now:
            return True, COOKIES_EXPIRY, "EXPIRED!"
        days = (COOKIES_EXPIRY - now).days
        if days <= 3:
            return False, COOKIES_EXPIRY, f"Expiring in {days} days"
        return False, COOKIES_EXPIRY, f"Valid ({days} days)"
    return False, None, "Loaded (expiry unknown)"

# =======================
# DATABASE
# =======================
DB = {"users": {}, "sessions": {}}

def db_load():
    global DB
    try:
        if os.path.exists(DB_FILE):
            with open(DB_FILE) as f:
                DB = json.load(f)
    except:
        pass

def db_save():
    try:
        with open(DB_FILE, 'w') as f:
            json.dump(DB, f)
    except:
        pass

def user_get(uid):
    k = str(uid)
    if k not in DB["users"]:
        DB["users"][k] = {
            "thumb": None, "state": "none", "used": 0,
            "reset": date.today().isoformat(),
            "is_pro": uid == OWNER_ID, "is_banned": False
        }
    u = DB["users"][k]
    if u.get("reset") != date.today().isoformat():
        u["reset"] = date.today().isoformat()
        u["used"] = 0
    return u

def session_get(uid):
    return DB["sessions"].get(str(uid))

def session_set(uid, data):
    DB["sessions"][str(uid)] = data
    db_save()

def session_clear(uid):
    DB["sessions"].pop(str(uid), None)
    user_get(uid)["state"] = "none"
    db_save()

# =======================
# HELPERS
# =======================
def safe_name(n):
    return re.sub(r'[\\/*?:"<>|]', "", str(n).strip())[:150] or "file"

def get_ext(n):
    return os.path.splitext(n)[1]

def is_yt(url):
    return any(x in url.lower() for x in ["youtube.com", "youtu.be"])

def is_ig(url):
    return "instagram.com" in url.lower()

def get_yt_id(url):
    if "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0]
    if "v=" in url:
        return url.split("v=")[1].split("&")[0]
    if "/shorts/" in url:
        return url.split("/shorts/")[1].split("?")[0]
    return ""

def human_size(n):
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"

def human_time(s):
    if not s or s <= 0:
        return "..."
    s = int(s)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s//60}m {s%60}s"
    return f"{s//3600}h {(s%3600)//60}m"

def progress_bar(p):
    f = int(p / 10)
    return "█" * f + "░" * (10 - f)

def check_limit(uid, size=0):
    if uid == OWNER_ID:
        return True, float('inf')
    u = user_get(uid)
    if u.get("is_pro"):
        return True, float('inf')
    used = u.get("used", 0)
    rem = DAILY_LIMIT - used
    if size:
        return used + size <= DAILY_LIMIT, rem
    return used < DAILY_LIMIT, rem

async def safe_edit(msg, text, kb=None):
    try:
        return await msg.edit_text(text, reply_markup=kb)
    except:
        return msg

async def is_member(uid):
    if uid == OWNER_ID:
        return True
    try:
        m = await app.get_chat_member(CHANNEL_ID, uid)
        return m.status in [enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except:
        return False

# =======================
# KEYBOARDS
# =======================
def kb_join():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("📢 Join Channel", url=INVITE_LINK)],
        [types.InlineKeyboardButton("✅ Verify", callback_data="verify")]
    ])

def kb_cancel():
    return types.InlineKeyboardMarkup([[types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])

def kb_menu(uid):
    kb = [
        [types.InlineKeyboardButton("🖼️ Thumbnail", callback_data="thumb"),
         types.InlineKeyboardButton("📊 Stats", callback_data="stats")],
        [types.InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    if uid == OWNER_ID:
        kb.append([types.InlineKeyboardButton("⚙️ Admin", callback_data="admin")])
    kb.append([types.InlineKeyboardButton("✖️ Close", callback_data="close")])
    return types.InlineKeyboardMarkup(kb)

def kb_thumb():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("👁️ View", callback_data="thumb_view"),
         types.InlineKeyboardButton("🗑️ Delete", callback_data="thumb_del")],
        [types.InlineKeyboardButton("🔙 Back", callback_data="back")]
    ])

def kb_upload():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("✏️ Rename", callback_data="rename"),
         types.InlineKeyboardButton("📄 File", callback_data="up_file"),
         types.InlineKeyboardButton("🎬 Video", callback_data="up_video")],
        [types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

def kb_rename():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("📝 Keep", callback_data="ren_keep"),
         types.InlineKeyboardButton("✏️ Custom", callback_data="ren_custom")],
        [types.InlineKeyboardButton("🔙 Back", callback_data="back_up")]
    ])

def kb_quality():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("🎬 1080p", callback_data="q_1080"),
         types.InlineKeyboardButton("🎬 720p", callback_data="q_720"),
         types.InlineKeyboardButton("📹 480p", callback_data="q_480")],
        [types.InlineKeyboardButton("📹 360p", callback_data="q_360"),
         types.InlineKeyboardButton("🎵 MP3", callback_data="q_mp3"),
         types.InlineKeyboardButton("🎵 MP3 HD", callback_data="q_mp3_320")],
        [types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

def kb_admin():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("📊 Stats", callback_data="adm_stats"),
         types.InlineKeyboardButton("📢 Broadcast", callback_data="adm_bc")],
        [types.InlineKeyboardButton("👑 Pro", callback_data="adm_pro"),
         types.InlineKeyboardButton("🚫 Ban", callback_data="adm_ban")],
        [types.InlineKeyboardButton("✅ Unban", callback_data="adm_unban"),
         types.InlineKeyboardButton("🍪 Cookies", callback_data="adm_cookies")],
        [types.InlineKeyboardButton("🔙 Back", callback_data="back")]
    ])

def kb_bc():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("✅ Send", callback_data="bc_yes"),
         types.InlineKeyboardButton("❌ Cancel", callback_data="bc_no")]
    ])

# =======================
# DOWNLOAD
# =======================
async def dl_url(uid, url, msg, name=None, quality="720"):
    timeout = ClientTimeout(total=600)
    async with ClientSession(timeout=timeout) as s:
        async with s.get(url) as r:
            if r.status != 200:
                raise Exception(f"HTTP {r.status}")
            if not name:
                name = f"video_{int(time.time())}" + (".mp3" if "mp3" in quality else ".mp4")
            name = safe_name(name)
            path = os.path.join(DOWNLOAD_DIR, name)
            total = int(r.headers.get("Content-Length", 0))
            ok, rem = check_limit(uid, total)
            if not ok:
                raise Exception(f"Daily limit! Remaining: {human_size(rem)}")
            done = 0
            last = 0
            start = time.time()
            with open(path, 'wb') as f:
                async for chunk in r.content.iter_chunked(524288):
                    sess = session_get(uid)
                    if sess and sess.get("cancel"):
                        raise Exception("CANCELLED")
                    f.write(chunk)
                    done += len(chunk)
                    now = time.time()
                    if now - last >= 2:
                        last = now
                        pct = (done / total * 100) if total else 0
                        spd = done / (now - start) if now > start else 0
                        await safe_edit(msg, f"⬇️ Downloading...\n\n`[{progress_bar(pct)}]` {pct:.1f}%\n📦 {human_size(done)}/{human_size(total)}\n⚡ {human_size(spd)}/s", kb_cancel())
            return path, os.path.splitext(name)[0]

async def dl_ytdlp(uid, url, msg, quality="720"):
    await safe_edit(msg, "🔄 Downloading...", kb_cancel())
    start = time.time()
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
        done = d.get("downloaded_bytes", 0)
        pct = (done / total * 100) if total else 0
        asyncio.get_event_loop().create_task(
            safe_edit(msg, f"⬇️ Downloading...\n\n`[{progress_bar(pct)}]` {pct:.1f}%\n📦 {human_size(done)}/{human_size(total)}", kb_cancel())
        )
    
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "outtmpl": f"{DOWNLOAD_DIR}/%(title).70s.%(ext)s",
        "progress_hooks": [hook], "retries": 5, "socket_timeout": 30,
        "nocheckcertificate": True, "geo_bypass": True,
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
        "http_headers": {"User-Agent": "com.google.android.youtube/17.36.4 (Linux; U; Android 12) gzip"}
    }
    
    if COOKIES_PATH and os.path.exists(COOKIES_PATH):
        opts["cookiefile"] = COOKIES_PATH
    
    if quality.startswith("mp3"):
        br = "320" if quality == "mp3_320" else "192"
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": br}]
    else:
        h = int(quality) if quality.isdigit() else 720
        opts["format"] = f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"
        opts["merge_output_format"] = "mp4"
    
    def do():
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            if quality.startswith("mp3"):
                path = os.path.splitext(path)[0] + ".mp3"
            return path, info.get("title", "video")
    
    return await asyncio.get_event_loop().run_in_executor(None, do)

async def dl_invidious(uid, url, msg, quality="720"):
    vid = get_yt_id(url)
    if not vid:
        raise Exception("Invalid URL")
    await safe_edit(msg, "🔄 Trying Invidious...", kb_cancel())
    instances = ["https://inv.nadeko.net", "https://invidious.nerdvpn.de", "https://invidious.jing.rocks"]
    is_audio = quality.startswith("mp3")
    for inst in instances:
        try:
            async with ClientSession(timeout=ClientTimeout(total=30)) as s:
                async with s.get(f"{inst}/api/v1/videos/{vid}") as r:
                    if r.status != 200:
                        continue
                    data = await r.json()
                    title = data.get("title", "video")
                    if is_audio:
                        streams = [x for x in data.get("adaptiveFormats", []) if x.get("type", "").startswith("audio")]
                        if streams:
                            streams.sort(key=lambda x: x.get("bitrate", 0), reverse=True)
                            return await dl_url(uid, streams[0]["url"], msg, f"{safe_name(title)}.mp3", quality)
                    else:
                        h = int(quality) if quality.isdigit() else 720
                        for s in data.get("formatStreams", []):
                            res = s.get("resolution", "")
                            if "p" in res and int(res.replace("p", "")) <= h:
                                return await dl_url(uid, s["url"], msg, f"{safe_name(title)}.mp4", quality)
        except:
            continue
    raise Exception("Invidious failed")

async def dl_cobalt(uid, url, msg, quality="720"):
    await safe_edit(msg, "🔄 Trying Cobalt...", kb_cancel())
    is_audio = quality.startswith("mp3")
    payload = {"url": url, "vCodec": "h264", "vQuality": quality if quality.isdigit() else "720", "isAudioOnly": is_audio}
    try:
        async with ClientSession(timeout=ClientTimeout(total=60)) as s:
            async with s.post("https://co.wuk.sh/api/json", json=payload) as r:
                if r.status == 200:
                    data = await r.json()
                    if data.get("url"):
                        ext = ".mp3" if is_audio else ".mp4"
                        return await dl_url(uid, data["url"], msg, f"video_{int(time.time())}{ext}", quality)
    except:
        pass
    raise Exception("Cobalt failed")

async def download(uid, url, msg, quality="720"):
    ok, rem = check_limit(uid)
    if not ok:
        raise Exception(f"Daily limit exceeded!\nRemaining: {human_size(rem)}")
    errors = []
    if is_yt(url):
        try:
            return await dl_invidious(uid, url, msg, quality)
        except Exception as e:
            if "CANCELLED" in str(e):
                raise
            errors.append(str(e)[:30])
    try:
        return await dl_cobalt(uid, url, msg, quality)
    except Exception as e:
        if "CANCELLED" in str(e):
            raise
        errors.append(str(e)[:30])
    try:
        return await dl_ytdlp(uid, url, msg, quality)
    except Exception as e:
        if "CANCELLED" in str(e):
            raise
        errors.append(str(e)[:30])
    raise Exception("All methods failed:\n" + "\n".join(errors))

# =======================
# SCREENSHOTS
# =======================
async def screenshots(path, count=5):
    out = os.path.join(DOWNLOAD_DIR, f"ss_{int(time.time())}")
    os.makedirs(out, exist_ok=True)
    screens = []
    try:
        proc = await asyncio.create_subprocess_shell(
            f'ffprobe -v error -show_entries format=duration -of csv=p=0 "{path}"',
            stdout=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        dur = float(stdout.decode().strip() or 0)
        if dur > 0:
            interval = dur / (count + 1)
            for i in range(1, count + 1):
                ss = os.path.join(out, f"{i}.jpg")
                p = await asyncio.create_subprocess_shell(f'ffmpeg -ss {interval*i} -i "{path}" -vframes 1 -q:v 5 -y "{ss}" 2>/dev/null')
                await p.wait()
                if os.path.exists(ss):
                    screens.append(ss)
    except:
        pass
    return screens, out

# =======================
# UPLOAD
# =======================
async def upload(uid, msg, path, name, as_video):
    user = user_get(uid)
    thumb = user.get("thumb")
    if thumb and not os.path.exists(thumb):
        thumb = None
    size = os.path.getsize(path)
    ok, rem = check_limit(uid, size)
    if not ok:
        raise Exception(f"Limit exceeded! Remaining: {human_size(rem)}")
    
    start = time.time()
    last = {"t": 0}
    
    async def prog(done, total):
        sess = session_get(uid)
        if sess and sess.get("cancel"):
            raise Exception("CANCELLED")
        now = time.time()
        if now - last["t"] < 2:
            return
        last["t"] = now
        pct = (done / total * 100) if total else 0
        await safe_edit(msg, f"📤 Uploading...\n\n`[{progress_bar(pct)}]` {pct:.1f}%", kb_cancel())
    
    if as_video:
        await app.send_video(uid, path, caption=f"🎬 `{name}`", file_name=name, supports_streaming=True, thumb=thumb, progress=prog)
        await safe_edit(msg, "📸 Screenshots...", None)
        ss, ss_dir = await screenshots(path)
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
app = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workdir="/tmp")

@app.on_message(filters.command("start") & filters.private)
async def start(_, m):
    uid = m.from_user.id
    user_get(uid)
    db_save()
    
    if user_get(uid).get("is
