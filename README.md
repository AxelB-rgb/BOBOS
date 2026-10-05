# Épure — Copilote d’optimisation des espaces

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
- `GET /api/dashboard?from=2026-01-12&to=2026-01-12` : espaces, tendances et utilisation.
- `GET /api/insights/raw?from=2026-01-12&to=2026-01-12` : anomalies brutes.
- `GET /api/insights/smart?from=2026-01-12&to=2026-01-12` : cartes d’action.
- `POST /api/actions/{id}/apply` : simulation persistante et idempotente (deux clics ne créent pas deux commandes).
- `GET /api/actions/history` : journal des simulations.

Les routes d’insights acceptent `min_hours` (défaut 2), `min_hvac_kwh` (0,08), `min_lighting_kwh` (0,05), `types`, `business_hours_only` et `limit` (défaut 200, maximum 1000). La date `to` sans heure est inclusive. Sans dates, l’API analyse tout le jeu de données ; l’interface démarre au 12 janvier pour une démo journalière lisible. Les dates invalides et les périodes inversées sont rejetées en HTTP 422. Les totaux portent sur toutes les anomalies détectées, même si le tableau est limité.

## Données et hypothèses à expliquer à l’oral

- **368 espaces** rapprochés depuis l’IFC et l’IoT ; **144 espaces** avec présence dans le jeu fourni ; **132 surfaces IFC** calculables avec les profils supportés. Aucun espace fictif n’est ajouté.
- Les coordonnées de ce fichier IFC sont en centimètres. Les surfaces sont calculées avec la formule du polygone sur les profils extrudés supportés. La géométrie non supportée reste inconnue.
- La **capacité estimée** est `surface / 4 m²`, arrondie à l’entier inférieur (minimum 1). C’est une hypothèse de planification paramétrable, pas une capacité réglementaire ou un effectif fourni par le BIM.
- La présence est **binaire**. L’utilisation est la part des heures observées où une présence est détectée. L’indicateur d’utilisation ouvrée concerne lundi–vendredi, 8h–18h ; la courbe utilise toutes les heures de la période. Il ne mesure pas le remplissage en personnes.
- Une pièce sans mesure n’est jamais déclarée vide. Une zone nécessite la présence d’observations pour tous ses espaces équipés de capteurs, et l’absence dans chacun.
- Les CSV d’énergie sont traités comme des **index cumulés** : deltas positifs par capteur, continuité entre fichiers mensuels, doublons temporels supprimés. Les deltas négatifs de remise à zéro sont ramenés à zéro. Le premier relevé sans précédent ne produit pas de consommation ; les décalages de mesure à l’intérieur d’une heure ne sont pas interpolés.
- Les compteurs identifiés par usage RE2020 / nom sont rapprochés à l’échelle pièce ou zone. Les départs généraux sont associés à une zone, même si leur champ room indique le local du compteur. Certains compteurs peuvent être imbriqués : l’énergie et le potentiel brut ne constituent pas une facture certifiée. Une consommation pendant une absence peut aussi être nécessaire techniquement.
- Seuils : présence nulle + CVC ≥ 0,08 kWh/h ou éclairage ≥ 0,05 kWh/h pendant **au moins 2 heures consécutives**. Modifier `min_hours=3` pour un critère strictement supérieur à 2 heures.
- Le filtre « heures ouvrées » conserve les épisodes qui comportent au moins deux heures ouvrées ; la consommation de l’épisode entier reste affichée. Les graphiques du contexte bâtiment gardent la période entière.
- Pas de données de réservation : aucune affirmation de salle « réservée mais vide ». Les autres exports (CO₂, eau, humidité, DOE et Excel) sont conservés mais ne sont pas requis par les trois jalons et ne sont pas incorporés à ce calcul.
- Économies : tarif hypothétique **0,20 €/kWh**, potentiel CVC **50 %**, éclairage **100 %**, revue de planning **0 %**. Pour une carte multi-preuves, on retient seulement l’énergie de la preuve la plus élevée pour limiter l’addition de compteurs potentiellement imbriqués. Ces montants portent sur la période observée ; ils ne sont ni garantis, ni extrapolés en €/jour ou €/an.
- L’application de commande est une **simulation** : aucun protocole BACnet/MQTT ni équipement réel n’est connecté. Les consignes CVC demandent une vérification de confort, sécurité et hors-gel.

## Démo de soutenance (3 minutes)

1. Ouvrir la vue d’ensemble au 12 janvier 2026. Présenter utilisation, consommation pendant les absences et 34 anomalies.
2. Ouvrir une preuve et expliquer la jointure SQL + les îlots d’heures consécutives.
3. Ouvrir le jumeau des espaces : surface, capacité estimée, présence réelle et couverture IoT.
4. Cliquer « Générer les recommandations ». Montrer la source locale ou OpenAI et les hypothèses de calcul dans Détails.
5. Appliquer une carte en simulation, ouvrir le journal, recharger : la commande reste enregistrée.
6. Montrer `/docs` et l’export JSON pour la traçabilité.

## Tests

```powershell
./.runtime/Scripts/python.exe -m unittest discover -s tests -v
node --check Backend/static/app.js
```

Les tests utilisent une base temporaire indépendante : rupture des îlots, exclusion des mesures manquantes, couverture complète des zones, totaux indépendants de la pagination, validation HTTP, validation et secours LLM, génération sans anomalies et idempotence des simulations.

## Organisation

- `Backend/etl.py` : import CSV, rapprochement des capteurs, agrégations horaires.
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
