import os
import datetime as dt
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from db import (
    init_db,
    get_assigned_person,
    assign_person,
    list_available_persons,
    list_persons,
    list_active_telegram_user_ids,
    is_admin,
    record_consumption,
    last_7_days_summary,
    month_summary,
    yearly_summary,
    monthly_summary_already_sent,
    mark_monthly_summary_sent,
)

# =========================
# Config
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("Falta BOT_TOKEN en variables de entorno.")

TZ = ZoneInfo(os.getenv("TZ", "Europe/Madrid"))

# =========================
# Teclados
# =========================

def menu_kb(admin: bool) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("➕ Apuntar consumición", callback_data="ADD")],
        [InlineKeyboardButton("📊 Resumen 7 días", callback_data="WEEK")],
        [InlineKeyboardButton("📅 Resumen mes", callback_data="MONTH")],
        [InlineKeyboardButton("🗓️ Resumen año", callback_data="YEAR")],
    ]
    if admin:
        buttons.append([InlineKeyboardButton("👥 Administración", callback_data="ADMIN")])
    return InlineKeyboardMarkup(buttons)

def persons_kb(persons) -> InlineKeyboardMarkup:
    buttons = []
    for p in persons:
        buttons.append([InlineKeyboardButton(p["name"], callback_data=f"WHO:{p['id']}")])
    return InlineKeyboardMarkup(buttons)

def admin_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("📋 Ver personas", callback_data="ADMIN:LIST")],
        [InlineKeyboardButton("⬅️ Volver", callback_data="BACK")],
    ]
    return InlineKeyboardMarkup(buttons)

# =========================
# Estado simple en memoria (por chat)
# =========================

def set_state(context: ContextTypes.DEFAULT_TYPE, state: str, data: dict | None = None):
    context.user_data["state"] = state
    context.user_data["data"] = data or {}

def get_state(context: ContextTypes.DEFAULT_TYPE):
    return context.user_data.get("state", "MENU"), context.user_data.get("data", {})

# =========================
# Lógica de “hitos” (mensajes de logro)
# =========================

def achievement_messages(rows, year_start: int):
    """
    rows: lista de dicts con keys: name, unidades, litros, euros (suelen venir del resumen anual)
    year_start: año que representa el resumen (ej: 2025)
    """
    msgs = []
    for r in rows:
        person_name = r["name"]
        u = int(r["unidades"])
        if u <= 0:
            continue

        # Logros por consumiciones
        milestones = [10, 25, 50, 100, 150, 200, 300, 500, 750, 1000]
        for m in milestones:
            if u == m:
                if m == 1:
                    continue
                msgs.append(f"🏅 {person_name} alcanza {m} consumiciones en {year_start}-{year_start+1}.")
    return msgs

# =========================
# Resumen mensual automático (día 1)
# =========================

async def monthly_summary_job(context: ContextTypes.DEFAULT_TYPE):
    now = dt.datetime.now(TZ)
    if now.day != 1:
        return

    # Resumen del mes anterior
    first_of_this_month = dt.date(now.year, now.month, 1)
    prev_month_last_day = first_of_this_month - dt.timedelta(days=1)
    y, m = prev_month_last_day.year, prev_month_last_day.month

    if monthly_summary_already_sent(y, m):
        return

    # Marca primero (para evitar duplicados si hay reinicios)
    if not mark_monthly_summary_sent(y, m):
        return

    rows = month_summary(y, m)

    total_units = sum(int(r["unidades"]) for r in rows)
    total_liters = sum(float(r["litros"]) for r in rows)
    total_euros = sum(float(r["euros"]) for r in rows)

    month_name = dt.date(y, m, 1).strftime("%B").capitalize()
    lines = [f"📅 Resumen {month_name} {y}", ""]
    lines.append(f"🍺 Total: {total_units} consumiciones")
    lines.append(f"📏 Litros: {total_liters:.2f} L")
    lines.append(f"💸 Gasto: {total_euros:.2f} €")
    lines.append("")
    lines.append("🏆 Top del mes:")

    top = [r for r in rows if int(r["unidades"]) > 0][:3]
    if not top:
        lines.append("• Nadie ha apuntado nada este mes 😇")
    else:
        for i, r in enumerate(top, 1):
            lines.append(
                f"• {i}º {r['name']} — {int(r['unidades'])} uds | {float(r['litros']):.2f} L | {float(r['euros']):.2f} €"
            )

    msg = "\n".join(lines)

    bot = context.bot
    for chat_id in list_active_telegram_user_ids():
        try:
            await bot.send_message(chat_id=chat_id, text=msg)
        except Exception:
            pass

# =========================
# Handlers
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    person = get_assigned_person(tg_id)

    if person:
        await update.message.reply_text(
            f"👋 Hola, {person['name']}.\n\n¿Qué quieres hacer?",
            reply_markup=menu_kb(is_admin(tg_id)),
        )
        set_state(context, "MENU", {})
        return

    available = list_available_persons()
    if not available:
        await update.message.reply_text("🚫 Acceso restringido.\nNo quedan plazas libres en CirrosisBot.")
        return

    await update.message.reply_text(
        "👤 ¿Quién eres?\n\n(Esto solo se hace una vez)",
        reply_markup=persons_kb(available),
    )
    set_state(context, "WHO", {})

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    tg_id = update.effective_user.id

    # Selección inicial de persona
    if data.startswith("WHO:"):
        person_id = int(data.split(":")[1])
        ok = assign_person(tg_id, person_id)
        if not ok:
            await query.edit_message_text("❌ No se ha podido asignar (¿ya está ocupada esa persona?).")
            return
        person = get_assigned_person(tg_id)
        await query.edit_message_text(
            f"✅ Perfecto. Te he asignado como *{person['name']}*.\n\n¿Qué quieres hacer?",
            parse_mode="Markdown",
        )
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="Menú:",
            reply_markup=menu_kb(is_admin(tg_id)),
        )
        set_state(context, "MENU", {})
        return

    # Menú
    if data == "ADD":
        await query.edit_message_text("🍺 Escribe el número de consumiciones a apuntar (ej: 1, 2, 3...)")
        set_state(context, "ADD_UNITS", {})
        return

    if data == "WEEK":
        person = get_assigned_person(tg_id)
        rows = last_7_days_summary()
        lines = ["📊 Resumen últimos 7 días", ""]
        for r in rows:
            lines.append(
                f"• {r['name']}: {int(r['unidades'])} uds | {float(r['litros']):.2f} L | {float(r['euros']):.2f} €"
            )
        await query.edit_message_text("\n".join(lines))
        await context.bot.send_message(chat_id=query.message.chat_id, text="Menú:", reply_markup=menu_kb(is_admin(tg_id)))
        set_state(context, "MENU", {})
        return

    if data == "MONTH":
        now = dt.datetime.now(TZ)
        rows = month_summary(now.year, now.month)
        month_name = dt.date(now.year, now.month, 1).strftime("%B").capitalize()
        lines = [f"📅 Resumen {month_name} {now.year}", ""]
        for r in rows:
            lines.append(
                f"• {r['name']}: {int(r['unidades'])} uds | {float(r['litros']):.2f} L | {float(r['euros']):.2f} €"
            )
        await query.edit_message_text("\n".join(lines))
        await context.bot.send_message(chat_id=query.message.chat_id, text="Menú:", reply_markup=menu_kb(is_admin(tg_id)))
        set_state(context, "MENU", {})
        return

    if data == "YEAR":
        now = dt.datetime.now(TZ)
        rows = yearly_summary(now.year)
        lines = [f"🗓️ Resumen {now.year}", ""]
        for r in rows:
            lines.append(
                f"• {r['name']}: {int(r['unidades'])} uds | {float(r['litros']):.2f} L | {float(r['euros']):.2f} €"
            )
        # Logros
        msgs = achievement_messages(rows, now.year)
        if msgs:
            lines.append("")
            lines.append("🎉 Logros:")
            lines.extend([f"• {m}" for m in msgs])

        await query.edit_message_text("\n".join(lines))
        await context.bot.send_message(chat_id=query.message.chat_id, text="Menú:", reply_markup=menu_kb(is_admin(tg_id)))
        set_state(context, "MENU", {})
        return

    if data == "ADMIN":
        if not is_admin(tg_id):
            await query.edit_message_text("🚫 No eres admin.")
            return
        await query.edit_message_text("👥 Administración", reply_markup=admin_kb())
        set_state(context, "ADMIN", {})
        return

    if data == "ADMIN:LIST":
        if not is_admin(tg_id):
            await query.edit_message_text("🚫 No eres admin.")
            return
        persons = list_persons()
        lines = ["👥 Personas registradas", ""]
        for p in persons:
            who = p.get("telegram_user_id")
            lines.append(f"• {p['name']} — {'asignado' if who else 'libre'}")
        await query.edit_message_text("\n".join(lines), reply_markup=admin_kb())
        return

    if data == "BACK":
        await query.edit_message_text("Menú:", reply_markup=menu_kb(is_admin(tg_id)))
        set_state(context, "MENU", {})
        return

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state, data = get_state(context)
    tg_id = update.effective_user.id
    text = (update.message.text or "").strip()

    if state == "ADD_UNITS":
        try:
            units = int(text)
            if units <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Escribe un número válido (ej: 1, 2, 3...).")
            return

        person = get_assigned_person(tg_id)
        if not person:
            await update.message.reply_text("🚫 No estás asignado a ninguna persona. Usa /start.")
            set_state(context, "MENU", {})
            return

        record_consumption(person["id"], units)
        await update.message.reply_text(f"✅ Apuntado: {units} consumición(es) para {person['name']}.")

        await update.message.reply_text("Menú:", reply_markup=menu_kb(is_admin(tg_id)))
        set_state(context, "MENU", {})
        return

    # Si llega texto fuera de estado, re-mostrar menú
    await update.message.reply_text("Menú:", reply_markup=menu_kb(is_admin(tg_id)))
    set_state(context, "MENU", {})

# =========================
# Main
# =========================

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # JobQueue: comprobar cada día y si es día 1 envía resumen del mes anterior
    # IMPORTANTE: Para que exista JobQueue, instala:
    #   python-telegram-bot[job-queue]==21.6
    if app.job_queue is None:
        print("⚠️ JobQueue no disponible. Instala python-telegram-bot[job-queue] para activar el resumen mensual automático.")
    else:
        app.job_queue.run_daily(
            monthly_summary_job,
            time=dt.time(hour=9, minute=0, tzinfo=TZ),
            name="monthly_summary_daily_check",
        )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling()

if __name__ == "__main__":
    main()


def _public_rankings_lines(year_start: int):
    lines = []
    lines.append("🏆 Rankings públicos")
    lines.append("")

    # 1) Total litros (todos los usuarios con litros > 0)
    total_rows = ranking_total_liters(year_start)
    lines.append("🍺 Ranking total por litros")
    if total_rows:
        for i, r in enumerate(total_rows, 1):
            lines.append(f"{i}. {r['name']} — {_fmt_liters(r['litros'])}")
    else:
        lines.append("• Nadie ha sumado litros este año 😇")
    lines.append("")

    # 2) Bebidas del año (todos los tipos consumidos > 0)
    drink_totals = ranking_drinks_totals(year_start)
    lines.append("🔥 Bebidas del año")
    if drink_totals:
        for i, r in enumerate(drink_totals, 1):
            label = r["label"]
            u = int(r["unidades"])
            unit_vol = r["unit_volume_liters"]
            if unit_vol is None:
                lines.append(f"{i}. {label} — {u} uds")
            else:
                lines.append(f"{i}. {label} — {_fmt_liters(r['litros'])} ({u} uds)")
    else:
        lines.append("• No hay bebidas registradas 😇")

    lines.append("")
    lines.append("🍺 Ranking por tipo de bebida")

    # 3) Ranking por bebida (todos los tipos con consumo > 0) y todos los usuarios con consumo > 0
    rows = ranking_by_drink(year_start)
    by_drink = {}
    meta = {}
    for r in rows:
        did = int(r["drink_type_id"])
        by_drink.setdefault(did, []).append(r)
        meta[did] = (r["category"], r["label"], r["unit_volume_liters"])

    drinks_sorted = sorted(by_drink.keys(), key=lambda did: (meta[did][0], meta[did][1]))

    for did in drinks_sorted:
        cat, label, unit_vol = meta[did]
        entries = by_drink[did]
        if not entries:
            continue
        icon = "🍺" if cat == "BEER" else "🥃"
        lines.append("")
        lines.append(f"{icon} {label}")

        # Si no hay litros (OTHER), ordenar por unidades
        if unit_vol is None:
            entries_sorted = sorted(entries, key=lambda r: (int(r["unidades"]), r["name"]), reverse=True)
            for i, r in enumerate(entries_sorted, 1):
                lines.append(f"{i}. {r['name']} — {int(r['unidades'])} uds")
        else:
            entries_sorted = sorted(entries, key=lambda r: (float(r["litros"] or 0), int(r["unidades"]), r["name"]), reverse=True)
            for i, r in enumerate(entries_sorted, 1):
                lines.append(f"{i}. {r['name']} — {_fmt_liters(r['litros'])} ({int(r['unidades'])} uds)")

    return lines

