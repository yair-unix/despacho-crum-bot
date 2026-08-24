import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
DB_PATH = Path(__file__).with_name("ambulancias.db")
DEFAULT_AMBULANCES = [str(n) for n in range(674, 696)]

STATUS = {
    "available": ("🟢", "Disponible"),
    "service": ("🔴", "En servicio"),
    "offline": ("🟡", "Fuera de servicio"),
}

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def has_col(conn, table, col):
    return any(r["name"] == col for r in conn.execute(f"PRAGMA table_info({table})"))

def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ambulances(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unit_code TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL DEFAULT 'available',
                destination_url TEXT,
                updated_at TEXT NOT NULL,
                updated_by INTEGER
            )
        """)
        if not has_col(conn, "ambulances", "destination_url"):
            conn.execute("ALTER TABLE ambulances ADD COLUMN destination_url TEXT")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unit_code TEXT NOT NULL,
                action TEXT NOT NULL,
                old_status TEXT,
                new_status TEXT,
                destination_url TEXT,
                user_id INTEGER,
                username TEXT,
                created_at TEXT NOT NULL
            )
        """)
        if not has_col(conn, "history", "destination_url"):
            conn.execute("ALTER TABLE history ADD COLUMN destination_url TEXT")

        conn.execute("DELETE FROM ambulances WHERE unit_code LIKE 'AMB-%'")
        now = datetime.now(timezone.utc).isoformat()
        for unit in DEFAULT_AMBULANCES:
            conn.execute(
                "INSERT OR IGNORE INTO ambulances(unit_code,status,updated_at) VALUES(?, 'available', ?)",
                (unit, now)
            )

def get_units():
    with db() as conn:
        return conn.execute("SELECT * FROM ambulances ORDER BY CAST(unit_code AS INTEGER)").fetchall()

def get_unit(code):
    with db() as conn:
        return conn.execute("SELECT * FROM ambulances WHERE unit_code=?", (code,)).fetchone()

def save_status(code, new_status, user, destination_url=None, action="Cambio de estado"):
    old = get_unit(code)
    if not old:
        return
    stored_url = destination_url if new_status == "service" else None
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        conn.execute(
            "UPDATE ambulances SET status=?, destination_url=?, updated_at=?, updated_by=? WHERE unit_code=?",
            (new_status, stored_url, now, user.id if user else None, code)
        )
        conn.execute(
            """INSERT INTO history(unit_code,action,old_status,new_status,destination_url,user_id,username,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (code, action, old["status"], new_status, stored_url,
             user.id if user else None, user.username if user else None, now)
        )

def valid_maps_url(text):
    try:
        p = urlparse(text.strip())
        host = (p.hostname or "").lower()
        return p.scheme in ("http", "https") and (
            host in {"maps.app.goo.gl","goo.gl","google.com","www.google.com","maps.google.com"}
            or host.endswith(".google.com")
        )
    except Exception:
        return False

def board_text():
    units = get_units()
    counts = {"available":0,"service":0,"offline":0}
    for u in units:
        counts[u["status"]] = counts.get(u["status"], 0) + 1
    return (
        "🚑 DESPACHO CRUM\n\n"
        f"🟢 Disponibles: {counts['available']}\n"
        f"🔴 En servicio: {counts['service']}\n"
        f"🟡 Fuera de servicio: {counts['offline']}\n\n"
        "Selecciona un número económico:"
    )

def board_markup():
    units = get_units()
    rows, row = [], []
    for u in units:
        emoji = STATUS[u["status"]][0]
        row.append(InlineKeyboardButton(f"{emoji} {u['unit_code']}", callback_data=f"unit:{u['unit_code']}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton("🔄 Actualizar", callback_data="board"),
        InlineKeyboardButton("📜 Historial", callback_data="history")
    ])
    return InlineKeyboardMarkup(rows)

def unit_markup(u):
    rows = []
    code = u["unit_code"]

    if u["status"] == "available":
        rows.append([InlineKeyboardButton("🚨 Despachar", callback_data=f"dispatch:{code}")])
        rows.append([InlineKeyboardButton("🟡 Fuera de servicio", callback_data=f"offline:{code}")])
    elif u["status"] == "service":
        if u["destination_url"]:
            rows.append([InlineKeyboardButton("🗺 Abrir destino", url=u["destination_url"])])
        rows.append([InlineKeyboardButton("✅ Finalizar servicio", callback_data=f"available:{code}")])
    else:
        rows.append([InlineKeyboardButton("🟢 Poner disponible", callback_data=f"available:{code}")])

    rows.append([InlineKeyboardButton("⬅️ Tablero", callback_data="board")])
    return InlineKeyboardMarkup(rows)

def unit_text(u):
    emoji, label = STATUS[u["status"]]
    txt = f"🚑 Unidad {u['unit_code']}\n\nEstado: {emoji} {label}"
    if u["destination_url"]:
        txt += f"\n\n📍 Destino registrado:\n{u['destination_url']}"
    return txt

def history_text():
    with db() as conn:
        rows = conn.execute("SELECT * FROM history ORDER BY id DESC LIMIT 15").fetchall()
    if not rows:
        return "📜 Historial\n\nAún no hay movimientos."

    out = ["📜 Últimos movimientos"]
    for r in rows:
        out.append(f"\n• {r['unit_code']} — {r['action']}")
        if r["destination_url"]:
            out.append(f"  📍 {r['destination_url']}")
    return "\n".join(out)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("awaiting_destination_for", None)
    await update.message.reply_text(board_text(), reply_markup=board_markup())

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "board":
        context.user_data.pop("awaiting_destination_for", None)
        await q.edit_message_text(board_text(), reply_markup=board_markup())
        return

    if data == "history":
        await q.edit_message_text(
            history_text(),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Tablero", callback_data="board")]])
        )
        return

    action, code = data.split(":", 1)
    u = get_unit(code)
    if not u:
        await q.edit_message_text("Unidad no encontrada.")
        return

    if action == "unit":
        await q.edit_message_text(unit_text(u), reply_markup=unit_markup(u))
        return

    if action == "dispatch":
        if u["status"] != "available":
            await q.edit_message_text("⚠️ La unidad ya no está disponible.", reply_markup=board_markup())
            return

        context.user_data["awaiting_destination_for"] = code
        await q.edit_message_text(
            f"🚨 Despacho de unidad {code}\n\n"
            "Pega ahora el link de Google Maps del destino.\n\n"
            "Ejemplo:\nhttps://maps.app.goo.gl/XXXXXXXX",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancelar", callback_data="board")]])
        )
        return

    if action == "offline":
        save_status(code, "offline", update.effective_user, action="Fuera de servicio")
    elif action == "available":
        save_status(code, "available", update.effective_user, action="Unidad disponible")

    u = get_unit(code)
    await q.edit_message_text(unit_text(u), reply_markup=unit_markup(u))

async def text_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = context.user_data.get("awaiting_destination_for")
    if not code:
        return

    text = (update.message.text or "").strip()

    if not valid_maps_url(text):
        await update.message.reply_text(
            "⚠️ Necesito un link válido de Google Maps.\n\n"
            "Ejemplo:\nhttps://maps.app.goo.gl/XXXXXXXX"
        )
        return

    u = get_unit(code)
    if not u or u["status"] != "available":
        context.user_data.pop("awaiting_destination_for", None)
        await update.message.reply_text("⚠️ La unidad ya no está disponible.")
        return

    save_status(code, "service", update.effective_user, destination_url=text, action="Despacho")
    context.user_data.pop("awaiting_destination_for", None)

    await update.message.reply_text(
        f"✅ UNIDAD {code} DESPACHADA\n\n"
        "🔴 Estado: En servicio\n"
        f"📍 Destino:\n{text}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗺 Abrir destino", url=text)],
            [InlineKeyboardButton("🚑 Volver al tablero", callback_data="board")]
        ])
    )

def main():
    if not TOKEN:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN")

    init_db()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ambulancias", start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_received))

    print("Despacho CRUM V3 iniciado")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
