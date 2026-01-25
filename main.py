import os
import re
import subprocess
import requests
import time
import asyncio
from datetime import datetime, timedelta
from tinydb import TinyDB, Query

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ==========================================
# CONFIGURATION
# ==========================================
OWNER_ID = 519459195                     # Your ID
CHANNEL_LINK = "https://t.me/+eooytvOAwjc0NTI1" 
CONTACT_LINK = "https://t.me/poocha"     
DOWNLOAD_DIR = "/tmp/downloads"          
DB_FILE = "/tmp/bot_state.json"          
SESSION_NAME = "koyeb_bot_session"

# ==========================================
# DATABASE SETUP
# ==========================================
db = TinyDB(DB_FILE)
users_table = db.table("users")
bot_state = {}

def get_user(uid):
    """Checks if user exists in DB. If not, creates new entry."""
    user = users_table.get(Query().uid == uid)
    if not user:
        users_table.insert({
            "uid": uid, 
            "is_pro": False, 
            "usage_gb": 0.0, 
            "links_count": 0
        })
        return users_table.get(Query().uid == uid)
    return user

# ==========================================
# UTILITIES
# ==========================================

def get_disk_usage(directory):
    """Calculates total space used in GB."""
    total_size = 0
    for dirpath, _, filenames in os.walk(directory):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if not os.path.islink(fp):
                total_size += os.path.getsize(fp)
    return total_size / (1024**3)

def generate_admin_buttons():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Stats & Usage", callback_data="admin_stats")],
        [InlineKeyboardButton("💾 Disk Space", callback_data="admin_disk")],
        [InlineKeyboardButton("🚹 Clear Downloads", callback_data="admin_clear")]
    ])

def generate_ready_buttons(filename: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Upload", callback_data=f"upload_{filename}")],
        [InlineKeyboardButton("✏️ Rename", callback_data="rename")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

async def cleanup_user(uid):
    """Removes active file from memory and disk."""
    if uid in bot_state:
        path = bot_state[uid].get('file')
        if path and os.path.exists(path):
            try: os.remove(path)
            except: pass
        del bot_state[uid]

# --- 18+ NSFW FILTER ---
async def check_nsfw(url):
    """Checks YouTube metadata for adult keywords."""
    if "youtube" not in url: return False
    try:
        cmd = ['yt-dlp', '--skip-download', '--print', "%(title)s %(description)s", url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        text = (result.stdout + result.stderr).lower()
        blocked = ["porn", "sex", "nsfw", "xxx", "adult", "anal", "free porn", "solo"]
        for word in blocked:
            if word in text: return True
    except Exception:
        pass
    return False

# ==========================================
# BOT SETUP
# ==========================================
app = Client(
    name=SESSION_NAME,
    api_id=int(os.getenv("API_ID")),
    api_hash=os.getenv("API_HASH"),
    bot_token=os.getenv("BOT_TOKEN"),
)

# ==========================================
# ADMIN COMMANDS
# ==========================================

@app.on_message(filters.command("admin") & filters.chat(OWNER_ID))
async def admin_command(client, msg):
    """Main Admin Dashboard Menu."""
    await msg.reply("🛡️ **Admin Panel**", reply_markup=generate_admin_buttons())

@app.on_callback_query(lambda q: q.data.startswith("admin_") & filters.chat(OWNER_ID))
async def admin_action(client, query):
    """Handles Admin Actions."""
    if query.data == "admin_stats":
        await query.answer()
        total_users = users_table.count()
        total_links = sum(u['links_count'] for u in users_table.all())
        
        msg = f"📊 **System Statistics**:\n\n"
        msg += f"👥 Active Users: {total_users}\n"
        msg += f"🔗 Total Links Processed: {total_links}\n"
        
        await query.message.edit(f"Generating full report...", reply_markup=None)
        await query.message.delete()
        await client.send_message(OWNER_ID, msg, disable_web_page_preview=True)
        
    elif query.data == "admin_disk":
        await query.answer()
        disk_gb = get_disk_usage(DOWNLOAD_DIR)
        msg = f"💾 **Disk Usage**: {disk_gb:.2f} GB"
        await client.send_message(OWNER_ID, msg)
        
    elif query.data == "admin_clear":
        await query.answer("Clearing files...")
        try:
            for filename in os.listdir(DOWNLOAD_DIR):
                os.remove(os.path.join(DOWNLOAD_DIR, filename))
            await client.send_message(OWNER_ID, "✅ Bot Cache cleared.")
        except Exception as e:
            await client.send_message(OWNER_ID, f"❌ Error: {e}")

# ==========================================
# USER HANDLERS
# ==========================================

async def check_channel_membership(client, user_id):
    """Verifies if user is subscribed to the channel."""
    try:
        # Requires bot to be admin in the channel
        chat_id = await client.get_chat(CHANNEL_LINK)
        member = await client.get_chat_member(chat_id.id, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception:
        return False

@app.on_message(filters.command("start"))
async def start_handler(client, msg):
    user_id = msg.from_user.id
    
    # 1. CHECK IF ADMIN
    if user_id == OWNER_ID:
        await msg.reply("🛡️ **Admin Mode**", reply_markup=generate_admin_buttons())
        return

    # 2. CHECK CHANNEL SUBSCRIPTION
    is_member = await check_channel_membership(client, user_id)
    if not is_member:
        await msg.reply(
            f"⛔ **Access Denied**\nPlease subscribe to our channel first:\n{CHANNEL_LINK}", 
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Check/Join Channel", url=CHANNEL_LINK)]
            ])
        )
        return

    # 3. REGISTER USER IF NEW
    get_user(user_id)
    await msg.reply(f"✅ **Welcome!** Usage limit: 15GB/Day.")

# ==========================================
# MAIN LOGIC (Owner Link Handling)
# ==========================================
@app.on_message(filters.chat(OWNER_ID) & filters.text)
async def main_handler(client, msg):
    user_id = msg.from_user.id
    text = msg.text.strip()

    if text.startswith("/cancel"): 
        await cleanup_user(user_id)
        return
    
    if text.startswith("/rename"):
        if user_id in bot_state:
            await msg.reply("Send new name:")
            bot_state[user_id]['state'] = 'rename_pending'
        return

    if text.startswith("http"):
        # NSFW Check for Owner
        if "youtube" in text and await check_nsfw(text):
            await msg.reply("⛔ NSFW content detected. Refusing.")
            return

        if "youtube" in text: await select_youtube_format(client, msg, text)
        else: await handle_download(client, msg, text)

# ==========================================
# CALLBACK HANDLERS (Upload, Rename, etc)
# ==========================================
@app.on_callback_query()
async def callback_handler(client, query):
    user_id = query.from_user.id
    if user_id != OWNER_ID:
        await query.answer("Unauthorized", show_alert=True)
        return

    if query.data == "cancel":
        await cleanup_user(user_id)
        await query.message.edit_text("❌ Cancelled.")
    elif query.data == "rename":
        bot_state[user_id]['state'] = 'rename_pending'
        await query.message.edit_text("📝 Send new name:")
    elif query.data.startswith("upload_"):
        _, filename = query.data.split("_", 1)
        path = bot_state[user_id]['file']
        
        try:
            # INCREMENT LINK COUNTER
            user_data = get_user(user_id)
            users_table.update({"links_count": user_data['links_count'] + 1}, Query().uid == user_id)
            
            is_video = filename.endswith(('.mp4', '.webm', '.mkv'))
            if is_video: await client.send_video(chat_id=query.message.chat.id, video=path, caption="Sent")
            else: await client.send_document(chat_id=query.message.chat.id, document=path, filename=filename)
            
            await cleanup_user(user_id)
            await query.message.delete()
        except Exception as e:
            await query.message.edit_text(f"Error: {e}")

# ==========================================
# HELPER FUNCTIONS
# ==========================================

async def select_youtube_format(client, msg, url):
    bot_state[msg.from_user.id] = {'url': url}
    await msg.reply("YouTube detected.\nChoose format:", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Video", callback_data="yt_video"), InlineKeyboardButton("🎵 Audio", callback_data="yt_audio")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ]))

async def download_youtube_file(client, msg, url, quality):
    status = await msg.reply("⏳ Downloading...")
    filename = "yt_temp.%(ext)s"
    try:
        if quality == "audio": cmd = ['yt-dlp', '-x', '--audio-format', 'mp3', '-o', filename, url]
        else: cmd = ['yt-dlp', '-f', 'bestvideo+bestaudio/best', '-merge-output-format', 'mp4', '-o', filename, url]
        subprocess.run(cmd, check=True, timeout=300)
        files = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith("yt_temp")]
        if files: await show_ready_state(client, msg, os.path.join(DOWNLOAD_DIR, files[0]))
        else: await status.edit("❌ Error.")
    except Exception as e:
        await status.edit(f"❌ Error: {e}")

async def handle_download(client, msg, url):
    status = await msg.reply("⏳ Downloading...")
    filename = "generic.%(ext)s"
    try:
        subprocess.run(['yt-dlp', '-f', 'best', '--no-playlist', '-o', filename, url], check=True, timeout=300)
    except: pass
    files = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith("generic")]
    if files: await show_ready_state(client, msg, os.path.join(DOWNLOAD_DIR, files[0]))
    else: status.edit("❌ Failed.")

async def show_ready_state(client, msg, file_path):
    filename = os.path.basename(file_path)
    bot_state[msg.from_user.id] = {'file': file_path, 'state': 'ready'}
    await client.send_message(msg.chat.id, f"File Ready: <code>{filename}</code>", reply_markup=generate_ready_buttons(filename))

@app.on_callback_query()
async def yt_callback(client, query):
    if query.data == "yt_video":
        await query.answer()
        await download_youtube_file(client, query.message, bot_state[query.from_user.id]['url'], "video")
    elif query.data == "yt_audio":
        await query.answer()
        await download_youtube_file(client, query.message, bot_state[query.from_user.id]['url'], "audio")

if __name__ == "__main__":
    app.run()
