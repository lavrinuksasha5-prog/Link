import asyncio
import os
import re
import json
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ChatJoinRequest, ChatMemberUpdated, BufferedInputFile
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

data_dir = Path("/app/data")
data_dir.mkdir(parents=True, exist_ok=True)

STATS_FILE = data_dir / "stats.json"
LINKS_FILE = data_dir / "links.json"
MESSAGES_FILE = data_dir / "messages.json"
REQUESTS_LOG_FILE = data_dir / "requests_log.json"

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

# --- УМНЫЙ СБОР СТАТИСТИКИ ---
@dp.chat_join_request()
async def handle_join_request(update: ChatJoinRequest):
    if update.chat.id != SOURCE_CHANNEL_ID: return
    link_name = update.invite_link.name if update.invite_link else "Unknown"
    user_id = update.from_user.id
    
    stats = load_stats()
    if link_name not in stats: 
        stats[link_name] = {"requests": [], "joins": []}
    
    # Конвертация старых данных (чисел) в список с "базой"
    if isinstance(stats[link_name].get("requests"), int):
        stats[link_name]["requests"] = [f"old_{i}" for i in range(stats[link_name]["requests"])]
    if isinstance(stats[link_name].get("joins"), int):
        stats[link_name]["joins"] = [f"old_{i}" for i in range(stats[link_name]["joins"])]

    # Считаем только уникальных
    if user_id not in stats[link_name]["requests"]:
        stats[link_name]["requests"].append(user_id)
        save_stats(stats)
        
        log_data = []
        if REQUESTS_LOG_FILE.exists():
            with open(REQUESTS_LOG_FILE, "r", encoding="utf-8") as f: log_data = json.load(f)
        
        log_data.append({
            "link": link_name, 
            "user_id": user_id, 
            "timestamp": int(datetime.now().timestamp())
        })
        if len(log_data) > 10000: log_data = log_data[-10000:]
        
        with open(REQUESTS_LOG_FILE, "w", encoding="utf-8") as f: json.dump(log_data, f)

@dp.chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def handle_user_join(update: ChatMemberUpdated):
    if update.chat.id != SOURCE_CHANNEL_ID: return
    link_name = update.invite_link.name if update.invite_link else "Unknown"
    user_id = update.new_chat_member.user.id
    
    stats = load_stats()
    if link_name not in stats: stats[link_name] = {"requests": [], "joins": []}
    if isinstance(stats[link_name].get("requests"), int):
        stats[link_name]["requests"] = [f"old_{i}" for i in range(stats[link_name]["requests"])]
    if isinstance(stats[link_name].get("joins"), int):
        stats[link_name]["joins"] = [f"old_{i}" for i in range(stats[link_name]["joins"])]
    
    if user_id not in stats[link_name]["joins"]:
        stats[link_name]["joins"].append(user_id)
        save_stats(stats)

# --- КОМАНДА /report ---
@dp.message(Command("report"))
async def cmd_report(message: Message):
    if not REQUESTS_LOG_FILE.exists():
        return await message.answer("Данных пока нет.")
        
    with open(REQUESTS_LOG_FILE, "r", encoding="utf-8") as f: log_data = json.load(f)
    
    daily_unique = {}
    total_unique = set()
    
    for entry in log_data:
        total_unique.add((entry["user_id"], entry["link"]))
        dt = datetime.utcfromtimestamp(entry["timestamp"]) + timedelta(hours=3)
        date_str = dt.strftime("%d.%m.%Y")
        
        if date_str not in daily_unique: daily_unique[date_str] = set()
        daily_unique[date_str].add((entry["user_id"], entry["link"]))

    report_text = "📊 <b>Отчет по заявкам:</b>\n\n"
    for date_str, users in sorted(daily_unique.items(), reverse=True)[:7]:
        report_text += f"За {date_str}: {len(users)} заявок\n"
        
    report_text += f"\n📈 <b>Всего за все время (уникальных):</b> {len(total_unique)} заявок"
    
    # Добавляем старую базу
    stats = load_stats()
    old_base = 0
    for link, data in stats.items():
        if isinstance(data.get("requests"), list):
            old_base += len([x for x in data["requests"] if isinstance(x, str) and x.startswith("old_")])
            
    if old_base > 0:
        report_text += f"\n\n📦 <i>(Включая старую базу: {old_base} заявок)</i>"

    await message.answer(report_text, parse_mode="HTML")

# --- API ДЛЯ MINI APP ---
async def handle_get_stats(request):
    stats, links = load_stats(), load_links()
    result = []
    for name, url in links.items():
        reqs = len(stats.get(name, {}).get("requests", []))
        joins = len(stats.get(name, {}).get("joins", []))
        result.append({"name": name, "requests": reqs, "joins": joins, "url": url})
    for name, v in stats.items():
        if name not in links:
            result.append({"name": name, "requests": len(v.get("requests", [])), "joins": len(v.get("joins", [])), "url": ""})
    return web.json_response(result, headers={"Access-Control-Allow-Origin": "*"})

async def handle_get_dashboard(request):
    stats = load_stats()
    total = sum(len(v.get("requests", [])) for v in stats.values())
    today, yesterday = 0, 0
    if REQUESTS_LOG_FILE.exists():
        with open(REQUESTS_LOG_FILE, "r", encoding="utf-8") as f: log_data = json.load(f)
        now = datetime.utcnow() + timedelta(hours=3)
        start_today = now.replace(hour=15, minute=0, second=0, microsecond=0) if now.hour >= 15 else (now - timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
        start_yesterday = start_today - timedelta(days=1)
        st, sy = int(start_today.timestamp()), int(start_yesterday.timestamp())
        
        today_users = set()
        yest_users = set()
        for e in log_data:
            if e["timestamp"] >= st: today_users.add((e["user_id"], e["link"]))
            elif sy <= e["timestamp"] < st: yest_users.add((e["user_id"], e["link"]))
        today = len(today_users)
        yesterday = len(yest_users)
            
    return web.json_response({"total_requests": total, "today_requests": today, "yesterday_requests": yesterday}, headers={"Access-Control-Allow-Origin": "*"})

async def handle_get_daily_report(request):
    report = {"history": []}
    if not REQUESTS_LOG_FILE.exists(): return web.json_response(report, headers={"Access-Control-Allow-Origin": "*"})
    with open(REQUESTS_LOG_FILE, "r", encoding="utf-8") as f: log_data = json.load(f)
    
    cycles = {}
    for entry in log_data:
        dt = datetime.utcfromtimestamp(entry["timestamp"]) + timedelta(hours=3)
        if dt.hour < 15: dt = dt - timedelta(days=1)
        cycle_key = dt.strftime("%Y-%m-%d")
        if cycle_key not in cycles: cycles[cycle_key] = set()
        cycles[cycle_key].add((entry["user_id"], entry["link"]))
        
    now_kyiv = datetime.utcnow() + timedelta(hours=3)
    if now_kyiv.hour < 15:
        current_cycle_date = (now_kyiv - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        current_cycle_date = now_kyiv.replace(hour=0, minute=0, second=0, microsecond=0)

    if cycles:
        earliest_date = datetime.strptime(min(cycles.keys()), "%Y-%m-%d")
    else:
        earliest_date = current_cycle_date

    temp_date = current_cycle_date
    while temp_date >= earliest_date:
        cycle_key = temp_date.strftime("%Y-%m-%d")
        start_date = temp_date
        end_date = start_date + timedelta(days=1)
        period_str = f"{start_date.strftime('%d.%m')} 15:00 — {end_date.strftime('%d.%m')} 15:00"
        
        cycle_users = cycles.get(cycle_key, set())
        total_in_cycle = len(cycle_users)
        
        link_counts = Counter()
        for u_id, l_name in cycle_users:
            link_counts[l_name] += 1
            
        details = [{"link": k, "count": v} for k, v in link_counts.most_common()]
        report["history"].append({"period": period_str, "total": total_in_cycle, "details": details})
        temp_date -= timedelta(days=1)
        
    return web.json_response(report, headers={"Access-Control-Allow-Origin": "*"})

# --- УНИКАЛИЗАТОР ФОТО ---
@dp.message(F.photo)
async def handle_unique_photo(message: Message):
    caption = message.caption or ""
    match = re.search(r'^(.+?)\s+(\d+)$', caption.strip())
    if not match: return await message.reply("⚠️ Подпиши фото в формате:\n<code>Название 50</code>", parse_mode="HTML")
    base_name = match.group(1).strip()
    count = int(match.group(2))
    if count < 1 or count > 100: return await message.reply("❌ От 1 до 100 копий.")

    msg = await message.reply(f"⚙️ Уникализирую {count} копий...")
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_bytes = await bot.download_file(file.file_path)
    temp_path = data_dir / f"temp_{message.from_user.id}.jpg"
    with open(temp_path, "wb") as f: f.write(file_bytes.read())

    from PIL import Image, ImageEnhance
    import piexif, random, io, zipfile

    invisible_chars = ["\u200b", "\u200c", "\u200d", "\ufeff"] 
    similar_chars = {'А': 'A', 'В': 'B', 'С': 'C', 'Е': 'E', 'Н': 'H', 'К': 'K', 'М': 'M', 'О': 'O', 'Р': 'P', 'Т': 'T', 'Х': 'X', 'а': 'a', 'е': 'e', 'о': 'o', 'р': 'p', 'с': 'c', 'у': 'y', 'х': 'x'}
    
    try:
        original_img = Image.open(temp_path)
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for i in range(1, count + 1):
                clean_img = Image.new(original_img.mode, original_img.size)
                clean_img.putdata(list(original_img.getdata()))
                clean_img = clean_img.resize((clean_img.width + random.choice([-1, 1]), clean_img.height + random.choice([-1, 1])), Image.Resampling.LANCZOS)
                enhancer = ImageEnhance.Brightness(clean_img)
                clean_img = enhancer.enhance(1.0 + random.uniform(-0.015, 0.015))
                for _ in range(10):
                    clean_img.putpixel((random.randint(0, clean_img.width - 1), random.randint(0, clean_img.height - 1)), (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)))
                img_byte_arr = io.BytesIO()
                clean_img.save(img_byte_arr, format='JPEG', quality=random.randint(92, 98), exif=piexif.dump({}))
                img_byte_arr.seek(0)
                
                new_name = base_name
                for _ in range(random.randint(1, 2)):
                    char_to_replace = random.choice(list(similar_chars.keys()))
                    if char_to_replace in new_name: new_name = new_name.replace(char_to_replace, similar_chars[char_to_replace], 1)
                new_name += random.choice(invisible_chars)
                zip_file.writestr(f"{new_name}_{i}.jpg", img_byte_arr.read())
        zip_buffer.seek(0)
        document = BufferedInputFile(zip_buffer.read(), filename=f"{base_name}.zip")
        await message.reply_document(document, caption=f"✅ Готово! {count} уникальных копий.")
    except Exception as e: await message.reply(f"⚠️ Ошибка: {e}")
    finally:
        if temp_path.exists(): os.remove(temp_path)
        await msg.delete()

# --- СОЗДАНИЕ ССЫЛОК ---
async def handle_create_single(request):
    headers = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type"}
    if request.method == 'OPTIONS': return web.Response(status=200, headers=headers)
    try:
        data = await request.json()
        channel_name, index, count = data.get("name"), data.get("index"), data.get("count", 1)
        link_name = f"{index}_{channel_name}"
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
            c_links = [links[f"{i}_{channel_name}"] for i in range(1, count + 1) if f"{i}_{channel_name}" in links]
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
    await message.answer("Привет! Открой Mini App для управления.", parse_mode="HTML")

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
        t_text = "".join([f"{'✅' if t['current']==t['target'] else ('⏳' if t['current']>0 else '⏸')} {t['name']}: <code>{t['current']}/{t['target']}</code>\n" for t in tasks])
        return f"🚀 <b>Генерация</b>\n<code>[{bar}]</code> <b>{pct}%</b>\nГотово: <b>{done} из {total_links}</b>\n\n{t_text}"

    try: await message.delete()
    except: pass
    msg = await message.answer(build_text(), parse_mode="HTML")
    created, links, msgs = 0, load_links(), load_messages()
    for task in tasks:
        clean_name = task["name"]
        c_links = []
        for i in range(1, task["target"] + 1):
            link_name = f"{i}_{clean_name}"
            while True:
                try:
                    link = await bot.create_chat_invite_link(chat_id=SOURCE_CHANNEL_ID, name=link_name, creates_join_request=True)
                    c_links.append(link.invite_link); links[link_name] = link.invite_link
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
