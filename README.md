# BOBOS
Projet BOS


# Sujet D : Le Copilote d'Optimisation des Espaces (Le Jumeau de Gestion + LLM Décisionnel)


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
