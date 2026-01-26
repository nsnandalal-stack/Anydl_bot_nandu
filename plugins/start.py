from pyrogram import Client
from config import API_ID, API_HASH, BOT_TOKEN, DOWNLOAD_DIR
import os

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# Plugins parameter tells Pyrogram to look inside the "plugins" folder
app = Client(
    "dl_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    plugins=dict(root="plugins")
)

if __name__ == "__main__":
    print("Bot started successfully!")
    app.run()
