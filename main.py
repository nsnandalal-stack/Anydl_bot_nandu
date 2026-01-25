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
DB_FILE = "database.json"
QUOTA_LIMIT = 5 * 1024 * 1024 * 1024 # 5GB

USER_GREETINGS = ["Thanks for chatting with me.", "Glad you’re here.", "Appreciate you using this bot."]
ADMIN_GREETINGS = ["Chief, systems are ready.", "Ready when you are, chief.", "Standing by."]

DB = {"users": {}, "active": {}, "banned": [], "history": []}
CANCEL_GROUPS = set()

app = Client("dl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, sleep_threshold=120)
scheduler = AsyncIOScheduler()

# --- DATABASE PERSISTENCE ---
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
    today = str(datetime.now().date())
    if uid not in DB["users"]:
        DB["users"][uid] = {"used": 0, "last_reset": today, "last_task": None, "thumb": None}
    if DB["users"][uid].get("last_reset") != today:
        DB["users"][uid].update({"used": 0, "last_reset": today})
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

def check_cooldown(uid):
    user = get_user(uid)
    if not user.get("last_task"): return True, 0
    wait_time = 120 if uid == OWNER_ID else 600
    last_t = datetime.fromisoformat(user["last_task"]) if isinstance(user["last_task"], str) else user["last_task"]
    elapsed = (datetime.now() - last_t).total_seconds()
    if elapsed < wait_time: return False, int(wait_time - elapsed)
    return True, 0

async def notify_ready(uid):
    try: await app.send_message(uid, "✅ **Cooldown finished.** Send task now.")
    except: pass

# --- UI KEYBOARDS ---
def get_main_btns(uid):
    user = get_user(uid)
    btns = [[types.InlineKeyboardButton("❓ Help", callback_data="help"), 
             types.InlineKeyboardButton("🆔 My ID", callback_data="my_id")]]
    t_row = [types.InlineKeyboardButton("👁 View Thumb", callback_data="view_t"),
             types.InlineKeyboardButton("🗑 Del Thumb", callback_data="del_t")] if user["thumb"] else [types.InlineKeyboardButton("🖼 Set Thumbnail", callback_data="help_thumb")]
    btns.append(t_row)
    if uid == OWNER_ID:
        btns.append([types.InlineKeyboardButton("⚙️ Admin Dashboard", callback_data="adm_main")])
    else:
        btns.append([types.InlineKeyboardButton("📊 My Status", callback_data="my_status"),
                     types.InlineKeyboardButton("💎 Upgrade", callback_data="upgrade_pro")])
    return types.InlineKeyboardMarkup(btns)

def get_ready_btns():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("Video 🎥", callback_data="up_video"), types.InlineKeyboardButton("File 📄", callback_data="up_file")],
        [types.InlineKeyboardButton("Rename ✏️", callback_data="rename"), types.InlineKeyboardButton("Cancel ❌", callback_data="cancel")]
    ])

def get_sub_btns():
    return types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Join Channel", url=INVITE_LINK)],
                                       [types.InlineKeyboardButton("🔄 Verify Membership", callback_data="verify_sub")]])

# --- HANDLERS ---
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(_, m):
    uid = m.from_user.id
    if str(uid) in DB["banned"]: return
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

@app.on_message(filters.text & ~filters.command(["start"]) & filters.private)
async def handle_text(client, m):
    uid = m.from_user.id
    uid_str = str(uid)
    user = get_user(uid)
    
    # Rename Flow
    if uid_str in DB["active"] and DB["active"][uid_str].get("status") == "renaming":
        state = DB["active"][uid_str]
        ext = os.path.splitext(state["path"])[1]
        new_name = m.text if m.text.endswith(ext) else f"{m.text}{ext}"
        new_path = os.path.join(DOWNLOAD_DIR, new_name)
        os.rename(state["path"], new_path)
        DB["active"][uid_str].update({"path": new_path, "name": new_name, "status": "ready"})
        return await m.reply(f"✅ Renamed.", reply_markup=get_ready_btns())

    if m.text.startswith("http"):
        if not await is_subscribed(uid): return await m.reply("⚠️ Join channel first.", reply_markup=get_sub_btns())
        can_run, wait = check_cooldown(uid)
        if not can_run: return await m.reply(f"⏳ Cooldown: Please wait {wait}s.")
        if user["used"] >= QUOTA_LIMIT and uid != OWNER_ID: return await m.reply("❌ 5GB Quota Finished.", reply_markup=get_main_btns(uid))

        status_msg = await m.reply("🔍 Analyzing...")
        try:
            with YoutubeDL({'quiet': True, 'extractor_args': {'youtube': {'player_client': ['ios', 'android']}}}) as ydl:
                info = ydl.extract_info(m.text, download=False)
                size = info.get('filesize_approx') or 0
                path = ydl.prepare_filename(info)
                await status_msg.edit("⏳ Downloading to server...")
                ydl.download([m.text])
                DB["active"][uid_str] = {"path": path, "name": os.path.basename(path), "status": "ready", "size": size}
                DB["history"].append({"uid": uid, "name": info.get('title', 'file')[:30], "size": size})
                if len(DB["history"]) > 100: DB["history"].pop(0)
                save_db()
                await status_msg.edit("✅ Ready.", reply_markup=get_ready_btns())
        except Exception as e: await status_msg.edit(f"❌ Error: {str(e)[:50]}")

@app.on_callback_query()
async def cb_handler(client, cb: types.CallbackQuery):
    uid = cb.from_user.id
    uid_str = str(uid)
    data = cb.data
    user = get_user(uid)
    await cb.answer()

    if data == "verify_sub":
        if await is_subscribed(uid): return await cb.message.edit("✅ Access Granted.", reply_markup=get_main_btns(uid))
        else: return await cb.answer("❌ Not joined yet!", show_alert=True)

    if data == "back_main": return await cb.message.edit("Main Menu", reply_markup=get_main_btns(uid))
    if data == "my_id": return await cb.answer(f"ID: {uid}", show_alert=True)
    if data == "help": return await cb.message.edit("📖 **Help**\n- Send links to download\n- Forward files to rename\n- Set photo for thumbnail", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("🔙 Back", callback_data="back_main")]]))
    if data == "my_status": return await cb.message.edit(f"📊 **Status**\nUsage: {format_size(user['used'])} / 5GB", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("🔙 Back", callback_data="back_main")]]))
    if data == "upgrade_pro": return await cb.message.edit("💎 **Pro Plan**\n- 2m Cooldown\n- Watermark Tools\n- Auto-Split\n\nContact @poocha", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("🔙 Back", callback_data="back_main"), types.InlineKeyboardButton("Owner", url=CONTACT_URL)]]))

    if data == "adm_main" and uid == OWNER_ID:
        btns = [[types.InlineKeyboardButton("📊 Reports", callback_data="adm_reports"), types.InlineKeyboardButton("📢 Broadcast", callback_data="adm_bc")], [types.InlineKeyboardButton("🔙 Back", callback_data="back_main")]]
        return await cb.message.edit("🛠 Admin Dashboard", reply_markup=types.InlineKeyboardMarkup(btns))

    if data == "adm_reports" and uid == OWNER_ID:
        h_log = "".join([f"• `{e['uid']}` | {format_size(e['size'])}\n" for e in DB["history"][-10:]])
        return await cb.message.edit(f"📈 **Reports**\nUsers: {len(DB['users'])}\nBanned: {len(DB['banned'])}\n\n**Log:**\n{h_log}", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("🔙 Back", callback_data="adm_main")]]))

    if data == "adm_bc" and uid == OWNER_ID:
        return await cb.message.edit("📢 Use `/broadcast message` in chat.")

    if data == "view_t":
        if user["thumb"]: await cb.message.reply_photo(user["thumb"])
        return
    if data == "del_t":
        if user["thumb"]: os.remove(user["thumb"]); user["thumb"] = None; save_db()
        return await cb.message.edit("🗑 Deleted.", reply_markup=get_main_btns(uid))

    if data == "cancel":
        DB["active"].pop(uid_str, None); save_db()
        return await cb.message.edit("❌ Cancelled.")

    if uid_str not in DB["active"]: return await cb.answer("Expired session.")
    state = DB["active"][uid_str]

    if data.startswith("up_"):
        await cb.message.edit("📤 Uploading...")
        path, is_vid = state["path"], (data == "up_video")
        user["last_task"] = str(datetime.now())
        scheduler.add_job(notify_ready, "date", run_date=datetime.now() + timedelta(seconds=(120 if uid==OWNER_ID else 600)), args=[uid])
        try:
            if is_vid: await client.send_video(uid, video=path, thumb=user["thumb"], caption=f"`{state['name']}`")
            else: await client.send_document(uid, document=path, thumb=user["thumb"], caption=f"`{state['name']}`")
            if uid != OWNER_ID: user["used"] += state["size"]
            save_db(); await cb.message.delete()
        finally:
            if os.path.exists(path): os.remove(path)
            DB["active"].pop(uid_str, None); save_db()

    elif data == "rename":
        state["status"] = "renaming"
        await cb.message.edit("📝 Send new name with extension:")

@app.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def bc_handler(_, m):
    if len(m.command) < 2: return
    text = m.text.split(None, 1)[1]
    count = 0
    for u in DB["users"]:
        try: await app.send_message(int(u), f"📢 **Broadcast**\n\n{text}"); count += 1
        except: pass
    await m.reply(f"✅ Sent to {count} users.")

async def main():
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    if not os.path.exists(THUMB_DIR): os.makedirs(THUMB_DIR)
    load_db()
    await app.start()
    server = web.Application(); server.add_routes([web.get('/', lambda r: web.Response(text="Bot Alive"))])
    runner = web.AppRunner(server); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8000).start()
    scheduler.start(); await idle()

if __name__ == "__main__":
    app.run(main())
