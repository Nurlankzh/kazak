import asyncio
import logging
import aiosqlite
import re
import unicodedata
import os
import sys
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher.handler import CancelHandler
from aiogram.dispatcher.middlewares import BaseMiddleware
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from aiogram.utils.exceptions import Throttled, TelegramAPIError

# --- CONFIG ---
API_TOKEN = os.getenv("BOT_TOKEN")
if not API_TOKEN:
    print("ҚАТЕ: BOT_TOKEN көрсетілмеген! Railway Variables бөліміне қосыңыз.")
    sys.exit(1)

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/QZQCONTENT")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@QZQCONTENT")
DB = "enterprise.db"

GENRES_CONFIG = {
    "🎬 Қазақша": {"price": 5},
    "🥵 Орысша": {"price": 4},
    "🤭 Bala": {"price": 6},
    "😍 Американша": {"price": 3},
    "😈 VIP Видео": {"price": 22}
}
GENRES = list(GENRES_CONFIG.keys())

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
bot = Bot(token=API_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot, storage=MemoryStorage())

# --- FLOOD PROTECTION MIDDLEWARE ---
class AntiFloodMiddleware(BaseMiddleware):
    def __init__(self, limit=1.5):
        self.limit = limit
        super(AntiFloodMiddleware, self).__init__()

    async def on_process_message(self, message: types.Message, data: dict):
        # Админ үшін антифлуд өшірілген
        if message.from_user.id == ADMIN_ID:
            return
            
        try:
            await dp.throttle('global_throttling', rate=self.limit)
        except Throttled:
            await message.answer("⚠️ <b>Тым жылдам жазбаңыз!</b> Сәл күте тұрыңыз.")
            raise CancelHandler()

dp.middleware.setup(AntiFloodMiddleware())

# --- STATES ---
class AdminStates(StatesGroup):
    giving_coins_id = State()
    giving_coins_amount = State()
    giving_all_amount = State()
    broadcast_msg = State()
    add_video_genre = State()
    add_video_files = State()

class UserStates(StatesGroup):
    upload_genre = State()
    upload_video = State()
    select_content_genre = State()

class ChatStates(StatesGroup):
    age_verify = State()
    set_gender = State()
    searching = State()
    in_chat = State()

# --- DATABASE INIT ---
async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 10, 
            last_bonus TEXT, last_active TEXT, vip_until TEXT DEFAULT NULL,
            gender TEXT DEFAULT NULL, is_banned_until TEXT DEFAULT NULL,
            dislikes INTEGER DEFAULT 0, age_confirmed INTEGER DEFAULT 0,
            referred_by INTEGER DEFAULT NULL, referrals_count INTEGER DEFAULT 0,
            referral_earnings INTEGER DEFAULT 0, is_shadowbanned INTEGER DEFAULT 0,
            uploaded_today INTEGER DEFAULT 0, last_upload_date TEXT DEFAULT NULL,
            last_search_type TEXT DEFAULT NULL)""")
        
        columns_to_check = [
            ("referred_by", "INTEGER DEFAULT NULL"), 
            ("referrals_count", "INTEGER DEFAULT 0"), 
            ("referral_earnings", "INTEGER DEFAULT 0"),
            ("is_shadowbanned", "INTEGER DEFAULT 0"),
            ("uploaded_today", "INTEGER DEFAULT 0"),
            ("last_upload_date", "TEXT DEFAULT NULL"),
            ("last_search_type", "TEXT DEFAULT NULL")
        ]
        for col, c_type in columns_to_check:
            try: await db.execute(f"ALTER TABLE users ADD COLUMN {col} {c_type}")
            except Exception: pass

        await db.execute("""CREATE TABLE IF NOT EXISTS content(
            id INTEGER PRIMARY KEY AUTOINCREMENT, file_id TEXT, 
            file_unique_id TEXT, type TEXT, genre TEXT)""")
        
        await db.execute("""CREATE TABLE IF NOT EXISTS submissions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, file_id TEXT, 
            file_unique_id TEXT, genre TEXT, user_id INTEGER)""")
            
        await db.execute("""CREATE TABLE IF NOT EXISTS history(
            user_id INTEGER, content_id INTEGER, timestamp TEXT)""")
            
        await db.execute("""CREATE TABLE IF NOT EXISTS chat_queue(
            user_id INTEGER PRIMARY KEY, gender TEXT, looking_for TEXT, created_at TEXT)""")
            
        await db.execute("""CREATE TABLE IF NOT EXISTS active_chats(
            user_id INTEGER PRIMARY KEY, partner_id INTEGER, started_at TEXT)""")
            
        await db.execute("""CREATE TABLE IF NOT EXISTS auto_delete_messages(
            chat_id INTEGER, message_id INTEGER, delete_at TEXT)""")
            
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_content_uniq ON content(file_unique_id)")
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_subs_uniq ON submissions(file_unique_id)")
        await db.commit()

# --- HELPER FUNCTIONS ---
async def sync_user_state(uid: int, state: FSMContext):
    current_fsm = await state.get_state()
    if current_fsm is None:
        async with aiosqlite.connect(DB) as db:
            async with db.execute("SELECT partner_id FROM active_chats WHERE user_id=?", (uid,)) as cur:
                in_chat = await cur.fetchone()
            if in_chat:
                await state.set_state(ChatStates.in_chat)
                return True
            async with db.execute("SELECT user_id FROM chat_queue WHERE user_id=?", (uid,)) as cur:
                in_queue = await cur.fetchone()
            if in_queue:
                await state.set_state(ChatStates.searching)
                return True
    return False

def check_spam(text: str) -> bool:
    if not text: return False
    text = unicodedata.normalize('NFKC', text).lower()
    text = re.sub(r'[\u200b-\u200d\u2060\ufeff\s_\-\.\,\/\\\*\|]', '', text)
    
    spam_words = ["inst", "instagram", "инст", "инста", "vk", "вк", "тг", "канал", 
                  "ақшабер", "теңге", "qiwi", "киви", "каспи", "kaspi", "жазыл", 
                  "подпишись", "сатыпал", "купи", "ватсап", "whatsapp", "номерім"]
    pattern = r"(https?://|t\.me|@\w+|www\.|instagram\.com|vk\.com|\+?[78]\d{9,10})"
    
    if any(word in text for word in spam_words) or bool(re.search(pattern, text)):
        return True
    return False

async def check_sub(uid):
    if uid == ADMIN_ID: return True
    try:
        member = await bot.get_chat_member(CHANNEL_ID, uid)
        return member.status != "left"
    except Exception: return False

async def send_no_coins_msg(m: types.Message):
    await m.answer("❌ <b>Монетаңыз бітті!</b> Жалғастыру үшін:\n\n"
                   "🎁 Күнделікті бонус алыңыз\n"
                   "👥 Дос шақырыңыз (әр досыңыз үшін +5 💰)\n"
                   "💎 Монета сатып алыңыз")

# --- DYNAMIC KEYBOARDS ---
async def get_main_kb(uid):
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    now = datetime.now()
    
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT last_bonus, uploaded_today, last_upload_date FROM users WHERE id=?", (uid,)) as cur:
            user = await cur.fetchone()
            
    show_bonus = True
    if user and user[0]:
        last_b = datetime.strptime(user[0], "%Y-%m-%d %H:%M")
        if now - last_b < timedelta(hours=24):
            show_bonus = False

    show_upload = True
    if user and user[2]:
        last_u = datetime.strptime(user[2], "%Y-%m-%d")
        if last_u.date() == now.date() and user[1] >= 10:
            show_upload = False

    kb.add("🎭 Анонимді чат")
    if show_bonus:
        kb.add("🎁 Күнделікті бонус")
    kb.add("🎬 Контент")
    if show_upload:
        kb.add("➕ Видео жіберу")
        
    kb.add("💰 Баланс", "👥 Реферал")
    kb.add("💎 Монета сатып алу", "🔐 VIP контент")
    if uid == ADMIN_ID: 
        kb.add("⚙️ Админ")
    return kb

def in_chat_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("🛑 Чатты тоқтату", "🔄 Келесі адам")
    kb.add("👁 Профиль көру", "🚨 Шағым")
    return kb

def chat_menu_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("🎲 Кездейсоқ іздеу (Тегін)")
    kb.add("👩 Қыз іздеу (5 💰)", "👨 Жігіт іздеу (5 💰)")
    kb.add("🔙 Артқа")
    return kb

def genres_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(*GENRES)
    kb.add("🔙 Артқа")
    return kb

def sub_kb():
    return InlineKeyboardMarkup().add(
        InlineKeyboardButton("Тіркелу 🚀", url=CHANNEL_URL),
        InlineKeyboardButton("Тіркелдім ✅", callback_data="check_subscription")
    )

# --- GLOBAL BACK ---
@dp.message_handler(lambda m: m.text == "🔙 Артқа", state="*")
async def global_back(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM chat_queue WHERE user_id=?", (uid,))
        async with db.execute("SELECT partner_id FROM active_chats WHERE user_id=?", (uid,)) as cur:
            chat = await cur.fetchone()
        if chat:
            partner_id = chat[0]
            await db.execute("DELETE FROM active_chats WHERE user_id IN (?, ?)", (uid, partner_id))
            try:
                p_state = dp.current_state(chat=partner_id, user=partner_id)
                await p_state.finish()
                await bot.send_message(partner_id, "🛑 Серіктесіңіз чаттан шығып кетті.", reply_markup=chat_menu_kb())
            except Exception: pass
        await db.commit()
        
    await state.finish()
    kb = await get_main_kb(uid)
    await m.answer("Басты мәзір:", reply_markup=kb)

# --- START & AGE GATE ---
@dp.message_handler(commands=['start'], state="*")
async def start(m: types.Message, state: FSMContext):
    await state.finish()
    uid = m.from_user.id
    ref = m.get_args()
    
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT id, age_confirmed FROM users WHERE id=?", (uid,)) as cur: 
            user = await cur.fetchone()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        
        if not user:
            ref_id = int(ref) if (ref and ref.isdigit() and int(ref) != uid) else None
            await db.execute("INSERT INTO users(id, balance, last_bonus, last_active, referred_by) VALUES (?,10,?,?,?)", 
                             (uid, yesterday, now, ref_id))
            if ref_id:
                await db.execute("UPDATE users SET balance = balance + 5, referrals_count = referrals_count + 1, referral_earnings = referral_earnings + 5 WHERE id=?", (ref_id,))
                try: await bot.send_message(ref_id, "🔔 Жаңа реферал тіркелді! Сізге <b>+5 монета</b> берілді.")
                except Exception: pass
            await db.commit()
            user = (uid, 0)
            
    if user[1] == 0:
        await ChatStates.age_verify.set()
        kb = InlineKeyboardMarkup(row_width=2).add(
            InlineKeyboardButton("Маған 18 жыл толды ✅", callback_data="age_yes"),
            InlineKeyboardButton("Толған жоқ ❌", callback_data="age_no")
        )
        return await m.answer("🔞 <b>ЕСКЕРТУ!</b>\nБұл ботта ересектерге арналған контент және анонимді чат бар. Жасыңыз 18-ден жоғары ма?", reply_markup=kb)

    if not await check_sub(uid): 
        return await m.answer("👋 Ботты қолдану үшін каналға тіркеліңіз!", reply_markup=sub_kb())
    
    kb = await get_main_kb(uid)
    await m.answer("✅ Қайта қош келдіңіз!", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith('age_'), state=ChatStates.age_verify)
async def process_age_gate(c: types.CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    if c.data == "age_yes":
        async with aiosqlite.connect(DB) as db:
            await db.execute("UPDATE users SET age_confirmed=1 WHERE id=?", (uid,))
            await db.commit()
        await state.finish()
        await c.message.delete()
        if not await check_sub(uid): 
            await bot.send_message(uid, "👋 Каналға тіркеліңіз:", reply_markup=sub_kb())
        else:
            kb = await get_main_kb(uid)
            await bot.send_message(uid, "✅ Сәтті расталды!", reply_markup=kb)
    else:
        await c.answer("❌ 18 жасқа толмағандарға кіруге тыйым салынады!", show_alert=True)

@dp.callback_query_handler(lambda c: c.data == "check_subscription", state="*")
async def check_sub_cb(c: types.CallbackQuery):
    if await check_sub(c.from_user.id):
        await c.message.delete()
        kb = await get_main_kb(c.from_user.id)
        await bot.send_message(c.from_user.id, "✅ Рахмет, кіру рұқсат!", reply_markup=kb)
    else: 
        await c.answer("❌ Каналға әлі тіркелмедіңіз!", show_alert=True)

# --- BALANCE & REFS ---
@dp.message_handler(lambda m: m.text == "💰 Баланс", state="*")
async def view_balance(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    if await sync_user_state(uid, state) and await state.get_state() == ChatStates.in_chat.state: return
    
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT balance, vip_until FROM users WHERE id=?", (uid,)) as cur: 
            row = await cur.fetchone()
    
    vip_status = f"⏳ {row[1]} дейін" if (row[1] and datetime.strptime(row[1], "%Y-%m-%d %H:%M") > datetime.now()) else "❌ Жоқ"
    await m.answer(f"💰 <b>Сіздің балансыңыз:</b> {row[0]} монета\n👑 <b>VIP Статус:</b> {vip_status}")

@dp.message_handler(lambda m: m.text == "👥 Реферал", state="*")
async def view_refs(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    if await sync_user_state(uid, state) and await state.get_state() == ChatStates.in_chat.state: return
    
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT referrals_count, referral_earnings FROM users WHERE id=?", (uid,)) as cur: 
            row = await cur.fetchone()
            
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={uid}"
    
    await m.answer(f"👥 <b>Серіктестік бағдарламасы</b>\n\n"
                   f"🔗 Сіздің реферал сілтемеңіз:\n<code>{ref_link}</code>\n\n"
                   f"📊 Статистика:\n"
                   f"- Шақырылды: <b>{row[0]} адам</b>\n"
                   f"- Табыс: <b>{row[1]} монета</b>\n\n"
                   f"🎁 Әр шақырылған белсенді адам үшін бірден <b>5 монета</b> аласыз!")

@dp.message_handler(lambda m: m.text == "💎 Монета сатып алу", state="*")
async def buy_coins(m: types.Message, state: FSMContext):
    if await sync_user_state(m.from_user.id, state) and await state.get_state() == ChatStates.in_chat.state: return
    kb = InlineKeyboardMarkup(row_width=1).add(
        InlineKeyboardButton("💳 Kaspi / Qiwi / Басқа", callback_data="pay_other")
    )
    await m.answer("💎 <b>Монета сатып алу</b>\nТөлем жасап, админге чек жіберіңіз.", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith('pay_'), state="*")
async def pay_mock(c: types.CallbackQuery):
    await c.message.answer("🛠 <b>Төлем:</b>\nБас админге жазыңыз: @QZQADMIN")
    await c.answer()

# --- DAILY BONUS ---
@dp.message_handler(lambda m: m.text == "🎁 Күнделікті бонус", state="*")
async def daily_bonus(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    if await sync_user_state(uid, state) and await state.get_state() == ChatStates.in_chat.state: return
    
    now = datetime.now()
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT last_bonus FROM users WHERE id=?", (uid,)) as cur: 
            user = await cur.fetchone()
        
        last_dt = datetime.strptime(user[0], "%Y-%m-%d %H:%M") if (user and user[0]) else now - timedelta(days=1)
        if now - last_dt >= timedelta(hours=24):
            await db.execute("UPDATE users SET balance = balance + 15, last_bonus = ? WHERE id=?", (now.strftime("%Y-%m-%d %H:%M"), uid))
            await db.commit()
            kb = await get_main_kb(uid)
            await m.answer("🎉 <b>+15 тегін монета</b> балансыңызға қосылды!\n⏳ Келесі бонус 24 сағаттан кейін.", reply_markup=kb)
        else:
            await m.answer("⏳ Бонус уақыты әлі келмеді.")

# --- ANONYMOUS CHAT ---
@dp.message_handler(lambda m: m.text == "🎭 Анонимді чат", state="*")
async def chat_entry(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    if await sync_user_state(uid, state) and await state.get_state() == ChatStates.in_chat.state: return
    
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT gender, is_banned_until FROM users WHERE id=?", (uid,)) as cur: 
            user = await cur.fetchone()
            
    if user and user[1] and datetime.now() < datetime.strptime(user[1], "%Y-%m-%d %H:%M"):
        return await m.answer(f"🚫 Сіз бұғатталғансыз. Мерзімі: {user[1]}")
            
    if not user or not user[0]:
        await ChatStates.set_gender.set()
        kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2).add("👨 Жігітпін", "👩 Қызбын", "🔙 Артқа")
        return await m.answer("Өз жынысыңызды таңдаңыз:", reply_markup=kb)
        
    await m.answer("🎭 Серіктес іздеу мәзірі:", reply_markup=chat_menu_kb())

@dp.message_handler(state=ChatStates.set_gender)
async def process_gender(m: types.Message, state: FSMContext):
    if m.text not in ["👨 Жігітпін", "👩 Қызбын"]: return await m.answer("Төмендегі батырманы басыңыз!")
    
    gender = "male" if m.text == "👨 Жігітпін" else "female"
    async with aiosqlite.connect(DB) as db:
        # ТҮЗЕТУ ОСЫ ЖЕРДЕ: Параметрлердің орны ауысып кеткен еді (gender, uid) 
        await db.execute("UPDATE users SET gender=? WHERE id=?", (gender, m.from_user.id))
        await db.commit()
    await state.finish()
    await m.answer("✅ Сақталды!", reply_markup=chat_menu_kb())

@dp.message_handler(lambda m: m.text in ["🎲 Кездейсоқ іздеу (Тегін)", "👩 Қыз іздеу (5 💰)", "👨 Жігіт іздеу (5 💰)"], state="*")
async def start_matchmaking(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    if await sync_user_state(uid, state) and await state.get_state() == ChatStates.in_chat.state: return
    
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT balance, gender FROM users WHERE id=?", (uid,)) as cur: 
            user = await cur.fetchone()
            
    if not user or not user[1]: return await chat_entry(m, state)
    
    balance, my_gender = user[0], user[1]
    price = 0 if "Кездейсоқ" in m.text else 5
    if balance < price:
        return await send_no_coins_msg(m)
    
    looking_for = "female" if "Қыз" in m.text else ("male" if "Жігіт" in m.text else "random")
    
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE users SET last_search_type=? WHERE id=?", (m.text, uid))
        await db.execute("DELETE FROM chat_queue WHERE user_id=?", (uid,))
        
        query = "SELECT user_id FROM chat_queue WHERE user_id != ?"
        params = [uid]
        if looking_for != "random":
            query += " AND gender = ? AND (looking_for = ? OR looking_for = 'random')"
            params.extend([looking_for, my_gender])
        else:
            query += " AND (looking_for = 'random' OR looking_for = ?)"
            params.append(my_gender)
        query += " ORDER BY created_at ASC LIMIT 1"
        
        async with db.execute(query, tuple(params)) as cur: partner = await cur.fetchone()
        
        if partner:
            partner_id = partner[0]
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            await db.execute("DELETE FROM chat_queue WHERE user_id IN (?, ?)", (uid, partner_id))
            await db.execute("INSERT OR REPLACE INTO active_chats VALUES (?, ?, ?), (?, ?, ?)", (uid, partner_id, now_str, partner_id, uid, now_str))
            if price > 0: await db.execute("UPDATE users SET balance = balance - ? WHERE id=?", (price, uid))
            await db.commit()
            
            await state.set_state(ChatStates.in_chat)
            await m.answer("✅ <b>Сұхбаттасушы табылды!</b>", reply_markup=in_chat_kb())
            
            p_state = dp.current_state(chat=partner_id, user=partner_id)
            await p_state.set_state(ChatStates.in_chat)
            try: await bot.send_message(partner_id, "✅ <b>Сұхбаттасушы табылды!</b>", reply_markup=in_chat_kb())
            except Exception: pass
        else:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            await db.execute("INSERT INTO chat_queue VALUES (?, ?, ?, ?)", (uid, my_gender, looking_for, now_str))
            if price > 0: await db.execute("UPDATE users SET balance = balance - ? WHERE id=?", (price, uid))
            await db.commit()
            await ChatStates.searching.set()
            await m.answer("🔍 Серіктес ізделуде...", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 Артқа"))

# --- IN CHAT ACTIONS & MODERATION ---
@dp.message_handler(state=ChatStates.in_chat, content_types=['text', 'photo', 'video', 'voice'])
async def handle_chat_messages(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT partner_id FROM active_chats WHERE user_id=?", (uid,)) as cur: chat = await cur.fetchone()
        async with db.execute("SELECT is_shadowbanned, balance, vip_until, last_search_type FROM users WHERE id=?", (uid,)) as cur: u_data = await cur.fetchone()
        
    if not chat:
        await state.finish()
        return await m.answer("Чат жабылған.", reply_markup=chat_menu_kb())
        
    partner_id, is_sb, balance, vip_until, last_search = chat[0], u_data[0], u_data[1], u_data[2], u_data[3]
    
    # 1. 🛑 STOP
    if m.text == "🛑 Чатты тоқтату":
        async with aiosqlite.connect(DB) as db:
            await db.execute("DELETE FROM active_chats WHERE user_id IN (?, ?)", (uid, partner_id))
            await db.commit()
        await state.finish()
        
        kb_r = InlineKeyboardMarkup().add(InlineKeyboardButton("👍", callback_data=f"rt_gd_{partner_id}"), InlineKeyboardButton("👎 Спам", callback_data=f"rt_bd_{partner_id}"))
        await m.answer("🛑 Чат аяқталды.", reply_markup=chat_menu_kb())
        await m.answer("Серіктесті бағалаңыз:", reply_markup=kb_r)
        
        p_state = dp.current_state(chat=partner_id, user=partner_id)
        await p_state.finish()
        try:
            await bot.send_message(partner_id, "🛑 Серіктесіңіз чатты аяқтады.", reply_markup=chat_menu_kb())
            await bot.send_message(partner_id, "Бағалаңыз:", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("👍", callback_data=f"rt_gd_{uid}"), InlineKeyboardButton("👎", callback_data=f"rt_bd_{uid}")))
        except Exception: pass
        return

    # 2. 🔄 NEXT
    if m.text == "🔄 Келесі адам":
        async with aiosqlite.connect(DB) as db:
            await db.execute("DELETE FROM active_chats WHERE user_id IN (?, ?)", (uid, partner_id))
            await db.commit()
        await state.finish()
        try:
            p_state = dp.current_state(chat=partner_id, user=partner_id)
            await p_state.finish()
            await bot.send_message(partner_id, "🛑 Серіктесіңіз чаттан шықты.", reply_markup=chat_menu_kb())
        except Exception: pass
        
        m.text = last_search if last_search else "🎲 Кездейсоқ іздеу (Тегін)"
        return await start_matchmaking(m, state)

    # 3. 👁 VIEW PROFILE
    if m.text == "👁 Профиль көру":
        is_vip = vip_until and datetime.strptime(vip_until, "%Y-%m-%d %H:%M") > datetime.now()
        cost = 25 if is_vip else 50
        
        if balance < cost:
            return await m.answer(f"❌ Профильді ашу құны — {cost} 💰.\nМонета жеткіліксіз!")
            
        async with aiosqlite.connect(DB) as db:
            await db.execute("UPDATE users SET balance = balance - ? WHERE id=?", (cost, uid))
            await db.commit()
            
        try:
            p_chat = await bot.get_chat(partner_id)
            if p_chat.username:
                await m.answer(f"✅ Профиль: t.me/{p_chat.username}")
            else:
                await m.answer("Пайдаланушы аккаунтын жасырып қойған.")
        except Exception:
            await m.answer("Пайдаланушы аккаунтын жасырып қойған.")
        return

    # 4. 🚨 REPORT
    if m.text == "🚨 Шағым":
        async with aiosqlite.connect(DB) as db:
            await db.execute("DELETE FROM active_chats WHERE user_id IN (?, ?)", (uid, partner_id))
            await db.execute("UPDATE users SET dislikes = dislikes + 2 WHERE id=?", (partner_id,))
            async with db.execute("SELECT dislikes FROM users WHERE id=?", (partner_id,)) as c: r = await c.fetchone()
            if r and r[0] >= 3:
                b_time = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
                await db.execute("UPDATE users SET is_banned_until=?, dislikes=0 WHERE id=?", (b_time, partner_id))
            await db.commit()
            
        await state.finish()
        p_state = dp.current_state(chat=partner_id, user=partner_id)
        await p_state.finish()
        await m.answer("🚨 Шағым қабылданды, чат жабылды.", reply_markup=chat_menu_kb())
        try: await bot.send_message(partner_id, "🛑 Шағым түсті, чат жабылды.", reply_markup=chat_menu_kb())
        except Exception: pass
        return

    # ANTI-SPAM
    msg_content = m.text or m.caption or ""
    if check_spam(msg_content):
        async with aiosqlite.connect(DB) as db:
            b_time = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
            await db.execute("UPDATE users SET is_banned_until=?, is_shadowbanned=1 WHERE id=?", (b_time, uid))
            await db.execute("DELETE FROM active_chats WHERE user_id IN (?, ?)", (uid, partner_id))
            await db.commit()
        await state.finish()
        await m.answer("🚫 Реклама немесе спам анықталды! 3 күнге бан алдыңыз.", reply_markup=ReplyKeyboardRemove())
        try:
            p_state = dp.current_state(chat=partner_id, user=partner_id)
            await p_state.finish()
            await bot.send_message(partner_id, "🛑 Серіктес спам үшін бұғатталды.", reply_markup=chat_menu_kb())
        except Exception: pass
        return

    if is_sb: return

    # MEDIA CHECK
    if m.content_type in ['photo', 'video']:
        is_vip = vip_until and datetime.strptime(vip_until, "%Y-%m-%d %H:%M") > datetime.now()
        if not is_vip:
            return await m.answer("🔒 Медиа файлдар жіберу тек <b>VIP</b> қолданушыларға рұқсат етілген!")

    try:
        if m.content_type == 'photo': await bot.send_photo(partner_id, m.photo[-1].file_id, caption=m.caption, has_spoiler=True)
        elif m.content_type == 'video': await bot.send_video(partner_id, m.video.file_id, caption=m.caption, has_spoiler=True)
        else: await m.copy_to(partner_id)
    except TelegramAPIError as e:
        if "blocked" in str(e).lower() or "deactivated" in str(e).lower():
            await state.finish()
            async with aiosqlite.connect(DB) as db:
                await db.execute("DELETE FROM active_chats WHERE user_id IN (?, ?)", (uid, partner_id))
                await db.commit()
            await m.answer("⚠️ Серіктес ботты өшіріп тастады.", reply_markup=chat_menu_kb())

# --- CHAT RATINGS ---
@dp.callback_query_handler(lambda c: c.data.startswith('rt_'), state="*")
async def chat_rating_cb(c: types.CallbackQuery):
    action, target_id = c.data.split('_')[1], int(c.data.split('_')[2])
    if action == "bd":
        async with aiosqlite.connect(DB) as db:
            await db.execute("UPDATE users SET dislikes = dislikes + 1 WHERE id=?", (target_id,))
            async with db.execute("SELECT dislikes FROM users WHERE id=?", (target_id,)) as cur: r = await cur.fetchone()
            if r and r[0] >= 3:
                b_time = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
                await db.execute("UPDATE users SET is_banned_until=?, dislikes=0 WHERE id=?", (b_time, target_id))
            await db.commit()
    await c.message.edit_text("✅ Баға сақталды!")
    await c.answer()

# --- CONTENT (VIDEO) ---
@dp.message_handler(lambda m: m.text in ["🎬 Контент", "😈 VIP Видео"], state="*")
async def content_menu(m: types.Message, state: FSMContext):
    if await sync_user_state(m.from_user.id, state) and await state.get_state() == ChatStates.in_chat.state: return
    if "VIP" in m.text: return await get_video_logic(m, "😈 VIP Видео", state)
    await UserStates.select_content_genre.set()
    await m.answer("Жанр таңдаңыз:", reply_markup=genres_kb())

@dp.message_handler(lambda m: m.text in GENRES, state=UserStates.select_content_genre)
async def process_content_genre(m: types.Message, state: FSMContext):
    await get_video_logic(m, m.text, state)

async def get_video_logic(m: types.Message, genre: str, state: FSMContext):
    uid = m.from_user.id
    config = GENRES_CONFIG[genre]
    
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT balance, vip_until FROM users WHERE id=?", (uid,)) as cur: user = await cur.fetchone()
        if "VIP" in genre and (not user[1] or datetime.strptime(user[1], "%Y-%m-%d %H:%M") < datetime.now()):
            return await m.answer("❌ Сізде VIP жазылым белсенді емес!")

        if user[0] < config['price']: return await send_no_coins_msg(m)

        async with db.execute("SELECT id, file_id FROM content WHERE genre=? AND id NOT IN (SELECT content_id FROM history WHERE user_id=?) ORDER BY RANDOM() LIMIT 1", (genre, uid)) as cur: v = await cur.fetchone()
        if not v:
            await db.execute("DELETE FROM history WHERE user_id=?", (uid,))
            async with db.execute("SELECT id, file_id FROM content WHERE genre=? ORDER BY RANDOM() LIMIT 1", (genre,)) as cur: v = await cur.fetchone()

        if v:
            del_t = (datetime.now() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M")
            await db.execute("UPDATE users SET balance = balance - ? WHERE id=?", (config['price'], uid))
            await db.execute("INSERT INTO history VALUES (?,?,?)", (uid, v[0], datetime.now().strftime("%Y-%m-%d %H:%M")))
            await db.commit()
            
            kb = InlineKeyboardMarkup().add(InlineKeyboardButton("Келесі ⏭", callback_data=f"nxtvd_{genre}"))
            sent = await bot.send_video(uid, v[1], caption=f"💰 Құны: {config['price']} 💰\n⚠️ 30 минуттан соң өшеді.", reply_markup=kb)
            await db.execute("INSERT INTO auto_delete_messages VALUES (?, ?, ?)", (uid, sent.message_id, del_t))
            await db.commit()
        else: await m.answer("Бұл бөлімде видео табылған жоқ.")

@dp.callback_query_handler(lambda c: c.data.startswith('nxtvd_'), state="*")
async def nxt_vd_cb(c: types.CallbackQuery, state: FSMContext):
    genre = c.data.split('_')[1]
    c.message.text = genre
    try: await c.message.delete()
    except Exception: pass
    await get_video_logic(c.message, genre, state)
    await c.answer()

# --- VIP ---
@dp.message_handler(lambda m: m.text == "🔐 VIP контент", state="*")
async def vip_access(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    if await sync_user_state(uid, state) and await state.get_state() == ChatStates.in_chat.state: return
    
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT balance, vip_until FROM users WHERE id=?", (uid,)) as cur: u = await cur.fetchone()
    
    if u[1] and datetime.strptime(u[1], "%Y-%m-%d %H:%M") > datetime.now():
        await m.answer(f"👑 VIP Мерзімі: {u[1]} дейін.", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("😈 VIP Видео", "🔙 Артқа"))
    else:
        await m.answer("🔐 <b>VIP (50 💰 / 24 сағат)</b>\n- Чатта медиа жіберу\n- VIP видео көру\n- Профильді 25 монетаға ашу", 
                       reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("Сатып алу (50 💰)", callback_data="buy_vip")))

@dp.callback_query_handler(lambda c: c.data == "buy_vip", state="*")
async def buy_vip_cb(c: types.CallbackQuery):
    uid = c.from_user.id
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT balance FROM users WHERE id=?", (uid,)) as cur: u = await cur.fetchone()
        if u[0] < 50: return await c.answer("❌ Қаражат жеткіліксіз!", show_alert=True)
        vt = (datetime.now() + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
        await db.execute("UPDATE users SET balance = balance - 50, vip_until = ? WHERE id=?", (vt, uid))
        await db.commit()
    await c.message.delete()
    kb = await get_main_kb(uid)
    await bot.send_message(uid, "👑 VIP сәтті қосылды!", reply_markup=kb)
    await c.answer()

# --- UPLOAD VIDEO ---
@dp.message_handler(lambda m: m.text == "➕ Видео жіберу", state="*")
async def up_start(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    if await sync_user_state(uid, state) and await state.get_state() == ChatStates.in_chat.state: return
    
    now = datetime.now()
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT uploaded_today, last_upload_date FROM users WHERE id=?", (uid,)) as cur: u = await cur.fetchone()
        
    today_str = now.strftime("%Y-%m-%d")
    uploaded = u[0] if (u and u[1] and u[1].startswith(today_str)) else 0
    
    if uploaded >= 10:
        return await m.answer("⏳ Бүгінгі лимит бітті (10/10). Келесі видео жіберу 24 сағаттан соң ашылады.")

    await UserStates.upload_genre.set()
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(*[g for g in GENRES if "VIP" not in g]).add("🔙 Артқа")
    await m.answer(f"Қай категорияға? (Бүгін қалды: {10 - uploaded})", reply_markup=kb)

@dp.message_handler(state=UserStates.upload_genre)
async def up_genre(m: types.Message, state: FSMContext):
    if m.text not in GENRES: return await m.answer("Мәзірден жарамды жанр таңдаңыз!")
    await state.update_data(g=m.text, added=0)
    await UserStates.upload_video.set()
    await m.answer("🎥 Видео жіберіңіз. Аяқтау үшін ✅ Аяқтау", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("✅ Аяқтау"))

@dp.message_handler(state=UserStates.upload_video, content_types=['video'])
async def up_file(m: types.Message, state: FSMContext):
    data = await state.get_data()
    uid = m.from_user.id
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT uploaded_today, last_upload_date FROM users WHERE id=?", (uid,)) as cur: u = await cur.fetchone()
        uploaded = u[0] if (u and u[1] and u[1].startswith(today_str)) else 0
        
        if uploaded >= 10:
            await state.finish()
            kb = await get_main_kb(uid)
            return await m.answer("⏳ Лимит аяқталды!", reply_markup=kb)
            
        async with db.execute("SELECT id FROM content WHERE file_unique_id=?", (m.video.file_unique_id,)) as cur: e1 = await cur.fetchone()
        async with db.execute("SELECT id FROM submissions WHERE file_unique_id=?", (m.video.file_unique_id,)) as cur: e2 = await cur.fetchone()
        if not e1 and not e2:
            await db.execute("INSERT INTO submissions(file_id, file_unique_id, genre, user_id) VALUES (?,?,?,?)", (m.video.file_id, m.video.file_unique_id, data['g'], uid))
            await db.execute("UPDATE users SET uploaded_today = ?, last_upload_date = ? WHERE id=?", (uploaded + 1, today_str, uid))
            await db.commit()
            await state.update_data(added=data.get('added', 0) + 1)
            await m.answer(f"Жүктелді. Тағы жіберіңіз немесе ✅ Аяқтау ({uploaded+1}/10)")

@dp.message_handler(lambda m: m.text == "✅ Аяқтау", state=[AdminStates.add_video_files, UserStates.upload_video])
async def fin_up(m: types.Message, state: FSMContext):
    data = await state.get_data()
    st = await state.get_state()
    kb = await get_main_kb(m.from_user.id)
    if st == AdminStates.add_video_files.state:
        await m.answer(f"👑 Админ: {data.get('added',0)} қосылды.", reply_markup=kb)
    else:
        await m.answer(f"📊 Модерацияға: {data.get('added', 0)} жіберілді. Әр видеоға +10 💰.", reply_markup=kb)
    await state.finish()

# ================= ADMIN PANEL =================
@dp.message_handler(lambda m: m.text == "⚙️ Админ", state="*")
async def adm_panel(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2).add("➕ Видео қосу", "📩 Жіберілгендер", "💰 Монета берегу", "🌍 Барлығына монета", "📢 Рассылка", "📊 Статистика", "🔙 Артқа")
    await m.answer("👑 Админ Панель:", reply_markup=kb)

@dp.message_handler(lambda m: m.text == "➕ Видео қосу", state="*")
async def adm_add_v(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    await AdminStates.add_video_genre.set()
    await m.answer("Категория:", reply_markup=genres_kb())

@dp.message_handler(state=AdminStates.add_video_genre)
async def adm_add_vg(m: types.Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    await state.update_data(g=m.text, added=0)
    await AdminStates.add_video_files.set()
    await m.answer(f"📥 {m.text} видеоларын жіберіңіз. ✅ Аяқтау", reply_markup=ReplyKeyboardMarkup().add("✅ Аяқтау"))

@dp.message_handler(state=AdminStates.add_video_files, content_types=['video'])
async def adm_add_vf(m: types.Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR IGNORE INTO content(file_id, file_unique_id, type, genre) VALUES (?,?,'video',?)", (m.video.file_id, m.video.file_unique_id, data['g']))
        await db.commit()
    await state.update_data(added=data.get('added', 0) + 1)

@dp.message_handler(lambda m: m.text == "📩 Жіберілгендер", state="*")
async def adm_rev(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB) as db:
        # ТҮЗЕТУ: Барлық видеоларды бірден алып шығу
        async with db.execute("SELECT id, file_id, genre, user_id FROM submissions") as cur: 
            rows = await cur.fetchall()
            
    if not rows: return await m.answer("Кезек бос.")
    
    await m.answer(f"Барлығы {len(rows)} видео табылды. Жіберілуде...")
    
    for row in rows:
        sub_id, file_id, genre, user_id = row
        kb = InlineKeyboardMarkup().add(
            InlineKeyboardButton("✅ Қабылдау", callback_data=f"sb_ok_{sub_id}"), 
            InlineKeyboardButton("❌ Қабылдамау", callback_data=f"sb_no_{sub_id}")
        )
        try:
            await bot.send_video(ADMIN_ID, file_id, caption=f"ID: {user_id} | Жанр: {genre}", reply_markup=kb)
            await asyncio.sleep(0.05) # Лимитке ұрынбау үшін аздап кідіріс
        except Exception:
            pass

@dp.callback_query_handler(lambda c: c.data.startswith('sb_'), state="*")
async def adm_dec(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    
    parts = c.data.split('_')
    action = parts[1] # ok немесе no
    sub_id = int(parts[2])
    
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT file_id, genre, user_id, file_unique_id FROM submissions WHERE id=?", (sub_id,)) as cur:
            row = await cur.fetchone()
            
        if not row:
            return await c.message.delete()
            
        file_id, genre, user_id, file_unique_id = row
        
        if action == "ok":
            await db.execute("INSERT OR IGNORE INTO content(file_id, file_unique_id, type, genre) VALUES (?,?,'video',?)", (file_id, file_unique_id, genre))
            await db.execute("UPDATE users SET balance = balance + 10 WHERE id=?", (user_id,))
            await db.execute("DELETE FROM submissions WHERE id=?", (sub_id,))
            await db.commit()
            
            try: await bot.send_message(user_id, f"🎉 Сіздің '{genre}' жанрындағы видеоңыз бекітілді! +10 💰")
            except Exception: pass
            
            await c.message.edit_caption(caption=f"{c.message.caption}\n\n✅ Қабылданды", reply_markup=None)
            
        elif action == "no":
            await db.execute("DELETE FROM submissions WHERE id=?", (sub_id,))
            await db.commit()
            await c.message.edit_caption(caption=f"{c.message.caption}\n\n❌ Қабылданбады", reply_markup=None)

@dp.message_handler(lambda m: m.text == "💰 Монета берегу", state="*")
async def adm_gc_id(m: types.Message):
    if m.from_user.id == ADMIN_ID: 
        await AdminStates.giving_coins_id.set()
        await m.answer("ID жазыңыз:", reply_markup=ReplyKeyboardMarkup().add("🔙 Артқа"))

@dp.message_handler(state=AdminStates.giving_coins_id)
async def adm_gc_id2(m: types.Message, state: FSMContext):
    if not m.text.isdigit(): return
    await state.update_data(tid=int(m.text))
    await AdminStates.giving_coins_amount.set()
    await m.answer("Сомма:")

@dp.message_handler(state=AdminStates.giving_coins_amount)
async def adm_gc_am(m: types.Message, state: FSMContext):
    d = await state.get_data()
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE id=?", (int(m.text), d['tid']))
        await db.commit()
    await state.finish()
    kb = await get_main_kb(ADMIN_ID)
    await m.answer("✅", reply_markup=kb)

@dp.message_handler(lambda m: m.text == "🌍 Барлығына монета", state="*")
async def adm_ga(m: types.Message):
    if m.from_user.id == ADMIN_ID:
        await AdminStates.giving_all_amount.set()
        await m.answer("Қанша?", reply_markup=ReplyKeyboardMarkup().add("🔙 Артқа"))

@dp.message_handler(state=AdminStates.giving_all_amount)
async def adm_ga_pr(m: types.Message, state: FSMContext):
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE users SET balance = balance + ?", (int(m.text),))
        await db.commit()
    await state.finish()
    kb = await get_main_kb(ADMIN_ID)
    await m.answer("✅ Таратылды", reply_markup=kb)

@dp.message_handler(lambda m: m.text == "📊 Статистика", state="*")
async def stat_v(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur: uc = await cur.fetchone()
        async with db.execute("SELECT genre, COUNT(*) FROM content GROUP BY genre") as cur: vc = await cur.fetchall()
    res = f"👥 Қолданушылар: {uc[0]}\n🎬 Видеолар:\n" + "".join([f"- {v[0]}: {v[1]}\n" for v in vc])
    await m.answer(res)

@dp.message_handler(lambda m: m.text == "📢 Рассылка", state="*")
async def br_st(m: types.Message):
    if m.from_user.id == ADMIN_ID:
        await AdminStates.broadcast_msg.set()
        await m.answer("Мәтін/файл жіберіңіз:", reply_markup=ReplyKeyboardMarkup().add("🔙 Артқа"))

@dp.message_handler(state=AdminStates.broadcast_msg, content_types=['any'])
async def br_pr(m: types.Message, state: FSMContext):
    if m.text == "🔙 Артқа": return await global_back(m, state)
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT id FROM users") as cur: usrs = await cur.fetchall()
    await m.answer("📢 Басталды...")
    c = 0
    for u in usrs:
        try: await m.copy_to(u[0]); c += 1; await asyncio.sleep(0.04)
        except Exception: pass
    await state.finish()
    kb = await get_main_kb(ADMIN_ID)
    await m.answer(f"✅ {c} адамға жетті.", reply_markup=kb)

# --- CLEANER LOOP ---
async def cleaner_loop():
    while True:
        await asyncio.sleep(30)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        try:
            async with aiosqlite.connect(DB) as db:
                async with db.execute("SELECT chat_id, message_id FROM auto_delete_messages WHERE delete_at <= ?", (now_str,)) as cur:
                    async for row in cur:
                        try: await bot.delete_message(row[0], row[1])
                        except Exception: pass
                await db.execute("DELETE FROM auto_delete_messages WHERE delete_at <= ?", (now_str,))
                qt = (datetime.now() - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M")
                async with db.execute("SELECT user_id FROM chat_queue WHERE created_at <= ?", (qt,)) as cur:
                    async for r in cur:
                        try: await bot.send_message(r[0], "⏳ Іздеу уақыты аяқталды.", reply_markup=chat_menu_kb())
                        except Exception: pass
                await db.execute("DELETE FROM chat_queue WHERE created_at <= ?", (qt,))
                await db.commit()
        except Exception: pass

# --- UX CLEANER ---
@dp.message_handler(content_types=['text'], state="*")
async def clean_chat_ux(m: types.Message, state: FSMContext):
    if await sync_user_state(m.from_user.id, state): return await handle_chat_messages(m, state)
    if await state.get_state() is not None: return
    try: await m.delete()
    except Exception: pass

# --- START ---
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())
    loop.create_task(cleaner_loop())
    executor.start_polling(dp, skip_updates=False)
