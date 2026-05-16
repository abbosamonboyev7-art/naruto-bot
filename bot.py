import os
import json
import asyncio
import threading
import aiosqlite
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
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

DARAJALAR = {
    "oson": "Oson savollar — asosiy Naruto faktlari",
    "orta": "O'rta savollar — birmuncha murakkab",
    "qiyin": "Qiyin savollar — juda batafsil bilim kerak"
}

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
        # Eski foydalanuvchilar uchun ustunlar qo'shish
        for col, default in [("correct", "0"), ("incorrect", "0"), ("daraja", "'oson'")]:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} {'INTEGER' if col != 'daraja' else 'TEXT'} DEFAULT {default}")
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
                "INSERT INTO users(user_id, score, correct, incorrect, last_q, daraja) VALUES (?,0,0,0,'','oson')", (uid,))
            await db.commit()
            return 0, 0, 0, "", "oson"
        return row

async def set_user(uid, score=None, correct=None, incorrect=None, last_q=None, daraja=None):
    async with aiosqlite.connect(DB) as db:
        updates = []
        values = []
        if score is not None:
            updates.append("score=?"); values.append(score)
        if correct is not None:
            updates.append("correct=?"); values.append(correct)
        if incorrect is not None:
            updates.append("incorrect=?"); values.append(incorrect)
        if last_q is not None:
            updates.append("last_q=?"); values.append(last_q)
        if daraja is not None:
            updates.append("daraja=?"); values.append(daraja)
        if updates:
            values.append(uid)
            await db.execute(f"UPDATE users SET {', '.join(updates)} WHERE user_id=?", values)
            await db.commit()

async def get_top10():
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT user_id, score FROM users ORDER BY score DESC LIMIT 10")
        return await cur.fetchall()

# ================= WEB SERVER =================
async def handle_ping(request):
    return web.Response(text="Bot ishlayapti! 🍥")

def run_web_server():
    async def start_server():
        app = web.Application()
        app.router.add_get("/", handle_ping)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        print(f"Web server {PORT} portda ishlamoqda...")
        await asyncio.sleep(float("inf"))
    asyncio.run(start_server())

# ================= AI =================
def generate_question(daraja="oson"):
    daraja_text = DARAJALAR.get(daraja, DARAJALAR["oson"])
    res = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": f"""Sen Naruto olamidagi quiz ustasisan.
Faqat o'zbek tilida gapir.
Daraja: {daraja_text}
Naruto, Sasuke, Kakashi, Itachi, Madara va boshqa personajlar haqida savollar ber."""},
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
    data = json.loads(content[start:end])
    return data

# ================= TIMER =================
active_timers = {}

async def time_is_up(context, uid, chat_id, message_id):
    await asyncio.sleep(TIMER_SECONDS)
    if active_timers.get(uid) == message_id:
        active_timers.pop(uid, None)
        await set_user(uid, last_q="")
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="⏰ *Vaqt tugadi!* 30 soniya o'tdi.\n\nYangi savol uchun /quiz bosing.",
                parse_mode="Markdown"
            )
        except Exception:
            pass

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍥 *Konoha Shinobi Quiz Bot!*\n\n"
        "📋 *Buyruqlar:*\n"
        "/quiz — savol boshlash\n"
        "/score — ballingiz\n"
        "/stat — statistika\n"
        "/top — eng yaxshi o'yinchilar\n"
        "/daraja — qiyinlik darajasi\n",
        parse_mode="Markdown"
    )

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    score, correct, incorrect, _, daraja = await get_user(uid)

    await update.message.reply_text("⏳ Savol tayyorlanmoqda...")

    try:
        data = generate_question(daraja)
    except Exception as e:
        await update.message.reply_text("❌ Savol yaratishda xato. Qayta urinib ko'ring: /quiz")
        return

    await set_user(uid, last_q=json.dumps(data, ensure_ascii=False))

    keyboard = [
        [InlineKeyboardButton(f"1️⃣  {data['variantlar'][0]}", callback_data="0")],
        [InlineKeyboardButton(f"2️⃣  {data['variantlar'][1]}", callback_data="1")],
        [InlineKeyboardButton(f"3️⃣  {data['variantlar'][2]}", callback_data="2")],
        [InlineKeyboardButton(f"4️⃣  {data['variantlar'][3]}", callback_data="3")],
    ]

    daraja_emoji = {"oson": "🟢", "orta": "🟡", "qiyin": "🔴"}.get(daraja, "🟢")

    msg = await update.message.reply_text(
        f"{daraja_emoji} *{daraja.upper()} daraja* | ⏱ {TIMER_SECONDS} soniya\n\n"
        f"🍥 *SAVOL:*\n\n{data['savol']}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

    # Taymer ishga tushirish
    active_timers[uid] = msg.message_id
    asyncio.create_task(time_is_up(context, uid, update.effective_chat.id, msg.message_id))

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    uid = str(query.from_user.id)
    chosen = int(query.data)

    # Taymerni bekor qilish
    active_timers.pop(uid, None)

    score, correct, incorrect, last_q_str, daraja = await get_user(uid)

    if not last_q_str:
        await query.edit_message_text("❌ Avval /quiz bilan savol oling!")
        return

    try:
        data = json.loads(last_q_str)
    except Exception:
        await query.edit_message_text("❌ Xato yuz berdi. /quiz bilan qayta boshlang.")
        return

    correct_idx = data["togri"]
    correct_answer = data["variantlar"][correct_idx]
    await set_user(uid, last_q="")

    if chosen == correct_idx:
        score += 1
        correct += 1
        await set_user(uid, score=score, correct=correct)
        msg = (
            f"✅ *To'g'ri!* +1 chakra 🔥\n\n"
            f"Javob: *{correct_answer}*\n\n"
            f"🏆 Score: {score} | ✅ {correct} | ❌ {incorrect}"
        )
    else:
        incorrect += 1
        chosen_answer = data["variantlar"][chosen]
        await set_user(uid, incorrect=incorrect)
        msg = (
            f"❌ *Noto'g'ri!*\n\n"
            f"Siz: {chosen_answer}\n"
            f"To'g'ri javob: *{correct_answer}*\n\n"
            f"🏆 Score: {score} | ✅ {correct} | ❌ {incorrect}"
        )

    await query.edit_message_text(msg, parse_mode="Markdown")

async def score_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    s, correct, incorrect, _, daraja = await get_user(uid)
    daraja_emoji = {"oson": "🟢", "orta": "🟡", "qiyin": "🔴"}.get(daraja, "🟢")
    await update.message.reply_text(
        f"🏆 *Sizning natijangiz:*\n\n"
        f"Score: *{s}*\n"
        f"Daraja: {daraja_emoji} {daraja}\n\n"
        f"✅ To'g'ri: {correct}\n"
        f"❌ Noto'g'ri: {incorrect}",
        parse_mode="Markdown"
    )

async def stat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    s, correct, incorrect, _, daraja = await get_user(uid)
    jami = correct + incorrect
    foiz = round((correct / jami) * 100) if jami > 0 else 0

    await update.message.reply_text(
        f"📊 *Statistika:*\n\n"
        f"🎮 Jami savollar: {jami}\n"
        f"✅ To'g'ri: {correct}\n"
        f"❌ Noto'g'ri: {incorrect}\n"
        f"🎯 Aniqlik: {foiz}%\n"
        f"🏆 Score: {s}",
        parse_mode="Markdown"
    )

async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await get_top10()
    if not rows:
        await update.message.reply_text("Hali hech kim o'ynamagan!")
        return

    medals = ["🥇", "🥈", "🥉"]
    text = "🏆 *TOP 10 Shinobi:*\n\n"
    for i, (user_id, sc) in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        text += f"{medal} User {user_id[:6]}... — {sc} ball\n"

    await update.message.reply_text(text, parse_mode="Markdown")

async def daraja_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🟢 Oson", callback_data="daraja_oson")],
        [InlineKeyboardButton("🟡 O'rta", callback_data="daraja_orta")],
        [InlineKeyboardButton("🔴 Qiyin", callback_data="daraja_qiyin")],
    ]
    await update.message.reply_text(
        "🎯 *Qiyinlik darajasini tanlang:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def daraja_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    uid = str(query.from_user.id)
    daraja = query.data.replace("daraja_", "")
    await set_user(uid, daraja=daraja)

    emoji = {"oson": "🟢", "orta": "🟡", "qiyin": "🔴"}.get(daraja, "🟢")
    await query.edit_message_text(
        f"{emoji} Daraja *{daraja}* ga o'rnatildi!\n\n/quiz bilan boshlang.",
        parse_mode="Markdown"
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data.startswith("daraja_"):
        await daraja_button(update, context)
    else:
        await button(update, context)

# ================= MAIN =================
if __name__ == "__main__":
    asyncio.run(init_db())

    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("score", score_cmd))
    app.add_handler(CommandHandler("stat", stat_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("daraja", daraja_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("Bot ishga tushdi...")
    app.run_polling()
