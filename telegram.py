"""
=============================================================================
Система геймифицированной эмоциональной поддержки «Хвалебный бот»
(PraiseMe Inc. — Лабораторные работы № 1, 2, 3)
=============================================================================
В этом одном файле реализованы:
1. БД из 6 таблиц (SQLite в 1 файл, не требует установки сервера БД):
   Characters, Phrases, Users, Deeds, Certificates, Crisis_Logs.
2. Подсистема Клиента (US-1 — US-7):
   - Прием бытовых дел и мгновенная похвала голосом персонажа.
   - Смена персонажа («Бабушка» / «Дарт Вейдер») в 1 клик.
   - Генерация именного PDF-сертификата с поддержкой русского языка.
   - Опрос состояния по шкале 1–10 (по расписанию и по кнопке) + реакция на 9–10.
   - Генерация PNG-графика динамики и недельного отчета.
   - Полное удаление своих данных (право на забвение, п. 4.2 ТЗ).
3. Подсистема Администратора (US-8 — US-10):
   - Обезличенная статистика (без доступа к личным оценкам кризиса).
   - Блокировка и разблокировка пользователей по Telegram ID.
   - Выгрузка системного журнала событий и ошибок (bot_events.log).
4. Подсистема Мастера / Основателя-психолога (US-11 — US-13):
   - Добавление и отключение фраз персонажей без изменения кода.
   - Настройка времени ежедневного опроса и текста помощи при уровнях 9–10.
   - Анонимная сводная аналитика кризисов для отчета перед инвесторами.

ИНСТРУКЦИЯ ПО ЗАПУСКУ:
1. Установите библиотеки одной командой в терминале:
   pip install aiogram matplotlib apscheduler
2. Вставьте ваш токен от @BotFather в переменную BOT_TOKEN ниже (строка 51)
   либо просто запустите скрипт — он сам спросит токен в консоли.
3. Запустите бота:
   python praise_bot.py
4. Чтобы протестировать панели Мастера и Администратора со своего аккаунта,
   отправьте боту команду /demo_role (она позволяет переключать вашу роль).
=============================================================================
"""

import asyncio
import io
import logging
import os
import random
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta

# Настройка matplotlib для работы на сервере без монитора (Генерация PDF и PNG)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import os
from dotenv import load_dotenv

# Загружаем переменные окружения из файла .env
load_dotenv()

# Получаем токен
BOT_TOKEN = os.getenv("BOT_TOKEN")



# Список Telegram ID администраторов и мастеров по умолчанию (можно оставить пустым
# и включить себе права прямо в боте командой /demo_role)
ADMIN_IDS: list[int] = []
MASTER_IDS: list[int] = []

DB_PATH = "praise_bot.db"
LOG_PATH = "bot_events.log"

# Настройка журналирования событий и ошибок (US-10)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("PraiseMeBot")


# =============================================================================
# 2. СЛОЙ БАЗЫ ДАННЫХ (6 СУЩНОСТЕЙ ИЗ ER-ДИАГРАММЫ ЛР № 3 + НАСТРОЙКИ)
# =============================================================================

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db() -> None:
    """Создает 6 таблиц по ER-диаграмме и заполняет стартовыми данными."""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Таблица 1: Characters (Персонажи)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS Characters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL
            );
        """)

        # Таблица 2: Phrases (Шаблоны фраз похвалы)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS Phrases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id INTEGER NOT NULL,
                phrase_text TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (character_id) REFERENCES Characters(id) ON DELETE CASCADE
            );
        """)

        # Таблица 3: Users (Обезличенные пользователи — без ФИО согласно п. 4.2 ТЗ)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS Users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL UNIQUE,
                nickname TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'client',
                character_id INTEGER NOT NULL DEFAULT 1,
                is_blocked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (character_id) REFERENCES Characters(id)
            );
        """)

        # Таблица 4: Deeds (Выполненные бытовые дела)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS Deeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                phrase_id INTEGER NOT NULL,
                deed_title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES Users(id) ON DELETE CASCADE,
                FOREIGN KEY (phrase_id) REFERENCES Phrases(id)
            );
        """)

        # Таблица 5: Certificates (Реестр выданных PDF-сертификатов)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS Certificates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deed_id INTEGER NOT NULL UNIQUE,
                cert_number TEXT NOT NULL UNIQUE,
                issued_at TEXT NOT NULL,
                FOREIGN KEY (deed_id) REFERENCES Deeds(id) ON DELETE CASCADE
            );
        """)

        # Таблица 6: Crisis_Logs (Журнал трекера состояния 1–10)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS Crisis_Logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                score INTEGER NOT NULL CHECK(score BETWEEN 1 AND 10),
                is_critical INTEGER NOT NULL DEFAULT 0,
                survey_date TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES Users(id) ON DELETE CASCADE
            );
        """)

        # Служебная таблица настроек Мастера (время опроса и кризисный текст 9-10)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS Settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

        # Заполнение персонажей по умолчанию
        cursor.execute("SELECT COUNT(*) as cnt FROM Characters;")
        if cursor.fetchone()["cnt"] == 0:
            cursor.executemany(
                "INSERT INTO Characters (id, name, description) VALUES (?, ?, ?);",
                [
                    (1, "Бабушка", "Заботливая, теплая поддержка с пирожками и любовью"),
                    (2, "Дарт Вейдер", "Торжественная галактическая похвала с эпичным юмором"),
                ],
            )

        # Заполнение стартовых шаблонов фраз (с плейсхолдером {deed})
        cursor.execute("SELECT COUNT(*) as cnt FROM Phrases;")
        if cursor.fetchone()["cnt"] == 0:
            initial_phrases = [
                # Фразы Бабушки (character_id = 1)
                (1, "Золотце моё, как же ты славно справился с делом «{deed}»! Я тобой так горжусь, иди обниму и налью чаю с малиной!"),
                (1, "Солнышко, сделать «{deed}» — это настоящий подвиг! Ты у меня самый трудолюбивый, возьми тёплый пирожок с полки!"),
                (1, "Родной мой человек, даже маленькое дело «{deed}» делает день светлее. Отдохни немного, ты это заслужил!"),
                (1, "Умница моя! Пока другие ленятся, ты берёшь и делаешь «{deed}». Бабушка всегда в тебя верила!"),
                # Фразы Дарта Вейдера (character_id = 2)
                (2, "Твоя мощь в деле «{deed}» впечатляет. Даже Император отметил твою дисциплину. Сила определенно течет в тебе!"),
                (2, "Я ощущаю великое возмущение в рутине: ты сокрушил задачу «{deed}» точнее, чем Звезда Смерти! Империя гордится тобой."),
                (2, "Недооценивать важность миссии «{deed}» было бы ошибкой. Ты блестяще доказал своё превосходство над хаосом и прокрастинацией!"),
                (2, "Твоя выдержка при выполнении «{deed}» достойна звания Лорда Ситхов. Переходи на сторону продуктивности — у нас есть сертификаты!"),
            ]
            cursor.executemany(
                "INSERT INTO Phrases (character_id, phrase_text, is_active) VALUES (?, ?, 1);",
                initial_phrases,
            )

        # Настройки по умолчанию для Мастера-психолога
        default_crisis_msg = (
            "💛 <b>Мы видим, что сейчас вам очень непросто (уровень {score}/10).</b>\n\n"
            "Пожалуйста, помните: вы не обязаны справляться с этим в одиночку, "
            "и ваши переживания абсолютно важны.\n"
            "<i>Хвалебный бот — это поддерживающий игровой помощник, но он не заменяет врача или психотерапевта.</i>\n\n"
            "📞 <b>Рекомендуем бережно обратиться к специалисту:</b>\n"
            "• Единый телефон доверия психологической помощи: <code>8-800-200-47-03</code> (круглосуточно, бесплатно, анонимно)\n"
            "• Психологическая служба или ваш лечащий специалист.\n"
            "Пожалуйста, позаботьтесь о себе сегодня — даже стакан воды и отдых уже большое дело."
        )
        cursor.execute(
            "INSERT OR IGNORE INTO Settings (key, value) VALUES ('survey_time', '20:00');"
        )
        cursor.execute(
            "INSERT OR IGNORE INTO Settings (key, value) VALUES ('crisis_message', ?);",
            (default_crisis_msg,),
        )
        conn.commit()
    logger.info("База данных успешно инициализирована (6 таблиц ER-схемы готовы).")


# Вспомогательные функции работы с БД
def get_or_create_user(telegram_id: int, username: str | None) -> sqlite3.Row:
    nickname = f"@{username}" if username else f"Герой #{str(telegram_id)[-4:]}"
    default_role = "client"
    if telegram_id in MASTER_IDS:
        default_role = "master"
    elif telegram_id in ADMIN_IDS:
        default_role = "admin"

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM Users WHERE telegram_id = ?;", (telegram_id,))
        row = cur.fetchone()
        if not row:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                """
                INSERT INTO Users (telegram_id, nickname, role, character_id, is_blocked, created_at)
                VALUES (?, ?, ?, 1, 0, ?);
                """,
                (telegram_id, nickname, default_role, now_str),
            )
            conn.commit()
            logger.info(f"Зарегистрирован новый пользователь telegram_id={telegram_id}")
            cur.execute("SELECT * FROM Users WHERE telegram_id = ?;", (telegram_id,))
            row = cur.fetchone()
        return row


def get_setting(key: str) -> str:
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM Settings WHERE key = ?;", (key,))
        row = cur.fetchone()
        return row["value"] if row else ""


def set_setting(key: str, value: str) -> None:
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO Settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value;",
            (key, value),
        )
        conn.commit()


# =============================================================================
# 3. ГЕНЕРАТОР ИМЕННЫХ PDF-СЕРТИФИКАТОВ И PNG-ГРАФИКОВ (БЕЗ ПРОБЛЕМ С КИРИЛЛИЦЕЙ)
# =============================================================================

def generate_pdf_certificate(
    nickname: str,
    deed_title: str,
    character_name: str,
    praise_text: str,
    cert_number: str,
    issued_at: str,
) -> bytes:
    """
    Генерирует красивый PDF-сертификат достижения в формате А4 альбомной ориентации.
    Использует векторный рендеринг Matplotlib PDF backend (полная поддержка кириллицы).
    """
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Arial", "Segoe UI", "Tahoma"]
    plt.rcParams["axes.unicode_minus"] = False

    fig = plt.figure(figsize=(11.69, 8.27), dpi=150)  # A4 Landscape
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    # Цветовая схема в зависимости от выбранного персонажа
    if character_name == "Бабушка":
        bg_color = "#FFFBEB"       # Теплый кремовый
        border_main = "#D97706"    # Янтарно-золотой
        border_inner = "#F59E0B"
        title_color = "#78350F"
        accent_color = "#B45309"
        badge_title = "ОРДЕН БАБУШКИНОЙ ГОРДОСТИ И ТЕПЛЫХ ПИРОЖКОВ"
    else:
        bg_color = "#0F172A"       # Глубокий космический темный
        border_main = "#DC2626"    # Ситхский красный
        border_inner = "#475569"
        title_color = "#F8FAFC"
        accent_color = "#EF4444"
        badge_title = "ВЫСШИЙ ИМПЕРСКИЙ ДЕКРЕТ СИЛЫ"

    # Заливка фона
    fig.patch.set_facecolor(bg_color)
    ax.add_patch(plt.Rectangle((0, 0), 100, 100, facecolor=bg_color))

    # Декоративные рамки сертификата
    outer_box = FancyBboxPatch(
        (4, 5), 92, 90,
        boxstyle="round,pad=0.6,rounding_size=2.0",
        ec=border_main, fc="none", lw=4.5
    )
    inner_box = FancyBboxPatch(
        (6, 7), 88, 86,
        boxstyle="round,pad=0.3,rounding_size=1.2",
        ec=border_inner, fc="none", lw=1.8, linestyle="--"
    )
    ax.add_patch(outer_box)
    ax.add_patch(inner_box)

    # Шапка сертификата
    ax.text(
        50, 86, "ПСИХОЛОГИЧЕСКИЙ СТАРТАП «PRAISEME INC.» • ХВАЛЕБНЫЙ БОТ",
        ha="center", va="center", fontsize=11, fontweight="bold", color=accent_color
    )
    ax.text(
        50, 78, "СЕРТИФИКАТ ДОСТИЖЕНИЯ",
        ha="center", va="center", fontsize=28, fontweight="bold", color=title_color
    )
    ax.text(
        50, 71, badge_title,
        ha="center", va="center", fontsize=12, fontweight="bold", color=accent_color
    )

    # Кому выдан
    ax.text(
        50, 62, "Настоящим документом официально подтверждается, что",
        ha="center", va="center", fontsize=13, color=title_color
    )
    ax.text(
        50, 54, nickname,
        ha="center", va="center", fontsize=22, fontweight="bold", color=accent_color
    )

    # За какое дело
    short_deed = deed_title if len(deed_title) <= 65 else deed_title[:62] + "..."
    ax.text(
        50, 45, "блестяще преодолел(а) бытовую рутину и выполнил(а) дело:",
        ha="center", va="center", fontsize=13, color=title_color
    )
    ax.text(
        50, 37, f"«{short_deed}»",
        ha="center", va="center", fontsize=18, fontweight="bold", color=title_color
    )

    # Текст похвалы персонажа (с переносом по словам)
    words = praise_text.split()
    lines, current_line = [], ""
    for w in words:
        if len(current_line) + len(w) + 1 <= 75:
            current_line = f"{current_line} {w}".strip()
        else:
            lines.append(current_line)
            current_line = w
    if current_line:
        lines.append(current_line)
    wrapped_praise = "\n".join(lines[:3])

    ax.text(
        50, 25, wrapped_praise,
        ha="center", va="center", fontsize=11.5, style="italic", color=title_color
    )

    # Подвал сертификата: номер, дата, персонаж
    ax.plot([12, 88], [16, 16], color=border_inner, lw=1.2)
    ax.text(
        12, 11.5, f"Регистрационный номер: {cert_number}",
        ha="left", va="center", fontsize=10.5, fontweight="bold", color=title_color
    )
    ax.text(
        50, 11.5, f"Дата выдачи: {issued_at}",
        ha="center", va="center", fontsize=10.5, color=title_color
    )
    ax.text(
        88, 11.5, f"Заверено персонажем: {character_name}",
        ha="right", va="center", fontsize=10.5, fontweight="bold", color=accent_color
    )

    buf = io.BytesIO()
    fig.savefig(buf, format="pdf", facecolor=bg_color)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def generate_crisis_chart_png(logs: list[sqlite3.Row], nickname: str) -> bytes:
    """
    Строит PNG-график динамики эмоционального состояния пользователя (US-5).
    """
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Arial", "Segoe UI"]
    plt.rcParams["axes.unicode_minus"] = False

    dates = [row["survey_date"][5:] for row in logs]  # MM-DD
    scores = [row["score"] for row in logs]

    fig, ax = plt.subplots(figsize=(8.5, 4.8), dpi=130)
    fig.patch.set_facecolor("#F8FAFC")
    ax.set_facecolor("#FFFFFF")

    # Цветовые зоны (зеленая 1-4, желтая 5-8, красная кризисная 9-10)
    ax.axhspan(1, 4.5, color="#DCFCE7", alpha=0.55, label="Спокойное состояние (1–4)")
    ax.axhspan(4.5, 8.5, color="#FEF9C3", alpha=0.55, label="Накопление усталости (5–8)")
    ax.axhspan(8.5, 10.3, color="#FEE2E2", alpha=0.65, label="Острый кризис / выгорание (9–10)")

    ax.plot(
        range(len(scores)), scores,
        marker="o", markersize=8, color="#2563EB", linewidth=2.6, label="Уровень состояния"
    )

    for idx, val in enumerate(scores):
        ax.annotate(
            str(val),
            (idx, val),
            textcoords="offset points",
            xytext=(0, 8),
            ha="center",
            fontsize=10,
            fontweight="bold",
            color="#0F172A",
        )

    ax.set_xticks(range(len(dates)))
    ax.set_xticklabels(dates, rotation=25, fontsize=9.5)
    ax.set_yticks(range(1, 11))
    ax.set_ylim(0.5, 10.8)
    ax.grid(True, linestyle="--", alpha=0.5)

    ax.set_title(f"Динамика эмоционального состояния ({nickname})", fontsize=13, fontweight="bold", pad=12)
    ax.set_xlabel("Дата опроса (ММ-ДД)", fontsize=10.5)
    ax.set_ylabel("Шкала кризиса (1 — отлично, 10 — критично)", fontsize=10.5)
    ax.legend(loc="upper left", fontsize=8.5, framealpha=0.9)

    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# =============================================================================
# 4. КЛАВИАТУРЫ И СОСТОЯНИЯ (FSM) — ВСЕ ДЕЙСТВИЯ ДОСТУПНЫ ЗА <= 2 НАЖАТИЯ (П. 4.3 ТЗ)
# =============================================================================

class BotStates(StatesGroup):
    waiting_nickname = State()
    master_adding_phrase_text = State()
    master_setting_time = State()
    master_setting_crisis_text = State()
    admin_blocking_user = State()


def get_main_reply_kb(role: str = "client") -> ReplyKeyboardMarkup:
    rows = [
        [
            KeyboardButton(text="🎭 Сменить персонажа"),
            KeyboardButton(text="📊 Оценить состояние (1–10)"),
        ],
        [
            KeyboardButton(text="📈 Мой график и отчёт"),
            KeyboardButton(text="✏️ Сменить псевдоним"),
        ],
        [
            KeyboardButton(text="🗑 Удалить мои данные"),
            KeyboardButton(text="❓ Справка (/help)"),
        ],
    ]
    if role == "admin":
        rows.append([KeyboardButton(text="🛡 Панель Администратора")])
    elif role == "master":
        rows.append([KeyboardButton(text="🧠 Панель Мастера (Психолога)")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def get_character_inline_kb(current_char_id: int) -> InlineKeyboardMarkup:
    b1_prefix = "✅ " if current_char_id == 1 else ""
    b2_prefix = "✅ " if current_char_id == 2 else ""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{b1_prefix}👵 Бабушка (Забота)", callback_data="set_char:1"
                ),
                InlineKeyboardButton(
                    text=f"{b2_prefix}⚔️ Дарт Вейдер (Сила)", callback_data="set_char:2"
                ),
            ]
        ]
    )


def get_survey_inline_kb() -> InlineKeyboardMarkup:
    """Клавиатура ежедневного опроса состояния от 1 до 10 одной кнопкой (US-4)."""
    row1 = [
        InlineKeyboardButton(text=str(i), callback_data=f"crisis_score:{i}")
        for i in range(1, 6)
    ]
    row2 = [
        InlineKeyboardButton(text=str(i), callback_data=f"crisis_score:{i}")
        for i in range(6, 11)
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row1, row2])


# =============================================================================
# 5. ОБРАБОТЧИКИ КОМАНД И СЦЕНАРИЕВ (ROUTER)
# =============================================================================

router = Router()


def check_is_blocked(user_row: sqlite3.Row) -> bool:
    return bool(user_row["is_blocked"])


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = get_or_create_user(message.from_user.id, message.from_user.username)
    if check_is_blocked(user):
        await message.answer("⛔ Ваш аккаунт заблокирован администратором за нарушение правил.")
        return

    with get_db_connection() as conn:
        char = conn.execute(
            "SELECT name FROM Characters WHERE id = ?;", (user["character_id"],)
        ).fetchone()

    welcome_text = (
        f"👋 Привет, <b>{user['nickname']}</b>!\n\n"
        "Я — <b>«Хвалебный бот»</b> от стартапа <i>PraiseMe Inc.</i>\n"
        "Моя цель — поддерживать тебя в бытовой рутине, превращать дела в игру и бережно отслеживать твоё эмоциональное состояние.\n\n"
        f"🎭 Твой текущий персонаж: <b>{char['name']}</b>\n\n"
        "✨ <b>Как мной пользоваться:</b>\n"
        "1️⃣ Просто <b>напиши мне любое выполненное дело</b> (например: <i>«Я помыл посуду»</i>, <i>«Сделал лабу по физике»</i>, <i>«Вынес мусор»</i>) — и я мгновенно похвалю тебя!\n"
        "2️⃣ Под каждой похвалой будет кнопка <b>«📜 Получить PDF-сертификат»</b>.\n"
        "3️⃣ Нажимай <b>«📊 Оценить состояние (1–10)»</b> для отслеживания выгорания.\n\n"
        "🔧 <i>Для демонстрации на защите лабы: отправь /demo_role чтобы переключить себе роль Клиент / Администратор / Мастер-психолог!</i>"
    )
    await message.answer(
        welcome_text,
        parse_mode="HTML",
        reply_markup=get_main_reply_kb(user["role"]),
    )


@router.message(Command("help"))
@router.message(F.text == "❓ Справка (/help)")
async def cmd_help(message: Message) -> None:
    user = get_or_create_user(message.from_user.id, message.from_user.username)
    help_text = (
        "📖 <b>Справка по системе «Хвалебный бот» (п. 4.3 ТЗ)</b>\n\n"
        "• <b>Написать любое текстовое сообщение</b> — получить мгновенную похвалу от выбранного персонажа (US-1).\n"
        "• <b>🎭 Сменить персонажа</b> — переключение между «Бабушкой» и «Дартом Вейдером» (US-2).\n"
        "• <b>📜 Кнопка под похвалой</b> — генерация именного PDF-сертификата за дело (US-3).\n"
        "• <b>📊 Оценить состояние (1–10)</b> — ежедневный трекер экзистенциального кризиса (US-4, US-6).\n"
        "• <b>📈 Мой график и отчёт</b> — PNG-график динамики состояния и статистика за 7 дней (US-5).\n"
        "• <b>🗑 Удалить мои данные</b> — полное удаление ваших записей из БД (US-7).\n\n"
        "🔐 <b>Приватность:</b> Бот не хранит ваши ФИО — только ID Telegram и выбранный вами псевдоним.\n"
        "⚙️ <b>Служебные команды для защиты:</b>\n"
        "• /demo_role — переключить свою роль (Клиент / Админ / Мастер)\n"
        "• /admin — открыть панель Администратора\n"
        "• /master — открыть панель Мастера-психолога"
    )
    await message.answer(help_text, parse_mode="HTML", reply_markup=get_main_reply_kb(user["role"]))


# -----------------------------------------------------------------------------
# ПЕРЕКЛЮЧАТЕЛЬ РОЛЕЙ ДЛЯ ЗАЩИТЫ ЛАБОРАТОРНОЙ РАБОТЫ (/demo_role)
# -----------------------------------------------------------------------------
@router.message(Command("demo_role"))
async def cmd_demo_role(message: Message) -> None:
    user = get_or_create_user(message.from_user.id, message.from_user.username)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👤 Роль: Клиент (Пользователь)", callback_data="set_role:client")],
            [InlineKeyboardButton(text="🛡 Роль: Администратор (US-8..10)", callback_data="set_role:admin")],
            [InlineKeyboardButton(text="🧠 Роль: Мастер-психолог (US-11..13)", callback_data="set_role:master")],
        ]
    )
    await message.answer(
        f"Текущая системная роль вашего аккаунта: <b>{user['role']}</b>\n"
        "Выберите роль для демонстрации преподавателю всех подсистем из ТЗ:",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("set_role:"))
async def cb_set_role(callback: CallbackQuery) -> None:
    new_role = callback.data.split(":")[1]
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE Users SET role = ? WHERE telegram_id = ?;",
            (new_role, callback.from_user.id),
        )
        conn.commit()
    logger.info(f"Пользователь {callback.from_user.id} сменил роль на {new_role}")
    await callback.message.answer(
        f"✅ Ваша роль успешно изменена на: <b>{new_role.upper()}</b>.\n"
        "Клавиатура внизу экрана обновлена!",
        parse_mode="HTML",
        reply_markup=get_main_reply_kb(new_role),
    )
    await callback.answer()


# -----------------------------------------------------------------------------
# US-2: ВЫБОР И СМЕНА ПЕРСОНАЖА («БАБУШКА» / «ДАРТ ВЕЙДЕР»)
# -----------------------------------------------------------------------------
@router.message(F.text == "🎭 Сменить персонажа")
async def menu_change_character(message: Message) -> None:
    user = get_or_create_user(message.from_user.id, message.from_user.username)
    if check_is_blocked(user):
        return
    await message.answer(
        "🎭 <b>Выберите персонажа, чьим голосом бот будет хвалить вас:</b>\n\n"
        "👵 <b>Бабушка</b> — тёплая, душевная забота, пирожки и безусловная любовь.\n"
        "⚔️ <b>Дарт Вейдер</b> — эпичный галактический пафос и признание вашей Тёмной стороны продуктивности.",
        parse_mode="HTML",
        reply_markup=get_character_inline_kb(user["character_id"]),
    )


@router.callback_query(F.data.startswith("set_char:"))
async def cb_set_character(callback: CallbackQuery) -> None:
    char_id = int(callback.data.split(":")[1])
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE Users SET character_id = ? WHERE telegram_id = ?;",
            (char_id, callback.from_user.id),
        )
        conn.commit()
        char = conn.execute("SELECT name, description FROM Characters WHERE id = ?;", (char_id,)).fetchone()

    await callback.message.edit_text(
        f"✅ Персонаж успешно изменён!\n\n"
        f"🎭 Теперь вас хвалит: <b>{char['name']}</b>\n"
        f"<i>({char['description']})</i>\n\n"
        "Напишите о любом выполненном деле, чтобы проверить!",
        parse_mode="HTML",
        reply_markup=get_character_inline_kb(char_id),
    )
    await callback.answer(f"Выбран персонаж: {char['name']}")


# -----------------------------------------------------------------------------
# СМЕНА ПСЕВДОНИМА ДЛЯ СЕРТИФИКАТОВ (БЕЗ ХРАНЕНИЯ ФИО — П. 4.2 ТЗ)
# -----------------------------------------------------------------------------
@router.message(F.text == "✏️ Сменить псевдоним")
async def menu_change_nickname(message: Message, state: FSMContext) -> None:
    user = get_or_create_user(message.from_user.id, message.from_user.username)
    if check_is_blocked(user):
        return
    await state.set_state(BotStates.waiting_nickname)
    await message.answer(
        f"Ваш текущий псевдоним для PDF-сертификатов: <b>{user['nickname']}</b>\n\n"
        "Отправьте новый псевдоним (не указывайте полные ФИО для соблюдения анонимности п. 4.2 ТЗ):",
        parse_mode="HTML",
    )


@router.message(BotStates.waiting_nickname)
async def process_new_nickname(message: Message, state: FSMContext) -> None:
    new_nick = (message.text or "").strip()[:35]
    if not new_nick:
        await message.answer("Пожалуйста, отправьте текстовый псевдоним.")
        return
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE Users SET nickname = ? WHERE telegram_id = ?;",
            (new_nick, message.from_user.id),
        )
        conn.commit()
    await state.clear()
    await message.answer(
        f"✅ Отлично! Теперь во всех PDF-сертификатах будет указываться псевдоним: <b>{new_nick}</b>",
        parse_mode="HTML",
    )


# -----------------------------------------------------------------------------
# US-4 и US-6: ОПРОС СОСТОЯНИЯ (1–10) И БЕРЕЖНАЯ РЕАКЦИЯ НА КРИЗИС (9–10)
# -----------------------------------------------------------------------------
@router.message(F.text == "📊 Оценить состояние (1–10)")
async def menu_crisis_survey(message: Message) -> None:
    user = get_or_create_user(message.from_user.id, message.from_user.username)
    if check_is_blocked(user):
        return
    await message.answer(
        "🧘 <b>Ежедневный трекер эмоционального состояния</b>\n\n"
        "Оцените ваш текущий уровень усталости, тревоги или экзистенциального кризиса по шкале от <b>1 до 10</b>:\n"
        "• <b>1–3</b> — чувствую себя бодро и спокойно\n"
        "• <b>4–6</b> — умеренная усталость от рутины\n"
        "• <b>7–8</b> — сильное истощение и выгорание\n"
        "• <b>9–10</b> — критический уровень кризиса, нужна поддержка",
        parse_mode="HTML",
        reply_markup=get_survey_inline_kb(),
    )


@router.callback_query(F.data.startswith("crisis_score:"))
async def cb_crisis_score(callback: CallbackQuery) -> None:
    score = int(callback.data.split(":")[1])
    user = get_or_create_user(callback.from_user.id, callback.from_user.username)
    is_critical = 1 if score >= 9 else 0
    today_str = datetime.now().strftime("%Y-%m-%d")

    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO Crisis_Logs (user_id, score, is_critical, survey_date)
            VALUES (?, ?, ?, ?);
            """,
            (user["id"], score, is_critical, today_str),
        )
        conn.commit()

    logger.info(f"Записан ответ трекера состояния user_id={user['id']}, score={score}, critical={is_critical}")

    if is_critical:
        # US-6: При выборе 9–10 выдаем согласованное с мастером бережное сообщение
        crisis_template = get_setting("crisis_message")
        crisis_text = crisis_template.replace("{score}", str(score))
        await callback.message.edit_text(crisis_text, parse_mode="HTML")
    else:
        await callback.message.edit_text(
            f"✅ <b>Ответ сохранён: {score}/10 за {today_str}.</b>\n\n"
            "Спасибо, что прислушиваетесь к своему состоянию! "
            "Посмотреть динамику можно по кнопке <b>«📈 Мой график и отчёт»</b>.",
            parse_mode="HTML",
        )
    await callback.answer("Оценка сохранена в БД")


# -----------------------------------------------------------------------------
# US-5: ГРАФИК ДИНАМИКИ И НЕДЕЛЬНЫЙ ОТЧЁТ
# -----------------------------------------------------------------------------
@router.message(F.text == "📈 Мой график и отчёт")
async def menu_my_chart(message: Message) -> None:
    user = get_or_create_user(message.from_user.id, message.from_user.username)
    if check_is_blocked(user):
        return

    with get_db_connection() as conn:
        logs = conn.execute(
            """
            SELECT score, is_critical, survey_date
            FROM Crisis_Logs
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 14;
            """,
            (user["id"],),
        ).fetchall()

        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        deeds_cnt = conn.execute(
            "SELECT COUNT(*) as cnt FROM Deeds WHERE user_id = ? AND created_at >= ?;",
            (user["id"], week_ago),
        ).fetchone()["cnt"]

        certs_cnt = conn.execute(
            """
            SELECT COUNT(*) as cnt
            FROM Certificates c
            JOIN Deeds d ON c.deed_id = d.id
            WHERE d.user_id = ?;
            """,
            (user["id"],),
        ).fetchone()["cnt"]

    if not logs:
        await message.answer(
            "📊 У вас пока нет записей в трекере состояния.\n"
            "Нажмите кнопку <b>«📊 Оценить состояние (1–10)»</b>, чтобы сделать первый замер!",
            parse_mode="HTML",
        )
        return

    logs_chronological = list(reversed(logs))
    scores = [r["score"] for r in logs_chronological]
    avg_score = sum(scores) / len(scores)
    min_score, max_score = min(scores), max(scores)

    png_bytes = generate_crisis_chart_png(logs_chronological, user["nickname"])
    photo_file = BufferedInputFile(png_bytes, filename="crisis_dynamics.png")

    caption = (
        f"📈 <b>Ваш недельный отчёт и динамика состояния ({user['nickname']})</b>\n\n"
        f"• Замеров состояния в графике: <b>{len(scores)}</b>\n"
        f"• Средний уровень кризиса: <b>{avg_score:.1f} / 10</b>\n"
        f"• Минимум / Максимум: <b>{min_score}</b> / <b>{max_score}</b>\n"
        f"• Выполнено дел с похвалой за 7 дней: <b>{deeds_cnt}</b>\n"
        f"• Получено именных PDF-сертификатов: <b>{certs_cnt}</b>"
    )
    await message.answer_photo(photo=photo_file, caption=caption, parse_mode="HTML")


# -----------------------------------------------------------------------------
# US-7: УДАЛЕНИЕ ВСЕХ СВОИХ ДАННЫХ (ПРАВО НА ЗАБВЕНИЕ, П. 4.2 ТЗ)
# -----------------------------------------------------------------------------
@router.message(F.text == "🗑 Удалить мои данные")
async def menu_delete_my_data(message: Message) -> None:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚠️ Да, безвозвратно удалить мои данные",
                    callback_data="confirm_delete_self",
                )
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="cancel_delete_self")],
        ]
    )
    await message.answer(
        "🔐 <b>Управление приватностью (US-7, п. 4.2 ТЗ)</b>\n\n"
        "Вы уверены, что хотите полностью удалить свой профиль, историю выполненных дел, "
        "выданные сертификаты и журнал оценок состояния из базы данных бота?",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data == "confirm_delete_self")
async def cb_confirm_delete_self(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM Users WHERE telegram_id = ?;", (callback.from_user.id,))
        conn.commit()
    logger.info(f"Пользователь telegram_id={callback.from_user.id} удалил все свои данные по US-7.")
    await callback.message.edit_text(
        "✅ <b>Все ваши персональные данные, история дел и оценки состояния полностью удалены из БД.</b>\n\n"
        "Если захотите начать заново с чистого листа — отправьте /start.",
        parse_mode="HTML",
    )
    await callback.answer("Данные удалены")


@router.callback_query(F.data == "cancel_delete_self")
async def cb_cancel_delete_self(callback: CallbackQuery) -> None:
    await callback.message.edit_text("👍 Удаление отменено. Ваши достижения в безопасности!")
    await callback.answer()


# =============================================================================
# 6. ПОДСИСТЕМА АДМИНИСТРАТОРА (US-8, US-9, US-10)
# =============================================================================

@router.message(Command("admin"))
@router.message(F.text == "🛡 Панель Администратора")
async def admin_panel(message: Message) -> None:
    user = get_or_create_user(message.from_user.id, message.from_user.username)
    if user["role"] != "admin" and message.from_user.id not in ADMIN_IDS:
        await message.answer(
            "🔒 Доступ к подсистеме администратора ограничен списком разрешённых ID (п. 4.2 ТЗ).\n"
            "💡 <i>Для демонстрации на защите отправьте /demo_role и выберите роль «Администратор».</i>",
            parse_mode="HTML",
        )
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Обезличенная статистика (US-8)", callback_data="admin:stats")],
            [InlineKeyboardButton(text="🚫 Блокировка / Разблокировка (US-9)", callback_data="admin:block_menu")],
            [InlineKeyboardButton(text="📋 Выгрузить журнал событий и ошибок (US-10)", callback_data="admin:logs")],
        ]
    )
    await message.answer(
        "🛡 <b>Подсистема Администратора (п. 3.2 ТЗ)</b>\n"
        "<i>Примечание безопасности (п. 4.2 ТЗ): администратор видит только агрегированную статистику и не имеет доступа к личным оценкам кризиса пользователей.</i>",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data == "admin:stats")
async def cb_admin_stats(callback: CallbackQuery) -> None:
    with get_db_connection() as conn:
        total_users = conn.execute("SELECT COUNT(*) as cnt FROM Users;").fetchone()["cnt"]
        blocked_users = conn.execute("SELECT COUNT(*) as cnt FROM Users WHERE is_blocked = 1;").fetchone()["cnt"]
        active_users = conn.execute("SELECT COUNT(DISTINCT user_id) as cnt FROM Deeds;").fetchone()["cnt"]
        total_deeds = conn.execute("SELECT COUNT(*) as cnt FROM Deeds;").fetchone()["cnt"]
        total_certs = conn.execute("SELECT COUNT(*) as cnt FROM Certificates;").fetchone()["cnt"]
        surveyed_users = conn.execute("SELECT COUNT(DISTINCT user_id) as cnt FROM Crisis_Logs;").fetchone()["cnt"]

    survey_share = (surveyed_users / total_users * 100) if total_users > 0 else 0.0
    text = (
        "📊 <b>Обезличенная статистика использования Бота (US-8):</b>\n\n"
        f"• Зарегистрировано пользователей: <b>{total_users}</b> (заблокировано: {blocked_users})\n"
        f"• Активных пользователей (отправили ≥1 дела): <b>{active_users}</b>\n"
        f"• Выдано текстовых похвал: <b>{total_deeds}</b>\n"
        f"• Сформировано PDF-сертификатов: <b>{total_certs}</b>\n"
        f"• Доля пользователей, прошедших опрос состояния: <b>{survey_share:.1f}%</b> ({surveyed_users} из {total_users})"
    )
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin:block_menu")
async def cb_admin_block_menu(callback: CallbackQuery, state: FSMContext) -> None:
    with get_db_connection() as conn:
        users = conn.execute(
            "SELECT telegram_id, nickname, is_blocked FROM Users ORDER BY id DESC LIMIT 10;"
        ).fetchall()

    users_list = "\n".join(
        [
            f"• ID: <code>{u['telegram_id']}</code> ({u['nickname']}) — {'🚫 Заблокирован' if u['is_blocked'] else '✅ Активен'}"
            for u in users
        ]
    )
    await state.set_state(BotStates.admin_blocking_user)
    await callback.message.answer(
        "🚫 <b>Управление блокировкой пользователей (US-9)</b>\n\n"
        f"Последние пользователи в базе:\n{users_list}\n\n"
        "Отправьте <code>telegram_id</code> пользователя, чтобы переключить его статус (Заблокировать / Разблокировать):",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(BotStates.admin_blocking_user)
async def process_admin_block_user(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await state.clear()
        await message.answer("Ввод отменён (ожидался числовой Telegram ID).")
        return

    target_tid = int(raw)
    with get_db_connection() as conn:
        row = conn.execute("SELECT id, nickname, is_blocked FROM Users WHERE telegram_id = ?;", (target_tid,)).fetchone()
        if not row:
            await message.answer("Пользователь с таким Telegram ID не найден в базе.")
            await state.clear()
            return
        new_status = 0 if row["is_blocked"] else 1
        conn.execute("UPDATE Users SET is_blocked = ? WHERE telegram_id = ?;", (new_status, target_tid))
        conn.commit()

    await state.clear()
    status_str = "ЗАБЛОКИРОВАН 🚫" if new_status == 1 else "РАЗБЛОКИРОВАН ✅"
    logger.warning(f"Администратор {message.from_user.id} изменил статус пользователя {target_tid} на {status_str}")
    await message.answer(
        f"Статус пользователя <b>{row['nickname']}</b> (ID <code>{target_tid}</code>): <b>{status_str}</b>",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin:logs")
async def cb_admin_logs(callback: CallbackQuery) -> None:
    if os.path.exists(LOG_PATH):
        doc = FSInputFile(LOG_PATH, filename="bot_events.log")
        await callback.message.answer_document(
            document=doc,
            caption="📋 Системный журнал событий и ошибок сервера (US-10, п. 3.2.3 ТЗ).",
        )
    else:
        await callback.message.answer("Журнал событий пока пуст.")
    await callback.answer()


# =============================================================================
# 7. ПОДСИСТЕМА МАСТЕРА / ОСНОВАТЕЛЯ-ПСИХОЛОГА (US-11, US-12, US-13)
# =============================================================================

@router.message(Command("master"))
@router.message(F.text == "🧠 Панель Мастера (Психолога)")
async def master_panel(message: Message) -> None:
    user = get_or_create_user(message.from_user.id, message.from_user.username)
    if user["role"] != "master" and message.from_user.id not in MASTER_IDS:
        await message.answer(
            "🔒 Доступ к подсистеме Мастера (основателя-психолога) ограничен (п. 3.3 ТЗ).\n"
            "💡 <i>Для демонстрации на защите отправьте /demo_role и выберите роль «Мастер-психолог».</i>",
            parse_mode="HTML",
        )
        return

    survey_time = get_setting("survey_time")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Управление фразами персонажей (US-11)", callback_data="master:phrases")],
            [InlineKeyboardButton(text=f"⏰ Изменить время опроса (сейчас {survey_time}) (US-12)", callback_data="master:time")],
            [InlineKeyboardButton(text="❤️‍🩹 Изменить текст реакции на уровни 9–10 (US-12)", callback_data="master:crisis_text")],
            [InlineKeyboardButton(text="📈 Анонимная аналитика кризисов для инвесторов (US-13)", callback_data="master:analytics")],
        ]
    )
    await message.answer(
        "🧠 <b>Подсистема Мастера / Основателя-психолога (п. 3.3 ТЗ)</b>\n\n"
        "Здесь вы можете управлять репликами персонажей без привлечения разработчика, "
        "настраивать психологически корректную реакцию на уровни 9–10 и выгружать сводную аналитику эффективности.",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data == "master:phrases")
async def cb_master_phrases(callback: CallbackQuery) -> None:
    with get_db_connection() as conn:
        phrases = conn.execute(
            """
            SELECT p.id, c.name as char_name, p.phrase_text, p.is_active
            FROM Phrases p
            JOIN Characters c ON p.character_id = c.id
            ORDER BY p.character_id, p.id;
            """
        ).fetchall()

    buttons = []
    for p in phrases:
        status_icon = "✅" if p["is_active"] else "🔇"
        short_txt = p["phrase_text"][:28] + "..."
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{status_icon} [{p['char_name']}] {short_txt}",
                    callback_data=f"master:toggle_phrase:{p['id']}",
                )
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton(text="➕ Добавить фразу Бабушке", callback_data="master:add_phrase:1"),
            InlineKeyboardButton(text="➕ Добавить фразу Вейдеру", callback_data="master:add_phrase:2"),
        ]
    )
    await callback.message.answer(
        "💬 <b>Редактор шаблонов фраз персонажей (US-11)</b>\n"
        "Нажмите на любую фразу, чтобы мгновенно включить или отключить её без изменения кода бота:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("master:toggle_phrase:"))
async def cb_master_toggle_phrase(callback: CallbackQuery) -> None:
    phrase_id = int(callback.data.split(":")[2])
    with get_db_connection() as conn:
        row = conn.execute("SELECT is_active FROM Phrases WHERE id = ?;", (phrase_id,)).fetchone()
        if row:
            new_val = 0 if row["is_active"] else 1
            conn.execute("UPDATE Phrases SET is_active = ? WHERE id = ?;", (new_val, phrase_id))
            conn.commit()
    await callback.answer("Статус фразы обновлён!")
    await cb_master_phrases(callback)


@router.callback_query(F.data.startswith("master:add_phrase:"))
async def cb_master_add_phrase(callback: CallbackQuery, state: FSMContext) -> None:
    char_id = int(callback.data.split(":")[2])
    await state.update_data(target_char_id=char_id)
    await state.set_state(BotStates.master_adding_phrase_text)
    char_name = "Бабушка" if char_id == 1 else "Дарт Вейдер"
    await callback.message.answer(
        f"✍️ Отправьте новый текст похвалы для персонажа <b>«{char_name}»</b>.\n"
        "Вы можете использовать тег <code>{deed}</code> — бот автоматически подставит туда название дела пользователя:",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(BotStates.master_adding_phrase_text)
async def process_master_new_phrase(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    char_id = data.get("target_char_id", 1)
    text = (message.text or "").strip()
    if not text:
        await message.answer("Текст не может быть пустым.")
        return
    if "{deed}" not in text:
        text += " (Дело: «{deed}»)"

    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO Phrases (character_id, phrase_text, is_active) VALUES (?, ?, 1);",
            (char_id, text),
        )
        conn.commit()
    await state.clear()
    logger.info(f"Мастер {message.from_user.id} добавил новую фразу для персонажа {char_id}")
    await message.answer("✅ Новая фраза успешно добавлена в базу данных и сразу активна!")


@router.callback_query(F.data == "master:time")
async def cb_master_time(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BotStates.master_setting_time)
    await callback.message.answer(
        "⏰ Введите новое время ежедневного автоматического опроса в формате <code>ЧЧ:ММ</code> (например, <code>20:30</code>):",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(BotStates.master_setting_time)
async def process_master_time(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    try:
        datetime.strptime(val, "%H:%M")
    except ValueError:
        await message.answer("Неверный формат. Введите время как ЧЧ:ММ (например, 20:00).")
        return
    set_setting("survey_time", val)
    await state.clear()
    await message.answer(f"✅ Время ежедневной рассылки опроса изменено на <b>{val}</b>.", parse_mode="HTML")


@router.callback_query(F.data == "master:crisis_text")
async def cb_master_crisis_text(callback: CallbackQuery, state: FSMContext) -> None:
    current_msg = get_setting("crisis_message")
    await state.set_state(BotStates.master_setting_crisis_text)
    await callback.message.answer(
        "❤️‍🩹 <b>Текущий текст реакции на критический уровень 9–10:</b>\n\n"
        f"{current_msg}\n\n"
        "Отправьте новый утверждённый текст сообщения (можно использовать тег <code>{score}</code>):",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(BotStates.master_setting_crisis_text)
async def process_master_crisis_text(message: Message, state: FSMContext) -> None:
    new_text = (message.text or "").strip()
    if len(new_text) < 15:
        await message.answer("Слишком короткий текст для кризисной поддержки.")
        return
    set_setting("crisis_message", new_text)
    await state.clear()
    logger.info(f"Мастер {message.from_user.id} обновил сценарий реакции на кризис 9-10")
    await message.answer("✅ Текст бережной реакции на уровни 9–10 успешно обновлён в системе!")


@router.callback_query(F.data == "master:analytics")
async def cb_master_analytics(callback: CallbackQuery) -> None:
    """US-13: Анонимная сводная аналитика по динамике кризисов для отчета перед инвесторами."""
    with get_db_connection() as conn:
        total_logs = conn.execute("SELECT COUNT(*) as cnt FROM Crisis_Logs;").fetchone()["cnt"]
        avg_row = conn.execute("SELECT AVG(score) as avg_s FROM Crisis_Logs;").fetchone()
        crit_cnt = conn.execute("SELECT COUNT(*) as cnt FROM Crisis_Logs WHERE is_critical = 1;").fetchone()["cnt"]
        regular_users = conn.execute(
            """
            SELECT COUNT(*) as cnt FROM (
                SELECT user_id FROM Deeds GROUP BY user_id HAVING COUNT(*) >= 3
            );
            """
        ).fetchone()["cnt"]
        total_users = conn.execute("SELECT COUNT(*) as cnt FROM Users;").fetchone()["cnt"]

    avg_score = avg_row["avg_s"] if (avg_row and avg_row["avg_s"] is not None) else 0.0
    crit_share = (crit_cnt / total_logs * 100) if total_logs > 0 else 0.0
    retention_3x = (regular_users / total_users * 100) if total_users > 0 else 0.0

    report = (
        "📈 <b>Анонимная сводная аналитика для Мастера и Инвесторов (US-13, п. 3.3.3 ТЗ)</b>\n\n"
        f"• Всего проведено замеров состояния: <b>{total_logs}</b>\n"
        f"• Средний уровень кризиса по базе: <b>{avg_score:.2f} / 10</b>\n"
        f"• Доля критических ответов (уровни 9–10): <b>{crit_share:.1f}%</b> ({crit_cnt} случаев — всем выдана рекомендация специалиста)\n"
        f"• Доля пользователей с ≥3 делами (целевая метрика ТЗ ≥60%): <b>{retention_3x:.1f}%</b> ({regular_users} из {total_users})"
    )
    await callback.message.answer(report, parse_mode="HTML")
    await callback.answer()


# =============================================================================
# 8. ОСНОВНОЙ СЦЕНАРИЙ: ПРИЕМ ВЫПОЛНЕННОГО ДЕЛА, ПОХВАЛА И ВЫДАЧА PDF (US-1, US-3)
# =============================================================================

@router.callback_query(F.data.startswith("get_pdf:"))
async def cb_generate_pdf_certificate(callback: CallbackQuery) -> None:
    """US-3: Генерация и отправка именного PDF-сертификата не позднее 10 секунд."""
    deed_id = int(callback.data.split(":")[1])
    await callback.answer("⏳ Формирую именной PDF-сертификат...")

    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT d.id as deed_id, d.deed_title, d.created_at,
                   u.nickname, c.name as char_name, p.phrase_text
            FROM Deeds d
            JOIN Users u ON d.user_id = u.id
            JOIN Phrases p ON d.phrase_id = p.id
            JOIN Characters c ON p.character_id = c.id
            WHERE d.id = ?;
            """,
            (deed_id,),
        ).fetchone()

        if not row:
            await callback.message.answer("Запись о выполненном деле не найдена.")
            return

        # Проверяем, выдавался ли уже сертификат по этому делу (связь 1 : 0..1 из ER-схемы)
        cert_row = conn.execute(
            "SELECT cert_number, issued_at FROM Certificates WHERE deed_id = ?;",
            (deed_id,),
        ).fetchone()

        if cert_row:
            cert_number = cert_row["cert_number"]
            issued_at = cert_row["issued_at"]
        else:
            cert_number = f"PM-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
            issued_at = datetime.now().strftime("%d.%m.%Y %H:%M")
            conn.execute(
                "INSERT INTO Certificates (deed_id, cert_number, issued_at) VALUES (?, ?, ?);",
                (deed_id, cert_number, issued_at),
            )
            conn.commit()

    formatted_praise = row["phrase_text"].replace("{deed}", row["deed_title"])
    pdf_bytes = generate_pdf_certificate(
        nickname=row["nickname"],
        deed_title=row["deed_title"],
        character_name=row["char_name"],
        praise_text=formatted_praise,
        cert_number=cert_number,
        issued_at=issued_at,
    )

    pdf_file = BufferedInputFile(pdf_bytes, filename=f"Certificate_{cert_number}.pdf")
    await callback.message.answer_document(
        document=pdf_file,
        caption=(
            f"🎓 <b>Ваш именной PDF-сертификат готов!</b>\n"
            f"• Достижение: <b>«{row['deed_title']}»</b>\n"
            f"• Получатель: <b>{row['nickname']}</b>\n"
            f"• Персонаж: <b>{row['char_name']}</b>\n"
            f"• Номер в реестре: <code>{cert_number}</code>"
        ),
        parse_mode="HTML",
    )
    logger.info(f"Выдан PDF-сертификат {cert_number} для deed_id={deed_id}")


@router.message(F.text)
async def handle_user_deed_message(message: Message) -> None:
    """
    US-1: Любое обычное текстовое сообщение воспринимается как отчёт о выполненном деле
    и мгновенно (< 3 сек) подкрепляется похвалой выбранного персонажа.
    """
    user = get_or_create_user(message.from_user.id, message.from_user.username)
    if check_is_blocked(user):
        await message.answer("⛔ Ваш доступ к боту ограничен администратором.")
        return

    deed_title = message.text.strip()
    if len(deed_title) > 120:
        deed_title = deed_title[:117] + "..."

    with get_db_connection() as conn:
        char = conn.execute(
            "SELECT id, name FROM Characters WHERE id = ?;", (user["character_id"],)
        ).fetchone()

        phrases = conn.execute(
            "SELECT id, phrase_text FROM Phrases WHERE character_id = ? AND is_active = 1;",
            (char["id"],),
        ).fetchall()

        if not phrases:
            # Фолбэк, если Мастер отключил все фразы
            phrases = conn.execute(
                "SELECT id, phrase_text FROM Phrases WHERE character_id = ?;",
                (char["id"],),
            ).fetchall()

        chosen_phrase = random.choice(phrases)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO Deeds (user_id, phrase_id, deed_title, created_at)
            VALUES (?, ?, ?, ?);
            """,
            (user["id"], chosen_phrase["id"], deed_title, now_str),
        )
        deed_id = cur.lastrowid
        conn.commit()

    praise_text = chosen_phrase["phrase_text"].replace("{deed}", deed_title)
    emoji_header = "👵" if char["name"] == "Бабушка" else "⚔️"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📜 Получить PDF-сертификат за это дело",
                    callback_data=f"get_pdf:{deed_id}",
                )
            ]
        ]
    )

    await message.answer(
        f"{emoji_header} <b>{char['name']} хвалит вас:</b>\n\n"
        f"<i>«{praise_text}»</i>\n\n"
        f"🏅 Записано достижение: <b>{deed_title}</b>",
        parse_mode="HTML",
        reply_markup=kb,
    )


# =============================================================================
# 9. ФОНОВЫЙ ПЛАНИРОВЩИК (APSCHEDULER) ДЛЯ ЕЖЕДНЕВНЫХ ОПРОСОВ ПО ТАЙМЕРУ
# =============================================================================

async def scheduled_daily_survey_check(bot: Bot) -> None:
    """Каждую минуту проверяет, не наступило ли заданное Мастером время опроса."""
    target_time = get_setting("survey_time") or "20:00"
    now_hm = datetime.now().strftime("%H:%M")
    if now_hm != target_time:
        return

    with get_db_connection() as conn:
        users = conn.execute("SELECT telegram_id FROM Users WHERE is_blocked = 0;").fetchall()

    for u in users:
        try:
            await bot.send_message(
                chat_id=u["telegram_id"],
                text=(
                    "🌙 <b>Ежедневная минутка заботы о себе!</b>\n\n"
                    "Как прошёл ваш день? Оцените своё эмоциональное состояние по шкале от 1 до 10 одним нажатием:"
                ),
                parse_mode="HTML",
                reply_markup=get_survey_inline_kb(),
            )
        except Exception as e:
            logger.warning(f"Не удалось доставить ежедневный опрос пользователю {u['telegram_id']}: {e}")


# =============================================================================
# 10. ТОЧКА ВХОДА И ЗАПУСК БОТА
# =============================================================================

async def main() -> None:
    init_db()

    token = os.getenv("BOT_TOKEN", BOT_TOKEN).strip()
    if not token or token == "ВСТАВЬТЕ_СЮДА_ВАШ_ТОКЕН":
        print("\n" + "=" * 72)
        print("ВНИМАНИЕ: Вы ещё не указали токен бота в переменной BOT_TOKEN.")
        print("Получить токен можно за 30 секунд у официального бота @BotFather в Telegram.")
        print("=" * 72)
        token = input("Вставьте ваш токен от @BotFather прямо сюда и нажмите Enter: ").strip()

    bot = Bot(token=token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(scheduled_daily_survey_check, "interval", minutes=1, args=[bot])
    scheduler.start()

    me = await bot.get_me()
    logger.info(f"Бот @{me.username} успешно запущен! Нажмите Ctrl+C для остановки.")
    print(f"\n🚀 Хвалебный бот успешно запущен! Откройте в Telegram: https://t.me/{me.username}\n")

    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен пользователем.")