import aiosqlite

DB_NAME = "bot.db"

async def connect():
    return await aiosqlite.connect(DB_NAME)
