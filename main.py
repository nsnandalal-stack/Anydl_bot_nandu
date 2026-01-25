from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

OWNER_ID = 519459195

DOWNLOADS = "downloads"
os.makedirs(DOWNLOADS, exist_ok=True)

USER_FILES = {}

app = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

async def smart_download(url):
    try:
        ydl_opts = {
            "outtmpl": f"{DOWNLOADS}/%(title)s.%(ext)s",
            "format": "best"
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
    except:
        pass

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            if r.status != 200:
                raise Exception("Unsupported link")

            name = url.split("/")[-1]
            path = f"{DOWNLOADS}/{name}"
            with open(path, "wb") as f:
                f.write(await r.read())
            return path

@app.on_message(filters.command("start"))
async def start(_, m):
    if m.from_user.id != OWNER_ID:
        return
    await m.reply("Send me any link.")

@app.on_message(filters.text & ~filters.command)
async def receive(_, m):
    if m.from_user.id != OWNER_ID:
        return

    msg = await m.reply("Downloading...")
    try:
        path = await smart_download(m.text)
        USER_FILES[m.from_user.id] = path

        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("Upload", callback_data="upload")],
            [InlineKeyboardButton("Rename", callback_data="rename")]
        ])

        await msg.edit("File ready:", reply_markup=buttons)
    except Exception as e:
        await msg.edit(f"Failed: {e}")

@app.on_callback_query()
async def callbacks(_, cb):
    if cb.from_user.id != OWNER_ID:
        return

    path = USER_FILES.get(cb.from_user.id)
    if not path:
        await cb.answer("No file", show_alert=True)
        return

    if cb.data == "upload":
        await cb.message.reply_document(path)
        os.remove(path)
        USER_FILES.pop(cb.from_user.id)

    elif cb.data == "rename":
        await cb.message.reply("Send new filename with extension:")

@app.on_message(filters.text & filters.private)
async def rename(_, m):
    if m.from_user.id != OWNER_ID:
        return

    path = USER_FILES.get(m.from_user.id)
    if not path:
        return

    new_path = f"{DOWNLOADS}/{m.text}"
    os.rename(path, new_path)
    USER_FILES[m.from_user.id] = new_path
    await m.reply("Renamed. Press Upload button.")

if __name__ == "__main__":
    app.run()
