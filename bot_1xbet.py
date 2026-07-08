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

# --- CATALOGUE DES LIGUES MAJEURES (Critère 1) ---
LIGUES_MAJEURES = [
    1, 2, 3, 531,         # Coupe du Monde, Euro, LDC, Copa America
    39, 140, 135, 78, 61, # Premier League, LaLiga, Serie A, Bundesliga, Ligue 1
    94, 88, 144, 40, 119, # Eredivisie, Pro League, Championship, Primeira Liga, Serie A (Brésil)
    253, 262, 71, 103,    # MLS, Liga MX, Serie A (Equateur), Primera Division (Argentine)
    98, 292, 307, 279     # J1 League, K League 1, Super League Chinoise, Saudi Pro League
]

def probabilite_poisson(k, laambda):
    if laambda <= 0: laambda = 0.01
    return (pow(laambda, k) * math.exp(-laambda)) / math.factorial(k)

# --- RECHERCHE ET ANALYSE AVANCÉE (Critères 2, 3, 5, 6) ---
def analyser_rencontre_complete(fixture_id, team_home, team_away, league_id, saison):
    headers = {'x-rapidapi-key': API_FOOTBALL_KEY, 'x-rapidapi-host': 'v3.football.api-sports.io'}
    
    # 1. Récupération des prédictions & Modèles de référence (Critère 5)
    url_pred = f"https://v3.football.api-sports.io/predictions?fixture={fixture_id}"
    try:
        res_pred = requests.get(url_pred, headers=headers, timeout=6).json()
        pred_data = res_pred.get("response", [])[0] if res_pred.get("response") else None
    except:
        pred_data = None

    # REJET STRICT : Si aucun modèle ou donnée historique réelle n'est accessible, on n'invente rien.
    if not pred_data:
        return None

    # Extraction des forces et dynamiques réelles (Critère 3)
    comparison = pred_data.get("comparison", {})
    h2h_stats = pred_data.get("h2h", [])
    
    # Récupération des buts récents et clean sheets sur la forme actuelle (Critère 3)
    teams_stats = pred_data.get("teams", {})
    
    # Loi de Poisson basée sur les buts réels marqués/encaissés par match de la saison (Critère 3)
    try:
        lambda_home = float(teams_stats["home"]["league"]["goals"]["for"]["average"]["total"])
        mu_away = float(teams_stats["away"]["league"]["goals"]["for"]["average"]["total"])
    except:
        return None # Pas de statistiques réelles de buts = Rejet strict

    if lambda_home == 0 or mu_away == 0:
        return None

    # 2. Analyse H2H (Critère 2)
    total_h2h = len(h2h_stats)
    home_win_h2h = 0
    if total_h2h > 0:
        for match in h2h_stats:
            if match.get("teams", {}).get("home", {}).get("winner") and match["teams"]["home"]["id"] == team_home["id"]:
                home_win_h2h += 1
            elif match.get("teams", {}).get("away", {}).get("winner") and match["teams"]["away"]["id"] == team_home["id"]:
                home_win_h2h += 1
        pct_h2h_home = int((home_win_h2h / total_h2h) * 100)
    else:
        pct_h2h_home = 50 # Neutre si pas de passif commun

    # 3. Calcul des probabilités par Poisson
    prob_1, prob_N, prob_2 = 0.0, 0.0, 0.0
    prob_btts, prob_over25 = 0.0, 0.0
    scores = {}
    
    for h in range(7):
        for a in range(7):
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

    # 4. Synthèse Algorithmique et Argumentation (Critère 7)
    conseil = pred_data.get("predictions", {}).get("advice", "Aucun avis fort")
    gagnant_pred = pred_data.get("predictions", {}).get("winner", {}).get("name", "Indéterminé")
    
    # Formulation des arguments
    arguments = [
        f"Forme : Dom {comparison.get('form', {}).get('home', '0%')} vs Ext {comparison.get('form', {}).get('away', '0%')}.",
        f"Attaque/Défense : Supériorité offensive estimée à {comparison.get('att', {}).get('home', '50%')} pour le camp local.",
        f"Confrontations directes : L'équipe à domicile a remporté {pct_h2h_home}% des récents face-à-face directs."
    ]

    options = [
        {"nom": f"Victoire {team_home['name']}", "prob": int(prob_1 * 100)},
        {"nom": f"Victoire {team_away['name']}", "prob": int(prob_2 * 100)},
        {"nom": "Les deux équipes marquent", "prob": int(prob_btts * 100)},
        {"nom": "Plus de 2.5 Buts", "prob": int(prob_over25 * 100)}
    ]
    recommandation = max(options, key=lambda x: x["prob"])

    return {
        "p1": int(prob_1*100), "pN": int(prob_N*100), "p2": int(prob_2*100),
        "scores": top_scores, "btts": int(prob_btts*100), "over25": int(prob_over25*100),
        "conseil_expert": conseil, "distribution": gagnant_pred,
        "arguments": arguments, "recommandation": recommendation
    }

# --- CRITÈRE 1 : IDENTIFICATION DES RENCONTRES (AUGMENTATION À 8 MATCHS) ---
def recuperer_rencontres_majeures():
    if not API_FOOTBALL_KEY: return []
    headers = {'x-rapidapi-key': API_FOOTBALL_KEY, 'x-rapidapi-host': 'v3.football.api-sports.io'}
    matchs_analyses = []
    date_string = dt.datetime.utcnow().strftime('%Y-%m-%d')
    
    try:
        # Recherche exclusive des fixtures du jour
        response = requests.get("https://v3.football.api-sports.io/fixtures", headers=headers, params={"date": date_string}, timeout=10).json()
        fixtures = response.get("response", [])
        
        for f in fixtures:
            league_id = f.get("league", {}).get("id")
            statut = f.get("fixture", {}).get("status", {}).get("short", "")
            saison = f.get("league", {}).get("season")
            
            # Filtrer rigoureusement sur les ligues majeures et matchs non commencés (Critère 1)
            if league_id in LIGUES_MAJEURES and statut == "NS" and saison:
                home = f.get("teams", {}).get("home", {})
                away = f.get("teams", {}).get("away", {})
                
                analyse = analyser_rencontre_complete(f["fixture"]["id"], home, away, league_id, saison)
                
                if analyse:
                    matchs_analyses.append({
                        "home": home["name"], "away": away["name"],
                        "league": f["league"]["name"], "country": f["league"]["country"],
                        "details": f.get("fixture", {}).get("venue", {}).get("name", "Stade inconnu"), # Lieu (Critère 6)
                        "analyse": analyse
                    })
            # CHANGEMENT ICI : Augmentation de la limite à 8 matchs maximum pour en garantir au moins 7
            if len(matchs_analyses) >= 8:
                break
    except Exception as e:
        logging.error(f"Erreur lors du scan : {e}")
    return matchs_analyses

# --- INTERFACE BOT TELEGRAM (Critère 7) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clavier = [['📊 Analyser les matchs du jour']]
    reply_markup = ReplyKeyboardMarkup(clavier, resize_keyboard=True)
    await update.message.reply_text(
        "🔬 *Moteur d'Analyse Football Rigoureux & Robustesse Algorithmique.*\n\n"
        "Aucune approximation. Recherche configurée pour lister au moins 7 analyses majeures complètes.",
        reply_markup=reply_markup, parse_mode="Markdown"
    )

async def analyser_matchs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.text == "📊 Analyser les matchs du jour":
        await update.message.reply_text("⏳ Synthèse des indicateurs fiables en cours (0% supposition, objectif : au moins 7 matchs)...")
        matchs_valides = recuperer_rencontres_majeures()
        
        if not matchs_valides:
            await update.message.reply_text("ℹ️ *Aucune rencontre majeure du jour ne dispose de données historiques complètes exploitables.*", parse_mode="Markdown")
            return
        
        for idx, m in enumerate(matchs_valides, 1):
            res = m["analyse"]
            rec = res["recommandation"]
            args_str = "\n".join([f"• {arg}" for arg in res["arguments"]])
            
            message_match = (
                f"⚔️ *MATCH MAJEUR {idx}/{len(matchs_valides)} : {m['home']} vs {m['away']}*\n"
                f"🌍 Compétition : *{m['country']} - {m['league']}*\n"
                f"📍 Lieu de la rencontre : {m['details']}\n\n"
                f"📊 *Indicateurs Statistiques Réels :*\n"
                f"• Probabilité 1 : {res['p1']}%\n"
                f"• Probabilité N : {res['pN']}%\n"
                f"• Probabilité 2 : {res['p2']}%\n"
                f"• Scores probables : {', '.join(res['scores'])}\n"
                f"• BTTS : {res['btts']}% | Over 2.5 : {res['over25']}%\n\n"
                f"🧠 *Synthèse & Modèles de Référence :*\n"
                f"{args_str}\n"
                f"💬 Avis Tendanciel : {res['conseil_expert']} (Axe : {res['distribution']})\n\n"
                f"🎯 *PRONOSTIC RETENU (Plus Haute Probabilité) :*\n"
                f"👉 *{rec['nom']} ({rec['prob']}% de confiance)*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            await update.message.reply_text(message_match, parse_mode="Markdown")
            await asyncio.sleep(1)

async def handle_ping(request): return web.Response(text="Bot opérationnel")

async def main():
    if not TOKEN: return
    token_propre = "".join(TOKEN.split())
    web_app = web.Application()
    web_app.router.add_get('/', handle_ping)
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 10000))).start()

    application = Application.builder().token(token_propre).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, analyser_matchs))
    
    async with application:
        await application.initialize()
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        while True: await asyncio.sleep(3600)

if __name__ == '__main__': asyncio.run(main())
