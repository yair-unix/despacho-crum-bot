import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
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
DB_PATH = Path(__file__).with_name("despacho_crum_simple.db")

AMBULANCES = [str(n) for n in range(674, 696)]
VECTORS = ["V07", "V08", "V15"]

STATUS = {
    "available": ("🟢", "Disponible"),
    "unavailable": ("🟡", "No disponible"),
    "dispatched": ("🚨", "Despachada"),
    "on_scene": ("🔴", "En labor"),
    "transport": ("🚑", "En traslado"),
    "hospital_arrival": ("🏥", "Arribo hospital"),
    "hospital_departure": ("🚪", "Salida hospital"),
    "service_end": ("✅", "Fin del servicio"),
}

AMBULANCE_TRANSITIONS = {
    "available": ["dispatched", "unavailable"],
    "unavailable": ["available"],
    "dispatched": ["on_scene"],
    "on_scene": ["transport", "service_end"],
    "transport": ["hospital_arrival"],
    "hospital_arrival": ["hospital_departure"],
    "hospital_departure": ["available", "unavailable"],
    "service_end": ["available", "unavailable"],
}

VECTOR_TRANSITIONS = {
    "available": ["dispatched", "unavailable"],
    "unavailable": ["available"],
    "dispatched": ["on_scene"],
    "on_scene": ["service_end"],
    "service_end": ["available", "unavailable"],
}


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS units (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unit_code TEXT UNIQUE NOT NULL,
                unit_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'available',
                unavailable_reason TEXT,
                destination_url TEXT,
                updated_at TEXT NOT NULL,
                updated_by INTEGER
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unit_code TEXT NOT NULL,
                unit_type TEXT NOT NULL,
                old_status TEXT,
                new_status TEXT NOT NULL,
                reason TEXT,
                destination_url TEXT,
                user_id INTEGER,
                username TEXT,
                created_at TEXT NOT NULL
            )
        """)

        now = datetime.now(timezone.utc).isoformat()

        for code in AMBULANCES:
            conn.execute(
                """INSERT OR IGNORE INTO units
                   (unit_code, unit_type, status, updated_at)
                   VALUES (?, 'ambulance', 'available', ?)""",
                (code, now),
            )

        for code in VECTORS:
            conn.execute(
                """INSERT OR IGNORE INTO units
                   (unit_code, unit_type, status, updated_at)
                   VALUES (?, 'vector', 'available', ?)""",
                (code, now),
            )


def get_units():
    with db() as conn:
        return conn.execute("""
            SELECT * FROM units
            ORDER BY CASE WHEN unit_type='ambulance' THEN 0 ELSE 1 END,
                     unit_code
        """).fetchall()


def get_unit(code):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM units WHERE unit_code=?",
            (code,),
        ).fetchone()


def transitions_for(unit):
    return AMBULANCE_TRANSITIONS if unit["unit_type"] == "ambulance" else VECTOR_TRANSITIONS


def valid_maps_url(text):
    try:
        p = urlparse(text.strip())
        host = (p.hostname or "").lower()
        return p.scheme in ("http", "https") and (
            host in {
                "maps.app.goo.gl",
                "goo.gl",
                "google.com",
                "www.google.com",
                "maps.google.com",
            } or host.endswith(".google.com")
        )
    except Exception:
        return False


def change_status(code, new_status, user, reason=None, destination_url=None):
    unit = get_unit(code)

    if not unit:
        return False, "Unidad no encontrada."

    allowed = transitions_for(unit).get(unit["status"], [])
    if new_status not in allowed:
        return False, "Ese cambio de estado ya no está disponible."

    old_status = unit["status"]
    old_destination = unit["destination_url"]

    new_reason = unit["unavailable_reason"]
    new_destination = unit["destination_url"]

    if new_status == "unavailable":
        new_reason = reason
        new_destination = None
    elif new_status == "available":
        new_reason = None
        new_destination = None

    if destination_url:
        new_destination = destination_url

    now = datetime.now(timezone.utc).isoformat()

    with db() as conn:
        conn.execute(
            """UPDATE units
               SET status=?, unavailable_reason=?, destination_url=?,
                   updated_at=?, updated_by=?
               WHERE unit_code=?""",
            (
                new_status,
                new_reason,
                new_destination,
                now,
                user.id if user else None,
                code,
            ),
        )

        conn.execute(
            """INSERT INTO history
               (unit_code, unit_type, old_status, new_status,
                reason, destination_url, user_id, username, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                code,
                unit["unit_type"],
                old_status,
                new_status,
                reason,
                destination_url or old_destination,
                user.id if user else None,
                user.username if user else None,
                now,
            ),
        )

    return True, None


def clear_history():
    with db() as conn:
        conn.execute("DELETE FROM history")
        conn.commit()


def reset_all_units():
    now = datetime.now(timezone.utc).isoformat()

    with db() as conn:
        conn.execute("DELETE FROM history")
        conn.execute(
            """UPDATE units
               SET status='available',
                   unavailable_reason=NULL,
                   destination_url=NULL,
                   updated_at=?,
                   updated_by=NULL""",
            (now,),
        )
        conn.commit()


def board_text():
    units = get_units()
    ambs = [u for u in units if u["unit_type"] == "ambulance"]
    vecs = [u for u in units if u["unit_type"] == "vector"]

    counts = {key: 0 for key in STATUS}
    for u in units:
        counts[u["status"]] += 1

    return (
        "🚑 DESPACHO CRUM\n\n"
        f"🚑 Ambulancias: {len(ambs)}\n"
        f"🏍️ Vectores: {len(vecs)}\n\n"
        f"🟢 Disponibles: {counts['available']}\n"
        f"🟡 No disponibles: {counts['unavailable']}\n"
        f"🚨 Despachadas: {counts['dispatched']}\n"
        f"🔴 En labor: {counts['on_scene']}\n"
        f"🚑 En traslado: {counts['transport']}\n"
        f"🏥 Arribo hospital: {counts['hospital_arrival']}\n"
        f"🚪 Salida hospital: {counts['hospital_departure']}\n"
        f"✅ Fin del servicio: {counts['service_end']}\n\n"
        "Selecciona una unidad:"
    )


def board_markup():
    units = get_units()
    ambs = [u for u in units if u["unit_type"] == "ambulance"]
    vecs = [u for u in units if u["unit_type"] == "vector"]

    rows = []
    rows.append([InlineKeyboardButton("🚑 AMBULANCIAS", callback_data="noop")])

    row = []
    for u in ambs:
        row.append(
            InlineKeyboardButton(
                f"{STATUS[u['status']][0]} {u['unit_code']}",
                callback_data=f"openunit:{u['unit_code']}",
            )
        )
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton("🏍️ VECTORES", callback_data="noop")])

    row = []
    for u in vecs:
        row.append(
            InlineKeyboardButton(
                f"{STATUS[u['status']][0]} {u['unit_code']}",
                callback_data=f"openunit:{u['unit_code']}",
            )
        )
    if row:
        rows.append(row)

    rows.append([
        InlineKeyboardButton("🔄 Actualizar este tablero", callback_data="refreshboard"),
        InlineKeyboardButton("📜 Bitácora", callback_data="generalhistory"),
    ])

    return InlineKeyboardMarkup(rows)


def unit_text(unit):
    title = (
        f"🚑 Ambulancia {unit['unit_code']}"
        if unit["unit_type"] == "ambulance"
        else f"🏍️ Vector {unit['unit_code']}"
    )

    emoji, label = STATUS[unit["status"]]
    text = f"{title}\n\nEstado: {emoji} {label}"

    if unit["unavailable_reason"]:
        text += f"\n\n📝 Motivo:\n{unit['unavailable_reason']}"

    if unit["destination_url"]:
        text += f"\n\n📍 Destino:\n{unit['destination_url']}"

    return text


def unit_markup(unit):
    code = unit["unit_code"]
    rows = []

    for target in transitions_for(unit).get(unit["status"], []):
        if target == "dispatched":
            rows.append([
                InlineKeyboardButton("🚨 Despachar", callback_data=f"dispatch:{code}")
            ])
        elif target == "unavailable":
            rows.append([
                InlineKeyboardButton("🟡 No disponible", callback_data=f"unavailable:{code}")
            ])
        else:
            emoji, label = STATUS[target]
            rows.append([
                InlineKeyboardButton(
                    f"{emoji} {label}",
                    callback_data=f"status:{code}:{target}",
                )
            ])

    if unit["destination_url"]:
        rows.append([
            InlineKeyboardButton("🗺 Abrir destino", url=unit["destination_url"])
        ])

    rows.append([
        InlineKeyboardButton("📜 Bitácora unidad", callback_data=f"unitlog:{code}")
    ])
    rows.append([
        InlineKeyboardButton("🚑 Ver tablero", callback_data="newboard")
    ])

    return InlineKeyboardMarkup(rows)


def format_entry(e):
    when = e["created_at"][:19].replace("T", " ")
    emoji, label = STATUS.get(e["new_status"], ("•", e["new_status"]))

    who = ""
    if e["username"]:
        who = f"@{e['username']}"
    elif e["user_id"]:
        who = str(e["user_id"])

    lines = [
        f"{emoji} {e['unit_code']} — {label}",
        f"🕐 {when} UTC",
    ]

    if e["reason"]:
        lines.append(f"📝 {e['reason']}")

    if e["destination_url"] and e["new_status"] == "dispatched":
        lines.append(f"📍 {e['destination_url']}")

    if who:
        lines.append(f"👤 {who}")

    return "\n".join(lines)


def history_text(limit=30):
    with db() as conn:
        entries = conn.execute(
            "SELECT * FROM history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    if not entries:
        return "📜 BITÁCORA\n\nAún no hay movimientos."

    return "📜 BITÁCORA GENERAL\n\n" + "\n\n".join(format_entry(e) for e in entries)


def unit_history_text(code, limit=50):
    with db() as conn:
        entries = conn.execute(
            """SELECT * FROM history
               WHERE unit_code=?
               ORDER BY id DESC
               LIMIT ?""",
            (code, limit),
        ).fetchall()

    if not entries:
        return f"📜 BITÁCORA {code}\n\nSin movimientos registrados."

    return f"📜 BITÁCORA {code}\n\n" + "\n\n".join(
        format_entry(e) for e in reversed(entries)
    )


async def send_board(message):
    await message.reply_text(
        board_text(),
        reply_markup=board_markup(),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await send_board(update.effective_message)


async def bitacora(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        code = context.args[0].upper().strip()

        if not get_unit(code):
            await update.effective_message.reply_text(
                "⚠️ Unidad no encontrada.\n\n"
                "Ejemplos:\n/bitacora 684\n/bitacora V07"
            )
            return

        text = unit_history_text(code)
    else:
        text = history_text()

    await update.effective_message.reply_text(text)


async def borrarbitacora(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "⚠️ BORRAR BITÁCORA\n\n"
        "Esto eliminará todos los registros históricos.\n"
        "NO cambiará los estados actuales de las unidades.\n\n"
        "¿Confirmas?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Sí, borrar bitácora", callback_data="confirm_clear_history")],
            [InlineKeyboardButton("❌ Cancelar", callback_data="cancel_action")],
        ]),
    )


async def reiniciar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "⚠️ REINICIAR SISTEMA SIMPLE\n\n"
        "Esto hará lo siguiente:\n"
        "• Borrará toda la bitácora\n"
        "• Pondrá todas las ambulancias y vectores en 🟢 Disponible\n"
        "• Limpiará destinos activos\n"
        "• Limpiará motivos de No disponible\n\n"
        "¿Confirmas?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Sí, reiniciar todo", callback_data="confirm_reset_all")],
            [InlineKeyboardButton("❌ Cancelar", callback_data="cancel_action")],
        ]),
    )


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "noop":
        return

    if data == "cancel_action":
        await q.edit_message_text("❌ Operación cancelada.")
        return

    if data == "confirm_clear_history":
        clear_history()
        await q.edit_message_text(
            "✅ Bitácora borrada.\n\n"
            "Los estados actuales de las unidades se conservaron."
        )
        return

    if data == "confirm_reset_all":
        reset_all_units()
        context.user_data.clear()
        await q.edit_message_text(
            "✅ Sistema reiniciado.\n\n"
            "Todas las unidades quedaron 🟢 Disponibles y la bitácora fue eliminada.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚑 Ver tablero", callback_data="newboard")]
            ]),
        )
        return

    if data == "refreshboard":
        await q.edit_message_text(
            board_text(),
            reply_markup=board_markup(),
        )
        return

    if data == "newboard":
        await q.message.reply_text(
            board_text(),
            reply_markup=board_markup(),
        )
        return

    if data.startswith("openunit:"):
        code = data.split(":", 1)[1]
        unit = get_unit(code)

        await q.message.reply_text(
            unit_text(unit),
            reply_markup=unit_markup(unit),
        )
        return

    if data == "generalhistory":
        await q.message.reply_text(history_text())
        return

    if data.startswith("unitlog:"):
        code = data.split(":", 1)[1]
        await q.message.reply_text(unit_history_text(code))
        return

    if data.startswith("dispatch:"):
        code = data.split(":", 1)[1]
        context.user_data.clear()
        context.user_data["awaiting"] = "destination"
        context.user_data["unit"] = code

        await q.message.reply_text(
            f"🚨 Despacho de {code}\n\n"
            "Pega el link de Google Maps del destino."
        )
        return

    if data.startswith("unavailable:"):
        code = data.split(":", 1)[1]
        context.user_data.clear()
        context.user_data["awaiting"] = "reason"
        context.user_data["unit"] = code

        await q.message.reply_text(
            f"🟡 {code}\n\n"
            "Escribe el motivo por el que no está disponible."
        )
        return

    if data.startswith("status:"):
        _, code, new_status = data.split(":", 2)

        # Validar siempre contra el estado REAL guardado en SQLite.
        current = get_unit(code)
        if not current:
            await q.message.reply_text("⚠️ Unidad no encontrada.")
            return

        allowed = transitions_for(current).get(current["status"], [])
        if new_status not in allowed:
            # La tarjeta quedó desactualizada. En lugar de dejar el botón muerto,
            # mostramos una tarjeta nueva con el estado real actual.
            await q.message.reply_text(
                "🔄 Esta tarjeta estaba desactualizada. Estado actual:\n\n" + unit_text(current),
                reply_markup=unit_markup(current),
            )
            return

        ok, err = change_status(
            code,
            new_status,
            update.effective_user,
        )

        if not ok:
            current = get_unit(code)
            await q.message.reply_text(
                "⚠️ No se pudo aplicar el cambio. Estado actual:\n\n" + unit_text(current),
                reply_markup=unit_markup(current),
            )
            return

        unit = get_unit(code)

        try:
            await q.edit_message_text(
                unit_text(unit),
                reply_markup=unit_markup(unit),
            )
        except BadRequest:
            # Si Telegram ya no permite editar esa tarjeta, publicar una nueva.
            await q.message.reply_text(
                unit_text(unit),
                reply_markup=unit_markup(unit),
            )
        return


async def text_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    awaiting = context.user_data.get("awaiting")
    code = context.user_data.get("unit")

    if not awaiting or not code:
        return

    text = (update.effective_message.text or "").strip()

    if awaiting == "destination":
        if not valid_maps_url(text):
            await update.effective_message.reply_text(
                "⚠️ Pega un enlace válido de Google Maps."
            )
            return

        ok, err = change_status(
            code,
            "dispatched",
            update.effective_user,
            destination_url=text,
        )

        context.user_data.clear()

        if not ok:
            await update.effective_message.reply_text(f"⚠️ {err}")
            return

        unit = get_unit(code)

        await update.effective_message.reply_text(
            f"✅ {code} despachado.\n\n{unit_text(unit)}",
            reply_markup=unit_markup(unit),
        )
        return

    if awaiting == "reason":
        ok, err = change_status(
            code,
            "unavailable",
            update.effective_user,
            reason=text,
        )

        context.user_data.clear()

        if not ok:
            await update.effective_message.reply_text(f"⚠️ {err}")
            return

        unit = get_unit(code)

        await update.effective_message.reply_text(
            unit_text(unit),
            reply_markup=unit_markup(unit),
        )


def main():
    if not TOKEN:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN")

    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ambulancias", start))
    app.add_handler(CommandHandler("bitacora", bitacora))
    app.add_handler(CommandHandler("borrarbitacora", borrarbitacora))
    app.add_handler(CommandHandler("reiniciar", reiniciar))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_received,
        )
    )

    print("Despacho CRUM Simple Persistente + Reset iniciado")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
