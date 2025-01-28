# main.py (СИНХРОННЫЙ вариант с run_polling)

import logging
from datetime import date, timedelta
from telegram.ext import ApplicationBuilder
from bot.handlers import setup_handlers
from bot.database import init_db, init_schedule_db
from config import settings
# APScheduler (не async, а background)
from apscheduler.schedulers.background import BackgroundScheduler


def main():
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger(__name__)

    init_db()
    init_schedule_db()

    application = ApplicationBuilder().token(f"{settings.TELEGRAM_TOKEN}").build()

    setup_handlers(application)

    # Сбрасываем вебхук
    application.bot.delete_webhook(drop_pending_updates=True)

    # Создаём BackgroundScheduler (не async)
    sched = BackgroundScheduler()
    from datetime import datetime

    sched.add_job(
        update_all_schedules,
        'interval',
        seconds=3600,
        next_run_time=datetime.now()  # Выполнить прямо сейчас
    )

    sched.start()

    logger.info("Запускаем run_polling() ...")
    application.run_polling()  # <-- СИНХРОННЫЙ вызов
    # Когда run_polling() завершится (например, Ctrl+C), идёт выход из main().

    logger.info("Stopping APScheduler...")
    sched.shutdown()
def update_all_schedules():
    """
    Функция, которую APScheduler будет вызывать раз в час.
    Внимание: теперь она обычная (sync) или внутри можно вызывать .run_until_complete(async...) при необходимости.
    """
    import asyncio
    import logging
    logger = logging.getLogger(__name__)

    from bot.database import get_db_connection, clear_user_schedule, save_events_in_db
    from bot.auth import decrypt_token
    from octodiary.apis import AsyncMobileAPI
    from octodiary.urls import Systems

    logger.info("Начинаем обновление расписаний (BackgroundScheduler)...")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT telegram_user_id, encrypted_token FROM users")
    rows = cur.fetchall()
    conn.close()

    # Поскольку внутри хотим вызвать async методы, делаем маленький вспомогательный "event_loop"
    loop = asyncio.new_event_loop()

    for (tg_id, enc_token) in rows:
        if not enc_token:
            continue
        try:
            token_data = decrypt_token(enc_token)
            mesh_api = AsyncMobileAPI(system=Systems.MES)
            mesh_api.token = token_data

            async def fetch_events():
                profiles = await mesh_api.get_users_profile_info()
                if not profiles:
                    logger.warning(f"Нет профилей у {tg_id}. Пропускаем.")
                    return None

                first_profile = profiles[0]
                fam = await mesh_api.get_family_profile(profile_id=first_profile.id)
                if not fam.children:
                    logger.warning(f"У пользователя {tg_id} нет children. Пропускаем.")
                    return None

                child = fam.children[0]
                person_guid = child.contingent_guid
                mes_role = fam.profile.type

                begin_date = date.today() - timedelta(days=10)
                end_date = date.today() + timedelta(days=10)

                events = await mesh_api.get_events(
                    person_id=person_guid,
                    mes_role=mes_role,
                    begin_date=begin_date,
                    end_date=end_date
                )
                return events

            # вызываем корутину fetch_events() в нашем временном event loop
            events = loop.run_until_complete(fetch_events())
            if events:
                clear_user_schedule(tg_id)
                save_events_in_db(tg_id, events)
                logger.info(f"Успешно обновили расписание user_id={tg_id}.")
        except Exception as e:
            logger.warning(f"Ошибка при обновлении расписания user_id={tg_id}: {e}")

    loop.close()
    logger.info("Глобальное обновление расписаний завершено.")

if __name__ == "__main__":
    main()
