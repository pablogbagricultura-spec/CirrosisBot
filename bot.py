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
CB_YEAR = "year:"

CB_UNDO_EVENT = "undo_ev:"

FUN_PHRASES = [
    "¡Salud!", "Otra más para el cuerpo.", "¡Qué bien entra!", "¡Viva el zumo de cebada!",
    "¡Ese cuerpo pide más!", "¡Dale ahí!", "Sin miedo al éxito.", "Una caña al día es alegría.",
    "¡Hidrátate!", "¡Esa es buena!", "¡Marchando otra!", "¡No te saltes ninguna!"
]

ACHIEVEMENTS = [1, 5, 10, 25, 50, 100, 250, 500, 750, 1000]

# --- Keyboards ---
def start_kb(persons):
    keys = [[InlineKeyboardButton(p["name"], callback_data=f"{CB_PICK_PERSON}{p['id']}")] for p in persons]
    return InlineKeyboardMarkup(keys)

def menu_kb(admin=False):
    keys = [
        [InlineKeyboardButton("🍺 Añadir Bebida", callback_data=CB_MENU_ADD)],
        [InlineKeyboardButton("📊 Informes", callback_data=CB_MENU_REPORT)],
        [InlineKeyboardButton("↩️ Deshacer Última", callback_data=CB_MENU_UNDO)]
    ]
    if admin:
        keys.append([InlineKeyboardButton("⚙️ Admin", callback_data=CB_MENU_ADMIN)])
    return InlineKeyboardMarkup(keys)

def cat_kb():
    keys = [
        [InlineKeyboardButton("🍺 Cerveza", callback_data=f"{CB_CAT}BEER")],
        [InlineKeyboardButton("🍹 Otros", callback_data=f"{CB_CAT}OTHER")],
        [InlineKeyboardButton("🔙 Atrás", callback_data="back:menu")]
    ]
    return InlineKeyboardMarkup(keys)

def type_kb(types):
    keys = [[InlineKeyboardButton(t["label"], callback_data=f"{CB_TYPE}{t['id']}")] for t in types]
    keys.append([InlineKeyboardButton("🔙 Atrás", callback_data="back:cat")])
    return InlineKeyboardMarkup(keys)

def qty_kb():
    keys = [
        [InlineKeyboardButton("1", callback_data=f"{CB_QTY}1"), InlineKeyboardButton("2", callback_data=f"{CB_QTY}2"), InlineKeyboardButton("3", callback_data=f"{CB_QTY}3")],
        [InlineKeyboardButton("4", callback_data=f"{CB_QTY}4"), InlineKeyboardButton("5", callback_data=f"{CB_QTY}5"), InlineKeyboardButton("6", callback_data=f"{CB_QTY}6")],
        [InlineKeyboardButton("🔙 Atrás", callback_data="back:type")]
    ]
    return InlineKeyboardMarkup(keys)

def date_kb():
    keys = [
        [InlineKeyboardButton("Hoy", callback_data=f"{CB_DATE}today")],
        [InlineKeyboardButton("Ayer", callback_data=f"{CB_DATE}yesterday")],
        [InlineKeyboardButton("Hace 2 días", callback_data=f"{CB_DATE}2days")],
        [InlineKeyboardButton("🔙 Atrás", callback_data="back:qty")]
    ]
    return InlineKeyboardMarkup(keys)

def years_kb(years):
    keys = [[InlineKeyboardButton(str(y), callback_data=f"{CB_YEAR}{y}")] for y in years]
    keys.append([InlineKeyboardButton("🔙 Menú Principal", callback_data="back:menu")])
    return InlineKeyboardMarkup(keys)

def undo_kb(events):
    keys = []
    for e in events:
        label = f"{e['quantity']}x {e['label']} ({e['consumed_at'].strftime('%d/%m')})"
        keys.append([InlineKeyboardButton(label, callback_data=f"{CB_UNDO_EVENT}{e['id']}")])
    keys.append([InlineKeyboardButton("🔙 Atrás", callback_data="back:menu")])
    return InlineKeyboardMarkup(keys)

def admin_kb():
    keys = [
        [InlineKeyboardButton("➕ Añadir Persona", callback_data="admin:add")],
        [InlineKeyboardButton("❌ Quitar Persona", callback_data="admin:remove")],
        [InlineKeyboardButton("🔙 Atrás", callback_data="back:menu")]
    ]
    return InlineKeyboardMarkup(keys)

# --- Helpers ---
def get_state(context):
    return context.user_data.get("state"), context.user_data.get("data", {})

def set_state(context, state, data=None):
    context.user_data["state"] = state
    context.user_data["data"] = data or {}

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    person = get_assigned_person(tg_id)

    if person:
        await update.message.reply_text(f"¡Hola {person['name']}! ¿Qué vamos a beber hoy?", reply_markup=menu_kb(is_admin(tg_id)))
        set_state(context, "MENU")
    else:
        persons = list_available_persons()
        if not persons:
            await update.message.reply_text("No hay huecos libres en el bot ahora mismo. Habla con Pablo.")
            return
        await update.message.reply_text("Hola. ¿Quién eres de esta lista?", reply_markup=start_kb(persons))
        set_state(context, "PICK_PERSON")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    tg_id = q.from_user.id
    state, sdata = get_state(context)

    # BACKS
    if data == "back:menu":
        await q.edit_message_text("Menú principal:", reply_markup=menu_kb(is_admin(tg_id)))
        set_state(context, "MENU")
        return
    if data == "back:cat":
        await q.edit_message_text("¿Qué quieres añadir?", reply_markup=cat_kb())
        set_state(context, "ADD_CAT")
        return
    if data == "back:type":
        cat = sdata.get("cat", "BEER")
        types = list_drink_types(cat)
        await q.edit_message_text("Selecciona la bebida:", reply_markup=type_kb(types))
        set_state(context, "ADD_TYPE", sdata)
        return
    if data == "back:qty":
        # REPARADO: Ahora vuelve al menú de elegir cantidad
        await q.edit_message_text("¿Cuántas has tomado?", reply_markup=qty_kb())
        set_state(context, "ADD_QTY", sdata)
        return

    # LOGICA
    if data.startswith(CB_PICK_PERSON):
        p_id = int(data.replace(CB_PICK_PERSON, ""))
        res, person = assign_person(tg_id, p_id)
        if res == "OK":
            await q.edit_message_text(f"¡Hecho! Ahora eres {person['name']}.", reply_markup=menu_kb(is_admin(tg_id)))
            set_state(context, "MENU")
        elif res == "ALREADY":
            await q.edit_message_text(f"Ya tenías asignado {person['name']}.", reply_markup=menu_kb(is_admin(tg_id)))
            set_state(context, "MENU")
        else:
            await q.edit_message_text("Esa plaza ya ha sido ocupada. Elige otra o habla con Pablo.")
        return

    if data == CB_MENU_ADD:
        await q.edit_message_text("¿Qué quieres añadir?", reply_markup=cat_kb())
        set_state(context, "ADD_CAT")
        return

    if data.startswith(CB_CAT):
        cat = data.replace(CB_CAT, "")
        sdata["cat"] = cat
        types = list_drink_types(cat)
        await q.edit_message_text("¿Qué has bebido?", reply_markup=type_kb(types))
        set_state(context, "ADD_TYPE", sdata)
        return

    if data.startswith(CB_TYPE):
        t_id = int(data.replace(CB_TYPE, ""))
        sdata["type_id"] = t_id
        await q.edit_message_text("¿Cuántas has tomado?", reply_markup=qty_kb())
        set_state(context, "ADD_QTY", sdata)
        return

    if data.startswith(CB_QTY):
        qty = int(data.replace(CB_QTY, ""))
        sdata["qty"] = qty
        await q.edit_message_text("¿Cuándo se bebió?", reply_markup=date_kb())
        set_state(context, "ADD_DATE", sdata)
        return

    if data.startswith(CB_DATE):
        d_str = data.replace(CB_DATE, "")
        now = dt.datetime.now(TZ).date()
        if d_str == "today":
            target = now
        elif d_str == "yesterday":
            target = now - dt.timedelta(days=1)
        else:
            target = now - dt.timedelta(days=2)
        
        person = get_assigned_person(tg_id)
        if not person: return

        # Guardar en DB
        insert_event(person["id"], tg_id, sdata["type_id"], sdata["qty"], target)
        
        await q.edit_message_text(f"{random.choice(FUN_PHRASES)} Registrado para el {target.strftime('%d/%m')}.", reply_markup=menu_kb(is_admin(tg_id)))
        set_state(context, "MENU")

        # LOGROS / AVISOS
        year_start = beer_year_start_for(target)
        totals = get_person_year_totals(person["id"], year_start)
        qty = int(totals["unidades"])
        after_units = qty - sdata["qty"]
        first = is_first_event_of_year(person["id"], year_start)
        
        ach_msgs = check_achievements(person["name"], int(year_start), qty, after_units, first)
        for msg in ach_msgs:
            try:
                await context.bot.send_message(chat_id=tg_id, text=msg)
            except Exception: pass
        return

    if data == CB_MENU_REPORT:
        years = list_years_with_data()
        if not years:
            await q.edit_message_text("No hay datos todavía.", reply_markup=menu_kb(is_admin(tg_id)))
            return
        await q.edit_message_text("Selecciona el año para ver el informe:", reply_markup=years_kb(years))
        return

    if data.startswith(CB_YEAR):
        y = int(data.replace(CB_YEAR, ""))
        person = get_assigned_person(tg_id)
        
        # MODIFICADO: Etiqueta y lógica de navegación dinámica
        lines = [f"📊 Informe Año Cervecero {y}", "", "👤 Tu informe personal (solo tú)", person["name"], ""]
        
        p_rows = person_year_breakdown(person["id"], y)
        if not p_rows:
            lines.append("Sin registros este año.")
        else:
            for r in p_rows:
                liters_str = f" ({float(r['litros']):.2f}L)" if r["has_liters"] else ""
                lines.append(f"• {r['label']}: {r['unidades']} uds{liters_str}")
            totals = get_person_year_totals(person["id"], y)
            lines.append(f"\nTOTAL: {float(totals['litros']):.2f}L | {float(totals['euros']):.2f}€")

        lines.extend(["", "🌍 Ranking Global (Litros)", ""])
        r_rows = report_year(y)
        for i, r in enumerate(r_rows, 1):
            medal = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else f"{i}."
            lines.append(f"{medal} {r['name']}: {float(r['litros']):.2f}L")

        lines.extend(["", "📑 Detalle por Bebida", ""])
        dt_rows = year_drinks_totals(y)
        dt_person_rows = year_drink_type_person_totals(y)
        for dtr in dt_rows:
            label = dtr["label"]
            lit_str = f" ({float(dtr['litros']):.2f}L)" if dtr["has_liters"] else ""
            lines.append(f"• {label}: {dtr['unidades']} uds{lit_str}")
            p_winners = [x for x in dt_person_rows if x["label"] == label]
            if p_winners:
                winner = p_winners[0]
                lines.append(f"  └ Líder: {winner['person_name']} ({winner['unidades']} uds)")

        # MODIFICADO: Si hay más de un año, permitir navegar entre ellos directamente
        years = list_years_with_data()
        markup = years_kb(years) if len(years) > 1 else menu_kb(is_admin(tg_id))
        await q.edit_message_text("\n".join(lines).rstrip(), reply_markup=markup)
        return

    if data == CB_MENU_UNDO:
        person = get_assigned_person(tg_id)
        events = list_last_events(person["id"], 5)
        if not events:
            await q.edit_message_text("No hay eventos recientes para deshacer.", reply_markup=menu_kb(is_admin(tg_id)))
            return
        await q.edit_message_text("Elige el registro a anular:", reply_markup=undo_kb(events))
        return

    if data.startswith(CB_UNDO_EVENT):
        e_id = int(data.replace(CB_UNDO_EVENT, ""))
        person = get_assigned_person(tg_id)
        ok = void_event(person["id"], tg_id, e_id)
        if ok:
            await q.edit_message_text("✅ Registro anulado correctamente.", reply_markup=menu_kb(is_admin(tg_id)))
        else:
            await q.edit_message_text("⚠️ No se pudo anular (o no es tuyo).", reply_markup=menu_kb(is_admin(tg_id)))
        return

    if data == CB_MENU_ADMIN:
        if not is_admin(tg_id):
            await q.edit_message_text("🚫 No tienes permisos.", reply_markup=menu_kb(False))
            return
        await q.edit_message_text("Panel de administración:", reply_markup=admin_kb())
        return

    if data == "admin:add":
        await q.edit_message_text("Escribe el nombre de la nueva persona:")
        set_state(context, "ADMIN_ADD")
        return

    if data == "admin:remove":
        active = list_active_persons()
        keys = [[InlineKeyboardButton(f"❌ {p['name']}", callback_data=f"admin:do_remove:{p['id']}")] for p in active]
        keys.append([InlineKeyboardButton("🔙 Atrás", callback_data=CB_MENU_ADMIN)])
        await q.edit_message_text("Elige a quién desactivar (pasará a INACTIVE):", reply_markup=InlineKeyboardMarkup(keys))
        return

    if data.startswith("admin:do_remove:"):
        p_id = int(data.replace("admin:do_remove:", ""))
        deactivate_person(p_id)
        await q.edit_message_text("✅ Persona desactivada.", reply_markup=admin_kb())
        return

# Mantener tu función de logros intacta
def check_achievements(name, year, current_qty, before_qty, is_first):
    msgs = []
    if is_first:
        msgs.append(f"🎉 ¡{name} inaugura el año cervecero {year}! ¡Salud!")
    for a in ACHIEVEMENTS:
        if before_qty < a <= current_qty:
            msgs.append(f"🏆 ¡LOGRO! {name} ha alcanzado las {a} consumiciones en el año {year}. 🍻")
    return msgs

# Mantener tu Job mensual intacto
async def monthly_summary_job(context: ContextTypes.DEFAULT_TYPE):
    now = dt.datetime.now(TZ)
    if now.day != 1: return
    
    last_month_date = now.replace(day=1) - dt.timedelta(days=1)
    y, m = last_month_date.year, last_month_date.month
    
    if monthly_summary_already_sent(y, m): return

    rows = month_summary(y, m)
    if not rows: return

    header = f"🗓 RESUMEN MENSUAL: {m:02d}/{y}\n"
    total_l = sum(float(r["litros"]) for r in rows)
    total_e = sum(float(r["euros"]) for r in rows)
    total_u = sum(int(r["unidades"]) for r in rows)
    
    body = [f"Total grupo: {total_l:.2f}L | {total_e:.2f}€ | {total_u} uds\n"]
    ranking = sorted(rows, key=lambda x: float(x["litros"]), reverse=True)
    for i, r in enumerate(ranking[:3], 1):
        body.append(f"{i}º {r['name']}: {float(r['litros']):.2f}L")

    full_text = header + "\n".join(body)
    tg_ids = list_active_telegram_user_ids()
    for tid in tg_ids:
        try: await context.bot.send_message(chat_id=tid, text=full_text)
        except Exception: pass

    shame = monthly_shame_report(y, m)
    if shame:
        stext = f"🤡 INFORME DE LA VERGÜENZA - {m:02d}/{y}\n\n"
        stext += f"👑 Campeón: {shame['final_leader']}\n"
        if shame['false_leader']:
            stext += f"🚩 Falso líder: {shame['false_leader']['name']} (iba 1º el día {shame['false_leader']['first_day'].day})\n"
        if shame['biggest_drop']:
            stext += f"📉 Mayor caída: {shame['biggest_drop']['name']} (del {shame['biggest_drop']['best_rank']}º al {shame['biggest_drop']['final_rank']}º)\n"
        if shame['almost_champion']:
            stext += f"🤏 Casi campeón: {shame['almost_champion']['name']} (estuvo a <0.5L del líder {shame['almost_champion']['times']} veces)\n"
        if shame['ghost']:
            stext += f"👻 Fantasma del mes: {shame['ghost']['name']} ({shame['ghost']['blank_days']}/{shame['ghost']['days']} días sin registrar nada)\n"
        if shame['saddest_week']:
            ws = shame['saddest_week']['week_start']
            stext += f"🥀 Semana más triste: la del {ws.strftime('%d/%m')} (solo {shame['saddest_week']['liters']:.2f}L totales)"
        
        for tid in tg_ids:
            try: await context.bot.send_message(chat_id=tid, text=stext)
            except Exception: pass

    mark_monthly_summary_sent(y, m)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state, _ = get_state(context)
    text = update.message.text
    tg_id = update.effective_user.id

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
    app.job_queue.run_daily(
        monthly_summary_job,
        time=dt.time(hour=9, minute=0, tzinfo=TZ),
        name="monthly_summary_daily"
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()