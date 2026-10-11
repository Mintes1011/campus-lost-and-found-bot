import os
import re
import sqlite3
from datetime import datetime
from difflib import SequenceMatcher

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ============================================================
# CONFIGURATION: edit these two settings before running.
# ============================================================
BOT_TOKEN = "PASTE_YOUR_BOT_TOKEN_HERE"
ADMIN_CHAT_ID = 0  # Replace 0 with the representative's Telegram ID.
DB_FILE = "lost_found.db"

CATEGORIES = [
    "University ID", "PC cable / charger", "PC bag",
    "Phone", "Money / wallet", "Keys", "Book / notebook", "Other"
]
MAIN_MENU = [["Report LOST item", "Report FOUND item"], ["Cancel"]]
CATEGORY_MENU = [[c] for c in CATEGORIES] + [["Cancel"]]
STEPS = ["category", "description", "location", "date", "contact"]

def db():
    return sqlite3.connect(DB_FILE)

def init_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                location TEXT NOT NULL,
                item_date TEXT NOT NULL,
                contact TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open'
            )
        """)

def normalize(text):
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).strip()

def similarity(a, b):
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    seq = SequenceMatcher(None, a, b).ratio()
    wa, wb = set(a.split()), set(b.split())
    overlap = len(wa & wb) / max(1, len(wa | wb))
    return max(seq, overlap)

def find_matches(report_id, category, description, location, kind):
    opposite = "found" if kind == "lost" else "lost"
    with db() as con:
        rows = con.execute("""
            SELECT id, category, description, location, item_date
            FROM reports
            WHERE kind = ? AND status = 'open' AND id != ?
        """, (opposite, report_id)).fetchall()

    matches = []
    for row in rows:
        rid, cat, desc, loc, item_date = row
        if cat != category:
            continue
        score = (
            0.60 * similarity(description, desc)
            + 0.40 * similarity(location, loc)
        )
        if score >= 0.20:
            matches.append((score, row))
    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[:3]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Welcome to the University Lost & Found Bot! 🎓\n\n"
        "Use this bot to report an item you lost or found on campus.\n"
        "The bot checks reports for possible matches and alerts the student representative.\n\n"
        "Choose an option below.",
        reply_markup=ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True),
    )

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Your Telegram user/chat ID is: {update.effective_user.id}\n\n"
        "The student representative should copy this number into ADMIN_CHAT_ID "
        "in bot.py, then restart the bot."
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Cancelled. Use /start to begin again.",
        reply_markup=ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True),
    )

async def begin_report(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str):
    context.user_data.clear()
    context.user_data["report"] = {"kind": kind}
    await update.message.reply_text(
        f"You are reporting an item you {kind.upper()}.\n\nChoose the item category:",
        reply_markup=ReplyKeyboardMarkup(CATEGORY_MENU, resize_keyboard=True),
    )
    context.user_data["step"] = "category"

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    text = (message.text or "").strip()
    if not text:
        return

    if text.lower() == "cancel":
        await cancel(update, context)
        return
    if text == "Report LOST item":
        await begin_report(update, context, "lost")
        return
    if text == "Report FOUND item":
        await begin_report(update, context, "found")
        return

    report = context.user_data.get("report")
    step = context.user_data.get("step")
    if not report or not step:
        await message.reply_text("Please choose an option. Send /start to open the menu.")
        return

    if step == "category":
        if text not in CATEGORIES:
            await message.reply_text(
                "Please choose a category using the buttons below.",
                reply_markup=ReplyKeyboardMarkup(CATEGORY_MENU, resize_keyboard=True),
            )
            return
        report["category"] = text
        context.user_data["step"] = "description"
        await message.reply_text(
            "Describe the item. For safety, do not include passwords, PINs, or full bank details.",
            reply_markup=ReplyKeyboardRemove(),
        )
    elif step == "description":
        report["description"] = text[:500]
        context.user_data["step"] = "location"
        await message.reply_text("Where was it lost or found? Example: Library, ground floor.")
    elif step == "location":
        report["location"] = text[:250]
        context.user_data["step"] = "date"
        await message.reply_text("On what date was it lost or found? Example: 2026-10-09 or 'yesterday'.")
    elif step == "date":
        report["date"] = text[:100]
        context.user_data["step"] = "contact"
        await message.reply_text(
            "How can the representative contact you? You can enter your Telegram @username "
            "or type 'here' to be contacted through this bot."
        )
    elif step == "contact":
        report["contact"] = text[:150]
        user = update.effective_user
        with db() as con:
            cur = con.execute("""
                INSERT INTO reports
                (kind, category, description, location, item_date, contact,
                 user_id, username, created_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
            """, (
                report["kind"], report["category"], report["description"],
                report["location"], report["date"], report["contact"],
                user.id, user.username, datetime.now().isoformat(timespec="seconds")
            ))
            report_id = cur.lastrowid

        matches = find_matches(
            report_id, report["category"], report["description"],
            report["location"], report["kind"]
        )

        summary = (
            f"📌 NEW {report['kind'].upper()} REPORT #{report_id}\n"
            f"Category: {report['category']}\n"
            f"Description: {report['description']}\n"
            f"Location: {report['location']}\n"
            f"Date: {report['date']}\n"
            f"Contact preference: {report['contact']}\n"
            f"Reporter Telegram ID: {user.id}\n"
            f"Status: open"
        )
        if matches:
            summary += "\n\n🔎 POSSIBLE MATCHES (please verify manually):"
            for score, row in matches:
                summary += (
                    f"\n#{row[0]} — {row[1]}; {row[2]}; location: {row[3]}; "
                    f"date: {row[4]} (similarity {score:.0%})"
                )

        if ADMIN_CHAT_ID:
            try:
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=summary)
            except Exception:
                await message.reply_text(
                    "Your report was saved, but the representative notification failed. "
                    "Please tell the representative to check the bot configuration."
                )
        else:
            await message.reply_text(
                "The report was saved, but the representative has not been configured yet. "
                "Ask the representative to set ADMIN_CHAT_ID in bot.py."
            )

        await message.reply_text(
            f"✅ Your report #{report_id} has been saved.\n"
            "The student representative will review it. Possible matches are suggestions only; "
            "the item must be verified before it is returned.",
            reply_markup=ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True),
        )
        context.user_data.clear()

async def reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_CHAT_ID or update.effective_chat.id != ADMIN_CHAT_ID:
        await update.message.reply_text("This command is only for the configured representative.")
        return
    with db() as con:
        rows = con.execute("""
            SELECT id, kind, category, description, location, item_date, status
            FROM reports ORDER BY id DESC LIMIT 15
        """).fetchall()
    if not rows:
        await update.message.reply_text("No reports yet.")
        return
    lines = ["Latest reports:"]
    for r in rows:
        lines.append(
            f"#{r[0]} [{r[1].upper()}] {r[2]} — {r[3]} | {r[4]} | "
            f"{r[5]} | status: {r[6]}"
        )
    await update.message.reply_text("\n".join(lines)[:4000])

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start — open the menu\n"
        "/myid — show your Telegram ID (for representative setup)\n"
        "/reports — show recent reports (representative only)\n"
        "/cancel — cancel the current report"
    )

def main():
    if BOT_TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        raise SystemExit("Open bot.py and replace BOT_TOKEN with the token from BotFather.")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("reports", reports))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("University Lost & Found bot is running. Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()
