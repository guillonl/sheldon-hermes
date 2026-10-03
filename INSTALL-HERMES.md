# Sheldon pour Hermes : le guide d'installation

Ce fichier s'adresse à Hermes. L'utilisateur t'a écrit, sur une messagerie où il te parle déjà, quelque chose comme : « Installe le plugin Sheldon pour me relier à l'app Sheldon : suis les instructions de https://github.com/guillonl/sheldon-hermes, puis envoie-moi le QR code de Sheldon. »

Sheldon est une app iPhone et Mac qui parle à Hermes par ce plugin. Elle joint cet ordinateur seulement par Tailscale, le réseau privé de l'utilisateur, jamais par Internet.

Fais les étapes dans l'ordre. Si l'une échoue, arrête-toi et dis à l'utilisateur ce qui bloque, en une ou deux phrases.

## Avant de commencer

- Tailscale doit être installé et connecté sur cet ordinateur (`tailscale status` répond). Sinon, demande à l'utilisateur de l'installer (https://tailscale.com/download) et de s'y connecter, puis attends qu'il te dise que c'est fait. Sur macOS, si la commande `tailscale` est introuvable, utilise `/Applications/Tailscale.app/Contents/MacOS/Tailscale` à sa place avant de conclure qu'elle manque.
- Dans la console de Tailscale (page DNS), MagicDNS et HTTPS Certificates doivent être activés : sans eux, `tailscale serve --https` échoue. Si Tailscale le dit à l'étape 4, demande à l'utilisateur de les activer, puis attends qu'il te dise que c'est fait.
- Hermes 0.20.4 ou plus récent, avec son gateway de messagerie : c'est de là que tu lis ce message. Sheldon est vérifié avec Hermes 0.20.4. Sur une version dont le gateway a changé ce dont dépend sa garde des approbations, le plugin refuse de démarrer : si le QR code n'arrive pas après le redémarrage et que `${HERMES_HOME:-$HOME/.hermes}/logs/errors.log` dit « Sheldon: not started », dis à l'utilisateur que cette version d'Hermes n'est pas encore prise en charge par Sheldon.

## Installer

1. Installe le plugin : `hermes plugins install https://github.com/guillonl/sheldon-hermes --enable`.
2. Les réglages de Sheldon : `hermes config set display.platforms.sheldon.streaming true`, puis `hermes config set display.platforms.sheldon.tool_progress off`.
3. Active Sheldon, par son marqueur `enabled` : `mkdir -p "${HERMES_HOME:-$HOME/.hermes}/sheldon" && touch "${HERMES_HOME:-$HOME/.hermes}/sheldon/enabled"`.
4. Rends Sheldon joignable dans le réseau Tailscale : `tailscale serve --bg --https=8443 http://127.0.0.1:8787`. Si Tailscale refuse parce que ce compte n'en a pas le droit (souvent sous Linux), demande à l'utilisateur de lancer exactement cette commande lui-même, avec les droits d'administrateur de cet ordinateur, et attends qu'il te dise que c'est fait. Ne lance jamais `tailscale funnel` et n'active jamais Funnel : Sheldon ne doit être joignable que depuis le réseau Tailscale, jamais depuis Internet.
5. Note cette conversation pour le QR code : `hermes sheldon pair --after-restart --lang fr` (`--lang en` si la conversation est en anglais). Au redémarrage, le plugin enverra lui-même le QR code ici : tu ne vois jamais le code. Si la commande répond que cette conversation n'est pas une messagerie du gateway (cas de l'app Fetch ou de l'app Hermes), dis à l'utilisateur de lancer lui-même, dans un terminal de cet ordinateur, `hermes gateway restart` puis `hermes sheldon pair`, et de scanner le QR code qui s'affiche.
6. Le redémarrage. Depuis une messagerie, tu ne peux pas lancer `hermes gateway restart` : Hermes bloque cette commande dans son propre gateway. Dis plutôt à l'utilisateur, dans sa langue : « Envoie /restart ici. Après le redémarrage, le QR code de Sheldon arrivera ici tout seul. S'il n'arrive pas dans la minute, écris-moi « QR code ». » /restart laisse finir ton tour, redémarre le gateway, puis prévient l'utilisateur ici. Si tu tournes dans un terminal hors du gateway, lance `hermes gateway restart`.

## Le QR code

Quand l'utilisateur te demande le QR code (« QR code », « connecte Sheldon », « relie un appareil »), appelle l'outil `sheldon_pair`, dans cette conversation. Mets la ligne `MEDIA:` de son champ `reply` telle quelle dans ta réponse : elle joint l'image. Puis dis ce que demande son champ `tell`, dans la langue de la conversation : installer Tailscale sur le téléphone et le connecter avec le même compte que cet ordinateur, puis scanner ce QR code avec Sheldon. Ne donne jamais le code ni le lien en texte et ne décris pas l'image : elle reste seulement dans cette conversation. Le code sert une fois, pendant 10 minutes.

Si `sheldon_pair` refuse, répète son refus : il dit ce qui manque (Tailscale, Serve, Funnel ouvert, trop de QR codes demandés dans l'heure).

## À savoir

- Rappelle une fois à l'utilisateur de désactiver l'expiration de la clé de cet ordinateur dans la console de Tailscale (Machines, cet ordinateur, Disable key expiry) : sinon la connexion s'arrête au bout de 180 jours.
- Après l'installation, le plugin te refuse toute commande qui touche à la sécurité de Sheldon (`hermes sheldon ...`, sa base, son port, ses clés) : l'utilisateur les lance lui-même dans un terminal.
- Le plugin te refuse aussi d'écrire dans son propre dossier : garde ce que tu apprends dans le profil de l'utilisateur ou dans tes propres skills. Cette garde lit le texte des commandes : elle protège contre les erreurs et les manipulations naïves, ce n'est pas une frontière de sécurité. Conseille une fois à l'utilisateur de te faire tourner sous un compte macOS séparé, avec un dossier du plugin qui ne t'appartient pas, pour qu'il soit vraiment en lecture seule.

---

# Sheldon for Hermes: the install guide

This file is for Hermes. The user wrote to you, on a messaging platform where they already talk to you, something like: "Install the Sheldon plugin to link me to the Sheldon app: follow the instructions at https://github.com/guillonl/sheldon-hermes, then send me Sheldon's QR code."

Sheldon is an iPhone and Mac app that talks to Hermes through this plugin. It reaches this computer only through Tailscale, the user's private network, never through the Internet.

Do the steps in order. If one fails, stop and tell the user what blocks, in one or two sentences.

## Before you start

- Tailscale must be installed and logged in on this computer (`tailscale status` answers). Otherwise, ask the user to install it (https://tailscale.com/download) and log in, then wait until they say it is done. On macOS, if the `tailscale` command is not found, use `/Applications/Tailscale.app/Contents/MacOS/Tailscale` instead before concluding it is missing.
- In the Tailscale admin console (DNS page), MagicDNS and HTTPS Certificates must be on: without them, `tailscale serve --https` fails. If Tailscale says so at step 4, ask the user to turn them on, then wait until they say it is done.
- Hermes 0.20.4 or newer, with its messaging gateway: that is where you are reading this message from. Sheldon is verified with Hermes 0.20.4. On a version whose gateway changed what its guard on approvals relies on, the plugin refuses to start: if the QR code does not arrive after the restart and `${HERMES_HOME:-$HOME/.hermes}/logs/errors.log` says "Sheldon: not started", tell the user that this Hermes version is not supported by Sheldon yet.

## Install

1. Install the plugin: `hermes plugins install https://github.com/guillonl/sheldon-hermes --enable`.
2. Sheldon's settings: `hermes config set display.platforms.sheldon.streaming true`, then `hermes config set display.platforms.sheldon.tool_progress off`.
3. Turn Sheldon on, with its `enabled` marker: `mkdir -p "${HERMES_HOME:-$HOME/.hermes}/sheldon" && touch "${HERMES_HOME:-$HOME/.hermes}/sheldon/enabled"`.
4. Make Sheldon reachable inside the Tailscale network: `tailscale serve --bg --https=8443 http://127.0.0.1:8787`. If Tailscale refuses because this account is not allowed (often on Linux), ask the user to run exactly this command themselves, with this computer's administrator rights, and wait until they say it is done. Never run `tailscale funnel` and never turn Funnel on: Sheldon must only be reachable from the Tailscale network, never from the Internet.
5. Note this conversation for the QR code: `hermes sheldon pair --after-restart --lang en` (`--lang fr` if the conversation is in French). At the restart, the plugin will itself send the QR code here: you never see the code. If the command answers that this conversation is not a messaging platform of the gateway (the Fetch app or the Hermes app), tell the user to run, themselves, in a terminal on this computer, `hermes gateway restart` then `hermes sheldon pair`, and to scan the QR code shown.
6. The restart. From a messaging platform you cannot run `hermes gateway restart`: Hermes blocks it inside its own gateway. Tell the user instead, in their language: "Send /restart here. After the restart, Sheldon's QR code will arrive here on its own. If it does not arrive within a minute, write me \"QR code\"." /restart lets your turn finish, restarts the gateway, then tells the user here. If you run in a terminal outside the gateway, run `hermes gateway restart`.

## The QR code

When the user asks you for the QR code ("QR code", "connect Sheldon", "link a device"), call the `sheldon_pair` tool, in this conversation. Put the `MEDIA:` line of its `reply` field in your answer exactly as it is: it attaches the image. Then say what its `tell` field asks, in the language of the conversation: install Tailscale on the phone and connect it with the same account as this computer, then scan this QR code with Sheldon. Never give the code or the link as text and never describe the image: it stays in this conversation only. The code works once, for 10 minutes.

If `sheldon_pair` refuses, repeat its refusal: it says what is missing (Tailscale, Serve, Funnel open, too many QR codes asked for within the hour).

## Good to know

- Remind the user once to disable key expiry for this computer in the Tailscale admin console (Machines, this computer, Disable key expiry): otherwise the connection stops after 180 days.
- After the install, the plugin refuses you every command that touches Sheldon's security (`hermes sheldon ...`, its database, its port, its keys): the user runs them in a terminal.
- The plugin also refuses you any write into its own folder: keep what you learn in the user's profile or in your own skills. This guard reads the text of commands: it protects against mistakes and naive manipulation, it is not a security boundary. Advise the user once to run you under a separate macOS account, with a plugin folder you do not own, so it is truly read-only.
