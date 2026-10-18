import asyncio
import os
import re
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.exceptions import TelegramRetryAfter

TOKEN = os.getenv("BOT_TOKEN")

SOURCE_CHANNEL_ID = -1004306238403 
STORAGE_CHANNEL_ID = -1003721670958

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Бот для генерации ссылок готов.\n\n"
        "Просто отправь мне список в таком формате:\n"
        "<code>Название канала 3</code>\n"
        "<code>Другой канал 2</code>",
        parse_mode="HTML"
    )

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text_list(message: Message):
    args = message.text.strip()
    if not args:
        return

    lines = [line.strip() for line in args.split("\n") if line.strip()]
    
    tasks = []
    total_links = 0
    
    for line in lines:
        try:
            if "|" in line:
                parts = line.rsplit("|", 1)
                name = parts[0].strip()
                count = int(parts[1].strip())
            else:
                match = re.search(r'(\d+)$', line)
                if not match:
                    continue
                count = int(match.group(1))
                name = line[:match.start()].strip()
            
            if count > 0 and name:
                tasks.append({"name": name, "target": count, "current": 0})
                total_links += count
        except ValueError:
            pass

    if total_links == 0:
        return

    def build_progress_text():
        text = "📋 <b>Список задач:</b>\n\n"
        for t in tasks:
            if t["current"] == t["target"]:
                status = f"✅ {t['name']}: {t['current']}/{t['target']}"
            elif t["current"] > 0:
                status = f"⏳ {t['name']}: {t['current']}/{t['target']}"
            else:
                status = f"⏸ {t['name']}: {t['current']}/{t['target']}"
            text += status + "\n"
        return text

    progress_msg = await message.answer(build_progress_text(), parse_mode="HTML")
    
    created_count = 0

    for task in tasks:
        channel_name = task["name"]
        count = task["target"]
        clean_link_name = re.sub(r'[^\w\s\-]', '', channel_name).strip()
        
        current_channel_links = []

        for i in range(1, count + 1):
            link_name = f"{i}_{clean_link_name}"
            
            while True:
                try:
                    link = await bot.create_chat_invite_link(
                        chat_id=SOURCE_CHANNEL_ID,
                        name=link_name,
                        creates_join_request=True
                    )
                    current_channel_links.append(link.invite_link)
                    task["current"] += 1
                    created_count += 1
                    break 
                    
                except TelegramRetryAfter as e:
                    wait_time = e.retry_after + 2
                    temp_text = build_progress_text() + f"\n⏳ <i>Флуд-контроль. Ждем {wait_time} сек...</i>"
                    try:
                        await progress_msg.edit_text(temp_text, parse_mode="HTML")
                    except:
                        pass
                    await asyncio.sleep(wait_time)
                    
                except Exception as e:
                    current_channel_links.append(f"Ошибка: {link_name}")
                    task["current"] += 1 
                    break
            
            try:
                await progress_msg.edit_text(build_progress_text(), parse_mode="HTML")
            except:
                pass
            
            await asyncio.sleep(3)

        # --- ОТПРАВКА В КАНАЛ С ЗАГОЛОВКОМ И ИДЕАЛЬНЫМ БЛОКОМ КОДА ---
        if current_channel_links:
            # Собираем ссылки в одну строку
            links_text = "\n".join(current_channel_links)
            
            # Используем MarkdownV2. Три апострофа создают идеальный блок кода (pre)
            # \n\n нужно для отступа
            # Экранируем название канала на случай спецсимволов (но для простоты берем как есть)
            safe_title = channel_name.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("]", "\\]").replace("(", "\\(").replace(")", "\\)").replace("~", "\\~").replace("`", "\\`").replace(">", "\\>").replace("#", "\\#").replace("+", "\\+").replace("-", "\\-").replace("=", "\\=").replace("|", "\\|").replace("{", "\\{").replace("}", "\\}").replace(".", "\\.").replace("!", "\\!")
            
            message_text = f"📁 **{safe_title}**\n```\n{links_text}\n```"
            
            try:
                await bot.send_message(
                    chat_id=STORAGE_CHANNEL_ID,
                    text=message_text,
                    parse_mode="MarkdownV2"
                )
                await asyncio.sleep(2) 
            except Exception as e:
                await message.answer(f"⚠️ Не удалось отправить ссылки в канал: {e}")

    await progress_msg.edit_text(
        build_progress_text() + f"\n\n✅ <b>Всё готово! Создано {created_count} ссылок.</b>",
        parse_mode="HTML"
    )

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
