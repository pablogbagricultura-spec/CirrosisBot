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
    "¡Ese cuerpo pide más!", "¡Dale ahí!", "Sin miedo al éxito.", "Una caña al día es alegría."
]

ACHIEVEMENTS = [50, 100, 200, 500]

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

# --- Helpers ---
def get_state(context): return context.user_data.get("state"), context.user_data.get("data", {})
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
            await update.message.reply_text("No hay huecos libres. Habla con Pablo.")
            return
        await update.message.reply_text("¿Quién eres?", reply_markup=start_kb(persons))
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
        await q.edit_message_text("¿Qué tipo de bebida?", reply_markup=cat_kb())
        set_state(context, "ADD_CAT")
        return
    if data == "back:type":
        cat = sdata.get("cat", "BEER")
        types = list_drink_types(cat)
        await q.edit_message_text("Selecciona bebida:", reply_markup=type_kb(types))
        set_state(context, "ADD_TYPE", sdata)
        return
    if data == "back:qty":
        # CORREGIDO: Ahora vuelve a elegir cantidad
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
        else:
            await q.edit_message_text("Esa persona ya ha sido asignada o no existe.")
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
        if d_str == "today": target = now
        elif d_str == "yesterday": target = now - dt.timedelta(days=1)
        else: target = now - dt.timedelta(days=2)
        
        person = get_assigned_person(tg_id)
        insert_event(person["id"], tg_id, sdata["type_id"], sdata["qty"], target)
        
        await q.edit_message_text(f"{random.choice(FUN_PHRASES)} Registrado para el {target.strftime('%d/%m')}.", reply_markup=menu_kb(is_admin(tg_id)))
        set_state(context, "MENU")
        return

    if data == CB_MENU_REPORT:
        years = list_years_with_data()
        if not years:
            await q.edit_message_text("No hay datos todavía.", reply_markup=menu_kb(is_admin(tg_id)))
            return
        await q.edit_message_text("Selecciona el año:", reply_markup=years_kb(years))
        return

    if data.startswith(CB_YEAR):
        y = int(data.replace(CB_YEAR, ""))
        person = get_assigned_person(tg_id)
        # CORREGIDO: Etiqueta más amigable y lógica de navegación circular
        lines = [f"📊 Informe Año Cervecero {y}", "", "👤 Tu informe personal", person["name"], ""]
        
        # Desglose Personal
        p_rows = person_year_breakdown(person["id"], y)
        if not p_rows:
            lines.append("Sin registros.")
        else:
            for r in p_rows:
                liters_str = f" ({float(r['litros']):.2f}L)" if r["has_liters"] else ""
                lines.append(f"• {r['label']}: {r['unidades']} uds{liters_str}")
            totals = get_person_year_totals(person["id"], y)
            lines.append(f"\nTOTAL: {float(totals['litros']):.2f}L | {float(totals['euros']):.2f}€")

        # Ranking Global
        lines.extend(["", "🌍 Ranking Global (Litros)", ""])
        r_rows = report_year(y)
        for i, r in enumerate(r_rows, 1):
            medal = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else f"{i}."
            lines.append(f"{medal} {r['name']}: {float(r['litros']):.2f}L")

        # PIE DE INFORME DINÁMICO
        years = list_years_with_data()
        if len(years) > 1:
            # Si hay varios años, mostramos el teclado de años para saltar entre ellos
            await q.edit_message_text("\n".join(lines).rstrip(), reply_markup=years_kb(years))
        else:
            await q.edit_message_text("\n".join(lines).rstrip(), reply_markup=menu_kb(is_admin(tg_id)))
        return

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    tg_id = update.effective_user.id
    state, sdata = get_state(context)

    if state == "ADMIN_ADD" and is_admin(tg_id):
        if add_person(text):
            await update.message.reply_text(f"✅ '{text}' añadido.", reply_markup=menu_kb(True))
        else:
            await update.message.reply_text("⚠️ Error al añadir.", reply_markup=menu_kb(True))
        set_state(context, "MENU")
        return

    await update.message.reply_text("Usa el menú o escribe /start")

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()