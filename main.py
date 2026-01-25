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

# --- DB PERSISTENCE ---
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
    uid_s = str(uid)
    today = str(datetime.now().date())
    if uid_s not in DB["users"]:
        DB["users"][uid_s] = {"used": 0, "last_reset": today, "warnings": 0, "is_pro": False, "thumb": None, "state": "none", "last_task": None}
    if DB["users"][uid_s].get("last_reset") != today:
        DB["users"][uid_s].update({"used": 0, "last_reset": today})
    return DB["users"][uid_s]

def format_size(size):
    if not size: return "0B"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024: return f"{size:.2f}{unit}"
        size /= 1024
    return f"{size:.2f}TB"

app = Client("dl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, sleep_threshold=120)

# --- UTILS ---
async def is_subscribed(uid):
    if uid == OWNER_ID: return True
    try:
        m = await app.get_chat_member(CHANNEL_ID, uid)
        return m.status in [enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except errors.UserNotParticipant: return False
    except Exception: return False

async def safe_edit(msg, text, reply_markup=None):
    try: await msg.edit(text, reply_markup=reply_markup)
    except errors.MessageNotModified: pass
    except Exception: pass

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

# --- KEYBOARDS ---
def get_main_btns(uid):
    user = get_user(uid)
    btns = [[types.InlineKeyboardButton("❓ Help", callback_data="m_help"), types.InlineKeyboardButton("🆔 My ID", callback_data="m_id")],
            [types.InlineKeyboardButton("🖼 Thumbnail Manager", callback_data="m_thumb")]]
    if uid == OWNER_ID:
        btns.append([types.InlineKeyboardButton("⚙️ Admin Dashboard", callback_data="m_adm")])
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

def get_rename_menu():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("✅ Use Default Name", callback_data="u_default")],
        [types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

# --- HANDLERS ---
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(_, m):
    uid = m.from_user.id
    msg = random.choice(ADMIN_GREETINGS if uid == OWNER_ID else USER_GREETINGS)
    await m.reply(msg, reply_markup=get_main_btns(uid))

@app.on_message(filters.photo & filters.private)
async def save_thumb(_, m):
    uid = m.from_user.id
    path = os.path.join(THUMB_DIR, f"{uid}.jpg")
    await m.download(path)
    get_user(uid)["thumb"] = path; save_db()
    await m.reply("✅ Thumbnail Saved.", reply_markup=get_main_btns(uid))

@app.on_message((filters.video | filters.document | filters.forwarded) & filters.private)
async def handle_media(client, m):
    uid, uid_str = m.from_user.id, str(m.from_user.id)
    if not await is_subscribed(uid):
        return await m.reply("⚠️ Join channel first.", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Join", url=INVITE_LINK)], [types.InlineKeyboardButton("🔄 Verify", callback_data="v_sub")]]))
    
    status_msg = await m.reply("📥 Downloading media...")
    path = os.path.join(DOWNLOAD_DIR, f"file_{uid}")
    
    media = m.video or m.document
    orig_name = getattr(media, "file_name", "file.mp4")
    
    await m.download(path, progress=progress_hook, progress_args=(status_msg, time.time(), "Downloading"))
    DB["active"][uid_str] = {"path": path, "name": orig_name, "status": "ready", "size": media.file_size}
    save_db()
    await status_msg.edit(f"✅ Downloaded: `{orig_name}`\nChoose an option:", reply_markup=get_ready_btns())

@app.on_message(filters.text & ~filters.command(["start"]) & filters.private)
async def handle_text(client, m):
    uid, uid_str = m.from_user.id, str(m.from_user.id)
    user = get_user(uid)

    if user["state"] == "pending_bc" and uid == OWNER_ID:
        DB["temp_bc"] = m.text; user["state"] = "none"
        btns = [[types.InlineKeyboardButton("✅ Confirm", callback_data="bc_yes"), types.InlineKeyboardButton("❌ Stop", callback_data="m_adm")]]
        return await m.reply(f"📝 **Broadcast Preview:**\n\n{m.text}", reply_markup=types.InlineKeyboardMarkup(btns))

    if uid_str in DB["active"] and DB["active"][uid_str].get("status") == "renaming":
        state = DB["active"][uid_str]
        ext = os.path.splitext(state["path"])[1] or ".mp4"
        new_name = m.text if m.text.endswith(ext) else f"{m.text}{ext}"
        new_path = os.path.join(DOWNLOAD_DIR, new_name)
        os.rename(state["path"], new_path); state.update({"path": new_path, "name": new_name, "status": "ready"})
        save_db()
        return await m.reply(f"✅ Renamed to `{new_name}`", reply_markup=get_ready_btns())

    if m.text.startswith("http"):
        if not await is_subscribed(uid): 
            return await m.reply("⚠️ Join channel first.", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Join", url=INVITE_LINK)], [types.InlineKeyboardButton("🔄 Verify", callback_data="v_sub")]]))
        
        status_msg = await m.reply("🔍 Analyzing...")
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
                    return await status_msg.edit("🎬 YouTube Detection:", reply_markup=types.InlineKeyboardMarkup(btns))
                
                await status_msg.edit("⏳ Downloading to server...")
                ydl.download([m.text]); path = ydl.prepare_filename(info)
                DB["active"][uid_str] = {"path": path, "name": os.path.basename(path), "status": "ready", "size": size}
                DB["history"].append({"uid": uid, "name": os.path.basename(path), "size": size}); save_db()
                await status_msg.edit("✅ Ready.", reply_markup=get_ready_btns())
        except Exception as e: await status_msg.edit(f"❌ Error: {str(e)[:50]}")

@app.on_callback_query()
async def cb_handler(client, cb: types.CallbackQuery):
    uid, uid_str = cb.from_user.id, str(cb.from_user.id)
    data, user = cb.data, get_user(uid)
    await cb.answer()

    if data == "exit": return await cb.message.delete()
    if data == "m_id": return await cb.answer(f"Your ID: {uid}", show_alert=True)
    if data == "m_help": return await safe_edit(cb.message, "📖 **Help Menu**\n- Send links to download.\n- Forward files to rename.\n- Send photo for thumbnail.", reply_markup=get_main_btns(uid))
    
    if data == "v_sub":
        if await is_subscribed(uid): return await safe_edit(cb.message, "✅ Verified! You can now use the bot.", reply_markup=get_main_btns(uid))
        else: return await cb.answer("❌ You haven't joined yet!", show_alert=True)

    if data == "m_adm" and uid == OWNER_ID:
        btns = [[types.InlineKeyboardButton("📊 Reports", callback_data="a_rep"), types.InlineKeyboardButton("📢 Broadcast", callback_data="a_bc")], [types.InlineKeyboardButton("🔙 Back", callback_data="back_main")]]
        return await safe_edit(cb.message, "🛠 Admin Panel", reply_markup=types.InlineKeyboardMarkup(btns))

    if data == "a_bc" and uid == OWNER_ID:
        user["state"] = "pending_bc"; await safe_edit(cb.message, "📢 Send message to broadcast:")
        return

    if data == "bc_yes" and uid == OWNER_ID:
        msg, count = DB.get("temp_bc"), 0
        for u in list(DB["users"].keys()):
            try: await app.send_message(int(u), f"📢 **Broadcast**\n\n{msg}"); count += 1; await asyncio.sleep(0.1)
            except: pass
        return await safe_edit(cb.message, f"✅ Sent to {count} users.", reply_markup=get_main_btns(uid))

    if data == "a_rep" and uid == OWNER_ID:
        log = "".join([f"• `{e['uid']}` | {format_size(e['size'])}\n" for e in DB["history"][-10:]])
        return await safe_edit(cb.message, f"📈 **Log:**\n{log}", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("🔙 Back", callback_data="m_adm")]]))

    if data == "back_main": return await safe_edit(cb.message, "Main Menu", reply_markup=get_main_btns(uid))
    if data == "m_thumb": 
        t_btns = [[types.InlineKeyboardButton("👁 View", callback_data="v_t"), types.InlineKeyboardButton("🗑 Del", callback_data="d_t")], [types.InlineKeyboardButton("🔙 Back", callback_data="back_main")]] if user["thumb"] else [[types.InlineKeyboardButton("🔙 Back", callback_data="back_main")]]
        return await safe_edit(cb.message, "🖼 Thumbnail Manager", reply_markup=types.InlineKeyboardMarkup(t_btns))

    if data == "cancel":
        DB["active"].pop(uid_str, None); save_db()
        return await safe_edit(cb.message, "❌ Cancelled.")

    # Session check for ready menu buttons
    if uid_str not in DB["active"]: return await cb.answer("Session expired.", show_alert=True)
    state = DB["active"][uid_str]

    if data == "u_ren":
        state["status"] = "renaming"; save_db()
        return await safe_edit(cb.message, f"📝 **Current:** `{state['name']}`\n\nSend a new name or click below:", reply_markup=get_rename_menu())

    if data == "u_default":
        state["status"] = "ready"; return await safe_edit(cb.message, "✅ Using default name.", reply_markup=get_ready_btns())

    if data.startswith("dl_"):
        await safe_edit(cb.message, "⏳ Downloading YouTube..."); is_vid = data == "dl_vid"
        ydl_opts = {'format': 'bestvideo+bestaudio/best' if is_vid else 'bestaudio/best', 'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s'}
        if os.path.exists(COOKIES_FILE): ydl_opts['cookiefile'] = COOKIES_FILE
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(state["url"], download=True); path = ydl.prepare_filename(info)
            if not is_vid: path = os.path.splitext(path)[0] + ".mp3"
            state.update({"path": path, "name": os.path.basename(path), "status": "ready"}); save_db()
            await safe_edit(cb.message, "✅ Download Complete.", reply_markup=get_ready_btns())

    if data.startswith("u_"):
        await safe_edit(cb.message, "📤 Uploading..."); path, thumb = state["path"], user["thumb"]
        try:
            if data == "u_scr":
                screens = await take_screenshots(path, uid)
                if screens: await client.send_media_group(uid, screens)
            
            if data == "u_vid": await client.send_video(uid, video=path, thumb=thumb, caption=f"`{state['name']}`", progress=progress_hook, progress_args=(cb.message, time.time(), "Uploading"))
            else: await client.send_document(uid, document=path, thumb=thumb, caption=f"`{state['name']}`", progress=progress_hook, progress_args=(cb.message, time.time(), "Uploading"))
            
            if uid != OWNER_ID: user["used"] += state.get("size", 0)
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
    print("Bot is fully operational!"); await idle()

if __name__ == "__main__":
    app.run(main())
