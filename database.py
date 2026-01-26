from tinydb import TinyDB, Query
from config import DB_PATH

db = TinyDB(DB_PATH)
users = db.table("users")
cache = db.table("cache")

def get_user(uid):
    U = Query()
    res = users.get(U.uid == uid)
    if not res:
        users.insert({"uid": uid, "is_pro": False, "usage": 0, "warns": 0, "banned": False})
        return users.get(U.uid == uid)
    return res

def update_user(uid, data):
    users.update(data, Query().uid == uid)

def get_cached_link(url):
    return cache.get(Query().url == url)

def add_to_cache(url, file_id, file_type, caption):
    cache.insert({"url": url, "file_id": file_id, "type": file_type, "caption": caption})
