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

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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
        "\nДоступные команды:\n"
        "/create_event - Создать мероприятие\n"
        "/my_events - Мои мероприятия\n"
        "/delete_event - Удалить мероприятие\n"
        "/remind_me - Добавить напоминание\n"
        "/add_tasks - Создать задачи\n"
        "/view_tasks - Просмотреть задачи"
    )


# Добавим новое состояние для удаления
class EventDeletionStates(StatesGroup):
    CONFIRM_DELETE = State()


# Дополним хелпер-функции
def delete_event(event_id):
    # Удаляем связанные данные
    cursor.execute('DELETE FROM participants WHERE event_id = ?', (event_id,))
    cursor.execute('DELETE FROM tasks WHERE event_id = ?', (event_id,))
    cursor.execute('DELETE FROM events WHERE id = ?', (event_id,))
    conn.commit()


def get_user_events(user_id):
    cursor.execute('SELECT id, name, date FROM events WHERE creator_id = ?', (user_id,))
    return cursor.fetchall()


# Добавим обработчики команд
@dp.message(Command("delete_event"))
async def cmd_delete_event(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    events = get_user_events(user_id)

    if not events:
        await message.answer("❌ У вас нет созданных мероприятий")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for event in events:
        event_id, name, date = event
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"{name} ({date})",
                callback_data=f"confirm_delete_{event_id}"
            )
        ])

    await message.answer("Выберите мероприятие для удаления:", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("confirm_delete_"))
async def process_delete_event(callback: types.CallbackQuery):
    event_id = int(callback.data.split("_")[-1])
    user_id = callback.from_user.id

    # Проверяем права доступа
    cursor.execute('SELECT creator_id FROM events WHERE id = ?', (event_id,))
    result = cursor.fetchone()

    if not result or result[0] != user_id:
        await callback.answer("❌ Вы не можете удалить это мероприятие!", show_alert=True)
        return

    # Удаляем мероприятие
    delete_event(event_id)

    # Удаляем сообщение с кнопками
    await callback.message.delete()
    await callback.answer("✅ Мероприятие успешно удалено!", show_alert=True)


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


# Добавляем новую таблицу для напоминаний
cursor.execute('''CREATE TABLE IF NOT EXISTS reminders
                  (id INTEGER PRIMARY KEY AUTOINCREMENT,
                   user_id INTEGER,
                   event_id INTEGER,
                   hours_before INTEGER,
                   reminder_sent BOOLEAN DEFAULT 0)''')
conn.commit()


# Добавляем новые состояния
class RemindMeStates(StatesGroup):
    SELECT_EVENT = State()
    SELECT_HOURS = State()


# Хелпер-функции для работы с напоминаниями
def add_reminder(user_id, event_id, hours_before):
    cursor.execute('''INSERT INTO reminders (user_id, event_id, hours_before)
                      VALUES (?, ?, ?)''',
                   (user_id, event_id, hours_before))
    conn.commit()
    return cursor.lastrowid


def get_user_events_with_participation(user_id):
    cursor.execute('''
        SELECT DISTINCT e.id, e.name, e.date 
        FROM events e
        LEFT JOIN participants p ON e.id = p.event_id
        WHERE e.creator_id = ? OR p.user_id = ?
    ''', (user_id, user_id))
    return cursor.fetchall()


# Обработчики команд
@dp.message(Command("remind_me"))
async def cmd_remind_me(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    events = get_user_events_with_participation(user_id)

    if not events:
        await message.answer("❌ Вы не участвуете ни в каких мероприятиях")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for event in events:
        event_id, name, date = event
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"{name} ({date})",
                callback_data=f"remind_event_{event_id}"
            )
        ])

    await message.answer("Выберите мероприятие для напоминания:", reply_markup=keyboard)
    await state.set_state(RemindMeStates.SELECT_EVENT)


@dp.callback_query(F.data.startswith("remind_event_"), RemindMeStates.SELECT_EVENT)
async def process_remind_event(callback: types.CallbackQuery, state: FSMContext):
    event_id = int(callback.data.split("_")[-1])
    await state.update_data(event_id=event_id)

    await callback.message.answer("За сколько часов до мероприятия вам напомнить? Введите число:")
    await state.set_state(RemindMeStates.SELECT_HOURS)
    await callback.answer()


@dp.message(RemindMeStates.SELECT_HOURS)
async def process_remind_hours(message: types.Message, state: FSMContext):
    try:
        hours = int(message.text)
        if hours <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Пожалуйста, введите положительное целое число")
        return

    data = await state.get_data()
    event_id = data['event_id']
    user_id = message.from_user.id

    # Проверяем существование мероприятия
    cursor.execute('SELECT date FROM events WHERE id = ?', (event_id,))
    event_date = cursor.fetchone()
    if not event_date:
        await message.answer("❌ Мероприятие не найдено")
        await state.clear()
        return

    # Добавляем напоминание
    add_reminder(user_id, event_id, hours)

    # Планируем напоминание
    try:
        event_datetime = datetime.strptime(event_date[0], "%d.%m.%Y %H:%M")
        reminder_time = event_datetime - timedelta(hours=hours)

        scheduler.add_job(
            send_personal_reminder,
            'date',
            run_date=reminder_time,
            args=[user_id, event_id]
        )
    except Exception as e:
        logging.error(f"Ошибка планирования: {e}")

    await message.answer(f"✅ Напоминание установлено за {hours} часов до мероприятия!")
    await state.clear()


async def send_personal_reminder(user_id, event_id):
    try:
        cursor.execute('SELECT name, date FROM events WHERE id = ?', (event_id,))
        event = cursor.fetchone()
        if event:
            await bot.send_message(
                chat_id=user_id,
                text=f"⏰ Напоминание: мероприятие '{event[0]}' начнётся {event[1]}!"
            )
            # Помечаем напоминание как отправленное
            cursor.execute('''UPDATE reminders SET reminder_sent = 1
                           WHERE user_id = ? AND event_id = ?''',
                           (user_id, event_id))
            conn.commit()
    except Exception as e:
        logging.error(f"Ошибка отправки персонального напоминания: {e}")


class AddTasksStates(StatesGroup):
    SELECT_EVENT = State()
    TASKS_COUNT = State()
    TASK_INPUT = State()


class ViewTasksStates(StatesGroup):
    SELECT_EVENT = State()


# Хелпер-функции для задач
def add_task(event_id, name, assigned_to=None):
    cursor.execute('''INSERT INTO tasks (event_id, name, assigned_to)
                      VALUES (?, ?, ?)''',
                   (event_id, name, assigned_to))
    conn.commit()
    return cursor.lastrowid


def get_tasks(event_id):
    cursor.execute('''SELECT id, name, assigned_to, completed 
                      FROM tasks WHERE event_id = ?''', (event_id,))
    return cursor.fetchall()


# Обработчики команд
@dp.message(Command("add_tasks"))
async def cmd_add_tasks(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    events = get_user_events_with_participation(user_id)

    if not events:
        await message.answer("❌ Вы не участвуете ни в каких мероприятиях")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for event in events:
        event_id, name, date = event
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"{name} ({date})",
                callback_data=f"tasks_event_{event_id}"
            )
        ])

    await message.answer("Выберите мероприятие для добавления задач:", reply_markup=keyboard)
    await state.set_state(AddTasksStates.SELECT_EVENT)


@dp.callback_query(F.data.startswith("tasks_event_"), AddTasksStates.SELECT_EVENT)
async def process_tasks_event(callback: types.CallbackQuery, state: FSMContext):
    event_id = int(callback.data.split("_")[-1])
    await state.update_data(event_id=event_id)
    await callback.message.answer("Сколько задач нужно добавить? Введите число:")
    await state.set_state(AddTasksStates.TASKS_COUNT)
    await callback.answer()


@dp.message(AddTasksStates.TASKS_COUNT)
async def process_tasks_count(message: types.Message, state: FSMContext):
    try:
        tasks_count = int(message.text)
        if tasks_count <= 0:
            raise ValueError
        await state.update_data(tasks_count=tasks_count, current_task=1)
        await message.answer(f"Введите название задачи 1 из {tasks_count}:")
        await state.set_state(AddTasksStates.TASK_INPUT)
    except ValueError:
        await message.answer("❌ Пожалуйста, введите положительное целое число")


@dp.message(AddTasksStates.TASK_INPUT)
async def process_task_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    event_id = data['event_id']
    tasks_count = data['tasks_count']
    current_task = data['current_task']

    # Сохраняем задачу
    add_task(event_id, message.text)

    if current_task < tasks_count:
        new_current = current_task + 1
        await state.update_data(current_task=new_current)
        await message.answer(f"Введите название задачи {new_current} из {tasks_count}:")
    else:
        await message.answer("✅ Все задачи успешно добавлены!")
        await state.clear()


@dp.message(Command("view_tasks"))
async def cmd_view_tasks(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    events = get_user_events_with_participation(user_id)

    if not events:
        await message.answer("❌ Вы не участвуете ни в каких мероприятиях")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for event in events:
        event_id, name, date = event
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"{name} ({date})",
                callback_data=f"view_tasks_{event_id}"
            )
        ])

    await message.answer("Выберите мероприятие для просмотра задач:", reply_markup=keyboard)
    await state.set_state(ViewTasksStates.SELECT_EVENT)


@dp.callback_query(F.data.startswith("view_tasks_"), ViewTasksStates.SELECT_EVENT)
async def process_view_tasks(callback: types.CallbackQuery, state: FSMContext):
    event_id = int(callback.data.split("_")[-1])
    tasks = get_tasks(event_id)

    if not tasks:
        await callback.message.answer("❌ Для этого мероприятия нет задач")
        await callback.answer()
        return

    response = ["📌 Список задач:"]
    for task in tasks:
        status = "✅ Выполнена" if task[3] else "🟡 В процессе"
        assigned = f" (ответственный: {task[2]})" if task[2] else ""
        response.append(f"{task[1]} {status}{assigned}")

    await callback.message.answer("\n".join(response))
    await callback.answer()
    await state.clear()


async def main():
    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
