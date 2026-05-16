import os
import json
import random
import asyncio
import threading
import aiosqlite
from datetime import date
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from openai import OpenAI

# ================= ENV =================
TOKEN = os.getenv("TOKEN")
PORT = int(os.getenv("PORT", 8000))
GROQ_KEY = os.getenv("GROQ_API_KEY")
REPLIT_AI_KEY = os.getenv("AI_INTEGRATIONS_OPENAI_API_KEY")
REPLIT_AI_URL = os.getenv("AI_INTEGRATIONS_OPENAI_BASE_URL")

if GROQ_KEY:
    client = OpenAI(api_key=GROQ_KEY, base_url="https://api.groq.com/openai/v1")
    MODEL = "llama3-8b-8192"
else:
    client = OpenAI(api_key=REPLIT_AI_KEY, base_url=REPLIT_AI_URL)
    MODEL = "gpt-4o-mini"

DB = "naruto.db"

# Qiyinlikka qarab vaqt (sekund)
TIMER = {"oson": 30, "orta": 20, "qiyin": 10}

DARAJA_INFO = {
    "oson":  {"emoji": "🟢", "ball": 1, "text": "Oson",  "timer": 30},
    "orta":  {"emoji": "🟡", "ball": 2, "text": "O'rta", "timer": 20},
    "qiyin": {"emoji": "🔴", "ball": 3, "text": "Qiyin", "timer": 10},
}

UNVONLAR = [
    (0,    "👶 Akademiya o'quvchisi"),
    (50,   "🥷 Genin"),
    (150,  "📜 Chunin"),
    (300,  "⚔️ Jonin"),
    (500,  "🎭 ANBU"),
    (800,  "👁 Sharingan egalari"),
    (1200, "🌀 Sage Mode"),
    (2000, "🦊 Bijuu ustasi"),
    (3000, "🌟 Hokage"),
    (5000, "💎 Anime Legenda"),
]

# 30 ta anime ro'yxati
ANIMELAR = [
    "Naruto / Naruto Shippuden", "Bleach", "Jujutsu Kaisen",
    "Attack on Titan (Shingeki no Kyojin)", "One Piece", "Dragon Ball Z / Dragon Ball Super",
    "Demon Slayer (Kimetsu no Yaiba)", "My Hero Academia (Boku no Hero)",
    "Death Note", "Fullmetal Alchemist: Brotherhood", "Hunter x Hunter",
    "Tokyo Ghoul", "Fairy Tail", "Black Clover", "One Punch Man",
    "Vinland Saga", "Chainsaw Man", "Re:Zero", "Overlord", "Mob Psycho 100",
    "Steins;Gate", "Code Geass", "Spy x Family", "Blue Lock", "Haikyuu!!",
    "Boruto", "Seven Deadly Sins", "Sword Art Online", "Neon Genesis Evangelion",
    "Cowboy Bebop", "Akame ga Kill", "Fate series", "No Game No Life",
]

DARAJA_DESC = {
    "oson": "taniqli personajlar, asosiy voqealar, oddiy faktlar",
    "orta": "o'rta murakkablik, kuchlar, oilalar, muhim epizodlar",
    "qiyin": "juda chuqur bilim: maxfiy faktlar, raqamlar, sanalar, kamdan-kam tilga olinadigan detallar"
}

def get_unvon(score):
    unvon = UNVONLAR[0][1]
    for min_ball, nom in UNVONLAR:
        if score >= min_ball:
            unvon = nom
    return unvon

def next_unvon(score):
    for min_ball, nom in UNVONLAR:
        if score < min_ball:
            return min_ball, nom
    return None, None

# ================= KEYBOARDS =================
def main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🟢 Oson savol"),  KeyboardButton("🟡 O'rta savol"), KeyboardButton("🔴 Qiyin savol")],
        [KeyboardButton("🏟 Turnir"),      KeyboardButton("🃏 Joker ishlatish")],
        [KeyboardButton("📊 Statistika"),  KeyboardButton("🏆 Top 10"),      KeyboardButton("ℹ️ Yordam")],
    ], resize_keyboard=True)

# ================= DB =================
async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id      TEXT PRIMARY KEY,
            score        INTEGER DEFAULT 0,
            correct      INTEGER DEFAULT 0,
            incorrect    INTEGER DEFAULT 0,
            streak       INTEGER DEFAULT 0,
            max_streak   INTEGER DEFAULT 0,
            jokers       INTEGER DEFAULT 1,
            last_q       TEXT DEFAULT '',
            daraja       TEXT DEFAULT 'oson',
            last_bonus   TEXT DEFAULT '',
            turnir_q     INTEGER DEFAULT 0,
            turnir_score INTEGER DEFAULT 0,
            in_turnir    INTEGER DEFAULT 0
        )
        """)
        extra_cols = [
            ("correct","INTEGER","0"), ("incorrect","INTEGER","0"),
            ("streak","INTEGER","0"),  ("max_streak","INTEGER","0"),
            ("jokers","INTEGER","1"),  ("daraja","TEXT","'oson'"),
            ("last_bonus","TEXT","''"),("turnir_q","INTEGER","0"),
            ("turnir_score","INTEGER","0"),("in_turnir","INTEGER","0"),
        ]
        for col, typ, default in extra_cols:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} {typ} DEFAULT {default}")
            except Exception:
                pass
        await db.commit()

async def get_user(uid):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT score,correct,incorrect,streak,max_streak,jokers,last_q,daraja,last_bonus,turnir_q,turnir_score,in_turnir FROM users WHERE user_id=?", (uid,))
        row = await cur.fetchone()
        if not row:
            await db.execute("INSERT INTO users(user_id) VALUES(?)", (uid,))
            await db.commit()
            return 0,0,0,0,0,1,"","oson","",0,0,0
        return row

async def upd(uid, **kwargs):
    async with aiosqlite.connect(DB) as db:
        if kwargs:
            sets = ", ".join(f"{k}=?" for k in kwargs)
            vals = list(kwargs.values()) + [uid]
            await db.execute(f"UPDATE users SET {sets} WHERE user_id=?", vals)
            await db.commit()

async def get_top10():
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT user_id, score FROM users ORDER BY score DESC LIMIT 10")
        return await cur.fetchall()

# ================= WEB SERVER =================
def run_web_server():
    async def start_server():
        app_web = web.Application()
        app_web.router.add_get("/", lambda r: web.Response(text="🍥 Anime Quiz Bot ishlayapti!"))
        runner = web.AppRunner(app_web)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()
        print(f"Web server {PORT} portda...")
        await asyncio.sleep(float("inf"))
    asyncio.run(start_server())

# ================= AI SAVOL GENERATSIYA =================
SAVOL_PROMPT = """Quyidagi JSON formatda qaytargin (boshqa hech narsa yozma):
{
  "anime": "Anime nomi",
  "savol": "Savol matni",
  "variantlar": ["variant1", "variant2", "variant3", "variant4"],
  "togri": 0
}
"togri" — to'g'ri javob indeksi (0 dan 3 gacha).
4 ta variant bir-biridan farqli va mantiqiy bo'lsin."""

def generate_question(daraja="oson"):
    d = DARAJA_INFO[daraja]
    desc = DARAJA_DESC[daraja]
    anime = random.choice(ANIMELAR)

    system = (
        f"Sen 30+ anime bo'yicha quiz ustasisan. Faqat o'zbek tilida javob ber.\n"
        f"Bu safar: *{anime}* animesidan savol ber.\n"
        f"Daraja: {d['emoji']} {d['text']} — {desc}\n"
        f"Savollar xilma-xil bo'lsin: personaj, kuch/jutsu, oila, voqea, raqam, unvon, epizod.\n"
        f"Variantlar bir-biriga o'xshamasin va hammasi mantiqiy bo'lsin."
    )
    res = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": SAVOL_PROMPT}
        ]
    )
    raw = res.choices[0].message.content.strip()
    s, e = raw.find("{"), raw.rfind("}") + 1
    return json.loads(raw[s:e])

# ================= TIMER =================
active_timers = {}

async def time_is_up(context, uid, chat_id, message_id, daraja):
    timer_sec = DARAJA_INFO.get(daraja, DARAJA_INFO["oson"])["timer"]
    await asyncio.sleep(timer_sec)

    if active_timers.get(uid) == message_id:
        active_timers.pop(uid, None)
        row = await get_user(uid)
        streak = row[3]
        await upd(uid, last_q="", streak=0)

        # Vaqt tugasa xabarni O'CHIR
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass

        # Alohida xabar yuborib, 3 sekunddan keyin uni ham o'chir
        try:
            streak_txt = f"\n💔 Streak uzildi! (edi: {streak})" if streak > 0 else ""
            note = await context.bot.send_message(
                chat_id=chat_id,
                text=f"⏰ *Vaqt tugadi!* ({timer_sec} sekund){streak_txt}\n\nYangi savol uchun tugmani bosing.",
                parse_mode="Markdown"
            )
            await asyncio.sleep(5)
            await context.bot.delete_message(chat_id=chat_id, message_id=note.message_id)
        except Exception:
            pass

# ================= SAVOL YUBORISH =================
async def send_quiz(update, context, daraja, is_turnir=False):
    uid = str(update.effective_user.id)
    d = DARAJA_INFO[daraja]
    row = await get_user(uid)
    turnir_q, turnir_score = row[9], row[10]

    await upd(uid, daraja=daraja)

    loading = await update.message.reply_text("⏳ Savol tayyorlanmoqda...")

    try:
        data = generate_question(daraja)
    except Exception:
        await loading.delete()
        await update.message.reply_text("❌ Xato yuz berdi. Qayta bosing.", reply_markup=main_keyboard())
        return

    await loading.delete()

    data["daraja"] = daraja
    data["is_turnir"] = is_turnir
    await upd(uid, last_q=json.dumps(data, ensure_ascii=False))

    nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    keyboard = [[InlineKeyboardButton(f"{nums[i]}  {data['variantlar'][i]}", callback_data=str(i))] for i in range(4)]

    streak = row[3]
    streak_txt = f" | 🔥{streak}" if streak > 0 else ""
    turnir_txt = f" | 🏟{turnir_q+1}/10" if is_turnir else ""
    anime_name = data.get("anime", "Anime")
    timer_sec = d["timer"]

    msg = await update.message.reply_text(
        f"{d['emoji']} *{d['text']}* | ⏱{timer_sec}s | +{d['ball']}ball{streak_txt}{turnir_txt}\n"
        f"🎌 *{anime_name}*\n\n"
        f"❓ *{data['savol']}*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

    active_timers[uid] = msg.message_id
    asyncio.create_task(time_is_up(context, uid, update.effective_chat.id, msg.message_id, daraja))

# ================= JAVOB =================
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)

    if query.data.startswith("joker_"):
        await handle_joker_callback(query, uid, context)
        return

    chosen = int(query.data)
    active_timers.pop(uid, None)

    row = await get_user(uid)
    score,correct,incorrect,streak,max_streak,jokers,last_q_str = row[0],row[1],row[2],row[3],row[4],row[5],row[6]
    in_turnir, turnir_q, turnir_score = row[11], row[9], row[10]

    if not last_q_str:
        await query.edit_message_text("❌ Avval savol oling!")
        return
    try:
        data = json.loads(last_q_str)
    except Exception:
        await query.edit_message_text("❌ Xato. Qayta bosing.")
        return

    daraja = data.get("daraja", "oson")
    is_turnir = data.get("is_turnir", False)
    d = DARAJA_INFO[daraja]
    correct_idx = data["togri"]
    anime_name = data.get("anime", "Anime")
    await upd(uid, last_q="")

    nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    variantlar_text = ""
    for i, v in enumerate(data["variantlar"]):
        if i == correct_idx:
            variantlar_text += f"✅ {nums[i]} *{v}*\n"
        elif i == chosen:
            variantlar_text += f"❌ {nums[i]} ~{v}~\n"
        else:
            variantlar_text += f"▪️ {nums[i]} {v}\n"

    turnir_end = ""
    if chosen == correct_idx:
        streak += 1
        max_streak = max(max_streak, streak)
        bonus = streak if streak % 5 == 0 else 0
        ball = d["ball"] + bonus
        score += ball
        correct += 1
        new_jokers = jokers + (1 if streak % 10 == 0 and streak > 0 else 0)
        new_jokers = min(new_jokers, 5)
        await upd(uid, score=score, correct=correct, streak=streak, max_streak=max_streak, jokers=new_jokers)
        streak_msg = f"\n🎯 *{streak} STREAK! +{bonus} bonus!*" if bonus > 0 else f"\n🔥 Streak: {streak}"
        joker_msg = f"\n🃏 *Joker oldingiz!*" if new_jokers > jokers else ""
        natija = f"🔥 *To'g'ri!* +{ball} ball{streak_msg}{joker_msg}\n\n"
        if is_turnir:
            turnir_score += ball; turnir_q += 1
            await upd(uid, turnir_q=turnir_q, turnir_score=turnir_score)
    else:
        old_streak = streak
        incorrect += 1
        await upd(uid, incorrect=incorrect, streak=0)
        streak_warn = f"\n💔 Streak uzildi! (edi: {old_streak})" if old_streak > 0 else ""
        natija = f"💥 *Noto'g'ri!*{streak_warn}\n\n"
        if is_turnir:
            turnir_q += 1
            await upd(uid, turnir_q=turnir_q)

    unvon = get_unvon(score)
    nxt_ball, nxt_unvon = next_unvon(score)
    nxt_txt = f"\n📈 {nxt_unvon} uchun {nxt_ball-score} ball qoldi" if nxt_unvon else ""

    if is_turnir and turnir_q >= 10:
        await upd(uid, in_turnir=0, turnir_q=0, turnir_score=0)
        turnir_end = f"\n\n🏟 *TURNIR YAKUNLANDI!*\nNatija: *{turnir_score}* ball"

    msg = (
        f"{natija}"
        f"🎌 *{anime_name}*\n"
        f"❓ {data['savol']}\n\n"
        f"{variantlar_text}\n"
        f"🏅 {unvon}{nxt_txt}\n"
        f"🏆 *{score}* ball | ✅{correct} ❌{incorrect}"
        f"{turnir_end}"
    )
    await query.edit_message_text(msg, parse_mode="Markdown")

# ================= JOKER =================
async def handle_joker_callback(query, uid, context):
    row = await get_user(uid)
    jokers, last_q_str = row[5], row[6]
    if jokers <= 0:
        await query.answer("❌ Jokeringiz yo'q!", show_alert=True)
        return
    if not last_q_str:
        await query.answer("❌ Avval savol oling!", show_alert=True)
        return
    await apply_joker(query, uid, jokers, json.loads(last_q_str))

async def joker_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    row = await get_user(uid)
    jokers, last_q_str = row[5], row[6]
    if jokers <= 0:
        await update.message.reply_text("❌ Jokeringiz yo'q!\nHar 10 streak = 1 joker.", reply_markup=main_keyboard())
        return
    if not last_q_str:
        await update.message.reply_text("❌ Avval savol oling!", reply_markup=main_keyboard())
        return
    data = json.loads(last_q_str)
    await apply_joker_msg(update, uid, jokers, data)

async def apply_joker(query, uid, jokers, data):
    correct_idx = data["togri"]
    wrong = [i for i in range(4) if i != correct_idx]
    remove = random.choice(wrong)
    data["removed"] = remove
    await upd(uid, jokers=jokers-1, last_q=json.dumps(data, ensure_ascii=False))
    nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    keyboard = [[InlineKeyboardButton(f"{nums[i]}  {data['variantlar'][i]}", callback_data=str(i))] for i in range(4) if i != remove]
    d = DARAJA_INFO.get(data.get("daraja","oson"), DARAJA_INFO["oson"])
    anime_name = data.get("anime", "Anime")
    await query.edit_message_text(
        f"🃏 *Joker!* 1 noto'g'ri o'chirildi. Qoldi: {jokers-1}\n"
        f"🎌 *{anime_name}*\n\n❓ *{data['savol']}*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def apply_joker_msg(update, uid, jokers, data):
    correct_idx = data["togri"]
    wrong = [i for i in range(4) if i != correct_idx]
    remove = random.choice(wrong)
    data["removed"] = remove
    await upd(uid, jokers=jokers-1, last_q=json.dumps(data, ensure_ascii=False))
    nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    keyboard = [[InlineKeyboardButton(f"{nums[i]}  {data['variantlar'][i]}", callback_data=str(i))] for i in range(4) if i != remove]
    d = DARAJA_INFO.get(data.get("daraja","oson"), DARAJA_INFO["oson"])
    anime_name = data.get("anime", "Anime")
    await update.message.reply_text(
        f"🃏 *Joker ishlatildi!* Qoldi: {jokers-1}\n"
        f"🎌 *{anime_name}*\n\n❓ *{data['savol']}*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# ================= TURNIR =================
async def start_turnir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    await upd(uid, in_turnir=1, turnir_q=0, turnir_score=0)
    await update.message.reply_text(
        "🏟 *TURNIR BOSHLANDI!*\n\n"
        "10 ta savol | Aralash anime | Aralash qiyinlik\n"
        "🟢 Oson=30s | 🟡 O'rta=20s | 🔴 Qiyin=10s\n\n"
        "Diqqat: Vaqt tugasa savol o'chib ketadi!",
        parse_mode="Markdown"
    )
    daraja = random.choice(["oson","oson","orta","orta","qiyin"])
    await send_quiz(update, context, daraja, is_turnir=True)

# ================= COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    row = await get_user(uid)
    unvon = get_unvon(row[0])
    await update.message.reply_text(
        f"🎌 *Anime Quiz Bot!*\n\n"
        f"30+ animeden savollar:\n"
        f"Naruto • Bleach • JJK • AoT • One Piece\n"
        f"Dragon Ball • Demon Slayer • MHA • Death Note va boshqalar!\n\n"
        f"Sizning unvoningiz: *{unvon}*\n\n"
        f"⏱ Vaqt: 🟢30s | 🟡20s | 🔴10s\n"
        f"Vaqt tugasa savol *o'chib ketadi!*\n\n"
        f"🔥 5 streak = bonus ball\n"
        f"🃏 10 streak = joker\n"
        f"📅 Har kun statistikani oching = +5 ball",
        reply_markup=main_keyboard(),
        parse_mode="Markdown"
    )

async def stat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    row = await get_user(uid)
    score,correct,incorrect,streak,max_streak,jokers = row[0],row[1],row[2],row[3],row[4],row[5]
    jami = correct + incorrect
    foiz = round((correct/jami)*100) if jami > 0 else 0
    unvon = get_unvon(score)
    nxt_ball, nxt_unvon = next_unvon(score)
    nxt_txt = f"\n📈 Keyingi: {nxt_unvon} ({nxt_ball-score} ball)" if nxt_unvon else "\n🌟 Maksimal unvon!"

    today = str(date.today())
    bonus_txt = ""
    if row[8] != today:
        await upd(uid, score=score+5, last_bonus=today)
        score += 5
        bonus_txt = "\n\n🎁 *Kunlik bonus: +5 ball!*"

    await update.message.reply_text(
        f"📊 *Statistika:*\n\n"
        f"🏅 {unvon}{nxt_txt}\n"
        f"🏆 Score: *{score}*\n"
        f"🎮 Jami: {jami} savol\n"
        f"✅ To'g'ri: {correct}\n"
        f"❌ Noto'g'ri: {incorrect}\n"
        f"🎯 Aniqlik: {foiz}%\n"
        f"🔥 Streak: {streak} (rekord: {max_streak})\n"
        f"🃏 Jokerlar: {jokers}{bonus_txt}",
        reply_markup=main_keyboard(),
        parse_mode="Markdown"
    )

async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await get_top10()
    if not rows:
        await update.message.reply_text("Hali hech kim o'ynamagan!", reply_markup=main_keyboard())
        return
    medals = ["🥇","🥈","🥉"]
    text = "🏆 *TOP 10 Anime Bilimdonlar:*\n\n"
    for i, (uid, sc) in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        text += f"{medal} {get_unvon(sc)} — *{sc}* ball\n"
    await update.message.reply_text(text, reply_markup=main_keyboard(), parse_mode="Markdown")

async def yordam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *Yordam:*\n\n"
        "🎌 *30+ animeden savollar:*\n"
        "Naruto, Bleach, JJK, AoT, One Piece, Dragon Ball,\n"
        "Demon Slayer, MHA, Death Note, FMA, HxH va boshqalar!\n\n"
        "⏱ *Vaqt:*\n"
        "🟢 Oson = 30 sekund (+1 ball)\n"
        "🟡 O'rta = 20 sekund (+2 ball)\n"
        "🔴 Qiyin = 10 sekund (+3 ball)\n"
        "⚠️ Vaqt tugasa savol o'chib ketadi!\n\n"
        "🔥 *Streak:* ketma-ket to'g'ri javob\n"
        "   5 streak = +5 bonus ball\n"
        "   10 streak = 1 joker sovg'a\n\n"
        "🃏 *Joker:* 1 noto'g'ri variantni o'chiradi\n\n"
        "🏟 *Turnir:* 10 savol ketma-ket, aralash anime\n\n"
        "📅 *Kunlik bonus:* Statistika ochsangiz +5 ball\n\n"
        "🎖 *Unvonlar:*\n"
        "👶→🥷→📜→⚔️→🎭→👁→🌀→🦊→🌟→💎",
        reply_markup=main_keyboard(),
        parse_mode="Markdown"
    )

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if "Oson" in text:       await send_quiz(update, context, "oson")
    elif "O'rta" in text:    await send_quiz(update, context, "orta")
    elif "Qiyin" in text:    await send_quiz(update, context, "qiyin")
    elif "Turnir" in text:   await start_turnir(update, context)
    elif "Joker" in text:    await joker_cmd(update, context)
    elif "Statistika" in text: await stat_cmd(update, context)
    elif "Top" in text:      await top_cmd(update, context)
    elif "Yordam" in text:   await yordam(update, context)

# ================= MAIN =================
if __name__ == "__main__":
    asyncio.run(init_db())
    threading.Thread(target=run_web_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stat", stat_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    print("Bot ishga tushdi...")
    app.run_polling()
