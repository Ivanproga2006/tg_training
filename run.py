# Minimal Telegram bot built with aiogram: replies "Привет!" to /start.

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import CommandStart

# The dispatcher receives updates from Telegram and routes them to handlers.
dp = Dispatcher()


# Handler for the /start command.
@dp.message(CommandStart())
async def cmd_start(message: Message):
    # Reply to the user with a greeting.
    await message.answer('Привет!')


async def main():
    # Create the bot; replace 'token' with a real token from @BotFather.
    bot = Bot(token='token')
    # Start long polling: the bot keeps asking Telegram for new messages.
    await dp.start_polling(bot)


# Run main() only when the file is executed directly.
if __name__ == '__main__':
    asyncio.run(main())
