# Copilote d’optimisation des espaces

Application de Facility Management pour le Campus Dijon (ESEO / ESTP). Elle croise la maquette IFC et les CSV IoT de `Data/`, détecte les consommations en absence, propose des actions et enregistre des commandes **simulées**.

## Lancer la démo

Avec Python 3.11 ou 3.12 installé, depuis le dossier du projet :

```powershell
./start.ps1
```

Le script utilise `.runtime`, installe les dépendances si nécessaire, importe les données si la base est absente et démarre le serveur. Ouvrir **http://127.0.0.1:8000**. Documentation interactive : **http://127.0.0.1:8000/docs**.

Installation manuelle :

```powershell
py -3 -m venv .runtime
./.runtime/Scripts/python.exe -m pip install -r requirements.txt
./.runtime/Scripts/python.exe -m Backend.etl
./.runtime/Scripts/python.exe -m uvicorn Backend.app:app --host 127.0.0.1 --port 8000
```

Les exports d’énergie représentent environ 1,4 Go : le premier import peut prendre quelques minutes. Ne pas lancer deux imports simultanés. Pour réimporter, arrêter le serveur et utiliser `./start.ps1 -ImportData`. L’ETL reconstruit les tables de données, sans supprimer le journal de simulation. La base générée n’est pas versionnée ; le dossier Data reste local.

## Activer le vrai LLM

Copier `.env.example` vers `.env`, renseigner `OPENAI_API_KEY`, puis redémarrer le serveur. Ne jamais mettre la clé dans le frontend ni dans Git.

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY= votre_cle
OPENAI_MODEL=gpt-4o
ENERGY_PRICE_EUR_KWH=0.20
AREA_PER_PERSON_M2=4
```

Le serveur appelle l’API Chat Completions OpenAI avec `response_format=json_schema`, `strict=true`, un prompt système de Facility Manager et les observations JSON (au maximum 60 anomalies de l’échantillon). Voir la [documentation officielle Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

La validation Pydantic vérifie la structure ; une seconde validation rejette les références inconnues, les preuves réutilisées et les commandes incompatibles. Les économies sont calculées par le backend. En configuration `LLM_PROVIDER=auto`, si la clé manque : `source=local`. Ollama fonctionne sans clé avec `LLM_PROVIDER=ollama`. Si l’appel échoue, est refusé ou renvoie un résultat invalide : `source=local_fallback`. Ces modes sont annoncés dans l’interface. L’intégration distante est testée avec des réponses simulées ; un appel réel nécessite une clé valide.

## Les trois jalons

| Jalon | Réalisation |
| --- | --- |
| 1 · Agrégation BOS | SQLite, jointures pièce/zone, CTE, fonctions de fenêtre et îlots temporels. `GET /api/insights/raw`. |
| 2 · Prompt engineering | Prompt strict, schéma JSON, appel GPT-4o, vérification des preuves, estimations serveur et secours local. `GET /api/insights/smart`. |
| 3 · App Facility Manager | Tableau de bord responsive, courbes présence/énergie, utilisation par étage, inventaire BIM, cartes d’action, preuves, filtres, export JSON et journal persistant. |

API :

- `GET /api/health` : disponibilité du serveur et présence de la base.
- `GET /api/metadata` : dates disponibles, hypothèses, nombre d’espaces.
- `GET /api/energy/circuits?from=2026-01-12&to=2026-01-12` : consommations électriques par départ, équipements, espaces desservis et provenance.
- `GET /api/dashboard?from=2026-01-12&to=2026-01-12` : espaces, tendances et utilisation.
- `GET /api/insights/raw?from=2026-01-12&to=2026-01-12` : anomalies brutes.
- `GET /api/insights/smart?from=2026-01-12&to=2026-01-12` : cartes d’action.
- `POST /api/actions/{id}/apply` : simulation persistante et idempotente (deux clics ne créent pas deux commandes).
- `GET /api/actions/history` : journal des simulations.

Les routes d’insights acceptent `min_hours` (défaut 2), `min_hvac_kwh` (0,08), `min_lighting_kwh` (0,05), `types`, `business_hours_only` et `limit` (défaut 200, maximum 1000). La date `to` sans heure est inclusive. Sans dates, l’API analyse tout le jeu de données ; l’interface démarre au 12 janvier pour une démo journalière lisible. Les dates invalides et les périodes inversées sont rejetées en HTTP 422. Les totaux portent sur toutes les anomalies détectées, même si le tableau est limité.

## Données et hypothèses à expliquer à l’oral

- **392 espaces** rapprochés depuis l’IFC et l’IoT ; **144 espaces** avec présence dans le jeu fourni ; **132 surfaces IFC** calculables avec les profils supportés. Aucun espace fictif n’est ajouté.
- Les coordonnées de ce fichier IFC sont en centimètres. Les surfaces sont calculées avec la formule du polygone sur les profils extrudés supportés. La géométrie non supportée reste inconnue.
- La **capacité estimée** est `surface / 4 m²`, arrondie à l’entier inférieur (minimum 1). C’est une hypothèse de planification paramétrable, pas une capacité réglementaire ou un effectif fourni par le BIM.
- La présence est **binaire**. L’utilisation est la part des heures observées où une présence est détectée. L’indicateur d’utilisation ouvrée concerne lundi–vendredi, 8h–18h ; la courbe utilise toutes les heures de la période. Il ne mesure pas le remplissage en personnes.
- Une pièce sans mesure n’est jamais déclarée vide. Une zone nécessite la présence d’observations pour tous ses espaces équipés de capteurs, et l’absence dans chacun.
- Les CSV d’énergie sont traités comme des **index cumulés** : deltas positifs par capteur, continuité entre fichiers mensuels, doublons temporels supprimés. Les deltas négatifs de remise à zéro sont ramenés à zéro. Le premier relevé sans précédent ne produit pas de consommation ; les décalages de mesure à l’intérieur d’une heure ne sont pas interpolés.
- **Référentiel électrique MSI/CDE** : `Structure.elecFeeds` relie espaces et départs. Les relations de service (chauffage/air/extraction) permettent de distinguer le local d'un équipement des salles qu'il dessert. `Equipements site` et `Device site` donnent les alimentations, suivies récursivement jusqu'au premier départ. Les nomenclatures BIM sont rapprochées exactement à `TwinOps Referential.nomenclature` du CDE, sans rapprochement approximatif ni choix silencieux en cas de doublons. Le CDE fourni ne contient pas directement les identifiants `Depart_...` : la nomenclature MSI constitue le pont.
- **234 compteurs électriques**, **15 compteurs parents exclus** et **219 compteurs retenus** dans les exports fournis. `Sous-Comptage` identifie les relations parent/descendant, y compris transitives. Tout compteur ayant un descendant mesuré est exclu des sommes. Cela évite d'ajouter départ général et sous-départs, mais laisse des consommations non sous-comptées : le total affiché n'est pas une facture bâtiment.
- La consommation conserve le **grain capteur/départ/heure**, tous usages électriques. Seuls les compteurs `resource=Electricity` et les index `Depart_..._Energie_Active` / `TGBT_..._Energie_Active` alimentent ce bilan. Les mesures d'énergie thermique sur eau restent exclues du bilan électrique.
- Un départ partagé n'est jamais réparti arbitrairement entre ses salles. La colonne Électricité dédiée additionne seulement les départs retenus desservant un unique espace. Les autres restent visibles dans la vue départs, avec équipements, capteur IoT, provenance et couverture présence.
- Une anomalie de départ exige que **toutes les salles desservies**, y compris celles sans capteur connu, soient effectivement mesurées sans présence pour chaque heure. Un espace non observé bloque l'alerte. Les types `empty_zone_*` désignent alors un départ partagé et `empty_room_*` un départ dédié.
- Les référentiels datent de 2022 et 2024 et sont appliqués à l'IoT 2026. Les changements ultérieurs de câblage ne sont pas connus. **21 différences de localisation** MSI/CDE et **1 correspondance CDE multiple** sont exposées dans les détails. Les liens historiques doivent être vérifiés avant une intervention réelle. Une consommation pendant une absence peut aussi être nécessaire techniquement.
- Seuils : présence nulle + CVC ≥ 0,08 kWh/h ou éclairage ≥ 0,05 kWh/h pendant **au moins 2 heures consécutives**. Modifier `min_hours=3` pour un critère strictement supérieur à 2 heures.
- Le filtre « heures ouvrées » conserve les épisodes qui comportent au moins deux heures ouvrées ; la consommation de l’épisode entier reste affichée. Les graphiques du contexte bâtiment gardent la période entière.
- Pas de données de réservation : aucune affirmation de salle « réservée mais vide ». Les autres exports (CO₂, eau, humidité et DOE) sont conservés mais ne sont pas requis par les trois jalons et ne sont pas incorporés à ce calcul.
- Économies : tarif hypothétique **0,20 €/kWh**, potentiel CVC **50 %**, éclairage **100 %**, revue de planning **0 %**. Pour une carte multi-preuves, on retient seulement l’énergie de la preuve la plus élevée pour limiter l’addition de compteurs potentiellement imbriqués. Ces montants portent sur la période observée ; ils ne sont ni garantis, ni extrapolés en €/jour ou €/an.
- L’application de commande est une **simulation** : aucun protocole BACnet/MQTT ni équipement réel n’est connecté. Les consignes CVC demandent une vérification de confort, sécurité et hors-gel.

## Démo de soutenance (3 minutes)

1. Ouvrir la vue d’ensemble au 12 janvier 2026. Présenter utilisation, consommation pendant les absences et les anomalies calculées par départ électrique.
2. Ouvrir une preuve et expliquer la jointure SQL + les îlots d’heures consécutives.
3. Ouvrir le jumeau des espaces : surface, capacité estimée, présence réelle et couverture IoT.
4. Cliquer « Générer les recommandations ». Montrer la source locale ou OpenAI et les hypothèses de calcul dans Détails.
5. Appliquer une carte en simulation, ouvrir le journal, recharger : la commande reste enregistrée.
6. Montrer `/docs` et l’export JSON pour la traçabilité.

## Tests

```powershell
./.runtime/Scripts/python.exe -m unittest discover -s tests -v
node --check Backend/static/app.js
node --check Backend/static/electrical.js
```

Les tests utilisent une base temporaire indépendante : rupture des îlots, exclusion des mesures manquantes, couverture complète des zones, totaux indépendants de la pagination, validation HTTP, validation et secours LLM, génération sans anomalies et idempotence des simulations.

## Organisation

- `Backend/etl.py` : import CSV, rapprochement des capteurs, agrégations horaires.
- `Backend/electrical.py` : import des référentiels MSI/CDE, liens elecFeeds, sous-comptage et lectures électriques.
- `Backend/electrical_import.py` : mise à jour d’une base existante sur une copie, puis remplacement après fermeture des connexions.
- `Backend/bim.py` : surfaces des profils IFC supportés.
- `Backend/sql/` : schéma et quatre requêtes d’anomalies.
- `Backend/insights.py` : filtres, preuves et bilans complets.
- `Backend/dashboard.py` : modèles analytiques du jumeau.
- `Backend/smart.py` : prompt, schéma, LLM, validation et simulations.
- `Backend/app.py` : routes FastAPI et service des fichiers statiques.
- `Backend/static/` : interface HTML/CSS/JS sans compilation frontend.
- `tests/` : tests d’intégration.

Le dossier `Frontend/` initial est vide : l’interface est servie directement par FastAPI pour conserver un lancement unique. Le projet est une démonstration locale ; authentification et connexion aux équipements ne font pas partie de cette version.

## Ollama local (sans clé API)

Le projet peut appeler un vrai LLM local via Ollama et son schéma JSON structuré :
[documentation Ollama](https://docs.ollama.com/capabilities/structured-outputs).

Configuration `.env` :

```dotenv
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.2:latest
LLM_TIMEOUT_SECONDS=180
```

Lancer Ollama, puis `./start.ps1`. Les modèles de génération installés apparaissent dans
le sélecteur du copilote. Changer le modèle et cliquer Générer pour comparer les cartes.
`llama3.2:latest`, `llama3:latest` et `gemma:2b` sont présents sur cette machine.
`nomic-embed-text` sert aux embeddings et est exclu du sélecteur de génération.
Pour installer un modèle supplémentaire, utiliser `ollama pull NOM_DU_MODELE` puis
actualiser la page. Aucun téléchargement automatique de modèle n’est effectué.

`GET /api/llm/status` indique le fournisseur, le modèle et la connexion Ollama.
`GET /api/insights/smart?model=llama3.2:latest` permet de sélectionner un modèle installé.
Le mode Ollama sélectionne jusqu’à trois anomalies prioritaires sur des lieux distincts. Le schéma impose une carte par preuve et une commande adaptée à son équipement, pour fiabiliser les petits modèles. Les libellés et explications sont rédigés par le LLM.
Les validations des références et commandes restent identiques au mode OpenAI.
Une erreur ou une réponse invalide produit un secours déterministe explicitement annoncé.
Le premier appel peut être plus lent, car Ollama charge le modèle en mémoire.

## Mettre à jour une base existante avec les liens électriques

Arrêter le serveur, puis :

```powershell
./.runtime/Scripts/python.exe -m pip install -r requirements.txt
./.runtime/Scripts/python.exe -m Backend.electrical_import
./start.ps1
```

Cette mise à jour conserve l’occupation, le BIM et le journal des simulations. Les futurs imports complets (`start.ps1 -ImportData`) intègrent aussi les classeurs. Les sources Excel ne sont jamais modifiées.

Exemples vérifiés : `Depart_146` dessert l'extraction du laboratoire `00-02-S`, même si son compteur est localisé en `S1-05-S`. `Depart_010` est un départ d'éclairage partagé par cinq espaces. Au 12 janvier 2026, le sous-comptage retenu donne 732,783 kWh électriques, dont 121,210 kWh dédiés, 608,826 kWh partagés et 2,747 kWh sans attribution d'espace. Les tests couvrent ces règles avec des données indépendantes, les index aux frontières mensuelles, les remises à zéro et l'exclusion des mesures thermiques.

## Plan interactif (branche babas)

La navigation propose **Plan interactif** (`/map`). Les plans SVG couvrent N0 à N5 et S1. Sélectionner un étage, une période puis une salle affiche ses anomalies et les départs concernés. Les dates du tableau de bord sont conservées à l’aller et au retour.

Les anomalies de départs partagés sont affichées sur chaque salle explicitement desservie, sans répartir ni additionner plusieurs fois leur énergie. Une salle sans présence mesurée est distinguée sur la carte. Le compteur de l’étage déduplique les anomalies et la pagination est annoncée si l’API renvoie un échantillon. Aucune commande réelle n’est envoyée depuis la carte.


Sujet D : Le Copilote d'Optimisation des Espaces (Le Jumeau de Gestion + LLM Décisionnel)
• Le Jumeau : Un tableau de bord analytique croisant la capacité théorique des pièces (données BIM) et leur usage
réel (IoT : taux d'occupation).
• L'IA : L'utilisation de l'API d'un LLM (ex: GPT-4o) avec des prompts structurés (JSON).
• L'Application : Une application web pour le Facility Manager. L'API Backend agrège les anomalies de la journée (ex:
"La grande salle du 4ème était réservée mais vide, le chauffage tournait"). L'API envoie ce résumé brut au LLM, qui
génère des "Cartes d'actions" en langage naturel sur le frontend (" Suggestion : Coupez le chauffage au 4ème
les vendredis et regroupez les réunions au 1er étage. Économie estimée : 15€/jour").
Jalon 1 (L'Agrégation BOS) : Écrire des requêtes SQL complexes pour identifier les anomalies (ex: pièces avec
occupation = 0 mais HVAC > 0 sur une durée > 2h). Exposer une API GET /api/insights/raw qui renvoie ces données
sous format JSON.
Jalon 2 (Prompt Engineering & IA) : Créer une route GET /api/insights/smart. Le backend récupère le JSON brut du
Jalon 1, le formate dans un prompt strict ("Tu es un Facility Manager, analyse ces anomalies et propose 3 actions
concrètes au format JSON structuré"), et appelle l'API du LLM (GPT-4o/Claude).
Jalon 3 (L'App Facility Manager) : Coder l'interface finale. Afficher les recommandations de l'IA sous forme de "Cartes
d'action" (Titre, Raison, Économie estimée). Ajouter un bouton "Appliquer" qui simulerait l'envoi d'une commande au
système de contrôle du bâtiment.

