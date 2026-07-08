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

# --- MOTEUR D'ANALYSE UNIVERSEL ADAPTATIF ---
def analyser_rencontre_universel(fixture_id, team_home_id, team_away_id, nom_home, nom_away, league_id, saison):
    headers = {'x-rapidapi-key': API_FOOTBALL_KEY, 'x-rapidapi-host': 'v3.football.api-sports.io'}
    
    lambda_home, mu_away = None, None
    conseil = "Analyse basée sur l'historique réel des équipes"
    gagnant_pred = "Équilibré"
    arguments = []

    # 1. Tentative principale : Modèle algorithmique de l'API
    try:
        url_pred = f"https://v3.football.api-sports.io/predictions?fixture={fixture_id}"
        res_pred = requests.get(url_pred, headers=headers, timeout=5).json()
        if res_pred.get("response"):
            pred_data = res_pred["response"][0]
            teams_stats = pred_data.get("teams", {})
            comparison = pred_data.get("comparison", {})
            
            if teams_stats.get("home") and teams_stats["home"].get("league"):
                lambda_home = float(teams_stats["home"]["league"]["goals"]["for"]["average"]["total"])
                mu_away = float(teams_stats["away"]["league"]["goals"]["for"]["average"]["total"])
                
                conseil = pred_data.get("predictions", {}).get("advice", conseil)
                gagnant_pred = pred_data.get("predictions", {}).get("winner", {}).get("name", gagnant_pred)
                arguments.append(f"Dynamique : Dom {comparison.get('form', {}).get('home', '50%')} vs Ext {comparison.get('form', {}).get('away', '50%')}")
    except:
        pass

    # 2. Premier Repli : Calcul sur l'historique disponible (Flexible de 1 à 5 matchs)
    if not lambda_home or not mu_away or lambda_home == 0 or mu_away == 0:
        try:
            url_h = f"https://v3.football.api-sports.io/fixtures?team={team_home_id}&last=5"
            url_a = f"https://v3.football.api-sports.io/fixtures?team={team_away_id}&last=5"
            
            res_h = requests.get(url_h, headers=headers, timeout=5).json().get("response", [])
            res_a = requests.get(url_a, headers=headers, timeout=5).json().get("response", [])
            
            if len(res_h) > 0 and len(res_a) > 0:
                buts_h = sum([f["goals"]["home"] for f in res_h if f["teams"]["home"]["id"] == team_home_id and f["goals"]["home"] is not None])
                buts_h += sum([f["goals"]["away"] for f in res_h if f["teams"]["away"]["id"] == team_home_id and f["goals"]["away"] is not None])
                
                buts_a = sum([f["goals"]["home"] for f in res_a if f["teams"]["home"]["id"] == team_away_id and f["goals"]["home"] is not None])
                buts_a += sum([f["goals"]["away"] for f in res_a if f["teams"]["away"]["id"] == team_away_id and f["goals"]["away"] is not None])
                
                lambda_home = round(buts_h / len(res_h), 2)
                mu_away = round(buts_a / len(res_a), 2)
                arguments.append(f"Moyenne calculée sur les {len(res_h)} derniers matchs réels.")
        except:
            pass

    # 3. Deuxième Repli : Si début de saison/coupe à 0 match, on interroge la saison précédente
    if not lambda_home or not mu_away or lambda_home == 0 or mu_away == 0:
        try:
            saison_precedente = saison - 1
            url_stats_h = f"https://v3.football.api-sports.io/teams/statistics?league={league_id}&season={saison_precedente}&team={team_home_id}"
            url_stats_a = f"https://v3.football.api-sports.io/teams/statistics?league={league_id}&season={saison_precedente}&team={team_away_id}"
            
            res_sh = requests.get(url_stats_h, headers=headers, timeout=5).json().get("response", {})
            res_sa = requests.get(url_stats_a, headers=headers, timeout=5).json().get("response", {})
            
            avg_h = res_sh.get("goals", {}).get("for", {}).get("average", {}).get("total")
            avg_a = res_sa.get("goals", {}).get("for", {}).get("average", {}).get("total")
            
            if avg_h and avg_a:
                lambda_home = float(avg_h)
                mu_away = float(avg_a)
                arguments.append(f"Statistiques d'attaque importées de la saison précédente ({saison_precedente}).")
        except:
            pass

    # Rejet ultime : Si aucun des 3 systèmes n'a trouvé de données historiques réelles, on ignore
    if not lambda_home or not mu_away or lambda_home == 0 or mu_away == 0:
        return None

    # Calcul Poisson
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

    options = [
        {"nom": f"Victoire {nom_home}", "prob": int(prob_1 * 100)},
        {"nom": f"Victoire {nom_away}", "prob": int(prob_2 * 100)},
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

# --- SCANNER ADAPTATIF DU JOUR ---
def recuperer_matchs_du_jour():
    if not API_FOOTBALL_KEY: return []
    headers = {'x-rapidapi-key': API_FOOTBALL_KEY, 'x-rapidapi-host': 'v3.football.api-sports.io'}
    matchs_analyses = []
    date_string = dt.datetime.utcnow().strftime('%Y-%m-%d')
    
    try:
        response = requests.get("https://v3.football.api-sports.io/fixtures", headers=headers, params={"date": date_string}, timeout=10).json()
        fixtures = response.get("response", [])
        
        for f in fixtures:
            statut = f.get("fixture", {}).get("status", {}).get("short", "")
            saison = f.get("league", {}).get("season")
            league_id = f.get("league", {}).get("id")
            
            if statut == "NS" and saison:
                home = f.get("teams", {}).get("home", {})
                away = f.get("teams", {}).get("away", {})
                
                analyse = analyser_rencontre_universel(f["fixture"]["id"], home["id"], away["id"], home["name"], away["name"], league_id, saison)
                
                if analyse:
                    matchs_analyses.append({
                        "home": home["name"], "away": away["name"],
                        "league": f["league"]["name"], "country": f["league"]["country"],
                        "details": f.get("fixture", {}).get("venue", {}).get("name", "Stade non spécifié"),
                        "analyse": analyse
                    })
            
            # Objectif : récolter un catalogue d'au moins 7 à 8 matchs complets
            if len(matchs_analyses) >= 8:
                break
    except Exception as e:
        logging.error(f"Erreur lors du scan global : {e}")
    return matchs_analyses

# --- INTERFACE BOT TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clavier = [['📊 Analyser les matchs du jour']]
    reply_markup = ReplyKeyboardMarkup(clavier, resize_keyboard=True)
    await update.message.reply_text(
        "🔬 *Moteur d'Analyse IA 100% Statistique Réelle.*\n\n"
        "Algorithme ajusté : scan flexible de l'historique disponible et des archives de saison.",
        reply_markup=reply_markup, parse_mode="Markdown"
    )

async def analyser_matchs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.text == "📊 Analyser les matchs du jour":
        await update.message.reply_text("⏳ Traitement et calcul des probabilités réelles du jour...")
        matchs_valides = recuperer_matchs_du_jour()
        
        if not matchs_valides:
            await update.message.reply_text("ℹ️ *Aucun match disponible avec des données historiques exploitables aujourd'hui.*", parse_mode="Markdown")
            return
        
        for idx, m in enumerate(matchs_valides, 1):
            res = m["analyse"]
            rec = res["recommandation"]
            args_str = "\n".join([f"• {arg}" for arg in res["arguments"]])
            
            message_match = (
                f"⚔️ *MATCH {idx}/{len(matchs_valides)} : {m['home']} vs {m['away']}*\n"
                f"🌍 Compétition : *{m['country']} - {m['league']}*\n"
                f"📍 Lieu : {m['details']}\n\n"
                f"📊 *Probabilités Statistiques :*\n"
                f"• Victoire Dom (1) : {res['p1']}%\n"
                f"• Match Nul (N) : {res['pN']}%\n"
                f"• Victoire Ext (2) : {res['p2']}%\n"
                f"• Scores probables : {', '.join(res['scores'])}\n"
                f"• Les deux marquent : {res['btts']}% | Over 2.5 : {res['over25']}%\n\n"
                f"🧠 *Indicateurs & Synthèse :*\n"
                f"{args_str}\n"
                f"💬 Tendance : {res['conseil_expert']}\n\n"
                f"🎯 *PRONOSTIC RETENU :*\n"
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
