# bot/database.py

import sqlite3
from config.settings import DATABASE_PATH

def get_db_connection():
    return sqlite3.connect(DATABASE_PATH)

def init_db():
    """
    Создаёт таблицу users (если нет) для хранения токенов.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_user_id INTEGER PRIMARY KEY,
            encrypted_token BLOB
        )
    ''')
    conn.commit()
    conn.close()
def init_schedule_db():
    """
    Создает таблицу schedule, если ее нет.
    Включаем дополнительные поля:
      homework_text, room_number, lesson_theme.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS schedule (
            user_id INTEGER,
            date TEXT,
            lesson_id INTEGER,
            subject_name TEXT,
            start_time TEXT,
            end_time TEXT,
            homework_text TEXT,
            room_number TEXT,
            lesson_theme TEXT
        )
    ''')
    conn.commit()
    conn.close()


def delete_user_data(telegram_user_id: int):
    """
    Удаляет данные пользователя (зашифрованный токен) из базы данных.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM users WHERE telegram_user_id = ?', (telegram_user_id,))
    conn.commit()
    conn.close()

def clear_user_schedule(user_id: int):
    """
    Удаляет все записи расписания пользователя user_id (телеграм-пользователя).
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM schedule WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def save_events_in_db(user_id: int, events_response):
    """
    Сохраняет список уроков (events) для данного user_id в таблицу schedule.
    Теперь также записываем room_number и lesson_theme.
    """
    items = events_response.response  # список уроков (Item)
    conn = get_db_connection()
    cursor = conn.cursor()

    for event in items:
        dt_str = ""
        start_str = ""
        end_str = ""
        if event.start_at:
            dt_str = event.start_at.strftime('%Y-%m-%d')
            start_str = event.start_at.strftime('%H:%M')
        if event.finish_at:
            end_str = event.finish_at.strftime('%H:%M')

        subject = event.subject_name or ""
        lesson_id = event.id

        # Домашка
        hw_text = None
        if event.homework and event.homework.descriptions:
            hw_text = "\n".join(event.homework.descriptions)

        # Новые поля
        room = event.room_number or ""
        theme = event.lesson_theme or ""

        cursor.execute('''
            INSERT INTO schedule (
                user_id,
                date,
                lesson_id,
                subject_name,
                start_time,
                end_time,
                homework_text,
                room_number,
                lesson_theme
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            user_id,
            dt_str,
            lesson_id,
            subject,
            start_str,
            end_str,
            hw_text,
            room,
            theme
        ))

    conn.commit()
    conn.close()

