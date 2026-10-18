import asyncio
import os
import re
import json
from pathlib import Path
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ChatJoinRequest, ChatMemberUpdated
from aiogram.filters import Command, ChatMemberUpdatedFilter, JOIN_TRANSITION
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest
from aiohttp import web

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080)) 
SOURCE_CHANNEL_ID = -1004306238403 
STORAGE_CHANNEL_ID = -1003721670958

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Постоянное хранилище
data_dir = Path("/app/data")
data_dir.mkdir(parents=True, exist_ok=True)

STATS_FILE = data_dir / "stats.json"
LINKS_FILE = data_dir / "links.json"
MESSAGES_FILE = data_dir / "messages.json" # Новый файл для ID сообщений

# --- ФАЙЛЫ ---
def load_stats():
    if STATS_FILE.exists():
        with open(STATS_FILE, "r", encoding="utf-8") as f: return json.load(f)
    return {}

def save_stats(stats):
    with open(STATS_FILE, "w", encoding="utf-8") as f: json.dump(stats, f, ensure_ascii=False, indent=2)

def load_links():
    if LINKS_FILE.exists():
        with open(LINKS_FILE, "r", encoding="utf-8") as f: return json.load(f)
    return {}

def save_links(links):
    with open(LINKS_FILE, "w", encoding="utf-8") as f: json.dump(links, f, ensure_ascii=False, indent=2)

def load_messages():
    if MESSAGES_FILE.exists():
        with open(MESSAGES_FILE, "r", encoding="utf-8") as f: return json.load(f)
    return {}

def save_messages(messages):
    with open(MESSAGES_FILE, "w", encoding="utf-8") as f: json.dump(messages, f, ensure_ascii=False, indent=2)

# --- СТАТИСТИКА ---
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

# --- API ДЛЯ MINI APP ---
async def handle_get_stats(request):
    stats = load_stats()
    links_info = load_links()
    result = []
    for name, url in links_info.items():
        reqs = stats.get(name, {}).get("requests", 0)
        joins = stats.get(name, {}).get("joins", 0)
        result.append({"name": name, "requests": reqs, "joins": joins, "url": url})
    for name, v in stats.items():
        if name not in links_info:
            result.append({"name": name, "requests": v["requests"], "joins": v["joins"], "url": ""})
    return web.json_response(result, headers={"Access-Control-Allow-Origin": "*"})

async def handle_create_single(request):
    headers = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type"}
    if request.method == 'OPTIONS': return web.Response(status=200, headers=headers)

    try:
        data = await request.json()
        channel_name = data.get("name")
        index = data.get("index")
        clean_name = re.sub(r'[^\w\s\-]', '', channel_name).strip()
        link_name = f"{index}_{clean_name}"
        links_info = load_links()
        
        retry = True
        link_url = None
        while retry:
            try:
                link = await bot.create_chat_invite_link(chat_id=SOURCE_CHANNEL_ID, name=link_name, creates_join_request=True)
                link_url = link.invite_link
                links_info[link_name] = link_url
                await asyncio.sleep(2.0) 
                retry = False
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 2)
            except Exception as e:
                return web.json_response({"status": "error", "message": str(e)}, status=500, headers=headers)

        save_links(links_info)
        return web.json_response({"status": "ok", "link": link_url}, headers=headers)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500, headers=headers)

async def handle_delete_links(request):
    headers = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type"}
    if request.method == 'OPTIONS': return web.Response(status=200, headers=headers)

    try:
        data = await request.json()
        names_to_delete = data.get("names", [])
        stats, links_info = load_stats(), load_links()
        messages = load_messages()
        deleted_count = 0

        for name in names_to_delete:
            # 1. Удаляем ссылку из Telegram
            if name in links_info:
                try: await bot.revoke_chat_invite_link(chat_id=SOURCE_CHANNEL_ID, invite_link=links_info[name])
                except: pass
                del links_info[name]

            # 2. Удаляем статистику
            if name in stats:
                del stats[name]
                deleted_count += 1

            # 3. Удаляем сообщение из канала (если знаем ID)
            if name in messages:
                try: 
                    await bot.delete_message(chat_id=STORAGE_CHANNEL_ID, message_id=messages[name])
                except: pass
                del messages[name]

        save_stats(stats)
        save_links(links_info)
        save_messages(messages)
        
        return web.json_response({"status": "ok", "deleted": deleted_count}, headers=headers)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500, headers=headers)

# --- ОБРАБОТКА КНОПОК ---
@dp.message(F.web_app_data)
async def handle_web_app_data(message: Message):
    pass

# --- КОМАНДА УДАЛЕНИЯ ---
@dp.message(Command("delete"))
async def cmd_delete(message: Message):
    args = message.text.replace("/delete", "").strip()
    if not args: return
    names_to_delete = args.split()
    stats, links_info, messages = load_stats(), load_links(), load_messages()
    deleted_count = 0
    for name in names_to_delete:
        if name in links_info:
            try: await bot.revoke_chat_invite_link(chat_id=SOURCE_CHANNEL_ID, invite_link=links_info[name])
            except: pass
            del links_info[name]
        if name in stats:
            del stats[name]
            deleted_count += 1
        if name in messages:
            try: await bot.delete_message(chat_id=STORAGE_CHANNEL_ID, message_id=messages[name])
            except: pass
            del messages[name]
    save_links(links_info)
    save_stats(stats)
    save_messages(messages)
    await message.answer(f"✅ Удалено ссылок и сообщений: {deleted_count}")

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Привет! Открой Mini App, чтобы управлять ссылками.", parse_mode="HTML")

# --- ПРОЦЕСС СОЗДАНИЯ (ЧАТ) ---
@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text_list(message: Message):
    args = message.text.strip()
    if not args: return
    lines = [line.strip() for line in args.split("\n") if line.strip()]
    tasks, total_links = [], 0
    
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
        total_done = sum(t["current"] for t in tasks)
        percent = int((total_done / total_links) * 100)
        filled_blocks = int(percent / 10)
        bar = "▰" * filled_blocks + "▱" * (10 - filled_blocks)
        tasks_text = ""
        for t in tasks:
            icon = "✅" if t["current"] == t["target"] else ("⏳" if t["current"] > 0 else "⏸")
            name_fmt = f"<b>{t['name']}</b>" if 0 < t["current"] < t["target"] else t['name']
            tasks_text += f"{icon} {name_fmt}: <code>{t['current']}/{t['target']}</code>\n"
        return f"🚀 <b>Генерация ссылок</b>\n\n<code>[{bar}]</code> <b>{percent}%</b>\nГотово: <b>{total_done} из {total_links}</b>\n\n<b>Статус:</b>\n{tasks_text}"

    try: await message.delete()
    except: pass
    progress_msg = await message.answer(build_progress_text(), parse_mode="HTML")
    created_count = 0
    links_info, messages = load_links(), load_messages()

    for task in tasks:
        channel_name = task["name"]
        clean_link_name = re.sub(r'[^\w\s\-]', '', channel_name).strip()
        current_channel_links = []
        for i in range(1, task["target"] + 1):
            link_name = f"{i}_{clean_link_name}"
            while True:
                try:
                    link = await bot.create_chat_invite_link(chat_id=SOURCE_CHANNEL_ID, name=link_name, creates_join_request=True)
                    current_channel_links.append(link.invite_link)
                    links_info[link_name] = link.invite_link
                    task["current"] += 1
                    created_count += 1
                    break 
                except TelegramRetryAfter as e:
                    try: await progress_msg.edit_text(build_progress_text() + f"\n⏳ Ждем {e.retry_after} сек...", parse_mode="HTML")
                    except: pass
                    await asyncio.sleep(e.retry_after + 2)
                except Exception: break
            try: await progress_msg.edit_text(build_progress_text(), parse_mode="HTML")
            except: pass
            await asyncio.sleep(2.0)

        if current_channel_links:
            links_text = "\n".join(current_channel_links)
            safe_title = re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', channel_name)
            try: 
                # Сохраняем ID сообщения, отправленного в канал
                sent_msg = await bot.send_message(STORAGE_CHANNEL_ID, f"📁 **{safe_title}**\n```\n{links_text}\n```", parse_mode="MarkdownV2")
                messages[channel_name] = sent_msg.message_id
            except: pass

    save_links(links_info)
    save_messages(messages)
    await progress_msg.edit_text(f"🎉 <b>Готово!</b> Создано {created_count} ссылок.", parse_mode="HTML")

# --- ЗАПУСК ---
async def main():
    app = web.Application()
    app.router.add_get('/api/stats', handle_get_stats)
    app.router.add_post('/api/create_single', handle_create_single)
    app.router.add_options('/api/create_single', handle_create_single)
    app.router.add_post('/api/delete', handle_delete_links)
    app.router.add_options('/api/delete', handle_delete_links)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
