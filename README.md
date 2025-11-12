🚀 PayFlow

Connecteur Silae ➔ Odoo automatisé.

PayFlow est un outil interne conçu pour automatiser l'import des écritures comptables de paie depuis Silae vers Odoo. Il est conçu pour un cabinet comptable gérant plusieurs dossiers clients.

Le système est divisé en deux composants principaux :

Une application web (tableau de bord) pour l'administration et le monitoring.

Une fonction serverless (moteur) pour l'exécution automatique des tâches.

⚙️ Architecture

Le projet est hébergé entièrement sur Google Cloud Platform (GCP) et utilise les services suivants :

payflow-app (Le Tableau de Bord) :

Service : Application Streamlit (app.py) déployée sur Cloud Run.

Rôle : Permet aux administrateurs de :

Gérer les clients : Ajouter, modifier ou lister les connexions (ID Silae, Hôte Odoo, base de données, login, clé API, et jour du transfert).

Consulter les logs : Afficher un journal des exécutions (succès et échecs) pour surveiller le bon fonctionnement du système.

Lancer un import manuel : Permet de forcer l'exécution pour un client et une période spécifique (pour débogage ou rattrapage).

payflow-function (Le Moteur) :

Service : Fonction Python (main.py) déployée sur Cloud Functions.

Déclencheur : Un job Cloud Scheduler s'exécute tous les jours (ex: 3h du matin).

Rôle :

La fonction se réveille et vérifie la date du jour (ex: "10").

Elle interroge Firestore pour trouver tous les clients dont le champ jour_transfert est égal à 10.

Pour chaque client trouvé, elle exécute l'import Silae ➔ Odoo pour le mois précédent.

Elle enregistre un log de succès ou d'échec dans la base payflow_logs.

Bases de Données (Firestore) :

Base : payflow-db

Collection payflow_clients : Stocke la configuration de chaque client.

Collection payflow_logs : Stocke un historique de chaque exécution (automatique ou manuelle).

Secrets (Secret Manager) :

Stocke les 3 clés API globales du cabinet pour Silae : SILAE_CLIENT_ID, SILAE_CLIENT_SECRET, SILAE_SUBSCRIPTION_KEY.

[Image de l'architecture technique de PayFlow sur GCP]

🗃️ Structure du Dépôt

Ce dépôt est un "monorepo" contenant les deux services dans des dossiers séparés.

/
├── .gitignore               # Fichiers à ignorer par Git
├── README.md                # Ce fichier
│
├── payflow-app/             # Projet de l'application Streamlit (Cloud Run)
│   ├── app.py               # Le code du tableau de bord
│   ├── Dockerfile           # Instructions pour le conteneur Cloud Run
│   ├── requirements.txt     # Dépendances Python de l'app
│   ├── lpde.png             # Logo
│   └── prelium.gif          # Logo
│
└── payflow-function/        # Projet de la fonction automatisée (Cloud Function)
    ├── main.py              # Le code du moteur d'import
    └── requirements.txt     # Dépendances Python de la fonction


🚀 Guide de Déploiement

Pour déployer ce projet sur un nouveau compte GCP :

1. Prérequis GCP

Un projet GCP (ex: payflow-476410).

Le SDK gcloud installé et authentifié (gcloud auth login).

Les API suivantes activées :

Cloud Run API

Cloud Functions API

Cloud Build API

Secret Manager API

Cloud Scheduler API

Eventarc API (pour les triggers Pub/Sub)

Cloud Datastore API (pour Firestore)

2. Configuration des Secrets 🔑

Allez dans Secret Manager et créez les 3 secrets suivants avec les valeurs fournies par Silae :

SILAE_CLIENT_ID

SILAE_CLIENT_SECRET

SILAE_SUBSCRIPTION_KEY

3. Configuration de Firestore 🗃️

Allez dans Firestore et créez une base de données avec les paramètres suivants :

Mode : Natif

ID de la base de données : payflow-db

Région : (ex: europe-west1)

Laissez les collections vides. L'application et la fonction les créeront.

4. Permissions (IAM) ⚙️

Vous avez besoin de deux comptes de service (vous pouvez aussi utiliser le compte Compute par défaut pour les deux) :

Compte de service pour Cloud Run :

Rôles requis : Secret Manager Secret Accessor, Cloud Datastore User.

Compte de service pour Cloud Function :

Rôles requis : Secret Manager Secret Accessor, Cloud Datastore User.

5. Déployer la Cloud Function (Moteur)

Naviguez dans le dossier payflow-function et exécutez :

# Remplacez [PROJECT_ID] et [SERVICE_ACCOUNT_EMAIL]
gcloud functions deploy process_monthly_import `
  --runtime python310 `
  --trigger-topic payflow-monthly-trigger `
  --entry-point process_monthly_import `
  --region europe-west1 `
  --project=[PROJECT_ID] `
  --set-env-vars="GCP_PROJECT=[PROJECT_ID]" `
  --service-account=[SERVICE_ACCOUNT_EMAIL] `
  --timeout=540s


6. Déployer l'App Streamlit (Tableau de Bord)

Naviguez dans le dossier payflow-app et exécutez :

# Remplacez [PROJECT_ID] et [SERVICE_ACCOUNT_EMAIL]
gcloud run deploy payflow-app `
  --source . `
  --platform managed `
  --region europe-west1 `
  --allow-unauthenticated `
  --project=[PROJECT_ID] `
  --set-env-vars="GCP_PROJECT=[PROJECT_ID]" `
  --service-account=[SERVICE_ACCOUNT_EMAIL]


7. Configurer le Planificateur (Déclencheur) 🗓️

Allez dans Cloud Scheduler.

Créez une tâche :

Nom : payflow-daily-trigger

Fréquence : 0 3 * * * (Tous les jours à 3h00 du matin)

Fuseau horaire : Europe/Paris

Cible : Pub/Sub

Sujet : payflow-monthly-trigger

Charge utile : Laissez vide.

Créez la tâche.

💻 Utilisation

1. Configuration Initiale (par l'Admin)

Point crucial : L'admin doit se connecter à Silae et configurer la Liaison Comptable pour chaque client. Les numéros de compte dans Silae doivent correspondre exactement aux numéros de compte dans Odoo. PayFlow ne fait pas de mapping.

Ouvrez l'application PayFlow (l'URL fournie par Cloud Run).

Allez à l'onglet "⚙️ Administration des Clients".

Ajoutez un client en remplissant le formulaire :

Numéro Dossier Silae

Nom du client

Jour du mois pour le transfert (ex: 10 pour que l'import se fasse le 10 de chaque mois)

Les informations de connexion Odoo (Hôte, Base, Login, Clé API)

Testez la connexion pour charger les Sociétés et Journaux.

Sélectionnez la bonne Société Odoo (très important en multi-société).

Sélectionnez le Journal Paie Odoo approprié.

Sauvegardez le client.

2. Monitoring (par l'utilisateur)

L'exécution est automatique.

L'utilisateur se connecte à PayFlow et ouvre l'onglet "📊 Journal des Exécutions".

Le tableau de bord affiche les succès (SUCCESS) et les échecs (ERROR).

Si status = ERROR_ACCOUNT : L'utilisateur doit contacter l'admin pour corriger la Liaison Comptable dans Silae (un compte est manquant ou erroné).

Si status = ERROR_ODOO_RPC : L'utilisateur doit contacter l'admin pour vérifier les identifiants Odoo (clé API expirée, etc.).

Si status = ERROR_ODOO_RPC: <Fault ... company inconsistencies ...> : L'admin doit corriger la Société Odoo sélectionnée dans l'onglet Admin de PayFlow.

3. Import Manuel (par l'Admin)

En cas d'erreur ou de besoin urgent, l'admin peut aller dans l'onglet "⚡ Import Manuel".

Sélectionnez un client et une période.

Cliquez sur "Lancer l'import".

Le résultat s'affichera à l'écran et sera également écrit dans le journal des logs.