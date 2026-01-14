
import logging
import os
import json
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters, ChatMemberHandler
)

BOT_TOKEN = '7173724242:AAFdkl2FunWVBfP0w2RUEXY_iU-Ivho_Fm8

DATA_FILE = 'registered_chats.json'
ALLOWED_USERNAMES = {'SpammBotsss'}

# ===== Загрузка чатов =====
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        registered_chats = set(tuple(chat) for chat in json.load(f))
else:
    registered_chats = set()

user_data = {}
scheduled_jobs = {}

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)


# ===== /start =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        return

    username = update.effective_user.username

    if username not in ALLOWED_USERNAMES:
        await update.message.reply_text(
            "Hallo, möchtest du auch so einen Bot? "
            "Schreib mir @SpammBotss."
        )
        return

    keyboard = [
        [
            InlineKeyboardButton("📂 Chats", callback_data='view_chats'),
            InlineKeyboardButton("📤 Senden", callback_data='send_message'),
        ],
        [
            InlineKeyboardButton("🛑 Stop", callback_data='stop_broadcast'),
        ]
    ]

    await update.message.reply_text(
        "Aktion wählen:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ===== Buttons =====
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == 'view_chats':
        if registered_chats:
            text = '\n'.join([f"{t} ({i})" for i, t in registered_chats])
            await query.message.reply_text(text)
        else:
            await query.message.reply_text("Keine Chats.")

    elif query.data == 'send_message':
        user_data[user_id] = {'state': 'awaiting_interval'}
        await query.message.reply_text(
            "⏱ Intervall in Sekunden eingeben:"
        )

    elif query.data == 'stop_broadcast':
        if user_id in scheduled_jobs:
            scheduled_jobs[user_id].cancel()
            del scheduled_jobs[user_id]
            await query.message.reply_text("🛑 Gestoppt.")
        else:
            await query.message.reply_text("❌ Läuft nichts.")


# ===== Messages =====
async def receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in user_data:
        return

    state = user_data[user_id].get('state')

    if state == 'awaiting_interval':
        try:
            interval = int(update.message.text)
            if interval <= 0:
                raise ValueError
            user_data[user_id]['interval'] = interval
            user_data[user_id]['state'] = 'awaiting_message'
            await update.message.reply_text(
                f"⏱ {interval} Sekunden.\nJetzt Nachricht senden."
            )
        except ValueError:
            await update.message.reply_text("Nur positive Zahl.")

    elif state == 'awaiting_message':
        if not registered_chats:
            await update.message.reply_text("Keine Chats.")
            user_data[user_id] = {}
            return

        interval = user_data[user_id]['interval']
        message = update.message

        if user_id in scheduled_jobs:
            scheduled_jobs[user_id].cancel()

        task = asyncio.create_task(
            sequential_broadcast(
                context,
                message,
                interval,
                registered_chats,
                user_id
            )
        )

        scheduled_jobs[user_id] = task
        user_data[user_id] = {}

        await update.message.reply_text(
            f"📤 Start. Pause {interval} сек."
        )

        await start(update, context)


# ===== Broadcast =====
async def sequential_broadcast(context, message, interval, chats, user_id):
    from_chat_id = message.chat_id
    message_id = message.message_id
    chats = list(chats)

    try:
        while True:
            for chat_id, chat_title in chats:
                try:
                    await context.bot.forward_message(
                        chat_id=chat_id,
                        from_chat_id=from_chat_id,
                        message_id=message_id
                    )
                    logging.info(f"Gesendet an {chat_title} ({chat_id})")
                except Exception as e:
                    logging.error(
                        f"Fehler {chat_title} ({chat_id}): {e}"
                    )

                await asyncio.sleep(interval)

    except asyncio.CancelledError:
        logging.info(f"Broadcast gestoppt für {user_id}")


# ===== Chat add/remove =====
async def my_chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.my_chat_member
    chat = result.chat
    chat_id = chat.id
    chat_title = chat.title or chat.username or str(chat.id)

    old = result.old_chat_member.status
    new = result.new_chat_member.status

    if old in ['left', 'kicked'] and new in ['member', 'administrator']:
        registered_chats.add((chat_id, chat_title))
        save_registered_chats()

    elif new in ['left', 'kicked']:
        registered_chats.discard((chat_id, chat_title))
        save_registered_chats()


def save_registered_chats():
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(registered_chats), f, ensure_ascii=False)


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(ChatMemberHandler(my_chat_member_handler, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(
        MessageHandler(filters.ALL & filters.ChatType.PRIVATE & (~filters.COMMAND), receive_message)
    )

    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
