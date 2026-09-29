"""Textes du terminal (langue prise dans LANG) et du chat (langue du chat), en français et en anglais."""
from __future__ import annotations

import os
import re
from typing import Dict, Mapping, Sequence

TEXTS: Dict[str, Dict[str, str]] = {
    "fr": {
        "tailscale_missing": "Tailscale est introuvable ou déconnecté sur ce Mac. Installe Tailscale, connecte-toi, puis relance : hermes sheldon pair",
        "tailscale_refused": "Tailscale a refusé la commande : {detail}. Vérifie que ce compte macOS peut utiliser Tailscale, puis relance : hermes sheldon pair",
        "extension_not_running": "L'extension Sheldon est activée mais ne tourne pas encore. Lance : hermes gateway restart, puis relance : hermes sheldon pair",
        "serve_missing": "Tailscale Serve n'expose pas encore Sheldon. Lance cette commande, puis relance hermes sheldon pair :",
        "key_expiry": "Rappel : désactive l'expiration de la clé de ce Mac dans la console Tailscale (Machines, ce Mac, Disable key expiry). Sinon la connexion s'arrête au bout de 180 jours.",
        "scan": "Scanne ce QR code avec l'app Sheldon sur ton iPhone :",
        "link": "Ou colle ce lien dans Sheldon, sur l'appareil à relier :",
        "expires": "Code valable {minutes} minutes, utilisable une seule fois.",
        "each_device": "Chaque appareil se relie par son propre lien : relance hermes sheldon pair pour le suivant. hermes sheldon revoke <id> coupe un appareil (son id est dans hermes sheldon devices).",
        "devices_empty": "Aucun appareil relié.",
        "device_line": "{name} ({platform})  id {id}  relié le {created}  vu le {seen}",
        "never": "jamais",
        "revoked": "Appareil retiré. Il revient à l'écran Scanner à sa prochaine connexion.",
        "revoke_unknown": "Aucun appareil avec l'identifiant {id}.",
        "reset_confirm": "Ceci retire la clé du compte et tous les appareils : il faudra rescanner partout. Relance avec --yes pour confirmer.",
        "reset_done": "Tout est retiré. Lance hermes sheldon pair pour relier un appareil.",
        "disabled": "Extension désactivée. Lance hermes gateway restart pour l'arrêter.",
        "help_pair": "affiche le QR code à scanner avec l'iPhone",
        "help_devices": "liste les appareils reliés",
        "help_revoke": "retire un appareil",
        "help_reset": "retire la clé du compte et tous les appareils",
        "help_disable": "désactive l'extension",
        "help_push": "notifications de l'iPhone et du Mac (clé APNs d'Apple)",
        "help_push_setup": "range la clé .p8 d'Apple et ses identifiants",
        "help_push_status": "montre si les notifications sont réglées",
        "help_push_test": "envoie une notification d'essai à chaque appareil",
        "help_chats": "conversations de sujet (Podcast, Veille...)",
        "help_chats_list": "liste les conversations",
        "help_chats_add": "ajoute une conversation de sujet",
        "help_chats_remove": "retire une conversation de sujet",
        "push_setup_done": "Notifications réglées (clé {key_id}, app {topic}). Lance hermes gateway restart pour les activer, puis hermes sheldon push test.",
        "push_setup_bad_ids": "Réglage refusé : l'identifiant de la clé (--key-id) et celui de l'équipe (--team-id) font chacun 10 lettres majuscules ou chiffres, comme sur developer.apple.com.",
        "push_setup_bad_topic": "Réglage refusé : --topic est l'identifiant de l'app (bundle id), fait de lettres, de chiffres, de points et de tirets, 60 caractères au plus.",
        "push_setup_key_unreadable": "Réglage refusé : le fichier de la clé est introuvable ou illisible. Donne le chemin du fichier AuthKey_XXXXXXXXXX.p8 téléchargé sur developer.apple.com.",
        "push_setup_key_invalid": "Réglage refusé : ce fichier n'est pas une clé .p8 d'Apple (clé privée EC P-256). La clé est le fichier AuthKey_XXXXXXXXXX.p8 téléchargé sur developer.apple.com.",
        "push_setup_write_failed": "Réglage refusé : impossible d'écrire les réglages dans le dossier des notifications de Sheldon. Vérifie les droits du dossier ~/.hermes/sheldon, puis relance.",
        "push_off": "Notifications pas réglées. Lance : hermes sheldon push setup --key <AuthKey.p8> --key-id <ID> --team-id <TEAM>",
        "push_on": "Notifications réglées : clé {key_id}, équipe {team_id}, app {topic}, aperçu {preview}.",
        "push_devices": "Appareils qui reçoivent les notifications : {count}.",
        "push_no_device": "Aucun appareil n'a encore donné son jeton de notification : ouvre l'app Sheldon et accepte les notifications.",
        "push_result_ok": "{device} : notification acceptée par Apple.",
        "push_result_failed": "{device} : notification refusée par Apple (HTTP {status}, motif {reason}).",
        "unknown": "inconnu",
        "push_test_title": "Sheldon",
        "push_test_body": "Notification d'essai depuis l'ordinateur d'Hermes.",
        "push_test_key_insecure": "Notifications coupées : la clé ou son dossier ne sont plus sûrs (droits, lien symbolique, courbe). Vérifie les droits (chmod 700 sur le dossier, chmod 600 sur la clé), puis relance : hermes sheldon push test.",
        "push_test_curl_missing": "curl est introuvable sur ce Mac : installe les outils de ligne de commande (xcode-select --install), puis relance : hermes sheldon push test.",
        "push_cut_off": "Notifications réglées mais coupées : la clé ou son dossier ne sont plus sûrs (droits, lien symbolique, courbe). Vérifie les droits (chmod 700 sur le dossier, chmod 600 sur la clé), puis relance : hermes gateway restart.",
        "yes": "oui",
        "no": "non",
        "chat_line": "{id}  {title}  ({kind}, agent {agent})",
        "kind_main": "principale",
        "kind_agent": "agent",
        "kind_topic": "sujet",
        "chat_added": "Conversation ajoutée : {id}. Cible des tâches planifiées : --deliver sheldon:{id}",
        "chat_removed": "Conversation retirée. Son historique reste dans Hermes.",
        "chat_title_invalid": "Titre refusé : une seule ligne, de 1 à {max} caractères.",
        "chat_agent_not_found": "Aucun agent ne porte cet identifiant : hermes sheldon chats list montre les agents (conversations agent-<id>).",
        "chat_exists": "Une conversation porte déjà ce titre : choisis-en un autre.",
        "chat_not_found": "Aucune conversation ne porte cet identifiant : hermes sheldon chats list les montre toutes.",
        "chat_protected": "Seules les conversations de sujet se retirent : la conversation principale et celles des agents restent.",
        "chat_refused": "Commande refusée par Sheldon.",
        "pair_tool_reply": "Dis à Léo, dans la langue de cette conversation : « Installe Tailscale sur ton téléphone et connecte-le avec le même compte que cet ordinateur, puis scanne ce QR code avec Sheldon. » Ajoute que, sur le téléphone qui affiche l'image, un appui long dessus ouvre aussi Sheldon, et que le code vaut {minutes} minutes, une seule fois.",
        "pair_tool_disabled": "Sheldon est désactivé sur cet ordinateur.",
        "pair_tool_not_here": "Le QR code de Sheldon ne se donne qu'en réponse à un message de Léo dans une conversation, jamais dans une tâche planifiée ou automatique. Recours : hermes sheldon pair, par SSH.",
        "pair_tool_limit": "Déjà {count} QR codes demandés cette heure-ci : réessaie plus tard, ou lance hermes sheldon pair par SSH.",
        "pair_needs_terminal": "Lance cette commande toi-même dans un terminal.",
        "funnel_open": "Tailscale Funnel est ouvert sur ce Mac et l'expose à Internet. Ferme Funnel (tailscale funnel status montre ce qui est ouvert), puis relance : hermes sheldon pair. Sheldon ne doit être joignable que depuis ton tailnet.",
        "help_pair_after_restart": "note la conversation où envoyer le QR code au redémarrage (installation depuis un chat)",
        "help_lang": "langue du message qui accompagne le QR code : fr ou en",
        "welcome_noted": "Noté : au redémarrage du gateway, dans les {minutes} minutes, l'extension enverra elle-même le QR code de Sheldon dans cette conversation, si /restart y est envoyé. Le code n'apparaît jamais en texte.",
        "welcome_not_here": "Refusé : cette commande note seulement une installation demandée dans une conversation d'une messagerie, depuis le terminal d'Hermes. Après le redémarrage, il suffira de demander « le QR code de Sheldon ».",
        "welcome_already_paired": "Refusé : un appareil est déjà relié à Sheldon. Pour en relier un autre, demande « le QR code de Sheldon » après le redémarrage.",
        "welcome_disabled": "Refusé : Sheldon n'est pas encore activé (marqueur enabled absent). Active-le, puis relance cette commande.",
        "welcome_caption": "Sheldon est installé. Installe Tailscale sur ton téléphone et connecte-le avec le même compte que cet ordinateur, puis scanne ce QR code avec Sheldon. Sur le téléphone qui affiche l'image, un appui long dessus ouvre aussi Sheldon. Code valable {minutes} minutes, une seule fois.",
        # Plan 6, tâche 1, ronde 4 (P6-4) : une commande hors de la liste permise, dans Sheldon.
        "command_from_terminal": "Cette commande se lance depuis le terminal de l'ordinateur d'Hermes.",
    },
    "en": {
        "tailscale_missing": "Tailscale is missing or logged out on this Mac. Install Tailscale, log in, then run again: hermes sheldon pair",
        "tailscale_refused": "Tailscale refused the command: {detail}. Check that this macOS account can use Tailscale, then run again: hermes sheldon pair",
        "extension_not_running": "The Sheldon extension is enabled but not running yet. Run: hermes gateway restart, then run again: hermes sheldon pair",
        "serve_missing": "Tailscale Serve does not expose Sheldon yet. Run this command, then run hermes sheldon pair again:",
        "key_expiry": "Reminder: disable key expiry for this Mac in the Tailscale admin console (Machines, this Mac, Disable key expiry). Otherwise the connection stops after 180 days.",
        "scan": "Scan this QR code with the Sheldon app on your iPhone:",
        "link": "Or paste this link into Sheldon, on the device to link:",
        "expires": "Code valid for {minutes} minutes, single use.",
        "each_device": "Each device links with its own link: run hermes sheldon pair again for the next one. hermes sheldon revoke <id> cuts a device off (its id is in hermes sheldon devices).",
        "devices_empty": "No linked device.",
        "device_line": "{name} ({platform})  id {id}  linked {created}  seen {seen}",
        "never": "never",
        "revoked": "Device removed. It goes back to the Scan screen on its next connection.",
        "revoke_unknown": "No device with id {id}.",
        "reset_confirm": "This removes the account key and every device: you will have to scan again everywhere. Run again with --yes to confirm.",
        "reset_done": "Everything removed. Run hermes sheldon pair to link a device.",
        "disabled": "Extension disabled. Run hermes gateway restart to stop it.",
        "help_pair": "show the QR code to scan with the iPhone",
        "help_devices": "list linked devices",
        "help_revoke": "remove a device",
        "help_reset": "remove the account key and every device",
        "help_disable": "disable the extension",
        "help_push": "iPhone and Mac notifications (Apple APNs key)",
        "help_push_setup": "store the Apple .p8 key and its identifiers",
        "help_push_status": "show whether notifications are set up",
        "help_push_test": "send a test notification to every device",
        "help_chats": "topic conversations (Podcast, Design watch...)",
        "help_chats_list": "list the conversations",
        "help_chats_add": "add a topic conversation",
        "help_chats_remove": "remove a topic conversation",
        "push_setup_done": "Notifications set up (key {key_id}, app {topic}). Run hermes gateway restart to turn them on, then hermes sheldon push test.",
        "push_setup_bad_ids": "Setup refused: the key identifier (--key-id) and the team identifier (--team-id) are each 10 uppercase letters or digits, as shown on developer.apple.com.",
        "push_setup_bad_topic": "Setup refused: --topic is the app identifier (bundle id), made of letters, digits, dots and hyphens, at most 60 characters.",
        "push_setup_key_unreadable": "Setup refused: the key file is missing or unreadable. Give the path of the AuthKey_XXXXXXXXXX.p8 file downloaded from developer.apple.com.",
        "push_setup_key_invalid": "Setup refused: this file is not an Apple .p8 key (EC P-256 private key). The key is the AuthKey_XXXXXXXXXX.p8 file downloaded from developer.apple.com.",
        "push_setup_write_failed": "Setup refused: the settings could not be written to the Sheldon notifications folder. Check the permissions of the ~/.hermes/sheldon folder, then run again.",
        "push_off": "Notifications are not set up. Run: hermes sheldon push setup --key <AuthKey.p8> --key-id <ID> --team-id <TEAM>",
        "push_on": "Notifications set up: key {key_id}, team {team_id}, app {topic}, preview {preview}.",
        "push_devices": "Devices that receive notifications: {count}.",
        "push_no_device": "No device has sent its notification token yet: open the Sheldon app and allow notifications.",
        "push_result_ok": "{device}: notification accepted by Apple.",
        "push_result_failed": "{device}: notification refused by Apple (HTTP {status}, reason {reason}).",
        "unknown": "unknown",
        "push_test_title": "Sheldon",
        "push_test_body": "Test notification from Hermes's computer.",
        "push_test_key_insecure": "Notifications are cut off: the key or its folder are no longer secure (permissions, symlink, curve). Check the permissions (chmod 700 on the folder, chmod 600 on the key), then run again: hermes sheldon push test.",
        "push_test_curl_missing": "curl was not found on this Mac: install the command line tools (xcode-select --install), then run again: hermes sheldon push test.",
        "push_cut_off": "Notifications are set up but cut off: the key or its folder are no longer secure (permissions, symlink, curve). Check the permissions (chmod 700 on the folder, chmod 600 on the key), then run again: hermes gateway restart.",
        "yes": "yes",
        "no": "no",
        "chat_line": "{id}  {title}  ({kind}, agent {agent})",
        "kind_main": "main",
        "kind_agent": "agent",
        "kind_topic": "topic",
        "chat_added": "Conversation added: {id}. Scheduled task target: --deliver sheldon:{id}",
        "chat_removed": "Conversation removed. Its history stays in Hermes.",
        "chat_title_invalid": "Title refused: one line, 1 to {max} characters.",
        "chat_agent_not_found": "No agent has this identifier: hermes sheldon chats list shows the agents (agent-<id> conversations).",
        "chat_exists": "A conversation already has this title: pick another one.",
        "chat_not_found": "No conversation has this identifier: hermes sheldon chats list shows them all.",
        "chat_protected": "Only topic conversations can be removed: the main conversation and the agent ones stay.",
        "chat_refused": "Command refused by Sheldon.",
        "pair_tool_reply": "Tell Léo, in the language of this conversation: \"Install Tailscale on your phone and connect it with the same account as this computer, then scan this QR code with Sheldon.\" Add that on the phone that shows the image, a long press on it also opens Sheldon, and that the code works for {minutes} minutes, once.",
        "pair_tool_disabled": "Sheldon is disabled on this computer.",
        "pair_tool_not_here": "Sheldon's QR code is only given in reply to a message from Léo in a conversation, never in a scheduled or automatic task. Fallback: hermes sheldon pair, over SSH.",
        "pair_tool_limit": "Already {count} QR codes requested this hour: try again later, or run hermes sheldon pair over SSH.",
        "pair_needs_terminal": "Run this command yourself in a terminal.",
        "funnel_open": "Tailscale Funnel is open on this Mac and exposes it to the Internet. Turn Funnel off (tailscale funnel status shows what is open), then run again: hermes sheldon pair. Sheldon must only be reachable from your tailnet.",
        "help_pair_after_restart": "note the conversation that gets the QR code at restart (install from a chat)",
        "help_lang": "language of the message that comes with the QR code: fr or en",
        "welcome_noted": "Noted: when the gateway restarts, within {minutes} minutes, the extension will itself send Sheldon's QR code to this conversation, if /restart is sent here. The code never appears as text.",
        "welcome_not_here": "Refused: this command only notes an install requested in a messaging conversation, from Hermes's terminal. After the restart, just ask for \"Sheldon's QR code\".",
        "welcome_already_paired": "Refused: a device is already linked to Sheldon. To link another one, ask for \"Sheldon's QR code\" after the restart.",
        "welcome_disabled": "Refused: Sheldon is not enabled yet (no enabled marker). Enable it, then run this command again.",
        "welcome_caption": "Sheldon is installed. Install Tailscale on your phone and connect it with the same account as this computer, then scan this QR code with Sheldon. On the phone that shows the image, a long press on it also opens Sheldon. Code valid for {minutes} minutes, single use.",
        "command_from_terminal": "This command is run from the terminal on Hermes's computer.",
    },
}


def language(env: Mapping[str, str] = os.environ) -> str:
    value = env.get("LC_ALL") or env.get("LC_MESSAGES") or env.get("LANG") or ""
    return "fr" if value.lower().startswith("fr") else "en"


# Des mots d'un message court qui ne s'écrivent qu'en français, ou qu'en anglais.
_FRENCH_WORDS = re.compile(
    r"[àâçéèêëîïôûùüœ]|\b(je|tu|il|le|la|les|des|est|pas|oui|merci|bonjour|salut|avec|ça|moi|peux|fais|dis)\b"
)
_ENGLISH_WORDS = re.compile(
    r"\b(the|and|is|are|you|your|what|please|thanks|thank|hello|hi|can|could|with|it's|my|do|does|check)\b"
)


def chat_language(texts: Sequence[str], default: str) -> str:
    """La langue d'un chat, lue dans les derniers messages de Léo (le plus récent d'abord) : le
    premier qui a un mot français ou anglais la donne ; sinon `default`."""
    for text in texts:
        lower = text.lower()
        if _FRENCH_WORDS.search(lower):
            return "fr"
        if _ENGLISH_WORDS.search(lower):
            return "en"
    return default


def t(key: str, **values: object) -> str:
    return TEXTS[language()][key].format(**values)
