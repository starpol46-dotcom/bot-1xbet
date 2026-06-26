import logging
import os
import requests
import asyncio
from datetime import datetime
from aiohttp import web
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Configuration du système de journalisation (Logs de production)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Récupération sécurisée des variables d'environnement (Injection de dépendance)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
API_KEY = os.environ.get("API_KEY")

# Vérification stricte des variables d'environnement au démarrage
if not TELEGRAM_TOKEN or not API_KEY:
    logger.critical("Erreur fatale : TELEGRAM_TOKEN ou API_KEY non configurés dans l'environnement.")
    raise ValueError("Les variables d'environnement doivent être configurées.")

URL_API = "https://v3.football.api-sports.io/fixtures"
URL_PREDICTIONS = "https://v3.football.api-sports.io/predictions"
headers = {
    "x-apisports-key": API_KEY,
    "Accept": "application/json"
}

# Base de connaissances et profiler des compétitions (Modulateur de variance)
PROFIL_LIGUES = {
    1:   ["FIFA World Cup", 0.88, 0.85, 1.15],       # Rencontres internationales tactiques serrées
    39:  ["Premier League", 1.12, 1.08, 0.95],      # Haute intensité offensive, buts réguliers
    61:  ["Ligue 1", 0.92, 0.94, 1.10],             # Rigueur défensive et tactique accrue
    113: ["Division 1 (Suède)", 1.18, 1.15, 0.90],  # Championnat scandinave ouvert
    172: ["Série B (Italie)", 0.82, 0.80, 1.25],   # Système de blocs bas défensifs
    357: ["EFL League Two", 1.05, 1.04, 1.00]       # Transition physique directe
}
LIGUES_CIBLES = list(PROFIL_LIGUES.keys())

# --- SERVEUR WEB ASYNC POUR SÉCURISER RENDER (PORT BINDING) ---
async def handle_ping(request):
    return web.Response(text="Moteur prédictif en ligne", status=200)

async def demarrer_serveur_ping():
    web_app = web.Application()
    web_app.router.add_get('/', handle_ping)
    port = int(os.environ.get("PORT", 10000))
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Serveur de ping réseau actif et lié au port : {port}")

# --- ENGIN ANALYTIQUE QUANTITATIF (PONDÉRÉ & DÉCODÉ) ---
def engin_analytique_pro(att_h, def_h, att_a, def_a, poss_h, league_id, nom_comp):
    """
    Simule la probabilité conjointe de l'Over 2.5 et du BTTS.
    Applique des correctifs d'asymétrie de terrain et de profil de ligue.
    """
    profil = PROFIL_LIGUES.get(league_id, ["Inconnu", 1.0, 1.0, 1.0])
    mult_over, mult_btts, densite_def = profil[1], profil[2], profil[3]
    
    # Prise en compte de l'asymétrie de l'avantage du terrain à domicile
    force_offensive_home = att_h * 1.05  
    force_defensive_away = def_a * densite_def * 1.03   
    force_offensive_away = att_a
    force_defensive_home = def_h * densite_def
    
    # Calcul des indices de dangerosité respectifs
    danger_home = max(5.0, force_offensive_home - (force_defensive_away * 0.4))
    danger_away = max(5.0, force_offensive_away - (force_defensive_home * 0.4))
    
    # Estimation de la tendance globale du flux de buts (Loi de Poisson révisée)
    indice_spectacle = (danger_home + danger_away) / 2
    if indice_spectacle > 50:
        base_over = 55.0 + (indice_spectacle - 50) * 1.1
    else:
        base_over = 55.0 - (50 - indice_spectacle) * 1.3
        
    # Estimation de la réciprocité offensive (BTTS)
    if danger_home > 42.0 and danger_away > 42.0:
        base_btts = 65.0 + ((danger_home + danger_away) / 4)
    else:
        base_btts = 45.0 + (danger_home - danger_away) * 0.3

    # Correctif enjeu de match de coupe (blocs qui se recroquevillent)
    if "Cup" in nom_comp or "Coupe" in nom_comp or league_id == 1:
        base_over *= 0.90  
        base_btts *= 0.92

    # Normalisation finale par le coefficient du championnat
    proba_over_finale = max(5.0, min(95.0, base_over * mult_over))
    proba_btts_finale = max(5.0, min(95.0, base_btts * mult_btts))
    
    # Modulateur stratégique de domination de possession
    if poss_h > 60.0 or poss_h < 40.0:
        proba_over_finale += 4.5
    
    return proba_over_finale, proba_btts_finale

def extraire_metrique_pro(comp_dict, cle_marche):
    """Sûreté d'extraction et de conversion des données brutes de l'API."""
    if not comp_dict or cle_marche not in comp_dict:
        return 50.0, 50.0
    try:
        h_raw = comp_dict.get(cle_marche, {}).get("home", 50)
        a_raw = comp_dict.get(cle_marche, {}).get("away", 50)
        h_val = float(str(h_raw).replace('%', '')) if h_raw is not None else 50.0
        a_val = float(str(a_raw).replace('%', '')) if a_raw is not None else 50.0
        return h_val, a_val
    except Exception:
        return 50.0, 50.0

# --- HANDLERS TELEGRAM (BOT INTERACTIF) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clavier = [['📊 Analyser les matchs du jour']]
    reply_markup = ReplyKeyboardMarkup(clavier, resize_keyboard=True)
    await update.message.reply_text(
        "👋 *Bienvenue sur ton Moteur de Prédictions Élite 1xBet !*\n\n"
        "Clique sur le bouton ci-dessous pour lancer l'analyse quantitative en temps réel.",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def analyser_matchs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    texte_recu = update.message.text

    if texte_recu == "📊 Analyser les matchs du jour":
        await update.message.reply_text("📡 *Connexion à l'API-Football... Analyse des flux en cours...*", parse_mode="Markdown")
        
        date_aujourdhui = datetime.now().strftime("%Y-%m-%d")
        matchs_envoyes = 0
        
        try:
            # Récupération du calendrier réel de la journée
            querystring = {"date": date_aujourdhui}
            response = requests.get(URL_API, headers=headers, params=querystring, timeout=10)
            
            if response.status_code != 200:
                await update.message.reply_text("❌ Échec de la récupération des matchs du jour (Erreur API).")
                return
                
            matchs = response.json().get("response", [])
            
            for m in matchs:
                statut = m["fixture"]["status"]["short"]
                id_ligue = m["league"]["id"]
                
                # Limitation rigoureuse aux ligues ciblées et aux matchs à venir
                if id_ligue in LIGUES_CIBLES and statut == "NS" and matchs_envoyes < 3:
                    home = m["teams"]["home"]["name"]
                    away = m["teams"]["away"]["name"]
                    match_id = m["fixture"]["id"]
                    nom_competition = m["league"]["name"]
                    heure_gmt = m["fixture"]["date"].split("T")[1][:5]
                    
                    try:
                        # Appel à la prédiction asymétrique de l'API
                        res_pred = requests.get(URL_PREDICTIONS, headers=headers, params={"fixture": match_id}, timeout=10)
                        data_response = res_pred.json().get("response", [])
                        
                        if not data_response:
                            continue
                            
                        comp = data_response[0].get("comparison", {})
                        if not comp:
                            continue
                        
                        # Traitement des métriques réelles d'organisation
                        att_home, att_away = extraire_metrique_pro(comp, "att")
                        def_home, def_away = extraire_metrique_pro(comp, "def")
                        poss_home, _ = extraire_metrique_pro(comp, "po")
                        
                        # Exécution du modèle de décision
                        proba_over, proba_btts = engin_analytique_pro(
                            att_home, def_home, att_away, def_away, poss_home, id_ligue, nom_competition
                        )
                        
                        cote_over = 100 / proba_over
                        cote_btts = 100 / proba_btts
                        
                        # Calcul de l'alerte de value
                        alerte_marche = "⚖️ [ZONE NEUTRE] : Équilibre structurel."
                        if proba_over > 65.0:
                            alerte_marche = f"🔥 [VALUE OVER 2.5] : Chercher une cote > {cote_over:.2f}"
                        elif proba_over < 36.0:
                            proba_under = 100 - proba_over
                            alerte_marche = f"🛡️ [VALUE UNDER 2.5] : Chercher une cote > {100/proba_under:.2f}"
                        
                        # Modélisation de l'avis tactique
                        analyse_brute = "Blocs défensifs supérieurs aux attaques. Match fermé attendu."
                        if att_home > def_away and att_away > def_home:
                            analyse_brute = "Les deux attaques surclassent les défenses. Potentiel de buts élevé !"
                        elif att_home > def_away:
                            analyse_brute = f"Domination unilatérale nette attendue pour {home}."
                        elif att_away > def_home:
                            analyse_brute = f"Danger extérieur ! {away} dispose des armes pour rompre le bloc adverse."

                        # Construction du rapport de prédiction
                        rapport_telegram = (
                            f"🤖 *ALERTE VALUE BET IA* (ID: {match_id})\n\n"
                            f"🏆 *Ligue* : {nom_competition} (ID: {id_ligue})\n"
                            f"⚔️ *Match* : {home} vs {away}\n"
                            f"⏰ *Horaire* : {heure_gmt} GMT\n\n"
                            f"📊 *Indicateurs de Performance* :\n"
                            f"• {home} (Dom) : Att {att_home:.0f}% / Def {def_home:.0f}%\n"
                            f"• {away} (Ext) : Att {att_away:.0f}% / Def {def_away:.0f}%\n\n"
                            f"🔮 *Lignes de Value Théoriques* :\n"
                            f"👉 *Over 2.5 buts* : {proba_over:.1f}%  |  Cote Min : *{cote_over:.2f}*\n"
                            f"👉 *BTTS Oui* : {proba_btts:.1f}%  |  Cote Min : *{cote_btts:.2f}*\n\n"
                            f"🎯 *Directive Algorithme* : {alerte_marche}\n"
                            f"👁️ *Analyse Tactique* : {analyse_brute}\n"
                            f"____________________________________"
                        )
                        
                        await update.message.reply_text(rapport_telegram, parse_mode="Markdown")
                        matchs_envoyes += 1
                        
                    except Exception as inner_error:
                        logger.error(f"Erreur d'analyse sur le match {match_id} : {inner_error}")
                        continue

            if matchs_envoyes == 0:
                await update.message.reply_text("ℹ️ Aucun match à venir éligible aux critères stricts pour le moment aujourd'hui.")
            else:
                await update.message.reply_text(f"✅ Analyse terminée. {matchs_envoyes} opportunités fiables détectées.")
                
        except Exception as e:
            logger.error(f"Erreur générale d'extraction : {e}")
            await update.message.reply_text("❌ Une erreur est survenue lors de l'extraction des données.")

# --- INITIALISATION ET EVENT LOOP LIFECYCLE ---
async def main():
    # 1. Démarrage parallèle du serveur de ping pour Render
    await demarrer_serveur_ping()
    
    # 2. Configuration et démarrage du bot Telegram
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, analyser_matchs))
    
    # Utilisation du gestionnaire de contexte asynchrone pour éviter les exceptions de boucle d'événements
    async with application:
        await application.initialize()
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        logger.info("Moteur prédictif et Polling Telegram démarrés sur Render.")
        
        # Maintien de l'event loop active indéfiniment
        while True:
            await asyncio.sleep(3600)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot arrêté par l'utilisateur.")
