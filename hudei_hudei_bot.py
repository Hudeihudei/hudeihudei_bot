"""
HUDEI HUDEI WOMEN CLUB — Telegram Bot
--------------------------------------
Запускается на Render без Docker.
Функции:
- /start — приветствие и меню
- /rules — правила сообщества
- /story — поделиться своей историей
- /unsubscribe — отписаться от вечерних сообщений
- /broadcast — рассылка для администратора
"""

from __future__ import annotations
import os
import logging
import asyncio
import aiosqlite
from datetime import time
from zoneinfo import ZoneInfo
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, ApplicationBuilder, AIORateLimiter,
    CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
log = logging.getLogger("hudei_bot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
CHANNEL_ID = os.getenv("CHANNEL_ID")
TZ = ZoneInfo("Europe/Vilnius")
DB_PATH = "hudei_bot.sqlite3"

MENU, STORY = range(2)
BTN_SHARE, BTN_RULES, BTN_UNSUB = "Поделиться историей", "Правила", "Отписаться"

WELCOME = (
    "🌷 Добро пожаловать в HUDEI HUDEI WOMEN CLUB!\n\n"
    "Здесь безопасно делиться, поддерживать и расти вместе. ✨\n\n"
    "Каждый день в 19:19 мы публикуем тёплый вечерний пост.\n"
    "Нажми 'Поделиться историей' — если хочешь рассказать о своём пути. 🦋"
)

RULES = (
    "🤍 Правила сообщества:\n"
    "1) Без осуждения и сравнения.\n"
    "2) Поддержка вместо критики.\n"
    "3) Бережность к себе и другим.\n"
    "4) Приватность: всё сказанное — остаётся в клубе.\n"
    "5) Уважение к разным историям и темпам."

)

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

async def upsert_user(user_id, username, first_name):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users(user_id, username, first_name) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name",
            (user_id, username, first_name),
        )
        await db.commit()

async def set_subscribed(user_id, flag):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET subscribed=? WHERE user_id=?", (flag, user_id))
        await db.commit()

async def get_subscribed_users():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users WHERE subscribed=1")
        rows = await cur.fetchall()
        return [r[0] for r in rows]

async def add_story(user_id, content):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO stories(user_id, content) VALUES(?,?)", (user_id, content))
        await db.commit()
        return cur.lastrowid

async def set_story_status(story_id, status):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE stories SET status=? WHERE id=?", (status, story_id))
        await db.commit()

MAIN_KB = ReplyKeyboardMarkup(
    [[KeyboardButton(BTN_SHARE)], [KeyboardButton(BTN_RULES), KeyboardButton(BTN_UNSUB)]],
    resize_keyboard=True,
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await upsert_user(u.id, u.username, u.first_name)
    await update.message.reply_text(WELCOME, reply_markup=MAIN_KB)
    return MENU

async def rules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(RULES)

async def story_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💌 Напиши свою историю (можно анонимно, без имён).\n\n"
        "Что ты чувствуешь? Что помогает? Что болит?\n\n"
        "Когда закончишь — просто отправь сообщением.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return STORY

async def unsubscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_subscribed(update.effective_user.id, 0)
    await update.message.reply_text("Вы отписались от вечерних сообщений 19:19. Возврат — командой /start", reply_markup=MAIN_KB)

async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text == BTN_SHARE:
        return await story_cmd(update, context)
    elif text == BTN_RULES:
        await update.message.reply_text(RULES, reply_markup=MAIN_KB)
    elif text == BTN_UNSUB:
        await unsubscribe_cmd(update, context)
    else:
        await update.message.reply_text("Выберите действие на клавиатуре ниже.", reply_markup=MAIN_KB)
    return MENU

async def receive_story(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    content = update.message.text
    story_id = await add_story(user_id, content)
    await update.message.reply_text("Спасибо за доверие. Твоя история отправлена на модерацию. 🦋", reply_markup=MAIN_KB)
    if ADMIN_IDS:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Опубликовать", callback_data=f"approve:{story_id}"), InlineKeyboardButton("✖️ Отклонить", callback_data=f"reject:{story_id}")]])
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(chat_id=admin_id, text=f"📝 Новая история #{story_id} от {user_id}:\n\n{content}", reply_markup=kb)
            except Exception as e:
                log.warning(f"Failed to notify admin {admin_id}: {e}")
    return MENU

async def approve_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action, sid = q.data.split(":", 1)
    story_id = int(sid)
    if action == "approve":
        await set_story_status(story_id, "approved")
        await q.edit_message_text(f"✅ История #{story_id} — одобрена.")
        text = f"💌 История участницы HUDEI HUDEI:\n\n{q.message.text.split('\\n\\n',1)[1]}"
        if CHANNEL_ID:
            await context.bot.send_message(chat_id=CHANNEL_ID, text=text)
    else:
        await set_story_status(story_id, "rejected")
        await q.edit_message_text(f"✖️ История #{story_id} — отклонена.")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    msg = update.message.text.partition(" ")[2].strip()
    if not msg:
        await update.message.reply_text("Использование: /broadcast Текст сообщения")
        return
    for uid in await get_subscribed_users():
        try:
            await context.bot.send_message(chat_id=uid, text=msg)
        except Exception:
            pass
    await update.message.reply_text("Рассылка завершена ✅")

async def main():
    assert BOT_TOKEN, "⚠️ Переменная BOT_TOKEN не установлена!"
    await init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).rate_limiter(AIORateLimiter()).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router)],
                STORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_story)]},
        fallbacks=[CommandHandler("start", start)]
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("rules", rules_cmd))
    app.add_handler(CommandHandler("story", story_cmd))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(approve_reject, pattern=r"^(approve|reject):"))
    job_time = time(hour=19, minute=19, tzinfo=TZ)
    app.job_queue.run_daily(job_evening, job_time, name="evening_post")
    log.info("Bot starting…")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot stopped.")
