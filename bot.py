import asyncio
import os
import re
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest

# Токен берем из переменных Railway
TOKEN = os.getenv("BOT_TOKEN")

# --- НАСТРОЙКИ ID КАНАЛОВ ---
# Откуда берем ссылки
SOURCE_CHANNEL_ID = -1003959661585 

# Куда кидаем готовый список
STORAGE_CHANNEL_ID = -1003721670958

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Бот для генерации ссылок готов.\n\n"
        "Отправь команду /link и список в формате:\n"
        "<code>Название канала 3</code>\n"
        "<code>Другой канал 2</code>\n\n"
        "(Число пиши в конце через пробел)",
        parse_mode="HTML"
    )

@dp.message(Command("link"))
async def cmd_link(message: Message):
    args = message.text.replace("/link", "").strip()
    if not args:
        await message.answer("Напиши список каналов после команды. Пример:\n/link\nЮлия ПАМП/ДАМП 2\nДжо 3")
        return

    lines = [line.strip() for line in args.split("\n") if line.strip()]
    
    tasks = []
    total_links = 0
    
    for line in lines:
        try:
            # Поддерживаем оба формата: "Название | 3" и "Название 3"
            if "|" in line:
                parts = line.rsplit("|", 1)
                name = parts[0].strip()
                count = int(parts[1].strip())
            else:
                # Ищем последнее число в строке
                match = re.search(r'(\d+)$', line)
                if not match:
                    continue
                count = int(match.group(1))
                name = line[:match.start()].strip()
            
            if count > 0 and name:
                tasks.append((name, count))
                total_links += count
        except ValueError:
            pass

    if total_links == 0:
        await message.answer("Не нашел валидных строк. Формат: <code>Название 3</code>", parse_mode="HTML")
        return

    progress_msg = await message.answer(f"⏳ <b>Начинаю создание ссылок...</b>\n\n[░░░░░░░░░░] 0%", parse_mode="HTML")
    
    created_count = 0
    results_text = "🔗 <b>Новые ссылки:</b>\n\n"
    done_list = []

    for channel_name, count in tasks:
        results_text += f"📁 <b>{channel_name}</b>\n"
        
        # Очищаем название от спецсимволов для внутреннего имени ссылки
        clean_link_name = re.sub(r'[^\w\s\-]', '', channel_name).strip()
        
        for i in range(1, count + 1):
            # Формат: число_название
            link_name = f"{i}_{clean_link_name}"
            
            try:
                link = await bot.create_chat_invite_link(
                    chat_id=SOURCE_CHANNEL_ID,
                    name=link_name,
                    creates_join_request=True
                )
                results_text += f"<code>{link.invite_link}</code>\n"
                created_count += 1
                done_list.append(f"✅ {link_name}")
                
            except TelegramBadRequest as e:
                results_text += f"⚠️ Ошибка: {e}\n"
                done_list.append(f"❌ {link_name} (Ошибка)")
            except Exception as e:
                results_text += f"⚠️ Ошибка: {e}\n"
                done_list.append(f"❌ {link_name} (Ошибка)")
            
            # --- ОБНОВЛЕНИЕ ПРОГРЕСС-БАРА ---
            percent = int((created_count / total_links) * 100)
            filled = int(percent / 10)
            bar = "▓" * filled + "░" * (10 - filled)
            last_done = "\n".join(done_list[-5:])
            
            progress_text = (
                f"⏳ <b>Создание ссылок...</b>\n\n"
                f"[{bar}] {percent}%\n"
                f"Сделано: <b>{created_count}</b> из <b>{total_links}</b>\n\n"
                f"<b>Последние действия:</b>\n{last_done}"
            )
            
            try:
                await progress_msg.edit_text(progress_text, parse_mode="HTML")
            except Exception:
                pass 
            
            await asyncio.sleep(1.5)

    # --- ФИНАЛ ---
    if created_count > 0:
        try:
            await bot.send_message(
                chat_id=STORAGE_CHANNEL_ID,
                text=results_text,
                parse_mode="HTML"
            )
            await progress_msg.edit_text(
                f"✅ <b>Готово! Создано {created_count} ссылок из {total_links}.</b>\n\n"
                f"Список отправлен в канал.",
                parse_mode="HTML"
            )
        except Exception as e:
            await progress_msg.edit_text(
                f"⚠️ Ссылки созданы, но не удалось отправить в канал: {e}",
                parse_mode="HTML"
            )
    else:
        await progress_msg.edit_text("❌ Не удалось создать ни одной ссылки. Проверь права бота.")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
