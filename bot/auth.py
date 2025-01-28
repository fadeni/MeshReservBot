# bot/auth.py

import os
import json
import logging
from cryptography.fernet import Fernet
from octodiary.apis import AsyncMobileAPI
from octodiary.urls import Systems
from .database import get_db_connection
from config.settings import ENCRYPTION_KEY_PATH

logger = logging.getLogger(__name__)

def get_cipher_suite():
    if os.path.exists(ENCRYPTION_KEY_PATH):
        with open(ENCRYPTION_KEY_PATH, 'rb') as f:
            key = f.read()
    else:
        key = Fernet.generate_key()
        with open(ENCRYPTION_KEY_PATH, 'wb') as f:
            f.write(key)
    return Fernet(key)

cipher_suite = get_cipher_suite()

def encrypt_token(token_data):
    token_json = json.dumps(token_data).encode()
    return cipher_suite.encrypt(token_json)

def decrypt_token(encrypted_token):
    decrypted_bytes = cipher_suite.decrypt(encrypted_token)
    return json.loads(decrypted_bytes.decode())

def save_token_db(telegram_user_id, encrypted_token):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        REPLACE INTO users (telegram_user_id, encrypted_token)
        VALUES (?, ?)
    ''', (telegram_user_id, encrypted_token))
    conn.commit()
    conn.close()

def load_token_db(telegram_user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT encrypted_token FROM users WHERE telegram_user_id = ?
    ''', (telegram_user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row[0]
    return None

async def is_user_logged_in(telegram_user_id):
    """
    Проверяет, есть ли у пользователя валидный токен.
    Если да, пытается вызвать get_users_profile_info().
    """
    api = AsyncMobileAPI(system=Systems.MES)
    encrypted_token = load_token_db(telegram_user_id)
    if encrypted_token:
        try:
            token_data = decrypt_token(encrypted_token)
            api.token = token_data
            profiles = await api.get_users_profile_info()
            if profiles:
                return True
        except Exception as e:
            logger.error("Сохранённый токен недействителен для пользователя %s: %s", telegram_user_id, e)
    return False

async def get_api_client(telegram_user_id, username=None, password=None):
    """
    Возвращает пару (api, sms_code_obj).
    Если sms_code_obj не None, нужна двухфакторная аутентификация.
    """
    api = AsyncMobileAPI(system=Systems.MES)
    encrypted_token = load_token_db(telegram_user_id)

    # 1) Пробуем использовать сохранённый токен
    if encrypted_token:
        try:
            token_data = decrypt_token(encrypted_token)
            api.token = token_data
            profiles = await api.get_users_profile_info()
            if profiles:
                return api, None
        except Exception as e:
            logger.error("Недействительный токен для пользователя %s: %s", telegram_user_id, e)

    # 2) Если нет токена или он невалиден, делаем полную авторизацию
    if username and password:
        try:
            sms_code_obj = await api.login(username=username, password=password)
            return api, sms_code_obj
        except Exception as e:
            logger.error("Ошибка авторизации пользователя %s: %s", telegram_user_id, e)
            return None, None
    else:
        return None, None
