# bot/utils.py

from datetime import date, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS_RU    = ["янв", "фев", "мар", "апр", "май", "июн",
                "июл", "авг", "сен", "окт", "ноя", "дек"]

def compute_21days():
    """
    Возвращает список из 21 date:
      - прошлая неделя (7 дней),
      - текущая неделя (7 дней),
      - следующая неделя (7 дней).

    Итого 21 день:
      Индексы  0..6   => прошлая неделя
               7..13 => текущая
               14..20=> следующая
    """
    today = date.today()
    # Понедельник текущей недели
    current_monday = today - timedelta(days=today.weekday())
    # Понедельник предыдущей недели
    start_date = current_monday - timedelta(days=7)

    days_21 = [start_date + timedelta(days=i) for i in range(21)]
    return days_21

def generate_calendar_keyboard(offset: int = 0) -> InlineKeyboardMarkup:
    """
    Создаёт инлайн-клавиатуру, показывающую максимум 5 дат
    из общего списка 21 дня (compute_21days()).

    offset - номер первого дня (индекс в days_21), по умолчанию 0
    (можно указать 7, чтобы сразу показать «текущую неделю»)

    Клавиатура:
      - Первая строка: день недели ("Пн", "Вт"...), до 5 столбцов
      - Вторая строка: число+месяц
      - Третья строка: кнопки "Назад"/"Вперёд"

    callback_data:
      "cal21_day_X"     -> пользователь выбрал день (X=0..20)
      "cal21_prev_OFF"  -> смещение offset -= 5
      "cal21_next_OFF"  -> смещение offset += 5
    """

    days_21 = compute_21days()

    # Гарантируем, что offset не вышел за границы (0..20)
    if offset < 0:
        offset = 0
    if offset >= 21:
        offset = 0

    # Покажем 5 дат начиная с offset
    slice_end = min(offset + 5, 21)  # не больше 21
    slice_days = days_21[offset:slice_end]

    # 1) Строка дней недели
    header_row = []
    # 2) Строка дат
    date_row = []

    for i, day_ in enumerate(slice_days):
        wd_name = WEEKDAYS_RU[day_.weekday()]  # "Пн", "Вт"...
        header_row.append(InlineKeyboardButton(
            wd_name,
            callback_data="ignore"  # не кликабельно
        ))
        # "DD MMM"
        m_name = MONTHS_RU[day_.month - 1]
        label = f"{day_.day} {m_name}"

        global_index = offset + i  # индекс в 0..20
        date_row.append(InlineKeyboardButton(
            label,
            callback_data=f"cal21_day_{global_index}"
        ))

    keyboard = [header_row, date_row]

    # Кнопки "Назад"/"Вперёд"
    nav_row = []
    if offset > 0:
        nav_row.append(InlineKeyboardButton(
            "« Назад",
            callback_data=f"cal21_prev_{offset}"
        ))
    else:
        nav_row.append(InlineKeyboardButton(" ", callback_data="ignore"))

    if slice_end < 21:
        nav_row.append(InlineKeyboardButton(
            "Вперёд »",
            callback_data=f"cal21_next_{offset}"
        ))
    else:
        nav_row.append(InlineKeyboardButton(" ", callback_data="ignore"))

    keyboard.append(nav_row)

    return InlineKeyboardMarkup(keyboard)