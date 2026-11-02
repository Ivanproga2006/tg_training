import asyncio

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import CommandStart

dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer('Привет!')


async def main():
    bot = Bot(token='token')
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())