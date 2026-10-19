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
from datetime import datetime, timedelta
from collections import Counter

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080)) 
SOURCE_CHANNEL_ID = -1004306238403 
STORAGE_CHANNEL_ID = -1003721670958

bot = Bot(token=TOKEN)
dp = Dispatcher()

data_dir = Path("/app/data")
data_dir.mkdir(parents=True, exist_ok=True)

STATS_FILE = data_dir / "stats.json"
LINKS_FILE = data_dir / "links.json"
MESSAGES_FILE = data_dir / "messages.json"
REQUESTS_LOG_FILE = data_dir / "requests_log.json"

# --- ФУНКЦИИ РАБОТЫ С ФАЙЛАМИ ---
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

# --- ВОССТАНОВЛЕНИЕ БАЗЫ ИЗ КАНАЛА ---
@dp.message(Command("restore"))
async def cmd_restore(message: Message):
    msg = await message.answer("⏳ Читаю историю канала и восстанавливаю базу...")
    
    links_info = load_links()
    messages = load_messages()
    restored_count = 0
    
    try:
        # Проходимся по последним 1000 сообщениям в канале (чтобы не зависнуть навсегда)
        async for msg_obj in bot.get_chat_history(chat_id=STORAGE_CHANNEL_ID, limit=1000):
            if msg_obj.text and "https://t.me/+" in msg_obj.text:
                # Парсим текст сообщения. Ищем заголовок и ссылки
                text = msg_obj.text
                
                # Пример текста: 📁 **Название**\n```\nhttps://t.me/+xxx\n```
                lines = text.split("\n")
                channel_name = ""
                
                # Ищем название канала (строка с 📁)
                for line in lines:
                    if "📁" in line:
                        # Убираем эмодзи, звездочки и пробелы
                        channel_name = re.sub(r'[📁*]', '', line).strip()
                        break
                
                if not channel_name:
                    continue
                    
                # Ищем ссылки
                found_links = re.findall(r'https://t\.me/\+\S+', text)
                if not found_links:
                    continue
                    
                # Восстанавливаем ссылки в базу
                for idx, link in enumerate(found_links, start=1):
                    link_name = f"{idx}_{channel_name}"
                    if link_name not in links_info:
                        links_info[link_name] = link
                        restored_count += 1
                
                # Запоминаем ID сообщения
                messages[channel_name] = msg_obj.message_id
                
        save_links(links_info)
        save_messages(messages)
        
        await msg.edit_text(f"✅ <b>База восстановлена!</b>\n\nНайдено и добавлено ссылок: {restored_count}", parse_mode="HTML")
        
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка при восстановлении: {e}")

# --- СБОР СТАТИСТИКИ ---
@dp.chat_join_request()
async def handle_join_request(update: ChatJoinRequest):
    if update.chat.id != SOURCE_CHANNEL_ID: return
    link_name = update.invite_link.name if update.invite_link else "Unknown"
    
    stats = load_stats()
    if link_name not in stats: stats[link_name] = {"requests": 0, "joins": 0}
    stats[link_name]["requests"] += 1
    save_stats(stats)

    log_data = []
    if REQUESTS_LOG_FILE.exists():
        with open(REQUESTS_LOG_FILE, "r", encoding="utf-8") as f:
            log_data = json.load(f)
            
    log_data.append({"link": link_name, "timestamp": int(datetime.now().timestamp())})
    if len(log_data) > 2000: log_data = log_data[-2000:]
    
    with open(REQUESTS_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log_data, f)

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

async def handle_get_dashboard(request):
    stats = load_stats()
    total_requests = sum(v["requests"] for v in stats.values())
    today_requests = 0
    yesterday_requests = 0
    
    if REQUESTS_LOG_FILE.exists():
        with open(REQUESTS_LOG_FILE, "r", encoding="utf-8") as f:
            log_data = json.load(f)
            
        now_kyiv = datetime.utcnow() + timedelta(hours=3)
        
        if now_kyiv.hour >= 15:
            start_today = now_kyiv.replace(hour=15, minute=0, second=0, microsecond=0)
        else:
            start_today = (now_kyiv - timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
            
        start_yesterday = start_today - timedelta(days=1)
        
        start_today_ts = int(start_today.timestamp())
        start_yesterday_ts = int(start_yesterday.timestamp())
        
        for entry in log_data:
            ts = entry["timestamp"]
            if ts >= start_today_ts:
                today_requests += 1
            elif start_yesterday_ts <= ts < start_today_ts:
                yesterday_requests += 1

    return web.json_response({
        "total_requests": total_requests,
        "today_requests": today_requests,
        "yesterday_requests": yesterday_requests
    }, headers={"Access-Control-Allow-Origin": "*"})

async def handle_get_daily_report(request):
    report = {"period": "", "total_requests": 0, "details": []}
    
    if not REQUESTS_LOG_FILE.exists():
        return web.json_response(report, headers={"Access-Control-Allow-Origin": "*"})
        
    with open(REQUESTS_LOG_FILE, "r", encoding="utf-8") as f:
        log_data = json.load(f)
        
    now_kyiv = datetime.utcnow() + timedelta(hours=3)
    
    if now_kyiv.hour >= 15:
        end_time = now_kyiv.replace(hour=15, minute=0, second=0, microsecond=0)
        start_time = end_time - timedelta(days=1)
    else:
        end_time = (now_kyiv - timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
        start_time = end_time - timedelta(days=1)
        
    start_ts = int(start_time.timestamp())
    end_ts = int(end_time.timestamp())
    
    report["period"] = f"{start_time.strftime('%d.%m')} 15:00 — {end_time.strftime('%d.%m')} 15:00"
    
    counter = Counter()
    for entry in log_data:
        ts = entry["timestamp"]
        if start_ts <= ts < end_ts:
            counter[entry["link"]] += 1
            report["total_requests"] += 1
            
    report["details"] = [{"link": k, "count": v} for k, v in counter.most_common()]
    
    return web.json_response(report, headers={"Access-Control-Allow-Origin": "*"})

async def handle_create_single(request):
    headers = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type"}
    if request.method == 'OPTIONS': return web.Response(status=200, headers=headers)

    try:
        data = await request.json()
        channel_name = data.get("name")
        index = data.get("index")
        count = data.get("count", 1)
        
        clean_name = re.sub(r'[^\w\s\-]', '', channel_name).strip()
        link_name = f"{index}_{clean_name}"
        
        links_info = load_links()
        messages = load_messages()
        
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
        
        if index == count:
            channel_links = [links_info[f"{i}_{clean_name}"] for i in range(1, count + 1) if f"{i}_{clean_name}" in links_info]
            if channel_links:
                links_text = "\n".join(channel_links)
                safe_title = re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', channel_name)
                try:
                    sent_msg = await bot.send_message(STORAGE_CHANNEL_ID, f"📁 **{safe_title}**\n```\n{links_text}\n```", parse_mode="MarkdownV2")
                    messages[channel_name] = sent_msg.message_id
                    save_messages(messages)
                except Exception as e:
                    print("Ошибка отправки в канал:", e)

        return web.json_response({"status": "ok", "link": link_url}, headers=headers)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500, headers=headers)

async def handle_delete_links(request):
    headers = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type"}
    if request.method == 'OPTIONS': return web.Response(status=200, headers=headers)

    try:
        data = await request.json()
        names_to_delete = data.get("names", [])
        stats, links_info, messages = load_stats(), load_links(), load_messages()
        deleted_count = 0
        deleted_channels = set()

        for name in names_to_delete:
            if name in links_info:
                try: await bot.revoke_chat_invite_link(chat_id=SOURCE_CHANNEL_ID, invite_link=links_info[name])
                except: pass
                del links_info[name]

            if name in stats:
                del stats[name]
                deleted_count += 1

            parts = name.split("_", 1)
            if len(parts) == 2: deleted_channels.add(parts[1])

        for channel_name in deleted_channels:
            if channel_name in messages:
                try: await bot.delete_message(chat_id=STORAGE_CHANNEL_ID, message_id=messages[channel_name])
                except: pass
                del messages[channel_name]

        save_stats(stats)
        save_links(links_info)
        save_messages(messages)
        
        return web.json_response({"status": "ok", "deleted": deleted_count}, headers=headers)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500, headers=headers)

# --- КОМАНДА УДАЛЕНИЯ ---
@dp.message(Command("delete"))
async def cmd_delete(message: Message):
    args = message.text.replace("/delete", "").strip()
    if not args: return
    names_to_delete = args.split()
    stats, links_info, messages = load_stats(), load_links(), load_messages()
    deleted_count = 0
    deleted_channels = set()
    
    for name in names_to_delete:
        if name in links_info:
            try: await bot.revoke_chat_invite_link(chat_id=SOURCE_CHANNEL_ID, invite_link=links_info[name])
            except: pass
            del links_info[name]
        if name in stats:
            del stats[name]
            deleted_count += 1
        parts = name.split("_", 1)
        if len(parts) == 2: deleted_channels.add(parts[1])
            
    for channel_name in deleted_channels:
        if channel_name in messages:
            try: await bot.delete_message(chat_id=STORAGE_CHANNEL_ID, message_id=messages[channel_name])
            except: pass
            del messages[channel_name]

    save_links(links_info)
    save_stats(stats)
    save_messages(messages)
    await message.answer(f"✅ Удалено ссылок и сообщений: {deleted_count}")

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Привет! Открой Mini App, чтобы управлять ссылками.", parse_mode="HTML")

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
    app.router.add_get('/api/dashboard', handle_get_dashboard)
    app.router.add_get('/api/daily_report', handle_get_daily_report)
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
