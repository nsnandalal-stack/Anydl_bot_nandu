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

# Cookies - supports folder or file
COOKIES_FOLDER = "cookies"
COOKIES_FILE = "cookies.txt"

DAILY_LIMIT = 5 * 1024 * 1024 * 1024  # 5GB

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
            "joined": date.today().isoformat()
        }
    # Reset daily quota
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
# COOKIES HELPER
# =======================
def get_cookies_path():
    """Find cookies file - check folder first, then single file"""
    # Check cookies folder
    if os.path.exists(COOKIES_FOLDER):
        # Look for any .txt file in cookies folder
        for f in os.listdir(COOKIES_FOLDER):
            if f.endswith(".txt"):
                path = os.path.join(COOKIES_FOLDER, f)
                print(f"✅ Using cookies: {path}")
                return path
    
    # Check single cookies.txt file
    if os.path.exists(COOKIES_FILE):
        print(f"✅ Using cookies: {COOKIES_FILE}")
        return COOKIES_FILE
    
    # Check in /app/ directory (Docker)
    app_cookies = "/app/cookies/cookies.txt"
    if os.path.exists(app_cookies):
        print(f"✅ Using cookies: {app_cookies}")
        return app_cookies
    
    app_cookie_file = "/app/cookies.txt"
    if os.path.exists(app_cookie_file):
        print(f"✅ Using cookies: {app_cookie_file}")
        return app_cookie_file
    
    print("⚠️ No cookies found!")
    return None

# =======================
# HELPERS
# =======================
def safe_name(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name.strip())[:150] or "file"

def get_ext(name: str) -> str:
    return os.path.splitext(name)[1]

def is_yt(url: str) -> bool:
    return any(x in url.lower() for x in ["youtube.com", "youtu.be", "youtube.com/shorts"])

def human_size(n) -> str:
    if not n:
        return "0B"
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"

async def safe_edit(msg, text, kb=None):
    try:
        return await msg.edit_text(text, reply_markup=kb)
    except errors.MessageNotModified:
        return msg
    except errors.FloodWait as e:
        await asyncio.sleep(e.value)
        return await safe_edit(msg, text, kb)
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

def main_menu_kb(uid: int):
    """Main menu keyboard"""
    kb = [
        [
            types.InlineKeyboardButton("🖼️ Thumbnail", callback_data="menu_thumb"),
            types.InlineKeyboardButton("📊 My Stats", callback_data="menu_stats")
        ],
        [
            types.InlineKeyboardButton("❓ Help", callback_data="menu_help"),
            types.InlineKeyboardButton("📋 Plan", callback_data="menu_plan")
        ]
    ]
    
    # Admin button for owner
    if uid == OWNER_ID:
        kb.append([types.InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    
    kb.append([types.InlineKeyboardButton("✖️ Close", callback_data="close")])
    
    return types.InlineKeyboardMarkup(kb)

def thumb_kb():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("👁️ View", callback_data="thumb_view"),
            types.InlineKeyboardButton("🗑️ Delete", callback_data="thumb_delete")
        ],
        [types.InlineKeyboardButton("🔙 Back", callback_data="back_main")]
    ])

def upload_kb():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("✏️ Rename", callback_data="rename"),
            types.InlineKeyboardButton("📄 File", callback_data="up_file"),
            types.InlineKeyboardButton("🎬 Video", callback_data="up_video")
        ],
        [types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

def rename_kb():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("📝 Default Name", callback_data="ren_default"),
            types.InlineKeyboardButton("✏️ Custom Name", callback_data="ren_custom")
        ],
        [types.InlineKeyboardButton("🔙 Back", callback_data="back_upload")]
    ])

def yt_kb():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("🎬 Video (Best)", callback_data="yt_video"),
            types.InlineKeyboardButton("🎵 MP3 Audio", callback_data="yt_audio")
        ],
        [
            types.InlineKeyboardButton("📹 720p", callback_data="yt_720"),
            types.InlineKeyboardButton("📹 480p", callback_data="yt_480"),
            types.InlineKeyboardButton("📹 360p", callback_data="yt_360")
        ],
        [types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

def admin_kb():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
            types.InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")
        ],
        [
            types.InlineKeyboardButton("👑 Add Pro", callback_data="admin_addpro"),
            types.InlineKeyboardButton("🚫 Ban User", callback_data="admin_ban")
        ],
        [
            types.InlineKeyboardButton("✅ Unban User", callback_data="admin_unban"),
            types.InlineKeyboardButton("👤 User Info", callback_data="admin_userinfo")
        ],
        [types.InlineKeyboardButton("🔙 Back", callback_data="back_main")]
    ])

def broadcast_confirm_kb():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("✅ Send", callback_data="bc_confirm"),
            types.InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel")
        ]
    ])

# =======================
# YOUTUBE DOWNLOAD
# =======================
async def download_yt(uid: int, url: str, msg, format_type: str = "best"):
    """Download YouTube video/audio"""
    
    last_update = {"t": 0, "text": ""}
    
    def progress_hook(d):
        sess = session_get(uid)
        if sess and sess.get("cancel"):
            raise Exception("CANCELLED")
        
        if d["status"] != "downloading":
            return
        
        now = time.time()
        if now - last_update["t"] < 3:
            return
        
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes") or 0
        speed = d.get("speed") or 0
        
        if total > 0:
            pct = done / total * 100
            text = f"⬇️ **Downloading...**\n\n📦 {human_size(done)}/{human_size(total)} ({pct:.0f}%)\n⚡ {human_size(speed)}/s"
            
            if text != last_update["text"]:
                last_update["t"] = now
                last_update["text"] = text
                asyncio.get_event_loop().create_task(safe_edit(msg, text, cancel_kb()))
    
    # Base options
    opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": f"{DOWNLOAD_DIR}/%(title).100s.%(ext)s",
        "noplaylist": True,
        "progress_hooks": [progress_hook],
        "concurrent_fragment_downloads": 8,
        "buffersize": 65536,
        "http_chunk_size": 10485760,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
            }
        }
    }
    
    # Add cookies if available
    cookies = get_cookies_path()
    if cookies:
        opts["cookiefile"] = cookies
    
    # Set format based on type
    if format_type == "audio":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192"
        }]
    elif format_type == "720":
        opts["format"] = "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
    elif format_type == "480":
        opts["format"] = "bestvideo[height<=480]+bestaudio/best[height<=480]/best"
    elif format_type == "360":
        opts["format"] = "bestvideo[height<=360]+bestaudio/best[height<=360]/best"
    else:
        # Best quality - prefer pre-merged
        opts["format"] = "b/bv+ba/best"
    
    loop = asyncio.get_event_loop()
    
    def do_download():
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            if format_type == "audio":
                path = os.path.splitext(path)[0] + ".mp3"
            return path, info.get("title", "video")
    
    path, title = await loop.run_in_executor(None, do_download)
    return path, title

# =======================
# DIRECT DOWNLOAD
# =======================
async def download_direct(uid: int, url: str, msg):
    """Fast async download for direct links"""
    
    timeout = ClientTimeout(total=600, connect=30)
    
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
            start = time.time()
            last_update = 0
            
            with open(path, "wb") as f:
                async for chunk in resp.content.iter_chunked(524288):
                    sess = session_get(uid)
                    if sess and sess.get("cancel"):
                        raise Exception("CANCELLED")
                    
                    f.write(chunk)
                    done += len(chunk)
                    
                    now = time.time()
                    if now - last_update >= 3:
                        last_update = now
                        speed = done / max(1, now - start)
                        if total > 0:
                            pct = done / total * 100
                            text = f"⬇️ **Downloading...**\n\n📦 {human_size(done)}/{human_size(total)} ({pct:.0f}%)\n⚡ {human_size(speed)}/s"
                        else:
                            text = f"⬇️ **Downloading...**\n\n📦 {human_size(done)}\n⚡ {human_size(speed)}/s"
                        await safe_edit(msg, text, cancel_kb())
            
            return path, os.path.splitext(name)[0]

# =======================
# SCREENSHOTS
# =======================
async def make_screenshots(path: str, count: int = 5):
    """Generate screenshots in parallel"""
    screens = []
    out_dir = os.path.join(DOWNLOAD_DIR, f"ss_{int(time.time())}")
    os.makedirs(out_dir, exist_ok=True)
    
    try:
        cmd = f'ffprobe -v error -show_entries format=duration -of csv=p=0 "{path}"'
        result = await asyncio.create_subprocess_shell(
            cmd, 
            stdout=asyncio.subprocess.PIPE, 
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await result.communicate()
        dur = float(stdout.decode().strip() or "0")
        
        if dur <= 0:
            return [], out_dir
        
        interval = dur / (count + 1)
        
        # Generate all screenshots in parallel
        async def gen_ss(i):
            t = interval * i
            out = os.path.join(out_dir, f"{i}.jpg")
            cmd = f'ffmpeg -ss {t} -i "{path}" -vframes 1 -q:v 5 -y "{out}" 2>/dev/null'
            proc = await asyncio.create_subprocess_shell(cmd)
            await proc.wait()
            return out if os.path.exists(out) else None
        
        tasks = [gen_ss(i) for i in range(1, count + 1)]
        results = await asyncio.gather(*tasks)
        screens = [r for r in results if r]
        
        return screens, out_dir
    except Exception as e:
        print(f"Screenshot error: {e}")
        return [], out_dir

# =======================
# UPLOAD
# =======================
async def do_upload(uid: int, msg, path: str, name: str, as_video: bool):
    """Upload file with progress"""
    user = user_get(uid)
    thumb = user.get("thumb")
    if thumb and not os.path.exists(thumb):
        thumb = None
    
    start = time.time()
    last = {"t": 0}
    size = os.path.getsize(path)
    
    async def prog(done, total):
        sess = session_get(uid)
        if sess and sess.get("cancel"):
            raise Exception("CANCELLED")
        
        now = time.time()
        if now - last["t"] < 3:
            return
        last["t"] = now
        
        speed = done / max(1, now - start)
        pct = (done / total * 100) if total else 0
        await safe_edit(msg, f"📤 **Uploading...**\n\n📦 {human_size(done)}/{human_size(total)} ({pct:.0f}%)\n⚡ {human_size(speed)}/s", cancel_kb())
    
    if as_video:
        await app.send_video(
            uid, path, 
            caption=f"🎬 `{name}`", 
            file_name=name, 
            supports_streaming=True, 
            thumb=thumb, 
            progress=prog
        )
        
        # Generate screenshots
        await safe_edit(msg, "📸 **Generating screenshots...**", None)
        screens, ss_dir = await make_screenshots(path, 5)
        if screens:
            media = [types.InputMediaPhoto(s) for s in screens]
            try:
                await app.send_media_group(uid, media)
            except Exception as e:
                print(f"Screenshot send error: {e}")
        shutil.rmtree(ss_dir, ignore_errors=True)
    else:
        await app.send_document(
            uid, path, 
            caption=f"📄 `{name}`", 
            file_name=name, 
            thumb=thumb, 
            progress=prog
        )
    
    # Update usage quota
    if uid != OWNER_ID and not user.get("is_pro"):
        user["used"] = user.get("used", 0) + size
        db_save()

# =======================
# BOT CLIENT
# =======================
app = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# =======================
# COMMAND HANDLERS
# =======================
@app.on_message(filters.command("start") & filters.private)
async def cmd_start(_, m):
    uid = m.from_user.id
    user_get(uid)
    db_save()
    
    text = f"""👋 **Welcome {m.from_user.first_name}!**

🚀 I can download videos from:
• YouTube, Instagram, Twitter
• TikTok, Facebook, Reddit
• Any direct link

📤 **Just send me a link!**

🔧 Use the menu below for settings."""
    
    await m.reply_text(text, reply_markup=main_menu_kb(uid))

@app.on_message(filters.command("menu") & filters.private)
async def cmd_menu(_, m):
    uid = m.from_user.id
    await m.reply_text("📋 **Main Menu**", reply_markup=main_menu_kb(uid))

@app.on_message(filters.command("admin") & filters.private)
async def cmd_admin(_, m):
    if m.from_user.id != OWNER_ID:
        return await m.reply_text("❌ Not authorized!")
    await m.reply_text("⚙️ **Admin Panel**", reply_markup=admin_kb())

# =======================
# TEXT HANDLER
# =======================
@app.on_message(filters.text & filters.private & ~filters.command(["start", "menu", "admin"]))
async def on_text(_, m):
    uid = m.from_user.id
    user = user_get(uid)
    text = m.text.strip()
    
    if user.get("is_banned"):
        return await m.reply_text("❌ You are banned!")
    
    # === STATE HANDLERS ===
    
    # Custom rename
    if user.get("state") == "rename":
        sess = session_get(uid)
        if not sess:
            user["state"] = "none"
            db_save()
            return await m.reply_text("❌ Session expired!")
        
        new_name = safe_name(text) + sess.get("ext", "")
        sess["name"] = new_name
        session_set(uid, sess)
        user["state"] = "none"
        db_save()
        return await m.reply_text(f"✅ **Renamed to:** `{new_name}`", reply_markup=upload_kb())
    
    # Broadcast message
    if user.get("state") == "broadcast" and uid == OWNER_ID:
        user["state"] = "none"
        user["bc_text"] = text
        db_save()
        
        count = len([u for u in DB["users"].values() if not u.get("is_banned")])
        return await m.reply_text(
            f"📢 **Broadcast Preview:**\n\n{text}\n\n👥 Will send to: {count} users",
            reply_markup=broadcast_confirm_kb()
        )
    
    # Add Pro user
    if user.get("state") == "addpro" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            target_id = int(text)
            target = user_get(target_id)
            target["is_pro"] = True
            db_save()
            return await m.reply_text(f"✅ User `{target_id}` is now PRO!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid user ID!", reply_markup=admin_kb())
    
    # Ban user
    if user.get("state") == "ban" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            target_id = int(text)
            if target_id == OWNER_ID:
                return await m.reply_text("❌ Can't ban owner!", reply_markup=admin_kb())
            target = user_get(target_id)
            target["is_banned"] = True
            db_save()
            return await m.reply_text(f"✅ User `{target_id}` banned!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid user ID!", reply_markup=admin_kb())
    
    # Unban user
    if user.get("state") == "unban" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        