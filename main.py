import os
import re
import time
import json
import shutil
import asyncio
import random
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

print("=" * 60)
print("🔍 ENVIRONMENT VARIABLES CHECK")
print(f"API_ID: {API_ID}")
print(f"API_HASH: {'SET ✅' if API_HASH else 'NOT SET ❌'}")
print(f"BOT_TOKEN: {'SET ✅' if BOT_TOKEN else 'NOT SET ❌'}")
print(f"CHANNEL_ID: {CHANNEL_ID}")
print("=" * 60)

if not API_ID or not API_HASH or not BOT_TOKEN or not CHANNEL_ID:
    print("\n❌ ERROR: MISSING ENVIRONMENT VARIABLES!")
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
            "is_banned": False,
            "verified": False,
            "verify_answer": None
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
# HUMAN VERIFICATION
# =======================
def generate_captcha():
    a = random.randint(1, 20)
    b = random.randint(1, 20)
    op = random.choice(['+', '-', 'x'])
    
    if op == '+':
        answer = a + b
        question = f"{a} + {b}"
    elif op == '-':
        if a < b:
            a, b = b, a
        answer = a - b
        question = f"{a} - {b}"
    else:
        a = random.randint(1, 10)
        b = random.randint(1, 10)
        answer = a * b
        question = f"{a} x {b}"
    
    return question, answer

def generate_verify_keyboard(correct_answer):
    wrong_answers = set()
    while len(wrong_answers) < 3:
        offset = random.randint(-5, 5)
        if offset == 0:
            offset = random.choice([-1, 1])
        wrong = correct_answer + offset
        if wrong != correct_answer and wrong >= 0:
            wrong_answers.add(wrong)
    
    all_answers = list(wrong_answers) + [correct_answer]
    random.shuffle(all_answers)
    
    row = []
    for ans in all_answers:
        row.append(types.InlineKeyboardButton(str(ans), callback_data=f"verify_{ans}"))
    
    return types.InlineKeyboardMarkup([
        row,
        [types.InlineKeyboardButton("🔄 New Question", callback_data="verify_new")]
    ])

def is_verified(uid: int) -> bool:
    if uid == OWNER_ID:
        return True
    return user_get(uid).get("verified", False)

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
         types.InlineKeyboardButton("🔓 Reset Verify", callback_data="adm_resetverify")],
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

# =======================
# YT-DLP DOWNLOAD
# =======================
async def download_ytdlp(uid: int, url: str, msg, quality: str = "720"):
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
        "geo_bypass_country": "US",
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "android", "web"],
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
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

# =======================
# COBALT API
# =======================
async def download_cobalt(uid: int, url: str, msg, quality: str = "720"):
    await safe_edit(msg, "🔄 **Fetching...**", cancel_kb())
    
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

# =======================
# MAIN DOWNLOAD
# =======================
async def download_video(uid: int, url: str, msg, quality: str = "720"):
    errors_list = []
    
    try:
        return await download_cobalt(uid, url, msg, quality)
    except Exception as e:
        if "CANCELLED" in str(e):
            raise
        errors_list.append(f"Cobalt: {str(e)[:50]}")
    
    try:
        return await download_ytdlp(uid, url, msg, quality)
    except Exception as e:
        if "CANCELLED" in str(e):
            raise
        errors_list.append(f"yt-dlp: {str(e)[:50]}")
    
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
# VERIFICATION HANDLER
# =======================
async def send_verification(message, uid):
    question, answer = generate_captcha()
    user = user_get(uid)
    user["verify_answer"] = answer
    db_save()
    
    kb = generate_verify_keyboard(answer)
    
    await message.reply_text(
        f"🤖 **Human Verification Required**\n\n"
        f"Please solve this to continue:\n\n"
        f"**{question} = ?**\n\n"
        f"Select the correct answer below:",
        reply_markup=kb
    )

# =======================
# HANDLERS
# =======================
@app.on_message(filters.command("start") & filters.private)
async def cmd_start(_, m):
    uid = m.from_user.id
    user_get(uid)
    db_save()
    
    if not is_verified(uid):
        return await send_verification(m, uid)
    
    await m.reply_text(
        f"👋 Hi **{m.from_user.first_name}**!\n\n"
        f"🚀 Send any video link (YouTube, Instagram, etc.)",
        reply_markup=menu_kb(uid)
    )

@app.on_message(filters.command("verify") & filters.private)
async def cmd_verify(_, m):
    uid = m.from_user.id
    
    if is_verified(uid):
        return await m.reply_text("✅ You are already verified!", reply_markup=menu_kb(uid))
    
    await send_verification(m, uid)

@app.on_message(filters.text & filters.private & ~filters.command(["start", "verify"]))
async def on_text(_, m):
    uid = m.from_user.id
    user = user_get(uid)
    text = m.text.strip()
    
    if user.get("is_banned"):
        return
    
    if not is_verified(uid):
        return await send_verification(m, uid)
    
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
            return await m.reply_text("✅ PRO!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid!", reply_markup=admin_kb())
    
    if user.get("state") == "ban" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            user_get(int(text))["is_banned"] = True
            db_save()
            return await m.reply_text("✅ Banned!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid!", reply_markup=admin_kb())
    
    if user.get("state") == "unban" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            user_get(int(text))["is_banned"] = False
            db_save()
            return await m.reply_text("✅ Unbanned!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid!", reply_markup=admin_kb())
    
    if user.get("state") == "resetverify" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            target_user = user_get(int(text))
            target_user["verified"] = False
            db_save()
            return await m.reply_text(f"✅ Reset verification for {text}!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid!", reply_markup=admin_kb())
    
    if not text.startswith("http"):
        return
    
    if not await is_subscribed(uid):
        return await m.reply_text("⚠️ **Join channel first!**", reply_markup=join_kb())
    
    status = await m.reply_text("🔍 **Analyzing...**", reply_markup=cancel_kb())
    session_set(uid, {"url": text, "cancel": False})
    
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
    if not is_verified(uid):
        return await send_verification(m, uid)
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
    user = user_get(uid)
    
    if user.get("is_banned"):
        return
    if not is_verified(uid):
        return await send_verification(m, uid)
    
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
    
    # Handle verification
    if data.startswith("verify_"):
        if data == "verify_new":
            question, answer = generate_captcha()
            user["verify_answer"] = answer
            db_save()
            kb = generate_verify_keyboard(answer)
            return await safe_edit(cb.message,
                f"🤖 **Human Verification Required**\n\n"
                f"Please solve this to continue:\n\n"
                f"**{question} = ?**\n\n"
                f"Select the correct answer below:",
                kb
            )
        
        selected = int(data.replace("verify_", ""))
        correct = user.get("verify_answer")
        
        if selected == correct:
            user["verified"] = True
            user["verify_answer"] = None
            db_save()
            await cb.answer("✅ Verification successful!", show_alert=True)
            return await safe_edit(cb.message,
                f"✅ **Verified Successfully!**\n\n"
                f"Welcome **{cb.from_user.first_name}**!\n\n"
                f"🚀 Send any video link to download.",
                menu_kb(uid)
            )
        else:
            await cb.answer("❌ Wrong answer! Try again.", show_alert=True)
            question, answer = generate_captcha()
            user["verify_answer"] = answer
            db_save()
            kb = generate_verify_keyboard(answer)
            return await safe_edit(cb.message,
                f"❌ **Wrong Answer!**\n\n"
                f"Try again:\n\n"
                f"**{question} = ?**",
                kb
            )
    
    if not is_verified(uid) and data != "close":
        await cb.answer("⚠️ Please verify first!", show_alert=True)
        return await send_verification(cb.message, uid)
    
    if data == "close":
        try:
            await cb.message.delete()
        except:
            pass
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
            verified_count = sum(1 for u in DB["users"].values() if u.get("verified"))
            return await safe_edit(cb.message,
                f"📊 **Bot Stats**\n\n"
                f"👥 Total Users: {len(DB['users'])}\n"
                f"✅ Verified: {verified_count}",
                menu_kb(uid))
        used = user.get("used", 0)
        return await safe_edit(cb.message, f"📊 Used today: {human_size(used)}", menu_kb(uid))
    
    if data == "menu_help":
        return await safe_edit(cb.message,
            "❓ **How to use:**\n\n"
            "1. Send YouTube/Instagram link\n"
            "2. Choose quality\n"
            "3. Wait for download\n"
            "4. Upload as file or video",
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
    
    if data == "admin":
        if uid != OWNER_ID:
            return
        return await safe_edit(cb.message, "⚙️ Admin Panel", admin_kb())
    
    if data == "adm_stats":
        if uid != OWNER_ID:
            return
        verified_count = sum(1 for u in DB["users"].values() if u.get("verified"))
        banned_count = sum(1 for u in DB["users"].values() if u.get("is_banned"))
        pro_count = sum(1 for u in DB["users"].values() if u.get("is_pro"))
        return await safe_edit(cb.message,
            f"📊 **Statistics**\n\n"
            f"👥 Total users: {len(DB['users'])}\n"
            f"✅ Verified: {verified_count}\n"
            f"👑 PRO: {pro_count}\n"
            f"🚫 Banned: {banned_count}",
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
    
    if data == "adm_resetverify":
        if uid != OWNER_ID:
            return
        user["state"] = "resetverify"
        db_save()
        return await safe_edit(cb.message, "🔓 Send user ID:", cancel_kb())
    
    if data.startswith("yt_"):
        if not sess or not sess.get("url"):
            return await safe_edit(cb.message, "❌ Session expired!", None)
        
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
    verified = sum(1 for u in DB["users"].values() if u.get("verified"))
    print(f"✅ Verified: {verified}")
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
