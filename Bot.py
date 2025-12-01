


import logging
import os
import json
import asyncio
from datetime import datetime, timedelta, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters, ChatMemberHandler
)
from telegram.error import RetryAfter, TimedOut, Forbidden, BadRequest

BOT_TOKEN = '7173724242:AAFdkl2FunWVBfP0w2RUEXY_iU-Ivho_Fm8'  # подставь свой токен

# Файл для хранения списка зарегистрированных чатов
DATA_FILE = 'registered_chats.json'

# Список разрешённых @username в Телеграм
ALLOWED_USERNAMES = {'Eggmmaann', 'SpammBotsss'}

# Загрузка зарегистрированных чатов
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        registered_chats = set(tuple(chat) for chat in json.load(f))
else:
    registered_chats = set()

# Словарь для хранения промежуточных данных пользователя (состояния, интервала и т.п.)
user_data = {}

# Логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Словарь для хранения запланированных заданий (по пользователю)
scheduled_jobs = {}

# Кулдауны по чатам: chat_id -> datetime, когда можно снова слать
chat_cooldowns = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        return

    user_id = update.effective_user.id
    username = update.effective_user.username  # @username без @
    logging.info(f"Получена команда /start от пользователя ID: {user_id}, username: @{username}")

    # Проверяем, есть ли @username пользователя в списке разрешённых
    if username not in ALLOWED_USERNAMES:
        await update.message.reply_text(
            "Hallo, möchtest du auch so einen Bot? "
            "Schreib mir @SpammBotss, du kannst ihn einen Tag lang kostenlos ausprobieren."
        )
        return

    keyboard = [
        [
            InlineKeyboardButton("📂 Chats ansehen", callback_data='view_chats'),
            InlineKeyboardButton("📤 Nachricht senden", callback_data='send_message'),
        ],
        [
            InlineKeyboardButton("🛑 Verteilung stoppen", callback_data='stop_broadcast'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "📋 Wählen Sie eine Aktion:",
        reply_markup=reply_markup
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        return

    user_id = update.effective_user.id
    logging.info(f"Пользователь {user_id} запросил команду /help.")

    await update.message.reply_text(
        "ℹ️ Dieser Bot ermöglicht das Senden von Nachrichten 📤 in alle Chats, in denen er hinzugefügt wurde. 📂\n\n"
        "🔧 Verfügbare Befehle:\n"
        "/start - Starten Sie die Arbeit mit dem Bot 🚀\n"
        "/help - Zeigen Sie diese Nachricht an ❓\n"
        "/stop - Stoppen Sie die aktuelle Verteilung 🛑"
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    logging.info(f"Пользователь {user_id} нажал кнопку: {query.data}")

    if query.data == 'view_chats':
        if registered_chats:
            chat_list = '\n'.join([f"{chat_title} ({chat_id})" for chat_id, chat_title in registered_chats])
            await query.message.reply_text(f"📂 Der Bot ist in folgenden Chats hinzugefügt:\n{chat_list}")
        else:
            await query.message.reply_text("🚫 Der Bot ist in keinem Chat hinzugefügt.")

    elif query.data == 'send_message':
        user_data[user_id] = {'state': 'awaiting_interval'}
        await query.message.reply_text("⏰ Bitte geben Sie das Intervall in Minuten für das Senden der Nachricht ein.")

    elif query.data == 'stop_broadcast':
        if user_id in scheduled_jobs:
            job = scheduled_jobs[user_id]
            job.schedule_removal()
            del scheduled_jobs[user_id]
            await query.message.reply_text("🛑 Die Verteilung wurde gestoppt.")
        else:
            await query.message.reply_text("❌ Keine aktive Verteilung.")


async def receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logging.info(f"Получено сообщение от пользователя {user_id}")

    if user_id in user_data:
        state = user_data[user_id].get('state')
        if state == 'awaiting_interval':
            # Получаем интервал
            try:
                interval = int(update.message.text)
                if interval <= 0:
                    raise ValueError
                user_data[user_id]['interval'] = interval
                user_data[user_id]['state'] = 'awaiting_broadcast_message'
                await update.message.reply_text(
                    f"⏰ Das Intervall wurde auf {interval} Minuten eingestellt.\n"
                    f"✉️ Jetzt senden Sie bitte die Nachricht für die Verteilung."
                )
            except ValueError:
                await update.message.reply_text("⚠️ Bitte geben Sie eine positive ganze Zahl ein.")

        elif state == 'awaiting_broadcast_message':
            message_to_forward = update.message
            interval = user_data[user_id]['interval']

            if not registered_chats:
                await update.message.reply_text("🚫 Der Bot ist in keinem Chat hinzugefügt.")
                user_data[user_id]['state'] = None
                return

            job_queue = context.job_queue
            if job_queue is None:
                logging.error("JobQueue не инициализирована.")
                await update.message.reply_text("⚠️ Ein Fehler ist aufgetreten: JobQueue ist nicht initialisiert.")
                return

            # Удаляем предыдущую задачу, если она была
            if user_id in scheduled_jobs:
                scheduled_jobs[user_id].schedule_removal()

            # Запускаем повторяющуюся задачу с тем же интервалом (в секундах)
            job = job_queue.run_repeating(
                send_scheduled_message,
                interval=interval * 60,
                first=0,
                data={'message': message_to_forward, 'chats': registered_chats, 'user_id': user_id}
            )
            scheduled_jobs[user_id] = job

            await update.message.reply_text(
                f"📤 Die Verteilung wurde gestartet. Die Nachricht wird alle {interval} Minuten gesendet."
            )

            user_data[user_id]['state'] = None

            # Возвращаемся к кнопкам
            await start(update, context)


async def send_scheduled_message(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    message_to_forward = job_data['message']
    chats = job_data['chats']
    user_id = job_data['user_id']

    from_chat_id = message_to_forward.chat_id
    message_id = message_to_forward.message_id

    now = datetime.now(timezone.utc)

    # Копия, чтобы можно было удалять чаты
    chats_list = list(chats)
    chats_to_remove = set()

    for chat_id, chat_title in chats_list:
        # Проверяем кулдаун для этого чата
        cooldown_until = chat_cooldowns.get(chat_id)
        if cooldown_until and cooldown_until > now:
            # Еще рано слать — Telegram сказал ждать
            continue

        try:
            await context.bot.forward_message(
                chat_id=chat_id,
                from_chat_id=from_chat_id,
                message_id=message_id
            )
            logging.info(f"✅ Nachricht an Chat {chat_title} ({chat_id}) gesendet.")

            # Лимит Telegram: желательно не долбить сразу десятки чатов
            await asyncio.sleep(0.2)  # 5 сообщений в секунду — безопаснее

        except RetryAfter as e:
            # Telegram явно сказал, сколько секунд ждать
            retry_seconds = int(getattr(e, "retry_after", 60))
            chat_cooldowns[chat_id] = now + timedelta(seconds=retry_seconds)
            logging.error(
                f"❌ Flood control in Chat {chat_title} ({chat_id}): "
                f"Retry after {retry_seconds} seconds."
            )

        except Forbidden as e:
            # Бот потерял доступ к чату
            logging.error(
                f"❌ Kein Zugriff mehr auf Chat {chat_title} ({chat_id}): {e}. "
                f"Chat wird aus der Liste entfernt."
            )
            chats_to_remove.add((chat_id, chat_title))

        except BadRequest as e:
            msg = str(e)
            logging.error(
                f"❌ BadRequest beim Senden an Chat {chat_title} ({chat_id}): {msg}"
            )

            # Чат заблокировал бота, или сообщение нельзя переслать
            if "Chat_restricted" in msg or "can't be forwarded" in msg:
                chats_to_remove.add((chat_id, chat_title))

        except TimedOut as e:
            # Временный таймаут — чат оставляем, просто логируем
            logging.error(
                f"❌ Timed out beim Senden an Chat {chat_title} ({chat_id}): {e}"
            )

        except Exception as e:
            # Любая другая ошибка
            logging.error(
                f"❌ Nachricht an Chat {chat_title} ({chat_id}) konnte nicht gesendet werden: {e}"
            )

    # Удаляем чаты, где навсегда запрещена пересылка или бот не имеет доступа
    if chats_to_remove:
        for chat_tuple in chats_to_remove:
            if chat_tuple in registered_chats:
                registered_chats.discard(chat_tuple)
        save_registered_chats()
        logging.info(
            f"ℹ️ Entfernte dauer-problematische Chats: "
            f"{', '.join([str(c[1]) for c in chats_to_remove])}"
        )


async def my_chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.my_chat_member
    chat = result.chat
    chat_id = chat.id
    chat_title = chat.title or chat.full_name or chat.username or str(chat.id)
    new_status = result.new_chat_member.status
    old_status = result.old_chat_member.status

    logging.info(
        f"my_chat_member-Update: Chat '{chat_title}' ({chat_id}), "
        f"alter Status: {old_status}, neuer Status: {new_status}"
    )

    if old_status in ['left', 'kicked'] and new_status in ['member', 'administrator']:
        registered_chats.add((chat_id, chat_title))
        save_registered_chats()
        logging.info(f"✅ Bot wurde dem Chat {chat_title} ({chat_id}) hinzugefügt.")
    elif new_status in ['left', 'kicked']:
        registered_chats.discard((chat_id, chat_title))
        save_registered_chats()
        logging.info(f"❌ Bot wurde aus dem Chat {chat_title} ({chat_id}) entfernt.")


def save_registered_chats():
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(registered_chats), f, ensure_ascii=False)


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(ChatMemberHandler(my_chat_member_handler, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ALL & filters.ChatType.PRIVATE & (~filters.COMMAND), receive_message))

    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
