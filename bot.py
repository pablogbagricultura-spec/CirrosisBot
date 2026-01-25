import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from db import init_db, get_assigned_person, list_available_persons, assign_person

BOT_TOKEN = os.environ["BOT_TOKEN"]

CB_PICK_PREFIX = "pick_person:"

def pick_person_keyboard(rows):
    # rows: [{"id":..,"name":..}, ...]
    keyboard = []
    for r in rows:
        keyboard.append([InlineKeyboardButton(r["name"], callback_data=f"{CB_PICK_PREFIX}{r['id']}")])
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id

    # ¿Ya asignado?
    person = get_assigned_person(tg_id)
    if person:
        await update.message.reply_text(f"👋 Hola, {person['name']}.\n\n¿Que quieres hacer ahora? (siguiente paso)")
        return

    # No asignado: mostrar plazas libres
    available = list_available_persons()
    if not available:
        await update.message.reply_text("🚫 Acceso restringido.\nNo quedan plazas libres en CirrosisBot.")
        return

    await update.message.reply_text(
        "👤 ¿Quién eres? (elige tu nombre)\n\n⚠️ Esto solo se hace una vez.",
        reply_markup=pick_person_keyboard(available),
    )

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    tg_id = q.from_user.id
    data = q.data or ""

    if data.startswith(CB_PICK_PREFIX):
        await q.answer()
        person_id = int(data.split(":", 1)[1])

        status, person = assign_person(tg_id, person_id)

        if status in ("OK", "ALREADY"):
            await q.edit_message_text(f"✅ Listo. Te has registrado como **{person['name']}**.", parse_mode="Markdown")
            return

        if status == "TAKEN":
            # Recargar lista por si alguien la pilló justo antes
            available = list_available_persons()
            if not available:
                await q.edit_message_text("🚫 Esa plaza ya no está disponible y no quedan plazas libres.")
            else:
                await q.edit_message_text(
                    "⚠️ Esa plaza ya fue ocupada. Elige otra:",
                    reply_markup=pick_person_keyboard(available),
                )
            return

    # callback de prueba (si quieres mantenerlo)
    if data == "hello":
        await q.answer()
        await q.edit_message_text("✅ Bot funcionando correctamente.")

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.run_polling()

if __name__ == "__main__":
    main()
