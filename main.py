import os
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
import sqlite3
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота
bot = Bot(token='8169250352:AAEAUh6DYw8fUG-NUFABtXD4TpAdfDIdlbI')
dp = Dispatcher(storage=MemoryStorage())
router = Router()
scheduler = AsyncIOScheduler()

# Инициализация БД
conn = sqlite3.connect('events.db')
cursor = conn.cursor()

# Создание таблиц
cursor.execute('''CREATE TABLE IF NOT EXISTS events
                  (id INTEGER PRIMARY KEY AUTOINCREMENT,
                   name TEXT,
                   date TEXT,
                   description TEXT,
                   creator_id INTEGER,
                   chat_id INTEGER)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS participants
                  (event_id INTEGER,
                   user_id INTEGER,
                   username TEXT,
                   FOREIGN KEY(event_id) REFERENCES events(id))''')

cursor.execute('''CREATE TABLE IF NOT EXISTS tasks
                  (id INTEGER PRIMARY KEY AUTOINCREMENT,
                   event_id INTEGER,
                   name TEXT,
                   assigned_to INTEGER,
                   completed BOOLEAN DEFAULT 0,
                   FOREIGN KEY(event_id) REFERENCES events(id))''')

conn.commit()


class EventCreationStates(StatesGroup):
    NAME = State()
    DATE = State()
    DESCRIPTION = State()
    PARTICIPANTS = State()


# Хелпер-функции
def add_event(name, date, description, creator_id, chat_id):
    cursor.execute('''INSERT INTO events (name, date, description, creator_id, chat_id)
                      VALUES (?, ?, ?, ?, ?)''',
                   (name, date, description, creator_id, chat_id))
    conn.commit()
    return cursor.lastrowid


def add_participants(event_id, participants):
    for participant in participants:
        cursor.execute('''INSERT INTO participants (event_id, user_id, username)
                          VALUES (?, ?, ?)''',
                       (event_id, participant['id'], participant['username']))
    conn.commit()


def get_participants(event_id):
    cursor.execute('SELECT username FROM participants WHERE event_id = ?', (event_id,))
    return [row[0] for row in cursor.fetchall()] or ['пока нет участников']


# Обработчики команд
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я бот для организации мероприятий.\n"
        "Доступные команды:\n"
        "/create_event - Создать мероприятие\n"
        "/my_events - Мои мероприятия"
    )


@dp.message(Command("create_event"))
async def create_event(message: types.Message, state: FSMContext):
    await state.set_state(EventCreationStates.NAME)
    await message.answer("Введите название мероприятия:")


@dp.message(EventCreationStates.NAME)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(EventCreationStates.DATE)
    await message.answer("Введите дату и время (формат: ДД.ММ.ГГГГ ЧЧ:ММ):")


@dp.message(EventCreationStates.DATE)
async def process_date(message: types.Message, state: FSMContext):
    try:
        datetime.strptime(message.text, "%d.%m.%Y %H:%M")
        await state.update_data(date=message.text)
        await state.set_state(EventCreationStates.DESCRIPTION)
        await message.answer("Введите описание мероприятия:")
    except ValueError:
        await message.answer("❌ Неверный формат! Используйте ДД.ММ.ГГГГ ЧЧ:ММ")


@dp.message(EventCreationStates.DESCRIPTION)
async def process_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(EventCreationStates.PARTICIPANTS)
    await message.answer("Введите username участников через пробел (например, @user1 @user2):")


@dp.message(EventCreationStates.PARTICIPANTS)
async def process_participants(message: types.Message, state: FSMContext):
    data = await state.get_data()
    participants = message.text.split()

    # Сохраняем мероприятие
    event_id = add_event(
        name=data['name'],
        date=data['date'],
        description=data['description'],
        creator_id=message.from_user.id,
        chat_id=message.chat.id
    )

    # Добавляем участников (заглушка)
    participants_list = [{'id': 0, 'username': u} for u in participants]
    add_participants(event_id, participants_list)

    # Напоминание
    try:
        event_date = datetime.strptime(data['date'], "%d.%m.%Y %H:%M")
        scheduler.add_job(
            send_reminder,
            'date',
            run_date=event_date - timedelta(days=1),
            args=[event_id]
        )
    except ValueError as e:
        logging.error(f"Ошибка планирования: {e}")

    await message.answer(f"✅ Мероприятие '{data['name']}' создано!")
    await state.clear()


@dp.message(Command("my_events"))
async def cmd_my_events(message: types.Message):
    user_id = message.from_user.id
    cursor.execute('''
        SELECT DISTINCT e.id, e.name, e.date, e.description 
        FROM events e
        LEFT JOIN participants p ON e.id = p.event_id
        WHERE e.creator_id = ? OR p.user_id = ?
    ''', (user_id, user_id))

    events = cursor.fetchall()

    if not events:
        await message.answer("❌ Вы не участвуете в мероприятиях")
        return

    for event in events:
        try:
            date_obj = datetime.strptime(event[2], "%d.%m.%Y %H:%M")
            date_str = date_obj.strftime("%d.%m.%Y")
            time_str = date_obj.strftime("%H:%M")
        except ValueError:
            date_str = time_str = "⚠️ Ошибка даты"

        text = (
            f"🎉 Мероприятие: {event[1]}\n"
            f"📅 Дата: {date_str}\n"
            f"⏰ Время: {time_str}\n"
            f"📝 Описание: {event[3] or 'нет описания'}\n"
            f"👥 Участники: {', '.join(get_participants(event[0]))}"
        )
        await message.answer(text)


async def send_reminder(event_id):
    cursor.execute("SELECT name, date FROM events WHERE id = ?", (event_id,))
    event = cursor.fetchone()
    if not event:
        return

    participants = get_participants(event_id)
    for username in participants:
        try:
            await bot.send_message(
                chat_id=event_id,  # Заглушка: нужен реальный chat_id
                text=f"⏰ Напоминание: {event[0]} через 24 часа!"
            )
        except Exception as e:
            logging.error(f"Ошибка отправки напоминания: {e}")


async def main():
    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
