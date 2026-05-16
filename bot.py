import os
import json
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
TIMER_SECONDS = 30

DARAJA_INFO = {
    "oson":  {"emoji": "🟢", "ball": 1, "text": "Oson"},
    "orta":  {"emoji": "🟡", "ball": 2, "text": "O'rta"},
    "qiyin": {"emoji": "🔴", "ball": 3, "text": "Qiyin"},
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
    (5000, "💎 Legenda Shinobi"),
]

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
        [KeyboardButton("🟢 Oson savol"), KeyboardButton("🟡 O'rta savol"), KeyboardButton("🔴 Qiyin savol")],
        [KeyboardButton("🏟 Turnir"),     KeyboardButton("🃏 Joker ishlatish")],
        [KeyboardButton("📊 Statistika"), KeyboardButton("🏆 Top 10"),       KeyboardButton("ℹ️ Yordam")],
    ], resize_keyboard=True)

# ================= DB =================
async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     TEXT PRIMARY KEY,
            score       INTEGER DEFAULT 0,
            correct     INTEGER DEFAULT 0,
            incorrect   INTEGER DEFAULT 0,
            streak      INTEGER DEFAULT 0,
            max_streak  INTEGER DEFAULT 0,
            jokers      INTEGER DEFAULT 1,
            last_q      TEXT DEFAULT '',
            daraja      TEXT DEFAULT 'oson',
            last_bonus  TEXT DEFAULT '',
            turnir_q    INTEGER DEFAULT 0,
            turnir_score INTEGER DEFAULT 0,
            in_turnir   INTEGER DEFAULT 0
        )
        """)
        cols = [
            ("correct","INTEGER","0"), ("incorrect","INTEGER","0"),
            ("streak","INTEGER","0"),  ("max_streak","INTEGER","0"),
            ("jokers","INTEGER","1"),  ("daraja","TEXT","'oson'"),
            ("last_bonus","TEXT","''"),("turnir_q","INTEGER","0"),
            ("turnir_score","INTEGER","0"),("in_turnir","INTEGER","0"),
        ]
        for col, typ, default in cols:
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
            await db.execute(
                "INSERT INTO users(user_id) VALUES(?)", (uid,))
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
        app_web.router.add_get("/", lambda r: web.Response(text="🍥 Bot ishlayapti!"))
        runner = web.AppRunner(app_web)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()
        print(f"Web server {PORT} portda...")
        await asyncio.sleep(float("inf"))
    asyncio.run(start_server())

# ================= AI =================
SAVOL_PROMPT = """Naruto bo'yicha bitta test savol yarat.
JSON formatda qaytargin (boshqa hech narsa yozma):
{
  "savol": "Savol matni",
  "variantlar": ["variant1", "variant2", "variant3", "variant4"],
  "togri": 0
}
"togri" — to'g'ri javob indeksi (0-3)."""

DARAJA_MISOL = {
    "oson": "Masalan: Zabuza kim? Narutoning rangi nima?",
    "orta": "Masalan: Itachining Mangekyou Sharingan kuchi nima? Orochimaru qaysi unvonda?",
    "qiyin": "Masalan: To'qqiz dumlining ichini yegan aka-ukaning ismi nima? Rikudo Sennin kimning avlodi?"
}

def generate_question(daraja="oson"):
    d = DARAJA_INFO[daraja]
    desc = {"oson": "oddiy, taniqli faktlar", "orta": "o'rtacha murakkab", "qiyin": "juda qiyin, chuqur bilim talab etadi"}[daraja]
    misol = DARAJA_MISOL[daraja]
    system = (
        f"Sen Naruto anime quiz ustasisan. Faqat o'zbek tilida.\n"
        f"Daraja: {d['emoji']} {d['text']} — {desc}\n"
        f"{misol}\n"
        f"Savollar xilma-xil bo'lsin: personajlar, jutsu, tarix, oila, unvon, epizod."
    )
    res = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": SAVOL_PROMPT}]
    )
    raw = res.choices[0].message.content.strip()
    s, e = raw.find("{"), raw.rfind("}") + 1
    return json.loads(raw[s:e])

# ================= TIMER =================
active_timers = {}

async def time_is_up(context, uid, chat_id, message_id):
    await asyncio.sleep(TIMER_SECONDS)
    if active_timers.get(uid) == message_id:
        active_timers.pop(uid, None)
        row = await get_user(uid)
        score,correct,incorrect,streak,max_streak = row[0],row[1],row[2],row[3],row[4]
        streak = 0
        await upd(uid, last_q="", in_turnir=row[11], streak=0)
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                text="⏰ *Vaqt tugadi!* Savol o'tdi.\n\n🔥 Streak uzildi!\n\nYangi savol uchun tugmani bosing.",
                parse_mode="Markdown"
            )
        except Exception:
            pass

# ================= SAVOL YUBORISH =================
async def send_quiz(update, context, daraja, is_turnir=False):
    uid = str(update.effective_user.id)
    d = DARAJA_INFO[daraja]
    row = await get_user(uid)
    in_turnir, turnir_q, turnir_score = row[11], row[9], row[10]

    if is_turnir:
        if turnir_q >= 10:
            await update.message.reply_text("❌ Turnir tugagan! /start bilan qayta boshlang.")
            return
    
    await upd(uid, daraja=daraja)
    await update.message.reply_text("⏳ Savol tayyorlanmoqda...")

    try:
        data = generate_question(daraja)
    except Exception:
        await update.message.reply_text("❌ Xato yuz berdi. Qayta bosing.", reply_markup=main_keyboard())
        return

    data["daraja"] = daraja
    data["is_turnir"] = is_turnir
    await upd(uid, last_q=json.dumps(data, ensure_ascii=False))

    nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    keyboard = [[InlineKeyboardButton(f"{nums[i]}  {data['variantlar'][i]}", callback_data=str(i))] for i in range(4)]

    streak = row[3]
    streak_txt = f" | 🔥 Streak: {streak}" if streak > 0 else ""
    turnir_txt = f" | 🏟 {turnir_q+1}/10" if is_turnir else ""

    msg = await update.message.reply_text(
        f"{d['emoji']} *{d['text'].upper()}* | ⏱{TIMER_SECONDS}s | +{d['ball']}ball{streak_txt}{turnir_txt}\n\n"
        f"🍥 *SAVOL:*\n\n{data['savol']}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

    active_timers[uid] = msg.message_id
    asyncio.create_task(time_is_up(context, uid, update.effective_chat.id, msg.message_id))

# ================= ANSWER =================
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)

    if query.data.startswith("joker_"):
        await handle_joker(query, uid)
        return

    chosen = int(query.data)
    active_timers.pop(uid, None)

    row = await get_user(uid)
    score,correct,incorrect,streak,max_streak,jokers,last_q_str,daraja = row[0],row[1],row[2],row[3],row[4],row[5],row[6],row[7]
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

    if chosen == correct_idx:
        streak += 1
        max_streak = max(max_streak, streak)
        bonus = 0
        streak_msg = ""
        if streak % 5 == 0:
            bonus = streak
            streak_msg = f"\n🎯 *{streak} STREAK BONUS! +{bonus} qo'shimcha ball!*"
        ball = d["ball"] + bonus
        score += ball
        correct += 1
        if streak % 10 == 0 and streak > 0:
            jokers = min(jokers + 1, 5)
        await upd(uid, score=score, correct=correct, streak=streak, max_streak=max_streak, jokers=jokers)
        natija = f"🔥 *To'g'ri!* +{ball} ball{streak_msg}\n🔥 Streak: {streak}\n\n"
        if is_turnir:
            turnir_score += ball
            turnir_q += 1
            await upd(uid, turnir_q=turnir_q, turnir_score=turnir_score)
    else:
        old_streak = streak
        streak = 0
        incorrect += 1
        await upd(uid, incorrect=incorrect, streak=0)
        streak_warn = f"\n💔 Streak uzildi! (edi: {old_streak})" if old_streak > 0 else ""
        natija = f"💥 *Noto'g'ri!*{streak_warn}\n\n"
        if is_turnir:
            turnir_q += 1
            await upd(uid, turnir_q=turnir_q)

    unvon = get_unvon(score)
    nxt_ball, nxt_unvon = next_unvon(score)
    nxt_txt = f"\n📈 Keyingi unvon: {nxt_unvon} ({nxt_ball - score} ball qoldi)" if nxt_unvon else ""

    turnir_end = ""
    if is_turnir and turnir_q >= 10:
        await upd(uid, in_turnir=0, turnir_q=0, turnir_score=0)
        turnir_end = f"\n\n🏟 *TURNIR YAKUNLANDI!*\nTurnir natija: *{turnir_score}* ball\n10 savoldan {correct} ta to'g'ri!"

    msg = (
        f"{natija}"
        f"📋 *{data['savol']}*\n\n"
        f"{variantlar_text}\n"
        f"🏆 Score: *{score}* | {unvon}{nxt_txt}\n"
        f"✅ {correct} | ❌ {incorrect} | 🃏 Joker: {jokers}"
        f"{turnir_end}"
    )
    await query.edit_message_text(msg, parse_mode="Markdown")

# ================= JOKER =================
async def handle_joker(query, uid):
    row = await get_user(uid)
    jokers, last_q_str = row[5], row[6]

    if jokers <= 0:
        await query.answer("❌ Jokeringiz yo'q!", show_alert=True)
        return
    if not last_q_str:
        await query.answer("❌ Avval savol oling!", show_alert=True)
        return

    data = json.loads(last_q_str)
    correct_idx = data["togri"]

    import random
    wrong = [i for i in range(4) if i != correct_idx]
    remove = random.choice(wrong)
    data["removed"] = remove
    await upd(uid, jokers=jokers-1, last_q=json.dumps(data, ensure_ascii=False))

    nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    keyboard = []
    for i in range(4):
        if i == remove:
            continue
        keyboard.append([InlineKeyboardButton(f"{nums[i]}  {data['variantlar'][i]}", callback_data=str(i))])

    d = DARAJA_INFO.get(data.get("daraja","oson"), DARAJA_INFO["oson"])
    await query.edit_message_text(
        f"🃏 *Joker ishlatildi!* 1 ta noto'g'ri o'chirildi.\nQolgan jokerlar: {jokers-1}\n\n"
        f"{d['emoji']} *SAVOL:*\n\n{data['savol']}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# ================= COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    row = await get_user(uid)
    score = row[0]
    unvon = get_unvon(score)
    await update.message.reply_text(
        f"🍥 *Xush kelibsiz, Shinobi!*\n\n"
        f"Unvoningiz: {unvon}\n\n"
        f"🟢 Oson +1 ball | 🟡 O'rta +2 | 🔴 Qiyin +3\n"
        f"🔥 5 ketma-ket to'g'ri = bonus ball!\n"
        f"🃏 Joker = 1 noto'g'ri variant o'chadi\n"
        f"🏟 Turnir = 10 savol ketma-ket",
        reply_markup=main_keyboard(),
        parse_mode="Markdown"
    )

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = str(update.effective_user.id)

    if "Oson" in text:
        await send_quiz(update, context, "oson")
    elif "O'rta" in text or "Orta" in text:
        await send_quiz(update, context, "orta")
    elif "Qiyin" in text:
        await send_quiz(update, context, "qiyin")
    elif "Turnir" in text:
        await start_turnir(update, context)
    elif "Joker" in text:
        await joker_cmd(update, context)
    elif "Statistika" in text:
        await stat_cmd(update, context)
    elif "Top" in text:
        await top_cmd(update, context)
    elif "Yordam" in text:
        await yordam(update, context)

async def start_turnir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    await upd(uid, in_turnir=1, turnir_q=0, turnir_score=0)
    await update.message.reply_text(
        "🏟 *TURNIR BOSHLANDI!*\n\n"
        "10 ta savol ketma-ket beriladi.\n"
        "🟢 Oson, 🟡 O'rta, 🔴 Qiyin aralash.\n\n"
        "Birinchi savol tayyorlanmoqda...",
        parse_mode="Markdown"
    )
    import random
    daraja = random.choice(["oson", "oson", "orta", "orta", "qiyin"])
    await send_quiz(update, context, daraja, is_turnir=True)

async def joker_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    row = await get_user(uid)
    jokers, last_q_str = row[5], row[6]

    if jokers <= 0:
        await update.message.reply_text("❌ Jokeringiz yo'q!\nHar 10 ketma-ket to'g'ri javobda 1 joker olasiz.", reply_markup=main_keyboard())
        return
    if not last_q_str:
        await update.message.reply_text("❌ Avval savol oling, keyin joker ishlating!", reply_markup=main_keyboard())
        return

    data = json.loads(last_q_str)
    correct_idx = data["togri"]
    import random
    wrong = [i for i in range(4) if i != correct_idx]
    remove = random.choice(wrong)
    data["removed"] = remove
    await upd(uid, jokers=jokers-1, last_q=json.dumps(data, ensure_ascii=False))

    nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    keyboard = [[InlineKeyboardButton(f"{nums[i]}  {data['variantlar'][i]}", callback_data=str(i))] for i in range(4) if i != remove]
    d = DARAJA_INFO.get(data.get("daraja","oson"), DARAJA_INFO["oson"])

    await update.message.reply_text(
        f"🃏 *Joker ishlatildi!* Qoldi: {jokers-1}\n\n"
        f"{d['emoji']} *SAVOL:*\n\n{data['savol']}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def stat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    row = await get_user(uid)
    score,correct,incorrect,streak,max_streak,jokers = row[0],row[1],row[2],row[3],row[4],row[5]
    daraja = row[7]
    jami = correct + incorrect
    foiz = round((correct/jami)*100) if jami > 0 else 0
    unvon = get_unvon(score)
    nxt_ball, nxt_unvon = next_unvon(score)
    nxt_txt = f"\n📈 Keyingi: {nxt_unvon} ({nxt_ball-score} ball)" if nxt_unvon else "\n🌟 Maksimal unvon!"

    # Kunlik bonus
    today = str(date.today())
    last_bonus = row[8]
    bonus_txt = ""
    if last_bonus != today:
        await upd(uid, score=score+5, last_bonus=today)
        score += 5
        bonus_txt = "\n\n🎁 *Kunlik bonus: +5 ball olding!*"

    await update.message.reply_text(
        f"📊 *Statistika:*\n\n"
        f"🏅 Unvon: {unvon}{nxt_txt}\n"
        f"🏆 Score: *{score}*\n"
        f"🎮 Jami: {jami} savol\n"
        f"✅ To'g'ri: {correct}\n"
        f"❌ Noto'g'ri: {incorrect}\n"
        f"🎯 Aniqlik: {foiz}%\n"
        f"🔥 Streak: {streak} (eng ko'p: {max_streak})\n"
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
    text = "🏆 *TOP 10 Shinobi:*\n\n"
    for i, (uid, sc) in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        unvon = get_unvon(sc)
        text += f"{medal} {unvon} — *{sc}* ball\n"
    await update.message.reply_text(text, reply_markup=main_keyboard(), parse_mode="Markdown")

async def yordam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *Yordam:*\n\n"
        "🟢 *Oson* — +1 ball | oddiy savollar\n"
        "🟡 *O'rta* — +2 ball | murakkab\n"
        "🔴 *Qiyin* — +3 ball | ekspert\n\n"
        "🔥 *Streak* — ketma-ket to'g'ri javob:\n"
        "   5 ketma-ket = +5 bonus ball!\n"
        "   10 ketma-ket = 1 joker!\n\n"
        "🃏 *Joker* — 1 ta noto'g'ri variantni o'chiradi\n\n"
        "🏟 *Turnir* — 10 savol ketma-ket, natija oxirida\n\n"
        "📅 *Kunlik bonus* — Statistikani ochsangiz +5 ball\n\n"
        "🎖 *Unvonlar:* Akademiya → Genin → Chunin → Jonin → ANBU → Sharingan → Sage → Bijuu → Hokage → Legenda",
        reply_markup=main_keyboard(),
        parse_mode="Markdown"
    )

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
