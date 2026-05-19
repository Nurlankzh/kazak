import asyncio
import logging
import aiosqlite
import re
import unicodedata
import os
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher.handler import CancelHandler
from aiogram.dispatcher.middlewares import BaseMiddleware
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.exceptions import Throttled, TelegramAPIError, BotBlocked, UserDeactivated, RetryAfter

# --- CONFIG ---
API_TOKEN = os.getenv("BOT_TOKEN", "6851505012:AAHA88fc7S7FH7AfbDx1h_layrzV6OjMbxI")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6303091468"))
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/QZQCONTENT")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@QZQCONTENT")
BOT_USER = os.getenv("BOT_USER", "@adeptiemesbot")
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
    review_submissions = State()

class UserStates(StatesGroup):
    upload_genre = State()
    upload_video = State()

class ChatStates(StatesGroup):
    age_verify = State()
    set_gender = State()
    searching = State()
    in_chat = State()

# --- DATABASE INIT & AUTO-MIGRATION ---
async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 10, 
            last_bonus TEXT, last_active TEXT, vip_until TEXT DEFAULT NULL,
            gender TEXT DEFAULT NULL, is_banned_until TEXT DEFAULT NULL,
            dislikes INTEGER DEFAULT 0, age_confirmed INTEGER DEFAULT 0,
            referred_by INTEGER DEFAULT NULL, referrals_count INTEGER DEFAULT 0,
            referral_earnings INTEGER DEFAULT 0, is_shadowbanned INTEGER DEFAULT 0)""")
        
        columns_to_check = [
            ("referred_by", "INTEGER DEFAULT NULL"), 
            ("referrals_count", "INTEGER DEFAULT 0"), 
            ("referral_earnings", "INTEGER DEFAULT 0"),
            ("is_shadowbanned", "INTEGER DEFAULT 0")
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
        await db.execute("CREATE INDEX IF NOT EXISTS idx_queue_look ON chat_queue(gender, looking_for)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_autodel ON auto_delete_messages(delete_at)")
        await db.commit()

# --- RESTART RECOVERY SYSTEM ---
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

# --- 🤖 AUTO ANTI SCAM & AI MODERATION MATRIX ---
def check_scam_keywords(text: str) -> bool:
    if not text: return False
    text = unicodedata.normalize('NFKC', text).lower()
    text = text.replace("․", ".").replace("о", "o").replace("а", "a").replace("е", "e").replace("х", "x").replace("с", "c")
    text = re.sub(r'[\u200b-\u200d\u2060\ufeff\s_\-\.\,\/\\\*\|]', '', text)
    
    scam_catalog = [
        "inst", "instagram", "инст", "инста", "vk", "вк", "тгканал", "телеграмканал", 
        "ақшабер", "акшабер", "теңге", "тенге", "qiwi", "киви", "каспи", "kaspi", 
        "жазыл", "подпишись", "сатыпал", "купи", "дон", "donat", "ақшасал", "акшасал",
        "ватсап", "whatsapp", "watsap", "номерим", "номерім", "жүкте", "скачай"
    ]
    return any(word in text for word in scam_catalog)

def normalize_and_check_spam(text: str) -> bool:
    if not text: return False
    text = unicodedata.normalize('NFKC', text)
    text = text.replace("․", ".").replace("о", "o").replace("а", "a").replace("е", "e")
    text = re.sub(r'[\u200b-\u200d\u2060\ufeff\s_-]', '', text).lower()
    
    pattern = r"(https?://|t\.me|@\w+|www\.|instagram\.com|vk\.com|\+?[78]\d{9,10})"
    return bool(re.search(pattern, text))

async def check_sub(uid):
    if uid == ADMIN_ID: return True
    try:
        member = await bot.get_chat_member(CHANNEL_ID, uid)
        return member.status != "left"
    except Exception: return False

# --- KEYBOARDS ---
def sub_kb():
    return InlineKeyboardMarkup(row_width=1).add(
        InlineKeyboardButton("Тіркелу 🚀", url=CHANNEL_URL),
        InlineKeyboardButton("Тіркелдім ✅", callback_data="check_subscription")
    )

def main_kb(uid):
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("🎭 Анонимді чат", "🎁 Күнделікті бонус")
    kb.add("🎬 Контент", "➕ Видео жіберу")
    kb.add("💰 Баланс", "👥 Реферал")
    kb.add("💎 Монета сатып алу", "🔐 VIP контент")
    if uid == ADMIN_ID: kb.add("⚙️ Админ")
    return kb

def chat_menu_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True, row_width=2).add(
        "🎲 Кездейсоқ іздеу (Тегін)", "👩 Қыз іздеу (5 💰)", "👨 Жігіт іздеу (5 💰)", "🔙 Артқа"
    )

# --- GLOBAL BACK BUTTON ---
@dp.message_handler(lambda m: m.text == "🔙 Артқа", state="*")
async def global_back(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM chat_queue WHERE user_id=?", (uid,))
        await db.commit()
    await state.finish()
    await m.answer("Басты мәзір:", reply_markup=main_kb(uid))

# --- START & 18+ AGE GATE ---
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

    if not await check_sub(uid): return await m.answer("👋 Ботты қолдану үшін каналға тіркеліңіз!", reply_markup=sub_kb())
    await m.answer("✅ Қайта қош келдіңіз!", reply_markup=main_kb(uid))

@dp.callback_query_handler(lambda c: c.data.startswith('age_'), state=ChatStates.age_verify)
async def process_age_gate(c: types.CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    if c.data == "age_yes":
        async with aiosqlite.connect(DB) as db:
            await db.execute("UPDATE users SET age_confirmed=1 WHERE id=?", (uid,))
            await db.commit()
        await state.finish()
        await c.message.delete()
        if not await check_sub(uid): await bot.send_message(uid, "👋 Каналға тіркеліңіз:", reply_markup=sub_kb())
        else: await bot.send_message(uid, "✅ Сәтті расталды!", reply_markup=main_kb(uid))
    else:
        await c.answer("❌ 18 жасқа толмағандарға кіруге тыйым салынады!", show_alert=True)

@dp.callback_query_handler(lambda c: c.data == "check_subscription", state="*")
async def check_subscription_callback(c: types.CallbackQuery):
    if await check_sub(c.from_user.id):
        await c.message.delete()
        await bot.send_message(c.from_user.id, "✅ Рахмет, кіру рұқсат!", reply_markup=main_kb(c.from_user.id))
    else: await c.answer("❌ Каналға әлі тіркелмедіңіз!", show_alert=True)

# --- БАЛАНС ЖҮЙЕСІ ---
@dp.message_handler(lambda m: m.text == "💰 Баланс", state="*")
async def view_balance(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    await sync_user_state(uid, state)
    if await state.get_state() == ChatStates.in_chat.state: return
    
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT balance, vip_until FROM users WHERE id=?", (uid,)) as cur: row = await cur.fetchone()
    
    vip_status = f"⏳ {row[1]} дейін белсенді" if (row[1] and datetime.strptime(row[1], "%Y-%m-%d %H:%M") > datetime.now()) else "❌ Белсенді емес"
    await m.answer(f"💰 <b>Сіздің балансыңыз:</b> {row[0]} монета\n👑 <b>VIP Статус:</b> {vip_status}\n\nМонеталарды чатта қыз/жігіт іздеуге немесе жабық видеоларды ашуға қолдана аласыз.")

# --- РЕФЕРАЛ СТАТИСТИКАСЫ ---
@dp.message_handler(lambda m: m.text == "👥 Реферал", state="*")
async def view_referrals(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    await sync_user_state(uid, state)
    if await state.get_state() == ChatStates.in_chat.state: return
    
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT referrals_count, referral_earnings FROM users WHERE id=?", (uid,)) as cur: row = await cur.fetchone()
        
    ref_link = f"https://t.me/{BOT_USER.replace('@','') }?start={uid}"
    await m.answer(f"👥 <b>Серіктестік бағдарламасы</b>\n\n"
                   f"🔗 Сіздің реферал сілтемеңіз:\n<code>{ref_link}</code>\n\n"
                   f"📊 <b>Статистика:</b>\n"
                   f"- Шақырылған адамдар: <b>{row[0]} қолданушы</b>\n"
                   f"- Барлық тапқан табысыңыз: <b>{row[1]} монета</b>\n\n"
                   f"🎁 Әр шақырылған белсенді адам үшін бірден <b>5 монета</b> аласыз!")

# --- МОНЕТА САТЫП АЛУ ---
@dp.message_handler(lambda m: m.text == "💎 Монета сатып алу", state="*")
async def buy_coins_menu(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    await sync_user_state(uid, state)
    if await state.get_state() == ChatStates.in_chat.state: return
    
    kb = InlineKeyboardMarkup(row_width=1).add(
        InlineKeyboardButton("💳 Kaspi.kz (Теңге)", callback_data="pay_kaspi"),
        InlineKeyboardButton("⭐️ Telegram Stars", callback_data="pay_stars"),
        InlineKeyboardButton("⚡️ Crypto (USDT/BTC)", callback_data="pay_crypto"),
        InlineKeyboardButton("🥝 Qiwi Wallet", callback_data="pay_qiwi")
    )
    await m.answer("💎 <b>Монета сатып алу бөлімі</b>\nӨзіңізге ыңғайлы төлем жүйесін таңдаңыз:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith('pay_'), state="*")
async def process_payment_mock(c: types.CallbackQuery):
    method = c.data.split('_')[1].upper()
    await c.message.answer(f"🛠 <b>{method} арқылы төлем қабылдау</b>\n\nАвтоматты төлем жүйесі техникалық жұмыстарға байланысты уақытша тоқтатылған. Қолмен сатып алу үшін админге жазыңыз: @QZQADMIN")
    await c.answer()

# --- КҮНДЕЛІКТІ БОНУС ---
@dp.message_handler(lambda m: m.text == "🎁 Күнделікті бонус", state="*")
async def daily_bonus_handler(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    await sync_user_state(uid, state)
    if await state.get_state() == ChatStates.in_chat.state: return
    
    now = datetime.now()
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT last_bonus FROM users WHERE id=?", (uid,)) as cur: user = await cur.fetchone()
        last_bonus_dt = datetime.strptime(user[0], "%Y-%m-%d %H:%M") if (user and user[0]) else now - timedelta(days=1)
            
        if now - last_bonus_dt >= timedelta(hours=24):
            await db.execute("UPDATE users SET balance = balance + 15, last_bonus = ? WHERE id=?", (now.strftime("%Y-%m-%d %H:%M"), uid))
            await db.commit()
            await m.answer("🎉 <b>+15 тегін монета</b> балансыңызға қосылды!")
        else:
            time_left = timedelta(hours=24) - (now - last_bonus_dt)
            hours, remainder = divmod(time_left.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            await m.answer(f"⏳ Бонус жаңартылуына: <b>{hours} сағат, {minutes} минут</b> бар.")

# --- АНОНИМДІ ЧАТ ПЕН КЕЗЕК ЭКСПЛОЙТ ҚОРҒАНЫСЫ ---
@dp.message_handler(lambda m: m.text == "🎭 Анонимді чат", state="*")
async def chat_entry(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    await sync_user_state(uid, state)
    if await state.get_state() == ChatStates.in_chat.state: return
    
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT gender, is_banned_until FROM users WHERE id=?", (uid,)) as cur: user = await cur.fetchone()
    
    if user[1] and datetime.now() < datetime.strptime(user[1], "%Y-%m-%d %H:%M"):
        return await m.answer(f"🚫 Бұғатталғансыз. Мерзімі: {user[1]}")
            
    if not user[0]:
        await ChatStates.set_gender.set()
        return await m.answer("Жынысыңызды таңдаңыз:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("👨 Жігітпін", "👩 Қызбын").add("🔙 Артқа"))
        
    await m.answer("🎭 Серіктес іздеу мәзірі:", reply_markup=chat_menu_kb())

@dp.message_handler(state=ChatStates.set_gender)
async def process_gender(m: types.Message):
    if m.text not in ["👨 Жігітпін", "👩 Қызбын"]: return await m.answer("Батырманы басыңыз!")
    gender = "male" if m.text == "👨 Жігітпін" else "female"
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE users SET gender=? WHERE id=?", (m.from_user.id, gender))
        await db.commit()
    await m.answer("✅ Жыныс сақталды!", reply_markup=chat_menu_kb())

@dp.message_handler(lambda m: m.text in ["🎲 Кездейсоқ іздеу (Тегін)", "👩 Қыз іздеу (5 💰)", "👨 Жігіт іздеу (5 💰)"], state="*")
async def start_matchmaking(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    await sync_user_state(uid, state)
    if await state.get_state() == ChatStates.in_chat.state: return
    
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT balance, gender FROM users WHERE id=?", (uid,)) as cur: user = await cur.fetchone()
    if not user or not user[1]: return await chat_entry(m, state)
    
    balance, my_gender = user[0], user[1]
    price = 0 if "Кездейсоқ" in m.text else 5
    if balance < price: return await m.answer("❌ Қаражат жеткіліксіз!")
    
    looking_for = "female" if "Қыз" in m.text else ("male" if "Жігіт" in m.text else "random")
    await ChatStates.searching.set()
    await m.answer("🔍 Серіктес ізделуде...", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 Артқа"))
    
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    async with aiosqlite.connect(DB) as db:
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
            await db.execute("DELETE FROM chat_queue WHERE user_id IN (?, ?)", (uid, partner_id))
            await db.execute("INSERT OR REPLACE INTO active_chats VALUES (?, ?, ?), (?, ?, ?)", (uid, partner_id, now_str, partner_id, uid, now_str))
            if price > 0: await db.execute("UPDATE users SET balance = balance - ? WHERE id=?", (price, uid))
            await db.commit()
            
            stop_kb = ReplyKeyboardMarkup(resize_keyboard=True).add("🛑 Тоқтату")
            rep_uid = InlineKeyboardMarkup().add(InlineKeyboardButton("🚨 Шағымдану", callback_data=f"report_{partner_id}"))
            rep_pid = InlineKeyboardMarkup().add(InlineKeyboardButton("🚨 Шағымдану", callback_data=f"report_{uid}"))
            
            await state.set_state(ChatStates.in_chat)
            await m.answer("✅ <b>Сұхбаттасушы табылды!</b>", reply_markup=stop_kb)
            await m.answer("⚠️ Ереже бұзылса шағымданыңыз:", reply_markup=rep_uid)
            
            p_state = dp.current_state(chat=partner_id, user=partner_id)
            await p_state.set_state(ChatStates.in_chat)
            try:
                await bot.send_message(partner_id, "✅ <b>Сұхбаттасушы табылды!</b>", reply_markup=stop_kb)
                await bot.send_message(partner_id, "⚠️ Ереже бұзылса шағымданыңыз:", reply_markup=rep_pid)
            except Exception: pass
        else:
            await db.execute("INSERT INTO chat_queue VALUES (?, ?, ?, ?)", (uid, my_gender, looking_for, now_str))
            await db.commit()

# --- 🚫 CHАТ ІШІНДЕГІ ХАБАРЛАМАЛАР ЖӘНЕ SHADOWBAN / MODERATION MATRIX ---
@dp.message_handler(state=ChatStates.in_chat, content_types=['text', 'photo', 'video', 'voice'])
async def handle_chat_messages(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT partner_id FROM active_chats WHERE user_id=?", (uid,)) as cur: chat = await cur.fetchone()
        async with db.execute("SELECT is_shadowbanned FROM users WHERE id=?", (uid,)) as cur: sb_row = await cur.fetchone()
        
    if not chat:
        await state.finish()
        return await m.answer("Чат жабылған.", reply_markup=chat_menu_kb())
        
    partner_id = chat[0]
    is_shadowbanned = sb_row[0] if sb_row else 0
    
    if m.text == "🛑 Тоқтату":
        async with aiosqlite.connect(DB) as db:
            await db.execute("DELETE FROM active_chats WHERE user_id IN (?, ?)", (uid, partner_id))
            await db.commit()
        await state.finish()
        rate_kb = InlineKeyboardMarkup(row_width=2).add(
            InlineKeyboardButton("👍 Жақсы", callback_data=f"rt_good_{partner_id}"), InlineKeyboardButton("👎 Спам", callback_data=f"rt_bad_{partner_id}")
        )
        await m.answer("🛑 Чат аяқталды.", reply_markup=chat_menu_kb())
        await m.answer("Серіктесті бағалаңыз:", reply_markup=rate_kb)
        
        p_state = dp.current_state(chat=partner_id, user=partner_id)
        await p_state.finish()
        try:
            await bot.send_message(partner_id, "🛑 Серіктесіңіз чатты аяқтады.", reply_markup=chat_menu_kb())
            await bot.send_message(partner_id, "Оны бағалаңыз:", reply_markup=rate_kb)
        except Exception: pass
        return

    # 2. 🤖 AUTO ANTI SCAM TRIGGER ENGINE
    message_content = m.text or m.caption or ""
    if check_scam_keywords(message_content) or normalize_and_check_spam(message_content):
        async with aiosqlite.connect(DB) as db:
            await db.execute("UPDATE users SET is_shadowbanned = 1 WHERE id=?", (uid,))
            await db.commit()
        is_shadowbanned = 1

    # 3. SHADOWBAN ACTION
    if is_shadowbanned:
        return

    # 4. VIP МЕДИА ШЕКТЕУІ
    if m.content_type in ['photo', 'video']:
        async with aiosqlite.connect(DB) as db:
            async with db.execute("SELECT vip_until FROM users WHERE id=?", (uid,)) as cur: u_data = await cur.fetchone()
        if not u_data or not u_data[0] or datetime.strptime(u_data[0], "%Y-%m-%d %H:%M") < datetime.now():
            return await m.answer("🔒 Медиа файлдар жіберу тек <b>VIP</b> қолданушыларға рұқсат етілген!")

    # 5. МЕДИА КӨШІРМЕСІН СЕРІКТЕСКЕ АЙДАУ
    try:
        if m.content_type == 'photo': await bot.send_photo(partner_id, m.photo[-1].file_id, caption=m.caption, has_spoiler=True)
        elif m.content_type == 'video': await bot.send_video(partner_id, m.video.file_id, caption=m.caption, has_spoiler=True)
        else: await m.copy_to(partner_id)
    except TelegramAPIError as e:
        if "restricted" in str(e).lower() or "content" in str(e).lower():
            await m.answer("⚠️ Telegram бұл файлды қауіпті/NSFW деп тауып, өткізбеді.")
        elif "blocked" in str(e).lower() or "deactivated" in str(e).lower():
            await state.finish()
            async with aiosqlite.connect(DB) as db:
                await db.execute("DELETE FROM active_chats WHERE user_id IN (?, ?)", (uid, partner_id))
                await db.commit()
            await m.answer("⚠️ Серіктес ботты өшіріп тастады.", reply_markup=chat_menu_kb())

# --- БАҒАЛАУ ЖӘНЕ ЖЕДЕЛ ШАҒЫМДАНУ (REPORT) ---
@dp.callback_query_handler(lambda c: c.data.startswith('rt_'), state="*")
async def process_chat_rating(c: types.CallbackQuery):
    action, target_id = c.data.split('_')[1], int(c.data.split('_')[2])
    if action == "bad":
        async with aiosqlite.connect(DB) as db:
            await db.execute("UPDATE users SET dislikes = dislikes + 1 WHERE id=?", (target_id,))
            async with db.execute("SELECT dislikes FROM users WHERE id=?", (target_id,)) as cur: res = await cur.fetchone()
            if res and res[0] >= 3:
                b_time = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
                await db.execute("UPDATE users SET is_banned_until=?, dislikes=0 WHERE id=?", (b_time, target_id))
            await db.commit()
    await c.message.edit_text("✅ Рахмет, баға сақталды!")

@dp.callback_query_handler(lambda c: c.data.startswith('report_'), state="*")
async def report_user_callback(c: types.CallbackQuery, state: FSMContext):
    target_id = int(c.data.split('_')[1])
    uid = c.from_user.id
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM active_chats WHERE user_id IN (?, ?)", (uid, target_id))
        await db.execute("UPDATE users SET dislikes = dislikes + 2 WHERE id=?", (target_id,))
        async with db.execute("SELECT dislikes FROM users WHERE id=?", (target_id,)) as cur: res = await cur.fetchone()
        if res and res[0] >= 3:
            b_time = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
            await db.execute("UPDATE users SET is_banned_until=?, dislikes=0 WHERE id=?", (b_time, target_id))
        await db.commit()
    await state.finish()
    p_state = dp.current_state(chat=target_id, user=target_id)
    await p_state.finish()
    try: await c.message.delete()
    except Exception: pass
    await bot.send_message(uid, "🚨 Жақын арада тексеріледі.", reply_markup=chat_menu_kb())
    try: await bot.send_message(target_id, "🛑 Сіздің үстіңізден шағым түсті, чат жабылды.", reply_markup=chat_menu_kb())
    except Exception: pass

# --- КОНТЕНТ ПЕН ВИДЕО СЕРФИНГ ---
@dp.message_handler(lambda m: m.text in ["🎬 Контент", "😈 VIP видео 😈", "😈 VIP Видео"], state="*")
async def content_menu(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    await sync_user_state(uid, state)
    if await state.get_state() == ChatStates.in_chat.state: return
    
    if "VIP" in m.text:
        m.text = "😈 VIP Видео"
        return await get_video(m, state)
        
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    for g in GENRES:
        if "VIP" not in g: kb.add(g)
    kb.add("🔙 Артқа")
    await m.answer("Жанр таңдаңыз:", reply_markup=kb)

@dp.message_handler(lambda m: m.text in GENRES, state="*")
async def get_video(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    await sync_user_state(uid, state)
    if await state.get_state() == ChatStates.in_chat.state: return
    
    genre = m.text
    config = GENRES_CONFIG[genre]
    
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT balance, vip_until FROM users WHERE id=?", (uid,)) as cur: user = await cur.fetchone()
        
        if "VIP" in genre:
            if not user[1] or datetime.strptime(user[1], "%Y-%m-%d %H:%M") < datetime.now():
                return await m.answer("❌ Сізде VIP жазылым белсенді емес!")

        if user[0] < config['price']: return await m.answer(f"⚠️ Монета жеткіліксіз! Құны: {config['price']} 💰")

        async with db.execute("""SELECT id, file_id FROM content WHERE genre=? AND id NOT IN 
                                 (SELECT content_id FROM history WHERE user_id=?) ORDER BY RANDOM() LIMIT 1""", (genre, uid)) as cur: video = await cur.fetchone()

        if not video:
            await db.execute("DELETE FROM history WHERE user_id=?", (uid,))
            async with db.execute("SELECT id, file_id FROM content WHERE genre=? ORDER BY RANDOM() LIMIT 1", (genre,)) as cur: video = await cur.fetchone()

        if video:
            del_time = (datetime.now() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M")
            await db.execute("UPDATE users SET balance = balance - ? WHERE id=?", (config['price'], uid))
            await db.execute("INSERT INTO history VALUES (?,?,?)", (uid, video[0], datetime.now().strftime("%Y-%m-%d %H:%M")))
            await db.commit()
            
            kb = InlineKeyboardMarkup().add(InlineKeyboardButton("Келесі ⏭", callback_data=f"nxtvd_{genre}"))
            sent = await bot.send_video(uid, video[1], caption=f"💰 Құны: {config['price']} монета\n⚠️ 30 минуттан соң өшеді.", reply_markup=kb)
            await db.execute("INSERT INTO auto_delete_messages VALUES (?, ?, ?)", (uid, sent.message_id, del_time))
            await db.commit()
        else: await m.answer("Бұл бөлімде видео табылған жоқ.")

@dp.callback_query_handler(lambda c: c.data.startswith('nxtvd_'), state="*")
async def next_video_callback(c: types.CallbackQuery, state: FSMContext):
    genre = c.data.split('_')[1]
    c.message.text = genre
    try: await c.message.delete()
    except Exception: pass
    await get_video(c.message, state)

# --- VIP КОНТЕНТКЕ ЖАЗЫЛУ ---
@dp.message_handler(lambda m: m.text == "🔐 VIP контент", state="*")
async def vip_access(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    await sync_user_state(uid, state)
    if await state.get_state() == ChatStates.in_chat.state: return
    
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT balance, vip_until FROM users WHERE id=?", (uid,)) as cur: user = await cur.fetchone()
    
    is_vip = user[1] is not None and datetime.strptime(user[1], "%Y-%m-%d %H:%M") > datetime.now()
    if is_vip:
        await m.answer(f"👑 VIP Мерзімі: {user[1]} дейін.", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("😈 VIP Видео", "🔙 Артқа"))
    else:
        await m.answer("🔐 <b>VIP Жазылым (50 💰 / 24 сағат)</b>\n\nАртықшылықтары:\n- Чатта фото/видео жіберу рұқсаты\n- Жабық VIP категорияны көру мүмкіндігі.", 
                       reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("Сатып алу (50 💰)", callback_data="buy_vip")))

@dp.callback_query_handler(lambda c: c.data == "buy_vip", state="*")
async def buy_vip_callback(c: types.CallbackQuery):
    uid = c.from_user.id
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT balance FROM users WHERE id=?", (uid,)) as cur: user = await cur.fetchone()
        if user[0] < 50: return await c.answer("❌ Қаражат жеткіліксіз!", show_alert=True)
        v_time = (datetime.now() + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
        await db.execute("UPDATE users SET balance = balance - 50, vip_until = ? WHERE id=?", (v_time, uid))
        await db.commit()
    await c.message.delete()
    await bot.send_message(uid, "👑 VIP сәтті қосылды!", reply_markup=main_kb(uid))

# --- ПАЙДАЛАНУШЫЛАРДАН ВИДЕО ҚАБЫЛДАУ ---
@dp.message_handler(lambda m: m.text == "➕ Видео жіберу", state="*")
async def user_up_start(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    await sync_user_state(uid, state)
    if await state.get_state() == ChatStates.in_chat.state: return
    
    await UserStates.upload_genre.set()
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    for g in GENRES:
        if "VIP" not in g: kb.add(g)
    kb.add("🔙 Артқа")
    await m.answer("Қай категорияға видео жібересіз?", reply_markup=kb)

@dp.message_handler(state=UserStates.upload_genre)
async def user_up_genre(m: types.Message, state: FSMContext):
    if m.text not in GENRES: return await m.answer("Мәзірден таңдаңыз!")
    await state.update_data(g=m.text, added=0, dupes=0)
    await UserStates.upload_video.set()
    await m.answer("🎥 Видеоларды жіберіңіз. Аяқтаған соң ✅ Аяқтау басыңыз:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("✅ Аяқтау"))

@dp.message_handler(state=UserStates.upload_video, content_types=['video'])
async def user_up_file(m: types.Message, state: FSMContext):
    data = await state.get_data()
    f_uniq = m.video.file_unique_id
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT id FROM content WHERE file_unique_id=?", (f_uniq,)) as cur: c1 = await cur.fetchone()
        async with db.execute("SELECT id FROM submissions WHERE file_unique_id=?", (f_uniq,)) as cur: c2 = await cur.fetchone()
        if c1 or c2:
            await state.update_data(dupes=data.get('dupes', 0) + 1)
            return
        await db.execute("INSERT INTO submissions(file_id, file_unique_id, genre, user_id) VALUES (?,?,?,?)", (m.video.file_id, f_uniq, data['g'], m.from_user.id))
        await db.commit()
    await state.update_data(added=data.get('added', 0) + 1)

@dp.message_handler(lambda m: m.text == "✅ Аяқтау", state=[AdminStates.add_video_files, UserStates.upload_video])
async def finish_upload(m: types.Message, state: FSMContext):
    data = await state.get_data()
    current_state = await state.get_state()
    
    if current_state == AdminStates.add_video_files.state:
        await m.answer(f"👑 <b>Админ: жүктеу аяқталды!</b>\nЖаңа: {data.get('added',0)}\nҚайталанған: {data.get('dupes',0)}", reply_markup=main_kb(m.from_user.id))
    else:
        await m.answer(f"📊 <b>Видео модерацияға жіберілді!</b>\nСәтті: {data.get('added', 0)}\nҚайталанған: {data.get('dupes', 0)}\n\nБекітілген әр видео үшін 10 монета аласыз!", reply_markup=main_kb(m.from_user.id))
    await state.finish()

# ================= ADMIN PANEL BLOCK =================

@dp.message_handler(lambda m: m.text == "⚙️ Админ", user_id=ADMIN_ID, state="*")
async def admin_panel(m: types.Message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2).add(
        "➕ Видео қосу", "📩 Жіберілгендер", "💰 Монета берегу", "🌍 Барлығына монета", "📢 Рассылка", "📊 Статистика", "🔙 Артқа"
    )
    await m.answer("👑 <b>Админ Панель:</b>", reply_markup=kb)

# 1. АДМИН: ВИДЕО ҚОСУ
@dp.message_handler(lambda m: m.text == "➕ Видео қосу", user_id=ADMIN_ID, state="*")
async def admin_add_video_start(m: types.Message):
    await AdminStates.add_video_genre.set()
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    for g in GENRES: kb.add(g)
    kb.add("🔙 Артқа")
    await m.answer("Базаға қосылатын видео категориясын таңдаңыз:", reply_markup=kb)

@dp.message_handler(state=AdminStates.add_video_genre, user_id=ADMIN_ID)
async def admin_add_video_genre_selected(m: types.Message, state: FSMContext):
    if m.text not in GENRES: return await m.answer("Мәзірді қолданыңыз.")
    await state.update_data(g=m.text, added=0, dupes=0)
    await AdminStates.add_video_files.set()
    await m.answer(f"📥 <b>{m.text}</b> категориясына видеоларды жаппай жіберіңіз. Аяқтау үшін: ✅ Аяқтау", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("✅ Аяқтау"))

@dp.message_handler(state=AdminStates.add_video_files, content_types=['video'], user_id=ADMIN_ID)
async def admin_add_video_file_receiver(m: types.Message, state: FSMContext):
    data = await state.get_data()
    f_uniq = m.video.file_unique_id
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT id FROM content WHERE file_unique_id=?", (f_uniq,)) as cur: exist = await cur.fetchone()
        if exist:
            await state.update_data(dupes=data.get('dupes', 0) + 1)
            return
        await db.execute("INSERT INTO content(file_id, file_unique_id, type, genre) VALUES (?,?,'video',?)", (m.video.file_id, f_uniq, data['g']))
        await db.commit()
    await state.update_data(added=data.get('added', 0) + 1)

# 2. АДМИН: ЖІБЕРІЛГЕНДЕРДІ ТЕКСЕРУ
@dp.message_handler(lambda m: m.text == "📩 Жіберілгендер", user_id=ADMIN_ID, state="*")
async def admin_review_submissions(m: types.Message, state: FSMContext):
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT id, file_id, genre, user_id FROM submissions LIMIT 1") as cur: row = await cur.fetchone()
    if not row: return await m.answer("📥 Жіберілген видеолар кезегі бос.")
    
    sub_id, file_id, genre, user_id = row
    await AdminStates.review_submissions.set()
    await state.update_data(sub_id=sub_id, file_id=file_id, genre=genre, user_id=user_id)
    
    kb = InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("✅ Бекіту (+10 💰)", callback_data="sub_approve"),
        InlineKeyboardButton("❌ Өшіру", callback_data="sub_reject"),
        InlineKeyboardButton("🛑 Шығу", callback_data="sub_exit")
    )
    await bot.send_video(ADMIN_ID, file_id, caption=f"Категория: {genre}\nЖіберуші ID: <code>{user_id}</code>", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith('sub_'), state=AdminStates.review_submissions, user_id=ADMIN_ID)
async def process_submission_decision(c: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    sub_id, file_id, genre, user_id = data['sub_id'], data['file_id'], data['genre'], data['user_id']
    
    async with aiosqlite.connect(DB) as db:
        if c.data == "sub_approve":
            f_uniq = f"sub_approved_{sub_id}"
            await db.execute("INSERT OR IGNORE INTO content(file_id, file_unique_id, type, genre) VALUES (?,?,'video',?)", (file_id, f_uniq, genre))
            await db.execute("UPDATE users SET balance = balance + 10 WHERE id=?", (user_id,))
            await db.execute("DELETE FROM submissions WHERE id=?", (sub_id,))
            await db.commit()
            try: await bot.send_message(user_id, "🎉 Сіз жіберген видео модерациядан өтті! Балансыңызға <b>+10 монета</b> қосылды.")
            except Exception: pass
            await c.answer("Бекітілді!")
        elif c.data == "sub_reject":
            await db.execute("DELETE FROM submissions WHERE id=?", (sub_id,))
            await db.commit()
            await c.answer("Өшірілді!")
        else:
            await state.finish()
            await c.message.delete()
            return await bot.send_message(ADMIN_ID, "Модерация тоқтатылды.", reply_markup=main_kb(ADMIN_ID))
            
    await c.message.delete()
    await admin_review_submissions(c.message, state)

# 3. АДМИН: МОНЕТА БЕРУ (ЖЕКЕ)
@dp.message_handler(lambda m: m.text == "💰 Монета берегу", user_id=ADMIN_ID, state="*")
async def admin_give_coins_start(m: types.Message):
    await AdminStates.giving_coins_id.set()
    await m.answer("Монета алатын қолданушының Telegram ID-ін жазыңыз:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 Артқа"))

@dp.message_handler(state=AdminStates.giving_coins_id, user_id=ADMIN_ID)
async def admin_give_coins_id_recv(m: types.Message, state: FSMContext):
    if not m.text.isdigit(): return await m.answer("Тек сандардан тұратын ID жазыңыз!")
    await state.update_data(target_id=int(m.text))
    await AdminStates.giving_coins_amount.set()
    await m.answer("Қанша монета қосамыз (алып тастау үшін минуспен жазыңыз):")

@dp.message_handler(state=AdminStates.giving_coins_amount, user_id=ADMIN_ID)
async def admin_give_coins_amount_recv(m: types.Message, state: FSMContext):
    try: amount = int(m.text)
    except ValueError: return await m.answer("Тек бүтін сан енгізіңіз!")
    data = await state.get_data()
    target_id = data['target_id']
    
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, target_id))
        await db.commit()
    await state.finish()
    await m.answer(f"✅ Баланс жаңартылды. ID {target_id} үшін {amount} монета амалы жасалды.", reply_markup=main_kb(ADMIN_ID))
    try: await bot.send_message(target_id, f"🔔 Балансыңыз админ тарапынан өзгертілді! Өзгеріс: <b>{amount} монета</b>")
    except Exception: pass

# 4. АДМИН: БАРЛЫҒЫНА МОНЕТА БЕРУ
@dp.message_handler(lambda m: m.text == "🌍 Барлығына монета", user_id=ADMIN_ID, state="*")
async def admin_give_all_start(m: types.Message):
    await AdminStates.giving_all_amount.set()
    await m.answer("Барлық қолданушыларға қанша монетадан таратамыз?", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 Артқа"))

@dp.message_handler(state=AdminStates.giving_all_amount, user_id=ADMIN_ID)
async def admin_give_all_process(m: types.Message, state: FSMContext):
    try: amount = int(m.text)
    except ValueError: return await m.answer("Тек бүтін сан жазыңыз!")
    
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE users SET balance = balance + ?", (amount,))
        await db.commit()
    await state.finish()
    await m.answer(f"🎉 Барлық пайдаланушыларға {amount} монета сәтті үлестірілді!", reply_markup=main_kb(ADMIN_ID))

# --- СТАТИСТИКА ЖӘНЕ РАССЫЛКА ---
@dp.message_handler(lambda m: m.text == "📊 Статистика", user_id=ADMIN_ID, state="*")
async def stat_view(m: types.Message):
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur: uc = await cur.fetchone()
        async with db.execute("SELECT COUNT(*) FROM users WHERE is_shadowbanned=1") as cur: sbc = await cur.fetchone()
        async with db.execute("SELECT genre, COUNT(*) FROM content GROUP BY genre") as cur: vc = await cur.fetchall()
    res = f"👥 Қолданушылар саны: {uc[0]} (Shadowban-да: {sbc[0]})\n\n🎬 Базадағы контент:\n"
    for v in vc: res += f"- {v[0]}: {v[1]} дана\n"
    await m.answer(res)

@dp.message_handler(lambda m: m.text == "📢 Рассылка", user_id=ADMIN_ID, state="*")
async def adm_broadcast_start(m: types.Message):
    await AdminStates.broadcast_msg.set()
    await m.answer("Хабарлама файлын немесе мәтінін жіберіңіз:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 Артқа"))

@dp.message_handler(state=AdminStates.broadcast_msg, content_types=['any'], user_id=ADMIN_ID)
async def adm_broadcast_process(m: types.Message, state: FSMContext):
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT id FROM users") as cur: users = await cur.fetchall()
    await m.answer(f"📢 Жолдануда...")
    count = 0
    for u in users:
        try:
            await m.copy_to(u[0])
            count += 1
            await asyncio.sleep(0.04)
        except RetryAfter as e:
            await asyncio.sleep(e.timeout)
            try: await m.copy_to(u[0])
            except Exception: pass
        except Exception: pass
    await state.finish()
    await m.answer(f"✅ Аяқталды: {count} адамға жетті.", reply_markup=main_kb(ADMIN_ID))

# --- CLEANER LOOP: КЕЗЕК ТАЙМ-АУТ, АВТО-ӨШІРУ ЖӘНЕ БАЗАНЫ ТАЗАЛАУ ---
async def cleaner_loop():
    while True:
        await asyncio.sleep(30)
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M")
        
        try:
            async with aiosqlite.connect(DB) as db:
                async with db.execute("SELECT chat_id, message_id FROM auto_delete_messages WHERE delete_at <= ?", (now_str,)) as cur:
                    async for row in cur:
                        try: await bot.delete_message(row[0], row[1])
                        except Exception: pass
                await db.execute("DELETE FROM auto_delete_messages WHERE delete_at <= ?", (now_str,))
                
                queue_timeout = (now - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M")
                async with db.execute("SELECT user_id FROM chat_queue WHERE created_at <= ?", (queue_timeout,)) as cur:
                    async for row in cur:
                        try: await bot.send_message(row[0], "⏳ Іздеу уақыты аяқталды. Серіктес табылмады. Қайта қосылып көріңіз.", reply_markup=chat_menu_kb())
                        except Exception: pass
                await db.execute("DELETE FROM chat_queue WHERE created_at <= ?", (queue_timeout,))
                
                history_timeout = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M")
                await db.execute("DELETE FROM history WHERE timestamp <= ?", (history_timeout,))
                
                await db.commit()
        except Exception as e:
            logging.error(f"Cleaner loop error: {e}")

# --- UX CLEANER FIX ---
@dp.message_handler(content_types=['text'], state="*")
async def clean_chat_ux(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    if await sync_user_state(uid, state):
        return await handle_chat_messages(m, state)
        
    current_fsm = await state.get_state()
    if current_fsm is not None: return

    buttons = ["🎬 Контент", "➕ Видео жіберу", "💰 Баланс", "👥 Реферал", "💎 Монета сатып алу", "⚙️ Админ", "🔐 VIP контент", "🔙 Артқа", "😈 VIP видео 😈", "😈 VIP Видео", "✅ Аяқтау", "🎭 Анонимді чат", "🎁 Күнделікті бонус", "🎲 Кездейсоқ іздеу (Тегін)", "👩 Қыз іздеу (5 💰)", "👨 Жігіт іздеу (5 💰)", "🛑 Тоқтату", "👨 Жігітпін", "👩 Қызбын", "➕ Видео қосу", "📩 Жіберілгендер", "💰 Монета берегу", "🌍 Барлығына монета", "📢 Рассылка", "📊 Статистика"]
    if m.text not in buttons and not m.text.startswith('/'):
        try: await m.delete()
        except Exception: pass

# --- START BOT ---
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())
    loop.create_task(cleaner_loop())
    executor.start_polling(dp, skip_updates=False)
