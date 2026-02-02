import os
import datetime as dt
import random
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

from db import (
    init_db,
    # Asignación / acceso
    get_assigned_person,
    upsert_pending_telegram,
    list_pending_telegrams,

    # Bebidas / eventos
    list_drink_types, insert_event, list_last_events, list_user_events_page, void_event,

    # Informes / rankings
    list_years_with_data, report_year,
    get_person_year_totals, is_first_event_of_year,
    month_summary, monthly_summary_already_sent, mark_monthly_summary_sent,
    get_person_day_units, list_person_consumed_dates,
    monthly_shame_report,
    person_year_breakdown,
    year_drinks_totals,
    year_drink_type_person_totals,

    # Envíos automáticos
    list_active_telegram_user_ids,

    # Admin
    is_admin,
    add_person,
    list_persons_by_status,
    list_persons_without_active_telegram,
    search_persons_by_name,
    get_person_profile,
    admin_assign_telegram_to_person,
    admin_suspend_person,
    admin_reactivate_person,
    admin_delete_person,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
TZ = ZoneInfo("Europe/Madrid")

# Callbacks
CB_MENU_ADD = "menu:add"
CB_MENU_REPORT = "menu:report"
CB_MENU_UNDO = "menu:undo"
CB_MENU_ADMIN = "menu:admin"
CB_MENU_PANEL = "menu:panel"

CB_PANEL_DRINKS = "panel:drinks"
CB_PANEL_OLDER = "panel:older:"  # panel:older:<cursor_id>
CB_PANEL_NEWER = "panel:newer:"  # panel:newer:<cursor_id>

CB_CAT = "cat:"
CB_TYPE = "type:"
CB_QTY = "qty:"
CB_DATE = "date:"

CB_UNDO_PICK = "undo:"
CB_UNDO_CONFIRM = "undo_yes:"
CB_UNDO_CANCEL = "undo_no"

CB_YEAR = "year:"  # year:<year_start>

# Admin callbacks
CB_ADMIN_PERSONS = "admin:persons"
CB_ADMIN_REQUESTS = "admin:requests"
CB_ADMIN_CREATE_PERSON = "admin:create_person"

CB_ADMIN_PERSONS_FILTER = "admin:persons_filter:"      # admin:persons_filter:<ACTIVE|INACTIVE|NO_TG>
CB_ADMIN_PERSON_VIEW = "admin:person_view:"           # admin:person_view:<person_id>
CB_ADMIN_PERSON_ASSIGN = "admin:person_assign:"        # admin:person_assign:<person_id>
CB_ADMIN_PERSON_SUSPEND = "admin:person_suspend:"      # admin:person_suspend:<person_id>
CB_ADMIN_PERSON_REACTIVATE = "admin:person_reactivate:"# admin:person_reactivate:<person_id>
CB_ADMIN_PERSON_DELETE = "admin:person_delete:"        # admin:person_delete:<person_id>
CB_ADMIN_PERSON_DELETE_CONFIRM = "admin:person_delete_confirm:"  # ...:<person_id>

CB_ADMIN_PICK_TG = "admin:pick_tg:"                    # admin:pick_tg:<telegram_user_id>
CB_ADMIN_SEARCH_PERSON = "admin:search_person"

def kb(rows):
    return InlineKeyboardMarkup(rows)

def menu_kb(is_admin_user: bool):
    rows = [
        [InlineKeyboardButton("➕ Añadir", callback_data=CB_MENU_ADD)],
        [InlineKeyboardButton("📊 Informes", callback_data=CB_MENU_REPORT)],
        [InlineKeyboardButton("👤 Panel de usuario", callback_data=CB_MENU_PANEL)],
    ]
    if is_admin_user:
        rows.append([InlineKeyboardButton("⚙️ Administración", callback_data=CB_MENU_ADMIN)])
    return kb(rows)


def user_panel_kb():
    rows = [
        [InlineKeyboardButton("🕒 Mis últimas bebidas", callback_data=CB_PANEL_DRINKS)],
        [InlineKeyboardButton("↩️ Deshacer bebidas", callback_data=CB_MENU_UNDO)],
        [InlineKeyboardButton("⬅️ Volver", callback_data="back:menu")],
    ]
    return kb(rows)


def panel_history_kb(has_older: bool, has_newer: bool, oldest_id: int | None, newest_id: int | None):
    nav = []
    if has_older and oldest_id is not None:
        nav.append(InlineKeyboardButton("⬅️ Más antiguas", callback_data=f"{CB_PANEL_OLDER}{oldest_id}"))
    if has_newer and newest_id is not None:
        nav.append(InlineKeyboardButton("➡️ Más recientes", callback_data=f"{CB_PANEL_NEWER}{newest_id}"))
    rows = []
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Volver", callback_data=CB_MENU_PANEL)])
    return kb(rows)

def format_event_line(ev):
    # ev: dict con id, label, quantity, created_at
    ts = ev["created_at"].astimezone(TZ)
    stamp = ts.strftime("%d/%m %H:%M")
    return f"{stamp} — {ev['label']} — x{ev['quantity']}"

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
        # Fecha consumida + hora del registro (normalizada a Europe/Madrid)
        day = e["consumed_at"].strftime("%d/%m/%Y")
        ca = e.get("created_at")
        tm = "--:--"
        if ca:
            try:
                # Si viene con zona horaria, convertimos; si viene naive, asumimos TZ del bot
                local = ca.astimezone(TZ) if getattr(ca, "tzinfo", None) else ca.replace(tzinfo=TZ)
                tm = local.strftime("%H:%M")
            except Exception:
                tm = "--:--"
        label = f"{day} {tm} — {e['label']} — x{e['quantity']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"{CB_UNDO_PICK}{e['id']}")])
    rows.append([InlineKeyboardButton("⬅️ Volver al panel", callback_data=CB_MENU_PANEL)])
    return kb(rows)

def undo_confirm_kb(event_id: int):
    return kb([
        [InlineKeyboardButton("✅ Sí, eliminar", callback_data=f"{CB_UNDO_CONFIRM}{event_id}")],
        [InlineKeyboardButton("❌ Cancelar", callback_data=CB_UNDO_CANCEL)],
    ])

def years_kb(years):
    rows = [[InlineKeyboardButton(f"{y}-{y+1}", callback_data=f"{CB_YEAR}{y}")] for y in years]
    rows.append([InlineKeyboardButton("⬅️ Volver al panel", callback_data=CB_MENU_PANEL)])
    return kb(rows)

def admin_main_kb():
    return kb([
        [InlineKeyboardButton("👥 Personas (histórico)", callback_data=CB_ADMIN_PERSONS)],
        [InlineKeyboardButton("📨 Solicitudes (pendientes)", callback_data=CB_ADMIN_REQUESTS)],
        [InlineKeyboardButton("➕ Crear persona/plaza", callback_data=CB_ADMIN_CREATE_PERSON)],
        [InlineKeyboardButton("⬅️ Menú", callback_data="back:menu")],
    ])

def admin_persons_menu_kb():
    return kb([
        [InlineKeyboardButton("✅ Ver ACTIVAS", callback_data=f"{CB_ADMIN_PERSONS_FILTER}ACTIVE")],
        [InlineKeyboardButton("⛔ Ver INACTIVAS", callback_data=f"{CB_ADMIN_PERSONS_FILTER}INACTIVE")],
        [InlineKeyboardButton("🆓 Ver SIN TELEGRAM", callback_data=f"{CB_ADMIN_PERSONS_FILTER}NO_TG")],
        [InlineKeyboardButton("🔎 Buscar por nombre", callback_data=CB_ADMIN_SEARCH_PERSON)],
        [InlineKeyboardButton("⬅️ Atrás", callback_data=CB_MENU_ADMIN)],
    ])

def admin_person_list_kb(persons):
    rows = []
    for p in persons:
        label = f"{p['name']} ({p['status']})"
        rows.append([InlineKeyboardButton(label, callback_data=f"{CB_ADMIN_PERSON_VIEW}{p['id']}")])
    rows.append([InlineKeyboardButton("⬅️ Atrás", callback_data=CB_ADMIN_PERSONS)])
    return kb(rows)

def admin_requests_kb(requests):
    rows = []
    for r in requests:
        uname = (r.get("username") or "").strip()
        name = (r.get("full_name") or "").strip()
        label = f"{name}" if name else (f"@{uname}" if uname else str(r["telegram_user_id"]))
        if uname:
            label = f"{label} (@{uname})" if name else f"@{uname}"
        label = f"{label} — {r['telegram_user_id']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"{CB_ADMIN_PICK_TG}{r['telegram_user_id']}")])
    rows.append([InlineKeyboardButton("⬅️ Atrás", callback_data=CB_MENU_ADMIN)])
    return kb(rows)

def admin_person_profile_kb(person_id: int, status: str, has_active_tg: bool):
    rows = []
    rows.append([InlineKeyboardButton("✅ Asignar / Reasignar Telegram", callback_data=f"{CB_ADMIN_PERSON_ASSIGN}{person_id}")])
    if status == "INACTIVE":
        rows.append([InlineKeyboardButton("▶️ Reactivar", callback_data=f"{CB_ADMIN_PERSON_REACTIVATE}{person_id}")])
    else:
        rows.append([InlineKeyboardButton("⛔ Suspender", callback_data=f"{CB_ADMIN_PERSON_SUSPEND}{person_id}")])
    rows.append([InlineKeyboardButton("💀 Eliminar (borrado total)", callback_data=f"{CB_ADMIN_PERSON_DELETE}{person_id}")])
    rows.append([InlineKeyboardButton("⬅️ Atrás", callback_data=CB_ADMIN_PERSONS)])
    return kb(rows)

def admin_delete_confirm_kb(person_id: int):
    return kb([
        [InlineKeyboardButton("✅ Entiendo, continuar", callback_data=f"{CB_ADMIN_PERSON_DELETE_CONFIRM}{person_id}")],
        [InlineKeyboardButton("❌ Cancelar", callback_data=CB_ADMIN_PERSONS)],
    ])

def set_state(context: ContextTypes.DEFAULT_TYPE, state: str, data: dict | None = None):
    context.user_data["state"] = state
    if data is not None:
        context.user_data["data"] = data

def get_state(context: ContextTypes.DEFAULT_TYPE):
    return context.user_data.get("state"), context.user_data.get("data", {})

def compute_streak_days(dates_desc, end_day: dt.date) -> int:
    """Racha de días consecutivos con al menos un registro, acabando en end_day."""
    if not dates_desc:
        return 0
    s = 0
    expected = end_day
    for d in dates_desc:
        if d == expected:
            s += 1
            expected = expected - dt.timedelta(days=1)
            continue
        if d < expected:
            break
    return s


# --------- Logros / frases ---------

MILESTONES_UNITS = [1, 50, 100, 200, 500]

NORMAL_ROAST_PHRASES = [
    '🍻 Apuntado. Otro paso firme en la dirección equivocada.',
    '✅ Guardado. No era necesario… por eso lo has hecho.',
    '📌 Registrado. La moderación te saluda desde lejos.',
    '😌 Hecho. Tu autocontrol hoy ha pedido teletrabajo.',
    '🧾 Anotado. Sigues siendo constante, en lo que no conviene.',
    '🧠 Confirmado. Has vuelto a elegir ‘pan para hoy, resaca para mañana’.',
    '📉 Apuntado. Buenas decisiones: 0. Tú: 1.',
    '🎭 Registrado. Excelente actuación: ‘yo controlo’.',
    '🧯 Guardado. Esto ya no es apagar fuegos: es iniciarlos.',
    '🏳️ Hecho. Te has rendido rápido, como siempre que toca frenar.',
    '🫠 Apuntado. Otra victoria aplastante… contra tu coherencia.',
    '📣 Confirmado. Nadie te obligó. Eso es lo más preocupante.',
]

STREAK_ROAST_PHRASES = [
    '📆 Racha activa. La constancia que no tienes para mejorar, pero aquí sí.',
    '🔥 Otro día seguido. Ya no es ‘un día tonto’, es tu modo de vida.',
    '🧱 Día tras día. Construyendo un hábito con disciplina militar.',
    '🏃\u200d♂️ Sigues huyendo de la moderación sin mirar atrás.',
    '📉 Racha confirmada. Tu autocontrol está oficialmente en ERTE.',
    '🪦 Día consecutivo registrado. Aquí yace tu ‘mañana paro’.',
    '🚨 Racha en marcha. El plan era frenar… pero tú no vienes a eso.',
    '🥇 Enhorabuena: récord personal en repetir la misma idea mala.',
    '🧠 Día seguido. Tu cerebro propuso parar; tú lo ignoraste con experiencia.',
    '🧯 Racha activa. Esto ya es mantenimiento preventivo de la resaca.',
    '📍 Sigues. La dirección correcta era la otra.',
    '🫡 Racha confirmada. Sabotaje personal ejecutado sin fallos.',
    '📚 Otro día más. Capítulo nuevo de ‘yo lo controlo’ (temporada 12).',
    '🧊 Día seguido. Frío en la cabeza, caliente en el contador.',
    '⚠️ Racha activa. No es casualidad, es dedicación.',
    '🎯 Día tras día acertando donde más duele: en tu propia disciplina.',
    '🧾 Racha confirmada. Tu historial ya se escribe solo.',
    '🪤 Otro día seguido. Caer en lo mismo ya es tradición.',
    '📉 Racha. Lo difícil era parar; por suerte no lo intentaste.',
    '💀 Día consecutivo. Tus buenas intenciones siguen desaparecidas.',
    '📆 Racha: +1. La moderación te ha bloqueado.',
    '🔥 Sigues. Es impresionante… en el sentido triste.',
    '🥴 Otro día. No era buena idea ayer y hoy sigue sin serlo.',
    '🧨 Racha activa. Te estás superando… en lo que no conviene.',
    '🪦 Racha. Un minuto de silencio por tu ‘solo hoy’.',
    '📣 Día seguido. La coherencia se fue antes que tú.',
    '🚮 Racha. Si esto fuese un examen, repetirías curso por gusto.',
    '🧠 Día consecutivo: tu excusa ya venía preparada, ¿verdad?',
    '📉 Racha activa. El freno está ahí, pero tú vas sin él.',
    '🏁 Otro día seguido. Vas fuerte… en la dirección equivocada.',
    '🔔 Racha confirmada. Tu hígado ha pedido que le trates de ‘usted’.',
    '🧱 Día seguido. La disciplina existe: solo la usas para liarla.',
    '🥇 Racha. Nadie esperaba nada y aun así lo empeoras.',
    '📍 Racha activa. Te atrae el desastre como si pagara alquiler.',
    '🧯 Día seguido. Esto ya requiere protocolo, no voluntad.',
    '🎭 Racha. ‘Yo controlo’ se ha convertido en tu chiste recurrente.',
    '🪤 Día consecutivo. La trampa eres tú y también el cebo.',
    '📚 Racha. Manual práctico de cómo sabotearte (edición ampliada).',
    '🚨 Día seguido. Aviso: esto ya cuenta como patrón.',
    '🧾 Racha activa. Estás compitiendo contra ti mismo… y perdiendo.',
    '🧊 Día seguido. Tu plan de parar está en mantenimiento.',
    '🥇 Racha confirmada. El ‘solo una’ ya es mitología.',
    '📉 Día consecutivo. La moderación te escribe y tú la dejas en visto.',
    '🧨 Racha activa. Estás construyendo un desastre con cariño artesanal.',
    '🪦 Día seguido. Tus buenas intenciones ya ni hacen check-in.',
    '📆 Racha activa. Te estás especializando en repetir lo peor.',
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
    user = update.effective_user

    person = get_assigned_person(tg_id)

    # Registrado
    if person:
        if person.get("status") == "INACTIVE":
            await update.message.reply_text(
                "🚫 Estás suspendido.\nEl admin tiene que reactivarte para volver a usar el bot."
            )
            set_state(context, "SUSPENDED", {})
            return

        await update.message.reply_text(
            f"👋 Hola, {person['name']}.\n\n¿Qué quieres hacer?",
            reply_markup=menu_kb(is_admin(tg_id)),
        )
        set_state(context, "MENU", {})
        return

    # No asignado -> solicitud pendiente (no hay auto-asignación)
    username = getattr(user, "username", None)
    full_name = getattr(user, "full_name", None)
    try:
        upsert_pending_telegram(tg_id, username, full_name)
    except Exception:
        pass

    await update.message.reply_text(
        "👋 ¡Recibido!\n\n📨 Tu solicitud está pendiente de aprobación.\n"
        "Cuando el admin te asigne una plaza podrás usar el bot."
    )
    set_state(context, "PENDING", {})


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tg_id = q.from_user.id
    data = q.data or ""
    state, sdata = get_state(context)

    # Guard rails: usuarios no asignados o suspendidos no pueden navegar por menús antiguos
    assigned = get_assigned_person(tg_id)
    if assigned and assigned.get("status") == "INACTIVE" and not is_admin(tg_id):
        await q.edit_message_text("🚫 Estás suspendido. El admin debe reactivarte.")
        set_state(context, "SUSPENDED", {})
        return
    if not assigned and not is_admin(tg_id):
        await q.edit_message_text("📨 Estás pendiente de aprobación. El admin debe asignarte una plaza.")
        set_state(context, "PENDING", {})
        return

    # -------- BACKS --------
    if data == "back:menu":
        person = get_assigned_person(tg_id)
        if not person:
            await q.edit_message_text("📨 Estás pendiente de aprobación. El admin debe asignarte una plaza.")
            set_state(context, "PENDING", {})
            return
        if person.get("status") == "INACTIVE":
            await q.edit_message_text("🚫 Estás suspendido. El admin debe reactivarte.")
            set_state(context, "SUSPENDED", {})
            return
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



    # -------- MENÚ --------
    if data == CB_MENU_ADD:
        await q.edit_message_text("¿Qué vas a añadir?", reply_markup=categories_kb())
        set_state(context, "ADD_CAT", {})
        return
    if data == CB_MENU_UNDO:
        person = get_assigned_person(tg_id)
        events = list_last_events(person["id"], 5)
        if not events:
            await q.edit_message_text(
                "No tienes entradas recientes para deshacer.",
                reply_markup=user_panel_kb(),
            )
            set_state(context, "PANEL", {})
            return
        await q.edit_message_text("Elige cuál quieres eliminar:", reply_markup=undo_list_kb(events))
        set_state(context, "UNDO_PICK", {})
        return
        await q.edit_message_text(
            "Elige cuál quieres eliminar:",
            reply_markup=undo_list_kb(events),
        )
        set_state(context, "UNDO_PICK", {})
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


# -------- PANEL USUARIO --------
    if data == CB_MENU_PANEL or data == "panel:menu":
        person = get_assigned_person(tg_id)
        if not person:
            await q.edit_message_text("No estás asignado. Espera a que el admin te apruebe.")
            set_state(context, "PENDING", {})
            return
        if person.get("status") == "INACTIVE":
            await q.edit_message_text("🚫 Estás suspendido. El admin tiene que reactivarte para volver a usar el bot.")
            set_state(context, "SUSPENDED", {})
            return

        await q.edit_message_text("👤 Panel de usuario", reply_markup=user_panel_kb())
        set_state(context, "PANEL", {})
        return

    if data == CB_PANEL_DRINKS:
        person = get_assigned_person(tg_id)
        if not person:
            await q.edit_message_text("No estás asignado. Espera a que el admin te apruebe.")
            set_state(context, "PENDING", {})
            return

        events = list_user_events_page(person["id"], limit=15)
        if not events:
            await q.edit_message_text(
                "Aún no has añadido bebidas 🙂",
                reply_markup=panel_history_kb(False, False, None, None),
            )
            set_state(context, "PANEL_DRINKS", {"oldest": None, "newest": None})
            return

        newest_id = max(e["id"] for e in events)
        oldest_id = min(e["id"] for e in events)
        has_older = bool(list_user_events_page(person["id"], limit=1, before_id=oldest_id))
        has_newer = bool(list_user_events_page(person["id"], limit=1, after_id=newest_id))

        lines = "\n".join(format_event_line(e) for e in events)
        await q.edit_message_text(
            "🕒 *Mis últimas bebidas* (15 más recientes)\n\n" + lines,
            reply_markup=panel_history_kb(has_older, has_newer, oldest_id, newest_id),
            parse_mode="Markdown",
        )
        set_state(context, "PANEL_DRINKS", {"oldest": oldest_id, "newest": newest_id})
        return

    if data.startswith(CB_PANEL_OLDER):
        person = get_assigned_person(tg_id)
        if not person:
            await q.edit_message_text("No estás asignado. Espera a que el admin te apruebe.")
            set_state(context, "PENDING", {})
            return

        try:
            cursor_id = int(data.split(":", 2)[-1])
        except Exception:
            await q.edit_message_text("⚠️ Cursor inválido.", reply_markup=user_panel_kb())
            set_state(context, "PANEL", {})
            return

        events = list_user_events_page(person["id"], limit=15, before_id=cursor_id)
        if not events:
            await q.edit_message_text(
                "No hay más antiguas.",
                reply_markup=panel_history_kb(False, True, None, cursor_id),
            )
            return

        newest_id = max(e["id"] for e in events)
        oldest_id = min(e["id"] for e in events)
        has_older = bool(list_user_events_page(person["id"], limit=1, before_id=oldest_id))
        has_newer = bool(list_user_events_page(person["id"], limit=1, after_id=newest_id))

        lines = "\n".join(format_event_line(e) for e in events)
        await q.edit_message_text(
            "🕒 *Historial de bebidas*\n\n" + lines,
            reply_markup=panel_history_kb(has_older, has_newer, oldest_id, newest_id),
            parse_mode="Markdown",
        )
        set_state(context, "PANEL_DRINKS", {"oldest": oldest_id, "newest": newest_id})
        return

    if data.startswith(CB_PANEL_NEWER):
        person = get_assigned_person(tg_id)
        if not person:
            await q.edit_message_text("No estás asignado. Espera a que el admin te apruebe.")
            set_state(context, "PENDING", {})
            return

        try:
            cursor_id = int(data.split(":", 2)[-1])
        except Exception:
            await q.edit_message_text("⚠️ Cursor inválido.", reply_markup=user_panel_kb())
            set_state(context, "PANEL", {})
            return

        # DB devuelve asc, invertimos para mostrar desc
        events_asc = list_user_events_page(person["id"], limit=15, after_id=cursor_id)
        events = list(reversed(events_asc))
        if not events:
            await q.edit_message_text(
                "Ya estás en las más recientes.",
                reply_markup=panel_history_kb(True, False, cursor_id, None),
            )
            return

        newest_id = max(e["id"] for e in events)
        oldest_id = min(e["id"] for e in events)
        has_older = bool(list_user_events_page(person["id"], limit=1, before_id=oldest_id))
        has_newer = bool(list_user_events_page(person["id"], limit=1, after_id=newest_id))

        lines = "\n".join(format_event_line(e) for e in events)
        await q.edit_message_text(
            "🕒 *Historial de bebidas*\n\n" + lines,
            reply_markup=panel_history_kb(has_older, has_newer, oldest_id, newest_id),
            parse_mode="Markdown",
        )
        set_state(context, "PANEL_DRINKS", {"oldest": oldest_id, "newest": newest_id})
        return

    # -------- ADMIN --------
    if data == CB_MENU_ADMIN:
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.", reply_markup=menu_kb(False))
            return
        await q.edit_message_text("⚙️ Administración", reply_markup=admin_main_kb())
        set_state(context, "ADMIN", {})
        return

    if data == CB_ADMIN_PERSONS:
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.")
            return
        await q.edit_message_text("👥 Personas (histórico)", reply_markup=admin_persons_menu_kb())
        set_state(context, "ADMIN_PERSONS_MENU", {})
        return

    if data.startswith(CB_ADMIN_PERSONS_FILTER):
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.")
            return
        filt = data.split(":", 2)[2]
        if filt == "ACTIVE":
            persons = list_persons_by_status("ACTIVE")
            title = "✅ Personas ACTIVAS"
        elif filt == "INACTIVE":
            persons = list_persons_by_status("INACTIVE")
            title = "⛔ Personas INACTIVAS"
        else:
            persons = list_persons_without_active_telegram()
            title = "🆓 Personas SIN TELEGRAM"
        if not persons:
            await q.edit_message_text(f"{title}\n\n(ninguna)", reply_markup=admin_persons_menu_kb())
            set_state(context, "ADMIN_PERSONS_MENU", {})
            return
        await q.edit_message_text(title, reply_markup=admin_person_list_kb(persons))
        set_state(context, "ADMIN_PERSONS_LIST", {"filter": filt})
        return

    if data == CB_ADMIN_SEARCH_PERSON:
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.")
            return
        await q.edit_message_text("🔎 Escribe el nombre (o parte) para buscar:")
        set_state(context, "ADMIN_PERSON_SEARCH", {})
        return

    if data.startswith(CB_ADMIN_PERSON_VIEW):
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.")
            return
        person_id = int(data.split(":", 2)[2])
        prof = get_person_profile(person_id)
        if not prof:
            await q.edit_message_text("⚠️ No encontrada.", reply_markup=admin_persons_menu_kb())
            set_state(context, "ADMIN_PERSONS_MENU", {})
            return

        p = prof["person"]
        active = prof["active_account"]
        prev = prof["previous_accounts"]
        stats = prof["stats"]

        lines = [f"👤 Ficha: {p['name']}", f"Estado: {p['status']}"]

        if active:
            lines.append(f"Telegram activo: {active['telegram_user_id']}")
        else:
            lines.append("Telegram activo: —")

        if prev:
            lines.append("")
            lines.append("Telegrams anteriores:")
            for r in prev[:5]:
                ua = r.get("unassigned_at")
                ua_txt = ua.strftime("%Y-%m-%d") if ua else "?"
                lines.append(f"• {r['telegram_user_id']} (hasta {ua_txt})")

        lines.append("")
        last = stats.get("last_activity_at")
        last_txt = last.strftime("%Y-%m-%d") if last else "—"
        lines.append(f"Eventos: {int(stats.get('events_count') or 0)}")
        lines.append(f"Última actividad: {last_txt}")

        await q.edit_message_text(
            "\n".join(lines),
            reply_markup=admin_person_profile_kb(p["id"], p["status"], bool(active)),
        )
        set_state(context, "ADMIN_PERSON_PROFILE", {"person_id": p["id"]})
        return

    if data.startswith(CB_ADMIN_PERSON_ASSIGN):
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.")
            return
        person_id = int(data.split(":", 2)[2])
        reqs = list_pending_telegrams(20)
        if not reqs:
            await q.edit_message_text(
                "📨 No hay solicitudes pendientes ahora mismo.",
                reply_markup=admin_person_profile_kb(person_id, get_person_profile(person_id)["person"]["status"], False),
            )
            set_state(context, "ADMIN_PERSON_PROFILE", {"person_id": person_id})
            return
        await q.edit_message_text("📨 Elige un Telegram pendiente para asignar:", reply_markup=admin_requests_kb(reqs))
        set_state(context, "ADMIN_ASSIGN_PICK_TG", {"person_id": person_id})
        return

    if data.startswith(CB_ADMIN_PICK_TG):
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.")
            return
        picked_tg = int(data.split(":", 2)[2])

        # Si venimos de "asignar a persona"
        if state == "ADMIN_ASSIGN_PICK_TG" and sdata.get("person_id"):
            person_id = int(sdata["person_id"])
            st, _ = admin_assign_telegram_to_person(person_id, picked_tg)
            if st == "TG_TAKEN":
                await q.edit_message_text("⚠️ Ese Telegram ya está asignado a otra persona.")
                return
            if st == "NOT_FOUND":
                await q.edit_message_text("⚠️ Persona no encontrada.")
                return

            prof = get_person_profile(person_id)
            name = prof["person"]["name"] if prof else "la persona"
            await q.edit_message_text(f"✅ Asignado {picked_tg} a {name}.")
            # Volver a ficha
            prof = get_person_profile(person_id)
            if prof:
                p = prof["person"]
                await q.message.reply_text(
                    f"👤 {p['name']} actualizado.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Abrir ficha", callback_data=f"{CB_ADMIN_PERSON_VIEW}{person_id}")]]),
                )
            set_state(context, "ADMIN", {})
            return

        # Vista simple de solicitud (desde menú solicitudes)
        await q.edit_message_text(
            f"📨 Solicitud pendiente\n\nTelegram: {picked_tg}\n\nPara asignarlo: abre una persona y pulsa “Asignar Telegram”.",
            reply_markup=admin_main_kb(),
        )
        set_state(context, "ADMIN", {})
        return

    if data == CB_ADMIN_REQUESTS:
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.")
            return
        reqs = list_pending_telegrams(20)
        if not reqs:
            await q.edit_message_text("📨 No hay solicitudes pendientes.", reply_markup=admin_main_kb())
            set_state(context, "ADMIN", {})
            return
        await q.edit_message_text("📨 Solicitudes pendientes:", reply_markup=admin_requests_kb(reqs))
        set_state(context, "ADMIN_REQUESTS", {})
        return

    if data == CB_ADMIN_CREATE_PERSON:
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.")
            return
        await q.edit_message_text("➕ Escribe el nombre de la nueva persona/plaza:")
        set_state(context, "ADMIN_CREATE_PERSON", {})
        return

    if data.startswith(CB_ADMIN_PERSON_SUSPEND):
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.")
            return
        person_id = int(data.split(":", 2)[2])
        admin_suspend_person(person_id)
        await q.edit_message_text("✅ Persona suspendida.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Volver a ficha", callback_data=f"{CB_ADMIN_PERSON_VIEW}{person_id}")]]))
        set_state(context, "ADMIN", {})
        return

    if data.startswith(CB_ADMIN_PERSON_REACTIVATE):
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.")
            return
        person_id = int(data.split(":", 2)[2])
        admin_reactivate_person(person_id)
        await q.edit_message_text("✅ Persona reactivada.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Volver a ficha", callback_data=f"{CB_ADMIN_PERSON_VIEW}{person_id}")]]))
        set_state(context, "ADMIN", {})
        return

    if data.startswith(CB_ADMIN_PERSON_DELETE):
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.")
            return
        person_id = int(data.split(":", 2)[2])
        prof = get_person_profile(person_id)
        if not prof:
            await q.edit_message_text("⚠️ No encontrada.", reply_markup=admin_persons_menu_kb())
            return
        name = prof["person"]["name"]
        warn = (
            f"💀 Vas a ELIMINAR a {name}.\n\n"
            "Esto borra TODO: persona/plaza, eventos e historial de Telegram.\n"
            "Si esa persona tenía Telegram asignado, tendrá que volver a solicitar acceso y aprobarlo el admin."
        )
        await q.edit_message_text(warn, reply_markup=admin_delete_confirm_kb(person_id))
        set_state(context, "ADMIN_DELETE_CONFIRM_1", {"person_id": person_id, "name": name})
        return

    if data.startswith(CB_ADMIN_PERSON_DELETE_CONFIRM):
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.")
            return
        person_id = int(data.rsplit(':', 1)[1])
        prof = get_person_profile(person_id)
        name = (prof["person"]["name"] if prof else sdata.get("name") or "persona")
        await q.edit_message_text(f"✍️ Escribe EXACTAMENTE:\n\nELIMINAR {name}")
        set_state(context, "ADMIN_DELETE_CONFIRM_TEXT", {"person_id": person_id, "name": name})
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
        day_units = get_person_day_units(person["id"], consumed_at)
        dates_desc = list_person_consumed_dates(person["id"], consumed_at, limit=60)
        streak_days = compute_streak_days(dates_desc, consumed_at)

        if streak_days >= 2 or day_units > 2:
            msg_pick = random.choice(STREAK_ROAST_PHRASES)
        else:
            msg_pick = random.choice(NORMAL_ROAST_PHRASES)

        base_msg = msg_pick + f"\n\n✅ Apuntado ({when})."
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
            reply_markup=user_panel_kb(),
        )
        set_state(context, "PANEL", {})
        return

    if data == CB_UNDO_CANCEL:
        # Cancelar deshacer -> volver SIEMPRE al panel de usuario
        await q.edit_message_text(
            "👤 Panel de usuario",
            reply_markup=user_panel_kb(),
        )
        set_state(context, "PANEL", {})
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
        await update.message.reply_text(random.choice(NORMAL_ROAST_PHRASES) + f"\n\n✅ Apuntado ({when}).", reply_markup=menu_kb(is_admin(tg_id)))
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

    # ADMIN: crear persona/plaza por texto
    if state == "ADMIN_CREATE_PERSON":
        if not is_admin(tg_id):
            await update.message.reply_text("🚫 No tienes permisos.")
            set_state(context, "MENU", {})
            return

        ok = add_person(text)
        if ok:
            await update.message.reply_text(f"✅ '{text}' creada como nueva persona/plaza.", reply_markup=menu_kb(True))
        else:
            await update.message.reply_text("⚠️ No se pudo crear (¿ya existe?).", reply_markup=menu_kb(True))
        set_state(context, "MENU", {})
        return

    # ADMIN: buscar persona
    if state == "ADMIN_PERSON_SEARCH":
        if not is_admin(tg_id):
            await update.message.reply_text("🚫 No tienes permisos.")
            set_state(context, "MENU", {})
            return
        persons = search_persons_by_name(text, limit=20)
        if not persons:
            await update.message.reply_text("No encontré coincidencias.", reply_markup=admin_persons_menu_kb())
            set_state(context, "ADMIN_PERSONS_MENU", {})
            return
        await update.message.reply_text("Resultados:", reply_markup=admin_person_list_kb(persons))
        set_state(context, "ADMIN_PERSONS_LIST", {"filter": "SEARCH"})
        return

    # ADMIN: confirmación fuerte de borrado
    if state == "ADMIN_DELETE_CONFIRM_TEXT":
        if not is_admin(tg_id):
            await update.message.reply_text("🚫 No tienes permisos.")
            set_state(context, "MENU", {})
            return

        name = sdata.get("name") or ""
        expected = f"ELIMINAR {name}".strip()
        if text != expected:
            await update.message.reply_text("❌ No coincide. Cancelado.", reply_markup=admin_main_kb())
            set_state(context, "ADMIN", {})
            return

        person_id = int(sdata["person_id"])
        ok = admin_delete_person(person_id)
        if ok:
            await update.message.reply_text("💀 Eliminado. Esa plaza ya no existe.", reply_markup=admin_main_kb())
        else:
            await update.message.reply_text("⚠️ No se pudo eliminar (¿ya no existe?).", reply_markup=admin_main_kb())
        set_state(context, "ADMIN", {})
        return

        ok = add_person(text)
        if ok:
            await update.message.reply_text(f"✅ '{text}' añadido como nueva persona.", reply_markup=menu_kb(True))
        else:
            await update.message.reply_text("⚠️ No se pudo añadir (¿ya existe?).", reply_markup=menu_kb(True))
        set_state(context, "MENU", {})
        return

    if state == "SUSPENDED":
        await update.message.reply_text("🚫 Estás suspendido. El admin debe reactivarte.")
        return

    if state == "PENDING":
        await update.message.reply_text("📨 Tu solicitud sigue pendiente. Cuando el admin te asigne una plaza podrás usar el bot.")
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