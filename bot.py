import asyncio
import os
import re
from pathlib import Path
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile
from aiogram.filters import Command
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest

# Токен берем из переменных Railway
TOKEN = os.getenv("BOT_TOKEN")

# ТВОИ ID (канал, откуда берем ссылки, и куда кидаем результат)
SOURCE_CHANNEL_ID = -1003959661585
STORAGE_CHANNEL_ID = -1003959661585

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Папка для временных файлов
TEMP_DIR = Path("temp_files")
TEMP_DIR.mkdir(exist_ok=True)

# Блокировка для строгой очереди обработки файлов
processing_lock = asyncio.Lock()

def count_lines(file_path: Path) -> int:
    """Считает непустые строки в файле"""
    count = 0
    with open(file_path, "r", encoding="utf-8-sig", errors="ignore") as f:
        for line in f:
            if line.strip():
                count += 1
    return count

def clean_name(name: str) -> str:
    """Очищает имя файла от цифр и лишних символов"""
    stem = Path(name).stem
    stem = re.sub(r'\d+', '', stem) # Удаляем все цифры
    stem = re.sub(r"[^\w\s\-]", "_", stem, flags=re.UNICODE) # Заменяем спецсимволы на _
    stem = re.sub(r'[_\s]+', '_', stem).strip('_') # Убираем лишние подчеркивания
    return stem

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Бот работает.\n\n"
        "1. Скидывай .txt файлы для переименования (обработка по очереди).\n"
        "2. Отправь команду /link и список каналов в формате:\n"
        "<code>Название канала | 3</code>\n"
        "<code>Другой канал | 2</code>\n\n"
        "Ссылки будут созданы с заявками и отправлены в канал.",
        parse_mode="HTML"
    )

@dp.message(Command("link"))
async def cmd_link(message: Message):
    # Получаем текст после команды /link
    args = message.text.replace("/link", "").strip()
    if not args:
        await message.answer(
            "Напиши список каналов после команды. Пример:\n"
            "/link\n"
            "vip клуб ict | 3\n"
            "Канал Новости | 2"
        )
        return

    # Разбиваем на строки и убираем пустые
    lines = [line.strip() for line in args.split("\n") if line.strip()]
    
    # Парсим задачи, чтобы знать общее количество ссылок для прогресс-бара
    tasks = []
    total_links = 0
    for line in lines:
        if "|" in line:
            try:
                name, count_str = line.split("|")
                count = int(count_str.strip())
                if count > 0:
                    tasks.append((name.strip(), count))
                    total_links += count
            except ValueError:
                pass

    if total_links == 0:
        await message.answer("Не нашел валидных строк. Убедись, что используешь формат: <code>Название | 3</code>", parse_mode="HTML")
        return

    # Отправляем первое сообщение-прогресс
    progress_msg = await message.answer(f"⏳ <b>Начинаю создание ссылок...</b>\n\n[░░░░░░░░░░] 0%", parse_mode="HTML")
    
    created_count = 0
    results_text = "🔗 <b>Новые ссылки с заявками:</b>\n\n"
    done_list = []

    for channel_name, count in tasks:
        results_text += f"📁 <b>{channel_name}</b>\n"
        
        for i in range(1, count + 1):
            link_name = f"{channel_name}_{i}"
            
            try:
                # Создаем ссылку с заявкой
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
            # Берем последние 5 строк из сделанного, чтобы сообщение не растягивалось
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
                pass # Игнорируем, если Telegram не дал обновить (например, текст не изменился)
            
            # Задержка между созданием ссылок, чтобы не забанили
            await asyncio.sleep(1.5)

    # --- ФИНАЛ ---
    if created_count > 0:
        try:
            # Отправляем итоговый текст в канал-хранилище
            await bot.send_message(
                chat_id=STORAGE_CHANNEL_ID,
                text=results_text,
                parse_mode="HTML"
            )
            await progress_msg.edit_text(
                f"✅ <b>Готово! Создано {created_count} ссылок из {total_links}.</b>\n\n"
                f"Список отправлен в канал-хранилище.",
                parse_mode="HTML"
            )
        except Exception as e:
            await progress_msg.edit_text(
                f"⚠️ Ссылки созданы, но не удалось отправить в канал: {e}",
                parse_mode="HTML"
            )
    else:
        await progress_msg.edit_text("❌ Не удалось создать ни одной ссылки. Проверь права бота.")

@dp.message(F.document)
async def handle_document(message: Message):
    doc = message.document

    if not doc.file_name.lower().endswith(".txt"):
        return

    # Ставим файл в очередь (чтобы обрабатывались строго по одному)
    async with processing_lock:
        src_path = TEMP_DIR / f"{message.from_user.id}_{doc.file_id}.txt"
        await bot.download(doc, destination=src_path)

        try:
            count = count_lines(src_path)
            base = clean_name(doc.file_name)
            new_name = f"{count}_{base}.txt"
            new_path = TEMP_DIR / new_name

            src_path.rename(new_path)

            # Жесткая пауза в 3 секунды ПЕРЕД отправкой
            await asyncio.sleep(3)

            # Пытаемся отправить файл (с защитой от антифлуда)
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    await message.reply_document(
                        FSInputFile(new_path, filename=new_name)
                    )
                    break # Если отправилось — выходим из цикла попыток
                except TelegramRetryAfter as e:
                    wait_time = e.retry_after + 5
                    print(f"Антифлуд! Ждем {wait_time} секунд...")
                    await asyncio.sleep(wait_time)
                except Exception as e:
                    print(f"Ошибка отправки (попытка {attempt+1}): {e}")
                    await asyncio.sleep(5)

        except Exception as e:
            print(f"Ошибка при обработке файла: {e}")
        finally:
            # Очищаем временные файлы, чтобы не забивать память сервера
            if src_path.exists():
                src_path.unlink(missing_ok=True)
            if new_path.exists():
                new_path.unlink(missing_ok=True)

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
