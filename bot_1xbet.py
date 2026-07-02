import logging
import os
import math
import requests
import asyncio
import datetime as dt
from aiohttp import web
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Configuration des logs
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

def nettoyer_variable(nom_variable):
    valeur = os.environ.get(nom_variable)
    if valeur:
        return valeur.replace('\n', '').replace('\r', '').strip()
    return None

TOKEN = nettoyer_variable("TELEGRAM_TOKEN")
API_FOOTBALL_KEY = nettoyer_variable("API_FOOTBALL_KEY")

# --- FORMULE MATHÉMATIQUE DE POISSON ---
def probabilite_poisson(k, laambda):
    if laambda <= 0: laambda = 0.01
    return (pow(laambda, k) * math.exp(-laambda)) / math.factorial(k)

# --- MOTEUR DE CALCUL EXPERT EN MATRICE ÉTENDUE ---
def analyser_match_expert(team_home_id, team_away_id, nom_home, nom_away, league_id, date_match):
    url = "https://v3.football.api-sports.io/teams/statistics"
    headers = {
        'x-rapidapi-key': API_FOOTBALL_KEY,
        'x-rapidapi-host': 'v3.football.api-sports.io'
    }
    
    # Calcul de la saison dynamique
    date_obj = dt.datetime.strptime(date_match.split('T')[0], '%Y-%m-%d')
    saison = date_obj.year if league_id in [1, 4] else (date_obj.year - 1 if date_obj.month < 7 else date_obj.year)
    
    lambda_home = 1.45  
    mu_away = 1.05
    
    if team_home_id and team_away_id and API_FOOTBALL_KEY:
        try:
            res_home = requests.get(f"{url}?league={league_id}&season={saison}&team={team_home_id}", headers=headers, timeout=5).json()
            res_away = requests.get(f"{url}?league={league_id}&season={saison}&team={team_away_id}", headers=headers, timeout=5).json()
            
            form_home_goals = res_home.get("response", {}).get("goals", {}).get("for", {}).get("average", {}).get("home")
            form_away_goals = res_away.get("response", {}).get("goals", {}).get("for", {}).get("average", {}).get("away")
            
            if form_home_goals: lambda_home = float(form_home_goals)
            if form_away_goals: mu_away = float(form_away_goals)
        except Exception as e:
            logging.error(f"Erreur calculs stats API : {e}")

    prob_1, prob_N, prob_2 = 0.0, 0.0, 0.0
    prob_btts_oui, prob_over_25, prob_over_35 = 0.0, 0.0, 0.0
    scores = {}
    
    for h in range(8):
        for a in range(8):
            p_h = probabilite_poisson(h, lambda_home)
            p_a = probabilite_poisson(a, mu_away)
            p_score = p_h * p_a
            
            if h > a: prob_1 += p_score
            elif h == a: prob_N += p_score
            else: prob_2 += p_score
            
            if h > 0 and a > 0: prob_btts_oui += p_score
            if (h + a) >= 3: prob_over_25 += p_score
            if (h + a) >= 4: prob_over_35 += p_score
            scores[f"{h}-{a}"] = p_score

    scores_tries = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_scores = [f"{sc[0]} ({round(sc[1]*100, 1)}%)" for sc in scores_tries[:2]]
    
    total_1N2 = prob_1 + prob_N + prob_2 if (prob_1 + prob_N + prob_2) > 0 else 1
    p1, pN, p2 = prob_1 / total_1N2, prob_N / total_1N2, prob_2 / total_1N2
    
    ct1 = round(1 / p1, 2) if p1 > 0 else 99.0
    ctN = round(1 / pN, 2) if pN > 0 else 99.0
    ct2 = round(1 / p2, 2) if p2 > 0 else 99.0
    ct_btts = round(1 / prob_btts_oui, 2) if prob_btts_oui > 0 else 99.0
    ct_o25 = round(1 / prob_over_25, 2) if prob_over_25 > 0 else 99.0
    ct_o35 = round(1 / prob_over_35, 2) if prob_over_35 > 0 else 99.0

    options_valides = [
        {"nom": f"Victoire {nom_home}", "prob": int(p1*100), "cote_th": ct1, "desc": "Dynamique offensive à domicile supérieure."},
        {"nom": f"Victoire {nom_away}", "prob": int(p2*100), "cote_th": ct2, "desc": "Supériorité nette à l'extérieur sur la matrice."},
        {"nom": "Les deux équipes marquent", "prob": int(prob_btts_oui*100), "cote_th": ct_btts, "desc": "Faible densité de clean sheets de part d'autre."},
        {"nom": "Plus de 2.5 Buts", "prob": int(prob_over_25*100), "cote_th": ct_o25, "desc": "Espérance de buts cumulée élevée."},
        {"nom": "Plus de 3.5 Buts 🔥", "prob": int(prob_over_35*100), "cote_th": ct_o35, "desc": "Modèle Poisson centré sur un score fleuve (Grosse Cote)."}
    ]
    
    grosses_cotes_viables = [o for o in options_valides if o["prob"] >= 38 and o["cote_th"] >= 1.90]
    
    if grosses_cotes_viables:
        recommandation = max(grosses_cotes_viables, key=lambda x: x["prob"])
    else:
        recommandation = max(options_valides, key=lambda x: x["prob"])
    
    return {
        "p1": int(p1*100), "pN": int(pN*100), "p2": int(p2*100),
        "cote_th1": ct1, "cote_thN": ctN, "cote_th2": ct2,
        "scores": top_scores, "btts": int(prob_btts_oui*100), "over25": int(prob_over_25*100),
        "recommandation": recommendation
    }

# --- FILTRE ET EXTRACTION SÉCURISÉE DES VRAIS MATCHS A VENIR ---
def recuperer_vrais_matchs():
    if not API_FOOTBALL_KEY or API_FOOTBALL_KEY == "METS_TA_CLE_API_ICI":
        logging.warning("Clé API-Football manquante.")
        return []
        
    headers = {
        'x-rapidapi-key': API_FOOTBALL_KEY,
        'x-rapidapi-host': 'v3.football.api-sports.io'
    }
    
    matchs_reels = []
    
    # Correction : Requête directe sur la Coupe du Monde (ID 1) pour la saison 2026, statut non démarré (NS)
    try:
        url = "https://v3.football.api-sports.io/fixtures?league=1&season=2026&status=NS"
        response = requests.get(url, headers=headers, timeout=10).json()
        matchs = response.get("response", [])
        
        for m in matchs:
            matchs_reels.append({
                "home": m.get("teams", {}).get("home", {}).get("name"),
                "home_id": m.get("teams", {}).get("home", {}).get("id"),
                "away": m.get("teams", {}).get("away", {}).get("name"),
                "away_id": m.get("teams", {}).get("away", {}).get("id"),
                "league": m.get("league", {}).get("name", ""),
                "league_id": 1,
                "date": m.get("fixture", {}).get("date")
            })
            
            # Limite à 5 affiches réelles pour éviter de saturer Telegram
            if len(matchs_reels) >= 5:
                break

        return matchs_reels
            
    except Exception as e:
        logging.error(f"Erreur lors de la récupération directe des fixtures : {e}")
        return []

# --- INTERFACE TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clavier = [['📊 Analyser les matchs du jour']]
    reply_markup = ReplyKeyboardMarkup(clavier, resize_keyboard=True)
    await update.message.reply_text(
        "🧠 *Bienvenue sur ton Bot Prono IA Algorithmique Multi-Matchs.*\n\n"
        "Analyses mathématiques pures basées sur le modèle Poisson étendu.",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def analyser_matchs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.text == "📊 Analyser les matchs du jour":
        await update.message.reply_text("🕵️‍♂️ Modélisation matricielle en cours... Scan des vrais matchs...")
        
        matchs_du_jour = recuperer_vrais_matchs()
        
        if not matchs_du_jour:
            await update.message.reply_text("ℹ️ *Aucun match réel disponible* dans la compétition ciblée pour le moment.", parse_mode="Markdown")
            return
        
        for idx, m in enumerate(matchs_du_jour, 1):
            res = analyser_match_expert(m["home_id"], m["away_id"], m["home"], m["away"], m["league_id"], m["date"])
            rec = res["recommandation"]

            message_match = (
                f"⚔️ *MATCH {idx}/{len(matchs_du_jour)} : {m['home']} vs {m['away']}*\n"
                f"🏆 Compétition : {m['league']}\n\n"
                f"📈 *Probabilités et Cotes Théoriques (Poisson 0-7 Buts) :*\n"
                f"• *1 :* {res['p1']}% (Cote juste : {res['cote_th1']})\n"
                f"• *N :* {res['pN']}% (Cote juste : {res['cote_thN']})\n"
                f"• *2 :* {res['p2']}% (Cote juste : {res['cote_th2']})\n\n"
                f"🎯 *Métriques Offensives Avancées :*\n"
                f"• *Top Scores Exacts :* {', '.join(res['scores'])}\n"
                f"• *Les deux marquent :* Oui ({res['btts']}%) | Non ({100 - res['btts']}%)\n"
                f"• *Total Buts :* Plus de 2.5 ({res['over25']}%)\n\n"
                f"💎 *FILTRE DE VALUE DETECTÉE :*\n"
                f"👉 *Option validée : {rec['nom']}*\n"
                f"📊 Probabilité algorithmique : {rec['prob']}%\n"
                f"📉 Notre Cote Cible : {rec['cote_th']}\n"
                f"💡 *Avis de l'IA :* {rec['desc']}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            await update.message.reply_text(message_match, parse_mode="Markdown")
            await asyncio.sleep(1)

async def handle_ping(request):
    return web.Response(text="Bot en ligne")

async def main():
    if not TOKEN: 
        logging.error("TELEGRAM_TOKEN manquant dans l'environnement.")
        return
    token_propre = "".join(TOKEN.split())

    # Serveur Web de maintien en ligne (Render)
    web_app = web.Application()
    web_app.router.add_get('/', handle_ping)
    
    port = int(os.environ.get("PORT", 10000))
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', port).start()

    # Application Telegram
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
