import logging
import telebot

from config import BOT_TOKEN
from database.db import init_db
from handlers import settings as settings_handlers
from handlers import media as media_handlers


def main():
    logging.basicConfig(level=logging.INFO)

    init_db()

    bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", num_threads=8)

    settings_handlers.register_handlers(bot)
    media_handlers.register_handlers(bot)

    logging.info("Bot starting - polling for updates...")
    bot.infinity_polling(skip_pending=True)


if __name__ == "__main__":
    main()
