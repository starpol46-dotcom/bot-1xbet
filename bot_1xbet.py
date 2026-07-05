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

# --- LISTE DES LIGUES SÉLECTIONNÉES (1XBET READY) ---
LIGUES_AUTORISEES = [
    1, 2, 3, 531, 39, 140, 135, 78, 61, 94, 88, 144, 40, 119, 
    253, 262, 71, 103, 239, 113, 98, 292, 307, 279,
    72, 104, 100, 105, 120, 145, 209
]

def probabilite_poisson(k, laambda):
    if laambda <= 0: laambda = 0.01
    return (pow(laambda, k) * math.exp(-laambda)) / math.factorial(k)

# --- MOTEUR DE CALCUL EXPERT STRICT ---
def analyser_match_expert(team_home_id, team_away_id, nom_home, nom_away, league_id, date_match):
    url = "https://v3.football.api-sports.io/teams/statistics"
    headers = {
        'x-rapidapi-key': API_FOOTBALL_KEY,
        'x-rapidapi-host': 'v3.football.api-sports.io'
    }
    
    try:
        date_obj = dt.datetime.strptime(date_match.split('T')[0], '%Y-%m-%d')
        saison = date_obj.year
    except:
        saison = 2026
    
    lambda_home = None
    mu_away = None
    
    if team_home_id and team_away_id and API_FOOTBALL_KEY:
        try:
            res_home = requests.get(f"{url}?league={league_id}&season={saison}&team={team_home_id}", headers=headers, timeout=5).json()
            res_away = requests.get(f"{url}?league={league_id}&season={saison}&team={team_away_id}", headers=headers, timeout=5).json()
            
            form_home_goals = res_home.get("response", {}).get("goals", {}).get("for", {}).get("average", {}).get("total")
            form_away_goals = res_away.get("response", {}).get("goals", {}).get("for", {}).get("average", {}).get("total")
            
            if form_home_goals and float(form_home_goals) > 0: 
                lambda_home = float(form_home_goals)
            if form_away_goals and float(form_away_goals) > 0: 
                mu_away = float(form_away_goals)
        except Exception as e:
            logging.error(f"Erreur API stats pour {nom_home} vs {nom_away}: {e}")

    # REJET STRICT : Si l'une des deux équipes n'a pas de stats réelles, on abandonne le match
    if lambda_home is None or mu_away is None:
        logging.info(f"Match rejeté par manque de statistiques réelles : {nom_home} vs {nom_away}")
        return None

    prob_1, prob_N, prob_2 = 0.0, 0.0, 0.0
    prob_btts_oui, prob_over_25 = 0.0, 0.0
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

    options_valides = [
        {"nom": f"Victoire {nom_home}", "prob": int(p1*100), "cote_th": ct1},
        {"nom": f"Victoire {nom_away}", "prob": int(p2*100), "cote_th": ct2},
        {"nom": "Les deux équipes marquent", "prob": int(prob_btts_oui*100), "cote_th": ct_btts},
        {"nom": "Plus de 2.5 Buts", "prob": int(prob_over_25*100), "cote_th": ct_o25}
    ]
    
    recommandation = max(options_valides, key=lambda x: x["prob"])
    
    return {
        "p1": int(p1*100), "pN": int(pN*100), "p2": int(p2*100),
        "cote_th1": ct1, "cote_th2": ct2, "cote_thN": ctN,
        "scores": top_scores, "btts": int(prob_btts_oui*100), "over25": int(prob_over_25*100),
        "recommandation": recommandation
    }

# --- RECHERCHE ET SÉLECTION AVANT-MATCH DYNAMIQUE ---
def recuperer_matchs_premium():
    if not API_FOOTBALL_KEY: return []
        
    headers = {
        'x-rapidapi-key': API_FOOTBALL_KEY,
        'x-rapidapi-host': 'v3.football.api-sports.io'
    }
    
    matchs_reels = []
    base_url = "https://v3.football.api-sports.io/fixtures"
    date_string = dt.datetime.utcnow().strftime('%Y-%m-%d')
    
    try:
        response = requests.get(base_url, headers=headers, params={"date": date_string}, timeout=10).json()
        fixtures = response.get("response", [])
        
        for f in fixtures:
            league_id = f.get("league", {}).get("id")
            statut = f.get("fixture", {}).get("status", {}).get("short", "")
            
            home_name = f.get("teams", {}).get("home", {}).get("name", "")
            away_name = f.get("teams", {}).get("away", {}).get("name", "")
            
            mots_bloques = [" II", " B", " Reserve", " U21", " U23", " Sub-"]
            est_reserve = any(mi in home_name or mi in away_name for mi in mots_bloques)
            
            if league_id in LIGUES_AUTORISEES and statut == "NS" and not est_reserve:
                # On tente l'analyse directement
                analyse = analyser_match_expert(
                    f.get("teams", {}).get("home", {}).get("id"),
                    f.get("teams", {}).get("away", {}).get("id"),
                    home_name, away_name, league_id, f.get("fixture", {}).get("date")
                )
                
                # Le match est ajouté UNIQUEMENT si l'analyse expert est valide (pas de None)
                if analyse:
                    matchs_reels.append({
                        "home": home_name,
                        "away": away_name,
                        "league": f.get("league", {}).get("name", ""),
                        "country": f.get("league", {}).get("country", ""),
                        "analyse": analyse
                    })
                    
            if len(matchs_reels) >= 8:
                break
                
    except Exception as e:
        logging.error(f"Erreur scan : {e}")
        
    return matchs_reels

# --- INTERFACE TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clavier = [['📊 Analyser les matchs du jour']]
    reply_markup = ReplyKeyboardMarkup(clavier, resize_keyboard=True)
    await update.message.reply_text(
        "🧠 *Moteur IA 100% Statistique Activé.*\n\n"
        "Zéro approximation : Seuls les matchs avec historiques complets seront affichés.",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def analyser_matchs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.text == "📊 Analyser les matchs du jour":
        await update.message.reply_text("⏳ Scan intransigeant des bases de données sportives en cours...")
        
        matchs_valides = recuperer_matchs_premium()
        
        if not matchs_valides:
            await update.message.reply_text("ℹ️ *Aucun match de notre liste ne possède assez de données historiques réelles aujourd'hui.*", parse_mode="Markdown")
            return
        
        for idx, m in enumerate(matchs_valides, 1):
            res = m["analyse"]
            rec = res["recommandation"]

            message_match = (
                f"⚔️ *MATCH {idx}/{len(matchs_valides)} : {m['home']} vs {m['away']}*\n"
                f"🌍 Compétition : *{m['country']} - {m['league']}*\n\n"
                f"📈 *Probabilités Mathématiques Strictes :*\n"
                f"• *1 :* {res['p1']}% (Cote : {res['cote_th1']})\n"
                f"• *N :* {res['pN']}% (Cote : {res['cote_thN']})\n"
                f"• *2 :* {res['p2']}% (Cote : {res['cote_th2']})\n\n"
                f"🎯 *Calcul de Poisson :*\n"
                f"• *Scores Exacts les plus probables :* {', '.join(res['scores'])}\n"
                f"• *Les deux marquent :* {res['btts']}% | *Plus de 2.5 Buts :* {res['over25']}%\n\n"
                f"💎 *OPTION VALIDÉE PAR LES FAITS :*\n"
                f"👉 *Pronostic : {rec['nom']}*\n"
                f"📊 Indice de Confiance Réel : {rec['prob']}%\n"
                f"📉 Cote théorique alignée : {rec['cote_th']}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            await update.message.reply_text(message_match, parse_mode="Markdown")
            await asyncio.sleep(1)

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
