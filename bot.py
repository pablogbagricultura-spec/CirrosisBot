import os
import datetime as dt
import random
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

from db import (
    init_db, get_assigned_person, list_available_persons, assign_person,
    list_drink_types, insert_event, list_last_events, void_event,
    list_years_with_data, report_year,
    is_admin, add_person, list_active_persons, deactivate_person,
    list_persons_admin, get_person_profile, suspend_person, reactivate_person,
    reset_person_to_new, soft_delete_person,
    get_person_year_totals, is_first_event_of_year,
    list_active_telegram_user_ids,
    month_summary, monthly_summary_already_sent, mark_monthly_summary_sent,
    monthly_shame_report,
    person_year_breakdown,
    year_drinks_totals,
    year_drink_type_person_totals,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
TZ = ZoneInfo("Europe/Madrid")

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

CB_YEAR = "year:"  # year:<year_start>

CB_ADMIN_ADD = "admin:add"
CB_ADMIN_REMOVE = "admin:remove"
CB_ADMIN_REMOVE_ID = "admin:remove:"

CB_ADMIN_PEOPLE = "admin:people"
CB_PEOPLE_TAB = "people:tab:"  # people:tab:ACTIVE|INACTIVE|NEW|DELETED
CB_PEOPLE_VIEW = "people:view:"  # people:view:<person_id>
CB_PEOPLE_SUSPEND = "people:suspend:"
CB_PEOPLE_REACTIVATE = "people:react:"
CB_PEOPLE_RESET = "people:reset:"
CB_PEOPLE_DELETE = "people:delete:"
CB_PEOPLE_DELETE_YES = "people:delete_yes:"
CB_PEOPLE_DELETE_NO = "people:delete_no"

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
        [InlineKeyboardButton("1", callback_data=f"{CB_QTY}1"),
         InlineKeyboardButton("2", callback_data=f"{CB_QTY}2"),
         InlineKeyboardButton("3", callback_data=f"{CB_QTY}3")],
        [InlineKeyboardButton("4", callback_data=f"{CB_QTY}4"),
         InlineKeyboardButton("5", callback_data=f"{CB_QTY}5"),
         InlineKeyboardButton("Más…", callback_data=f"{CB_QTY}more")],
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
        [InlineKeyboardButton("➕ Añadir persona", callback_data=CB_ADMIN_ADD)],
        [InlineKeyboardButton("👥 Gestionar personas", callback_data=CB_ADMIN_PEOPLE)],
        [InlineKeyboardButton("⬅️ Menú", callback_data="back:menu")],
    ])


def people_tabs_kb():
    return kb([
        [InlineKeyboardButton("✅ Activas", callback_data=f"{CB_PEOPLE_TAB}ACTIVE")],
        [InlineKeyboardButton("⛔ Suspendidas", callback_data=f"{CB_PEOPLE_TAB}INACTIVE")],
        [InlineKeyboardButton("🆕 Nuevas", callback_data=f"{CB_PEOPLE_TAB}NEW")],
        [InlineKeyboardButton("🗑️ Eliminadas", callback_data=f"{CB_PEOPLE_TAB}DELETED")],
        [InlineKeyboardButton("⬅️ Atrás", callback_data=CB_MENU_ADMIN)],
    ])

def people_list_kb(persons, back_cb=CB_ADMIN_PEOPLE):
    rows = [[InlineKeyboardButton(f"{p['name']}", callback_data=f"{CB_PEOPLE_VIEW}{p['id']}")] for p in persons]
    rows.append([InlineKeyboardButton("⬅️ Atrás", callback_data=back_cb)])
    return kb(rows)

def person_actions_kb(profile):
    pid = profile["id"]
    rows = []
    if not profile.get("is_deleted"):
        if profile.get("status") == "ACTIVE":
            rows.append([InlineKeyboardButton("⛔ Suspender", callback_data=f"{CB_PEOPLE_SUSPEND}{pid}")])
        elif profile.get("status") == "INACTIVE":
            rows.append([InlineKeyboardButton("✅ Reactivar", callback_data=f"{CB_PEOPLE_REACTIVATE}{pid}")])

        rows.append([InlineKeyboardButton("♻️ Reset a NEW (liberar TG)", callback_data=f"{CB_PEOPLE_RESET}{pid}")])
        rows.append([InlineKeyboardButton("🗑️ Eliminar (soft)", callback_data=f"{CB_PEOPLE_DELETE}{pid}")])
    rows.append([InlineKeyboardButton("⬅️ Atrás", callback_data=f"{CB_PEOPLE_TAB}{profile.get('status','ACTIVE')}")])
    return kb(rows)

def confirm_delete_kb(pid: int):
    return kb([
        [InlineKeyboardButton("💀 Sí, eliminar", callback_data=f"{CB_PEOPLE_DELETE_YES}{pid}")],
        [InlineKeyboardButton("❌ Cancelar", callback_data=CB_PEOPLE_DELETE_NO)],
    ])

def admin_remove_kb(persons):
    rows = [[InlineKeyboardButton(p["name"], callback_data=f"{CB_ADMIN_REMOVE_ID}{p['id']}")] for p in persons]
    rows.append([InlineKeyboardButton("⬅️ Atrás", callback_data=CB_MENU_ADMIN)])
    return kb(rows)

def set_state(context: ContextTypes.DEFAULT_TYPE, state: str, data: dict | None = None):
    context.user_data["state"] = state
    if data is not None:
        context.user_data["data"] = data

def get_state(context: ContextTypes.DEFAULT_TYPE):
    return context.user_data.get("state"), context.user_data.get("data", {})

# --------- Logros / frases ---------

MILESTONES_UNITS = [1, 50, 100, 200, 500]

FUN_PHRASES = [
    "🍻 Apuntado. Esto va cogiendo ritmo…",
    "✅ Hecho. La ciencia avanza.",
    "📌 Guardado. La libreta de la vergüenza no perdona.",
    "😄 Apuntado. Nadie te juzga (bueno… un poco).",
    "✅ Listo. CirrosisBot lo ha visto todo.",
]

def build_achievement_messages(person_name: str, year_start: int, qty_added: int, after_units: int, is_first: bool):
    msgs = []
    if is_first:
        msgs.append(f"🥇 {person_name} inaugura el año cervecero {year_start}-{year_start+1}.")

    before_units = after_units - qty_added
    for m in MILESTONES_UNITS:
        if before_units < m <= after_units:
            if m == 1:
                continue  # ya lo cubre el "primera del año"
            msgs.append(f"🏅 {person_name} alcanza {m} consumiciones en {year_start}-{year_start+1}.")
    return msgs

# --------- Resumen mensual automático (día 1) ---------

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

    # Top 3 por euros (ya viene ordenado)
    top = [r for r in rows if int(r["unidades"]) > 0][:3]
    if not top:
        lines.append("• Nadie ha apuntado nada este mes 😇")
    else:
        for i, r in enumerate(top, 1):
            lines.append(
                f"• {i}º {r['name']} — {int(r['unidades'])} uds | {float(r['litros']):.2f} L | {float(r['euros']):.2f} €"
            )

    msg = "\n".join(lines)

    # Enviar a todos los usuarios activos
    bot = context.bot
    for chat_id in list_active_telegram_user_ids():
        try:
            await bot.send_message(chat_id=chat_id, text=msg)
        except Exception:
            pass

    # --- Estadísticas vergonzosas (mensaje aparte, público) ---
    # (IMPORTANTE: esto va DENTRO del async def)
    try:
        shame = monthly_shame_report(y, m)
    except Exception:
        shame = None

    # Regla: mínimo 2 personas con consumo en el mes

    active_people = sum(1 for r in rows if int(r["unidades"]) > 0)

    if shame and active_people >= 2:
        month_name2 = dt.date(y, m, 1).strftime("%B").capitalize()
        lines2 = [f"🤡 Estadísticas vergonzosas — {month_name2} {y}", ""]

        fl = shame.get("false_leader")
        if fl:
            d = fl.get("first_day")
            d_txt = d.strftime("%d/%m") if d else ""
            lines2.append("🪦 Falso líder del mes")
            lines2.append(f"• {fl['name']} lideró ({d_txt}) y acabó {fl['final_rank']}º.")
            lines2.append("")

        bd = shame.get("biggest_drop")
        if bd and bd.get("drop", 0) > 0:
            lines2.append("📉 Mayor caída del mes")
            lines2.append(f"• {bd['name']} pasó de {bd['best_rank']}º a {bd['final_rank']}º.")
            lines2.append("")

        ac = shame.get("almost_champion")
        if ac and ac.get("times", 0) > 0:
            lines2.append("🫠 El casi campeón")
            lines2.append(f"• {ac['name']} se quedó a < 0,5 L del liderato {ac['times']} veces.")
            lines2.append("")

        gh = shame.get("ghost")
        if gh:
            lines2.append("😴 Fantasma del mes")
            lines2.append(f"• {gh['name']} desapareció {gh['blank_days']} de {gh['days']} días.")
            lines2.append("")

        sw = shame.get("saddest_week")
        if sw:
            ws = sw["week_start"]
            we = ws + dt.timedelta(days=6)
            lines2.append("🧊 Semana más triste")
            lines2.append(f"• {ws.strftime('%d/%m')}–{we.strftime('%d/%m')}: {sw['liters']:.2f} L.")
            lines2.append("")

        if len(lines2) > 2:
            msg2 = "\n".join(lines2).rstrip()
            for chat_id in list_active_telegram_user_ids():
                try:
                    await bot.send_message(chat_id=chat_id, text=msg2)
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

    # -------- BACKS --------
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
        # Volver desde FECHA -> CANTIDAD
        await q.edit_message_text("¿Cuántas has tomado?", reply_markup=qty_kb())

        # Copia de seguridad del estado para no tocar el original
        sdata2 = dict(sdata)

        # Si había una cantidad previa, la borramos para forzar a elegir otra
        sdata2.pop("qty", None)

        # Volvemos al paso de cantidad
        set_state(context, "ADD_QTY", sdata2)
        return



    # -------- REGISTRO PERSONA --------
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

    # -------- MENÚ --------
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

    # -------- ADMIN --------
    if data == CB_MENU_ADMIN:
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.", reply_markup=menu_kb(False))
            return
        await q.edit_message_text("⚙️ Administración\n\n¿Qué quieres hacer?", reply_markup=admin_kb())
        set_state(context, "ADMIN", {})
        return

    if data == CB_ADMIN_ADD:
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.")
            return
        await q.edit_message_text("Escribe el nombre de la nueva persona:")
        set_state(context, "ADMIN_ADD", {})
        return

    if data == CB_ADMIN_REMOVE:
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.")
            return
        persons = list_active_persons()
        if not persons:
            await q.edit_message_text("No hay personas activas para desactivar.", reply_markup=admin_kb())
            return
        await q.edit_message_text("¿A quién quieres desactivar?", reply_markup=admin_remove_kb(persons))
        set_state(context, "ADMIN_REMOVE", {})

if data == CB_ADMIN_PEOPLE:
    if not is_admin(tg_id):
        await q.edit_message_text("🚫 No tienes permisos.")
        return
    await q.edit_message_text("👥 Gestión de personas\n\nElige un listado:", reply_markup=people_tabs_kb())
    set_state(context, "ADMIN_PEOPLE", {})
    return

if data.startswith(CB_PEOPLE_TAB):
    if not is_admin(tg_id):
        await q.edit_message_text("🚫 No tienes permisos.")
        return
    tab = data.split(":", 2)[2]

    if tab == "DELETED":
        persons = list_persons_admin(status=None, is_deleted=True)
    else:
        persons = list_persons_admin(status=tab, is_deleted=False)

    title_map = {
        "ACTIVE": "✅ Activas",
        "INACTIVE": "⛔ Suspendidas",
        "NEW": "🆕 Nuevas (sin asignar)",
        "DELETED": "🗑️ Eliminadas",
    }
    title = title_map.get(tab, "Personas")
    if not persons:
        await q.edit_message_text(f"{title}\n\n(No hay personas en este listado)", reply_markup=people_tabs_kb())
        return
    await q.edit_message_text(f"{title}\n\nElige una persona:", reply_markup=people_list_kb(persons, back_cb=CB_ADMIN_PEOPLE))
    set_state(context, "ADMIN_PEOPLE_LIST", {"tab": tab})
    return

if data.startswith(CB_PEOPLE_VIEW):
    if not is_admin(tg_id):
        await q.edit_message_text("🚫 No tienes permisos.")
        return
    pid = int(data.split(":", 2)[2])
    prof = get_person_profile(pid)
    if not prof:
        await q.edit_message_text("No encontrada.", reply_markup=people_tabs_kb())
        return

    lines = [f"👤 {prof['name']} (id={prof['id']})"]
    lines.append(f"• status: {prof['status']}")
    lines.append(f"• deleted: {'YES' if prof['is_deleted'] else 'NO'}")
    if prof.get("active_telegram_user_id"):
        lines.append(f"• TG activo: {prof['active_telegram_user_id']}")
    else:
        lines.append("• TG activo: —")
    lines.append(f"• eventos: {prof.get('event_count', 0)}")
    if prof.get("deleted_at"):
        lines.append(f"• deleted_at: {prof['deleted_at']}")
    await q.edit_message_text("\n".join(lines), reply_markup=person_actions_kb(prof))
    set_state(context, "ADMIN_PERSON", {"person_id": pid})
    return

if data.startswith(CB_PEOPLE_SUSPEND):
    if not is_admin(tg_id):
        await q.edit_message_text("🚫 No tienes permisos.")
        return
    pid = int(data.split(":", 2)[2])
    ok = suspend_person(pid, tg_id)
    await q.edit_message_text("✅ Suspendido (y Telegram liberado)." if ok else "⚠️ No se pudo suspender.", reply_markup=people_tabs_kb())
    return

if data.startswith(CB_PEOPLE_REACTIVATE):
    if not is_admin(tg_id):
        await q.edit_message_text("🚫 No tienes permisos.")
        return
    pid = int(data.split(":", 2)[2])
    ok = reactivate_person(pid)
    await q.edit_message_text("✅ Reactivado." if ok else "⚠️ No se pudo reactivar.", reply_markup=people_tabs_kb())
    return

if data.startswith(CB_PEOPLE_RESET):
    if not is_admin(tg_id):
        await q.edit_message_text("🚫 No tienes permisos.")
        return
    pid = int(data.split(":", 2)[2])
    ok, msg = reset_person_to_new(pid, tg_id)
    await q.edit_message_text("✅ Reseteado a NEW y Telegram liberado." if ok else f"⚠️ No se pudo: {msg}", reply_markup=people_tabs_kb())
    return

if data.startswith(CB_PEOPLE_DELETE):
    if not is_admin(tg_id):
        await q.edit_message_text("🚫 No tienes permisos.")
        return
    pid = int(data.split(":", 2)[2])
    await q.edit_message_text(
        "⚠️ Esto marca a la persona como ELIMINADA (soft delete).\nNo podrá volver a asignarse y desaparecerá de informes.\n\n¿Confirmas?",
        reply_markup=confirm_delete_kb(pid),
    )
    return

if data.startswith(CB_PEOPLE_DELETE_YES):
    if not is_admin(tg_id):
        await q.edit_message_text("🚫 No tienes permisos.")
        return
    pid = int(data.split(":", 2)[2])
    ok = soft_delete_person(pid, tg_id)
    await q.edit_message_text("💀 Eliminada (soft delete)." if ok else "⚠️ No se pudo eliminar.", reply_markup=people_tabs_kb())
    return

if data == CB_PEOPLE_DELETE_NO:
    if not is_admin(tg_id):
        await q.edit_message_text("🚫 No tienes permisos.")
        return
    await q.edit_message_text("Vale, no toco nada 🙂", reply_markup=people_tabs_kb())
    return

        return

    if data.startswith(CB_ADMIN_REMOVE_ID):
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.")
            return
        pid = int(data.split(":")[2])
        deactivate_person(pid)
        await q.edit_message_text("✅ Persona desactivada.", reply_markup=menu_kb(True))
        set_state(context, "MENU", {})
        return

    # -------- INFORME POR AÑO + RANKINGS --------
    if data.startswith(CB_YEAR):
        y = int(data.split(":", 1)[1])

        person = get_assigned_person(tg_id)
        if not person:
            await q.edit_message_text("🚫 No estás registrado. Usa /start.")
            return

        # -------- Helpers de formato --------
        def fmt_units(n): 
            return f"{int(n)} uds"

        def fmt_liters(x): 
            return f"{float(x):.2f} L"

        def fmt_eur(x): 
            return f"{float(x):.2f} €"

        # -------- Datos --------
        personal_rows = person_year_breakdown(person["id"], y)
        year_rows = report_year(y)
        drinks_year = year_drinks_totals(y)
        per_type_people = year_drink_type_person_totals(y)

        # -------- Construcción mensaje --------
        lines = [f"📊 Informe {y}-{y+1}", "", "👤 Tu informe personal (solo tú)", person["name"], ""]

        beers = [r for r in personal_rows if r["category"] == "BEER"]
        others = [r for r in personal_rows if r["category"] == "OTHER"]

        def sum_block(rows):
            total_u = sum(int(r["unidades"]) for r in rows)
            total_l = sum(float(r["litros"]) for r in rows)
            total_e = sum(float(r["euros"]) for r in rows)
            return total_u, total_l, total_e

        if beers:
            lines.append("🍺 Cervezas")
            for r in beers:
                lines.append(f"• {r['label']} — {fmt_units(r['unidades'])} · {fmt_liters(r['litros'])} · {fmt_eur(r['euros'])}")
            bu, bl, be = sum_block(beers)
            lines.append(f"Total cerveza: {fmt_units(bu)} · {fmt_liters(bl)} · {fmt_eur(be)}")
            lines.append("")

        if others:
            lines.append("🥃 Otros")
            for r in others:
                # si NO quieres euros aquí, quita "· {fmt_eur...}"
                lines.append(f"• {r['label']} — {fmt_units(r['unidades'])} · {fmt_eur(r['euros'])}")
            ou = sum(int(r["unidades"]) for r in others)
            oe = sum(float(r["euros"]) for r in others)
            lines.append(f"Total otros: {fmt_units(ou)} · {fmt_eur(oe)}")
            lines.append("")

        tu = sum(int(r["unidades"]) for r in personal_rows)
        te = sum(float(r["euros"]) for r in personal_rows)
        lines.append(f"💸 Total general: {tu} consumiciones · {fmt_eur(te)}")
        lines.append("")
        lines.append("🏆 Rankings públicos")
        lines.append("")

        ranked_liters = sorted(
            [r for r in year_rows if float(r["litros"]) > 0],
            key=lambda r: float(r["litros"]),
            reverse=True
        )
        lines.append("🍺 Ranking total por litros")
        if not ranked_liters:
            lines.append("Nadie ha apuntado litros aún 😇")
        else:
            for i, r in enumerate(ranked_liters, 1):
                lines.append(f"{i}. {r['name']} — {fmt_liters(r['litros'])}")
        lines.append("")

        lines.append("🔥 Bebidas del año")
        if not drinks_year:
            lines.append("Nada registrado todavía.")
        else:
            for i, r in enumerate(drinks_year, 1):
                has_liters = bool(r["has_liters"])
                u = int(r["unidades"])
                l = float(r["litros"])
                if has_liters and l > 0:
                    lines.append(f"{i}. {r['label']} — {fmt_liters(l)} ({fmt_units(u)})")
                else:
                    lines.append(f"{i}. {r['label']} — {fmt_units(u)}")
        lines.append("")
        lines.append("🍺 Ranking por tipo de bebida")
        lines.append("")

        grouped = {}
        for r in per_type_people:
            key = (r["category"], r["label"], bool(r["has_liters"]))
            grouped.setdefault(key, []).append(r)

        keys_sorted = sorted(grouped.keys(), key=lambda k: (0 if k[0] == "BEER" else 1, k[1].lower()))

        for (cat, label, has_liters) in keys_sorted:
            rows = grouped[(cat, label, has_liters)]
            emoji = "🍺" if cat == "BEER" else "🥃"
            lines.append(f"{emoji} {label}")

            if has_liters:
                rows = sorted(rows, key=lambda x: (float(x["litros"]), int(x["unidades"]), x["person_name"]), reverse=True)
                for i, rr in enumerate(rows, 1):
                    lines.append(f"{i}. {rr['person_name']} — {fmt_liters(rr['litros'])} ({fmt_units(rr['unidades'])})")
            else:
                rows = sorted(rows, key=lambda x: (int(x["unidades"]), x["person_name"]), reverse=True)
                for i, rr in enumerate(rows, 1):
                    lines.append(f"{i}. {rr['person_name']} — {fmt_units(rr['unidades'])}")

            lines.append("")

        await q.edit_message_text("\n".join(lines).rstrip(), reply_markup=menu_kb(is_admin(tg_id)))
        set_state(context, "MENU", {})
        return

    # -------- AÑADIR: CATEGORÍA --------
    if data.startswith(CB_CAT):
        cat = data.split(":", 1)[1]
        types = list_drink_types(cat)
        title = "🍺 Elige el tipo de cerveza:" if cat == "BEER" else "🥃 Elige el tipo:"
        await q.edit_message_text(title, reply_markup=types_kb(types, back_to="cat"))
        set_state(context, "ADD_TYPE", {"cat": cat})
        return

    # -------- AÑADIR: TIPO --------
    if data.startswith(CB_TYPE):
        drink_type_id = int(data.split(":", 1)[1])
        await q.edit_message_text("¿Cuántas has tomado?", reply_markup=qty_kb())
        set_state(context, "ADD_QTY", {**sdata, "drink_type_id": drink_type_id})
        return

    # -------- AÑADIR: CANTIDAD --------
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

    # -------- AÑADIR: FECHA --------
    if data.startswith(CB_DATE):
        which = data.split(":", 1)[1]
        if which == "other":
            await q.edit_message_text("Escribe la fecha en formato YYYY-MM-DD (ej: 2026-01-25):")
            set_state(context, "ADD_DATE_MANUAL", sdata)
            return

        consumed_at = dt.date.today() if which == "today" else (dt.date.today() - dt.timedelta(days=1))
        person = get_assigned_person(tg_id)
        qty = int(sdata["qty"])

        insert_event(
            person_id=person["id"],
            telegram_user_id=tg_id,
            drink_type_id=sdata["drink_type_id"],
            quantity=qty,
            consumed_at=consumed_at,
        )

        # Mensaje principal (bonito)
        when = consumed_at.strftime("%d/%m/%Y")
        base_msg = random.choice(FUN_PHRASES) + f"\n\n✅ Apuntado ({when})."
        await q.edit_message_text(base_msg, reply_markup=menu_kb(is_admin(tg_id)))
        set_state(context, "MENU", {})

        # Logros (si toca)
        year_start = (dt.date(consumed_at.year, 1, 7) <= consumed_at) and consumed_at.year or (consumed_at.year - 1)
        totals = get_person_year_totals(person["id"], int(year_start))
        after_units = int(totals["unidades"])
        first = is_first_event_of_year(person["id"], int(year_start))
        ach_msgs = build_achievement_messages(person["name"], int(year_start), qty, after_units, first)

        for msg in ach_msgs:
            try:
                await context.bot.send_message(chat_id=tg_id, text=msg)
            except Exception:
                pass

        return

    # -------- DESHACER --------
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
            f"Vale, no toco nada 🙂\n\n¿Qué quieres hacer?",
            reply_markup=menu_kb(is_admin(tg_id)),
        )
        set_state(context, "MENU", {})
        return

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
        await update.message.reply_text(random.choice(FUN_PHRASES) + f"\n\n✅ Apuntado ({when}).", reply_markup=menu_kb(is_admin(tg_id)))
        set_state(context, "MENU", {})

        # Logros
        year_start = (dt.date(consumed_at.year, 1, 7) <= consumed_at) and consumed_at.year or (consumed_at.year - 1)
        totals = get_person_year_totals(person["id"], int(year_start))
        after_units = int(totals["unidades"])
        first = is_first_event_of_year(person["id"], int(year_start))
        ach_msgs = build_achievement_messages(person["name"], int(year_start), qty, after_units, first)
        for msg in ach_msgs:
            try:
                await context.bot.send_message(chat_id=tg_id, text=msg)
            except Exception:
                pass

        return

    # ADMIN: añadir persona por texto
    if state == "ADMIN_ADD":
        if not is_admin(tg_id):
            await update.message.reply_text("🚫 No tienes permisos.")
            set_state(context, "MENU", {})
            return

        ok = add_person(text)
        if ok:
            await update.message.reply_text(f"✅ '{text}' añadido como nueva persona.", reply_markup=menu_kb(True))
        else:
            await update.message.reply_text("⚠️ No se pudo añadir (¿ya existe?).", reply_markup=menu_kb(True))
        set_state(context, "MENU", {})
        return

    await update.message.reply_text("Escribe /start para ver el menú.")

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # JobQueue: comprobar cada día y si es día 1 envía resumen del mes anterior
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
