# main.py - Version 3.2 (Correction Syntaxe Odoo 'read')

import base64
import json
import os
import traceback
from datetime import datetime
import xmlrpc.client
from urllib.parse import quote
import pandas as pd 

import requests
from google.cloud import firestore, secretmanager

# --- Initialisation des Clients GCP (Globale) ---
try:
    SECRET_CLIENT = secretmanager.SecretManagerServiceClient()
    DB = firestore.Client(database="payflow-db") # Spécifie votre BDD
    
    PROJECT_ID = os.environ.get("GCP_PROJECT")
    if not PROJECT_ID:
        PROJECT_ID = os.environ.get("GCLOUD_PROJECT")
        if not PROJECT_ID:
             raise Exception("Variable d'environnement GCP_PROJECT ou GCLOUD_PROJECT non définie.")
            
except Exception as e:
    print(f"ERREUR CRITIQUE: Échec d'initialisation des clients GCP: {e}")
    SECRET_CLIENT = None
    DB = None

# --- Fonctions Helpers (Authentification Silae - Inchangées) ---

def load_silae_secrets():
    """Charge les secrets Silae depuis Secret Manager."""
    if not SECRET_CLIENT or not PROJECT_ID:
        raise Exception("Client Secret Manager non initialisé ou PROJECT_ID manquant.")

    secrets_to_fetch = ["SILAE_CLIENT_ID", "SILAE_CLIENT_SECRET", "SILAE_SUBSCRIPTION_KEY"]
    config = {}
    try:
        for key in secrets_to_fetch:
            name = f"projects/{PROJECT_ID}/secrets/{key}/versions/latest"
            # --- CORRECTION : 'client' n'était pas défini ---
            response = SECRET_CLIENT.access_secret_version(request={"name": name})
            value = response.payload.data.decode("UTF-8").strip()
            config_key = key.split('_', 1)[-1].lower()
            config[config_key] = value
        if not all(k in config for k in ['client_id', 'client_secret', 'subscription_key']):
             raise ValueError("Un ou plusieurs secrets Silae sont manquants.")
        return config
    except Exception as e:
        print(f"ERREUR: Échec du chargement des secrets Silae: {e}")
        raise 

def get_silae_token(silae_config):
    """Obtient un token Silae."""
    auth_url = "https://payroll-api-auth.silae.fr/oauth2/v2.0/token"
    try:
        client_id = quote(silae_config.get("client_id", ""))
        client_secret = quote(silae_config.get("client_secret", ""))
        if not client_id or not client_secret:
            raise ValueError("ID Client ou Secret Client Silae manquant.")
        grant_type = "client_credentials"
        scope = quote("https://silaecloudb2c.onmicrosoft.com/36658aca-9556-41b7-9e48-77e90b006f34/.default")
        auth_data_string = f"grant_type={grant_type}&client_id={client_id}&client_secret={client_secret}&scope={scope}"
        auth_headers = {"Content-Type": "application/x-www-form-urlencoded"}
        response = requests.post(auth_url, data=auth_data_string, headers=auth_headers, timeout=15)
        response.raise_for_status()
        return response.json()["access_token"]
    except requests.exceptions.RequestException as e:
        error_details = ""
        if e.response is not None:
            try: error_details = e.response.json()
            except json.JSONDecodeError: error_details = e.response.text
        raise Exception(f"Échec de la requête du token Silae: {e} - Détails: {error_details}")

def get_silae_ecritures(access_token, silae_config, numero_dossier, date_debut, date_fin):
    """Récupère les écritures Silae."""
    api_url = "https://payroll-api.silae.fr/payroll/v1/EcrituresComptables/EcrituresComptables4"
    subscription_key = silae_config.get("subscription_key")
    if not subscription_key:
        raise ValueError("Clé d'abonnement Silae manquante.")
    api_headers = {"Authorization": f"Bearer {access_token}", "Ocp-Apim-Subscription-Key": subscription_key, "Content-Type": "application/json", "dossiers": str(numero_dossier)}
    api_body = {"numeroDossier": str(numero_dossier), "periodeDebut": date_debut.strftime('%Y-%m-%d'), "periodeFin": date_fin.strftime('%Y-%m-%d'), "avecToutesLesRepartitionsAnalytiques": False}
    try:
        response_api = requests.post(api_url, headers=api_headers, data=json.dumps(api_body), timeout=60)
        response_api.raise_for_status()
        return response_api.json()
    except requests.exceptions.RequestException as e:
        error_details = ""
        if e.response is not None:
            try: error_details = e.response.json()
            except json.JSONDecodeError: error_details = e.response.text
        raise Exception(f"Échec de la récupération des écritures Silae (Dossier {numero_dossier}): {e} - Détails: {error_details}")

# --- MODIFICATION ICI ---
def import_to_odoo_auto(client_config, ecritures_data, period_str):
    """Tente d'importer les écritures dans Odoo via XML-RPC (Gère le Multi-Société)."""
    host = client_config.get('odoo_host')
    db = client_config.get('database_odoo')
    username = client_config.get('odoo_login')
    password = client_config.get('odoo_password')
    journal_code = client_config.get('journal_paie_odoo')
    
    company_id = client_config.get('odoo_company_id') 

    if not all([host, db, username, password, journal_code]):
        raise ValueError("Configuration Odoo manquante (host, db, login, password ou journal).")
    
    if not company_id:
        raise ValueError(f"ID de société Odoo (odoo_company_id) manquant pour le client {client_config.get('nom')}. Veuillez reconfigurer le client dans PayFlow.")

    if ".odoo.com" in host:
        url_common = f"https://{host}/xmlrpc/common"
        url_object = f"https://{host}/xmlrpc/object"
    else:
        url_common = f"https://{host}/xmlrpc/2/common"
        url_object = f"https://{host}/xmlrpc/2/object"

    try:
        journal_silae = ecritures_data['ruptures'][0]
        lignes_silae = journal_silae.get('ecritures')
        if not lignes_silae:
            return "SUCCESS_EMPTY", "Journal Silae vide, rien à importer."

        comptes_odoo_a_verifier = set()
        lignes_pour_odoo = []
        for ligne in lignes_silae:
            code_compte = ligne['compte'] 
            lignes_pour_odoo.append({'account_code': code_compte, 'name': ligne['libelle'], 'debit': ligne['valeur'] if ligne['sens'] == 'D' else 0.0, 'credit': ligne['valeur'] if ligne['sens'] == 'C' else 0.0})
            comptes_odoo_a_verifier.add(code_compte)
        
        common = xmlrpc.client.ServerProxy(url_common)
        uid = common.authenticate(db, username, password, {})
        if not uid:
            raise Exception("Échec d'authentification Odoo. Vérifiez les identifiants.")
            
        models = xmlrpc.client.ServerProxy(url_object)
        
        context = {'allowed_company_ids': [company_id]} 
        
        def execute(model, method, *args, **kwargs):
            kwargs.setdefault('context', {}).update(context)
            return models.execute_kw(db, uid, password, model, method, args, kwargs)

        domain_comptes = [('code', 'in', list(comptes_odoo_a_verifier))]
        fields_comptes = ['code', 'id']
        account_data = execute('account.account', 'search_read', domain_comptes, fields=fields_comptes)
        
        code_to_id_map = {acc['code']: acc['id'] for acc in account_data}
        comptes_manquants = comptes_odoo_a_verifier - set(code_to_id_map.keys())
        if comptes_manquants:
            return "ERROR_ACCOUNT", f"Comptes Odoo introuvables: {sorted(list(comptes_manquants))}. Vérifiez la liaison Silae ET que la bonne société Odoo est sélectionnée."

        domain_journal = [('code', '=', journal_code)]
        journal_id = execute('account.journal', 'search', domain_journal, limit=1)
        if not journal_id:
            return "ERROR_JOURNAL", f"Journal Odoo introuvable (Code: '{journal_code}') dans la société ID {company_id}. Vérifiez la config client."
        journal_id = journal_id[0]
        lignes_finales = []
        for ligne in lignes_pour_odoo:
            lignes_finales.append((0, 0, {'account_id': code_to_id_map[ligne['account_code']], 'name': ligne['name'], 'debit': ligne['debit'], 'credit': ligne['credit']}))
        
        move_vals = {'journal_id': journal_id, 'ref': journal_silae.get('libelle', f"Import Paie Silae {period_str}"), 'date': datetime.now().strftime('%Y-%m-%d'), 'line_ids': lignes_finales}
        move_id = execute('account.move', 'create', move_vals)
        
        # --- CORRECTION ICI : [move_id] devient [move_id] (liste d'IDs) et le kwarg 'fields' devient une liste positionnelle ['name'] ---
        move_info = execute('account.move', 'read', [move_id], ['name']) 
        move_name = move_info[0].get('name') if move_info and move_info[0].get('name') else f"ID {move_id}"
        return "SUCCESS", f"Pièce créée (Brouillon): {move_name}"
    
    except xmlrpc.client.Fault as e:
        print(f"ERREUR XML-RPC (Client: {client_config.get('nom', 'N/A')}): {e.faultString}")
        return "ERROR_ODOO_RPC", f"Erreur Odoo (Fault): {str(e)}"
    except Exception as e:
        print(f"ERREUR Inattendue (Import Odoo pour {client_config.get('nom', 'N/A')}): {e}")
        traceback.print_exc()
        return "ERROR_UNKNOWN", f"Erreur inattendue: {str(e)}"
# --- FIN DE LA MODIFICATION ---

def log_execution(client_doc_id, client_name, period_str, status, message):
    """Enregistre le résultat dans la collection payflow_logs de Firestore."""
    if not DB:
        print(f"ERREUR: Client Firestore non dispo, log non enregistré pour {client_doc_id}")
        return
        
    try:
        log_entry = {
            "client_doc_id": client_doc_id,
            "client_name": client_name,
            "period": period_str,
            "execution_time": datetime.utcnow(),
            "status": status,
            "message": message[:1500]
        }
        log_doc_id = f"{client_doc_id}_{period_str}_{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}"
        DB.collection("payflow_logs").document(log_doc_id).set(log_entry)
        print(f"Log enregistré pour {client_name} - Période: {period_str} - Statut: {status}")
        
    except Exception as e:
        print(f"ERREUR: Échec d'écriture du log Firestore pour {client_doc_id}: {e}")

# --- Point d'Entrée de la Cloud Function (MODIFIÉ) ---

def process_monthly_import(event, context):
    """
    Fonction Cloud déclenchée par Pub/Sub (via Cloud Scheduler).
    S'exécute CHAQUE JOUR, vérifie le jour actuel, et traite
    les clients configurés pour ce jour-là.
    """
    print(f"--- Démarrage de la fonction PayFlow (ID Contexte: {context.event_id}) ---")
    
    # 1. Déterminer la date du jour ET la période à traiter
    today = datetime.utcnow()
    current_day = today.day # Ex: 10
    
    first_day_current_month = today.replace(day=1)
    last_day_previous_month = first_day_current_month - pd.Timedelta(days=1)
    first_day_previous_month = last_day_previous_month.replace(day=1)
    
    date_debut = first_day_previous_month
    date_fin = last_day_previous_month
    period_str = date_debut.strftime('%Y-%m') # Ex: "2025-10"
    
    print(f"Jour actuel (UTC): {current_day}. Période de paie à traiter: {period_str}")

    # 2. Charger les secrets Silae
    try:
        silae_config = load_silae_secrets()
    except Exception as e:
        print(f"ERREUR CRITIQUE: Secrets Silae introuvables. Arrêt. Erreur: {e}")
        return

    # 3. Lire les clients DEPUIS FIRESTORE (FILTRÉ)
    if not DB:
        print("ERREUR CRITIQUE: Client Firestore non dispo. Arrêt.")
        return
        
    try:
        clients_ref = DB.collection("payflow_clients").where(
            "jour_transfert", "==", current_day
        ).stream()
        
        client_docs = list(clients_ref)
        if not client_docs:
            print(f"Aucun client configuré pour un transfert le {current_day} du mois. Terminé.")
            return
            
        print(f"{len(client_docs)} clients trouvés à traiter pour aujourd'hui.")
        
    except Exception as e:
        print(f"ERREUR CRITIQUE: Échec de lecture des clients Firestore. Arrêt. Erreur: {e}")
        return

    # 4. Obtenir le token Silae
    try:
        silae_token = get_silae_token(silae_config)
        if not silae_token:
             raise Exception("Token Silae non obtenu (vide).")
    except Exception as e:
        print(f"ERREUR CRITIQUE: Token Silae inaccessible. Arrêt. Erreur: {e}")
        log_execution("GLOBAL", "Système PayFlow", period_str, "ERROR_SILAE_AUTH", f"Token Silae inaccessible: {e}")
        return

    # 5. Boucle sur chaque client (maintenant filtré)
    processed_count = 0
    error_count = 0
    for doc in client_docs:
        client_doc_id = doc.id
        client_config = doc.to_dict()
        client_name = client_config.get("nom", client_doc_id)
        silae_dossier = client_config.get("numero_dossier_silae")

        print(f"\n--- Traitement client: {client_name} (Dossier Silae: {silae_dossier}) ---")

        if not silae_dossier:
            print(f"Client {client_name} ignoré: 'numero_dossier_silae' manquant.")
            log_execution(client_doc_id, client_name, period_str, "ERROR_CONFIG", "Dossier Silae non configuré dans Firestore.")
            error_count += 1
            continue

        try:
            # A. Récupérer écritures Silae
            print(f"  Étape 1: Récupération des écritures Silae pour {period_str}...")
            ecritures_silae = get_silae_ecritures(silae_token, silae_config, silae_dossier, date_debut, date_fin)
            if not ecritures_silae or not ecritures_silae.get('ruptures') or not ecritures_silae['ruptures'][0].get('ecritures'):
                 print("  Statut: Aucune écriture Silae trouvée pour cette période.")
                 log_execution(client_doc_id, client_name, period_str, "SUCCESS_NO_DATA", "Aucune écriture Silae trouvée pour cette période.")
                 processed_count += 1
                 continue

            # B. Tenter l'import Odoo
            print("  Étape 2: Tentative d'import Odoo...")
            status, message = import_to_odoo_auto(client_config, ecritures_silae, period_str)
            print(f"  Statut: {status} - {message}")

            # C. Logguer le résultat
            log_execution(client_doc_id, client_name, period_str, status, message)
            
            if status.startswith("SUCCESS"):
                processed_count += 1
            else:
                error_count += 1

        except Exception as e:
            print(f"!! ERREUR FONCTIONNELLE (Client {client_name}): {e}")
            traceback.print_exc()
            log_execution(client_doc_id, client_name, period_str, "ERROR_FUNCTION", f"Erreur fonctionnelle: {e}")
            error_count += 1

    print(f"\n--- Exécution du jour {current_day} terminée. {processed_count} succès, {error_count} erreurs. ---")