import asyncio
import os
import re
import json
from pathlib import Path
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ChatJoinRequest, ChatMemberUpdated
from aiogram.filters import Command, ChatMemberUpdatedFilter, JOIN_TRANSITION
from aiogram.exceptions import TelegramRetryAfter
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
# Railway обычно сам выдает порт, если его нет - используем 8080
PORT = int(os.getenv("PORT", 8080)) 
SOURCE_CHANNEL_ID = -1004306238403 
STORAGE_CHANNEL_ID = -1003721670958

bot = Bot(token=TOKEN)
dp = Dispatcher()
STATS_FILE = Path("stats.json")

def load_stats():
    if STATS_FILE.exists():
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_stats(stats):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

# --- СБОР СТАТИСТИКИ ---
@dp.chat_join_request()
async def handle_join_request(update: ChatJoinRequest):
    if update.chat.id != SOURCE_CHANNEL_ID: return
    link_name = update.invite_link.name if update.invite_link else "Unknown"
    stats = load_stats()
    if link_name not in stats: stats[link_name] = {"requests": 0, "joins": 0}
    stats[link_name]["requests"] += 1
    save_stats(stats)

@dp.chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def handle_user_join(update: ChatMemberUpdated):
    if update.chat.id != SOURCE_CHANNEL_ID: return
    link_name = update.invite_link.name if update.invite_link else "Unknown"
    stats = load_stats()
    if link_name not in stats: stats[link_name] = {"requests": 0, "joins": 0}
    stats[link_name]["joins"] += 1
    save_stats(stats)

# --- API ДЛЯ MINI APP (Отдаем JSON) ---
async def handle_get_stats(request):
    stats = load_stats()
    # Превращаем словарь в список для удобства сортировки на фронтенде
    result = [{"name": k, "requests": v["requests"], "joins": v["joins"]} for k, v in stats.items()]
    return web.json_response(result, headers={"Access-Control-Allow-Origin": "*"})

# --- ГЕНЕРАЦИЯ ССЫЛОК (Твой старый код) ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Бот готов! Скидывай список.")

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text_list(message: Message):
    args = message.text.strip()
    if not args: return

    lines = [line.strip() for line in args.split("\n") if line.strip()]
    tasks = []
    total_links = 0
    
    for line in lines:
        try:
            if "|" in line:
                parts = line.rsplit("|", 1)
                name, count = parts[0].strip(), int(parts[1].strip())
            else:
                match = re.search(r'(\d+)$', line)
                if not match: continue
                count, name = int(match.group(1)), line[:match.start()].strip()
            
            if count > 0 and name:
                tasks.append({"name": name, "target": count, "current": 0})
                total_links += count
        except ValueError: pass

    if total_links == 0: return

    def build_progress_text():
        text = "📋 <b>Список задач:</b>\n\n"
        for t in tasks:
            if t["current"] == t["target"]: status = f"✅ {t['name']}: {t['current']}/{t['target']}"
            elif t["current"] > 0: status = f"⏳ {t['name']}: {t['current']}/{t['target']}"
            else: status = f"⏸ {t['name']}: {t['current']}/{t['target']}"
            text += status + "\n"
        return text

    progress_msg = await message.answer(build_progress_text(), parse_mode="HTML")
    created_count = 0

    for task in tasks:
        channel_name = task["name"]
        clean_link_name = re.sub(r'[^\w\s\-]', '', channel_name).strip()
        current_channel_links = []

        for i in range(1, task["target"] + 1):
            link_name = f"{i}_{clean_link_name}"
            while True:
                try:
                    link = await bot.create_chat_invite_link(
                        chat_id=SOURCE_CHANNEL_ID, name=link_name, creates_join_request=True
                    )
                    current_channel_links.append(link.invite_link)
                    task["current"] += 1
                    created_count += 1
                    break 
                except TelegramRetryAfter as e:
                    await progress_msg.edit_text(build_progress_text() + f"\n⏳ Ждем {e.retry_after} сек...", parse_mode="HTML")
                    await asyncio.sleep(e.retry_after + 2)
                except Exception as e:
                    current_channel_links.append(f"Ошибка: {link_name}")
                    task["current"] += 1 
                    break
            
            await progress_msg.edit_text(build_progress_text(), parse_mode="HTML")
            await asyncio.sleep(3)

        if current_channel_links:
            links_text = "\n".join(current_channel_links)
            safe_title = re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', channel_name)
            try:
                await bot.send_message(STORAGE_CHANNEL_ID, f"📁 **{safe_title}**\n```\n{links_text}\n```", parse_mode="MarkdownV2")
                await asyncio.sleep(2) 
            except Exception as e:
                await message.answer(f"⚠️ Ошибка отправки в канал: {e}")

    await progress_msg.edit_text(build_progress_text() + f"\n\n✅ <b>Готово! Создано {created_count} ссылок.</b>", parse_mode="HTML")

# --- ЗАПУСК СЕРВЕРА И БОТА ---
async def main():
    # Создаем веб-приложение для API
    app = web.Application()
    app.router.add_get('/api/stats', handle_get_stats)
    
    # Запускаем сервер
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f"Сервер запущен на порту {PORT}")

    # Запускаем бота
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
