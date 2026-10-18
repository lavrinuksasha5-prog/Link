import asyncio
import os
import re
import json
from pathlib import Path
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ChatJoinRequest, ChatMemberUpdated, WebAppData
from aiogram.filters import Command, ChatMemberUpdatedFilter, JOIN_TRANSITION
from aiogram.exceptions import TelegramRetryAfter
from aiohttp import web

# Токен и ID каналов
TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080)) 
SOURCE_CHANNEL_ID = -1004306238403 
STORAGE_CHANNEL_ID = -1003721670958

bot = Bot(token=TOKEN)
dp = Dispatcher()

STATS_FILE = Path("stats.json")
LINKS_FILE = Path("links.json")

def load_stats():
    if STATS_FILE.exists():
        with open(STATS_FILE, "r", encoding="utf-8") as f: 
            return json.load(f)
    return {}

def save_stats(stats):
    with open(STATS_FILE, "w", encoding="utf-8") as f: 
        json.dump(stats, f, ensure_ascii=False, indent=2)

def load_links():
    if LINKS_FILE.exists():
        with open(LINKS_FILE, "r", encoding="utf-8") as f: 
            return json.load(f)
    return {}

def save_links(links):
    with open(LINKS_FILE, "w", encoding="utf-8") as f: 
        json.dump(links, f, ensure_ascii=False, indent=2)

# --- СБОР СТАТИСТИКИ ---
@dp.chat_join_request()
async def handle_join_request(update: ChatJoinRequest):
    if update.chat.id != SOURCE_CHANNEL_ID: 
        return
    link_name = update.invite_link.name if update.invite_link else "Unknown"
    stats = load_stats()
    if link_name not in stats: 
        stats[link_name] = {"requests": 0, "joins": 0}
    stats[link_name]["requests"] += 1
    save_stats(stats)

@dp.chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def handle_user_join(update: ChatMemberUpdated):
    if update.chat.id != SOURCE_CHANNEL_ID: 
        return
    link_name = update.invite_link.name if update.invite_link else "Unknown"
    stats = load_stats()
    if link_name not in stats: 
        stats[link_name] = {"requests": 0, "joins": 0}
    stats[link_name]["joins"] += 1
    save_stats(stats)

# --- API ДЛЯ MINI APP (СТАТИСТИКА) ---
async def handle_get_stats(request):
    stats = load_stats()
    links_info = load_links()
    result = []
    
    # Показываем ВСЕ ссылки, даже без заявок
    for name, url in links_info.items():
        reqs = stats.get(name, {}).get("requests", 0)
        joins = stats.get(name, {}).get("joins", 0)
        result.append({"name": name, "requests": reqs, "joins": joins, "url": url})
    
    # Добавляем те, что есть в статистике, но нет в links.json
    for name, v in stats.items():
        if name not in links_info:
            result.append({"name": name, "requests": v["requests"], "joins": v["joins"], "url": ""})
            
    return web.json_response(result, headers={"Access-Control-Allow-Origin": "*"})

# --- НОВЫЙ API ДЛЯ СОЗДАНИЯ ССЫЛОК ИЗ MINI APP ---
async def handle_create_links(request):
    try:
        data = await request.json()
        tasks = data.get("tasks", [])
        
        links_info = load_links()
        created_links = []
        
        for task in tasks:
            channel_name = task["name"]
            count = task["count"]
            clean_name = re.sub(r'[^\w\s\-]', '', channel_name).strip()
            
            channel_links = []
            for i in range(1, count + 1):
                link_name = f"{i}_{clean_name}"
                retry = True
                while retry:
                    try:
                        link = await bot.create_chat_invite_link(
                            chat_id=SOURCE_CHANNEL_ID, 
                            name=link_name, 
                            creates_join_request=True
                        )
                        links_info[link_name] = link.invite_link
                        channel_links.append(link.invite_link)
                        await asyncio.sleep(2.5) # Задержка от флуда
                        retry = False
                    except TelegramRetryAfter as e:
                        await asyncio.sleep(e.retry_after + 2)
                    except Exception:
                        retry = False # Пропускаем, если ошибка не флуд
            
            # Отправляем сразу в канал
            if channel_links:
                links_text = "\n".join(channel_links)
                safe_title = re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', channel_name)
                try:
                    await bot.send_message(
                        STORAGE_CHANNEL_ID, 
                        f"📁 **{safe_title}**\n```\n{links_text}\n```", 
                        parse_mode="MarkdownV2"
                    )
                except: pass
                
            created_links.append({"name": channel_name, "links": channel_links})
            
        save_links(links_info)
        return web.json_response({"status": "ok", "data": created_links})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)

# --- ОБРАБОТКА КНОПОК УДАЛЕНИЯ ИЗ MINI APP ---
@dp.message(F.web_app_data)
async def handle_web_app_data(message: Message):
    try:
        data = json.loads(message.web_app_data.data)
        action = data.get("action")
        stats, links_info = load_stats(), load_links()
        deleted_count = 0

        if action == "delete":
            name_to_delete = data.get("name")
            if name_to_delete in links_info:
                try: 
                    await bot.revoke_chat_invite_link(chat_id=SOURCE_CHANNEL_ID, invite_link=links_info[name_to_delete])
                except: 
                    pass
                del links_info[name_to_delete]
            if name_to_delete in stats:
                del stats[name_to_delete]
                deleted_count += 1

        elif action == "mass_delete":
            for name in data.get("names", []):
                if name in links_info:
                    try: 
                        await bot.revoke_chat_invite_link(chat_id=SOURCE_CHANNEL_ID, invite_link=links_info[name])
                    except: 
                        pass
                    del links_info[name]
                if name in stats:
                    del stats[name]
                    deleted_count += 1

        save_stats(stats)
        save_links(links_info)
        
        if deleted_count > 0:
            await message.answer(f"🗑 <b>Успешно удалено ссылок:</b> {deleted_count}", parse_mode="HTML")
    except Exception as e:
        print("Ошибка WebApp:", e)

# --- КОМАНДА УДАЛЕНИЯ ТЕКСТОМ ---
@dp.message(Command("delete"))
async def cmd_delete(message: Message):
    args = message.text.replace("/delete", "").strip()
    if not args: 
        return
    names_to_delete = args.split()
    stats, links_info = load_stats(), load_links()
    deleted_count = 0

    for name in names_to_delete:
        if name in links_info:
            try: 
                await bot.revoke_chat_invite_link(chat_id=SOURCE_CHANNEL_ID, invite_link=links_info[name])
            except: 
                pass
            del links_info[name]
        if name in stats:
            del stats[name]
            deleted_count += 1

    save_links(links_info)
    save_stats(stats)
    await message.answer(f"✅ Удалено ссылок: {deleted_count}")

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Я бот для создания ссылок.\n\n"
        "Отправь мне список в формате:\n"
        "<code>Название 5</code>\n"
        "<code>Другое название 2</code>\n\n"
        "Или открой Mini App (кнопка внизу), чтобы посмотреть статистику и создать ссылки.",
        parse_mode="HTML"
    )

# --- КРАСИВЫЙ ПРОЦЕСС СОЗДАНИЯ ССЫЛОК (ЧЕРЕЗ ЧАТ) ---
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
                name, count = parts[0].strip(), int(parts[1].strip())
            else:
                match = re.search(r'(\d+)$', line)
                if not match: 
                    continue
                count, name = int(match.group(1)), line[:match.start()].strip()
            
']            if}: count > 0 and name:
                tasks <.append({"name": name, "targetcode": count, "current": >{0})
                total_links += count
        except ValueError: 
            pass

    if total_links == 0: 
        return

    def build_progress_text():
        total_done = sum(t["current"] for t in tasks)
        percent = int((total_done / total_links) * 100)
        
        filled_blocks = int(percent / 10)
        bar = "▰" * filled_blocks + "▱" * (10 - filled_blocks)
        
        tasks_text = ""
        for t in tasks:
            if t["current"] == t["target"]: 
                icon = "✅"
            elif t["current"] > 0: 
                icon = "⏳"
            else: 
                icon = "⏸"
            
            if t["current"] > 0 and t["current"] < t["target"]:
                tasks_text += f"{icon} <b>{t['name']}</b>: <code>{t['current']}/{t['target']}</code>\n"
            else:
                tasks_text += f"{icon} {t['namet['current']}/{t['target']}</code>\n"

        return (
            f"🚀 <b>Генерация ссылок</b>\n\n"
            f"<code>[{bar}]</code> <b>{percent}%</b>\n"
            f"Всего готово: <b>{total_done} из {total_links}</b>\n\n"
            f"<b>Статус каналов:</b>\n"
            f"{tasks_text}"
        )

    progress_msg = await message.answer(build_progress_text(), parse_mode="HTML")
    created_count = 0
    links_info = load_links()

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
                    links_info[link_name] = link.invite_link
                    task["current"] += 1
                    created_count += 1
                    break 
                except TelegramRetryAfter as e:
                    wait_text = (
                        f"🚀 <b>Генерация ссылок</b>\n\n"
                        f"⚠️ <i>Защита от спама. Ждем {e.retry_after} сек...</i>\n\n"
                        f"{build_progress_text().split('Статус каналов:')[0]}"
                    )
                    try: 
                        await progress_msg.edit_text(wait_text, parse_mode="HTML")
                    except: 
                        pass
                    await asyncio.sleep(e.retry_after + 2)
                except Exception as e:
                    current_channel_links.append(f"Ошибка: {link_name}")
                    task["current"] += 1 
                    break
            
            try: 
                await progress_msg.edit_text(build_progress_text(), parse_mode="HTML")
            except: 
                pass
            
            await asyncio.sleep(2.5)

        if current_channel_links:
            links_text = "\n".join(current_channel_links)
            safe_title = re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', channel_name)
            try:
                await bot.send_message(STORAGE_CHANNEL_ID, f"📁 **{safe_title}**\n```\n{links_text}\n```", parse_mode="MarkdownV2")
                await asyncio.sleep(2) 
            except Exception as e:
                await message.answer(f"⚠️ Ошибка отправки в канал: {e}")

    save_links(links_info)
    
    final_text = (
        f"🎉 <b>Все ссылки успешно созданы!</b>\n\n"
        f"📊 <b>Итого:</b> {created_count} ссылок\n"
        f"📁 <b>Каналов обработано:</b> {len(tasks)}\n\n"
        f"<i>Все ссылки отправлены в канал-хранилище. Статистику можно посмотреть в Mini App.</i>"
    )
    try: 
        await progress_msg.edit_text(final_text, parse_mode="HTML")
    except: 
        pass

async def main():
    app = web.Application()
    app.router.add_get('/api/stats', handle_get_stats)
    app.router.add_post('/api/create', handle_create_links) # Новый эндпоинт для Mini App
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
