import os, re, shutil, time, asyncio, random
from datetime import datetime
from pyrogram import Client, filters, types, enums
from yt_dlp import YoutubeDL
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- HARDCODED CONFIG ---
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", 0)) 
OWNER_ID = 519459195
CONTACT_URL = "https://t.me/poocha"
INVITE_LINK = "https://t.me/+eooytvOAwjc0NTI1"
DOWNLOAD_DIR = "downloads"
DAILY_LIMIT = 15 * 1024 * 1024 * 1024 # 15GB

# --- IN-MEMORY DATABASE ---
DB = {"users": {}, "active": {}}

app = Client("dl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

def get_user(uid):
    today = datetime.now().date()
    if uid not in DB["users"]:
        DB["users"][uid] = {"used": 0, "pro": False, "banned": False, "last_reset": today}
    if DB["users"][uid]["last_reset"] != today:
        DB["users"][uid].update({"used": 0, "last_reset": today})
    return DB["users"][uid]

def format_size(size):
    if not size: return "0B"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024: return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"

async def is_subscribed(uid):
    if uid == OWNER_ID: return True
    try:
        member = await app.get_chat_member(CHANNEL_ID, uid)
        return member.status in [enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except: return False

async def auto_clean():
    """Clears files older than 1 hour to save Koyeb disk space"""
    now = time.time()
    for uid, data in list(DB["active"].items()):
        if now - data.get("time", 0) > 3600:
            if "path" in data and os.path.exists(data["path"]):
                try: os.remove(data["path"])
                except: pass
            DB["active"].pop(uid, None)

# --- KEYBOARDS ---
def get_admin_btns():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("📊 User Stats", callback_data="adm_stats"),
         types.InlineKeyboardButton("💾 Disk Usage", callback_data="adm_disk")],
        [types.InlineKeyboardButton("🔗 Active Links", callback_data="adm_links")]
    ])

def get_action_btns():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("Upload ⬆️", callback_data="upload")],
        [types.InlineKeyboardButton("Rename ✏️", callback_data="rename"),
         types.InlineKeyboardButton("Cancel ❌", callback_data="cancel")]
    ])

# --- OWNER COMMANDS ---
@app.on_message(filters.command("admin") & filters.user(OWNER_ID))
async def admin_panel(_, m):
    await m.reply("🛠 **Admin Dashboard**", reply_markup=get_admin_btns())

@app.on_message(filters.command("pro") & filters.user(OWNER_ID))
async def make_pro(_, m):
    try:
        target = int(m.command[1]); get_user(target)["pro"] = True
        await m.reply(f"✅ User {target} is now PRO.")
    except: await m.reply("Usage: /pro [user_id]")

@app.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def broadcast(_, m):
    if len(m.command) < 2: return
    text = m.text.split(None, 1)[1]
    for uid in DB["users"]:
        try: await app.send_message(uid, f"📢 **Announcement:**\n\n{text}")
        except: pass

# --- MAIN HANDLERS ---
@app.on_message(filters.text)
async def handle_message(client, m):
    uid = m.from_user.id
    user = get_user(uid)
    if user["banned"]: return

    # 1. Force Subscription Check
    if not await is_subscribed(uid):
        btn = types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Join Channel", url=INVITE_LINK)]])
        return await m.reply("⚠️ **Access Denied!**\nYou must join our channel to use this bot.", reply_markup=btn)

    # 2. Rename Flow
    if uid in DB["active"] and DB["active"][uid].get("status") == "renaming":
        state = DB["active"][uid]
        ext = os.path.splitext(state["path"])[1]
        new_name = m.text if m.text.endswith(ext) else f"{m.text}{ext}"
        new_path = os.path.join(DOWNLOAD_DIR, new_name)
        try:
            os.rename(state["path"], new_path)
            DB["active"][uid].update({"path": new_path, "name": new_name, "status": "ready"})
            return await m.reply(f"✅ Renamed: `{new_name}`", reply_markup=get_action_btns())
        except Exception as e: return await m.reply(f"❌ Rename error: {e}")

    # 3. Media Link Handling
    if m.text.startswith("http"):
        # Usage Limit Check
        if uid != OWNER_ID and not user["pro"] and user["used"] >= DAILY_LIMIT:
            return await m.reply("❌ **15GB Daily Limit Reached!**", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("🚀 Upgrade to Pro", url=CONTACT_URL)]]))

        # Random promo for free users
        if uid != OWNER_ID and not user["pro"]:
            await m.reply(random.choice(["⚡ Pro users get unlimited usage!", "💎 No NSFW restrictions for Pro users."]), reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Contact Admin", url=CONTACT_URL)]]))

        status_msg = await m.reply("🔍 Detecting Media...")
        try:
            with YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
                info = ydl.extract_info(m.text, download=False)
                
                # NSFW Check
                trigger_words = ["porn", "xvideo", "hentai", "sex", "nude", "xxx", "erotic"]
                is_nsfw = any(w in info.get("title","").lower() for w in trigger_words) or info.get("age_limit",0) >= 18
                if uid != OWNER_ID and not user["pro"] and is_nsfw:
                    return await status_msg.edit("🔞 **NSFW Restricted!**\nAdult content is only available for Pro users.", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Upgrade", url=CONTACT_URL)]]))

                size = info.get('filesize') or info.get('filesize_approx') or 0
                
                # Owner Monitoring
                if uid != OWNER_ID:
                    await app.send_message(OWNER_ID, f"👁️ **User Uploading:**\nID: `{uid}`\nFile: `{info.get('title')}`\nSize: {format_size(size)}")

                # YouTube Logic
                if "youtube.com" in m.text or "youtu.be" in m.text:
                    DB["active"][uid] = {"url": m.text, "time": time.time(), "status": "choosing"}
                    btns = [[types.InlineKeyboardButton(f"Video ({format_size(size)})", callback_data="dl_vid")],
                            [types.InlineKeyboardButton("Audio (MP3 Extract)", callback_data="dl_aud")],
                            [types.InlineKeyboardButton("Cancel", callback_data="cancel")]]
                    return await status_msg.edit("🎬 YouTube Detected. Choose format:", reply_markup=types.InlineKeyboardMarkup(btns))
                
                # Direct / Generic Logic
                await status_msg.edit("⏳ Downloading...")
                path = ydl.prepare_filename(info)
                ydl.download([m.text])
                DB["active"][uid] = {"path": path, "name": os.path.basename(path), "status": "ready", "time": time.time()}
                user["used"] += size
                await status_msg.edit(f"✅ Ready! Daily Usage: {format_size(user['used'])}", reply_markup=get_action_btns())

        except Exception as e: await status_msg.edit(f"❌ Failed: {str(e)[:100]}")

@app.on_callback_query()
async def cb_handler(client, cb: types.CallbackQuery):
    uid = cb.from_user.id
    data = cb.data

    # Admin Callback Logic
    if data.startswith("adm_") and uid == OWNER_ID:
        if data == "adm_stats":
            await cb.answer(f"Users: {len(DB['users'])} | Active: {len(DB['active'])}", show_alert=True)
        elif data == "adm_disk":
            total, used, free = shutil.disk_usage("/")
            await cb.answer(f"Server Disk: {format_size(used)} / {format_size(total)}", show_alert=True)
        elif data == "adm_links":
            links = "\n".join([f"`{k}`: {v.get('name', 'Analysing')}" for k,v in DB["active"].items()])
            await cb.message.reply(f"🔗 **Active Links:**\n{links or 'None'}")
        return

    if uid not in DB["active"]: return await cb.answer("Session expired. Please send link again.")
    state = DB["active"][uid]

    if data.startswith("dl_"):
        await cb.message.edit("⏳ Processing media... please wait.")
        is_vid = data == "dl_vid"
        opts = {
            'format': 'bestvideo+bestaudio/best' if is_vid else 'bestaudio/best',
            'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s',
            'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3'}] if not is_vid else [],
            'quiet': True
        }
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(state["url"], download=True)
                path = ydl.prepare_filename(info)
                if not is_vid: path = os.path.splitext(path)[0] + ".mp3"
                DB["active"][uid].update({"path": path, "name": os.path.basename(path), "status": "ready"})
                await cb.message.edit(f"✅ Ready: `{os.path.basename(path)}`", reply_markup=get_action_btns())
        except Exception as e: await cb.message.edit(f"❌ Error: {e}")

    elif data == "upload":
        await cb.message.edit("📤 Uploading to Telegram...")
        try:
            p = state["path"]
            if p.lower().endswith(('.mp4', '.mkv', '.mov', '.webm')):
                await client.send_video(uid, video=p, caption=f"`{state['name']}`", supports_streaming=True)
            else:
                await client.send_document(uid, document=p, caption=f"`{state['name']}`")
            await cb.message.delete()
        except Exception as e: await cb.message.reply(f"❌ Upload failed: {e}")
        finally:
            if os.path.exists(state["path"]): os.remove(state["path"])
            DB["active"].pop(uid, None)

    elif data == "rename":
        DB["active"][uid]["status"] = "renaming"
        await cb.message.edit("📝 Send me the new filename with extension (e.g. video.mp4):")

    elif data == "cancel":
        if "path" in state and os.path.exists(state["path"]): os.remove(state["path"])
        DB["active"].pop(uid, None)
        await cb.message.edit("❌ Session Cancelled and files deleted.")

if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    scheduler = AsyncIOScheduler()
    scheduler.add_job(auto_clean, "interval", minutes=30) # Cleanup check every 30 mins
    scheduler.start()
    app.run()
