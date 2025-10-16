"""
HUDEI HUDEI WOMEN CLUB — Telegram Bot
--------------------------------------
Features:
- /start onboarding with welcome & rules
- Main menu buttons: Share Story, Rules, Unsubscribe
- Story submission flow (admin approval)
- Admin panel: approve/reject stories, broadcast
- Daily posts (Morning / Day / Evening) from posts.txt
- SQLite storage (users, stories)
- Works with python-telegram-bot v21+
"""

from __future__ import annotations
import os
import logging
import asyncio
import aiosqlite
import re
from datetime import time, datetime
from zoneinfo import ZoneInfo
from typing import List, Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    AIORateLimiter,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ====== Logging ======
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("hudei_bot")

# ====== Config ======
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS: List[int] = [int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x]
CHANNEL_ID: Optional[str] = os.getenv("CHANNEL_ID")  # e.g. "@hudeihudei"
TZ = ZoneInfo("Europe/Vilnius")
DB_PATH = os.getenv("DB_PATH", "hudei_bot.sqlite3")

# ====== Constants & States ======
MENU, STORY = range(2)
BTN_SHARE = "Поделиться историей"
BTN_RULES = "Правила"
BTN_UNSUB = "Отписаться"

WELCOME = (
    "🌷 Добро пожаловать в HUDEI HUDEI WOMEN CLUB!\n\n"
    "Здесь безопасно делиться, поддерживать и расти вместе. ✨\n\n"
    "Каждый день в 19:19 мы публикуем тёплый вечерний пост.\n"
    "Нажми «Поделиться историей» — если хочешь рассказать о своём пути. 🦋"
)

RULES = (
    "🤍 Правила сообщества:\n"
    "1) Без осуждения и сравнения.\n"
    "2) Поддержка вместо критики.\n"
    "3) Бережность к себе и другим.\n"
    "4) Приватность: всё сказанное — остаётся в клубе.\n"
    "5) Уважение к разным историям и темпам."
)

# ====== DB helpers ======
CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY,
  username TEXT,
  first_name TEXT,
  joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  subscribed INTEGER DEFAULT 1
);
"""
CREATE_STORIES = """
CREATE TABLE IF NOT EXISTS stories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  content TEXT,
  status TEXT DEFAULT 'pending',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(CREATE_USERS)
        await db.execute(CREATE_STORIES)
        await db.commit()

async def upsert_user(user_id: int, username: str | None, first_name: str | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users(user_id, username, first_name) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name",
            (user_id, username, first_name),
        )
        await db.commit()

async def set_subscribed(user_id: int, flag: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET subscribed=? WHERE user_id=?", (flag, user_id))
        await db.commit()

async def get_subscribed_users() -> List[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users WHERE subscribed=1")
        rows = await cur.fetchall()
        return [r[0] for r in rows]

async def add_story(user_id: int, content: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO stories(user_id, content) VALUES(?,?)", (user_id, content))
        await db.commit()
        return cur.lastrowid

async def set_story_status(story_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE stories SET status=? WHERE id=?", (status, story_id))
        await db.commit()

# ====== POSTS PARSER ======
POSTS = {"morning": [], "day": [], "evening": []}

def load_posts(path: str = "posts.txt"):
    global POSTS
    POSTS = {"morning": [], "day": [], "evening": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        blocks = re.split(r"^#\s*Day\s+\d+\s+(Morning|Day|Evening)\s*$", content, flags=re.MULTILINE)
        for i in range(1, len(blocks), 2):
            tag = blocks[i].strip().lower()
            text = blocks[i + 1].strip()
            if tag in POSTS and text:
                POSTS[tag].append(text)
    except Exception as e:
        log.error(f"Failed to load posts.txt: {e}")
    for k in POSTS:
        if not POSTS[k]:
            POSTS[k] = [f"{k.title()} post placeholder. Добавь содержимое в posts.txt"]
    log.info(f"Loaded posts: morning={len(POSTS['morning'])}, day={len(POSTS['day'])}, evening={len(POSTS['evening'])}")

def day_index_by_date():
    now = datetime.now(TZ)
    first = datetime(now.year, now.month, 1, tzinfo=TZ)
    return (now - first).days

def target_chat_id_fallback():
    return CHANNEL_ID

async def post_text(text: str, context: ContextTypes.DEFAULT_TYPE):
    chat = target_chat_id_fallback()
    if chat:
        try:
            await context.bot.send_message(chat_id=chat, text=text)
            return
        except Exception as e:
            log.error(f"Send to channel failed: {e}")
    users = await get_subscribed_users()
    for uid in users:
        try:
            await context.bot.send_message(chat_id=uid, text=text)
        except Exception:
            pass

async def job_morning(context: ContextTypes.DEFAULT_TYPE):
    idx = day_index_by_date() % len(POSTS["morning"])
    await post_text(POSTS["morning"][idx], context)

async def job_day(context: ContextTypes.DEFAULT_TYPE):
    idx = day_index_by_date() % len(POSTS["day"])
    await post_text(POSTS["day"][idx], context)

async def job_evening(context: ContextTypes.DEFAULT_TYPE):
    idx = day_index_by_date() % len(POSTS["evening"])
    await post_text(POSTS["evening"][idx], context)

# ====== Handlers ======
MAIN_KB = ReplyKeyboardMarkup(
    [[KeyboardButton(BTN_SHARE)], [KeyboardButton(BTN_RULES), KeyboardButton(BTN_UNSUB)]],
    resize_keyboard=True,
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await upsert_user(u.id, u.username, u.first_name)
    await update.message.reply_text(WELCOME, reply_markup=MAIN_KB)
    return MENU

async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text == BTN_SHARE:
        await update.message.reply_text(
            "💌 Напиши свою историю (можно анонимно, без имён).",
            reply_markup=ReplyKeyboardRemove(),
        )
        return STORY
    elif text == BTN_RULES:
        await update.message.reply_text(RULES, reply_markup=MAIN_KB)
        return MENU
    elif text == BTN_UNSUB:
        await set_subscribed(update.effective_user.id, 0)
        await update.message.reply_text("Вы отписались от вечерних сообщений. Чтобы вернуться — /start", reply_markup=MAIN_KB)
        return MENU
    else:
        await update.message.reply_text("Выберите действие на клавиатуре ниже.", reply_markup=MAIN_KB)
        return MENU

async def receive_story(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    content = update.message.text
    story_id = await add_story(user_id, content)
    await update.message.reply_text("Спасибо за доверие 💌 История отправлена на модерацию.", reply_markup=MAIN_KB)
    if ADMIN_IDS:
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Опубликовать", callback_data=f"approve:{story_id}"),
                InlineKeyboardButton("✖️ Отклонить", callback_data=f"reject:{story_id}"),
            ]
        ])
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"📝 Новая история #{story_id}:\n\n{content}",
                    reply_markup=kb,
                )
            except Exception:
                pass
    return MENU

async def approve_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action, sid = query.data.split(":", 1)
    story_id = int(sid)
    if action == "approve":
        await set_story_status(story_id, "approved")
        await query.edit_message_text(f"✅ История #{story_id} одобрена.")
    elif action == "reject":
        await set_story_status(story_id, "rejected")
        await query.edit_message_text(f"✖️ История #{story_id} отклонена.")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    msg = update.message.text.partition(" ")[2].strip()
    if not msg:
        await update.message.reply_text("Использование: /broadcast <текст>")
        return
    users = await get_subscribed_users()
    ok = 0
    for uid in users:
        try:
            await context.bot.send_message(chat_id=uid, text=msg)
            ok += 1
        except Exception:
            pass
    await update.message.reply_text(f"Рассылка отправлена {ok} участницам.")

# ====== Application ======
def main():
    assert BOT_TOKEN, "Set BOT_TOKEN env var"

    # Инициализация БД и загрузка постов
    # (эти функции у тебя async, поэтому создадим краткий раннер)
    import asyncio as _asyncio
    _asyncio.run(init_db())
    load_posts()

    app: Application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .rate_limiter(AIORateLimiter())
        .build()
    )

    # Хэндлеры
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router)],
            STORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_story)],
        },
        fallbacks=[CommandHandler("start", start)],
    )
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(approve_reject, pattern=r"^(approve|reject):"))
    app.add_handler(CommandHandler("broadcast", broadcast))

    # Ежедневные посты
    app.job_queue.run_daily(job_morning, time(hour=8, minute=0, tzinfo=TZ),   name="morning_post")
    app.job_queue.run_daily(job_day,     time(hour=12, minute=0, tzinfo=TZ),  name="day_post")
    app.job_queue.run_daily(job_evening, time(hour=19, minute=19, tzinfo=TZ), name="evening_post")

    # ЕДИНСТВЕННЫЙ запуск polling (без asyncio.run вокруг)
    app.run_polling(
    allowed_updates=Update.ALL_TYPES,
    drop_pending_updates=True,
    stop_signals=None,
    close_loop=False,  
    )

# ====== Application ======
async def main():
    assert BOT_TOKEN, "Set BOT_TOKEN env var"
    await init_db()
    load_posts()

   app: Application = (
    ApplicationBuilder()
    .token(BOT_TOKEN)
    .rate_limiter(AIORateLimiter())
    .post_init(_post_init)        
    .build()
)

app.run_polling(
    allowed_updates=Update.ALL_TYPES,
    drop_pending_updates=True,
    stop_signals=None,
    close_loop=False,
)

    # Хэндлеры
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router)],
            STORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_story)],
        },
        fallbacks=[CommandHandler("start", start)],
    )
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(approve_reject, pattern=r"^(approve|reject):"))
    app.add_handler(CommandHandler("broadcast", broadcast))

    # Расписание
    app.job_queue.run_daily(job_morning, time(hour=8,  minute=0,  tzinfo=TZ), name="morning_post")
    app.job_queue.run_daily(job_day,     time(hour=12, minute=0,  tzinfo=TZ), name="day_post")
    app.job_queue.run_daily(job_evening, time(hour=19, minute=19, tzinfo=TZ), name="evening_post")

    # ОДИН запуск polling. ВАЖНО: close_loop=False
    await app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        stop_signals=None,
        close_loop=False,
    )

# ====== Application ======

# Асинхронная инициализация перед стартом polling
async def _post_init(application: Application):
    await init_db()
    # можно послать себе пинг-уведомление о старте (если хочешь):
    for admin_id in ADMIN_IDS:
        try:
            await application.bot.send_message(admin_id, "HUDEI HUDEI BOT запущен ✅")
        except Exception:
            pass

def main():
    assert BOT_TOKEN, "Set BOT_TOKEN env var"

    # Загружаем контент-план из posts.txt (синхронно)
    load_posts()

    app: Application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .rate_limiter(AIORateLimiter())
        .build()
    )

    # Хэндлеры
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router)],
            STORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_story)],
        },
        fallbacks=[CommandHandler("start", start)],
    )
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(approve_reject, pattern=r"^(approve|reject):"))
    app.add_handler(CommandHandler("broadcast", broadcast))

    # Ежедневные посты в канал/подписчицам
    app.job_queue.run_daily(job_morning, time(hour=8,  minute=0,  tzinfo=TZ), name="morning_post")
    app.job_queue.run_daily(job_day,     time(hour=12, minute=0,  tzinfo=TZ), name="day_post")
    app.job_queue.run_daily(job_evening, time(hour=19, minute=19, tzinfo=TZ), name="evening_post")

    # Единственный корректный запуск polling (PTB сам управляет event loop)
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        stop_signals=None,
        post_init=_post_init,   # <- асинхронная инициализация БД здесь
    )

if __name__ == "__main__":
    main()
