import os, re, shutil, time, asyncio, random, subprocess, json
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
DB_FILE = "tasks.json"

USER_GREETINGS = ["Thanks for chatting with me.", "Glad you’re here.", "Appreciate you using this bot."]
ADMIN_GREETINGS = ["Chief, systems are ready.", "Ready when you are, chief.", "Standing by."]

DB = {"users": {}, "active": {}, "banned": [], "history": []}
CANCEL_GROUPS = set()

app = Client("dl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, sleep_threshold=120)
scheduler = AsyncIOScheduler()

# --- DB PERSISTENCE ---
def save_db():
    with open(DB_FILE, "w") as f:
        json.dump(DB, f, default=str)

def load_db():
    global DB
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                DB = json.load(f)
                if "banned" not in DB: DB["banned"] = []
                if "history" not in DB: DB["history"] = []
        except: pass

def get_user(uid):
    uid = str(uid)
    if uid not in DB["users"]:
        DB["users"][uid] = {"used": 0, "warnings": 0, "is_paid": False, "thumb": None, "last_task": None}
    return DB["users"][uid]

def format_size(size):
    if not size: return "0B"
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

# --- UTILS ---
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
    try: await app.send_message(uid, "✅ **Cooldown finished.** You can send a new task now.")
    except: pass

def check_cooldown(uid):
    user = get_user(uid)
    if not user.get("last_task"): return True, 0
    wait_time = 120 if uid == OWNER_ID else 600
    last_t = datetime.fromisoformat(user["last_task"]) if isinstance(user["last_task"], str) else user["last_task"]
    elapsed = (datetime.now() - last_t).total_seconds()
    if elapsed < wait_time: return False, int(wait_time - elapsed)
    return True, 0

# --- KEYBOARDS ---
def get_main_btns(uid):
    user = get_user(uid)
    btns = [[types.InlineKeyboardButton("❓ Help", callback_data="help"), 
             types.InlineKeyboardButton("🆔 My ID", callback_data="my_id")]]
    
    # Thumbnail Row
    t_row = []
    if user["thumb"]:
        t_row += [types.InlineKeyboardButton("👁 View", callback_data="view_t"),
                  types.InlineKeyboardButton("🗑 Del", callback_data="del_t")]
    else:
        t_row.append(types.InlineKeyboardButton("🖼 Set Thumbnail", callback_data="help_thumb"))
    btns.append(t_row)
    
    if uid == OWNER_ID:
        btns.append([types.InlineKeyboardButton("⚙️ Admin Dashboard", callback_data="adm_main")])
    else:
        btns.append([types.InlineKeyboardButton("📊 My Status", callback_data="my_status"),
                     types.InlineKeyboardButton("💎 Upgrade", url=CONTACT_URL)])
    return types.InlineKeyboardMarkup(btns)

def get_ready_btns():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("Video 🎥", callback_data="up_video"),
         types.InlineKeyboardButton("File 📄", callback_data="up_file")],
        [types.InlineKeyboardButton("Rename ✏️", callback_data="rename"),
         types.InlineKeyboardButton("Cancel ❌", callback_data="cancel")]
    ])

# --- COMMANDS ---
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(_, m):
    uid = m.from_user.id
    if uid in DB["banned"]: return
    msg = random.choice(ADMIN_GREETINGS if uid == OWNER_ID else USER_GREETINGS)
    await m.reply(msg, reply_markup=get_main_btns(uid))

@app.on_message(filters.photo & filters.private)
async def save_thumb(_, m):
    uid = m.from_user.id
    path = os.path.join(THUMB_DIR, f"{uid}.jpg")
    await m.download(path)
    get_user(uid)["thumb"] = path
    save_db()
    await m.reply("🖼 **Thumbnail Saved.**", reply_markup=get_main_btns(uid))

@app.on_message((filters.video | filters.document | filters.forwarded) & filters.private)
async def handle_media_rename(client, m):
    uid = m.from_user.id
    if not await is_subscribed(uid): return await m.reply("Join channel first.", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Join", url=INVITE_LINK)]]))
    
    status_msg = await m.reply("📥 Downloading for processing...")
    path = os.path.join(DOWNLOAD_DIR, f"file_{uid}")
    await m.download(path, progress=progress_hook, progress_args=(status_msg, time.time(), "Downloading"))
    
    DB["active"][str(uid)] = {"path": path, "name": "file", "status": "ready", "size": (m.video.file_size if m.video else m.document.file_size)}
    await status_msg.edit("✅ Media Ready. Choose Action:", reply_markup=get_ready_btns())

@app.on_message(filters.text & ~filters.command(["start", "admin"]) & filters.private)
async def handle_text(client, m):
    uid = m.from_user.id
    uid_str = str(uid)
    if uid in DB["banned"]: return
    
    # 1. Rename Logic
    if uid_str in DB["active"] and DB["active"][uid_str].get("status") == "renaming":
        state = DB["active"][uid_str]
        ext = os.path.splitext(state["path"])[1]
        new_name = m.text if m.text.endswith(ext) else f"{m.text}{ext}"
        new_path = os.path.join(DOWNLOAD_DIR, new_name)
        os.rename(state["path"], new_path)
        DB["active"][uid_str].update({"path": new_path, "name": new_name, "status": "ready"})
        return await m.reply(f"✅ Renamed.", reply_markup=get_ready_btns())

    # 2. Link Logic
    if m.text.startswith("http"):
        if not await is_subscribed(uid): return await m.reply("⚠️ Join channel first.", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Join", url=INVITE_LINK)]]))
        
        can_run, wait = check_cooldown(uid)
        if not can_run: return await m.reply(f"⏳ Cooldown: Please wait {wait}s.")

        status_msg = await m.reply("🔍 Analyzing...")
        try:
            with YoutubeDL({'quiet': True}) as ydl:
                info = ydl.extract_info(m.text, download=False)
                size = info.get('filesize_approx') or 0
                path = ydl.prepare_filename(info)
                
                # Update history for Reports
                DB["history"].append({"uid": uid, "name": info.get('title', 'Link')[:30], "size": size, "time": str(datetime.now())})
                if len(DB["history"]) > 100: DB["history"].pop(0) # Keep it light
                
                await status_msg.edit("⏳ Downloading to server...")
                ydl.download([m.text])
                DB["active"][uid_str] = {"path": path, "name": os.path.basename(path), "status": "ready", "size": size}
                save_db()
                await status_msg.edit("✅ Downloaded to server.", reply_markup=get_ready_btns())
        except Exception as e: await status_msg.edit(f"❌ Error: {str(e)[:50]}")

@app.on_callback_query()
async def cb_handler(client, cb: types.CallbackQuery):
    uid = cb.from_user.id
    uid_str = str(uid)
    data = cb.data
    user = get_user(uid)

    await cb.answer() # Stop spinning icon

    # Admin Dashboard
    if data == "adm_main" and uid == OWNER_ID:
        btns = [[types.InlineKeyboardButton("📊 Reports", callback_data="adm_reports"),
                 types.InlineKeyboardButton("📢 Broadcast", callback_data="adm_bc")]]
        return await cb.message.edit("🛠 **Admin Dashboard**", reply_markup=types.InlineKeyboardMarkup(btns))

    if data == "adm_reports" and uid == OWNER_ID:
        h_log = ""
        for e in DB["history"][-10:]:
            h_log += f"• `{e['uid']}` | {e['name']} | {format_size(e['size'])}\n"
        rep = f"📈 **System Report**\n\nUsers: {len(DB['users'])}\nBanned: {len(DB['banned'])}\n\n**Last 10 Activities:**\n{h_log if h_log else 'None'}"
        return await cb.message.edit(rep, reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("🔙 Back", callback_data="adm_main")]]))

    # Navigation
    if data == "help": return await cb.message.edit("📖 **Help**\n- Send links to download\n- Forward files to rename\n- Send photo for thumbnail", reply_markup=get_main_btns(uid))
    if data == "my_id": return await cb.message.reply(f"Your ID: `{uid}`")
    if data == "view_t": 
        if user["thumb"]: await cb.message.reply_photo(user["thumb"])
        return
    if data == "del_t":
        if user["thumb"]: os.remove(user["thumb"]); user["thumb"] = None; save_db()
        return await cb.message.edit("🗑 Thumbnail Deleted.", reply_markup=get_main_btns(uid))

    # Session Buttons
    if data == "cancel":
        CANCEL_GROUPS.add(uid); DB["active"].pop(uid_str, None); save_db()
        return await cb.message.edit("❌ Session Cancelled.")

    if uid_str not in DB["active"]: return
    state = DB["active"][uid_str]

    if data.startswith("up_"):
        await cb.message.edit("📤 Uploading...")
        path, is_vid = state["path"], (data == "up_video")
        user["last_task"] = str(datetime.now())
        scheduler.add_job(notify_ready, "date", run_date=datetime.now() + timedelta(seconds=(120 if uid==OWNER_ID else 600)), args=[uid])
        
        try:
            if is_vid: await client.send_video(uid, video=path, thumb=user["thumb"], caption=f"`{state['name']}`", progress=progress_hook, progress_args=(cb.message, time.time(), "Uploading"))
            else: await client.send_document(uid, document=path, thumb=user["thumb"], caption=f"`{state['name']}`", progress=progress_hook, progress_args=(cb.message, time.time(), "Uploading"))
            
            if uid != OWNER_ID: user["used"] += (state.get("size") or 0)
            save_db()
            await cb.message.delete()
        finally:
            if os.path.exists(path): os.remove(path)
            DB["active"].pop(uid_str, None); save_db()

    elif data == "rename":
        state["status"] = "renaming"
        await cb.message.edit("📝 Send new name with extension:")

# --- STARTUP ---
async def main():
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    if not os.path.exists(THUMB_DIR): os.makedirs(THUMB_DIR)
    load_db()
    await app.start()
    server = web.Application(); server.add_routes([web.get('/', lambda r: web.Response(text="Bot Alive"))])
    runner = web.AppRunner(server); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8000).start()
    print("Bot is Master-Ready!")
    await idle()

if __name__ == "__main__":
    app.run(main())
