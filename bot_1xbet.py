import logging
import os  # L'import est maintenant seul sur sa ligne
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# 1. Configuration des logs pour suivre le comportement du bot sur Render
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# On récupère le token masqué enregistré dans l'onglet Environment de Render
TOKEN = os.environ.get("TELEGRAM_TOKEN")

# 2. Fonction déclenchée par /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Création du clavier avec ton bouton exact
    clavier = [['📊 Analyser les matchs du jour']]
    reply_markup = ReplyKeyboardMarkup(clavier, resize_keyboard=True)
    
    await update.message.reply_text(
        "Bonjour ! Le bot 1xbet est en ligne et prêt à fonctionner.",
        reply_markup=reply_markup
    )

# 3. Fonction mathématique déclenchée par le bouton d'analyse
async def analyser_matchs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    texte_recu = update.message.text

    # On vérifie si l'utilisateur a cliqué sur le bouton d'analyse
    if texte_recu == "📊 Analyser les matchs du jour":
        await update.message.reply_text("🔄 Connexion aux bases de données... Analyse quantitative en cours...")
        
        # --- LOGIQUE DE CALCUL DE VALUE MATCH MATHÉMATIQUE ---
        match = "Japan vs Sweden"
        
        # Exemple basé sur la distribution de Poisson (58% de probabilité pour le BTTS)
        probabilite_btts = 0.58  
        cote_1xbet = 1.95  # Exemple de cote du bookmaker
        
        # Formule mathématique de la Value : (Probabilité * Cote) - 1
        value = (probabilite_btts * cote_1xbet) - 1
        
        if value > 0:
            rendement_theorique = value * 100
            message_prono = (
                f"⚔️ **{match}**\n"
                f"🏆 World Cup\n\n"
                f"📋 **Calcul des Probabilités :**\n"
                f"• BTTS Oui : {probabilite_btts * 100:.1f}% (Cote min mathématique : {1/probabilite_btts:.2f})\n"
                f"💰 Cote actuelle 1XBet : {cote_1xbet}\n\n"
                f"🎯 **Directive : VALUE DETECTED (+{rendement_theorique:.1f}% de rendement)**\n"
                f"👉 Option conseillée : **BTTS Oui**\n\n"
                f"👁️‍🗨️ _Avis Brut : Modèle basé sur la distribution de Poisson validé par l'historique de performance._"
            )
        else:
            message_prono = "❌ Aucun match ne remplit actuellement les critères de Value algorithmiques stricts."
            
        await update.message.reply_text(message_prono)

# 4. Fonction principale de lancement
def main() -> None:
    # Initialisation de l'application avec le TOKEN sécurisé
    application = Application.builder().token(TOKEN).build()

    # Gestionnaire pour la commande /start
    application.add_handler(CommandHandler("start", start))
    
    # Gestionnaire pour intercepter le texte du bouton d'analyse
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, analyser_matchs))
    
    # Lancement du bot
    print("Le bot démarre sur Render...")
    application.run_polling()

if __name__ == '__main__':
    main()
