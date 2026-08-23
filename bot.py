import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
DB_PATH = Path(__file__).with_name("ambulancias.db")

raw_allowed = os.getenv("ALLOWED_USER_IDS", "").strip()
ALLOWED_USER_IDS = {
    int(x.strip()) for x in raw_allowed.split(",") if x.strip().isdigit()
}

DEFAULT_AMBULANCES = [
    "AMB-01", "AMB-02", "AMB-03", "AMB-04",
    "AMB-05", "AMB-06", "AMB-07", "AMB-08",
]

STATUS = {
    "available": ("🟢", "Disponible"),
    "service": ("🔴", "En servicio"),
    "offline": ("🟡", "Fuera de servicio"),
}


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ambulances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unit_code TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'available',
                latitude REAL,
                longitude REAL,
                destination_note TEXT,
                updated_at TEXT NOT NULL,
                updated_by INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unit_code TEXT NOT NULL,
                action TEXT NOT NULL,
                old_status TEXT,
                new_status TEXT,
                latitude REAL,
                longitude REAL,
                user_id INTEGER,
                username TEXT,
                created_at TEXT NOT NULL
            )
        """)
        now = datetime.now(timezone.utc).isoformat()
        for unit in DEFAULT_AMBULANCES:
            conn.execute(
                """INSERT OR IGNORE INTO ambulances
                   (unit_code, status, updated_at)
                   VALUES (?, 'available', ?)""",
                (unit, now),
            )


def allowed(update: Update) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    user = update.effective_user
    return bool(user and user.id in ALLOWED_USER_IDS)


async def deny(update: Update):
    msg = update.effective_message
    if msg:
        await msg.reply_text(
            "⛔ No tienes autorización para operar este tablero.\n"
            "Usa /miid para conocer tu Telegram User ID."
        )


def get_units():
    with db() as conn:
        return conn.execute(
            "SELECT * FROM ambulances ORDER BY unit_code"
        ).fetchall()


def get_unit(code):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM ambulances WHERE unit_code = ?", (code,)
        ).fetchone()


def log_action(unit_code, action, old_status, new_status, user, lat=None, lon=None):
    with db() as conn:
        conn.execute(
            """INSERT INTO history
               (unit_code, action, old_status, new_status, latitude, longitude,
                user_id, username, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                unit_code,
                action,
                old_status,
                new_status,
                lat,
                lon,
                user.id if user else None,
                user.username if user else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def set_status(code, new_status, user, lat=None, lon=None, note=None, action="Cambio de estado"):
    row = get_unit(code)
    if not row:
        return False

    with db() as conn:
        conn.execute(
            """UPDATE ambulances
               SET status=?, latitude=?, longitude=?, destination_note=?,
                   updated_at=?, updated_by=?
               WHERE unit_code=?""",
            (
                new_status,
                lat,
                lon,
                note,
                datetime.now(timezone.utc).isoformat(),
                user.id if user else None,
                code,
            ),
        )
    log_action(code, action, row["status"], new_status, user, lat, lon)
    return True


def board_markup():
    units = get_units()
    rows = []
    row = []
    for unit in units:
        emoji, _ = STATUS.get(unit["status"], ("⚪", unit["status"]))
        row.append(
            InlineKeyboardButton(
                f"{emoji} {unit['unit_code']}",
                callback_data=f"unit:{unit['unit_code']}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([
        InlineKeyboardButton("🔄 Actualizar", callback_data="board"),
        InlineKeyboardButton("📜 Historial", callback_data="history"),
    ])
    return InlineKeyboardMarkup(rows)


def board_text():
    units = get_units()
    counts = {"available": 0, "service": 0, "offline": 0}
    for unit in units:
        counts[unit["status"]] = counts.get(unit["status"], 0) + 1

    return (
        "🚑 *CENTRAL DE DESPACHO*\n\n"
        f"🟢 Disponibles: *{counts.get('available', 0)}*\n"
        f"🔴 En servicio: *{counts.get('service', 0)}*\n"
        f"🟡 Fuera de servicio: *{counts.get('offline', 0)}*\n\n"
        "Selecciona una unidad:"
    )


def unit_markup(code, status):
    rows = []
    if status == "available":
        rows.append([InlineKeyboardButton("🚨 Despachar", callback_data=f"dispatch:{code}")])
        rows.append([InlineKeyboardButton("🟡 Fuera de servicio", callback_data=f"offline:{code}")])
    elif status == "service":
        rows.append([InlineKeyboardButton("✅ Finalizar servicio", callback_data=f"available:{code}")])
        rows.append([InlineKeyboardButton("📍 Ver destino", callback_data=f"location:{code}")])
    elif status == "offline":
        rows.append([InlineKeyboardButton("🟢 Poner disponible", callback_data=f"available:{code}")])

    rows.append([InlineKeyboardButton("⬅️ Volver al tablero", callback_data="board")])
    return InlineKeyboardMarkup(rows)


def unit_text(row):
    emoji, label = STATUS.get(row["status"], ("⚪", row["status"]))
    text = (
        f"🚑 *{row['unit_code']}*\n\n"
        f"Estado: {emoji} *{label}*"
    )
    if row["latitude"] is not None and row["longitude"] is not None:
        text += (
            "\n\n📍 Destino registrado\n"
            f"`{row['latitude']:.6f}, {row['longitude']:.6f}`"
        )
    return text


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await deny(update)
    context.user_data.pop("awaiting_location_for", None)
    await update.message.reply_text(
        board_text(),
        parse_mode="Markdown",
        reply_markup=board_markup(),
    )


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(f"Tu Telegram User ID es: `{user.id}`", parse_mode="Markdown")


async def show_history_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await deny(update)
    text = history_text()
    await update.message.reply_text(text, parse_mode="Markdown")


def history_text():
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM history ORDER BY id DESC LIMIT 15"
        ).fetchall()

    if not rows:
        return "📜 *Historial*\n\nAún no hay movimientos."

    lines = ["📜 *Últimos movimientos*"]
    for r in rows:
        when = r["created_at"][:19].replace("T", " ")
        old_e = STATUS.get(r["old_status"], ("", ""))[0] if r["old_status"] else ""
        new_e = STATUS.get(r["new_status"], ("", ""))[0] if r["new_status"] else ""
        who = f"@{r['username']}" if r["username"] else str(r["user_id"] or "sistema")
        lines.append(
            f"\n• `{r['unit_code']}` {old_e}→{new_e} {r['action']}\n"
            f"  {when} UTC · {who}"
        )
    return "\n".join(lines)


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not allowed(update):
        return await query.edit_message_text("⛔ No autorizado.")

    data = query.data
    user = update.effective_user

    if data == "board":
        context.user_data.pop("awaiting_location_for", None)
        await query.edit_message_text(
            board_text(),
            parse_mode="Markdown",
            reply_markup=board_markup(),
        )
        return

    if data == "history":
        await query.edit_message_text(
            history_text(),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Volver al tablero", callback_data="board")]
            ]),
        )
        return

    action, code = data.split(":", 1)
    unit = get_unit(code)
    if not unit:
        await query.edit_message_text("Unidad no encontrada.")
        return

    if action == "unit":
        await query.edit_message_text(
            unit_text(unit),
            parse_mode="Markdown",
            reply_markup=unit_markup(code, unit["status"]),
        )
        return

    if action == "dispatch":
        if unit["status"] != "available":
            await query.edit_message_text(
                "⚠️ La unidad ya no está disponible. Actualiza el tablero.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Tablero", callback_data="board")]
                ]),
            )
            return

        context.user_data["awaiting_location_for"] = code

        await query.edit_message_text(
            f"🚨 *Despacho de {code}*\n\n"
            "Ahora envía la ubicación del servicio.\n\n"
            "Puedes pulsar el botón de ubicación que aparecerá abajo o "
            "adjuntar manualmente una ubicación desde Telegram.",
            parse_mode="Markdown",
        )

        keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton("📍 Enviar mi ubicación", request_location=True)],
             [KeyboardButton("❌ Cancelar despacho")]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await query.message.reply_text(
            f"📍 Esperando ubicación para *{code}*",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        return

    if action == "offline":
        set_status(code, "offline", user, action="Fuera de servicio")
        unit = get_unit(code)
        await query.edit_message_text(
            unit_text(unit),
            parse_mode="Markdown",
            reply_markup=unit_markup(code, unit["status"]),
        )
        return

    if action == "available":
        set_status(code, "available", user, action="Unidad disponible")
        unit = get_unit(code)
        await query.edit_message_text(
            unit_text(unit),
            parse_mode="Markdown",
            reply_markup=unit_markup(code, unit["status"]),
        )
        return

    if action == "location":
        if unit["latitude"] is None:
            await query.answer("No hay ubicación registrada.", show_alert=True)
            return
        lat, lon = unit["latitude"], unit["longitude"]
        maps = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
        await query.message.reply_text(
            f"📍 Destino de *{code}*\n{lat:.6f}, {lon:.6f}\n\n"
            f"🗺 Google Maps:\n{maps}",
            parse_mode="Markdown",
        )


async def location_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await deny(update)

    code = context.user_data.get("awaiting_location_for")
    if not code:
        await update.message.reply_text(
            "Recibí una ubicación, pero no hay un despacho pendiente."
        )
        return

    loc = update.message.location
    user = update.effective_user
    lat, lon = loc.latitude, loc.longitude

    unit = get_unit(code)
    if not unit or unit["status"] != "available":
        context.user_data.pop("awaiting_location_for", None)
        await update.message.reply_text(
            "⚠️ La unidad ya no está disponible.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    set_status(
        code,
        "service",
        user,
        lat=lat,
        lon=lon,
        action="Despacho",
    )
    context.user_data.pop("awaiting_location_for", None)

    maps = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"

    await update.message.reply_text(
        f"✅ *{code} DESPACHADA*\n\n"
        "🔴 Estado: *En servicio*\n"
        f"📍 `{lat:.6f}, {lon:.6f}`\n\n"
        f"🗺 Google Maps:\n{maps}",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    await update.message.reply_text(
        board_text(),
        parse_mode="Markdown",
        reply_markup=board_markup(),
    )


async def cancel_dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await deny(update)

    code = context.user_data.pop("awaiting_location_for", None)
    await update.message.reply_text(
        f"❌ Despacho cancelado{f' para {code}' if code else ''}.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await update.message.reply_text(
        board_text(),
        parse_mode="Markdown",
        reply_markup=board_markup(),
    )


async def unknown_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Cancelar despacho":
        return await cancel_dispatch(update, context)

    if context.user_data.get("awaiting_location_for"):
        await update.message.reply_text(
            "📍 Estoy esperando una ubicación. "
            "Envíala desde el botón o desde Adjuntar → Ubicación."
        )


def main():
    if not TOKEN:
        raise RuntimeError(
            "Falta TELEGRAM_BOT_TOKEN. Crea un archivo .env a partir de .env.example."
        )

    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ambulancias", start))
    app.add_handler(CommandHandler("historial", show_history_message))
    app.add_handler(CommandHandler("miid", my_id))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.LOCATION, location_received))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_text))

    print("Bot de ambulancias iniciado.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
