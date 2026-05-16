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
GROQ_KEY = os.getenv("GROQ_API_KEY")
PORT = int(os.getenv("PORT", 8000))

client = OpenAI(api_key=GROQ_KEY, base_url="https://api.groq.com/openai/v1")

DB = "naruto.db"

SYSTEM_PROMPT = """
Sen Naruto olamidagi Konoha shinobi quiz ustasisan.
Faqat o'zbek tilida gapir.
Naruto, Sasuke, Kakashi, Itachi, Madara haqida savollar ber.
Stil: ninja, qisqa, anime vibe.
"""

# ================= DB =================
async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            score INTEGER DEFAULT 0,
            last_q TEXT
        )
        """)
        await db.commit()

async def get_user(uid):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT score, last_q FROM users WHERE user_id=?", (uid,))
        row = await cur.fetchone()
        if not row:
            await db.execute("INSERT INTO users(user_id, score, last_q) VALUES (?,0,'')", (uid,))
            await db.commit()
            return 0, ""
        return row

async def set_user(uid, score=None, last_q=None):
    async with aiosqlite.connect(DB) as db:
        if score is not None:
            await db.execute("UPDATE users SET score=? WHERE user_id=?", (score, uid))
        if last_q is not None:
            await db.execute("UPDATE users SET last_q=? WHERE user_id=?", (last_q, uid))
        await db.commit()

# ================= WEB SERVER (UptimeRobot uchun) =================
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
def generate_question():
    res = client.chat.completions.create(
        model="llama3-8b-8192",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
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
    # JSON ni ajratib olish
    start = content.find("{")
    end = content.rfind("}") + 1
    data = json.loads(content[start:end])
    return data

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍥 Konoha shinobi bot!\n\n"
        "/quiz - savol boshlash\n/score - ballingiz"
    )

# ================= QUIZ =================
async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)

    await update.message.reply_text("⏳ Savol tayyorlanmoqda...")

    try:
        data = generate_question()
    except Exception as e:
        await update.message.reply_text("❌ Savol yaratishda xato. Qayta urinib ko'ring: /quiz")
        return

    await set_user(uid, last_q=json.dumps(data, ensure_ascii=False))

    keyboard = [
        [InlineKeyboardButton(f"1️⃣ {data['variantlar'][0]}", callback_data="0")],
        [InlineKeyboardButton(f"2️⃣ {data['variantlar'][1]}", callback_data="1")],
        [InlineKeyboardButton(f"3️⃣ {data['variantlar'][2]}", callback_data="2")],
        [InlineKeyboardButton(f"4️⃣ {data['variantlar'][3]}", callback_data="3")],
    ]

    await update.message.reply_text(
        f"🍥 *SAVOL:*\n\n{data['savol']}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# ================= ANSWER =================
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    uid = str(query.from_user.id)
    chosen = int(query.data)

    score, last_q_str = await get_user(uid)

    if not last_q_str:
        await query.edit_message_text("❌ Avval /quiz bilan savol oling!")
        return

    try:
        data = json.loads(last_q_str)
    except Exception:
        await query.edit_message_text("❌ Xato yuz berdi. /quiz bilan qayta boshlang.")
        return

    correct = data["togri"]
    correct_answer = data["variantlar"][correct]

    if chosen == correct:
        score += 1
        await set_user(uid, score=score)
        msg = f"✅ *To'g'ri!* +1 chakra 🔥\n\nJavob: *{correct_answer}*\n\n🏆 Score: {score}"
    else:
        chosen_answer = data["variantlar"][chosen]
        msg = f"❌ *Noto'g'ri!*\n\nSiz: {chosen_answer}\nTo'g'ri javob: *{correct_answer}*\n\n🏆 Score: {score}"

    await query.edit_message_text(msg, parse_mode="Markdown")

# ================= SCORE =================
async def score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    s, _ = await get_user(uid)
    await update.message.reply_text(f"🏆 Shinobi score: {s}")

# ================= MAIN =================
if __name__ == "__main__":
    asyncio.run(init_db())

    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("score", score))
    app.add_handler(CallbackQueryHandler(button))

    print("Bot ishga tushdi...")
    app.run_polling()
