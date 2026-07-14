import logging
import os
import math
import asyncio
import datetime as dt
import aiohttp
from aiohttp import web
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Configuration des logs
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def nettoyer_variable(nom_variable):
    valeur = os.environ.get(nom_variable)
    if valeur:
        return valeur.replace('\n', '').replace('\r', '').strip()
    return None

TOKEN = nettoyer_variable("TELEGRAM_TOKEN")
API_FOOTBALL_KEY = nettoyer_variable("API_FOOTBALL_KEY")
BASE_URL = "https://v3.football.api-sports.io"

# ID des ligues majeures pour cibler en priorité les compétitions d'élite
LIGUES_CIBLES = [39, 61, 140, 135, 78, 2, 3, 848]

def probabilite_poisson(k, laambda):
    if laambda <= 0: 
        laambda = 0.01
    return (pow(laambda, k) * math.exp(-laambda)) / math.factorial(k)

# --- CALCULATEUR DE POISSON ---
def executer_modele_poisson(goals_home_avg, goals_away_avg, nom_home, nom_away):
    lambda_home = float(goals_home_avg) if goals_home_avg else 1.40
    mu_away = float(goals_away_avg) if goals_away_avg else 1.20

    prob_1, prob_N, prob_2 = 0.0, 0.0, 0.0
    prob_btts, prob_over25 = 0.0, 0.0
    scores = {}
    
    for h in range(6):
        for a in range(6):
            p_h = probabilite_poisson(h, lambda_home)
            p_a = probabilite_poisson(a, mu_away)
            p_score = p_h * p_a
            if h > a: prob_1 += p_score
            elif h == a: prob_N += p_score
            else: prob_2 += p_score
            if h > 0 and a > 0: prob_btts += p_score
            if (h + a) >= 3: prob_over25 += p_score
            scores[f"{h}-{a}"] = p_score

    scores_tries = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_scores = [f"{sc[0]} ({round(sc[1]*100, 1)}%)" for sc in scores_tries[:2]]

    return {
        "p1": int(prob_1 * 100),
        "pN": int(prob_N * 100),
        "p2": int(prob_2 * 100),
        "btts": int(prob_btts * 100),
        "over25": int(prob_over25 * 100),
        "scores": top_scores,
        "lambda": round(lambda_home, 2),
        "mu": round(mu_away, 2)
    }

# --- EXTRACTEUR DE DONNÉES ---
async def fetch_predictions_pour_match(session, fixture_id, headers):
    url = f"{BASE_URL}/predictions"
    params = {"fixture": fixture_id}
    try:
        async with session.get(url, headers=headers, params=params, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                predictions = data.get("response", [])
                if predictions:
                    return predictions[0]
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des prédictions pour {fixture_id}: {e}")
    return None

async def recuperer_matchs_du_jour():
    if not API_FOOTBALL_KEY:
        logger.error("Clé API-Football manquante.")
        return []
        
    headers = {
        'x-apisports-key': API_FOOTBALL_KEY,
        'x-rapidapi-host': 'v3.football.api-sports.io'
    }
    
    date_string = dt.datetime.now().strftime('%Y-%m-%d')
    matchs_analyses = []
    
    async with aiohttp.ClientSession() as session:
        url_fixtures = f"{BASE_URL}/fixtures"
        params = {"date": date_string}
        
        try:
            async with session.get(url_fixtures, headers=headers, params=params, timeout=10) as resp:
                if resp.status != 200:
                    logger.error(f"Erreur API Fixtures : {resp.status}")
                    return []
                
                data = await resp.json()
                fixtures = data.get("response", [])
                
                # 1. On cherche d'abord dans les ligues d'élite
                fixtures_filtrees = [
                    f for f in fixtures 
                    if f.get("league", {}).get("id") in LIGUES_CIBLES 
                    and f.get("fixture", {}).get("status", {}).get("short") == "NS"
                ]
                
                # 2. Repli : si vide, on prend toutes les ligues disponibles
                if not fixtures_filtrees:
                    logger.info("Recherche élargie...")
                    fixtures_filtrees = [
                        f for f in fixtures 
                        if f.get("fixture", {}).get("status", {}).get("short") == "NS"
                    ]
                
                # On limite aux 3 premiers matchs pour économiser l'API
                for f in fixtures_filtrees[:3]:
                    fixture_id = f["fixture"]["id"]
                    home = f["teams"]["home"]
                    away = f["teams"]["away"]
                    league = f["league"]
                    
                    prediction_data = await fetch_predictions_pour_match(session, fixture_id, headers)
                    
                    # Valeurs par défaut sécurisées au cas où l'API n'a pas de prédiction pour ce match
                    goals_home_avg = 1.40
                    goals_away_avg = 1.20
                    api_advice = "Double chance (Calcul local)"
                    percent_api = {"home": "33", "draw": "33", "away": "33"}
                    h2h_list = []

                    if prediction_data:
                        stats_home = prediction_data.get("teams", {}).get("home", {}).get("league", {}).get("goals", {})
                        stats_away = prediction_data.get("teams", {}).get("away", {}).get("league", {}).get("goals", {})
                        
                        goals_home_avg = stats_home.get("for", {}).get("average", {}).get("home", 1.40)
                        goals_away_avg = stats_away.get("for", {}).get("average", {}).get("away", 1.20)
                        
                        api_advice = prediction_data.get("predictions", {}).get("advice", "Analyse neutre")
                        percent_api = prediction_data.get("predictions", {}).get("percent", {"home": "33", "draw": "33", "away": "33"})
                        h2h_list = prediction_data.get("h2h", [])[:3]
                    
                    # Calcul Poisson (tourne toujours, même sans données API tierces !)
                    poisson = executer_modele_poisson(goals_home_avg, goals_away_avg, home["name"], away["name"])
                    
                    matchs_analyses.append({
                        "home": home["name"],
                        "away": away["name"],
                        "league": league.get("name", "Ligue"),
                        "country": league.get("country", "Monde"),
                        "heure": f.get("fixture", {}).get("date", "")[11:16],
                        "stade": f.get("fixture", {}).get("venue", {}).get("name", "Stade non spécifié"),
                        "poisson": poisson,
                        "api_advice": api_advice,
                        "api_percent": percent_api,
                        "h2h": h2h_list
                    })
                        
        except Exception as e:
            logger.error(f"Erreur globale lors du scan : {e}")
            
    return matchs_analyses

# --- INTERFACE BOT TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clavier = [['📊 Analyser les matchs du jour']]
    reply_markup = ReplyKeyboardMarkup(clavier, resize_keyboard=True)
    await update.message.reply_text(
        "⚡ *Moteur IA Ultra-Précis Activé.*\n\n"
        "Prêt à analyser les meilleures affiches du jour en combinant les modèles de Poisson et les données prédictives.",
        reply_markup=reply_markup, parse_mode="Markdown"
    )

async def analyser_matchs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.text == "📊 Analyser les matchs du jour":
        await update.message.reply_text("⏳ Scan, extraction et calcul des probabilités en cours...")
        matchs_valides = await recuperer_matchs_du_jour()
        
        if not matchs_valides:
            await update.message.reply_text(
                "ℹ️ *Aucun match restant à jouer aujourd'hui sur l'API.*", 
                parse_mode="Markdown"
            )
            return
        
        for idx, m in enumerate(matchs_valides, 1):
            poi = m["poisson"]
            api_pct = m["api_percent"]
            
            h2h_text = ""
            for h in m["h2h"]:
                h_home = h.get("teams", {}).get("home", {}).get("name", "")
                h_away = h.get("teams", {}).get("away", {}).get("name", "")
                h_score = h.get("goals", {})
                h2h_text += f"• {h_home} {h_score.get('home', '?')}-{h_score.get('away', '?')} {h_away}\n"
            
            if not h2h_text:
                h2h_text = "Aucun historique disponible pour ces équipes."

            message_match = (
                f"⚔️ *MATCH {idx}/{len(matchs_valides)} : {m['home']} vs {m['away']}*\n"
                f"🏆 Compétition : *{m['country']} - {m['league']}*\n"
                f"⏰ Heure : *{m['heure']}* | Lieu : {m['stade']}\n\n"
                f"📊 *1) Loi de Poisson (Calculs locaux) :*\n"
                f"• Victoire {m['home']} : {poi['p1']}%\n"
                f"• Match Nul : {poi['pN']}%\n"
                f"• Victoire {m['away']} : {poi['p2']}%\n"
                f"• Scores probables : {', '.join(poi['scores'])}\n"
                f"• Les deux marquent : {poi['btts']}% | Plus de 2.5 : {poi['over25']}%\n\n"
                f"📉 *2) Données Prédictives :*\n"
                f"• Distribution : {api_pct.get('home', '33')}% | {api_pct.get('draw', '33')}% | {api_pct.get('away', '33')}%\n"
                f"• Tendance : {m.get('api_advice', 'N/A')}\n\n"
                f"🔄 *3) Confrontations Directes (H2H) :*\n"
                f"{h2h_text}\n"
                f"🎯 *SYNTHÈSE DU MODÈLE ENSEMBLE :*\n"
                f"👉 *Conseil Recommandé : {m['api_advice']}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            await update.message.reply_text(message_match, parse_mode="Markdown")
            await asyncio.sleep(1)

# --- SERVEUR WEB ASYNC ---
async def handle_ping(request): 
    return web.Response(text="Bot en ligne")

async def main():
    if not TOKEN: 
        logger.error("Token Telegram manquant.")
        return

    token_propre = "".join(TOKEN.split())
    
    web_app = web.Application()
    web_app.router.add_get('/', handle_ping)
    runner = web.AppRunner(web_app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

    application = Application.builder().token(token_propre).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, analyser_matchs))
    
    async with application:
        await application.initialize()
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        
        while True: 
            await asyncio.sleep(3600)

if __name__ == '__main__':
    asyncio.run(main())
