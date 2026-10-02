"""Versions de l'extension et de sa porte d'entrée."""

__version__ = "0.2.0"
API_VERSION = 1
# Ce que cette extension sait faire en plus de l'étape 1 (GET /v1/status, champ features).
FEATURES = (
    "conversations", "agents", "requests", "feed", "threads", "creations", "files", "push", "calls", "voice",
    "pairOffers", "turnContext", "liveActivities",
)
