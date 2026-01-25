import os, re, shutil, time, asyncio, random, subprocess, json
from datetime import datetime, timedelta
from pyrogram import Client, filters, types, enums, idle, errors
from yt_dlp import YoutubeDL
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp import web

# --- MASTER CONFIG ---
OWNER_ID = 519459195
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", 0))
INVITE_LINK = "https://t.me/+eooytvOAwjc0NTI1"
CONTACT_URL = "https://t.me/poocha"
DOWNLOAD_DIR = "/app/downloads"
THUMB_DIR = "/app/thumbnails"
DB_FILE = "/app/database.json"
COOKIES_FILE = "/app/cookies.txt"
QUOTA_LIMIT = 5 * 1024 * 1024 * 1024 

USER_GREETINGS = ["Thanks for chatting with me.", "Glad you’re here.", "Appreciate you using this bot."]
ADMIN_GREETINGS = ["Chief, systems are ready.", "Ready when you are, chief.", "Standing by."]

# --- PERSISTENT DATABASE ---
DB = {"users": {}, "active": {}, "history": [], "temp_bc": None}

def save_db():
    with open(DB_FILE, "w") as f: json.dump(DB, f, default=str)

def load_db():
    global DB
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f: 
                DB = json.load(f)
                if "history" not in DB: DB["history"] = []
                if "active" not in DB: DB["active"] = {}
        except: pass

def get_user(uid):
    uid = str(uid)
    today = str(datetime.now().date())
    if uid not in DB["users"]:
        DB["users"][uid] = {"used": 0, "last_reset": today, "warnings": 0, "is_paid": False, "thumb": None, "state": "none", "last_task": None}
    if DB["users"][uid].get("last_reset") != today:
        DB["users"][uid].update({"used": 0, "last_reset": today})
    return DB["users"][uid]

def format_size(size):
    if not size: return "0B"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024: return f"{size:.2f}{unit}"
        size /= 1024
    return f"{size:.2f}TB"

app = Client("dl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, sleep_threshold=120)
scheduler = AsyncIOScheduler()

# --- UTILS ---
async def is_subscribed(uid):
    if uid == OWNER_ID: return True
    try:
        m = await app.get_chat_member(CHANNEL_ID, uid)
        return m.status in [enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except: return False

async def safe_edit(msg, text, reply_markup=None):
    try: await msg.edit(text, reply_markup=reply_markup)
    except errors.MessageNotModified: pass

async def take_screenshots(video_path, uid):
    output_dir = os.path.join(DOWNLOAD_DIR, f"screens_{uid}")
    if not os.path.exists(output_dir): os.makedirs(output_dir)
    try:
        cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{video_path}"'
        duration = float(subprocess.check_output(cmd, shell=True))
        screens = []
        for i in range(1, 11):
            time_pos = (duration / 11) * i
            out_path = os.path.join(output_dir, f"thumb_{i}.jpg")
            subprocess.call(['ffmpeg', '-ss', str(time_pos), '-i', video_path, '-vframes', '1', '-q:v', '2', out_path, '-y'], stderr=subprocess.DEVNULL)
            if os.path.exists(out_path): screens.append(types.InputMediaPhoto(out_path))
        return screens
    except: return []

async def progress_hook(current, total, msg, start_time, action):
    now = time.time()
    if not hasattr(msg, "last_up"): msg.last_up = 0
    if (now - msg.last_up) < 10: return 
    msg.last_up = now
    try:
        p = (current * 100 / total) if total > 0 else 0
        bar = "✅" * int(p/10) + "⬜" * (10 - int(p/10))
        await safe_edit(msg, f"⏳ {action}...\n`{bar}` {p:.1f}%\n📦 {format_size(current)} / {format_size(total)}")
    except: pass

# --- UI KEYBOARDS ---
def get_main_btns(uid):
    user = get_user(uid)
    btns = [[types.InlineKeyboardButton("❓ Help", callback_data="m_help"), types.InlineKeyboardButton("🆔 My ID", callback_data="m_id")],
            [types.InlineKeyboardButton("🖼 Thumbnail Manager", callback_data="m_thumb")]]
    if uid == OWNER_ID:
        btns.append([types.InlineKeyboardButton("⚙️ Admin Panel", callback_data="m_adm")])
    else:
        btns.append([types.InlineKeyboardButton("📊 My Status", callback_data="m_stat"), types.InlineKeyboardButton("💎 Upgrade", url=CONTACT_URL)])
    btns.append([types.InlineKeyboardButton("🚪 Exit", callback_data="exit")])
    return types.InlineKeyboardMarkup(btns)

def get_ready_btns():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("Video 🎥", callback_data="u_vid"), types.InlineKeyboardButton("File 📄", callback_data="u_fil")],
        [types.InlineKeyboardButton("Upload + 📸", callback_data="u_scr"), types.InlineKeyboardButton("Rename ✏️", callback_data="u_ren")],
        [types.InlineKeyboardButton("Cancel ❌", callback_data="cancel")]
    ])

# --- COMMANDS ---
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(_, m):
    uid = m.from_user.id
    msg = random.choice(ADMIN_GREETINGS if uid == OWNER_ID else USER_GREETINGS)
    await m.reply(msg, reply_markup=get_main_btns(uid))

@app.on_message(filters.command("setcustomthumbnail") & filters.private)
async def set_thumb_cmd(_, m):
    await m.reply("📸 **Please send the photo** you want to set as your custom thumbnail.")

@app.on_message(filters.photo & filters.private)
async def save_photo_as_thumb(_, m):
    uid = m.from_user.id
    path = os.path.join(THUMB_DIR, f"{uid}.jpg")
    await m.download(path)
    get_user(uid)["thumb"] = path; save_db()
    await m.reply("✅ **Custom Thumbnail Saved.** It will be used for all your future uploads.")

@app.on_message((filters.video | filters.document | filters.forwarded) & filters.private)
async def handle_media(client, m):
    uid = m.from_user.id
    if not await is_subscribed(uid): return await m.reply("⚠️ Join channel first.", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Join", url=INVITE_LINK)], [types.InlineKeyboardButton("🔄 Verify", callback_data="v_sub")]]))
    
    status_msg = await m.reply("📥 Downloading media for processing...")
    path = os.path.join(DOWNLOAD_DIR, f"file_{uid}")
    await m.download(path, progress=progress_hook, progress_args=(status_msg, time.time(), "Downloading"))
    DB["active"][str(uid)] = {"path": path, "name": "file", "status": "ready", "size": (m.video.file_size if m.video else m.document.file_size)}
    await status_msg.edit("✅ Media Ready.", reply_markup=get_ready_btns())

@app.on_message(filters.text & ~filters.command(["start", "setcustomthumbnail"]) & filters.private)
async def handle_text(client, m):
    uid, uid_str = m.from_user.id, str(m.from_user.id)
    user = get_user(uid)

    if user["state"] == "pending_bc" and uid == OWNER_ID:
        DB["temp_bc"] = m.text; user["state"] = "none"
        btns = [[types.InlineKeyboardButton("✅ Confirm", callback_data="bc_yes"), types.InlineKeyboardButton("❌ Stop", callback_data="m_adm")]]
        return await m.reply(f"📝 **Broadcast Preview:**\n\n{m.text}", reply_markup=types.InlineKeyboardMarkup(btns))

    if uid_str in DB["active"] and DB["active"][uid_str].get("status") == "renaming":
        state = DB["active"][uid_str]
        ext = os.path.splitext(state["path"])[1]
        new_name = m.text if m.text.endswith(ext) else f"{m.text}{ext}"
        new_path = os.path.join(DOWNLOAD_DIR, new_name)
        os.rename(state["path"], new_path); state.update({"path": new_path, "name": new_name, "status": "ready"})
        return await m.reply(f"✅ Renamed.", reply_markup=get_ready_btns())

    if m.text.startswith("http"):
        if not await is_subscribed(uid): return await m.reply("⚠️ Join channel first.", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Join", url=INVITE_LINK)], [types.InlineKeyboardButton("🔄 Verify", callback_data="v_sub")]]))
        
        status_msg = await m.reply("🔍 Analyzing Link...")
        ydl_opts = {'quiet': True, 'extractor_args': {'youtube': {'player_client': ['ios', 'android']}}}
        if os.path.exists(COOKIES_FILE): ydl_opts['cookiefile'] = COOKIES_FILE

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(m.text, download=False)
                size = info.get('filesize_approx') or info.get('filesize') or 0
                if "youtube.com" in m.text or "youtu.be" in m.text:
                    DB["active"][uid_str] = {"url": m.text, "status": "choosing", "size": size}
                    btns = [[types.InlineKeyboardButton(f"Video ({format_size(size)})", callback_data="dl_vid")],
                            [types.InlineKeyboardButton("Audio (MP3)", callback_data="dl_aud"), types.InlineKeyboardButton("Cancel", callback_data="cancel")]]
                    return await status_msg.edit("🎬 YouTube Detected:", reply_markup=types.InlineKeyboardMarkup(btns))
                
                await status_msg.edit("⏳ Downloading...")
                ydl.download([m.text]); path = ydl.prepare_filename(info)
                DB["active"][uid_str] = {"path": path, "name": os.path.basename(path), "status": "ready", "size": size}
                DB["history"].append({"uid": uid, "name": info.get('title', 'file')[:30], "size": size}); save_db()
                await status_msg.edit("✅ Ready.", reply_markup=get_ready_btns())
        except Exception as e: await status_msg.edit(f"❌ Error: {str(e)[:50]}")

@app.on_callback_query()
async def cb_handler(client, cb: types.CallbackQuery):
    uid, uid_str = cb.from_user.id, str(cb.from_user.id)
    data, user = cb.data, get_user(uid)
    await cb.answer()

    if data == "back_main": return await safe_edit(cb.message, random.choice(ADMIN_GREETINGS if uid == OWNER_ID else USER_GREETINGS), reply_markup=get_main_btns(uid))
    if data == "m_thumb":
        btns = [[types.InlineKeyboardButton("👁 View", callback_data="v_t"), types.InlineKeyboardButton("🗑 Del", callback_data="d_t")], [types.InlineKeyboardButton("🔙 Back", callback_data="back_main")]] if user["thumb"] else [[types.InlineKeyboardButton("✨ Set Thumbnail", callback_data="back_main")], [types.InlineKeyboardButton("🔙 Back", callback_data="back_main")]]
        return await safe_edit(cb.message, "🖼 **Thumbnail Manager**", reply_markup=types.InlineKeyboardMarkup(btns))

    if data == "v_t":
        if user["thumb"]: await cb.message.reply_photo(user["thumb"], caption="Your Custom Thumbnail")
        return
    if data == "d_t":
        if user["thumb"]: os.remove(user["thumb"]); user["thumb"] = None; save_db()
        return await safe_edit(cb.message, "🗑 **Thumbnail Deleted.**", reply_markup=get_main_btns(uid))

    if data == "m_adm" and uid == OWNER_ID:
        btns = [[types.InlineKeyboardButton("📊 Reports", callback_data="a_rep"), types.InlineKeyboardButton("📢 Broadcast", callback_data="a_bc")], 
                [types.InlineKeyboardButton("🛠 Stability", callback_data="a_stb"), types.InlineKeyboardButton("🔙 Back", callback_data="back_main")]]
        return await safe_edit(cb.message, "🛠 **Admin Panel**", reply_markup=types.InlineKeyboardMarkup(btns))

    if data == "a_rep" and uid == OWNER_ID:
        log = "".join([f"• `{e['uid']}` | {format_size(e['size'])}\n" for e in DB["history"][-10:]])
        return await safe_edit(cb.message, f"📈 **Stats**\nUsers: {len(DB['users'])}\n\n**Log:**\n{log}", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("🔙 Back", callback_data="m_adm")]]))

    if data == "cancel":
        DB["active"].pop(uid_str, None); save_db(); await safe_edit(cb.message, "❌ Cancelled.")

    if uid_str not in DB["active"]: return 
    state = DB["active"][uid_str]

    if data.startswith("dl_"):
        await safe_edit(cb.message, "⏳ Downloading YouTube..."); is_vid = data == "dl_vid"
        ydl_opts = {'format': 'bestvideo+bestaudio/best' if is_vid else 'bestaudio/best', 'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s'}
        if os.path.exists(COOKIES_FILE): ydl_opts['cookiefile'] = COOKIES_FILE
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(state["url"], download=True); path = ydl.prepare_filename(info)
            if not is_vid: path = os.path.splitext(path)[0] + ".mp3"
            state.update({"path": path, "name": os.path.basename(path), "status": "ready"}); save_db()
            await safe_edit(cb.message, "✅ Ready.", reply_markup=get_ready_btns())

    if data.startswith("u_"):
        await safe_edit(cb.message, "📤 Uploading..."); path = state["path"]
        # THUMBNAIL LOGIC: Only use if manual thumb is set
        custom_thumb = user["thumb"] if (user["thumb"] and os.path.exists(user["thumb"])) else None
        try:
            if data == "u_scr":
                screens = await take_screenshots(path, uid)
                if screens: await client.send_media_group(uid, screens)
            
            if data == "u_vid": await client.send_video(uid, video=path, thumb=custom_thumb, caption=f"`{state['name']}`", progress=progress_hook, progress_args=(cb.message, time.time(), "Uploading"))
            else: await client.send_document(uid, document=path, thumb=custom_thumb, caption=f"`{state['name']}`", progress=progress_hook, progress_args=(cb.message, time.time(), "Uploading"))
            if uid != OWNER_ID: user["used"] += state["size"]
            save_db(); await cb.message.delete()
        finally:
            if os.path.exists(path): os.remove(path)
            DB["active"].pop(uid_str, None); save_db()

# --- STARTUP ---
async def main():
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    if not os.path.exists(THUMB_DIR): os.makedirs(THUMB_DIR)
    load_db(); await app.start()
    server = web.Application(); server.add_routes([web.get('/', lambda r: web.Response(text="Bot Alive"))])
    runner = web.AppRunner(server); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8000).start()
    scheduler.start(); await idle()

if __name__ == "__main__":
    app.run(main())
