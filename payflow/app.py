# app.py - Version 4.9 (Ajout Authentification + Logo Odoo)

import streamlit as st
import xmlrpc.client
import pandas as pd
from datetime import datetime
import os
from urllib.parse import quote
import requests
import json
import traceback

# --- Imports Google Cloud ---
try:
    from google.cloud import firestore
    from google.cloud import secretmanager
except ImportError:
    st.error("Bibliothèques GCP manquantes. (google-cloud-firestore, google-cloud-secret-manager)")
    st.stop()

# --- CONFIGURATION DE LA PAGE ---
st.set_page_config(page_title="PayFlow", layout="wide")

# --- Logos (toujours visibles) ---
with st.sidebar:
    try: st.image("lpde.png", width=200)
    except Exception: st.warning("Image lpde.png non trouvée.")
    st.write("---")
    try: st.image("prelium.gif", width=200)
    except Exception: st.warning("Image prelium.gif non trouvée.")

# --- FONCTIONS DE CHARGEMENT GCP ---

@st.cache_resource
def get_secret_client():
    """Initialise le client Secret Manager."""
    return secretmanager.SecretManagerServiceClient()

@st.cache_resource
def get_firestore_client():
    """Initialise le client Firestore."""
    return firestore.Client(database="payflow-db") # Spécifie la BDD

# --- NOUVELLE FONCTION : Mot de passe de l'application ---
@st.cache_data(ttl=3600) # Cache 1h
def get_payflow_password():
    """Charge le mot de passe de l'application (PAYFLOW_PASSWORD) depuis Secret Manager."""
    client = get_secret_client()
    project_id = os.environ.get("GCP_PROJECT")
    if not project_id:
        project_id = os.environ.get("GCLOUD_PROJECT")
        if not project_id:
            st.error("Variable d'environnement GCP_PROJECT non définie.")
            return None
    try:
        name = f"projects/{project_id}/secrets/PAYFLOW_PASSWORD/versions/latest"
        response = client.access_secret_version(request={"name": name})
        password = response.payload.data.decode("UTF-8").strip()
        if not password:
             st.error("Le secret PAYFLOW_PASSWORD est vide.")
             return None
        return password
    except Exception as e:
        # Gère l'erreur si le secret n'existe pas
        if "NotFound" in str(e):
             st.error(f"Erreur fatale : Le secret 'PAYFLOW_PASSWORD' est introuvable dans Secret Manager.")
        else:
            st.error(f"Erreur fatale : Impossible de charger le mot de passe PAYFLOW_PASSWORD. {e}")
        st.stop() # Arrête l'app si le mdp ne peut être chargé
        return None
# --- FIN NOUVELLE FONCTION ---

@st.cache_data(ttl=60) # Cache court
def load_silae_secrets():
    """Charge les secrets SILAE depuis Google Secret Manager."""
    client = get_secret_client()
    project_id = os.environ.get("GCP_PROJECT")
    if not project_id:
        project_id = os.environ.get("GCLOUD_PROJECT")
        if not project_id:
            st.error("Variable d'environnement GCP_PROJECT non définie.")
            return None
    secrets_to_fetch = ["SILAE_CLIENT_ID", "SILAE_CLIENT_SECRET", "SILAE_SUBSCRIPTION_KEY"]
    config = {}
    try:
        for key in secrets_to_fetch:
            name = f"projects/{project_id}/secrets/{key}/versions/latest"
            response = client.access_secret_version(request={"name": name})
            value = response.payload.data.decode("UTF-8").strip() # .strip() pour nettoyer
            config_key = key.split('_', 1)[-1].lower()
            config[config_key] = value
        return config
    except Exception as e:
        st.error(f"Erreur lors du chargement des secrets Silae : {e}")
        return None

@st.cache_data(ttl=600)
def load_client_mappings():
    """Charge les clients depuis Firestore."""
    db = get_firestore_client()
    clients_config = {}
    try:
        clients_ref = db.collection("payflow_clients").stream()
        for doc in clients_ref:
            clients_config[doc.id] = doc.to_dict()
        return clients_config
    except Exception as e:
        st.error(f"Erreur lors de la lecture des clients Firestore : {e}")
        return {}

def add_client_to_firestore(doc_id, data):
    """Ajoute ou écrase un document client dans Firestore."""
    try:
        db = get_firestore_client()
        doc_ref = db.collection("payflow_clients").document(doc_id)
        doc_ref.set(data, merge=True)
        return True
    except Exception as e:
        st.error(f"Erreur d'écriture Firestore : {e}")
        return False

# --- Fonctions de connexion Odoo ---
def get_odoo_connection_details(odoo_host, database_odoo, odoo_login, odoo_password):
    """Tente de s'authentifier et retourne les détails de connexion."""
    try:
        if ".odoo.com" in odoo_host:
            url_common = f"https://{odoo_host}/xmlrpc/common"
            url_object = f"https://{odoo_host}/xmlrpc/object"
        else:
            url_common = f"https://{odoo_host}/xmlrpc/2/common"
            url_object = f"https://{odoo_host}/xmlrpc/2/object"

        common = xmlrpc.client.ServerProxy(url_common)
        uid = common.authenticate(database_odoo, odoo_login, odoo_password, {})
        if not uid:
            st.error("Échec de l'authentification Odoo. Vérifiez les identifiants.")
            return None

        models = xmlrpc.client.ServerProxy(url_object)

        # Fonction execute() imbriquée pour cette session
        def execute(model, method, *args, **kwargs):
            # La syntaxe d'execute_kw est (db, uid, password, model, method, args_list, kwargs_dict)
            return models.execute_kw(database_odoo, uid, odoo_password, model, method, args, kwargs)

        return uid, execute

    except Exception as e:
        st.error(f"Erreur Odoo (Connexion): {e}")
        return None

@st.cache_data(ttl=600)
def get_odoo_companies_and_journals(odoo_host, database_odoo, odoo_login, odoo_password):
    """Récupère les sociétés et les journaux."""
    journals_dict = {}
    company_dict = {}

    connection_details = get_odoo_connection_details(odoo_host, database_odoo, odoo_login, odoo_password)
    if not connection_details:
        return company_dict, journals_dict # Retourne des dicts vides

    uid, execute = connection_details

    try:
        # 1. Récupérer les sociétés
        company_ids_data = execute('res.users', 'read', [uid], ['company_ids'])
        company_ids = company_ids_data[0]['company_ids']

        if not company_ids: # Si vide (rare), tombe sur la société par défaut
            company_info_data = execute('res.users', 'read', [uid], ['company_id'])
            company_ids = [company_info_data[0]['company_id'][0]]

        domain_company = [('id', 'in', company_ids)]
        fields_company = ['name']
        companies_data = execute('res.company', 'search_read', domain_company, fields=fields_company, order="name")
        company_dict = {c['id']: c['name'] for c in companies_data}

        # 2. Récupérer les journaux
        journal_types = ['bank', 'cash', 'sale', 'purchase', 'general']
        domain_journal = [('type', 'in', journal_types)]
        fields_journal = ['code', 'name', 'company_id']
        journals_data = execute('account.journal', 'search_read', domain_journal, fields=fields_journal, order="code")

        journals_dict = {
            j['code']: f"{j['code']} - {j['name']} ({j['company_id'][1] if j['company_id'] else 'N/A'})"
            for j in journals_data
        }

        return company_dict, journals_dict

    except Exception as e:
        st.error(f"Erreur Odoo (lecture sociétés/journaux): {e}")
        return company_dict, journals_dict


@st.cache_data(ttl=60)
def get_execution_logs():
    """Charge les logs d'exécution depuis Firestore."""
    db = get_firestore_client()
    logs = []
    try:
        logs_ref = db.collection("payflow_logs").order_by("execution_time", direction=firestore.Query.DESCENDING).limit(100)
        for doc in logs_ref.stream():
            log_data = doc.to_dict()
            exec_time = log_data.get('execution_time')
            if exec_time:
                log_data['execution_time'] = exec_time.strftime('%Y-%m-%d %H:%M:%S')
            logs.append(log_data)
        return pd.DataFrame(logs)
    except Exception as e:
        st.error(f"Erreur lors de la lecture des logs Firestore : {e}")
        return pd.DataFrame()

# --- FONCTIONS D'IMPORT (Réintégrées depuis la Cloud Function) ---

@st.cache_data(ttl=60)
def get_silae_token_manual(SILAE_CONFIG): # --- MODIFIÉ : Passe la config en paramètre
    """Obtient un token Silae (version pour Streamlit)."""
    if not SILAE_CONFIG:
        st.error("Configuration Silae non chargée.")
        return None
    auth_url = "https://payroll-api-auth.silae.fr/oauth2/v2.0/token"
    try:
        client_id = quote(SILAE_CONFIG.get("client_id", ""))
        client_secret = quote(SILAE_CONFIG.get("client_secret", ""))
        if not client_id or not client_secret:
            st.error("Client ID ou Secret Client Silae manquant.")
            return None
        grant_type = "client_credentials"
        scope = quote("https://silaecloudb2c.onmicrosoft.com/36658aca-9556-41b7-9e48-77e90b006f34/.default")
        auth_data_string = f"grant_type={grant_type}&client_id={client_id}&client_secret={client_secret}&scope={scope}"
        auth_headers = {"Content-Type": "application/x-www-form-urlencoded"}
        response = requests.post(auth_url, data=auth_data_string, headers=auth_headers)
        response.raise_for_status()
        return response.json()["access_token"]
    except requests.exceptions.HTTPError as err:
        response_json = err.response.json()
        st.error(f"Erreur d'authentification Silae: {response_json.get('error', 'Inconnue')} - {response_json.get('error_description', '')}")
        return None
    except Exception as e:
        st.error(f"Erreur Silae inattendue (Token): {e}")
        return None

def get_silae_ecritures_manual(access_token, numero_dossier, date_debut, date_fin, SILAE_CONFIG): # --- MODIFIÉ : Passe la config en paramètre
    """Récupère les écritures Silae (version pour Streamlit)."""
    api_url = "https://payroll-api.silae.fr/payroll/v1/EcrituresComptables/EcrituresComptables4"
    subscription_key = SILAE_CONFIG.get("subscription_key")
    if not subscription_key:
        st.error("Clé d'abonnement Silae manquante.")
        return None
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
        st.error(f"Échec de la récupération des écritures Silae: {e} - Détails: {error_details}")
        return None

def import_to_odoo_auto(client_config, ecritures_data, period_str):
    """Tente d'importer les écritures dans Odoo (Gère le Multi-Société)."""
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
            raise Exception("Échec d'authentification Odoo. Vérifiez login/clé API/base de données.")

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

        move_info = execute('account.move', 'read', [move_id], ['name'])
        move_name = move_info[0].get('name') if move_info and move_info[0].get('name') else f"ID {move_id}"
        return "SUCCESS", f"Pièce créée (Brouillon): {move_name}"

    except xmlrpc.client.Fault as e:
        st.error(traceback.format_exc())
        return "ERROR_ODOO_RPC", f"Erreur Odoo (Fault): {str(e)}"
    except Exception as e:
        st.error(traceback.format_exc())
        return "ERROR_UNKNOWN", f"Erreur inattendue: {str(e)}"


def log_execution(client_doc_id, client_name, period_str, status, message):
    """Enregistre le résultat dans la collection payflow_logs de Firestore."""
    db = get_firestore_client()
    if not db:
        st.error(f"ERREUR: Client Firestore non dispo, log non enregistré pour {client_doc_id}")
        return
    try:
        log_entry = {
            "client_doc_id": client_doc_id, "client_name": client_name,
            "period": period_str, "execution_time": datetime.utcnow(),
            "status": status, "message": message[:1500]
        }
        log_doc_id = f"{client_doc_id}_{period_str}_{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}"
        db.collection("payflow_logs").document(log_doc_id).set(log_entry)
        st.success(f"Log enregistré pour {client_name} - Statut: {status}")
    except Exception as e:
        st.error(f"ERREUR: Échec d'écriture du log Firestore pour {client_doc_id}: {e}")

# --- FIN DES FONCTIONS D'IMPORT ---


# --- NOUVEAU : GESTION DE L'AUTHENTIFICATION ---

if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

# Fonction de rappel (callback) pour vérifier le mot de passe
def check_login():
    try:
        correct_password = get_payflow_password()
        if not correct_password:
            st.error("Le mot de passe de l'application (PAYFLOW_PASSWORD) n'est pas configuré dans Secret Manager.")
            st.session_state.logged_in = False
            return

        if st.session_state.password_input == correct_password:
            st.session_state.logged_in = True
            # Nettoyer le mot de passe de la session_state
            if 'password_input' in st.session_state:
                del st.session_state.password_input
        else:
            st.error("Mot de passe incorrect.")
            st.session_state.logged_in = False
    except Exception as e:
        st.error(f"Erreur lors de la vérification du mot de passe : {e}")
        st.session_state.logged_in = False

# --- INTERFACE PRINCIPALE (conditionnée par le login) ---

if not st.session_state.logged_in:
    st.title("🔒 PayFlow - Connexion")
    st.write("Veuillez entrer le mot de passe d'accès à l'application.")

    with st.form("login_form"):
        st.text_input("Mot de passe", type="password", key="password_input")
        st.form_submit_button("Se connecter", on_click=check_login)

    # Pré-charge le mot de passe pour afficher les erreurs de chargement si nécessaire
    if st.button("Vérifier la configuration du mot de passe"):
        with st.spinner("Vérification..."):
            pwd = get_payflow_password()
            if pwd:
                st.success("Configuration du mot de passe (PAYFLOW_PASSWORD) OK.")

else:
    # --- MODIFIÉ : L'application principale est maintenant dans ce 'else' ---

    # --- NOUVEAU : Affichage du titre avec logo Odoo ---
    col1, col2, col3 = st.columns([0.1, 0.7, 0.2]) # Ajustez les ratios si besoin

    with col1:
        st.write("") # Colonne vide pour l'alignement si nécessaire, ou vous pouvez y mettre une petite icône Silae par exemple
    with col2:
        st.title("🚀 PayFlow - Tableau de Bord")
    with col3:
        try:
            # Assurez-vous que l'image 'odoo.png' est bien présente au même niveau que 'app.py'
            st.image("odoo.png", width=150) # Ajustez la taille selon vos préférences
        except Exception:
            st.warning("Image odoo.png non trouvée pour le titre.")
    st.write("Interface de configuration et de monitoring des imports Silae ➔ Odoo.")


    # Bouton de déconnexion dans la sidebar
    with st.sidebar:
        st.write("---")
        if st.button("Se déconnecter"):
            st.session_state.logged_in = False
            # Nettoyer les caches de données spécifiques à la session si nécessaire
            get_silae_token_manual.clear()
            get_execution_logs.clear()
            load_client_mappings.clear()
            st.rerun()

    # --- CHARGEMENT DE LA CONFIGURATION (uniquement après login) ---
    with st.spinner("Chargement de la configuration..."):
        SILAE_CONFIG = load_silae_secrets()
        CLIENTS_CONFIG = load_client_mappings()

    if not SILAE_CONFIG:
        st.error("Échec critique: Configuration Silae (Secrets) non chargée. L'import manuel est désactivé.")

    if not CLIENTS_CONFIG:
        st.info("Aucun client configuré. Veuillez en ajouter un dans l'onglet 'Administration'.")

    # --- INTERFACE PRINCIPALE (Onglets) ---

    tab_logs, tab_admin, tab_manual_import = st.tabs([
        "📊 Journal des Exécutions",
        "⚙️ Administration des Clients",
        "⚡ Import Manuel"
    ])

    # --- Onglet 1: Journal des Exécutions ---
    with tab_logs:
        st.header("Historique des imports mensuels automatisés")

        col1, col2 = st.columns([3, 1])
        with col2:
            if st.button("Rafraîchir les logs"):
                get_execution_logs.clear(); load_client_mappings.clear(); st.rerun()
        with col1:
            st.info("Cette page affiche les 100 derniers résultats d'import (succès ou échec) de la fonction automatisée.")

        with st.spinner("Chargement des logs d'exécution..."):
            logs_df = get_execution_logs()

        if logs_df.empty:
            st.warning("Aucun log d'exécution trouvé dans la base de données `payflow_logs`.")
            st.info("La fonction automatisée ne s'est peut-être pas encore exécutée. Vous pouvez la forcer via Cloud Scheduler.")
        else:
            st.subheader("Dernières exécutions")
            def color_status(val):
                if "SUCCESS" in val: color = 'green'
                elif "ERROR" in val: color = 'red'
                else: color = 'orange'
                return f'color: {color}'
            columns_to_display = ['execution_time', 'period', 'client_name', 'status', 'message']
            display_df = logs_df[[col for col in columns_to_display if col in logs_df.columns]]
            st.dataframe(display_df.style.applymap(color_status, subset=['status']), use_container_width=True)

    # --- Onglet 2: Administration des Clients ---
    with tab_admin:
        st.header("Gérer les connexions clients")
        st.info("Ajoutez ou modifiez les clients qui seront traités par la fonction mensuelle.")

        client_options = {"-- Nouveau Client --": None}
        client_options.update({cfg.get("nom", doc_id): doc_id for doc_id, cfg in sorted(CLIENTS_CONFIG.items(), key=lambda item: item[1].get("nom", item[0]))})

        if st.session_state.get("client_saved_successfully", False):
            st.session_state.admin_client_loader = "-- Nouveau Client --"
            st.session_state.client_saved_successfully = False

        def load_form_data():
            selected_doc_id = client_options.get(st.session_state.admin_client_loader)
            if selected_doc_id:
                cfg = CLIENTS_CONFIG[selected_doc_id]
                st.session_state.admin_numero_silae = selected_doc_id
                st.session_state.admin_nom = cfg.get("nom", "")
                st.session_state.admin_jour_transfert = cfg.get("jour_transfert", 1)
                st.session_state.admin_odoo_host = cfg.get("odoo_host", "")
                st.session_state.admin_database_odoo = cfg.get("database_odoo", "")
                st.session_state.admin_odoo_login = cfg.get("odoo_login", "")
                st.session_state.admin_odoo_password = cfg.get("odoo_password", "")
                st.session_state.admin_journal_actuel = cfg.get("journal_paie_odoo", "")
                st.session_state.admin_company_actuelle = cfg.get("odoo_company_id", None) # Charge l'ID de société
            else:
                st.session_state.admin_numero_silae = ""; st.session_state.admin_nom = ""; st.session_state.admin_jour_transfert = 1
                st.session_state.admin_odoo_host = ""; st.session_state.admin_database_odoo = ""; st.session_state.admin_odoo_login = ""
                st.session_state.admin_odoo_password = ""; st.session_state.admin_journal_actuel = ""; st.session_state.admin_company_actuelle = None
            st.session_state.admin_odoo_journals_list = {}; st.session_state.admin_odoo_companies_list = {}; st.session_state.admin_odoo_connection_tested = False

        st.selectbox("Charger un client pour modification", options=client_options.keys(), key="admin_client_loader", on_change=load_form_data)

        form_keys = ["admin_numero_silae", "admin_nom", "admin_jour_transfert", "admin_odoo_host", "admin_database_odoo", "admin_odoo_login", "admin_odoo_password", "admin_journal_actuel", "admin_company_actuelle"]
        for key in form_keys:
            default_value = 1 if key == "admin_jour_transfert" else (None if key == "admin_company_actuelle" else "")
            if key not in st.session_state: st.session_state[key] = default_value
        if 'admin_odoo_journals_list' not in st.session_state: st.session_state.admin_odoo_journals_list = {}
        if 'admin_odoo_companies_list' not in st.session_state: st.session_state.admin_odoo_companies_list = {} # Ajout
        if 'admin_odoo_connection_tested' not in st.session_state: st.session_state.admin_odoo_connection_tested = False

        st.markdown("---")

        with st.form(key="client_form"):
            st.subheader("Informations du client")
            col1, col2, col3 = st.columns(3)
            with col1:
                numero_dossier_silae = st.text_input("Numéro Dossier Silae (ID unique)", key="admin_numero_silae")
            with col2:
                nom = st.text_input("Nom du client (pour l'affichage)", key="admin_nom")
            with col3:
                jour_transfert = st.number_input("Jour du mois pour le transfert", min_value=1, max_value=31, step=1, key="admin_jour_transfert")

            st.subheader("Configuration Odoo (spécifique au client)")
            col1, col2 = st.columns(2)
            with col1:
                odoo_host = st.text_input("Hôte Odoo (ex: instance.odoo.com)", key="admin_odoo_host")
                odoo_login = st.text_input("Login Odoo (API)", key="admin_odoo_login")
            with col2:
                database_odoo = st.text_input("Base de données Odoo", key="admin_database_odoo")
                odoo_password = st.text_input("Clé API Odoo (Password)", type="password", key="admin_odoo_password")

            load_data_button = st.form_submit_button("Tester connexion Odoo & Charger Sociétés/Journaux")

            if load_data_button:
                if all([st.session_state.admin_odoo_host, st.session_state.admin_database_odoo, st.session_state.admin_odoo_login, st.session_state.admin_odoo_password]):
                    with st.spinner("Chargement des Sociétés et Journaux Odoo..."):
                        # --- MODIFIÉ : Clear cache spécifique avant appel ---
                        get_odoo_companies_and_journals.clear()
                        companies, journals = get_odoo_companies_and_journals(
                            st.session_state.admin_odoo_host,
                            st.session_state.admin_database_odoo,
                            st.session_state.admin_odoo_login,
                            st.session_state.admin_odoo_password
                        )
                        st.session_state.admin_odoo_companies_list = companies
                        st.session_state.admin_odoo_journals_list = journals
                        if not companies or (not journals and not companies): # Accepte si juste les sociétés chargent
                            st.error("Impossible de charger les sociétés ou les journaux. Vérifiez les infos Odoo.")
                            st.session_state.admin_odoo_connection_tested = False
                        else:
                            st.success(f"{len(companies)} société(s) et {len(journals)} journaux Odoo chargés.")
                            st.session_state.admin_odoo_connection_tested = True
                else:
                    st.warning("Veuillez remplir tous les champs de connexion Odoo avant de charger les données.")
                    st.session_state.admin_odoo_connection_tested = False

            selected_journal_code = None
            selected_company_id = None

            if st.session_state.admin_odoo_connection_tested:
                # --- Choix de la Société ---
                company_options = st.session_state.admin_odoo_companies_list
                if len(company_options) > 1:
                    st.write("Sociétés Odoo détectées (Multi-société) :")
                    company_display_options = [f"{name} (ID: {id})" for id, name in company_options.items()]
                    default_company_name = company_options.get(st.session_state.admin_company_actuelle)
                    default_index_company = 0
                    if default_company_name:
                        try:
                            default_index_company = company_display_options.index(f"{default_company_name} (ID: {st.session_state.admin_company_actuelle})")
                        except ValueError: pass
                    selected_company_display = st.selectbox("Société Odoo à utiliser", options=company_display_options, index=default_index_company, key="admin_selected_company")
                    selected_company_id = int(selected_company_display.split('(ID: ')[1].replace(')', ''))
                elif len(company_options) == 1:
                    selected_company_id = list(company_options.keys())[0]
                    st.info(f"Société Odoo unique détectée : {list(company_options.values())[0]} (ID: {selected_company_id})")
                else:
                    st.error("Aucune société Odoo n'a été trouvée pour cet utilisateur.")

                # --- Choix du Journal ---
                if st.session_state.admin_odoo_journals_list:
                    journal_options = list(st.session_state.admin_odoo_journals_list.values())
                    default_index_journal = 0
                    journal_actuel_str = st.session_state.admin_odoo_journals_list.get(st.session_state.admin_journal_actuel)
                    if journal_actuel_str in journal_options: default_index_journal = journal_options.index(journal_actuel_str)
                    selected_journal_display = st.selectbox("Journal Odoo pour la Paie", options=journal_options, index=default_index_journal, key="admin_selected_journal")
                    if selected_journal_display: selected_journal_code = selected_journal_display.split(" - ")[0]
                else:
                    st.warning("Aucun journal compatible trouvé.")

            elif st.session_state.admin_client_loader != "-- Nouveau Client --":
                st.info(f"Journal actuel: {st.session_state.admin_journal_actuel} | Société actuelle: {st.session_state.admin_company_actuelle}. (Cliquez 'Tester connexion' pour changer.)")
            else:
                st.info("Veuillez tester la connexion Odoo pour afficher la liste des sociétés et journaux.")

            st.markdown("---")
            submit_client_button = st.form_submit_button("Ajouter / Mettre à jour ce client")

            if submit_client_button:
                final_journal_code = selected_journal_code or st.session_state.admin_journal_actuel
                final_company_id = selected_company_id or st.session_state.admin_company_actuelle

                if not final_journal_code or not final_company_id:
                    st.error("Société Odoo ou Journal Odoo non sélectionné. Veuillez tester la connexion et en choisir un.")
                elif not all([st.session_state.admin_numero_silae, st.session_state.admin_nom, st.session_state.admin_odoo_host, st.session_state.admin_database_odoo, st.session_state.admin_odoo_login, st.session_state.admin_odoo_password]):
                    st.error("Veuillez remplir tous les champs d'information du client et de connexion Odoo.")
                else:
                    client_data = {
                        "nom": st.session_state.admin_nom,
                        "numero_dossier_silae": st.session_state.admin_numero_silae,
                        "jour_transfert": int(st.session_state.admin_jour_transfert),
                        "journal_paie_odoo": final_journal_code,
                        "odoo_company_id": int(final_company_id), # Sauvegarde l'ID de la société
                        "odoo_host": st.session_state.admin_odoo_host,
                        "database_odoo": st.session_state.admin_database_odoo,
                        "odoo_login": st.session_state.admin_odoo_login,
                        "odoo_password": st.session_state.admin_odoo_password,
                    }
                    with st.spinner("Enregistrement dans Firestore..."):
                        success = add_client_to_firestore(doc_id=st.session_state.admin_numero_silae, data=client_data)
                        if success:
                            st.success(f"Client '{st.session_state.admin_nom}' ajouté/mis à jour avec succès !")
                            load_client_mappings.clear(); st.session_state.client_saved_successfully = True; st.rerun()
                        else: st.error("Une erreur est survenue lors de l'ajout.")

        st.divider()

        st.subheader("Clients configurés")
        if not CLIENTS_CONFIG:
            st.info("Aucun client configuré.")
        else:
            clients_list = []
            for doc_id, config in CLIENTS_CONFIG.items():
                clients_list.append({
                    "ID Document (N° Silae)": doc_id, "Nom Client": config.get("nom", "N/A"),
                    "Jour Transfert": config.get("jour_transfert", "N/A"),
                    "Hôte Odoo": config.get("odoo_host", "N/A"),
                    "Base Odoo": config.get("database_odoo", "N/A"),
                    "Journal Paie Odoo": config.get("journal_paie_odoo", "N/A"),
                    "ID Société Odoo": config.get("odoo_company_id", "Non concerné") # Ajout pour vérification
                })
            st.dataframe(pd.DataFrame(clients_list), use_container_width=True)


    # --- Onglet 3: Import Manuel ---
    with tab_manual_import:
        st.header("⚡ Forcer un import manuel")
        st.warning("Cette action est destinée au débogage ou aux imports urgents. L'import automatique s'exécute déjà selon le jour configuré pour chaque client.")

        if not CLIENTS_CONFIG:
            st.error("Aucun client n'est configuré. Veuillez en ajouter un dans l'onglet 'Administration'.")
        elif not SILAE_CONFIG:
            st.error("Configuration Silae (Secrets) non chargée. L'import manuel est désactivé.")
        else:
            client_name_map = {cfg.get("nom", doc_id): doc_id for doc_id, cfg in CLIENTS_CONFIG.items()}
            selected_name = st.selectbox("1. Sélectionner un client", client_name_map.keys())

            st.write("2. Sélectionner la période à importer")
            today = datetime.now()
            col1, col2 = st.columns(2)
            with col1:
                month = st.selectbox("Mois", range(1, 13), index=today.month - 1, key="manual_month")
            with col2:
                year = st.number_input("Année", 2020, 2030, value=today.year, key="manual_year")

            date_debut = datetime(year, month, 1)
            date_fin = (date_debut + pd.DateOffset(months=1) - pd.DateOffset(days=1))
            period_str = date_debut.strftime('%Y-%m')
            st.write(f"Période cible : **{period_str}**")

            if st.button(f"Lancer l'import pour {selected_name} (Période: {period_str})"):
                client_doc_id = client_name_map[selected_name]
                client_config = CLIENTS_CONFIG[client_doc_id]
                client_name = client_config.get("nom", client_doc_id)
                silae_dossier = client_config.get("numero_dossier_silae")

                if not silae_dossier:
                    st.error(f"Client {client_name} n'a pas de 'numero_dossier_silae' configuré.")
                elif not client_config.get("odoo_company_id"):
                    st.error(f"Client {client_name} n'a pas d'ID de société Odoo configuré. Veuillez le configurer dans l'onglet Admin.")
                else:
                    try:
                        with st.spinner("Étape 1/4 : Obtention du token Silae..."):
                            # --- MODIFIÉ : Passe SILAE_CONFIG ---
                            silae_token = get_silae_token_manual(SILAE_CONFIG)
                        if silae_token:
                            with st.spinner(f"Étape 2/4 : Récupération des écritures Silae pour {client_name} (Période: {period_str})..."):
                                # --- MODIFIÉ : Passe SILAE_CONFIG ---
                                ecritures_silae = get_silae_ecritures_manual(silae_token, silae_dossier, date_debut, date_fin, SILAE_CONFIG)
                            if ecritures_silae:
                                with st.spinner("Étape 3/4 : Tentative d'import Odoo..."):
                                    status, message = import_to_odoo_auto(client_config, ecritures_silae, period_str)
                                st.subheader("Résultat de l'import :")
                                if status.startswith("SUCCESS"):
                                    st.success(message)
                                else:
                                    st.error(f"Erreur d'import : {message}")
                                with st.spinner("Étape 4/4 : Enregistrement du log..."):
                                    log_execution(client_doc_id, client_name, period_str, f"MANUAL_{status}", message)
                                st.balloons()
                                st.info("L'import manuel est terminé. Le journal des exécutions a été mis à jour.")
                                get_execution_logs.clear()
                            else:
                                st.error(f"Aucune écriture Silae trouvée pour {client_name} (Période: {period_str}).")
                                log_execution(client_doc_id, client_name, period_str, "MANUAL_ERROR_NO_DATA", "Aucune écriture Silae trouvée.")
                    except Exception as e:
                        st.error(f"Une erreur imprévue est survenue lors de l'import manuel : {e}")
                        log_execution(client_doc_id, client_name, period_str, f"MANUAL_ERROR_FUNCTION ({type(e).__name__})", str(e))