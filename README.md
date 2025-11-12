# 🚀 PayFlow

## Connecteur Silae ➔ Odoo automatisé

PayFlow est un outil interne conçu pour automatiser l'import des écritures comptables de paie depuis Silae vers Odoo.  
Il est conçu pour un cabinet comptable gérant plusieurs dossiers clients.

Le système repose sur deux composants principaux :

- Application web (tableau de bord) pour l'administration et le monitoring.  
- Fonction serverless (moteur) pour l'exécution automatique des tâches.

---

## ⚙️ Architecture

Le projet est hébergé entièrement sur Google Cloud Platform (GCP) et utilise les services suivants :

### `payflow-app` (Le Tableau de Bord)

- **Service** : Application Streamlit (`app.py`) déployée sur Cloud Run.  
- **Rôle** :
  - Gérer les clients : ajouter, modifier ou lister les connexions (ID Silae, Hôte Odoo, base, login, clé API, jour du transfert).  
  - Consulter les logs : afficher les journaux d’exécution (succès, échecs).  
  - Lancer un import manuel : forcer une exécution pour un client et une période donnée.

### `payflow-function` (Le Moteur)

- **Service** : Fonction Python (`main.py`) déployée sur Cloud Functions.  
- **Déclencheur** : Job Cloud Scheduler exécuté chaque jour (ex : 3h du matin).  
- **Rôle** :
  - Vérifie la date du jour (ex : "10").  
  - Interroge Firestore pour trouver les clients avec `jour_transfert = 10`.  
  - Exécute l’import Silae ➔ Odoo pour le mois précédent.  
  - Enregistre un log de succès ou d’échec dans `payflow_logs`.

### Bases de Données (Firestore)

- **Base** : `payflow-db`
- **Collections** :
  - `payflow_clients` : stocke la configuration de chaque client.
  - `payflow_logs` : historique des exécutions (auto/manuelles).

### Secrets (Secret Manager)

Contient les 3 clés API globales du cabinet pour Silae :

- `SILAE_CLIENT_ID`  
- `SILAE_CLIENT_SECRET`  
- `SILAE_SUBSCRIPTION_KEY`


---

## 🗃️ Structure du Dépôt

```
/
├── .gitignore                 # Fichiers à ignorer par Git
├── README.md                  # Ce fichier
│
├── payflow-app/               # Application Streamlit (Cloud Run)
│   ├── app.py                 # Code du tableau de bord
│   ├── Dockerfile             # Instructions du conteneur
│   ├── requirements.txt       # Dépendances Python
│   ├── lpde.png               # Logo
│   └── prelium.gif            # Logo
│
└── payflow-function/          # Fonction automatisée (Cloud Function)
    ├── main.py                # Code du moteur d'import
    └── requirements.txt       # Dépendances Python
```

---

## 🚀 Guide de Déploiement

### 1. Prérequis GCP

- Projet GCP (ex : `payflow-476410`)  
- SDK `gcloud` installé et connecté (`gcloud auth login`)  
- APIs activées :
  - Cloud Run API  
  - Cloud Functions API  
  - Cloud Build API  
  - Secret Manager API  
  - Cloud Scheduler API  
  - Eventarc API (pour triggers Pub/Sub)  
  - Cloud Datastore API (pour Firestore)

### 2. Configuration des Secrets 🔑

Créer les secrets dans **Secret Manager** :

- `SILAE_CLIENT_ID`  
- `SILAE_CLIENT_SECRET`  
- `SILAE_SUBSCRIPTION_KEY`

### 3. Configuration de Firestore 🗃️

- Mode : Natif  
- ID de la base : `payflow-db`  
- Région : `europe-west1`  
- Laisser les collections vides (elles seront créées automatiquement).

### 4. Permissions (IAM) ⚙️

Deux comptes de service sont requis :

- **Cloud Run** :  
  - Rôles : Secret Manager Secret Accessor, Cloud Datastore User  
- **Cloud Function** :  
  - Rôles : Secret Manager Secret Accessor, Cloud Datastore User  

### 5. Déploiement de la Cloud Function (Moteur)

```
# Remplacez [PROJECT_ID] et [SERVICE_ACCOUNT_EMAIL]
gcloud functions deploy process_monthly_import \
  --runtime python310 \
  --trigger-topic payflow-monthly-trigger \
  --entry-point process_monthly_import \
  --region europe-west1 \
  --project=[PROJECT_ID] \
  --set-env-vars="GCP_PROJECT=[PROJECT_ID]" \
  --service-account=[SERVICE_ACCOUNT_EMAIL] \
  --timeout=540s
```

### 6. Déploiement de l’Application Streamlit (Tableau de Bord)

```
# Remplacez [PROJECT_ID] et [SERVICE_ACCOUNT_EMAIL]
gcloud run deploy payflow-app \
  --source . \
  --platform managed \
  --region europe-west1 \
  --allow-unauthenticated \
  --project=[PROJECT_ID] \
  --set-env-vars="GCP_PROJECT=[PROJECT_ID]" \
  --service-account=[SERVICE_ACCOUNT_EMAIL]
```

### 7. Planificateur (Déclencheur) 🗓️

Créer une tâche dans **Cloud Scheduler** :

| Champ            | Valeur                               |
|------------------|--------------------------------------|
| Nom              | payflow-daily-trigger                |
| Fréquence        | 0 3 * * * (tous les jours à 3h)       |
| Fuseau horaire   | Europe/Paris                         |
| Cible            | Pub/Sub                              |
| Sujet            | payflow-monthly-trigger              |
| Charge utile     | *(vide)*                             |

---

## 💻 Utilisation

### 1. Configuration initiale (Admin)

- L’admin doit configurer la **Liaison Comptable** pour chaque client dans Silae.  
  Les numéros de compte doivent correspondre à ceux d’Odoo (aucun mapping n’est fait).  
- Ouvrir l’application PayFlow (Cloud Run URL).  
- Onglet ⚙️ **Administration des Clients** :
  - Ajouter un client :
    - Numéro dossier Silae  
    - Nom du client  
    - Jour de transfert (ex : 10)
    - Connexions Odoo (Hôte, Base, Login, Clé API)
  - Tester la connexion et sélectionner :
    - Société Odoo  
    - Journal Paie  
  - Sauvegarder.

### 2. Monitoring (Utilisateur)

- L’exécution est automatique.  
- Dans 📊 **Journal des Exécutions**, les statuts possibles sont :
  - SUCCESS : Import réussi  
  - ERROR_ACCOUNT : Liaison comptable incorrecte dans Silae  
  - ERROR_ODOO_RPC : Erreur liée à Odoo (identifiants, société, etc.)

### 3. Import manuel (Admin)

- Onglet ⚡ **Import Manuel**
  - Sélectionner un client et une période.  
  - Cliquer sur "Lancer l’import".  
  - Le résultat est affiché et loggé dans Firestore.
```

