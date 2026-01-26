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
        try:
            target_id = int(text)
            target = user_get(target_id)
            target["is_banned"] = False
            db_save()
            return await m.reply_text(f"✅ User `{target_id}` unbanned!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid user ID!", reply_markup=admin_kb())
    
    # User info
    if user.get("state") == "userinfo" and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            target_id = int(text)
            if str(target_id) in DB["users"]:
                t = DB["users"][str(target_id)]
                info = f"""👤 **User Info**

🆔 ID: `{target_id}`
👑 Pro: {'Yes' if t.get('is_pro') else 'No'}
🚫 Banned: {'Yes' if t.get('is_banned') else 'No'}
📦 Used Today: {human_size(t.get('used', 0))}
📅 Joined: {t.get('joined', 'Unknown')}"""
                return await m.reply_text(info, reply_markup=admin_kb())
            else:
                return await m.reply_text("❌ User not found!", reply_markup=admin_kb())
        except:
            return await m.reply_text("❌ Invalid user ID!", reply_markup=admin_kb())
    
    # === URL HANDLER ===
    if not text.startswith("http"):
        return
    
    # Check subscription
    if not await is_subscribed(uid):
        return await m.reply_text("⚠️ **Please join our channel first!**", reply_markup=join_kb())
    
    status = await m.reply_text("🔍 **Analyzing link...**", reply_markup=cancel_kb())
    session_set(uid, {"url": text, "cancel": False})
    
    # YouTube
    if is_yt(text):
        return await safe_edit(status, "🎬 **YouTube Detected!**\n\nChoose format:", yt_kb())
    
    # Other links
    try:
        await safe_edit(status, "⬇️ **Starting download...**", cancel_kb())
        
        # Try yt-dlp first
        try:
            path, title = await download_yt(uid, text, status, "best")
        except:
            path, title = await download_direct(uid, text, status)
        
        if not os.path.exists(path):
            raise Exception("Download failed!")
        
        name = os.path.basename(path)
        size = os.path.getsize(path)
        
        session_set(uid, {
            "url": text, 
            "path": path, 
            "name": name, 
            "ext": get_ext(name), 
            "size": size, 
            "cancel": False
        })
        
        await safe_edit(
            status, 
            f"✅ **Download Complete!**\n\n📄 **Name:** `{name}`\n📦 **Size:** {human_size(size)}", 
            upload_kb()
        )
        
    except Exception as e:
        session_clear(uid)
        if "CANCELLED" in str(e):
            await safe_edit(status, "❌ **Cancelled!**", None)
        else:
            await safe_edit(status, f"❌ **Error:** {str(e)[:100]}", None)

# =======================
# FILE HANDLER
# =======================
@app.on_message((filters.video | filters.document | filters.audio) & filters.private)
async def on_file(_, m):
    uid = m.from_user.id
    
    if user_get(uid).get("is_banned"):
        return
    
    if not await is_subscribed(uid):
        return await m.reply_text("⚠️ Join channel first!", reply_markup=join_kb())
    
    media = m.video or m.document or m.audio
    status = await m.reply_text("⬇️ **Downloading file...**", reply_markup=cancel_kb())
    session_set(uid, {"cancel": False})
    
    try:
        name = safe_name(getattr(media, "file_name", None) or f"file_{int(time.time())}")
        path = os.path.join(DOWNLOAD_DIR, name)
        await m.download(path)
        
        size = os.path.getsize(path)
        session_set(uid, {"path": path, "name": name, "ext": get_ext(name), "size": size, "cancel": False})
        
        await safe_edit(
            status, 
            f"✅ **Download Complete!**\n\n📄 **Name:** `{name}`\n📦 **Size:** {human_size(size)}", 
            upload_kb()
        )
    except Exception as e:
        session_clear(uid)
        await safe_edit(status, f"❌ **Error:** {str(e)[:80]}", None)

# =======================
# PHOTO HANDLER (Thumbnail)
# =======================
@app.on_message(filters.photo & filters.private)
async def on_photo(_, m):
    uid = m.from_user.id
    
    if user_get(uid).get("is_banned"):
        return
    
    path = os.path.join(THUMB_DIR, f"{uid}.jpg")
    await m.download(path)
    user_get(uid)["thumb"] = path
    db_save()
    await m.reply_text("✅ **Thumbnail saved!**\n\nThis will be used for all your uploads.")

# =======================
# CALLBACK HANDLER
# =======================
@app.on_callback_query()
async def on_callback(_, cb):
    uid = cb.from_user.id
    data = cb.data
    user = user_get(uid)
    sess = session_get(uid)
    
    try:
        await cb.answer()
    except:
        pass
    
    if user.get("is_banned"):
        return await cb.answer("❌ You are banned!", show_alert=True)
    
    # ============ GENERAL ============
    
    if data == "check_join":
        if await is_subscribed(uid):
            return await safe_edit(cb.message, "✅ **Verified!** Now send me a link.", main_menu_kb(uid))
        return await cb.answer("❌ You haven't joined yet!", show_alert=True)
    
    if data == "close":
        try:
            await cb.message.delete()
        except:
            pass
        return
    
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
        return await safe_edit(cb.message, "❌ **Cancelled!**", None)
    
    if data == "back_main":
        user["state"] = "none"
        db_save()
        return await safe_edit(cb.message, "📋 **Main Menu**", main_menu_kb(uid))
    
    # ============ MAIN MENU ============
    
    if data == "menu_thumb":
        return await safe_edit(cb.message, "🖼️ **Thumbnail Settings**\n\nSend a photo to set as thumbnail.", thumb_kb())
    
    if data == "menu_stats":
        used = user.get("used", 0)
        limit = DAILY_LIMIT
        remaining = max(0, limit - used)
        
        text = f"""📊 **Your Statistics**

📦 **Used Today:** {human_size(used)}
📉 **Remaining:** {human_size(remaining)}
📈 **Daily Limit:** {human_size(limit)}

👑 **Pro Status:** {'Yes ✅' if user.get('is_pro') else 'No ❌'}
📅 **Joined:** {user.get('joined', 'Unknown')}"""
        
        return await safe_edit(cb.message, text, main_menu_kb(uid))
    
    if data == "menu_help":
        text = """❓ **How to Use**

1️⃣ Send any video link
2️⃣ Choose quality (for YouTube)
3️⃣ Wait for download
4️⃣ Choose: Rename / File / Video
5️⃣ Get your file + screenshots!

**Supported Sites:**
• YouTube, Instagram, Twitter
• TikTok, Facebook, Reddit
• And 1000+ more!

**Tips:**
• Send a photo to set thumbnail
• Pro users have unlimited downloads"""
        
        return await safe_edit(cb.message, text, main_menu_kb(uid))
    
    if data == "menu_plan":
        if user.get("is_pro"):
            text = "👑 **You are a PRO user!**\n\n✅ Unlimited downloads\n✅ Priority support"
        else:
            used = user.get("used", 0)
            remaining = max(0, DAILY_LIMIT - used)
            text = f"""📋 **Free Plan**

📦 Daily Limit: {human_size(DAILY_LIMIT)}
📉 Remaining: {human_size(remaining)}

**Upgrade to PRO:**
• Unlimited downloads
• Priority support

Contact admin to upgrade!"""
        
        return await safe_edit(cb.message, text, main_menu_kb(uid))
    
    # ============ THUMBNAIL ============
    
    if data == "thumb_view":
        thumb = user.get("thumb")
        if thumb and os.path.exists(thumb):
            await cb.message.reply_photo(thumb, caption="🖼️ Your current thumbnail")
        else:
            await cb.answer("❌ No thumbnail set!", show_alert=True)
        return
    
    if data == "thumb_delete":
        thumb = user.get("thumb")
        if thumb and os.path.exists(thumb):
            try:
                os.remove(thumb)
            except:
                pass
        user["thumb"] = None
        db_save()
        return await safe_edit(cb.message, "✅ **Thumbnail deleted!**", thumb_kb())
    
    # ============ ADMIN PANEL ============
    
    if data == "admin_panel":
        if uid != OWNER_ID:
            return await cb.answer("❌ Not authorized!", show_alert=True)
        return await safe_edit(cb.message, "⚙️ **Admin Panel**", admin_kb())
    
    if data == "admin_stats":
        if uid != OWNER_ID:
            return await cb.answer("❌ Not authorized!", show_alert=True)
        
        total_users = len(DB["users"])
        pro_users = len([u for u in DB["users"].values() if u.get("is_pro")])
        banned = len([u for u in DB["users"].values() if u.get("is_banned")])
        active = len(DB["sessions"])
        
        # Disk usage
        total, used, free = shutil.disk_usage("/")
        
        text = f"""📊 **Bot Statistics**

👥 **Users:** {total_users}
👑 **Pro Users:** {pro_users}
🚫 **Banned:** {banned}
⚡ **Active Sessions:** {active}

💾 **Disk Usage:**
• Total: {human_size(total)}
• Used: {human_size(used)}
• Free: {human_size(free)}

🍪 **Cookies:** {'✅ Found' if get_cookies_path() else '❌ Not found'}"""
        
        return await safe_edit(cb.message, text, admin_kb())
    
    if data == "admin_broadcast":
        if uid != OWNER_ID:
            return await cb.answer("❌ Not authorized!", show_alert=True)
        user["state"] = "broadcast"
        db_save()
        return await safe_edit(cb.message, "📢 **Broadcast**\n\nSend me the message to broadcast:", cancel_kb())
    
    if data == "bc_confirm":
        if uid != OWNER_ID:
            return
        
        bc_text = user.get("bc_text", "")
        if not bc_text:
            return await safe_edit(cb.message, "❌ No message to send!", admin_kb())
        
        await safe_edit(cb.message, "📢 **Broadcasting...**", None)
        
        sent = 0
        failed = 0
        for user_id in list(DB["users"].keys()):
            if DB["users"][user_id].get("is_banned"):
                continue
            try:
                await app.send_message(int(user_id), bc_text)
                sent += 1
                await asyncio.sleep(0.1)
            except:
                failed += 1
        
        user["bc_text"] = ""
        db_save()
        
        return await safe_edit(cb.message, f"✅ **Broadcast Complete!**\n\n📤 Sent: {sent}\n❌ Failed: {failed}", admin_kb())
    
    if data == "bc_cancel":
        user["state"] = "none"
        user["bc_text"] = ""
        db_save()
        return await safe_edit(cb.message, "❌ Broadcast cancelled!", admin_kb())
    
    if data == "admin_addpro":
        if uid != OWNER_ID:
            return await cb.answer("❌ Not authorized!", show_alert=True)
        user["state"] = "addpro"
        db_save()
        return await safe_edit(cb.message, "👑 **Add Pro User**\n\nSend me the user ID:", cancel_kb())
    
    if data == "admin_ban":
        if uid != OWNER_ID:
            return await cb.answer("❌ Not authorized!", show_alert=True)
        user["state"] = "ban"
        db_save()
        return await safe_edit(cb.message, "🚫 **Ban User**\n\nSend me the user ID:", cancel_kb())
    
    if data == "admin_unban":
        if uid != OWNER_ID:
            return await cb.answer("❌ Not authorized!", show_alert=True)
        user["state"] = "unban"
        db_save()
        return await safe_edit(cb.message, "✅ **Unban User**\n\nSend me the user ID:", cancel_kb())
    
    if data == "admin_userinfo":
        if uid != OWNER_ID:
            return await cb.answer("❌ Not authorized!", show_alert=True)
        user["state"] = "userinfo"
        db_save()
        return await safe_edit(cb.message, "👤 **User Info**\n\nSend me the user ID:", cancel_kb())
    
    # ============ YOUTUBE ============
    
    if data.startswith("yt_"):
        if not sess or not sess.get("url"):
            return await safe_edit(cb.message, "❌ Session expired!", None)
        
        format_map = {
            "yt_video": "best",
            "yt_audio": "audio",
            "yt_720": "720",
            "yt_480": "480",
            "yt_360": "360"
        }
        
        format_type = format_map.get(data, "best")
        format_name = "Video" if format_type == "best" else "Audio" if format_type == "audio" else f"{format_type}p"
        
        try:
            await safe_edit(cb.message, f"⬇️ **Downloading {format_name}...**", cancel_kb())
            path, title = await download_yt(uid, sess["url"], cb.message, format_type)
            
            if not os.path.exists(path):
                raise Exception("Download failed!")
            
            name = os.path.basename(path)
            size = os.path.getsize(path)
            
            session_set(uid, {
                "url": sess["url"], 
                "path": path, 
                "name": name, 
                "ext": get_ext(name), 
                "size": size, 
                "cancel": False
            })
            
            await safe_edit(
                cb.message, 
                f"✅ **Download Complete!**\n\n📄 **Name:** `{name}`\n📦 **Size:** {human_size(size)}", 
                upload_kb()
            )
        except Exception as e:
            session_clear(uid)
            if "CANCELLED" in str(e):
                await safe_edit(cb.message, "❌ **Cancelled!**", None)
            else:
                await safe_edit(cb.message, f"❌ **Error:** {str(e)[:100]}", None)
        return
    
    # ============ RENAME ============
    
    if data == "rename":
        if not sess:
            return await safe_edit(cb.message, "❌ Session expired!", None)
        return await safe_edit(cb.message, "✏️ **Rename File**\n\nChoose an option:", rename_kb())
    
    if data == "ren_default":
        if not sess:
            return await safe_edit(cb.message, "❌ Session expired!", None)
        return await safe_edit(
            cb.message, 
            f"📝 **Using default name:**\n`{sess['name']}`", 
            upload_kb()
        )
    
    if data == "ren_custom":
        if not sess:
            return await safe_edit(cb.message, "❌ Session expired!", None)
        user["state"] = "rename"
        db_save()
        return await safe_edit(
            cb.message, 
            f"✏️ **Current name:** `{sess['name']}`\n\nSend me the new name (without extension):", 
            cancel_kb()
        )
    
    if data == "back_upload":
        if not sess:
            return await safe_edit(cb.message, "❌ Session expired!", None)
        return await safe_edit(
            cb.message, 
            f"📄 **Name:** `{sess['name']}`\n📦 **Size:** {human_size(sess.get('size', 0))}", 
            upload_kb()
        )
    
    # ============ UPLOAD ============
    
    if data in ["up_file", "up_video"]:
        if not sess or not sess.get("path"):
            return await safe_edit(cb.message, "❌ Session expired!", None)
        
        if not os.path.exists(sess["path"]):
            session_clear(uid)
            return await safe_edit(cb.message, "❌ File not found!", None)
        
        as_video = (data == "up_video")
        
        try:
            await safe_edit(cb.message, "📤 **Uploading...**", cancel_kb())
            await do_upload(uid, cb.message, sess["path"], sess["name"], as_video)
            
            # Cleanup
            try:
                os.remove(sess["path"])
            except:
                pass
            session_clear(uid)
            
            await safe_edit(cb.message, "✅ **Upload Complete!**", main_menu_kb(uid))
        except Exception as e:
            if "CANCELLED" in str(e):
                await safe_edit(cb.message, "❌ **Cancelled!**", None)
            else:
                await safe_edit(cb.message, f"❌ **Error:** {str(e)[:80]}", None)

# =======================
# HEALTH CHECK & MAIN
# =======================
async def health_check(_):
    return web.Response(text="OK")

async def main():
    # Create directories
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(THUMB_DIR, exist_ok=True)
    
    # Load database
    db_load()
    
    # Check cookies
    cookies = get_cookies_path()
    if cookies:
        print(f"✅ Cookies loaded: {cookies}")
    else:
        print("⚠️ No cookies found - some videos may not work")
    
    # Start bot
    await app.start()
    print("✅ Bot started successfully!")
    
    # Start health server
    web_app = web.Application()
    web_app.add_routes([web.get("/", health_check)])
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()
    print("✅ Health server on port 8000")
    
    # Keep running
    await idle()
    await app.stop()

if __name__ == "__main__":
    app.run(main())