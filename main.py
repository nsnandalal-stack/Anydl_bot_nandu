import os
import re
import time
import json
import math
import shutil
import asyncio
import subprocess
from aiohttp import web

from pyrogram import Client, filters, types, enums, idle, errors
from yt_dlp import YoutubeDL

# =======================
# Config
# =======================
OWNER_ID = 519459195

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

INVITE_LINK = "https://t.me/+eooytvOAwjc0NTI1"
CONTACT_URL = "https://t.me/poocha"

DOWNLOAD_DIR = "/app/downloads"
THUMB_DIR = "/app/thumbnails"
DB_FILE = "/app/database.json"
COOKIES_FILE = "/app/cookies.txt"

# =======================
# Pyrogram client
# =======================
app = Client(
    "dl_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    sleep_threshold=120
)

# =======================
# Persistence
# =======================
DB = {
    "users": {},   # uid(str): {"thumb": str|None, "state": "none|await_thumb|await_rename|await_bc_text", "pending": {}}
    "active": {},  # uid(str): {"status": ..., "path": ..., "name": ..., "ext": ..., "url": ..., "cancel": bool}
}

def db_load():
    global DB
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                DB = json.load(f)
        except Exception:
            DB = {"users": {}, "active": {}}
    DB.setdefault("users", {})
    DB.setdefault("active", {})

def db_save():
    tmp = DB_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(DB, f, ensure_ascii=False)
    os.replace(tmp, DB_FILE)

def ukey(uid: int) -> str:
    return str(uid)

def user_get(uid: int) -> dict:
    k = ukey(uid)
    if k not in DB["users"]:
        DB["users"][k] = {"thumb": None, "state": "none", "pending": {}}
    return DB["users"][k]

def session_get(uid: int) -> dict | None:
    return DB["active"].get(ukey(uid))

def session_set(uid: int, s: dict):
    DB["active"][ukey(uid)] = s
    db_save()

def session_clear(uid: int):
    DB["active"].pop(ukey(uid), None)
    u = user_get(uid)
    u["state"] = "none"
    u["pending"] = {}
    db_save()

# =======================
# Helpers
# =======================
def is_youtube(url: str) -> bool:
    u = url.lower()
    return "youtube.com" in u or "youtu.be" in u

def safe_filename(name: str) -> str:
    name = name.strip().replace("\n", " ")
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:180] if name else "file"

def human_size(n: int | float | None) -> str:
    if not n:
        return "0B"
    n = float(n)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"

def human_time(seconds: float | None) -> str:
    if seconds is None or seconds <= 0 or math.isinf(seconds):
        return "—"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"

async def safe_edit(msg: types.Message, text: str, reply_markup=None):
    try:
        return await msg.edit_text(text, reply_markup=reply_markup)
    except errors.MessageNotModified:
        return msg
    except Exception:
        return msg

async def is_subscribed(uid: int) -> bool:
    if uid == OWNER_ID:
        return True
    try:
        m = await app.get_chat_member(CHANNEL_ID, uid)
        return m.status in (
            enums.ChatMemberStatus.MEMBER,
            enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.OWNER,
        )
    except errors.UserNotParticipant:
        return False
    except Exception:
        return False

# =======================
# Keyboards
# =======================
def join_markup():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("Join Channel", url=INVITE_LINK)],
        [types.InlineKeyboardButton("Verify", callback_data="join_verify")]
    ])

def main_menu_markup(uid: int):
    kb = [
        [
            types.InlineKeyboardButton("Help", callback_data="menu_help"),
            types.InlineKeyboardButton("My ID", callback_data="menu_id"),
        ],
        [types.InlineKeyboardButton("Thumbnail Manager", callback_data="thumb_menu")],
    ]
    if uid == OWNER_ID:
        kb.append([types.InlineKeyboardButton("Admin Dashboard", callback_data="admin_menu")])
    else:
        kb.append([types.InlineKeyboardButton("Upgrade", url=CONTACT_URL)])
    kb.append([types.InlineKeyboardButton("Exit", callback_data="menu_exit")])
    return types.InlineKeyboardMarkup(kb)

def thumb_menu_markup():
    # STRICT: only View/Delete + Exit
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("View Thumbnail", callback_data="thumb_view"),
            types.InlineKeyboardButton("Delete Thumbnail", callback_data="thumb_delete"),
        ],
        [types.InlineKeyboardButton("Exit", callback_data="thumb_exit")]
    ])

def ready_markup():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("Rename", callback_data="act_rename"),
            types.InlineKeyboardButton("Upload", callback_data="act_upload"),
        ],
        [types.InlineKeyboardButton("Cancel", callback_data="act_cancel")],
    ])

def rename_choice_markup():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("Use Default Name", callback_data="ren_default"),
            types.InlineKeyboardButton("Enter New Name", callback_data="ren_custom"),
        ],
        [types.InlineKeyboardButton("Cancel", callback_data="act_cancel")]
    ])

def upload_choice_markup():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("Video", callback_data="up_as_video"),
            types.InlineKeyboardButton("File", callback_data="up_as_file"),
        ],
        [types.InlineKeyboardButton("Upload + Screenshots", callback_data="up_with_screens")],
        [types.InlineKeyboardButton("Cancel", callback_data="act_cancel")]
    ])

def youtube_format_markup():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("1080p", callback_data="yt_v_1080"),
         types.InlineKeyboardButton("720p", callback_data="yt_v_720"),
         types.InlineKeyboardButton("480p", callback_data="yt_v_480"),
         types.InlineKeyboardButton("360p", callback_data="yt_v_360")],
        [types.InlineKeyboardButton("MP3", callback_data="yt_a_mp3"),
         types.InlineKeyboardButton("M4A", callback_data="yt_a_m4a"),
         types.InlineKeyboardButton("AAC", callback_data="yt_a_aac")],
        [types.InlineKeyboardButton("Cancel", callback_data="act_cancel")]
    ])

def admin_menu_markup():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("Reports", callback_data="admin_reports"),
         types.InlineKeyboardButton("Broadcast", callback_data="admin_broadcast")],
        [types.InlineKeyboardButton("Back", callback_data="admin_back")]
    ])

def broadcast_confirm_markup():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("Confirm", callback_data="bc_confirm"),
         types.InlineKeyboardButton("Stop", callback_data="bc_stop")]
    ])

# =======================
# yt-dlp helpers
# =======================
def ydl_base_opts():
    opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": f"{DOWNLOAD_DIR}/%(title)s.%(ext)s",
    }
    if os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    return opts

async def download_generic(url: str) -> tuple[str, str]:
    opts = ydl_base_opts()
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
        return path, os.path.basename(path)

async def download_youtube(url: str, kind: str, value: str) -> tuple[str, str]:
    opts = ydl_base_opts()
    if kind == "v":
        h = int(value)
        opts["format"] = f"bestvideo[height<={h}]+bestaudio/best"
    else:
        opts["format"] = "bestaudio/best"
        if value in ("mp3", "m4a", "aac"):
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": value}]
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
        if kind == "a" and value in ("mp3", "m4a", "aac"):
            path = os.path.splitext(path)[0] + "." + value
        return path, os.path.basename(path)

# =======================
# Screenshots + upload progress
# =======================
async def generate_screenshots(video_path: str, uid: int):
    out_dir = os.path.join(DOWNLOAD_DIR, f"screens_{uid}")
    os.makedirs(out_dir, exist_ok=True)
    try:
        cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{video_path}"'
        dur = float(subprocess.check_output(cmd, shell=True).decode().strip() or "0")
        if dur <= 0:
            return [], out_dir
        medias = []
        for i in range(1, 11):
            t = (dur / 11) * i
            out = os.path.join(out_dir, f"{i}.jpg")
            subprocess.call(
                ["ffmpeg", "-ss", str(t), "-i", video_path, "-vframes", "1", "-q:v", "2", out, "-y"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if os.path.exists(out):
                medias.append(types.InputMediaPhoto(out))
        return medias, out_dir
    except Exception:
        return [], out_dir

async def upload_with_progress(chat_id: int, msg: types.Message, path: str, as_video: bool, thumb_path: str | None):
    start = time.time()
    last = {"t": 0.0}

    async def prog(cur, tot):
        s = session_get(chat_id)
        if s and s.get("cancel"):
            raise Exception("CANCELLED")
        now = time.time()
        if now - last["t"] < 3:
            return
        last["t"] = now
        speed = cur / max(1, now - start)
        eta = (tot - cur) / speed if speed > 0 and tot else None
        await safe_edit(
            msg,
            f"Uploading… {human_size(cur)}/{human_size(tot)} | ETA {human_time(eta)}",
            reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Cancel", callback_data="act_cancel")]])
        )

    if as_video:
        return await app.send_video(
            chat_id=chat_id,
            video=path,
            supports_streaming=True,
            thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
            progress=prog,
        )
    return await app.send_document(
        chat_id=chat_id,
        document=path,
        thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
        progress=prog,
    )

# =======================
# Health server
# =======================
async def health(_):
    return web.Response(text="OK")

# =======================
# Commands
# =======================
@app.on_message(filters.command("start") & filters.private)
async def cmd_start(_, m: types.Message):
    user_get(m.from_user.id)
    db_save()
    await m.reply_text(
        "Chief, systems are ready." if m.from_user.id == OWNER_ID else "Appreciate you using this bot.",
        reply_markup=main_menu_markup(m.from_user.id),
    )

@app.on_message(filters.command("help") & filters.private)
async def cmd_help(_, m: types.Message):
    await m.reply_text(
        "Commands:\n"
        "/start\n"
        "/help\n"
        "/id\n"
        "/setcustomthumbnail\n\n"
        "Workflows:\n"
        "• YouTube link → choose format → download → Rename/Upload/Cancel\n"
        "• Other link → download → Rename/Upload/Cancel\n"
        "• Forward file → download → Rename/Upload/Cancel\n",
        reply_markup=main_menu_markup(m.from_user.id),
    )

@app.on_message(filters.command("id") & filters.private)
async def cmd_id(_, m: types.Message):
    await m.reply_text(f"Your ID: `{m.from_user.id}`")

@app.on_message(filters.command("setcustomthumbnail") & filters.private)
async def cmd_setthumb(_, m: types.Message):
    u = user_get(m.from_user.id)
    u["state"] = "await_thumb"
    db_save()
    await m.reply_text("Send a photo now to set as your custom thumbnail.")

# =======================
# Photo (thumbnail)
# =======================
@app.on_message(filters.photo & filters.private)
async def on_photo(_, m: types.Message):
    uid = m.from_user.id
    u = user_get(uid)
    if u.get("state") != "await_thumb":
        return
    path = os.path.join(THUMB_DIR, f"{uid}.jpg")
    await m.download(path)
    u["thumb"] = path
    u["state"] = "none"
    db_save()
    await m.reply_text("Thumbnail saved.", reply_markup=main_menu_markup(uid))

# =======================
# Forwarded files
# =======================
@app.on_message((filters.video | filters.document | filters.audio | filters.voice | filters.animation) & filters.private)
async def on_forwarded(_, m: types.Message):
    uid = m.from_user.id
    if not await is_subscribed(uid):
        await m.reply_text("Join channel first.", reply_markup=join_markup())
        return

    media = m.video or m.document or m.audio or m.voice or m.animation
    if not media:
        return

    status = await m.reply_text(
        "Downloading…",
        reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Cancel", callback_data="act_cancel")]])
    )
    path = os.path.join(DOWNLOAD_DIR, f"fwd_{uid}_{int(time.time())}")
    session_set(uid, {"cancel": False})

    try:
        await m.download(path)
    except Exception:
        session_clear(uid)
        return await safe_edit(status, "Download failed.", None)

    orig = getattr(media, "file_name", None) or os.path.basename(path)
    name = safe_filename(orig)
    ext = os.path.splitext(name)[1] or os.path.splitext(path)[1] or ""

    sess = {"path": path, "orig_name": name, "name": name, "ext": ext, "status": "ready", "cancel": False}
    session_set(uid, sess)
    await safe_edit(status, f"Downloaded: `{name}`", reply_markup=ready_markup())

# =======================
# Text handler (links + rename + broadcast)
# =======================
@app.on_message(filters.text & ~filters.command(["start", "help", "id", "setcustomthumbnail"]) & filters.private)
async def on_text(_, m: types.Message):
    uid = m.from_user.id
    u = user_get(uid)

    # rename input
    if u.get("state") == "await_rename_name":
        sess = session_get(uid)
        if not sess or "path" not in sess or not os.path.exists(sess["path"]):
            u["state"] = "none"
            db_save()
            return await m.reply_text("No active file. Send again.")
        base = safe_filename(m.text)
        ext = sess.get("ext") or os.path.splitext(sess["path"])[1] or ""
        new_name = base + ext
        new_path = os.path.join(DOWNLOAD_DIR, new_name)
        try:
            os.rename(sess["path"], new_path)
        except Exception:
            u["state"] = "none"
            db_save()
            return await m.reply_text("Rename failed.")
        sess["path"] = new_path
        sess["name"] = new_name
        sess["status"] = "ready"
        u["state"] = "none"
        session_set(uid, sess)
        return await m.reply_text(f"Renamed: `{new_name}`", reply_markup=ready_markup())

    # broadcast text
    if uid == OWNER_ID and u.get("state") == "await_bc_text":
        u["state"] = "none"
        u["pending"]["broadcast_text"] = m.text
        db_save()
        return await m.reply_text(f"Preview:\n\n{m.text}", reply_markup=broadcast_confirm_markup())

    # link
    text = m.text.strip()
    if not (text.startswith("http://") or text.startswith("https://")):
        return

    if not await is_subscribed(uid):
        await m.reply_text("Join channel first.", reply_markup=join_markup())
        return

    status = await m.reply_text(
        "Analyzing…",
        reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Cancel", callback_data="act_cancel")]])
    )
    session_set(uid, {"cancel": False})

    if is_youtube(text):
        session_set(uid, {"url": text, "status": "await_format", "cancel": False})
        return await safe_edit(status, "YouTube detected. Select a format:", reply_markup=youtube_format_markup())

    try:
        await safe_edit(
            status,
            "Downloading…",
            reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Cancel", callback_data="act_cancel")]])
        )
        path, name = await download_generic(text)
        ext = os.path.splitext(name)[1]
        sess = {"path": path, "orig_name": name, "name": name, "ext": ext, "status": "ready", "cancel": False}
        session_set(uid, sess)
        return await safe_edit(status, f"Downloaded: `{name}`", reply_markup=ready_markup())
    except Exception as e:
        session_clear(uid)
        return await safe_edit(status, f"Error: {str(e)[:120]}", None)

# =======================
# Callbacks
# =======================
@app.on_callback_query()
async def on_cb(_, cb: types.CallbackQuery):
    uid = cb.from_user.id
    data = cb.data
    u = user_get(uid)
    await cb.answer()

    # menu
    if data == "menu_help":
        return await safe_edit(cb.message, "Commands: /start /help /id /setcustomthumbnail", reply_markup=main_menu_markup(uid))
    if data == "menu_id":
        return await cb.answer(f"Your ID: {uid}", show_alert=True)
    if data == "menu_exit":
        try:
            await cb.message.delete()
        except Exception:
            pass
        return

    # thumbnail manager strict
    if data == "thumb_menu":
        return await safe_edit(cb.message, "Thumbnail Manager", reply_markup=thumb_menu_markup())
    if data == "thumb_view":
        thumb = u.get("thumb")
        if thumb and os.path.exists(thumb):
            await cb.message.reply_photo(thumb, caption="Your thumbnail")
        else:
            await cb.answer("No thumbnail set.", show_alert=True)
        return
    if data == "thumb_delete":
        thumb = u.get("thumb")
        if thumb and os.path.exists(thumb):
            try:
                os.remove(thumb)
            except Exception:
                pass
        u["thumb"] = None
        db_save()
        await cb.answer("Deleted.", show_alert=True)
        return
    if data == "thumb_exit":
        try:
            await cb.message.delete()
        except Exception:
            pass
        return

    # join verify
    if data == "join_verify":
        ok = await is_subscribed(uid)
        if ok:
            return await safe_edit(cb.message, "Verified. You can use the bot now.", reply_markup=main_menu_markup(uid))
        return await safe_edit(cb.message, "Join channel first.", reply_markup=join_markup())

    # admin
    if data == "admin_menu":
        if uid != OWNER_ID:
            return await cb.answer("Not allowed.", show_alert=True)
        return await safe_edit(cb.message, "Admin Dashboard", reply_markup=admin_menu_markup())

    if data == "admin_back":
        return await safe_edit(cb.message, "Main menu", reply_markup=main_menu_markup(uid))

    if data == "admin_reports":
        if uid != OWNER_ID:
            return await cb.answer("Not allowed.", show_alert=True)
        total, used, free = shutil.disk_usage("/")
        txt = f"Users: {len(DB['users'])}\nActive: {len(DB['active'])}\nDisk used: {human_size(used)} / {human_size(total)} (free {human_size(free)})"
        return await safe_edit(cb.message, txt, reply_markup=admin_menu_markup())

    if data == "admin_broadcast":
        if uid != OWNER_ID:
            return await cb.answer("Not allowed.", show_alert=True)
        u["state"] = "await_bc_text"
        db_save()
        return await safe_edit(cb.message, "Send broadcast text now.", reply_markup=admin_menu_markup())

    if data == "bc_stop":
        if uid != OWNER_ID:
            return
        u["state"] = "none"
        u["pending"]["broadcast_text"] = ""
        db_save()
        return await safe_edit(cb.message, "Broadcast cancelled.", reply_markup=admin_menu_markup())

    if data == "bc_confirm":
        if uid != OWNER_ID:
            return
        text = u.get("pending", {}).get("broadcast_text", "")
        if not text:
            return await cb.answer("No broadcast text.", show_alert=True)
        sent = 0
        for k in list(DB["users"].keys()):
            try:
                await app.send_message(int(k), f"Broadcast:\n\n{text}")
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                continue
        u["pending"]["broadcast_text"] = ""
        db_save()
        return await safe_edit(cb.message, f"Sent to {sent} users.", reply_markup=admin_menu_markup())

    # cancel (global)
    if data == "act_cancel":
        sess = session_get(uid)
        if sess:
            sess["cancel"] = True
            session_set(uid, sess)

        if sess and sess.get("path") and os.path.exists(sess["path"]):
            try:
                os.remove(sess["path"])
            except Exception:
                pass

        session_clear(uid)
        return await safe_edit(cb.message, "Cancelled.", reply_markup=None)

    # session required below
    sess = session_get(uid)
    if not sess:
        return await cb.answer("No active task.", show_alert=True)

    # youtube format selection
    if data.startswith("yt_v_"):
        h = data.split("_")[-1]
        try:
            await safe_edit(cb.message, f"Downloading YouTube video {h}p…",
                            reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Cancel", callback_data="act_cancel")]]))
            path, name = await download_youtube(sess["url"], "v", h)
            ext = os.path.splitext(name)[1]
            session_set(uid, {"path": path, "orig_name": name, "name": name, "ext": ext, "status": "ready", "cancel": False})
            return await safe_edit(cb.message, f"Downloaded: `{name}`", reply_markup=ready_markup())
        except Exception as e:
            session_clear(uid)
            return await safe_edit(cb.message, f"Error: {str(e)[:120]}", None)

    if data.startswith("yt_a_"):
        codec = data.split("_")[-1]
        try:
            await safe_edit(cb.message, f"Downloading YouTube audio {codec.upper()}…",
                            reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Cancel", callback_data="act_cancel")]]))
            path, name = await download_youtube(sess["url"], "a", codec)
            ext = os.path.splitext(name)[1]
            session_set(uid, {"path": path, "orig_name": name, "name": name, "ext": ext, "status": "ready", "cancel": False})
            return await safe_edit(cb.message, f"Downloaded: `{name}`", reply_markup=ready_markup())
        except Exception as e:
            session_clear(uid)
            return await safe_edit(cb.message, f"Error: {str(e)[:120]}", None)

    # rename
    if data == "act_rename":
        return await safe_edit(cb.message, "Rename:", reply_markup=rename_choice_markup())

    if data == "ren_default":
        u["state"] = "none"
        db_save()
        return await safe_edit(cb.message, f"Using default name: `{sess.get('name')}`", reply_markup=ready_markup())

    if data == "ren_custom":
        u["state"] = "await_rename_name"
        db_save()
        return await safe_edit(cb.message, "Send new name text (extension will be added).", reply_markup=None)

    # upload
    if data == "act_upload":
        return await safe_edit(cb.message, "Choose upload type:", reply_markup=upload_choice_markup())

    if data in ("up_as_video", "up_as_file", "up_with_screens"):
        if not sess.get("path") or not os.path.exists(sess["path"]):
            session_clear(uid)
            return await safe_edit(cb.message, "File missing. Send again.", None)

        as_video = (data == "up_as_video")
        do_screens = (data == "up_with_screens")

        try:
            if do_screens:
                medias, outdir = await generate_screenshots(sess["path"], uid)
                if medias:
                    await app.send_media_group(uid, medias)
                shutil.rmtree(outdir, ignore_errors=True)

            thumb = u.get("thumb") if u.get("thumb") and os.path.exists(u.get("thumb")) else None
            prog_msg = await cb.message.reply_text(
                "Uploading…",
                reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Cancel", callback_data="act_cancel")]])
            )

            await upload_with_progress(uid, prog_msg, sess["path"], as_video, thumb)

            # delete from server after upload
            try:
                os.remove(sess["path"])
            except Exception:
                pass

            session_clear(uid)

            try:
                await prog_msg.delete()
            except Exception:
                pass

            return await safe_edit(cb.message, "Done.", reply_markup=None)
        except Exception as e:
            try:
                if sess.get("path") and os.path.exists(sess["path"]):
                    os.remove(sess["path"])
            except Exception:
                pass
            session_clear(uid)
            return await safe_edit(cb.message, f"Upload error: {str(e)[:120]}", None)

    return await cb.answer("Unhandled action.", show_alert=True)

# =======================
# Main
# =======================
async def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(THUMB_DIR, exist_ok=True)
    db_load()

    await app.start()

    # Koyeb health server
    srv = web.Application()
    srv.add_routes([web.get("/", health)])
    runner = web.AppRunner(srv)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()

    await idle()
    await app.stop()

if __name__ == "__main__":
    app.run(main())
