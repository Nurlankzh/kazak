from aiogram.types import ReplyKeyboardMarkup

def main_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)

    kb.add("🎭 Анонимді чат")
    kb.add("🎁 Күнделікті бонус")
    kb.add("💰 Баланс")

    return kb

def chat_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)

    kb.add("👩 Қыз іздеу (5 💰)")
    kb.add("👨 Жігіт іздеу (5 💰)")
    kb.add("🎲 Кездейсоқ")

    return kb
