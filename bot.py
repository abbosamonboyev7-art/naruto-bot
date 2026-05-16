import os
import json
import asyncio
import threading
import aiosqlite
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

# ================= ASOSIY TUGMALAR =================
def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🟢 Oson savol"), KeyboardButton("🟡 O'rta savol")],
            [KeyboardButton("🔴 Qiyin savol"), KeyboardButton("📊 Statistika")],
            [KeyboardButton("🏆 Top 10"),      KeyboardButton("ℹ️ Yordam")],
        ],
        resize_keyboard=True
    )

# ================= DB =================
async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            score INTEGER DEFAULT 0,
            correct INTEGER DEFAULT 0,
            incorrect INTEGER DEFAULT 0,
            last_q TEXT,
            daraja TEXT DEFAULT 'oson'
        )
        """)
        for col, typ, default in [
            ("correct", "INTEGER", "0"),
            ("incorrect", "INTEGER", "0"),
            ("daraja", "TEXT", "'oson'"),
        ]:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} {typ} DEFAULT {default}")
            except Exception:
                pass
        await db.commit()

async def get_user(uid):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT score, correct, incorrect, last_q, daraja FROM users WHERE user_id=?", (uid,))
        row = await cur.fetchone()
        if not row:
            await db.execute(
                "INSERT INTO users(user_id,score,correct,incorrect,last_q,daraja) VALUES(?,0,0,0,'','oson')", (uid,))
            await db.commit()
            return 0, 0, 0, "", "oson"
        return row

async def set_user(uid, score=None, correct=None, incorrect=None, last_q=None, daraja=None):
    async with aiosqlite.connect(DB) as db:
        updates, values = [], []
        if score    is not None: updates.append("score=?");    values.append(score)
        if correct  is not None: updates.append("correct=?");  values.append(correct)
        if incorrect is not None: updates.append("incorrect=?"); values.append(incorrect)
        if last_q   is not None: updates.append("last_q=?");   values.append(last_q)
        if daraja   is not None: updates.append("daraja=?");   values.append(daraja)
        if updates:
            values.append(uid)
            await db.execute(f"UPDATE users SET {', '.join(updates)} WHERE user_id=?", values)
            await db.commit()

async def get_top10():
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT user_id, score FROM users ORDER BY score DESC LIMIT 10")
        return await cur.fetchall()

# ================= WEB SERVER =================
async def handle_ping(request):
    return web.Response(text="Bot ishlayapti! 🍥")

def run_web_server():
    async def start_server():
        app_web = web.Application()
        app_web.router.add_get("/", handle_ping)
        runner = web.AppRunner(app_web)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        print(f"Web server {PORT} portda ishlamoqda...")
        await asyncio.sleep(float("inf"))
    asyncio.run(start_server())

# ================= AI =================
def generate_question(daraja="oson"):
    d = DARAJA_INFO.get(daraja, DARAJA_INFO["oson"])
    daraja_desc = {"oson": "asosiy faktlar", "orta": "o'rta murakkablik", "qiyin": "juda batafsil bilim"}.get(daraja, "asosiy faktlar")
    system_text = f"Sen Naruto olamidagi quiz ustasisan.\nFaqat o'zbek tilida gapir.\nDaraja: {d['emoji']} {d['text']} — {daraja_desc}\nNaruto, Sasuke, Kakashi, Itachi, Madara va boshqa personajlar haqida savollar ber."
    res = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_text},
            {"role": "user", "content": """Naruto bo'yicha bitta test savol yarat.
Quyidagi JSON formatda qaytargin (boshqa hech narsa yozma):
{
  "savol": "Savol matni",
  "variantlar": ["1-variant", "2-variant", "3-variant", "4-variant"],
  "togri": 0
}
"togri" — to'g'ri javobning indeksi (0 dan 3 gacha)."""}
        ]
    )
    content = res.choices[0].message.content.strip()
    start = content.find("{")
    end = content.rfind("}") + 1
    return json.loads(content[start:end])

# ================= TIMER =================
active_timers = {}

async def time_is_up(context, uid, chat_id, message_id, daraja):
    await asyncio.sleep(TIMER_SECONDS)
    if active_timers.get(uid) == message_id:
        active_timers.pop(uid, None)
        await set_user(uid, last_q="")
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="⏰ *Vaqt tugadi!* 30 soniya o'tdi.\n\nYangi savol uchun pastdagi tugmalardan birini bosing.",
                parse_mode="Markdown"
            )
        except Exception:
            pass

# ================= SAVOL YUBORISH =================
async def send_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE, daraja: str):
    uid = str(update.effective_user.id)
    d = DARAJA_INFO[daraja]

    await set_user(uid, daraja=daraja)
    await update.message.reply_text("⏳ Savol tayyorlanmoqda...")

    try:
        data = generate_question(daraja)
    except Exception:
        await update.message.reply_text(
            "❌ Savol yaratishda xato. Qayta urinib ko'ring.",
            reply_markup=main_keyboard()
        )
        return

    data["daraja"] = daraja
    await set_user(uid, last_q=json.dumps(data, ensure_ascii=False))

    nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    keyboard = [
        [InlineKeyboardButton(f"{nums[i]}  {data['variantlar'][i]}", callback_data=str(i))]
        for i in range(4)
    ]

    msg = await update.message.reply_text(
        f"{d['emoji']} *{d['text'].upper()} DARAJA* | ⏱ {TIMER_SECONDS} soniya | +{d['ball']} ball\n\n"
        f"🍥 *SAVOL:*\n\n{data['savol']}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

    active_timers[uid] = msg.message_id
    asyncio.create_task(time_is_up(context, uid, update.effective_chat.id, msg.message_id, daraja))

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍥 *Xush kelibsiz, Shinobi!*\n\n"
        "Pastdagi tugmalardan qiyinlikni tanlab o'yin boshlang:\n\n"
        "🟢 *Oson* — +1 ball\n"
        "🟡 *O'rta* — +2 ball\n"
        "🔴 *Qiyin* — +3 ball",
        reply_markup=main_keyboard(),
        parse_mode="Markdown"
    )

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if "Oson" in text:
        await send_quiz(update, context, "oson")
    elif "O'rta" in text or "Orta" in text:
        await send_quiz(update, context, "orta")
    elif "Qiyin" in text:
        await send_quiz(update, context, "qiyin")
    elif "Statistika" in text:
        await stat_cmd(update, context)
    elif "Top" in text:
        await top_cmd(update, context)
    elif "Yordam" in text:
        await yordam(update, context)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    uid = str(query.from_user.id)
    chosen = int(query.data)
    active_timers.pop(uid, None)

    score, correct, incorrect, last_q_str, daraja = await get_user(uid)

    if not last_q_str:
        await query.edit_message_text("❌ Avval savol oling!")
        return

    try:
        data = json.loads(last_q_str)
    except Exception:
        await query.edit_message_text("❌ Xato. Qaytadan bosing.")
        return

    daraja = data.get("daraja", "oson")
    d = DARAJA_INFO.get(daraja, DARAJA_INFO["oson"])
    correct_idx = data["togri"]
    await set_user(uid, last_q="")

    # Barcha variantlarni ko'rsatish — to'g'ri ✅, noto'g'ri ❌
    nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    variantlar_text = ""
    for i, v in enumerate(data["variantlar"]):
        if i == correct_idx:
            variantlar_text += f"✅ {nums[i]}  *{v}*\n"
        elif i == chosen:
            variantlar_text += f"❌ {nums[i]}  ~{v}~\n"
        else:
            variantlar_text += f"▪️ {nums[i]}  {v}\n"

    if chosen == correct_idx:
        score += 1
        ball = d["ball"]
        score_new = score + ball - 1
        score = score_new
        correct += 1
        await set_user(uid, score=score, correct=correct)
        natija = f"🔥 *To'g'ri!* +{ball} chakra\n\n"
    else:
        incorrect += 1
        await set_user(uid, incorrect=incorrect)
        natija = f"💥 *Noto'g'ri!*\n\n"

    msg = (
        f"{natija}"
        f"📋 *Savol:* {data['savol']}\n\n"
        f"{variantlar_text}\n"
        f"🏆 Score: {score} | ✅ {correct} | ❌ {incorrect}"
    )

    await query.edit_message_text(msg, parse_mode="Markdown")

async def stat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    s, correct, incorrect, _, daraja = await get_user(uid)
    jami = correct + incorrect
    foiz = round((correct / jami) * 100) if jami > 0 else 0
    d = DARAJA_INFO.get(daraja, DARAJA_INFO["oson"])

    await update.message.reply_text(
        f"📊 *Sizning statistikangiz:*\n\n"
        f"🏆 Score: *{s}* ball\n"
        f"🎮 Jami savollar: {jami}\n"
        f"✅ To'g'ri: {correct}\n"
        f"❌ Noto'g'ri: {incorrect}\n"
        f"🎯 Aniqlik: {foiz}%\n"
        f"📈 Daraja: {d['emoji']} {d['text']}",
        reply_markup=main_keyboard(),
        parse_mode="Markdown"
    )

async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await get_top10()
    if not rows:
        await update.message.reply_text("Hali hech kim o'ynamagan!", reply_markup=main_keyboard())
        return

    medals = ["🥇", "🥈", "🥉"]
    text = "🏆 *TOP 10 Shinobi:*\n\n"
    for i, (user_id, sc) in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        text += f"{medal} `{user_id}` — *{sc}* ball\n"

    await update.message.reply_text(text, reply_markup=main_keyboard(), parse_mode="Markdown")

async def yordam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *Yordam:*\n\n"
        "🟢 *Oson savol* — +1 ball, oson savollar\n"
        "🟡 *O'rta savol* — +2 ball, o'rtacha qiyinlik\n"
        "🔴 *Qiyin savol* — +3 ball, qiyin savollar\n"
        "📊 *Statistika* — natijalaringiz\n"
        "🏆 *Top 10* — eng yaxshi o'yinchilar\n\n"
        "⏱ Har savolga *30 soniya* vaqt beriladi!",
        reply_markup=main_keyboard(),
        parse_mode="Markdown"
    )

# ================= MAIN =================
if __name__ == "__main__":
    asyncio.run(init_db())

    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stat", stat_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    print("Bot ishga tushdi...")
    app.run_polling()
