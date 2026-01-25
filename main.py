import os, re, shutil, time, asyncio, random, subprocess
from datetime import datetime, timedelta
from pyrogram import Client, filters, types, enums, idle, errors
from yt_dlp import YoutubeDL
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp import web

# --- CONFIG ---
OWNER_ID = 519459195 
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", 0)) 
INVITE_LINK = "https://t.me/+eooytvOAwjc0NTI1"
CONTACT_URL = "https://t.me/poocha"
DOWNLOAD_DIR = "downloads"
THUMB_DIR = "thumbnails"
DAILY_LIMIT = 15 * 1024 * 1024 * 1024 

USER_GREETINGS = ["Thanks for chatting with me.", "Glad you’re here.", "Appreciate you using this bot."]
ADMIN_GREETINGS = ["Chief, systems are ready.", "Ready when you are, chief.", "Standing by."]

DB = {"users": {}, "active": {}, "banned": set()}
CANCEL_GROUPS = set()

app = Client("dl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, sleep_threshold=120)
scheduler = AsyncIOScheduler()

# --- UTILS ---
async def health_check(request): return web.Response(text="Bot Alive")

def get_user(uid):
    today = datetime.now().date()
    if uid not in DB["users"]:
        DB["users"][uid] = {"used": 0, "last_reset": today, "last_task": None, "thumb": None}
    if DB["users"][uid]["last_reset"] != today:
        DB["users"][uid].update({"used": 0, "last_reset": today})
    return DB["users"][uid]

def format_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024: return f"{size:.2f}{unit}"
        size /= 1024
    return f"{size:.2f}TB"

async def is_subscribed(uid):
    if uid == OWNER_ID: return True
    try:
        m = await app.get_chat_member(CHANNEL_ID, uid)
        return m.status in [enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except: return False

async def progress_hook(current, total, msg, start_time, action):
    if msg.chat.id in CANCEL_GROUPS: raise Exception("USER_CANCEL")
    now = time.time()
    if not hasattr(msg, "last_up"): msg.last_up = 0
    if (now - msg.last_up) < 10: return 
    msg.last_up = now
    try:
        p = (current * 100 / total) if total > 0 else 0
        bar = "✅" * int(p/10) + "⬜" * (10 - int(p/10))
        await msg.edit(f"⏳ {action}...\n`{bar}` {p:.1f}%\n📦 {format_size(current)} / {format_size(total)}")
    except: pass

async def notify_ready(uid):
    try: await app.send_message(uid, "✅ **Cooldown finished.** Send task now.")
    except: pass

def check_cooldown(uid):
    user = get_user(uid)
    if not user["last_task"]: return True, 0
    wait_time = 120 if uid == OWNER_ID else 600
    elapsed = (datetime.now() - user["last_task"]).total_seconds()
    if elapsed < wait_time: return False, int(wait_time - elapsed)
    return True, 0

# --- KEYBOARDS ---
def get_start_btns(uid):
    user = get_user(uid)
    btns = [[types.InlineKeyboardButton("❓ Help", callback_data="help"), 
             types.InlineKeyboardButton("🆔 My ID", callback_data="my_id")]]
    if user["thumb"]:
        btns.append([types.InlineKeyboardButton("🖼 View Thumb", callback_data="view_t"),
                     types.InlineKeyboardButton("🗑 Delete Thumb", callback_data="del_t")])
    else:
        btns.append([types.InlineKeyboardButton("🖼 Set Thumbnail (Send Photo)", callback_data="help_thumb")])
    btns.append([types.InlineKeyboardButton("💖 Donate / Contact", url=CONTACT_URL)])
    return types.InlineKeyboardMarkup(btns)

def get_ready_btns():
    btns = [[types.InlineKeyboardButton("Upload ⬆️", callback_data="up_normal"),
             types.InlineKeyboardButton("Upload + 📸", callback_data="up_screen")],
            [types.InlineKeyboardButton("Rename ✏️", callback_data="rename")],
            [types.InlineKeyboardButton("Cancel ❌", callback_data="cancel")]]
    return types.InlineKeyboardMarkup(btns)

def get_sub_btns():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("Join Channel", url=INVITE_LINK)],
        [types.InlineKeyboardButton("🔄 Verify Membership", callback_data="verify_sub")]
    ])

# --- HANDLERS ---
@app.on_message(filters.command("start"))
async def start_cmd(_, m):
    uid = m.from_user.id
    msg = random.choice(ADMIN_GREETINGS if uid == OWNER_ID else USER_GREETINGS)
    await m.reply(msg, reply_markup=get_start_btns(uid))

@app.on_message(filters.photo)
async def save_thumb(_, m):
    uid = m.from_user.id
    path = os.path.join(THUMB_DIR, f"{uid}.jpg")
    await m.download(path)
    get_user(uid)["thumb"] = path
    await m.reply("🖼 **Thumbnail saved.** Applied to future uploads.", reply_markup=get_start_btns(uid))

@app.on_message(filters.forwarded | filters.video | filters.document)
async def handle_media(client, m):
    uid = m.from_user.id
    if not await is_subscribed(uid): return await m.reply("⚠️ Join channel first.", reply_markup=get_sub_btns())
    can_run, wait = check_cooldown(uid)
    if not can_run: return await m.reply(f"⏳ Please wait {wait}s.")
    
    status_msg = await m.reply("📥 Downloading media...")
    path = os.path.join(DOWNLOAD_DIR, f"file_{uid}")
    await m.download(path, progress=progress_hook, progress_args=(status_msg, time.time(), "Downloading"))
    
    DB["active"][uid] = {"path": path, "name": "file", "status": "ready", "size": (m.video.file_size if m.video else m.document.file_size)}
    await status_msg.edit("✅ Media processed. Choose action:", reply_markup=get_ready_btns())

@app.on_message(filters.text)
async def handle_text(client, m):
    uid = m.from_user.id
    if uid in DB["banned"]: return
    user = get_user(uid)

    if uid in DB["active"] and DB["active"][uid].get("status") == "renaming":
        state = DB["active"][uid]
        ext = os.path.splitext(state["path"])[1]
        new_name = m.text if m.text.endswith(ext) else f"{m.text}{ext}"
        new_path = os.path.join(DOWNLOAD_DIR, new_name)
        os.rename(state["path"], new_path)
        DB["active"][uid].update({"path": new_path, "name": new_name, "status": "ready"})
        return await m.reply(f"✅ Renamed.", reply_markup=get_ready_btns())

    if m.text.startswith("http"):
        if not await is_subscribed(uid): return await m.reply("⚠️ Join channel first.", reply_markup=get_sub_btns())
        can_run, wait = check_cooldown(uid)
        if not can_run: return await m.reply(f"⏳ Wait {wait}s.")

        status_msg = await m.reply("🔍 Analyzing...")
        try:
            with YoutubeDL({'quiet': True}) as ydl:
                info = ydl.extract_info(m.text, download=False)
                size = info.get('filesize') or info.get('filesize_approx') or 0
                DB["active"][uid] = {"url": m.text, "time": time.time(), "name": info.get('title', 'file'), "size": size}
                btns = [[types.InlineKeyboardButton(f"Video ({format_size(size)})", callback_data="dl_vid")],
                        [types.InlineKeyboardButton("Audio (MP3)", callback_data="dl_aud")]]
                await status_msg.edit("Choose format:", reply_markup=types.InlineKeyboardMarkup(btns))
        except: await status_msg.edit("❌ Link Error.")

@app.on_callback_query()
async def cb_handler(client, cb: types.CallbackQuery):
    uid = cb.from_user.id
    data = cb.data
    user = get_user(uid)

    # Membership Verification
    if data == "verify_sub":
        if await is_subscribed(uid):
            await cb.answer("✅ Membership Verified!", show_alert=True)
            return await cb.message.edit("✅ Access Granted. You can now send your links or files.")
        else:
            return await cb.answer("❌ You haven't joined yet!", show_alert=True)

    # Static Buttons
    if data == "my_id": return await cb.answer(f"ID: {uid}", show_alert=True)
    if data == "help": return await cb.message.edit("📖 **Help**\n- Send links to download\n- Forward files to rename\n- Send photos to set thumb", reply_markup=get_start_btns(uid))
    if data == "view_t":
        if user["thumb"]: await cb.message.reply_photo(user["thumb"], caption="Thumbnail")
        return await cb.answer()
    if data == "del_t":
        if user["thumb"]: os.remove(user["thumb"])
        user["thumb"] = None
        return await cb.message.edit("🗑 Deleted.", reply_markup=get_start_btns(uid))

    # Session Buttons
    if data == "cancel":
        CANCEL_GROUPS.add(uid)
        DB["active"].pop(uid, None)
        return await cb.message.edit("❌ Cancelled.")

    if uid not in DB["active"]: return await cb.answer("Expired. Send media again.")
    state = DB["active"][uid]

    if data.startswith("dl_"):
        await cb.message.edit("⏳ Downloading...")
        is_vid = data == "dl_vid"
        ydl_opts = {'format': 'bestvideo+bestaudio/best' if is_vid else 'bestaudio/best', 'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s',
                    'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3'}] if not is_vid else [], 'quiet': True}
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(state["url"], download=True)
            path = ydl.prepare_filename(info)
            if not is_vid: path = os.path.splitext(path)[0] + ".mp3"
            DB["active"][uid].update({"path": path, "name": os.path.basename(path), "status": "ready"})
            await cb.message.edit(f"✅ Ready.", reply_markup=get_ready_btns())

    elif data.startswith("up_"):
        await cb.message.edit("📤 Uploading...")
        path, thumb = state["path"], user["thumb"]
        user["last_task"] = datetime.now()
        wait = 120 if uid == OWNER_ID else 600
        scheduler.add_job(notify_ready, "date", run_date=datetime.now() + timedelta(seconds=wait), args=[uid])

        try:
            if path.lower().endswith(('.mp4', '.mkv', '.mov')):
                await client.send_video(uid, video=path, thumb=thumb, caption=f"`{state['name']}`", progress=progress_hook, progress_args=(cb.message, time.time(), "Uploading"))
            else:
                await client.send_document(uid, document=path, thumb=thumb, caption=f"`{state['name']}`", progress=progress_hook, progress_args=(cb.message, time.time(), "Uploading"))
            await cb.message.delete()
        except Exception as e: await cb.message.reply(f"❌ Error: {e}")
        finally:
            if os.path.exists(path): os.remove(path)
            DB["active"].pop(uid, None)

    elif data == "rename":
        DB["active"][uid]["status"] = "renaming"
        await cb.message.edit("📝 Send new name with extension:")

# --- STARTUP ---
async def main():
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    if not os.path.exists(THUMB_DIR): os.makedirs(THUMB_DIR)
    await app.start()
    scheduler.start()
    server = web.Application(); server.add_routes([web.get('/', health_check)])
    runner = web.AppRunner(server); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8000).start()
    await idle()

if __name__ == "__main__":
    app.run(main())
