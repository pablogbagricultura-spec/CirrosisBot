import os
import datetime as dt
import random
import calendar
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
    list_calendar_years_with_data,
    user_stats_range,
    user_year_stats,
    group_month_summary,
    drink_type_person_totals_range,
    drink_type_totals_range,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
TZ = ZoneInfo("Europe/Madrid")


def _to_local_dt(ts):
    """Make datetime safe for display in TZ (works with naive/aware)."""
    if ts is None:
        return None
    try:
        if getattr(ts, "tzinfo", None) is None:
            # assume it's already local time
            return ts.replace(tzinfo=TZ)
        return ts.astimezone(TZ)
    except Exception:
        return ts

def _fmt_ts(ts, fmt="%d/%m %H:%M"):
    dtv = _to_local_dt(ts)
    if not dtv:
        return "--:--"
    try:
        return dtv.strftime(fmt)
    except Exception:
        return str(dtv)


# Callbacks
CB_MENU_ADD = "menu:add"
CB_MENU_REPORT = "menu:report"
CB_MENU_RANK = "menu:rank"
CB_MENU_UNDO = "menu:undo"
CB_MENU_ADMIN = "menu:admin"
CB_MENU_PANEL = "menu:panel"
CB_MENU_ROOT = "menu:root"

CB_PANEL_MENU = "panel:menu"
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

CB_RANK_MENU = "rank:menu"
CB_RANK_USERS = "rank:users"
CB_RANK_TYPES = "rank:types"
CB_RANK_USERS_PREV = "rank:users_prev"
CB_RANK_USERS_CURR = "rank:users_curr"

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
        [InlineKeyboardButton("🏆 Ranking", callback_data=CB_MENU_RANK)],
        [InlineKeyboardButton("👤 Panel de usuario", callback_data=CB_MENU_PANEL)],
    ]
    if is_admin_user:
        rows.append([InlineKeyboardButton("⚙️ Administración", callback_data=CB_MENU_ADMIN)])
    return kb(rows)


def rank_menu_kb():
    return kb([
        [InlineKeyboardButton("👥 Ranking por usuarios", callback_data=CB_RANK_USERS)],
        [InlineKeyboardButton("🍺 Ranking por tipos de bebida", callback_data=CB_RANK_TYPES)],
        [InlineKeyboardButton("⬅️ Volver", callback_data=CB_MENU_ROOT)],
    ])

def rank_back_kb():
    return kb([[InlineKeyboardButton("⬅️ Volver a Ranking", callback_data=CB_RANK_MENU)]])

STRONG_DAY_THRESHOLD_L = 3.0

def _legend_strong_day_short() -> str:
    return f"🧨 Día fuerte = día con ≥ {STRONG_DAY_THRESHOLD_L:.1f} L"

def _legend_month_summary_full() -> str:
    return (
        "🧴 Total del mes: litros totales del grupo\n"
        "📆 Días activos: días con al menos un registro\n"
        "📈 Media por día activo: litros / días activos\n"
        "🧃 Promedio diario del mes: litros / días del mes\n"
        "🚫 Días 0: días sin consumo\n"
        f"🧨 Días fuertes: días con ≥ {STRONG_DAY_THRESHOLD_L:.1f} L en total"
    )

def _fmt_l(x: float) -> str:
    return f"{x:.2f} L"

def _fmt_month_es(m: int) -> str:
    return ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"][m-1]

def _week_range(today: dt.date):
    start = today - dt.timedelta(days=today.weekday())
    end = start + dt.timedelta(days=6)
    return start, end

def _month_range(today: dt.date):
    start = dt.date(today.year, today.month, 1)
    last = calendar.monthrange(today.year, today.month)[1]
    end = dt.date(today.year, today.month, last)
    return start, end

def _year_range(year: int):
    return dt.date(year, 1, 1), dt.date(year, 12, 31)


def render_users_block(title: str, rows: list, include_month_extras: bool = False, month_days: int = 0, include_year_extras: bool = False, year: int = None):
    """
    Option A layout (tarjeta por usuario):
      1) "<pos> Nombre — 🧴 X,XX L"
      2) "• métricas principales (orden fijo)"
      3) "• pico ..." (semana/mes) o "• mes fuerte/flojo" (año)
    """
    lines = [title]
    if not rows:
        lines.append("• Nadie ha registrado nada en este periodo.")
        lines.append(_legend_strong_day_short())
        return "\n".join(lines)

    medals = ["🥇", "🥈", "🥉"]
    for i, r in enumerate(rows, start=1):
        prefix = medals[i - 1] if i <= 3 else f"{i}️⃣"

        liters = float(r["liters_total"])
        ad = int(r["active_days"])
        avg_ad = float(r["avg_liters_per_active_day"])
        sd = int(r["strong_days"])

        peak_day = r.get("peak_day")
        peak_l = float(r.get("peak_liters") or 0)

        # Línea 1
        lines.append(f"{prefix} {r['person']} — 🧴 {_fmt_l(liters)}")

        # Línea 2
        parts = [f"📆 {ad} días", f"📈 {_fmt_l(avg_ad)}/día"]

        if include_month_extras and month_days:
            avg_cd = liters / month_days
            z0 = month_days - ad
            parts.append(f"🧃 {_fmt_l(avg_cd)}/día")
            parts.append(f"🚫 {z0} días 0")
        elif include_year_extras and year:
            days_in_year = 366 if calendar.isleap(year) else 365
            avg_year = liters / days_in_year
            parts.append(f"🧃 {_fmt_l(avg_year)}/día")

        parts.append(f"🧨 {sd} días fuertes")
        lines.append("• " + " · ".join(parts))

        # Línea 3
        if include_year_extras and year:
            sm = _fmt_month_es(int(r.get("strongest_month", 1)))
            wm = _fmt_month_es(int(r.get("weakest_month", 1)))
            sm_l = float(r.get("strongest_month_liters", 0))
            wm_l = float(r.get("weakest_month_liters", 0))
            lines.append(f"• 💥 mes más fuerte: {sm} ({_fmt_l(sm_l)}) · 🧊 mes más flojo: {wm} ({_fmt_l(wm_l)})")
        else:
            if peak_day:
                try:
                    d = peak_day.strftime("%d/%m")
                except Exception:
                    d = str(peak_day)
                lines.append(f"• 💥 pico: {d} ({_fmt_l(peak_l)})")

        lines.append("")

    lines.append(_legend_strong_day_short())
    return "\n".join(lines).strip()

def render_users_ranking_current(today: dt.date):
    ws, we = _week_range(today)
    ms, me = _month_range(today)
    week_rows = user_stats_range(ws, we)
    month_rows = user_stats_range(ms, me)
    year_rows = user_year_stats(today.year)
    month_days = (me - ms).days + 1

    parts = ["🏆 Ranking por usuarios", ""]
    parts.append(render_users_block(f"📅 Semana ({ws.strftime('%d/%m')}–{we.strftime('%d/%m')})", week_rows))
    parts.append("")
    parts.append(render_users_block(f"🗓️ Mes ({_fmt_month_es(today.month)} {today.year})", month_rows, include_month_extras=True, month_days=month_days))
    parts.append("")
    parts.append(render_users_block(f"📆 Año ({today.year})", year_rows, include_year_extras=True, year=today.year))
    return "\n".join(parts).strip()

def render_prev_year_extra(year: int):
    year_rows = user_year_stats(year)
    annual = render_users_block(f"📆 Resumen anual ({year})", year_rows, include_year_extras=True, year=year)
    months = group_month_summary(year)

    lines = [f"🗓️ Resumen mensual ({year}) — grupo", ""]
    for r in months:
        m = _fmt_month_es(int(r["month"]))
        liters = float(r["liters_total"])
        ad = int(r["active_days"])
        avg_ad = float(r["avg_per_active_day"])
        avg_cd = float(r["avg_per_calendar_day"])
        z0 = int(r["zero_days"])
        sd = int(r["strong_days"])
        lines.append(f"{m} — 🧴 {_fmt_l(liters)} · 📆 {ad} días · 📈 {_fmt_l(avg_ad)}/día · 🧃 {_fmt_l(avg_cd)}/día · 🚫 {z0} días 0 · 🧨 {sd}")
    lines.append("")
    lines.append(_legend_month_summary_full())
    monthly = "\n".join(lines).strip()
    return annual + "\n\n" + monthly


def render_types_block(title: str, start_date: dt.date, end_date: dt.date, period_label: str):
    totals = drink_type_totals_range(start_date, end_date)
    per_person = drink_type_person_totals_range(start_date, end_date)

    key = lambda r: (r["category"], r["label"])
    totals_map = {(t["category"], t["label"]): t for t in totals}
    persons_map = {}
    for r in per_person:
        persons_map.setdefault(key(r), []).append(r)

    lines = [title]
    if not totals_map:
        lines.append("• No hay consumos en este periodo.")
        return "\n".join(lines)

    for (cat, label), t in totals_map.items():
        has_liters = bool(t["has_liters"])
        header_icon = "🍺" if cat == "BEER" else "🥃"
        lines.append("")
        lines.append(f"{header_icon} {label} ({'litros + cervezas' if has_liters else 'unidades'})")

        users = persons_map.get((cat, label), [])
        if has_liters:
            users.sort(key=lambda r: (float(r["litros"] or 0), int(r["unidades"] or 0), r["person"]), reverse=True)
        else:
            users.sort(key=lambda r: (int(r["unidades"] or 0), r["person"]), reverse=True)

        medals = ["🥇", "🥈", "🥉"]
        for i, u in enumerate(users, start=1):
            prefix = medals[i - 1] if i <= 3 else f"{i}️⃣"
            if has_liters:
                liters = _fmt_l(float(u["litros"] or 0))
                beers = int(u["unidades"] or 0)
                lines.append(f"{prefix} {u['person']} — 🧴 {liters} · 🍺 {beers}")
            else:
                lines.append(f"{prefix} {u['person']} — 🍸 {int(u['unidades'])} uds")

        if has_liters:
            total_l = _fmt_l(float(t["litros"] or 0))
            total_u = int(t["unidades"] or 0)
            lines.append(f"📊 Total {label} {period_label}: 🧴 {total_l} · 🍺 {total_u}")
        else:
            lines.append(f"📊 Total {label} {period_label}: {int(t['unidades'])} uds")

    return "\n".join(lines).strip()

def render_types_ranking_current(today: dt.date):
    ws, we = _week_range(today)
    ms, me = _month_range(today)
    ys, ye = _year_range(today.year)

    parts = ["🏆 Ranking por tipos de bebida", ""]
    parts.append(render_types_block(f"📅 Semana ({ws.strftime('%d/%m')}–{we.strftime('%d/%m')})", ws, we, "semana"))
    parts.append("")
    parts.append(render_types_block(f"🗓️ Mes ({_fmt_month_es(today.month)} {today.year})", ms, me, "mes"))
    parts.append("")
    parts.append(render_types_block(f"📆 Año ({today.year})", ys, ye, "año"))
    return "\n".join(parts).strip()


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
    rows.append([InlineKeyboardButton("⬅️ Volver", callback_data=CB_PANEL_MENU)])
    return kb(rows)

def format_event_line(ev):
    # ev: dict con id, label, quantity, created_at
    stamp = _fmt_ts(ev["created_at"])
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
        when = _fmt_ts(e.get("consumed_at"))
        label = f"{e['quantity']} × {e['label']} — {when}"
        rows.append([InlineKeyboardButton(label, callback_data=f"{CB_UNDO_PICK}{e['id']}")])
    rows.append([InlineKeyboardButton("⬅️ Volver al panel", callback_data="back:panel")])
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

    # -------- RANKING --------
    if data == CB_MENU_RANK or data == CB_RANK_MENU:
        await q.edit_message_text(
            "🏆 Ranking\nElige qué quieres ver:",
            reply_markup=rank_menu_kb()
        )
        return

    if data == CB_MENU_ROOT:
        await q.edit_message_text(
            "📌 Menú principal:",
            reply_markup=menu_kb(is_admin(tg_id))
        )
        return

    if data == CB_RANK_USERS:
        today = dt.datetime.now(TZ).date()
        years = list_calendar_years_with_data()
        prev_year = (today.year - 1) if (today.year - 1) in years else None

        txt = render_users_ranking_current(today)
        rows = []
        if prev_year:
            rows.append([InlineKeyboardButton(f"📆 Ver {prev_year}", callback_data=CB_RANK_USERS_PREV)])
        rows.append([InlineKeyboardButton("⬅️ Volver a Ranking", callback_data=CB_RANK_MENU)])
        await q.edit_message_text(txt, reply_markup=kb(rows))
        return

    if data == CB_RANK_USERS_PREV:
        today = dt.datetime.now(TZ).date()
        prev_year = today.year - 1
        years = list_calendar_years_with_data()
        if prev_year not in years:
            await q.edit_message_text("No hay datos del año anterior.", reply_markup=rank_back_kb())
            return

        txt = "🏆 Ranking por usuarios\n\n" + render_prev_year_extra(prev_year)
        rows = [
            [InlineKeyboardButton(f"📆 Volver a {today.year}", callback_data=CB_RANK_USERS_CURR)],
            [InlineKeyboardButton("⬅️ Volver a Ranking", callback_data=CB_RANK_MENU)],
        ]
        await q.edit_message_text(txt, reply_markup=kb(rows))
        return

    if data == CB_RANK_USERS_CURR:
        today = dt.datetime.now(TZ).date()
        years = list_calendar_years_with_data()
        prev_year = (today.year - 1) if (today.year - 1) in years else None

        txt = render_users_ranking_current(today)
        rows = []
        if prev_year:
            rows.append([InlineKeyboardButton(f"📆 Ver {prev_year}", callback_data=CB_RANK_USERS_PREV)])
        rows.append([InlineKeyboardButton("⬅️ Volver a Ranking", callback_data=CB_RANK_MENU)])
        await q.edit_message_text(txt, reply_markup=kb(rows))
        return

    if data == CB_RANK_TYPES:
        today = dt.datetime.now(TZ).date()
        txt = render_types_ranking_current(today)
        await q.edit_message_text(txt, reply_markup=rank_back_kb())
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

    if data == "back:panel":
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
        if not person and not is_admin(tg_id):
            await q.edit_message_text(
                "⚠️ Aún no estás asignado a ningún usuario. Pide al admin que te asigne.",
                reply_markup=menu_kb(is_admin(tg_id))
            )
            set_state(context, "MENU", {})
            return

        # Muestra las últimas 5 entradas del usuario asignado (más recientes primero)
        events = list_last_events(person["id"], 5) if person else []
        if not events:
            await q.edit_message_text(
                "No tienes entradas recientes para deshacer.",
                reply_markup=user_panel_kb()
            )
            set_state(context, "PANEL", {})
            return

        await q.edit_message_text("Elige cuál quieres eliminar:", reply_markup=undo_list_kb(events))
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
    if data == CB_MENU_PANEL or data == CB_PANEL_MENU:
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
            ("✅ Entrada eliminada." if ok else "⚠️ No se pudo eliminar.") + "\n\n👤 Panel de usuario",
            reply_markup=user_panel_kb(),
        )
        set_state(context, "PANEL", {})
        return

    if data == CB_UNDO_CANCEL:
        person = get_assigned_person(tg_id)
        await q.edit_message_text(
            "Vale, no toco nada 🙂\n\n👤 Panel de usuario",
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
