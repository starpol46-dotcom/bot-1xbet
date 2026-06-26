import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Configuration des logs pour voir si tout fonctionne
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Fonction qui s'exécute quand on tape /start dans Telegram
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Bonjour ! Le bot 1xbet est en ligne et prêt à fonctionner.")

def main() -> None:
    # Ton token Telegram exact
    TOKEN = "8968865656:AAFeQ8dI5iEG5xtwI7O1TjcdVRPi_t6h8Js"

    
    # Création de l'application (sans proxy !)
    application = Application.builder().token(TOKEN).build()

    # Ajout de la commande /start
    application.add_handler(CommandHandler("start", start))

    # Lancement du bot
    print("Le bot démarre sur Render...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
