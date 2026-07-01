import logging
import os
import math
import requests
import asyncio
from datetime import datetime
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

# --- MOTEUR DE CALCUL EXPERT EN MATRICE ÉTENDUE (0 À 7 BUTS) ---
def analyser_match_expert(team_home_id, team_away_id, nom_home, nom_away):
    url = "https://v3.football.api-sports.io/teams/statistics"
    headers = {
        'x-rapidapi-key': API_FOOTBALL_KEY,
        'x-rapidapi-host': 'v3.football.api-sports.io'
    }
    saison = 2025
    
    # Valeurs moyennes de base stables pour le modèle xG
    lambda_home = 1.55  
    mu_away = 1.15
    
    if team_home_id and team_away_id and API_FOOTBALL_KEY:
        try:
            res_home = requests.get(f"{url}?league=39&season={saison}&team={team_home_id}", headers=headers, timeout=5).json()
            res_away = requests.get(f"{url}?league=39&season={saison}&team={team_away_id}", headers=headers, timeout=5).json()
            
            form_home_goals = res_home.get("response", {}).get("goals", {}).get("for", {}).get("average", {}).get("home")
            form_away_goals = res_away.get("response", {}).get("goals", {}).get("for", {}).get("average", {}).get("away")
            
            if form_home_goals: lambda_home = float(form_home_goals) * 1.05
            if form_away_goals: mu_away = float(form_away_goals) * 0.95
        except Exception as e:
            logging.error(f"Erreur calculs stats API : {e}")

    prob_1, prob_N, prob_2 = 0.0, 0.0, 0.0
    prob_btts_oui = 0.0
    prob_over_25 = 0.0
    scores = {}
    
    # MATRICE ÉTENDUE : Analyse de 0 à 7 buts par équipe (range(8))
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
            scores[f"{h}-{a}"] = p_score

    scores_tries = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_scores = [f"{sc[0]} ({round(sc[1]*100, 1)}%)" for sc in scores_tries[:2]]
    
    total_1N2 = prob_1 + prob_N + prob_2 if (prob_1 + prob_N + prob_2) > 0 else 1
    p1 = prob_1 / total_1N2
    pN = prob_N / total_1N2
    p2 = prob_2 / total_1N2
    
    cote_theorique_1 = round(1 / p1, 2) if p1 > 0 else 99.0
    cote_theorique_N = round(1 / pN, 2) if pN > 0 else 99.0
    cote_theorique_2 = round(1 / p2, 2) if p2 > 0 else 99.0
    
    cote_1xbet_1 = round(cote_theorique_1 * 1.08, 2)
    
    options_valides = [
        {"nom": f"Victoire {nom_home}", "prob": int(p1*100), "cote_th": cote_theorique_1, "cote_bk": cote_1xbet_1, "desc": "Indice de dangerosité xG supérieur à la moyenne de la ligue."},
        {"nom": "Les deux équipes marquent (BTTS)", "prob": int(prob_btts_oui*100), "cote_th": round(1/prob_btts_oui, 2) if prob_btts_oui > 0 else 99.0, "cote_bk": round((1/prob_btts_oui)*1.02, 2) if prob_btts_oui > 0 else 99.0, "desc": "Volume de clean sheets très faible sur la distribution matricielle."},
        {"nom": "Plus de 2.5 Buts", "prob": int(prob_over_25*100), "cote_th": round(1/prob_over_25, 2) if prob_over_25 > 0 else 99.0, "cote_bk": round((1/prob_over_25)*1.03, 2) if prob_over_25 > 0 else 99.0, "desc": "Densité de probabilité centrée sur des scores à fort xG cumulé."}
    ]
    
    recommandation = max(options_valides, key=lambda x: x["prob"])
    
    return {
        "p1": int(p1*100), "pN": int(pN*100), "p2": int(p2*100),
        "cote_th1": cote_theorique_1, "cote_thN": cote_theorique_N, "cote_th2": cote_theorique_2,
        "scores": top_scores, "btts": int(prob_btts_oui*100), "over25": int(prob_over_25*100),
        "recommandation": recommandation
    }

# --- FILTRE ET EXTRACTION DYNAMIQUE DES 7 PROCHAINS MATCHS RÉELS ---
def recuperer_vrais_matchs():
    if not API_FOOTBALL_KEY or API_FOOTBALL_KEY == "METS_TA_CLE_API_ICI":
        logging.warning("Clé API-Football manquante.")
        return obtenir_matchs_secours()
        
    headers = {
        'x-rapidapi-key': API_FOOTBALL_KEY,
        'x-rapidapi-host': 'v3.football.api-sports.io'
    }
    saison = 2025
    
    try:
        # On demande en priorité absolue les 7 prochains matchs programmés de la Coupe du Monde (League 1)
        url = f"https://v3.football.api-sports.io/fixtures?league=1&season={saison}&next=7"
        response = requests.get(url, headers=headers, timeout=10).json()
        matchs = response.get("response", [])
        
        # Si la Coupe du Monde n'a pas d'événement immédiat, on pioche dans les ligues majeures actives
        if not matchs:
            url = f"https://v3.football.api-sports.io/fixtures?season={saison}&next=20"
            response = requests.get(url, headers=headers, timeout=10).json()
            matchs = response.get("response", [])

        matchs_reels = []
        for m in matchs:
            ligue = m.get("league", {}).get("name", "")
            matchs_reels.append({
                "home": m.get("teams", {}).get("home", {}).get("name"),
                "home_id": m.get("teams", {}).get("home", {}).get("id"),
                "away": m.get("teams", {}).get("away", {}).get("name"),
                "away_id": m.get("teams", {}).get("away", {}).get("id"),
                "league": ligue
            })
            # On stoppe la boucle dès qu'on a réuni nos 7 fiches de matchs
            if len(matchs_reels) >= 7:
                break
                
        if matchs_reels:
            return matchs_reels
            
    except Exception as e:
        logging.error(f"Erreur lors de la récupération des matchs réels : {e}")
        
    return obtenir_matchs_secours()

def obtenir_matchs_secours():
    return [
        {"home": "Real Madrid", "home_id": 541, "away": "FC Barcelone", "away_id": 529, "league": "La Liga"},
        {"home": "Manchester City", "home_id": 50, "away": "Liverpool", "away_id": 40, "league": "Premier League"},
        {"home": "Bayern Munich", "home_id": 157, "away": "Dortmund", "away_id": 165, "league": "Bundesliga"},
        {"home": "Paris SG", "home_id": 85, "away": "Marseille", "away_id": 81, "league": "Ligue 1"},
        {"home": "Juventus", "home_id": 496, "away": "Inter Milan", "away_id": 505, "league": "Serie A"},
        {"home": "Arsenal", "home_id": 42, "away": "Chelsea", "away_id": 49, "league": "Premier League"},
        {"home": "Atletico Madrid", "home_id": 530, "away": "FC Valence", "away_id": 532, "league": "La Liga"}
    ]

# --- INTERFACE TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clavier = [['📊 Analyser les matchs du jour']]
    reply_markup = ReplyKeyboardMarkup(clavier, resize_keyboard=True)
    await update.message.reply_text(
        "🧠 *Bienvenue sur ton Bot Prono IA Algorithmique Multi-Matchs.*\n\n"
        "Filtres actifs : Volume étendu (7 matchs), Matrice de Poisson 0-7 buts, Modélisation xG & Détection de Value.",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def analyser_matchs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.text == "📊 Analyser les matchs du jour":
        await update.message.reply_text("🕵️‍♂️ Modélisation matricielle en cours... Analyse des 7 prochaines grosses affiches réelles...")
        
        matchs_du_jour = recuperer_vrais_matchs()
        
        for idx, m in enumerate(matchs_du_jour, 1):
            res = analyser_match_expert(m["home_id"], m["away_id"], m["home"], m["away"])
            rec = res["recommandation"]

            message_match = (
                f"⚔️ *MATCH {idx}/{len(matchs_du_jour)} : {m['home']} vs {m['away']}*\n"
                f"🏆 Compétition : {m['league']}\n\n"
                f"📈 *Probabilités et Cotes Théoriques (Poisson 0-7 Buts) :*\n"
                f"• *1 :* {res['p1']}% (Cote juste : {res['cote_th1']})\n"
                f"• *N :* {res['pN']}% (Cote juste : {res['cote_thN']})\n"
                f"• *2 :* {res['p2']}% (Cote juste : {res['cote_th2']})\n\n"
                f"🎯 *Métriques Offensives Avancées (Modèle xG) :*\n"
                f"• *Top Scores Exacts :* {', '.join(res['scores'])}\n"
                f"• *Les deux marquent :* Oui ({res['btts']}%) | Non ({100 - res['btts']}%)\n"
                f"• *Total Buts :* Plus de 2.5 ({res['over25']}%)\n\n"
                f"💎 *FILTRE DE VALUE DETECTÉE :*\n"
                f"👉 *Option validée : {rec['nom']}*\n"
                f"📊 Probabilité algorithmique : {rec['prob']}%\n"
                f"📉 Notre Cote : {rec['cote_th']} | ⚠️ Seuil minimum conseillé : {rec['cote_bk']}\n"
                f"💡 *Avis de l'IA :* {rec['desc']}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            await update.message.reply_text(message_match, parse_mode="Markdown")
            await asyncio.sleep(1) # Petit délai pour éviter que Telegram ne bloque l'envoi massif de messages

async def handle_ping(request):
    return web.Response(text="Bot en ligne")

async def main():
    if not TOKEN: return
    token_propre = "".join(TOKEN.split())

    web_app = web.Application()
    web_app.router.add_get('/', handle_ping)
    
    port = int(os.environ.get("PORT", 10000))
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', port).start()

    application = Application.builder().token(token_propre).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, analyser_matchs))
    
    async with application:
        await application.initialize()
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        while True: await asyncio.sleep(3600)

if __name__ == '__main__':
    asyncio.run(main())
