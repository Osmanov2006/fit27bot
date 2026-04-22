import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, MenuButtonWebApp
from telegram.ext import Application, CommandHandler, ContextTypes
 
TOKEN = os.environ.get("BOT_TOKEN", "")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://osmanov2006.github.io/fit-webapp/")
 
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🔥 Открыть FIT TRACKER",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    ]])
    await update.message.reply_text(
        "Нажми кнопку ниже 👇",
        reply_markup=kb
    )
 
async def post_init(app):
    # Синяя кнопка в левом нижем углу чата
    await app.bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="🔥 FIT TRACKER",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    )
 
def main():
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.run_polling(drop_pending_updates=True)
 
if __name__ == "__main__":
    main()
 
