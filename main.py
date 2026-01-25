import os, re, shutil, time, asyncio, random, subprocess, json
from datetime import datetime
from pyrogram import Client, filters, types, enums, idle, errors
from yt_dlp import YoutubeDL
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

# --- DB ENGINE ---
DB = {"users": {}, "active": {}, "admins": [519459195]}

def save_db():
    with open(DB_FILE, "w") as f: json.dump(DB, f, default=str)

def load_db():
    global DB
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                DB = json.load(f)
                if "active" not in DB: DB["active"] = {}
                if "admins" not in DB: DB["admins"] = [OWNER_ID]
        except: pass

def get_user(uid):
    uid_s = str(uid)
    if uid_s not in DB["users"]:
        DB["users"][uid_s] = {"thumb": None, "state": "none"}
    return DB["users"][uid_s]

app = Client("dl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, sleep_threshold=120)

# --- UTILS ---
async def safe_edit(msg, text, reply_markup=None):
    try: return await msg.edit(text, reply_markup=reply_markup)
    except: return msg

async def is_subscribed(uid):
    if uid == OWNER_ID: return True
    try:
        m = await app.get_chat_member(CHANNEL_ID, uid)
        return m.status in [enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except: return False

async def take_screenshots(video_path, uid):
    out_dir = os.path.join(DOWNLOAD_DIR, f"sc_{uid}")
    if not os.path.exists(out_dir): os.makedirs(out_dir)
    try:
        cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{video_path}"'
        duration = float(subprocess.check_output(cmd, shell=True))
        screens = []
        for i in range(1, 11):
            t = (duration / 11) * i
            p = os.path.join(out_dir, f"{i}.jpg")
            subprocess.call(['ffmpeg', '-ss', str(t), '-i', video_path, '-vframes', '1', p, '-y'], stderr=subprocess.DEVNULL)
            if os.path.exists(p): screens.append(types.InputMediaPhoto(p))
        return screens
    except: return []

# --- KEYBOARDS ---
def get_main_btns(uid):
    btns = [[types.InlineKeyboardButton("❓ Help", callback_data="cb_help"), types.InlineKeyboardButton("🆔 My ID", callback_data="cb_id")],
            [types.InlineKeyboardButton("🖼 Thumbnail Manager", callback_data="cb_thumb_mgr")]]
    if uid == OWNER_ID: btns.append([types.InlineKeyboardButton("⚙️ Admin Panel", callback_data="cb_admin_main")])
    btns.append([types.InlineKeyboardButton("🚪 Exit", callback_data="cb_exit")])
    return types.InlineKeyboardMarkup(btns)

def get_ready_menu():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("Rename ✏️", callback_data="cb_ren_start"), types.InlineKeyboardButton("Upload ⬆️", callback_data="cb_up_start")],
        [types.InlineKeyboardButton("Cancel ❌", callback_data="cb_cancel")]
    ])

def get_rename_options():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("✅ Use Default", callback_data="cb_ren_def"), types.InlineKeyboardButton("✏️ Enter New Name", callback_data="cb_ren_cus")],
        [types.InlineKeyboardButton("🔙 Back", callback_data="cb_ready_back")]
    ])

def get_upload_options():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("Video 🎥", callback_data="cb_up_vid"), types.InlineKeyboardButton("File 📄", callback_data="cb_up_fil")],
        [types.InlineKeyboardButton("Upload + 📸", callback_data="cb_up_scr")],
        [types.InlineKeyboardButton("🔙 Back", callback_data="cb_ready_back")]
    ])

# --- HANDLERS ---
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(_, m):
    get_user(m.from_user.id)["state"] = "none"
    await m.reply("Chief, systems are ready." if m.from_user.id == OWNER_ID else "Appreciate you using this bot.", reply_markup=get_main_btns(m.from_user.id))

@app.on_message((filters.video | filters.document | filters.forwarded) & filters.private)
async def media_handler(client, m):
    uid, uid_s = m.from_user.id, str(m.from_user.id)
    if not await is_subscribed(uid): return await m.reply("⚠️ Join channel first.", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Join", url=INVITE_LINK)], [types.InlineKeyboardButton("🔄 Verify", callback_data="cb_verify")]]))
    
    status = await m.reply("📥 Downloading...")
    path = os.path.join(DOWNLOAD_DIR, f"f_{uid}")
    media = m.video or m.document
    await m.download(path)
    DB["active"][uid_s] = {"path": path, "name": getattr(media, "file_name", "file.mp4"), "status": "ready"}
    save_db(); await status.edit("✅ Downloaded to server.", reply_markup=get_ready_menu())

@app.on_message(filters.text & ~filters.command(["start"]) & filters.private)
async def text_handler(client, m):
    uid, uid_s = m.from_user.id, str(m.from_user.id)
    user = get_user(uid)

    if user["state"] == "renaming" and uid_s in DB["active"]:
        state = DB["active"][uid_s]
        ext = os.path.splitext(state["path"])[1] or ".mp4"
        new_name = m.text if m.text.endswith(ext) else f"{m.text}{ext}"
        new_path = os.path.join(DOWNLOAD_DIR, new_name)
        os.rename(state["path"], new_path)
        state.update({"path": new_path, "name": new_name})
        user["state"] = "none"; save_db()
        return await m.reply(f"✅ Name set to: `{new_name}`", reply_markup=get_ready_menu())

    if m.text.startswith("http"):
        if not await is_subscribed(uid): return await m.reply("⚠️ Join channel.", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Join", url=INVITE_LINK)], [types.InlineKeyboardButton("🔄 Verify", callback_data="cb_verify")]]))
        
        status = await m.reply("🔍 Analyzing...")
        if "youtube.com" in m.text or "youtu.be" in m.text:
            DB["active"][uid_s] = {"url": m.text, "status": "choosing"}
            save_db()
            btns = [[types.InlineKeyboardButton("1080p", callback_data="yt_1080"), types.InlineKeyboardButton("720p", callback_data="yt_720")],
                    [types.InlineKeyboardButton("480p", callback_data="yt_480"), types.InlineKeyboardButton("MP3", callback_data="yt_mp3")],
                    [types.InlineKeyboardButton("❌ Cancel", callback_data="cb_cancel")]]
            return await status.edit("🎬 YouTube Format:", reply_markup=types.InlineKeyboardMarkup(btns))
        
        try:
            with YoutubeDL({'quiet': True}) as ydl:
                info = ydl.extract_info(m.text, download=True)
                path = ydl.prepare_filename(info)
                DB["active"][uid_s] = {"path": path, "name": os.path.basename(path), "status": "ready"}
                save_db(); await status.edit("✅ Ready.", reply_markup=get_ready_menu())
        except Exception as e: await status.edit(f"❌ Error: {str(e)[:50]}")

@app.on_callback_query()
async def cb_handler(client, cb: types.CallbackQuery):
    uid, uid_s = cb.from_user.id, str(cb.from_user.id)
    data, user = cb.data, get_user(uid)
    await cb.answer()

    if data == "cb_exit": return await cb.message.delete()
    if data == "cb_id": return await cb.answer(f"ID: {uid}", show_alert=True)
    if data == "cb_help": return await safe_edit(cb.message, "📖 Send a link or forward a file.", reply_markup=get_main_btns(uid))
    if data == "cb_verify":
        if await is_subscribed(uid): return await safe_edit(cb.message, "✅ Verified!", reply_markup=get_main_btns(uid))
        else: return await cb.answer("❌ Join first!", show_alert=True)

    if data == "cb_thumb_mgr":
        btns = [[types.InlineKeyboardButton("👁 View Thumbnail", callback_data="t_view"), types.InlineKeyboardButton("🗑 Delete Thumbnail", callback_data="t_del")]]
        return await safe_edit(cb.message, "🖼 **Thumbnail Manager**", reply_markup=types.InlineKeyboardMarkup(btns))

    if data == "t_view":
        if user["thumb"]: await cb.message.reply_photo(user["thumb"])
        else: await cb.answer("No thumbnail!", show_alert=True)
        return
    if data == "t_del":
        user["thumb"] = None; save_db(); return await cb.answer("Deleted!")

    if data == "cb_cancel":
        DB["active"].pop(uid_s, None); return await safe_edit(cb.message, "❌ Cancelled.")

    if uid_s not in DB["active"]: return await cb.answer("Expired session.")
    state = DB["active"][uid_s]

    if data.startswith("yt_"):
        fmt = data.split("_")[1]
        await safe_edit(cb.message, f"⏳ Downloading YouTube {fmt}...")
        y_opts = {'format': f'bestvideo[height<={fmt}]+bestaudio/best' if fmt.isdigit() else 'bestaudio/best', 'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s'}
        with YoutubeDL(y_opts) as ydl:
            info = ydl.extract_info(state["url"], download=True)
            path = ydl.prepare_filename(info)
            state.update({"path": path, "name": os.path.basename(path), "status": "ready"}); save_db()
            await safe_edit(cb.message, "✅ Ready.", reply_markup=get_ready_menu())

    if data == "cb_ren_start": return await safe_edit(cb.message, "✏️ **Rename**", reply_markup=get_rename_options())
    if data == "cb_ren_def": return await safe_edit(cb.message, "✅ Using default name.", reply_markup=get_ready_menu())
    if data == "cb_ren_cus": user["state"] = "renaming"; return await safe_edit(cb.message, "📝 Send new name:")
    if data == "cb_ready_back": return await safe_edit(cb.message, "✅ Ready.", reply_markup=get_ready_menu())
    
    if data == "cb_up_start": return await safe_edit(cb.message, "📤 **Upload**", reply_markup=get_upload_options())

    if data.startswith("cb_up_"):
        await safe_edit(cb.message, "📤 Uploading..."); path = state["path"]
        try:
            if data == "cb_up_scr":
                screens = await take_screenshots(path, uid)
                if screens: await client.send_media_group(uid, screens)
            
            if data == "cb_up_vid": await client.send_video(uid, video=path, thumb=user["thumb"], caption=f"`{state['name']}`")
            else: await client.send_document(uid, document=path, thumb=user["thumb"], caption=f"`{state['name']}`")
            await cb.message.delete()
        finally:
            if os.path.exists(path): os.remove(path)
            DB["active"].pop(uid_s, None); save_db()

# --- STARTUP ---
async def main():
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    if not os.path.exists(THUMB_DIR): os.makedirs(THUMB_DIR)
    load_db(); await app.start()
    server = web.Application(); server.add_routes([web.get('/', lambda r: web.Response(text="OK"))])
    runner = web.AppRunner(server); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8000).start()
    await idle()

if __name__ == "__main__":
    app.run(main())
