# bot/handlers.py

import logging
import re
from datetime import datetime, date, timedelta
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from .auth import (
    is_user_logged_in,
    get_api_client,
    save_token_db,
    load_token_db,
    encrypt_token,
    decrypt_token,
)
from .database import (
    get_db_connection,
    # init_db, init_schedule_db, clear_user_schedule, save_events_in_db,
    delete_user_data,
)
from .utils import generate_calendar_keyboard, compute_21days
from octodiary.apis import AsyncMobileAPI
from octodiary.urls import Systems
from octodiary.types.enter_sms_code import EnterSmsCode

logger = logging.getLogger(__name__)

# Состояния для ConversationHandler (логин)
USERNAME, PASSWORD, SMS_CODE = range(3)

def setup_handlers(application):
    """
    Регистрируем все необходимые хендлеры в Application.
    """
    # Создаем ConversationHandler для логина
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('login', login)],
        states={
            USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_username)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_password)],
            SMS_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_sms_code)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    application.add_handler(CommandHandler('start', start))
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('schedule', schedule))

    # Обработчик всех колбэков (callback_data)
    application.add_handler(CallbackQueryHandler(handle_callback_query))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start — проверяем, авторизован ли пользователь.
    Если да, предлагаем действия (посмотреть расписание / удалить данные),
    иначе выводим приветствие и просим /login.
    """
    user = update.effective_user
    telegram_user_id = user.id

    if await is_user_logged_in(telegram_user_id):
        # Если уже есть валидный токен
        keyboard = [
            [InlineKeyboardButton("Посмотреть расписание", callback_data='view_schedule')],
            [InlineKeyboardButton("Удалить мои данные из бота", callback_data='delete_my_data')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f'Здравствуйте, {user.first_name}! Вы уже авторизованы. Выберите действие:',
            reply_markup=reply_markup
        )
    else:
        # Приветственное сообщение + описание команд
        welcome_text = (
            f"Здравствуйте, {user.first_name}!\n\n"
            "Это бот-помощник, позволяющий просматривать расписание МЭШ.\n"
            "Основные команды:\n"
            "  /login - Авторизация (логин/пароль + SMS)\n"
            "  /schedule - Просмотр расписания (после авторизации)\n"
            "  /cancel - Отмена любой операции\n"
            "  /start - Повторное приветствие или выбор действий\n\n"
            "Чтобы начать, введите /login."
        )
        await update.message.reply_text(welcome_text)


async def login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Начало процесса логина (ConversationHandler).
    Если пользователь уже авторизован, завершаем сразу.
    Иначе просим ввести логин.
    """
    telegram_user_id = update.effective_user.id
    if await is_user_logged_in(telegram_user_id):
        await update.message.reply_text('Вы уже авторизованы.')
        return ConversationHandler.END
    else:
        await update.message.reply_text('Пожалуйста, введите ваш номер телефона/почту/логин от mos.ru:')
        return USERNAME


async def get_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['username'] = update.message.text
    await update.message.reply_text('Теперь введите ваш пароль:')
    return PASSWORD


async def get_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['password'] = update.message.text
    await update.message.reply_text('Пожалуйста, подождите, идёт авторизация...')

    telegram_user_id = update.effective_user.id
    username = context.user_data['username']
    password = context.user_data['password']
    api, sms_code_obj = await get_api_client(telegram_user_id, username, password)

    if api is None:
        await update.message.reply_text('Ошибка авторизации. Попробуйте снова /login.')
        return ConversationHandler.END

    context.user_data['api'] = api
    context.user_data['sms_code_obj'] = sms_code_obj

    if sms_code_obj:
        await update.message.reply_text('Введите код из SMS/Приложения Госуслуг:')
        return SMS_CODE
    else:
        await update.message.reply_text(
            'Авторизация успешна! Используйте /schedule для просмотра расписания.'
        )
        return ConversationHandler.END

async def sync_user_schedule(tg_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    Синхронизирует расписание одного пользователя (tg_id) из МЭШ в локальную БД.
    Смысл - вызвать, когда пользователь впервые залогинился.
    """
    from .database import clear_user_schedule, save_events_in_db
    from bot.auth import decrypt_token
    from octodiary.apis import AsyncMobileAPI
    from octodiary.urls import Systems

    logger = logging.getLogger(__name__)

    # 1) Берём зашифрованный токен из БД
    enc_token = load_token_db(tg_id)
    if not enc_token:
        logger.warning(f"У пользователя {tg_id} нет токена, пропускаем sync_user_schedule.")
        return

    try:
        token_data = decrypt_token(enc_token)
        mesh_api = AsyncMobileAPI(system=Systems.MES)
        mesh_api.token = token_data
    except Exception as e:
        logger.warning(f"Ошибка расшифровки токена при sync_user_schedule(tg_id={tg_id}): {e}")
        return

    # 2) Вызываем API MЭШ, например, на 7 дней назад и 7 дней вперёд
    today = date.today()
    begin_date = today - timedelta(days=7)
    end_date = today + timedelta(days=7)

    try:
        profiles = await mesh_api.get_users_profile_info()
        if not profiles:
            logger.warning(f"Нет профилей у {tg_id}, не можем синхронизировать.")
            return
        first_profile = profiles[0]
        fam = await mesh_api.get_family_profile(profile_id=first_profile.id)
        if not fam.children:
            logger.warning(f"У пользователя {tg_id} нет children, пропускаем.")
            return

        child = fam.children[0]
        person_guid = child.contingent_guid
        mes_role = fam.profile.type

        events = await mesh_api.get_events(
            person_id=person_guid,
            mes_role=mes_role,
            begin_date=begin_date,
            end_date=end_date
        )

        # 3) Очищаем расписание, сохраняем свежее
        clear_user_schedule(tg_id)
        save_events_in_db(tg_id, events)

        logger.info(f"Синхронизация расписания user_id={tg_id} завершена успешно.")
    except Exception as e:
        logger.warning(f"Ошибка при синхронизации user_id={tg_id}: {e}")

async def get_sms_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sms_code = update.message.text
    telegram_user_id = update.effective_user.id
    api = context.user_data['api']
    sms_code_obj = context.user_data['sms_code_obj']

    try:
        api.token = await sms_code_obj.async_enter_code(sms_code)
        encrypted_token = encrypt_token(api.token)
        save_token_db(telegram_user_id, encrypted_token)
    except Exception as e:
        logger.error("Ошибка при вводе SMS-кода для пользователя %s: %s", telegram_user_id, e)
        await update.message.reply_text(
            'Неверный SMS-код или истекло время. Попробуйте снова с помощью команды /login.'
        )
        return ConversationHandler.END

    await update.message.reply_text(
        'Авторизация успешна! Используйте /schedule для просмотра расписания.'
    )

    await sync_user_schedule(telegram_user_id, context)
    return ConversationHandler.END


async def schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /schedule — показываем календарь (21 день, offset=7 => текущая неделя),
    прикрепляя 1.jpg ("Выберите дату").
    """
    telegram_user_id = update.effective_user.id
    api = context.user_data.get('api')

    if not api:
        encrypted_token = load_token_db(telegram_user_id)
        if encrypted_token:
            try:
                token_data = decrypt_token(encrypted_token)
                api_local = AsyncMobileAPI(system=Systems.MES)
                api_local.token = token_data
                context.user_data['api'] = api_local
            except Exception as e:
                logger.error("Ошибка при дешифровании токена: %s", e)
                await update.effective_message.reply_text(
                    'Сессия истекла. Пожалуйста, /login снова.'
                )
                return
        else:
            await update.effective_message.reply_text('Пожалуйста, выполните /login.')
            return

    # Предупреждение
    await update.effective_message.reply_text(
        "Внимание: храним расписание только на 3 недели (прошлая, текущая, следующая)."
    )

    # Формируем календарь
    markup = generate_calendar_keyboard(offset=7)  # Текущая неделя

    # Удаляем предыдущее сообщение, отправляем фото 1.jpg
    await update.effective_message.delete()
    with open("bot/photo/1.jpg", "rb") as f:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=f,
            caption="Выберите дату",
            reply_markup=markup
        )


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    logger.info("callback_data: %s", data)

    match_day = re.match(r'^cal21_day_(\d+)$', data)
    if match_day:
        idx = int(match_day.group(1))
        await process_calendar_day(query, context, idx)
        return

    match_prev = re.match(r'^cal21_prev_(\d+)$', data)
    if match_prev:
        old_offset = int(match_prev.group(1))
        new_offset = max(0, old_offset - 5)
        markup = generate_calendar_keyboard(offset=new_offset)

        await query.message.delete()
        with open("bot/photo/1.jpg", "rb") as f:
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=f,
                caption="Выберите дату",
                reply_markup=markup
            )
        return

    match_next = re.match(r'^cal21_next_(\d+)$', data)
    if match_next:
        old_offset = int(match_next.group(1))
        new_offset = old_offset + 5
        if new_offset >= 21:
            new_offset = 16
        markup = generate_calendar_keyboard(offset=new_offset)

        await query.message.delete()
        with open("bot/photo/1.jpg", "rb") as f:
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=f,
                caption="Выберите дату",
                reply_markup=markup
            )
        return

    if data == 'back_to_schedule':
        await back_to_schedule(update, context)
    elif data == 'back_to_lessons':
        await back_to_lessons(update, context)
    elif data == 'delete_my_data':
        await delete_my_data(update, context)
    elif data == 'view_schedule':
        await schedule(update, context)
    elif data.startswith('lesson_'):
        await lesson_detail(update, context)
    else:
        logger.warning("Неизвестный callback_data: %s", data)
        await query.answer("Неизвестный ввод")


async def process_calendar_day(query, context, day_index: int):
    """
    Когда пользователь выбрал дату (cal21_day_X):
      - Пытаемся получить расписание из МЭШ.
      - Если ошибка => fallback из локальной БД (schedule).
      - Независимо от fallback или нет, прикрепляем фото 2.jpg: "Выберите урок на <дата>".
      - Сохраняем ДЗ в lessons (fallback) через homework_text.
    """
    await query.answer()

    days_21 = compute_21days()
    if day_index < 0 or day_index >= len(days_21):
        await query.message.delete()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="Ошибка: индекс даты вне диапазона."
        )
        return

    chosen_date = days_21[day_index]
    date_str = chosen_date.strftime('%Y-%m-%d')
    chosen_date_str = chosen_date.strftime("%d.%m.%Y")

    telegram_user_id = query.from_user.id
    api = context.user_data.get('api')

    if not api:
        await query.message.delete()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="Сессия истекла. Пожалуйста, /login заново."
        )
        return

    # Попробуем MЭШ
    try:
        profiles = await api.get_users_profile_info()
        profile_id = profiles[0].id

        family = await api.get_family_profile(profile_id=profile_id)
        mes_role = family.profile.type
        child = family.children[0]
        person_guid = child.contingent_guid

        events = await api.get_events(
            person_id=person_guid,
            mes_role=mes_role,
            begin_date=chosen_date,
            end_date=chosen_date
        )
        mesh_lessons = [
            ev for ev in events.response
            if ev.subject_name and ev.start_at and ev.finish_at
        ]
        lessons = mesh_lessons

    except Exception as e:
        logger.error(f"MЭШ недоступен: {e}")
        # fallback
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('''
            SELECT lesson_id, subject_name, start_time, end_time,
                   homework_text, room_number, lesson_theme
            FROM schedule
            WHERE user_id=? AND date=?
            ORDER BY start_time
        ''', (telegram_user_id, date_str))
        rows = cur.fetchall()
        conn.close()

        class FakeEvent: pass
        lessons = []
        for (lid, subj, st, et, hw_text, r_num, l_theme) in rows:
            fe = FakeEvent()

            fe.id = lid
            fe.subject_name = subj
            fe.start_at = datetime.strptime(st, '%H:%M') if st else None
            fe.finish_at = datetime.strptime(et, '%H:%M') if et else None
            fe.homework_text = hw_text

            # <-- ВАЖНО: сохраняем колонку room_number в fe.room_number
            fe.room_number = r_num if r_num else None
            # <-- Сохраняем lesson_theme
            fe.lesson_theme = l_theme if l_theme else None

            # при желании: fe.materials = None
            lessons.append(fe)

    if not lessons:
        await query.message.delete()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"Нет расписания на {date_str} (MЭШ или локальные данные отсутствуют)."
        )
        return

    # Формируем inline-кнопки
    keyboard = []
    for idx, ev in enumerate(lessons):
        if getattr(ev, "start_at", None):
            st_t = ev.start_at.strftime('%H:%M')
        else:
            st_t = "--:--"
        if getattr(ev, "finish_at", None):
            et_t = ev.finish_at.strftime('%H:%M')
        else:
            et_t = "--:--"
        subj = ev.subject_name or '---'
        btn_txt = f"{st_t}-{et_t} {subj}"
        callback_data = f"lesson_{idx}"
        keyboard.append([InlineKeyboardButton(btn_txt, callback_data=callback_data)])

    keyboard.append([InlineKeyboardButton("Вернуться к расписанию", callback_data='back_to_schedule')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    context.user_data['lessons'] = lessons

    # Удаляем старое сообщение и отправляем 2.jpg => "Выберите урок на ..."
    await query.message.delete()
    with open("bot/photo/2.jpg", "rb") as f:
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=f,
            caption=f"Выберите урок на {chosen_date_str}:",
            reply_markup=reply_markup
        )


async def lesson_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Когда пользователь выбрал конкретный урок (lesson_X).
    Показываем домашку. Если fallback => event.homework_text, иначе event.homework.descriptions.
    """
    query = update.callback_query
    await query.answer()
    data = query.data

    lessons = context.user_data.get('lessons')
    lesson_index = int(data.split('_')[1])
    event = lessons[lesson_index]

    # Собираем сообщение
    start_time = getattr(event, 'start_at', None)
    finish_time = getattr(event, 'finish_at', None)
    st_str = start_time.strftime('%H:%M') if start_time else 'Не указано'
    et_str = finish_time.strftime('%H:%M') if finish_time else 'Не указано'
    subject = getattr(event, 'subject_name', 'Не указано')
    room = getattr(event, 'room_number', 'Не указан')
    theme = getattr(event, 'lesson_theme', 'Не указана')

    message = (
        f"⏰ {st_str}-{et_str}\n"
        f"📚 Предмет: {subject}\n"
        f"🚪 Кабинет: {room}\n"
        f"📖 Тема урока: {theme}\n"
    )

    # ДЗ: если fallback => event.homework_text, иначе event.homework.descriptions
    fallback_hw = getattr(event, 'homework_text', None)
    if fallback_hw is not None:
        # fallback
        fallback_hw = fallback_hw.strip()
        if fallback_hw:
            message += "📝 Домашнее задание:\n" + fallback_hw + "\n"
        else:
            message += "📝 Домашнее задание: нет\n"
    else:
        # normal event
        if getattr(event, "homework", None) and event.homework.descriptions:
            message += "📝 Домашнее задание:\n"
            for desc in event.homework.descriptions:
                message += f"- {desc}\n"
        else:
            message += "📝 Домашнее задание: нет\n"

    # ЦДЗ
    has_cdz = False
    if fallback_hw is None:
        # normal event => check event.materials
        if getattr(event, "materials", None):
            has_cdz = True
    # (Если fallback, materials=None => has_cdz=False, либо вы можете хранить info)

    if has_cdz:
        message += "💻 Учитель прикрепил ЦДЗ к ДЗ.\n"

    keyboard = [
        [InlineKeyboardButton("Вернуться к урокам", callback_data='back_to_lessons')],
        [InlineKeyboardButton("Вернуться к расписанию", callback_data='back_to_schedule')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.delete()
    with open("bot/photo/3.jpg", "rb") as f:
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=f,
            caption=message,
            reply_markup=reply_markup
        )


async def back_to_lessons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Возврат к списку уроков => прикрепляем 3.jpg + "Выберите урок:"
    """
    query = update.callback_query
    await query.answer()

    lessons = context.user_data.get('lessons')
    if not lessons:
        await query.message.delete()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text='Ошибка: список уроков не найден.'
        )
        return

    # Генерируем inline-кнопки по урокам
    keyboard = []
    for idx, ev in enumerate(lessons):
        st_t = ev.start_at.strftime('%H:%M') if ev.start_at else '--:--'
        et_t = ev.finish_at.strftime('%H:%M') if ev.finish_at else '--:--'
        subj = ev.subject_name or '---'
        btn_txt = f"{st_t}-{et_t} {subj}"
        callback_data = f"lesson_{idx}"
        keyboard.append([InlineKeyboardButton(btn_txt, callback_data=callback_data)])

    keyboard.append([InlineKeyboardButton("Вернуться к расписанию", callback_data='back_to_schedule')])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.delete()
    with open("bot/photo/2.jpg", "rb") as f:
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=f,
            caption="Выберите урок:",
            reply_markup=reply_markup
        )


async def back_to_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Возвращаемся к календарю => 1.jpg
    """
    query = update.callback_query
    await query.answer()

    markup = generate_calendar_keyboard(offset=7)
    await query.message.delete()
    with open("bot/photo/1.jpg", "rb") as f:
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=f,
            caption="Выберите дату",
            reply_markup=markup
        )


async def delete_my_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Удаляем зашифрованный токен из таблицы users + очищаем context
    """
    query = update.callback_query
    await query.answer()

    telegram_user_id = update.effective_user.id
    delete_user_data(telegram_user_id)
    context.user_data.clear()

    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text='Ваши данные удалены. Используйте /start, чтобы начать заново.'
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Отмена ConversationHandler (логин).
    """
    await update.message.reply_text(
        "Операция отменена. Введите /start для нового начала.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END
