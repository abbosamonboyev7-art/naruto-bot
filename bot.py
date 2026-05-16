import os
import aiosqlite
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from openai import OpenAI

# ================= ENV =================
TOKEN = os.getenv("TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")

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

# ================= AI =================
def generate_question():
    res = client.chat.completions.create(
        model="llama3-8b-8192",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Naruto bo'yicha oson quiz savol ber (faqat savol)"}
        ]
    )
    return res.choices[0].message.content

def check_answer(q, a):
    res = client.chat.completions.create(
        model="llama3-8b-8192",
        messages=[
            {"role": "system", "content": "TRUE yoki FALSE qaytar"},
            {"role": "user", "content": f"Savol: {q}\nJavob: {a}"}
        ]
    )
    return "true" in res.choices[0].message.content.lower()

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍥 Konoha shinobi bot!\n\n"
        "/quiz - boshlash\n/score - ball"
    )

# ================= QUIZ =================
async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)

    q = generate_question()
    await set_user(uid, last_q=q)

    keyboard = [
        [InlineKeyboardButton("🅰 A", callback_data="A"),
         InlineKeyboardButton("🅱 B", callback_data="B")],
        [InlineKeyboardButton("🅲 C", callback_data="C"),
         InlineKeyboardButton("🅳 D", callback_data="D")]
    ]

    await update.message.reply_text(
        f"🍥 SAVOL:\n\n{q}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ================= ANSWER =================
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    uid = str(query.from_user.id)
    ans = query.data

    score, last_q = await get_user(uid)

    if check_answer(last_q, ans):
        score += 1
        msg = "🔥 To'g'ri! +1 chakra"
    else:
        msg = "💥 Noto'g'ri!"

    await set_user(uid, score=score)

    await query.edit_message_text(f"{msg}\n🏆 Score: {score}")

# ================= SCORE =================
async def score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    s, _ = await get_user(uid)

    await update.message.reply_text(f"🏆 Shinobi score: {s}")

# ================= MAIN =================
if __name__ == "__main__":
    import asyncio
    asyncio.run(init_db())

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("score", score))
    app.add_handler(CallbackQueryHandler(button))

    print("Bot ishga tushdi...")
    app.run_polling()
