import os
import time
import asyncio
import subprocess
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def human_size(num):
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(num) < 1024.0: return f"{num:3.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} TB"

async def progress_bar(current, total, status_msg, start_time, action):
    now = time.time()
    diff = now - start_time
    if diff < 1: return
    
    percentage = current * 100 / total
    speed = current / diff
    elapsed_time = round(diff)
    eta = round((total - current) / speed) if speed > 0 else 0
    
    bar = "".join(["■" for i in range(int(percentage // 10))])
    bar += "".join(["□" for i in range(10 - int(percentage // 10))])
    
    tmp = (
        f"**{action}**\n"
        f"[{bar}] {percentage:.1f}%\n"
        f"🚀 Speed: {human_size(speed)}/s\n"
        f"📟 Done: {human_size(current)} / {human_size(total)}\n"
        f"⏱️ ETA: {eta}s"
    )
    try:
        await status_msg.edit_text(tmp, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_task")]]))
    except: pass

async def run_yt_dlp(url, format_str, output_path):
    cmd = ["yt-dlp", "-f", format_str, "--no-playlist", "--cookies", "cookies.txt", "-o", output_path, url]
    process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    _, stderr = await process.communicate()
    return process.returncode, stderr.decode()
