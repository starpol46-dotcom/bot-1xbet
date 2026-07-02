import logging
import os
import math
import requests
import asyncio
import datetime as dt
from aiohttp import web
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

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

def probabilite_poisson(k, laambda):
    if laambda <= 0: laambda = 0.01
    return (pow(laambda, k) * math.exp(-laambda)) / math.factorial(k)

def analyser_match_expert(team_home_id, team_away_id, nom_home, nom_away, league_id, date_match):
    url = "https://v3.football.api-sports.io/teams/statistics"
    headers = {'x-rapidapi-key': API_FOOTBALL_KEY, 'x-rapidapi-host': 'v3.football.api-sports.io'}
    
    try:
        date_obj = dt.datetime.strptime(date_match.split('T')[0], '%Y-%m-%d')
        saison = date_obj.year
    except:
        saison = 2026
    
    lambda_home, mu_away = 1.45, 1.05
    
    if team_home_id and team_away_id and API_FOOTBALL_KEY:
        try:
            res_home = requests.get(f"{url}?league={league_id}&season={saison}&team={team_home_id}", headers=headers, timeout=5).json()
            res_away = requests.get(f"{url}?league={league_id}&season={saison}&team={team_away_id}", headers=headers, timeout=5).json()
            
            form_home_goals = res_home.get("response", {}).get("goals", {}).get("for", {}).get("average", {}).get("home")
            form_away_goals = res_away.get("response", {}).get("goals", {}).get("for", {}).get("average", {}).get("away")
            
            if form_home_goals: lambda_home = float(form_home_goals)
            if form_away_goals: mu_away = float(form_away_goals)
        except:
            pass

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
    
    options_valides = [
        {"nom": f"Victoire {nom_home}", "prob": int(p1*100), "cote_th": round(1/p1, 2) if p1 > 0 else 99, "desc": "Indicateurs favorables.", "min_p": 55},
        {"nom": f"Victoire {nom_away}", "prob": int(p2*100), "cote_th": round(1/p2, 2) if p2 > 0 else 99, "desc": "Supériorité nette.", "min_p": 55},
        {"nom": "Les deux équipes marquent", "prob": int(prob_btts_oui*100), "cote_th": round(1/prob_btts_oui, 2) if prob_btts_oui > 0 else 99, "desc": "Carences défensives.", "min_p": 52}
    ]
    
    recommandation = max(options_valides, key=lambda x: x["prob"])
    return {
        "p1": int(p1*100), "pN": int(pN*100), "p2": int(p2*100),
        "cote_th1": round(1/p1, 2) if p1 > 0 else 99, "cote_thN": round(1/pN, 2) if pN > 0 else 99, "cote_th2": round(1/p2, 2) if p2 > 0 else 99,
        "scores": top_scores, "btts": int(prob_btts_oui*100), "over25": int(prob_over_25*100),
        "recommandation": recommandation, "statut_validation": "🔒 SÉLECTION"
    }

# --- RECHERCHE ET DIAGNOSTIC DIRECT ---
def recuperer_matchs_ou_erreur():
    if not API_FOOTBALL_KEY or API_FOOTBALL_KEY == "METS_TA_CLE_API_ICI":
        return {"status": "erreur", "message": "La clé API-Football est absente ou mal configurée dans tes variables d'environnement."}
        
    headers = {
        'x-rapidapi-key': API_FOOTBALL_KEY,
        'x-rapidapi-host': 'v3.football.api-sports.io'
    }
    
    base_url = "https://v3.football.api-sports.io/fixtures"
    date_string = dt.datetime.utcnow().strftime('%Y-%m-%d')
    
    try:
        req = requests.get(base_url, headers=headers, params={"date": date_string}, timeout=10)
        
        if req.status_code != 200:
            return {"status": "erreur", "message": f"Le serveur de l'API a répondu avec un code erreur {req.status_code}."}
            
        data = req.json()
        
        # Si l'API renvoie une erreur officielle (ex: clé invalide, quota dépassé)
        if data.get("errors"):
            return {"status": "erreur", "message": f"L'API renvoie une erreur système : {data['errors']}"}
            
        fixtures = data.get("response", [])
        if not fixtures:
            return {"status": "erreur", "message": f"L'API est connectée mais son tableau 'response' est totalement vide pour la date du {date_string}."}
            
        matchs_reels = []
        for f in fixtures:
            statut = f.get("fixture", {}).get("status", {}).get("short", "")
            if statut in ["NS", "TBD"]:
                matchs_reels.append({
                    "home": f.get("teams", {}).get("home", {}).get("name"),
                    "home_id": f.get("teams", {}).get("home", {}).get("id"),
                    "away": f.get("teams", {}).get("away", {}).get("name"),
                    "away_id": f.get("teams", {}).get("away", {}).get("id"),
                    "league": f.get("league", {}).get("name", ""),
                    "league_id": f.get("league", {}).get("id"),
                    "date": f.get("fixture", {}).get("date")
                })
            if len(matchs_reels) >= 5:
                break
                
        if not matchs_reels:
            return {"status": "erreur", "message": "Tous les matchs de l'API aujourd'hui ont déjà commencé ou sont terminés."}
            
        return {"status": "succes", "donnees": matchs_reels}
        
    except Exception as e:
        return {"status": "erreur", "message": f"Impossible de contacter l'API. Erreur technique : {str(e)}"}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clavier = [['📊 Analyser les matchs du jour']]
    reply_markup = ReplyKeyboardMarkup(clavier, resize_keyboard=True)
    await update.message.reply_text("🔍 *Mode Diagnostic API activé.* Appuie ci-dessous pour voir le verdict.", reply_markup=reply_markup, parse_mode="Markdown")

async def analyser_matchs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.text == "📊 Analyser les matchs du jour":
        await update.message.reply_text("📡 Interrogation de l'API en cours...")
        
        resultat = recuperer_matchs_ou_erreur()
        
        if resultat["status"] == "erreur":
            # On envoie l'erreur brute sur Telegram pour comprendre le souci
            await update.message.reply_text(f"❌ *PROBLÈME API DÉTECTÉ :*\n\n{resultat['message']}", parse_mode="Markdown")
            return
            
        matchs = resultat["donnees"]
        for idx, m in enumerate(matchs, 1):
            res = analyser_match_expert(m["home_id"], m["away_id"], m["home"], m["away"], m["league_id"], m["date"])
            rec = res["recommandation"]
            message_match = f"⚔️ *MATCH {idx}/{len(matchs)} : {m['home']} vs {m['away']}*\n👉 *Option : {rec['nom']}* ({rec['prob']}%)\n━━━━━━━━━━━━━━━━"
            await update.message.reply_text(message_match, parse_mode="Markdown")

async def handle_ping(request): return web.Response(text="En ligne")

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

if __name__ == '__main__':
    asyncio.run(main())
