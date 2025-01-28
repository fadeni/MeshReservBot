# config/settings.py

import os

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '7669190496:AAFRCW2pX4w5k52JI31usGnLRhraWYGGZMI')

# Уровень логирования: DEBUG, INFO, WARNING, ERROR, CRITICAL
LOGGING_LEVEL = os.getenv('LOGGING_LEVEL', 'INFO')

# Путь к файлу базы данных
DATABASE_PATH = 'users.db'

# Путь к файлу ключа шифрования для Fernet
ENCRYPTION_KEY_PATH = 'encryption.key'
