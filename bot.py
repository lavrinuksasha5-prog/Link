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

# --- ВОССТАНОВЛЕНИЕ СТАРЫХ ССЫЛОК ---
@dp.message(Command("recreate"))
async def cmd_recreate(message: Message):
    args = message.text.replace("/recreate", "").strip()
    if not args: return await message.answer("Пример:\n/recreate\nДжо 3")
    lines = [line.strip() for line in args.split("\n") if line.strip()]
    tasks = []
    for line in lines:
        try:
            if "|" in line:
                p = line.rsplit("|", 1); tasks.append({"name": p[0].strip(), "target": int(p[1].strip())})
            else:
                m = re.search(r'(\d+)$', line)
                if m: tasks.append({"name": line[:m.start()].strip(), "target": int(m.group(1))})
        except: pass

    msg = await message.answer("🔄 Восстанавливаю...")
    links_info = load_links()
    count = 0
    for t in tasks:
        clean_name = t["name"]
        for i in range(1, t["target"] + 1):
            try:
                link = await bot.create_chat_invite_link(chat_id=SOURCE_CHANNEL_ID, name=f"{i}_{clean_name}", creates_join_request=True)
                links_info[f"{i}_{clean_name}"] = link.invite_link
                count += 1
                await asyncio.sleep(1.5)
            except TelegramRetryAfter as e: await asyncio.sleep(e.retry_after + 2)
            except Exception: pass
    save_links(links_info)
    await msg.edit_text(f"✅ Восстановлено: {count}")

# --- СТАТИСТИКА ---
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
        with open(REQUESTS_LOG_FILE, "r", encoding="utf-8") as f: log_data = json.load(f)
    log_data.append({"link": link_name, "timestamp": int(datetime.now().timestamp())})
    if len(log_data) > 2000: log_data = log_data[-2000:]
    with open(REQUESTS_LOG_FILE, "w", encoding="utf-8") as f: json.dump(log_data, f)

@dp.chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def handle_user_join(update: ChatMemberUpdated):
    if update.chat.id != SOURCE_CHANNEL_ID: return
    link_name = update.invite_link.name if update.invite_link else "Unknown"
    stats = load_stats()
    if link_name not in stats: stats[link_name] = {"requests": 0, "joins": 0}
    stats[link_name]["joins"] += 1
    save_stats(stats)

# --- API ---
async def handle_get_stats(request):
    stats, links = load_stats(), load_links()
    result = []
    for name, url in links.items():
        result.append({"name": name, "requests": stats.get(name, {}).get("requests", 0), "joins": stats.get(name, {}).get("joins", 0), "url": url})
    for name, v in stats.items():
        if name not in links: result.append({"name": name, "requests": v["requests"], "joins": v["joins"], "url": ""})
    return web.json_response(result, headers={"Access-Control-Allow-Origin": "*"})

async def handle_get_dashboard(request):
    stats = load_stats()
    total = sum(v["requests"] for v in stats.values())
    today, yesterday = 0, 0
    if REQUESTS_LOG_FILE.exists():
        with open(REQUESTS_LOG_FILE, "r", encoding="utf-8") as f: log_data = json.load(f)
        now = datetime.utcnow() + timedelta(hours=3)
        start_today = now.replace(hour=15, minute=0, second=0, microsecond=0) if now.hour >= 15 else (now - timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
        start_yesterday = start_today - timedelta(days=1)
        st, sy = int(start_today.timestamp()), int(start_yesterday.timestamp())
        for e in log_data:
            if e["timestamp"] >= st: today += 1
            elif sy <= e["timestamp"] < st: yesterday += 1
    return web.json_response({"total_requests": total, "today_requests": today, "yesterday_requests": yesterday}, headers={"Access-Control-Allow-Origin": "*"})

async def handle_get_daily_report(request):
    report = {"period": "", "total_requests": 0, "details": []}
    if not REQUESTS_LOG_FILE.exists(): return web.json_response(report, headers={"Access-Control-Allow-Origin": "*"})
    with open(REQUESTS_LOG_FILE, "r", encoding="utf-8") as f: log_data = json.load(f)
    now = datetime.utcnow() + timedelta(hours=3)
    end_time = now.replace(hour=15, minute=0, second=0, microsecond=0) if now.hour >= 15 else (now - timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
    start_time = end_time - timedelta(days=1)
    report["period"] = f"{start_time.strftime('%d.%m')} 15:00 — {end_time.strftime('%d.%m')} 15:00"
    st, en = int(start_time.timestamp()), int(end_time.timestamp())
    counter = Counter()
    for e in log_data:
        if st <= e["timestamp"] < en: counter[e["link"]] += 1; report["total_requests"] += 1
    report["details"] = [{"link": k, "count": v} for k, v in counter.most_common()]
    return web.json_response(report, headers={"Access-Control-Allow-Origin": "*"})

async def handle_create_single(request):
    headers = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type"}
    if request.method == 'OPTIONS': return web.Response(status=200, headers=headers)
    try:
        data = await request.json()
        channel_name, index, count = data.get("name"), data.get("index"), data.get("count", 1)
        clean_name = channel_name # БЕЗ очистки, чтобы сохранить русские буквы
        link_name = f"{index}_{clean_name}"
        links, msgs = load_links(), load_messages()
        
        retry = True
        url = None
        while retry:
            try:
                link = await bot.create_chat_invite_link(chat_id=SOURCE_CHANNEL_ID, name=link_name, creates_join_request=True)
                url = link.invite_link; links[link_name] = url
                await asyncio.sleep(2.0); retry = False
            except TelegramRetryAfter as e: await asyncio.sleep(e.retry_after + 2)
            except Exception as e: return web.json_response({"status": "error", "message": str(e)}, status=500, headers=headers)
        save_links(links)
        
        if index == count:
            c_links = [links[f"{i}_{clean_name}"] for i in range(1, count + 1) if f"{i}_{clean_name}" in links]
            if c_links:
                try:
                    safe_title = re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', channel_name)
                    sent = await bot.send_message(STORAGE_CHANNEL_ID, f"📁 **{safe_title}**\n```\n{chr(10).join(c_links)}\n```", parse_mode="MarkdownV2")
                    msgs[channel_name] = sent.message_id; save_messages(msgs)
                except: pass
        return web.json_response({"status": "ok", "link": url}, headers=headers)
    except Exception as e: return web.json_response({"status": "error", "message": str(e)}, status=500, headers=headers)

async def handle_delete_links(request):
    headers = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type"}
    if request.method == 'OPTIONS': return web.Response(status=200, headers=headers)
    try:
        data = await request.json()
        names = data.get("names", [])
        stats, links, msgs = load_stats(), load_links(), load_messages()
        count = 0
        del_channels = set()
        for name in names:
            if name in links:
                try: await bot.revoke_chat_invite_link(chat_id=SOURCE_CHANNEL_ID, invite_link=links[name])
                except: pass
                del links[name]
            if name in stats: del stats[name]; count += 1
            parts = name.split("_", 1)
            if len(parts) == 2: del_channels.add(parts[1])
        for ch in del_channels:
            if ch in msgs:
                try: await bot.delete_message(chat_id=STORAGE_CHANNEL_ID, message_id=msgs[ch])
                except: pass
                del msgs[ch]
        save_stats(stats); save_links(links); save_messages(msgs)
        return web.json_response({"status": "ok", "deleted": count}, headers=headers)
    except Exception as e: return web.json_response({"status": "error", "message": str(e)}, status=500, headers=headers)

@dp.message(Command("delete"))
async def cmd_delete(message: Message):
    args = message.text.replace("/delete", "").strip()
    if not args: return
    names = args.split()
    stats, links, msgs = load_stats(), load_links(), load_messages()
    count = 0
    del_channels = set()
    for name in names:
        if name in links:
            try: await bot.revoke_chat_invite_link(chat_id=SOURCE_CHANNEL_ID, invite_link=links[name])
            except: pass
            del links[name]
        if name in stats: del stats[name]; count += 1
        parts = name.split("_", 1)
        if len(parts) == 2: del_channels.add(parts[1])
    for ch in del_channels:
        if ch in msgs:
            try: await bot.delete_message(chat_id=STORAGE_CHANNEL_ID, message_id=msgs[ch])
            except: pass
            del msgs[ch]
    save_stats(stats); save_links(links); save_messages(msgs)
    await message.answer(f"✅ Удалено: {count}")

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Привет! Открой Mini App.", parse_mode="HTML")

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text_list(message: Message):
    args = message.text.strip()
    if not args: return
    lines = [line.strip() for line in args.split("\n") if line.strip()]
    tasks, total_links = [], 0
    for line in lines:
        try:
            if "|" in line:
                p = line.rsplit("|", 1); name, count = p[0].strip(), int(p[1].strip())
            else:
                m = re.search(r'(\d+)$', line)
                if not m: continue
                name, count = line[:m.start()].strip(), int(m.group(1))
            if count > 0 and name: tasks.append({"name": name, "target": count, "current": 0}); total_links += count
        except: pass
    if total_links == 0: return
    
    def build_text():
        done = sum(t["current"] for t in tasks)
        pct = int((done / total_links) * 100)
        bar = "▰" * int(pct / 10) + "▱" * (10 - int(pct / 10))
        t_text = "".join([f"{'✅' if t['current']==t['target'] else ('⏳' if t['current']>0 else '⏸')} {f'<b>{t[chr(39)+chr(110)+chr(97)+chr(109)+chr(101)+chr(39)]}</b>' if 0<t['current']<t['target'] else t['name']}: <code>{t['current']}/{t['target']}</code>\n" for t in tasks])
        return f"🚀 <b>Генерация</b>\n<code>[{bar}]</code> <b>{pct}%</b>\nГотово: <b>{done} из {total_links}</b>\n\n{t_text}"

    try: await message.delete()
    except: pass
    msg = await message.answer(build_text(), parse_mode="HTML")
    created, links, msgs = 0, load_links(), load_messages()
    for task in tasks:
        clean_name = task["name"]
        c_links = []
        for i in range(1, task["target"] + 1):
            while True:
                try:
                    link = await bot.create_chat_invite_link(chat_id=SOURCE_CHANNEL_ID, name=f"{i}_{clean_name}", creates_join_request=True)
                    c_links.append(link.invite_link); links[f"{i}_{clean_name}"] = link.invite_link
                    task["current"] += 1; created += 1
                    break 
                except TelegramRetryAfter as e:
                    try: await msg.edit_text(build_text() + f"\n⏳ Ждем {e.retry_after}...", parse_mode="HTML")
                    except: pass
                    await asyncio.sleep(e.retry_after + 2)
                except: break
            try: await msg.edit_text(build_text(), parse_mode="HTML")
            except: pass
            await asyncio.sleep(2.0)
        if c_links:
            try:
                safe_title = re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', task['name'])
                sent = await bot.send_message(STORAGE_CHANNEL_ID, f"📁 **{safe_title}**\n```\n{chr(10).join(c_links)}\n```", parse_mode="MarkdownV2")
                msgs[task["name"]] = sent.message_id
            except: pass
    save_links(links); save_messages(msgs)
    await msg.edit_text(f"🎉 Готово! {created} ссылок.", parse_mode="HTML")

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
