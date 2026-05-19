from aiogram.dispatcher.filters.state import State, StatesGroup

class ChatState(StatesGroup):
    gender = State()
    searching = State()
    chatting = State()
