import os
import datetime as dt
import random
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
    list_available_persons,
    assign_person,
    list_drink_types,
    insert_event,
    list_last_events,
    void_event,
    list_years_with_data,
    report_year,
    is_admin,
    add_person,
    list_active_persons,
    deactivate_person,
    get_person_year_totals,
    is_first_event_of_year,
    list_active_telegram_user_ids,
    month_summary,
    monthly_summary_already_sent,
    mark_monthly_summary_sent,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
TZ = ZoneInfo(os.getenv("TZ", "Europe/Madrid"))

# Callbacks
CB_PICK_PERSON = "pick_person:"
CB_MENU_ADD = "menu:add"
CB_MENU_REPORT = "menu:report"
CB_MENU_UNDO = "menu:undo"
CB_MENU_ADMIN = "menu:admin"

CB_CAT = "cat:"
CB_TYPE = "type:"
CB_QTY = "qty:"
CB_DATE = "date:"

CB_UNDO_PICK = "undo:"
CB_UNDO_CONFIRM = "undo_yes:"
CB_UNDO_CANCEL = "undo_no"

CB_YEAR = "year:"

# Admin people
CB_ADMIN_ADD_PERSON = "admin:add_person"
CB_ADMIN_DEACTIVATE_PERSON = "admin:deactivate_person"
CB_ADMIN_DEACTIVATE_ID = "admin:deactivate:"

def kb(rows):
    return InlineKeyboardMarkup(rows)

def menu_kb(is_admin_user: bool):
    rows = [
        [InlineKeyboardButton("➕ Añadir", callback_data=CB_MENU_ADD)],
        [InlineKeyboardButton("📊 Informes", callback_data=CB_MENU_REPORT)],
        [InlineKeyboardButton("↩️ Deshacer", callback_data=CB_MENU_UNDO)],
    ]
    if is_admin_user:
        rows.append([InlineKeyboardButton("⚙️ Administración", callback_data=CB_MENU_ADMIN)])
    return kb(rows)

def persons_kb(persons):
    return kb([[InlineKeyboardButton(p["name"], callback_data=f"{CB_PICK_PERSON}{p['id']}")] for p in persons])

def categories_kb():
    return kb([
        [InlineKeyboardButton("🍺 Cerveza", callback_data=f"{CB_CAT}BEER")],
        [InlineKeyboardButton("🥃 Otros", callback_data=f"{CB_CAT}OTHER")],
        [InlineKeyboardButton("⬅️ Menú", callback_data="back:menu")],
    ])

def types_kb(types, back_to="cat"):
    rows = [[InlineKeyboardButton(t["label"], callback_data=f"{CB_TYPE}{t['id']}")] for t in types]
    rows.append([InlineKeyboardButton("⬅️ Atrás", callback_data=f"back:{back_to}")])
    return kb(rows)

def qty_kb():
    return kb([
        [
            InlineKeyboardButton("1", callback_data=f"{CB_QTY}1"),
            InlineKeyboardButton("2", callback_data=f"{CB_QTY}2"),
            InlineKeyboardButton("3", callback_data=f"{CB_QTY}3"),
        ],
        [
            InlineKeyboardButton("4", callback_data=f"{CB_QTY}4"),
            InlineKeyboardButton("5", callback_data=f"{CB_QTY}5"),
            InlineKeyboardButton("Más…", callback_data=f"{CB_QTY}more"),
        ],
        [InlineKeyboardButton("⬅️ Atrás", callback_data="back:type")],
    ])

def date_kb():
    return kb([
        [InlineKeyboardButton("Hoy", callback_data=f"{CB_DATE}today")],
        [InlineKeyboardButton("Ayer", callback_data=f"{CB_DATE}yesterday")],
        [InlineKeyboardButton("Otra fecha", callback_data=f"{CB_DATE}other")],
        [InlineKeyboardButton("⬅️ Atrás", callback_data="back:qty")],
    ])

def undo_list_kb(events):
    rows = []
    for e in events:
        when = e["consumed_at"].strftime("%d/%m/%Y")
        label = f"{e['quantity']} × {e['label']} — {when}"
        rows.append([InlineKeyboardButton(label, callback_data=f"{CB_UNDO_PICK}{e['id']}")])
    rows.append([InlineKeyboardButton("⬅️ Menú", callback_data="back:menu")])
    return kb(rows)

def undo_confirm_kb(event_id: int):
    return kb([
        [InlineKeyboardButton("✅ Sí, eliminar", callback_data=f"{CB_UNDO_CONFIRM}{event_id}")],
        [InlineKeyboardButton("❌ Cancelar", callback_data=CB_UNDO_CANCEL)],
    ])

def years_kb(years):
    rows = [[InlineKeyboardButton(f"{y}-{y+1}", callback_data=f"{CB_YEAR}{y}")] for y in years]
    rows.append([InlineKeyboardButton("⬅️ Menú", callback_data="back:menu")])
    return kb(rows)

def admin_kb():
    return kb([
        [InlineKeyboardButton("➕ Añadir persona", callback_data=CB_ADMIN_ADD_PERSON)],
        [InlineKeyboardButton("➖ Desactivar persona", callback_data=CB_ADMIN_DEACTIVATE_PERSON)],
        [InlineKeyboardButton("⬅️ Menú", callback_data="back:menu")],
    ])

def admin_deactivate_kb(persons):
    rows = [[InlineKeyboardButton(p["name"], callback_data=f"{CB_ADMIN_DEACTIVATE_ID}{p['id']}")] for p in persons]
    rows.append([InlineKeyboardButton("⬅️ Atrás", callback_data=CB_MENU_ADMIN)])
    return kb(rows)

def set_state(context: ContextTypes.DEFAULT_TYPE, state: str, data: dict | None = None):
    context.user_data["state"] = state
    if data is not None:
        context.user_data["data"] = data

def get_state(context: ContextTypes.DEFAULT_TYPE):
    return context.user_data.get("state"), context.user_data.get("data", {})

# --------- Frases ---------
FUN_PHRASES = [
    "🍻 Apuntado. Esto va cogiendo ritmo…",
    "✅ Hecho. La ciencia avanza.",
    "📌 Guardado. La libreta de la vergüenza no perdona.",
    "😄 Apuntado. Nadie te juzga (bueno… un poco).",
    "✅ Listo. CirrosisBot lo ha visto todo.",
]

MILESTONES_UNITS = [1, 50, 100, 200, 500]

def build_achievement_messages(person_name: str, year_start: int, qty_added: int, after_units: int, is_first: bool):
    msgs = []
    if is_first:
        msgs.append(f"🥇 {person_name} inaugura el año cervecero {year_start}-{year_start+1}.")
    before_units = after_units - qty_added
    for m in MILESTONES_UNITS:
        if before_units < m <= after_units:
            if m == 1:
                continue
            msgs.append(f"🏅 {person_name} alcanza {m} consumiciones en {year_start}-{year_start+1}.")
    return msgs

# --------- Resumen mensual automático (día 1) ---------
async def monthly_summary_job(context: ContextTypes.DEFAULT_TYPE):
    now = dt.datetime.now(TZ)
    if now.day != 1:
        return

    first_of_this_month = dt.date(now.year, now.month, 1)
    prev_month_last_day = first_of_this_month - dt.timedelta(days=1)
    y, m = prev_month_last_day.year, prev_month_last_day.month

    if monthly_summary_already_sent(y, m):
        return
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
            lines.append(f"• {i}º {r['name']} — {int(r['unidades'])} uds | {float(r['litros']):.2f} L | {float(r['euros']):.2f} €")

    msg = "\n".join(lines)

    bot = context.bot
    for chat_id in list_active_telegram_user_ids():
        try:
            await bot.send_message(chat_id=chat_id, text=msg)
        except Exception:
            pass

# --------- Handlers ---------
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
    set_state(context, "PICK_PERSON", {})

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tg_id = q.from_user.id
    data = q.data or ""
    state, sdata = get_state(context)

    # ---- BACKS ----
    if data == "back:menu":
        person = get_assigned_person(tg_id)
        await q.edit_message_text(
            f"👋 Hola, {person['name']}.\n\n¿Qué quieres hacer?",
            reply_markup=menu_kb(is_admin(tg_id)),
        )
        set_state(context, "MENU", {})
        return

    if data == "back:cat":
        await q.edit_message_text("¿Qué vas a añadir?", reply_markup=categories_kb())
        set_state(context, "ADD_CAT", {})
        return

    if data == "back:type":
        # volver a lista de tipos (necesita cat)
        cat = sdata.get("cat")
        if not cat:
            await q.edit_message_text("¿Qué vas a añadir?", reply_markup=categories_kb())
            set_state(context, "ADD_CAT", {})
            return
        types = list_drink_types(cat)
        await q.edit_message_text("Elige el tipo:", reply_markup=types_kb(types, back_to="cat"))
        set_state(context, "ADD_TYPE", {"cat": cat})
        return

    if data == "back:qty":
        # ✅ FIX: volver a elegir cantidad (NO volver a fecha)
        await q.edit_message_text("¿Cuántas has tomado?", reply_markup=qty_kb())
        new_data = dict(sdata)
        new_data.pop("qty", None)
        set_state(context, "ADD_QTY", new_data)
        return

    # ---- Registro ----
    if data.startswith(CB_PICK_PERSON):
        person_id = int(data.split(":", 1)[1])
        status, person = assign_person(tg_id, person_id)

        if status in ("OK", "ALREADY"):
            await q.edit_message_text(f"✅ Perfecto. Te has registrado como {person['name']}.")
            await q.message.reply_text(
                f"👋 Hola, {person['name']}.\n\n¿Qué quieres hacer?",
                reply_markup=menu_kb(is_admin(tg_id)),
            )
            set_state(context, "MENU", {})
            return

        available = list_available_persons()
        if not available:
            await q.edit_message_text("🚫 Esa plaza ya no está disponible y no quedan plazas libres.")
        else:
            await q.edit_message_text("⚠️ Esa plaza ya fue ocupada. Elige otra:", reply_markup=persons_kb(available))
        return

    # ---- Menú ----
    if data == CB_MENU_ADD:
        await q.edit_message_text("¿Qué vas a añadir?", reply_markup=categories_kb())
        set_state(context, "ADD_CAT", {})
        return

    if data == CB_MENU_UNDO:
        person = get_assigned_person(tg_id)
        events = list_last_events(person["id"], 3)
        if not events:
            await q.edit_message_text("No tienes entradas recientes para deshacer.", reply_markup=menu_kb(is_admin(tg_id)))
            set_state(context, "MENU", {})
            return
        await q.edit_message_text("Elige cuál quieres eliminar:", reply_markup=undo_list_kb(events))
        set_state(context, "UNDO_PICK", {})
        return

    if data == CB_MENU_REPORT:
        years = list_years_with_data()
        if not years:
            await q.edit_message_text("Aún no hay datos para informes 🙂", reply_markup=menu_kb(is_admin(tg_id)))
            set_state(context, "MENU", {})
            return
        await q.edit_message_text("¿Qué año cervecero quieres ver?", reply_markup=years_kb(years))
        set_state(context, "REPORT_PICK_YEAR", {})
        return

    # ---- Admin main ----
    if data == CB_MENU_ADMIN:
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.", reply_markup=menu_kb(False))
            return
        await q.edit_message_text("⚙️ Administración\n\n¿Qué quieres hacer?", reply_markup=admin_kb())
        set_state(context, "ADMIN", {})
        return

    if data == CB_ADMIN_ADD_PERSON:
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.")
            return
        await q.edit_message_text("Escribe el nombre de la nueva persona:")
        set_state(context, "ADMIN_ADD_PERSON", {})
        return

    if data == CB_ADMIN_DEACTIVATE_PERSON:
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.")
            return
        persons = list_active_persons()
        if not persons:
            await q.edit_message_text("No hay personas activas para desactivar.", reply_markup=admin_kb())
            return
        await q.edit_message_text("¿A quién quieres desactivar?", reply_markup=admin_deactivate_kb(persons))
        set_state(context, "ADMIN_DEACTIVATE", {})
        return

    if data.startswith(CB_ADMIN_DEACTIVATE_ID):
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.")
            return
        pid = int(data.split(":", 2)[2])
        deactivate_person(pid)
        await q.edit_message_text("✅ Persona desactivada.", reply_markup=menu_kb(is_admin(tg_id)))
        set_state(context, "MENU", {})
        return

    # ---- Informe por año ----
    if data.startswith(CB_YEAR):
        y = int(data.split(":", 1)[1])
        rows = report_year(y)

        lines = [f"📊 Informe {y}-{y+1}", ""]
        for r in rows:
            lines.append(
                f"• {r['name']}: {int(r['unidades'])} uds | {float(r['litros']):.2f} L | {float(r['euros']):.2f} €"
            )

        await q.edit_message_text("\n".join(lines), reply_markup=menu_kb(is_admin(tg_id)))
        set_state(context, "MENU", {})
        return

    # ---- Añadir flujo ----
    if data.startswith(CB_CAT):
        cat = data.split(":", 1)[1]
        types = list_drink_types(cat)
        title = "🍺 Elige el tipo de cerveza:" if cat == "BEER" else "🥃 Elige el tipo:"
        await q.edit_message_text(title, reply_markup=types_kb(types, back_to="cat"))
        set_state(context, "ADD_TYPE", {"cat": cat})
        return

    if data.startswith(CB_TYPE):
        drink_type_id = int(data.split(":", 1)[1])
        await q.edit_message_text("¿Cuántas has tomado?", reply_markup=qty_kb())
        set_state(context, "ADD_QTY", {**sdata, "drink_type_id": drink_type_id})
        return

    if data.startswith(CB_QTY):
        v = data.split(":", 1)[1]
        if v == "more":
            await q.edit_message_text("Vale 🙂 Escribe el número (ej: 7):")
            set_state(context, "ADD_QTY_MANUAL", sdata)
            return

        qty = int(v)
        await q.edit_message_text("¿Cuándo se bebió?", reply_markup=date_kb())
        set_state(context, "ADD_DATE", {**sdata, "qty": qty})
        return

    if data.startswith(CB_DATE):
        which = data.split(":", 1)[1]
        if which == "other":
            await q.edit_message_text("Escribe la fecha en formato YYYY-MM-DD (ej: 2026-01-25):")
            set_state(context, "ADD_DATE_MANUAL", sdata)
            return

        today = dt.datetime.now(TZ).date()
        consumed_at = today if which == "today" else (today - dt.timedelta(days=1))

        person = get_assigned_person(tg_id)
        qty = int(sdata["qty"])

        insert_event(
            person_id=person["id"],
            telegram_user_id=tg_id,
            drink_type_id=sdata["drink_type_id"],
            quantity=qty,
            consumed_at=consumed_at,
        )

        when = consumed_at.strftime("%d/%m/%Y")
        base_msg = random.choice(FUN_PHRASES) + f"\n\n✅ Apuntado ({when})."
        await q.edit_message_text(base_msg, reply_markup=menu_kb(is_admin(tg_id)))
        set_state(context, "MENU", {})

        # logros
        year_start = consumed_at.year if consumed_at >= dt.date(consumed_at.year, 1, 7) else (consumed_at.year - 1)
        totals = get_person_year_totals(person["id"], int(year_start))
        after_units = int(totals["unidades"])
        first = is_first_event_of_year(person["id"], int(year_start))
        for msg in build_achievement_messages(person["name"], int(year_start), qty, after_units, first):
            try:
                await context.bot.send_message(chat_id=tg_id, text=msg)
            except Exception:
                pass
        return

    # ---- Deshacer ----
    if data.startswith(CB_UNDO_PICK):
        event_id = int(data.split(":", 1)[1])
        await q.edit_message_text("¿Seguro que quieres eliminar esta entrada?", reply_markup=undo_confirm_kb(event_id))
        set_state(context, "UNDO_CONFIRM", {"event_id": event_id})
        return

    if data.startswith(CB_UNDO_CONFIRM):
        event_id = int(data.split(":", 1)[1])
        person = get_assigned_person(tg_id)
        ok = void_event(person["id"], tg_id, event_id)
        await q.edit_message_text(
            "✅ Entrada eliminada." if ok else "⚠️ No se pudo eliminar.",
            reply_markup=menu_kb(is_admin(tg_id)),
        )
        set_state(context, "MENU", {})
        return

    if data == CB_UNDO_CANCEL:
        person = get_assigned_person(tg_id)
        await q.edit_message_text(
            "Vale, no toco nada 🙂\n\n¿Qué quieres hacer?",
            reply_markup=menu_kb(is_admin(tg_id)),
        )
        set_state(context, "MENU", {})
        return

    # Fallback
    person = get_assigned_person(tg_id)
    if person:
        await q.edit_message_text(
            f"👋 Hola, {person['name']}.\n\n¿Qué quieres hacer?",
            reply_markup=menu_kb(is_admin(tg_id)),
        )
        set_state(context, "MENU", {})
    else:
        await q.edit_message_text("Escribe /start para empezar.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    text = (update.message.text or "").strip()
    state, sdata = get_state(context)
    tg_id = update.effective_user.id

    if state == "ADD_QTY_MANUAL":
        try:
            qty = int(text)
            if qty <= 0:
                raise ValueError()
        except ValueError:
            await update.message.reply_text("Número inválido. Escribe un entero mayor que 0 (ej: 7).")
            return
        await update.message.reply_text("¿Cuándo se bebió?", reply_markup=date_kb())
        set_state(context, "ADD_DATE", {**sdata, "qty": qty})
        return

    if state == "ADD_DATE_MANUAL":
        try:
            consumed_at = dt.date.fromisoformat(text)
        except ValueError:
            await update.message.reply_text("Formato inválido. Usa YYYY-MM-DD (ej: 2026-01-25).")
            return

        person = get_assigned_person(tg_id)
        qty = int(sdata["qty"])

        insert_event(
            person_id=person["id"],
            telegram_user_id=tg_id,
            drink_type_id=sdata["drink_type_id"],
            quantity=qty,
            consumed_at=consumed_at,
        )

        when = consumed_at.strftime("%d/%m/%Y")
        await update.message.reply_text(
            random.choice(FUN_PHRASES) + f"\n\n✅ Apuntado ({when}).",
            reply_markup=menu_kb(is_admin(tg_id)),
        )
        set_state(context, "MENU", {})

        year_start = consumed_at.year if consumed_at >= dt.date(consumed_at.year, 1, 7) else (consumed_at.year - 1)
        totals = get_person_year_totals(person["id"], int(year_start))
        after_units = int(totals["unidades"])
        first = is_first_event_of_year(person["id"], int(year_start))
        for msg in build_achievement_messages(person["name"], int(year_start), qty, after_units, first):
            try:
                await context.bot.send_message(chat_id=tg_id, text=msg)
            except Exception:
                pass
        return

    # Admin: añadir persona por texto
    if state == "ADMIN_ADD_PERSON":
        if not is_admin(tg_id):
            await update.message.reply_text("🚫 No tienes permisos.")
            set_state(context, "MENU", {})
            return
        ok = add_person(text)
        await update.message.reply_text(
            f"✅ '{text}' añadido como nueva persona." if ok else "ℹ️ No se añadió (¿ya existe?).",
            reply_markup=menu_kb(is_admin(tg_id)),
        )
        set_state(context, "MENU", {})
        return

    await update.message.reply_text("Escribe /start para ver el menú.")

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # JobQueue: comprobación diaria y si es día 1 envía resumen del mes anterior
    if app.job_queue is not None:
        app.job_queue.run_daily(
            monthly_summary_job,
            time=dt.time(hour=9, minute=0, tzinfo=TZ),
            name="monthly_summary_daily_check",
        )
    else:
        print('⚠️ JobQueue no disponible. Instala python-telegram-bot[job-queue].')

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
