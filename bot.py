import os
from datetime import datetime, timezone
from urllib.parse import urlparse

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, select, delete
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
SUPERADMIN_IDS_RAW = os.getenv("SUPERADMIN_IDS", "").strip()

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

if not DATABASE_URL:
    raise RuntimeError("Falta DATABASE_URL.")

SUPERADMIN_IDS = {int(x.strip()) for x in SUPERADMIN_IDS_RAW.split(",") if x.strip().isdigit()}

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()

AMBULANCES = [str(n) for n in range(674, 696)]
VECTORS = [f"V{n:02d}" for n in range(1, 21)]

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

ROLE_LABELS = {
    "superadmin": "👑 Superadministrador",
    "admin": "🛠 Administrador",
    "operator": "👁 Operativo",
}

CREW_ROLES = ["Operador", "TUM", "Médico", "Otro"]


class Shift(Base):
    __tablename__ = "shifts"
    id = Column(Integer, primary_key=True)
    shift_date = Column(String(10))
    shift_type = Column(String(30))
    source_filename = Column(String(255))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_by = Column(Integer)


class Unit(Base):
    __tablename__ = "units"
    id = Column(Integer, primary_key=True)
    unit_code = Column(String(10), unique=True, nullable=False)
    unit_type = Column(String(20), nullable=False)
    active_in_shift = Column(Integer, nullable=False, default=0)
    status = Column(String(40), nullable=False, default="available")
    unavailable_reason = Column(Text)
    destination_url = Column(Text)
    r7 = Column(String(100))
    schedule = Column(String(100))
    current_shift_id = Column(Integer, ForeignKey("shifts.id"))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_by = Column(Integer)


class CrewMember(Base):
    __tablename__ = "crew_members"
    id = Column(Integer, primary_key=True)
    unit_code = Column(String(10), nullable=False, index=True)
    person_name = Column(String(255), nullable=False)
    role = Column(String(30), nullable=False)
    shift_id = Column(Integer, ForeignKey("shifts.id"), nullable=True)
    active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class LogEntry(Base):
    __tablename__ = "log_entries"
    id = Column(Integer, primary_key=True)
    unit_code = Column(String(10), nullable=False, index=True)
    event_type = Column(String(40), nullable=False, default="status")
    old_status = Column(String(40))
    new_status = Column(String(40))
    description = Column(Text)
    reason = Column(Text)
    destination_url = Column(Text)
    telegram_user_id = Column(Integer)
    telegram_username = Column(String(255))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


class AuthorizedUser(Base):
    __tablename__ = "authorized_users"
    id = Column(Integer, primary_key=True)
    telegram_user_id = Column(Integer, unique=True, nullable=False, index=True)
    display_name = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_by = Column(Integer)


Base.metadata.create_all(engine)


def init_units():
    with SessionLocal() as s:
        existing = {u.unit_code for u in s.scalars(select(Unit)).all()}
        for code in AMBULANCES:
            if code not in existing:
                s.add(Unit(unit_code=code, unit_type="ambulance", active_in_shift=0, status="available"))
        for code in VECTORS:
            if code not in existing:
                s.add(Unit(unit_code=code, unit_type="vector", active_in_shift=0, status="available"))
        s.commit()


def bootstrap_superadmins():
    with SessionLocal() as s:
        for uid in SUPERADMIN_IDS:
            row = s.scalar(select(AuthorizedUser).where(AuthorizedUser.telegram_user_id == uid))
            if row:
                row.role = "superadmin"
            else:
                s.add(AuthorizedUser(telegram_user_id=uid, display_name=f"Superadmin {uid}", role="superadmin", created_by=uid))
        s.commit()


def role_for_user(user_id):
    if user_id in SUPERADMIN_IDS:
        return "superadmin"
    with SessionLocal() as s:
        row = s.scalar(select(AuthorizedUser).where(AuthorizedUser.telegram_user_id == user_id))
        return row.role if row else None


def can_view(uid):
    return role_for_user(uid) in {"superadmin", "admin", "operator"}


def can_operate(uid):
    return role_for_user(uid) in {"superadmin", "admin"}


def get_transitions(unit):
    return AMBULANCE_TRANSITIONS if unit.unit_type == "ambulance" else VECTOR_TRANSITIONS


def get_unit(code):
    with SessionLocal() as s:
        return s.scalar(select(Unit).where(Unit.unit_code == code))


def get_active_units():
    with SessionLocal() as s:
        return list(s.scalars(select(Unit).where(Unit.active_in_shift == 1).order_by(Unit.unit_type, Unit.unit_code)).all())


def get_crew(code):
    with SessionLocal() as s:
        return list(s.scalars(select(CrewMember).where(CrewMember.unit_code == code, CrewMember.active == 1).order_by(CrewMember.id)).all())


def crew_valid_for_dispatch(code):
    crew = get_crew(code)
    if len(crew) > 4:
        return False, "La unidad tiene más de 4 tripulantes."
    operators = [c for c in crew if c.role == "Operador"]
    if len(operators) != 1:
        return False, "Debe existir exactamente 1 Operador."
    return True, None


def is_maps_url(text):
    try:
        p = urlparse(text.strip())
        host = (p.hostname or "").lower()
        return p.scheme in ("http", "https") and (
            host in {"maps.app.goo.gl","goo.gl","google.com","www.google.com","maps.google.com"} or host.endswith(".google.com")
        )
    except Exception:
        return False


def change_status(code, new_status, user, reason=None, destination_url=None):
    with SessionLocal() as s:
        unit = s.scalar(select(Unit).where(Unit.unit_code == code))
        if not unit:
            return False, "Unidad no encontrada."
        if unit.active_in_shift != 1:
            return False, "La unidad no está activa en el turno."
        if new_status not in get_transitions(unit).get(unit.status, []):
            return False, "Transición no permitida para este tipo de unidad."

        if new_status == "dispatched":
            ok, err = crew_valid_for_dispatch(code)
            if not ok:
                return False, err

        old_status = unit.status
        old_dest = unit.destination_url
        unit.status = new_status
        unit.updated_at = datetime.now(timezone.utc)
        unit.updated_by = user.id

        if new_status == "unavailable":
            unit.unavailable_reason = reason
        elif new_status == "available":
            unit.unavailable_reason = None

        if destination_url:
            unit.destination_url = destination_url
        if new_status in ("available", "unavailable"):
            unit.destination_url = None

        s.add(LogEntry(
            unit_code=code,
            event_type="status",
            old_status=old_status,
            new_status=new_status,
            reason=reason,
            destination_url=destination_url or old_dest,
            telegram_user_id=user.id,
            telegram_username=user.username,
        ))
        s.commit()
        return True, None


def board_text():
    units = get_active_units()
    amb_count = sum(1 for u in units if u.unit_type == "ambulance")
    vec_count = sum(1 for u in units if u.unit_type == "vector")
    counts = {k: 0 for k in STATUS}
    for u in units:
        counts[u.status] += 1

    return (
        "🚑 DESPACHO CRUM\n\n"
        f"🚑 Ambulancias activas: {amb_count}\n"
        f"🏍️ Vectores activos: {vec_count}\n\n"
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
    units = get_active_units()
    ambs = [u for u in units if u.unit_type == "ambulance"]
    vecs = [u for u in units if u.unit_type == "vector"]
    rows = []

    if ambs:
        rows.append([InlineKeyboardButton("🚑 AMBULANCIAS", callback_data="noop")])
        row = []
        for u in ambs:
            row.append(InlineKeyboardButton(f"{STATUS[u.status][0]} {u.unit_code}", callback_data=f"unit:{u.unit_code}"))
            if len(row) == 3:
                rows.append(row); row = []
        if row:
            rows.append(row)

    if vecs:
        rows.append([InlineKeyboardButton("🏍️ VECTORES", callback_data="noop")])
        row = []
        for u in vecs:
            row.append(InlineKeyboardButton(f"{STATUS[u.status][0]} {u.unit_code}", callback_data=f"unit:{u.unit_code}"))
            if len(row) == 3:
                rows.append(row); row = []
        if row:
            rows.append(row)

    if not units:
        rows.append([InlineKeyboardButton("Sin unidades activas", callback_data="noop")])

    rows.append([InlineKeyboardButton("🔄 Actualizar", callback_data="board")])
    return InlineKeyboardMarkup(rows)


def unit_text(u):
    crew = get_crew(u.unit_code)
    icon = "🚑" if u.unit_type == "ambulance" else "🏍️"
    label = "Ambulancia" if u.unit_type == "ambulance" else "Vector"
    lines = [
        f"{icon} {label} {u.unit_code}",
        "",
        f"Estado: {STATUS[u.status][0]} {STATUS[u.status][1]}",
        f"🏠 R7: {u.r7 or 'Sin asignar'}",
        f"🕐 Horario: {u.schedule or 'Sin cargar'}",
        "",
        "👥 Tripulación:"
    ]
    if crew:
        lines += [f"• {c.role}: {c.person_name}" for c in crew]
    else:
        lines.append("• Sin cargar")
    if u.unavailable_reason:
        lines += ["", f"📝 Motivo: {u.unavailable_reason}"]
    if u.destination_url:
        lines += ["", "📍 Destino activo:", u.destination_url]
    return "\n".join(lines)


def unit_markup(u, allow_ops):
    rows = []
    code = u.unit_code

    if allow_ops:
        for st in get_transitions(u).get(u.status, []):
            if st == "dispatched":
                rows.append([InlineKeyboardButton("🚨 Despachar", callback_data=f"dispatch:{code}")])
            elif st == "unavailable":
                rows.append([InlineKeyboardButton("🟡 No disponible", callback_data=f"unavailable:{code}")])
            else:
                rows.append([InlineKeyboardButton(f"{STATUS[st][0]} {STATUS[st][1]}", callback_data=f"status:{code}:{st}")])

    if u.destination_url:
        rows.append([InlineKeyboardButton("🗺 Abrir destino", url=u.destination_url)])

    if allow_ops:
        rows.append([InlineKeyboardButton("🏠 Editar R7", callback_data=f"r7:{code}")])
        rows.append([InlineKeyboardButton("👥 Editar tripulación", callback_data=f"crew:{code}")])

    rows.append([InlineKeyboardButton("📜 Ver bitácora", callback_data=f"log:{code}")])
    rows.append([InlineKeyboardButton("⬅️ Tablero", callback_data="board")])
    return InlineKeyboardMarkup(rows)


async def deny(update, msg="⛔ No tienes permisos para realizar esta acción."):
    if update.callback_query:
        await update.callback_query.answer(msg, show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text(msg)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not can_view(update.effective_user.id):
        return await deny(update, "⛔ No estás autorizado. Usa /miid y solicita acceso.")
    context.user_data.clear()
    await update.effective_message.reply_text(board_text(), reply_markup=board_markup())


async def miid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    role = role_for_user(uid)
    await update.effective_message.reply_text(f"Tu Telegram User ID es:\n{uid}\n\nRol: {ROLE_LABELS.get(role, 'Sin autorización')}")


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = update.effective_user

    if not can_view(user.id):
        return await deny(update)

    await q.answer()
    data = q.data
    allow_ops = can_operate(user.id)

    if data == "noop":
        return

    if data == "board":
        context.user_data.clear()
        await q.edit_message_text(board_text(), reply_markup=board_markup())
        return

    if data.startswith("unit:"):
        code = data.split(":",1)[1]
        u = get_unit(code)
        if not u or u.active_in_shift != 1:
            return await q.answer("Unidad no activa en este turno.", show_alert=True)
        await q.edit_message_text(unit_text(u), reply_markup=unit_markup(u, allow_ops))
        return

    if not allow_ops:
        return await deny(update)

    if data.startswith("dispatch:"):
        code = data.split(":",1)[1]
        context.user_data.clear()
        context.user_data.update({"awaiting":"destination","unit":code})
        await q.edit_message_text(f"🚨 Despacho de unidad {code}\n\nPega el enlace de Google Maps del servicio.")
        return

    if data.startswith("unavailable:"):
        code = data.split(":",1)[1]
        context.user_data.clear()
        context.user_data.update({"awaiting":"unavailable_reason","unit":code})
        await q.edit_message_text(f"🟡 Unidad {code}\n\nEscribe el motivo de no disponibilidad.")
        return

    if data.startswith("status:"):
        _, code, new_status = data.split(":",2)
        ok, err = change_status(code, new_status, user)
        if not ok:
            return await q.answer(err, show_alert=True)
        u = get_unit(code)
        await q.edit_message_text(unit_text(u), reply_markup=unit_markup(u, True))


async def text_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not can_operate(user.id):
        return

    awaiting = context.user_data.get("awaiting")
    if not awaiting:
        return

    text = (update.effective_message.text or "").strip()

    if awaiting == "destination":
        code = context.user_data["unit"]
        if not is_maps_url(text):
            return await update.effective_message.reply_text("⚠️ Pega un enlace válido de Google Maps.")
        ok, err = change_status(code, "dispatched", user, destination_url=text)
        context.user_data.clear()
        if not ok:
            return await update.effective_message.reply_text(f"⚠️ {err}")
        u = get_unit(code)
        await update.effective_message.reply_text(f"✅ Unidad {code} despachada.\n\n📍 {text}", reply_markup=unit_markup(u, True))
        return

    if awaiting == "unavailable_reason":
        code = context.user_data["unit"]
        ok, err = change_status(code, "unavailable", user, reason=text)
        context.user_data.clear()
        if not ok:
            return await update.effective_message.reply_text(f"⚠️ {err}")
        u = get_unit(code)
        await update.effective_message.reply_text(unit_text(u), reply_markup=unit_markup(u, True))


def main():
    if not TOKEN:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN")
    init_units()
    bootstrap_superadmins()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ambulancias", start))
    app.add_handler(CommandHandler("miid", miid))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_received))

    print("Despacho CRUM V8 iniciado")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
