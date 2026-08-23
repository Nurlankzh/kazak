import asyncio
import logging
from datetime import datetime

import aiosqlite

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher.handler import CancelHandler
from aiogram.dispatcher.middlewares import BaseMiddleware
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup
)
from aiogram.utils import executor

# =========================================================
# CONFIG (Барлығы тікелей жазылды, Railway қате бермейді)
# =========================================================

# ӨЗ ТОКЕНІҢІЗДІ ТӨМЕНДЕГІ ТЫРНАҚШАНЫҢ ІШІНЕ ЖАЗЫҢЫЗ
API_TOKEN = "8007564684:AAEb_Ib26hfjcu-feJnfy2MJdeGm5scSjOQ"

ADMIN_ID = 6303091468
CHANNEL_URL = "https://t.me/QZQCONTENT"
CHANNEL_ID = "@QZQCONTENT"
BOT_USERNAME = "yumybarbot" 
DB = "enterprise.db"

# =========================================================
# GENRES
# =========================================================

GENRES_CONFIG = {
    "🎬 Қазақша": {"price": 5},
    "🎬 Орысша": {"price": 4},
    "🧸 Балаларға": {"price": 6},
    "🎬 Шетелдік": {"price": 3},
    "💎 VIP Контент": {"price": 22}
}

GENRES = list(GENRES_CONFIG.keys())

NORMAL_GENRES = [
    g for g in GENRES
    if "VIP" not in g
]

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# =========================================================
# BOT & DP
# =========================================================

bot = Bot(token=API_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot, storage=MemoryStorage())

# =========================================================
# STATES
# =========================================================

class AdminStates(StatesGroup):
    give_id = State()
    give_amount = State()
    give_all_amount = State()
    broadcast_msg = State()
    add_v_genre = State()
    add_v_file = State()

class UserStates(StatesGroup):
    upload_genre = State()
    upload_video = State()

# =========================================================
# DATABASE
# =========================================================

async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY,
                balance INTEGER DEFAULT 10,
                last_bonus TEXT,
                last_active TEXT,
                vip_until TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS content(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id TEXT NOT NULL,
                type TEXT DEFAULT 'video',
                genre TEXT NOT NULL,
                file_unique_id TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS submissions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id TEXT NOT NULL,
                genre TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                file_unique_id TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS history(
                user_id INTEGER NOT NULL,
                content_id INTEGER NOT NULL,
                UNIQUE(user_id, content_id)
            )
        """)

        try:
            await db.execute("ALTER TABLE content ADD COLUMN file_unique_id TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE submissions ADD COLUMN file_unique_id TEXT")
        except Exception:
            pass

        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_content_unique ON content(file_unique_id)")
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_submissions_unique ON submissions(file_unique_id)")
        
        await db.commit()
    logger.info("Database дайын.")

# =========================================================
# USER HELPERS
# =========================================================

async def ensure_user(uid: int):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    async with aiosqlite.connect(DB) as db:
        user = await (await db.execute("SELECT id FROM users WHERE id=?", (uid,))).fetchone()
        if not user:
            await db.execute("""
                INSERT INTO users(id, balance, last_bonus, last_active, vip_until)
                VALUES (?, ?, ?, ?, ?)
            """, (uid, 10, now, now, "None"))
            await db.commit()

async def check_sub(uid: int) -> bool:
    if uid == ADMIN_ID:
        return True
    try:
        member = await bot.get_chat_member(CHANNEL_ID, uid)
        return member.status not in ("left", "kicked")
    except Exception as e:
        logger.error(f"Subscription check error for {uid}: {e}")
        return False

async def get_user(uid: int):
    async with aiosqlite.connect(DB) as db:
        return await (await db.execute(
            "SELECT balance, last_bonus, last_active, vip_until FROM users WHERE id=?", (uid,)
        )).fetchone()

# =========================================================
# KEYBOARDS
# =========================================================

def subscription_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📢 Каналға тіркелу", url=CHANNEL_URL),
        InlineKeyboardButton("✅ Тіркелдім", callback_data="check_subscription")
    )
    return kb

def main_kb(uid: int):
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("🎬 Контент", "➕ Видео жіберу")
    kb.add("💰 Баланс", "👥 Реферал")
    kb.add("💎 Монета сатып алу", "🔐 VIP контент")
    if uid == ADMIN_ID:
        kb.add("⚙️ Админ")
    return kb

def back_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 Артқа")

def finish_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True).add("✅ Аяқтау", "🔙 Артқа")

def genre_kb(include_vip=True):
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    genres = GENRES if include_vip else NORMAL_GENRES
    for genre in genres:
        kb.add(genre)
    kb.add("🔙 Артқа")
    return kb

# =========================================================
# MIDDLEWARE
# =========================================================

class MandatorySubMiddleware(BaseMiddleware):
    async def on_process_message(self, message: types.Message, data: dict):
        if message.chat.type != "private" or message.from_user.id == ADMIN_ID:
            return
        if message.text and message.text.startswith("/start"):
            return
        if not await check_sub(message.from_user.id):
            await message.answer(
                "⚠️ <b>Каналға тіркелу қажет!</b>\n\nБотты қолдану үшін алдымен біздің каналға тіркеліңіз.",
                reply_markup=subscription_kb()
            )
            raise CancelHandler()

    async def on_process_callback_query(self, call: types.CallbackQuery, data: dict):
        if call.from_user.id == ADMIN_ID or call.data == "check_subscription":
            return
        if not await check_sub(call.from_user.id):
            await call.answer("❌ Алдымен каналға тіркеліңіз!", show_alert=True)
            try:
                await bot.send_message(
                    call.from_user.id,
                    "⚠️ <b>Ботты қолдану үшін каналға тіркеліңіз:</b>",
                    reply_markup=subscription_kb()
                )
            except Exception:
                pass
            raise CancelHandler()

dp.middleware.setup(MandatorySubMiddleware())

# =========================================================
# GLOBAL BACK & FINISH
# =========================================================

@dp.message_handler(lambda m: m.text == "🔙 Артқа", state="*")
async def global_back(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("🏠 Басты мәзір:", reply_markup=main_kb(message.from_user.id))

@dp.message_handler(lambda m: m.text == "✅ Аяқтау", state=[AdminStates.add_v_file, UserStates.upload_video])
async def finish_upload(message: types.Message, state: FSMContext):
    data = await state.get_data()
    added = data.get("added", 0)
    dupes = data.get("dupes", 0)
    state_name = await state.get_state()
    
    text = f"📊 <b>Жүктеу нәтижесі:</b>\n\n✅ Қабылданды: <b>{added}</b>\n♻️ Қайталанған: <b>{dupes}</b>"
    if state_name == "UserStates:upload_video":
        text += "\n\nℹ️ Видеолар админ мақұлдағаннан кейін монета беріледі."
        
    await state.finish()
    await message.answer(text, reply_markup=main_kb(message.from_user.id))

# =========================================================
# COMMANDS
# =========================================================

@dp.message_handler(commands=["start"], state="*")
async def start(message: types.Message, state: FSMContext):
    await state.finish()
    uid = message.from_user.id
    ref = message.get_args().strip()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    async with aiosqlite.connect(DB) as db:
        user = await (await db.execute("SELECT id FROM users WHERE id=?", (uid,))).fetchone()
        if not user:
            await db.execute("""
                INSERT INTO users(id, balance, last_bonus, last_active, vip_until)
                VALUES (?, ?, ?, ?, ?)
            """, (uid, 10, now, now, "None"))
            
            if ref.isdigit():
                ref_id = int(ref)
                if ref_id != uid:
                    ref_user = await (await db.execute("SELECT id FROM users WHERE id=?", (ref_id,))).fetchone()
                    if ref_user:
                        await db.execute("UPDATE users SET balance = balance + 6 WHERE id=?", (ref_id,))
                        try:
                            await bot.send_message(ref_id, "🎁 <b>Реферал бонусы!</b>\n+6 монета алдыңыз.")
                        except Exception:
                            pass
        await db.commit()

    if not await check_sub(uid):
        await message.answer(
            "👋 <b>Сәлем!</b>\n\nБотты қолдану үшін каналға міндетті түрде тіркеліңіз.",
            reply_markup=subscription_kb()
        )
        return

    await message.answer(
        "✅ <b>Қош келдіңіз!</b>\n\nТөмендегі мәзірден қажетті бөлімді таңдаңыз.",
        reply_markup=main_kb(uid)
    )

@dp.callback_query_handler(lambda c: c.data == "check_subscription")
async def check_subscription_callback(call: types.CallbackQuery):
    if await check_sub(call.from_user.id):
        try:
            await call.message.delete()
        except Exception:
            pass
        await bot.send_message(
            call.from_user.id,
            "✅ <b>Тіркелу сәтті расталды!</b>\n\nЕнді ботты қолдана аласыз.",
            reply_markup=main_kb(call.from_user.id)
        )
        await call.answer()
    else:
        await call.answer("❌ Сіз әлі каналға тіркелмедіңіз!", show_alert=True)

# =========================================================
# USER FUNCTION HANDLERS
# =========================================================

@dp.message_handler(lambda m: m.text == "💰 Баланс")
async def user_balance(message: types.Message):
    user_data = await get_user(message.from_user.id)
    if user_data:
        balance = user_data[0]
        await message.answer(f"💰 Сіздің қазіргі балансыңыз: <b>{balance}</b> монета.")

@dp.message_handler(lambda m: m.text == "👥 Реферал")
async def user_referral(message: types.Message):
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={message.from_user.id}"
    await message.answer(
        f"👥 <b>Рефералдық бағдарлама</b>\n\n"
        f"Достарыңызды шақырып монета жинаңыз!\nӘр шақырылған дос үшін <b>+6 монета</b> беріледі.\n\n"
        f"🔗 <b>Сіздің сілтемеңіз:</b>\n<code>{ref_link}</code>"
    )

@dp.message_handler(lambda m: m.text in ["🎬 Контент", "💎 Монета сатып алу", "🔐 VIP контент"])
async def upcoming_features(message: types.Message):
    await message.answer("Бұл бөлім жақын арада іске қосылады ⏳")

@dp.message_handler(lambda m: m.text == "➕ Видео жіберу")
async def user_upload_start(message: types.Message):
    await UserStates.upload_genre.set()
    await message.answer("📂 <b>Қай жанрға видео жібересіз?</b>", reply_markup=genre_kb(include_vip=False))

@dp.message_handler(state=UserStates.upload_genre)
async def user_upload_genre(message: types.Message, state: FSMContext):
    if message.text not in NORMAL_GENRES:
        await message.answer("❌ Бұл жанрды таңдауға болмайды.\nТізімнен таңдаңыз.")
        return
    await state.update_data(genre=message.text, added=0, dupes=0)
    await UserStates.upload_video.set()
    await message.answer(
        f"📂 Жанр: <b>{message.text}</b>\n\n🎥 Видеоларды жібере беріңіз.\nБірнеше видео жіберуге болады.\n\nВидеолар админ тексеруіне түседі.",
        reply_markup=finish_kb()
    )

@dp.message_handler(state=UserStates.upload_video, content_types=["video"])
async def user_upload_video(message: types.Message, state: FSMContext):
    data = await state.get_data()
    unique_id = message.video.file_unique_id
    async with aiosqlite.connect(DB) as db:
        content_exists = await (await db.execute("SELECT id FROM content WHERE file_unique_id=?", (unique_id,))).fetchone()
        sub_exists = await (await db.execute("SELECT id FROM submissions WHERE file_unique_id=?", (unique_id,))).fetchone()
        if content_exists or sub_exists:
            await state.update_data(dupes=data.get("dupes", 0) + 1)
            try:
                await message.delete()
            except Exception:
                pass
            return
        await db.execute(
            "INSERT INTO submissions(file_id, file_unique_id, genre, user_id) VALUES (?, ?, ?, ?)",
            (message.video.file_id, unique_id, data["genre"], message.from_user.id)
        )
        await db.commit()
    await state.update_data(added=data.get("added", 0) + 1)

# =========================================================
# ADMIN PANEL
# =========================================================

@dp.message_handler(lambda m: m.text == "⚙️ Админ", user_id=ADMIN_ID)
async def admin_panel(message: types.Message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("➕ Видео қосу", "📩 Жіберілгендер")
    kb.add("💰 Монета беру", "🌍 Барлығына монета")
    kb.add("📢 Рассылка", "📊 Статистика")
    kb.add("🔙 Артқа")
    await message.answer("👑 <b>Админ панелі</b>\n\nҚажетті бөлімді таңдаңыз:", reply_markup=kb)

@dp.message_handler(lambda m: m.text in ["📩 Жіберілгендер", "💰 Монета беру", "🌍 Барлығына монета", "📢 Рассылка", "📊 Статистика"], user_id=ADMIN_ID)
async def admin_features(message: types.Message):
    if message.text == "📊 Статистика":
        async with aiosqlite.connect(DB) as db:
            users_count = await (await db.execute("SELECT COUNT(id) FROM users")).fetchone()
            content_count = await (await db.execute("SELECT COUNT(id) FROM content")).fetchone()
        await message.answer(
            f"📊 <b>Бот статистикасы:</b>\n\n"
            f"👥 Қолданушылар: <b>{users_count[0]}</b>\n"
            f"🎥 Жүктелген видеолар: <b>{content_count[0]}</b>"
        )
    else:
        await message.answer(f"{message.text} бөлімі дайындалу үстінде ⏳")

@dp.message_handler(lambda m: m.text == "➕ Видео қосу", user_id=ADMIN_ID)
async def add_video_start(message: types.Message):
    await AdminStates.add_v_genre.set()
    await message.answer("📂 <b>Видео жанрын таңдаңыз:</b>", reply_markup=genre_kb(include_vip=True))

@dp.message_handler(state=AdminStates.add_v_genre, user_id=ADMIN_ID)
async def add_video_genre(message: types.Message, state: FSMContext):
    if message.text not in GENRES:
        await message.answer("❌ Тізімнен жанр таңдаңыз.")
        return
    await state.update_data(genre=message.text, added=0, dupes=0)
    await AdminStates.add_v_file.set()
    await message.answer(
        f"📂 Жанр: <b>{message.text}</b>\n\n🎥 Видеоларды жібере беріңіз.\nБірнеше видео жіберуге болады.\n\nБолған кезде «✅ Аяқтау» батырмасын басыңыз.",
        reply_markup=finish_kb()
    )

@dp.message_handler(state=AdminStates.add_v_file, content_types=["video"], user_id=ADMIN_ID)
async def add_video_file(message: types.Message, state: FSMContext):
    data = await state.get_data()
    unique_id = message.video.file_unique_id
    async with aiosqlite.connect(DB) as db:
        exists = await (await db.execute("SELECT id FROM content WHERE file_unique_id=?", (unique_id,))).fetchone()
        if exists:
            await state.update_data(dupes=data.get("dupes", 0) + 1)
            try:
                await message.delete()
            except Exception:
                pass
            return
        await db.execute(
            "INSERT INTO content(file_id, file_unique_id, type, genre) VALUES (?, ?, ?, ?)",
            (message.video.file_id, unique_id, "video", data["genre"])
        )
        await db.commit()
    await state.update_data(added=data.get("added", 0) + 1)

# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=lambda x: init_db(), skip_updates=True)
