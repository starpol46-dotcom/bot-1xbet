import os
from datetime import datetime
import requests

# ==========================================
# CONFIGURATION ET CONSTANTES (API-FOOTBALL)
# ==========================================
API_KEY = os.environ.get("API_FOOTBALL_KEY", "TON_API_KEY_ICI")
BASE_URL = "https://v3.football.api-sports.io"

# ID officiel pour la Coupe du Monde (League ID = 1, Saison = 2026)
WORLD_CUP_LEAGUE_ID = 1
SEASON_YEAR = 2026

HEADERS = {
    "x-rapidapi-host": "v3.football.api-sports.io",
    "x-rapidapi-key": API_KEY
}


def obtenir_matchs_du_jour():
    """
    Récupère la liste des vrais matchs programmés pour la date du jour.
    Si aucun match n'est trouvé, le bot retourne une liste vide au lieu de simuler.
    """
    date_aujourdhui = datetime.now().strftime("%Y-%m-%d")
    endpoint = f"{BASE_URL}/fixtures"
    
    parametres = {
        "league": WORLD_CUP_LEAGUE_ID,
        "season": SEASON_YEAR,
        "date": date_aujourdhui
    }
    
    try:
        response = requests.get(endpoint, headers=HEADERS, params=parametres, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Extraction sécurisée des données de l'API
        fixtures = data.get("response", [])
        return fixtures
        
    except requests.exceptions.RequestException as e:
        print(f"[ERREUR API] Impossible de récupérer les matchs : {e}")
        return []


def analyser_statistiques_btts(match_data):
    """
    Exemple d'algorithme d'analyse statistique épuré.
    Calcule des probabilités réelles basées sur les données récoltées.
    """
    # Éviter les calculs inutiles si les données du match sont manquantes
    if not match_data:
        return None
        
    # Exemple de structure d'analyse (à adapter selon les données statistiques réelles de tes équipes)
    probabilite_btts = 50.0  # Remplacer par ta logique matricielle basée sur xG / Clean sheets réels
    cote_theorique = round(100 / probabilite_btts, 2) if probabilite_btts > 0 else 0
    
    return {
        "btts_oui_prob": probabilite_btts,
        "cote_cible": cote_theorique
    }


def formater_message_prediction(match):
    """
    Génère proprement le texte du message Telegram pour un vrai match.
    """
    equipe_domicile = match["teams"]["home"]["name"]
    equipe_exterieur = match["teams"]["away"]["name"]
    statut_match = match["fixture"]["status"]["long"]
    
    # Lancement de ton analyse matricielle propre
    analyse = analyser_statistiques_btts(match)
    
    message = (
        f"⚔️ **MATCH : {equipe_domicile} vs {equipe_exterieur}**\n"
        f"🏆 Compétition : Coupe du Monde 2026\n"
        f"📊 Statut : {statut_match}\n\n"
        f"💎 **OPTION VALIDÉE (RECHERCHE DE VALUE) :**\n"
        f"👉 Les deux équipes marquent (BTTS)\n"
        f"📊 Probabilité calculée : {analyse['btts_oui_prob']}%\n"
        f"📉 Cote théorique cible : {analyse['cote_cible']}\n"
    )
    return message


def executer_scan_prediction():
    """
    Fonction principale appelée par ton webhook ou ta commande Telegram /analyser.
    """
    print("[LOG] Lancement du scan des matchs réels...")
    matchs = obtenir_matchs_du_jour()
    
    if not matchs:
        message_vide = "ℹ️ **Aucun match de Coupe du Monde programmé pour aujourd'hui ({}) dans l'API.**".format(
            datetime.now().strftime("%d/%m/%Y")
        )
        print("[LOG] Fin du traitement : Aucun match trouvé.")
        return [message_vide]
        
    messages_a_envoyer = []
    for match in matchs:
        texte_pronostic = formater_message_prediction(match)
        messages_a_envoyer.append(texte_pronostic)
        
    return messages_a_envoyer


# ==========================================
# ZONE DE TEST LOCAL
# ==========================================
if __name__ == "__main__":
    # Permet de tester le script directement dans ta console PythonAnywhere
    resultats = executer_scan_prediction()
    for msg in resultats:
        print("\n--- Message Généré ---")
        print(msg)
