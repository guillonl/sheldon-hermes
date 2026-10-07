"""Versions de l'extension et de sa porte d'entrée."""

__version__ = "0.2.0"
API_VERSION = 1
# Ce que cette extension sait faire en plus de l'étape 1 (GET /v1/status, champ features).
FEATURES = (
    "conversations", "agents", "requests", "feed", "threads", "creations", "files", "push", "calls", "voice",
    "pairOffers", "turnContext", "liveActivities", "blockNotes", "agentAvatars", "botChat",
)
# La version du catalogue des blocs (docs/app/blocs/catalogue.json), la même que l'en-tête de
# sheldon:blocks et que BlockCatalog.version de l'app (spec 7.3) : un appareil qui dessine un
# catalogue plus ancien le dit dans context.catalog, et Hermes l'apprend (spec 3.2).
BLOCK_CATALOG_VERSION = 3
