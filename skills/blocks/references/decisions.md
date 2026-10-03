<!-- Généré par scripts/sync-blocks-skill.py depuis docs/app/blocs/ : ne pas modifier à la main. -->

# Decisions

Load this sheet before your first question block in a conversation; each block gives its fields (required ones in bold, other accepted names in parentheses), its preview, its full view on a tap, its buttons and two examples, French then English.

### `ask`

Also `question`. A two-button question inside the chat.

Fields: **`question`**, **`primary`** (the button that names the action), `secondary` (the button that
declines; "Pas maintenant" by default), `detail`, `id`, `deadline`.

Preview: the whole block. Full view: none, the block shows everything.

Buttons: `primary` and `secondary`. Each sends its label back to you as `label · question`.

A decision that can wait goes to your `sheldon_propose` tool (Requests tab, orange badge,
notification), not through `ask`; your `clarify` tool only when you cannot go on without the answer
now, never for a decision that can wait.

Examples:

```sheldon
{"type": "ask", "question": "Publier l’épisode 43 demain à 8 h ?", "detail": "Spotify, Apple Podcasts, YouTube", "primary": "Programmer", "secondary": "Pas maintenant"}
```

```sheldon
{"type": "ask", "question": "Publish episode 43 tomorrow at 8 am?", "detail": "Spotify, Apple Podcasts, YouTube", "primary": "Schedule", "secondary": "Not now"}
```

### `choice`

One option out of two to six, in the chat: three time slots, four mails to archive, a first use. Use
`ask` for yes or no. The user can always write instead: the message field stays free.

Fields: **`question`** (or `title`), **`options`** (`[{label, detail?, id?}]`, or labels; 2 to 6),
`multiple` (several options at once), `max` (with `multiple`, at most this many), `submit` (the button
label; "Choisir" by default), `other` (adds "Autre…", which puts the cursor in the message field and
sends nothing), `detail`, `id`, `deadline`.

Preview: the question and three options to tick; the send button stays inactive until one is chosen.
Full view: the options beyond three, to tick as well.

Buttons: one message answers, `submit · options · question`: "Choisir · Jeudi 14 h · Quel créneau ?",
or "Choisir · Jeudi 14 h, Vendredi 10 h · Quels créneaux ?" for several. Once answered, the block shows
what was chosen. On a locked iPhone, nothing can be ticked or sent.

Examples:

```sheldon
{"type": "choice", "question": "Quel créneau pour l’appel avec Camille Roy ?", "detail": "30 minutes, en visio.", "options": [{"label": "Jeudi 14 h", "detail": "Juste après ton point d’équipe"}, {"label": "Vendredi 10 h"}, {"label": "Lundi 9 h", "detail": "Avant tes réunions"}], "submit": "Choisir", "other": true}
```

```sheldon
{"type": "choice", "question": "Which newsletters should I archive?", "options": [{"label": "Figma Weekly"}, {"label": "Medium Daily Digest", "detail": "Unread for a month"}, {"label": "Product Hunt"}, {"label": "Sidebar"}], "multiple": true, "max": 3, "submit": "Archive"}
```

### `form`

Two to five pieces of information at once: a booking, a sign-up, the details of an order. Use `choice`
for one option out of several and `ask` for yes or no.

Fields: **`fields`** (`[{id, label, kind, value?, options?, required?, placeholder?}]`; 5 at most),
`title`, `submit` (the button label; "Envoyer" by default), `id`. `kind` is `text`, `number`, `date`,
`time`, `choice` (with `options`) or `toggle`; a field of another kind is left out. `value` is what you
already know: a date as `2026-10-08`, a time as `19:30`, a toggle as `true` or `false`.

Preview: what is already filled ("Alex · Personnes : 4"), what is left to fill, then "Compléter". Full
view: a native form, one row per field; its send button stays inactive while a `required` field is
empty.

Buttons: only the send button answers, in one message of several lines: `submit · title`, then one
`label : value` line per filled field ("Envoyer · Réservation", "Nom : Alex", "Date : jeudi 20 h"). A
toggle always says its state. Once sent, the block shows what was sent. On a locked iPhone, nothing
opens or is sent.

Never ask for a secret: Hermes never asks for a secret in a form, and Sheldon has no masked
field; it leaves out any field whose label looks like a password, a verification code, a PIN,
a CVV, a card number, an IBAN, an API key or similar. A safety net on plausible wording, not an
absolute guarantee.

Examples:

```sheldon
{"type": "form", "title": "Réservation au Bistro du Port", "subtitle": "Vendredi soir", "fields": [{"id": "name", "label": "Au nom de", "kind": "text", "value": "Alex Dubois", "required": true}, {"id": "time", "label": "Heure", "kind": "time", "value": "19:30"}, {"id": "seats", "label": "Personnes", "kind": "number", "value": "4", "required": true}, {"id": "room", "label": "Salle", "kind": "choice", "options": ["Terrasse", "Salle", "Bar"]}, {"id": "allergies", "label": "Allergies", "kind": "text", "placeholder": "Aucune"}], "submit": "Réserver"}
```

```sheldon
{"type": "form", "title": "Table at the Harbour Bistro", "subtitle": "Friday night", "fields": [{"id": "name", "label": "Name", "kind": "text", "value": "Alex Dubois", "required": true}, {"id": "time", "label": "Time", "kind": "time", "value": "19:30"}, {"id": "seats", "label": "Guests", "kind": "number", "value": "4", "required": true}, {"id": "room", "label": "Seating", "kind": "choice", "options": ["Terrace", "Dining room", "Bar"]}, {"id": "allergies", "label": "Allergies", "kind": "text", "placeholder": "None"}], "submit": "Book"}
```

### `variants`

Two to four versions of the same text or visual, for the user to pick one: a post, a subject line, a
thumbnail. Use `choice` for options that are not versions of one thing.

Fields: **`items`** (`[{label, text}]` or `[{label, file}]`, `file` being the id of one of your files,
never a URL; 2 to 4; without `label`, "A", "B"…), `title`, `submit` (the button label; "Choisir" by
default).

Preview: two versions side by side (one under the other at the largest text sizes), each with its
button; "N autres" or "Lire en entier" opens every version whole. Full view: every version, whole.

Buttons: one per version, each answers you in one message, `submit · label · title`: "Choisir ·
Version B · Annonce de l’épisode 43". The user can also pick one aloud. Once chosen, the block shows the
chosen version. On a locked iPhone, nothing is sent. The version picked is a style lesson: note it as a
preference.

Examples:

```sheldon
{"type": "variants", "title": "Annonce de l’épisode 43", "subtitle": "Pour Instagram", "items": [{"label": "Version A", "text": "L’épisode 43 est en ligne : Camille Roy raconte comment l’IA a changé ses matins de designer."}, {"label": "Version B", "text": "Et si l’IA vous rendait vos matins ? Camille Roy nous ouvre son bureau dans l’épisode 43, à écouter dès ce soir."}, {"label": "Version C", "text": "Épisode 43, avec Camille Roy. Design, IA et café du matin."}], "submit": "Choisir"}
```

```sheldon
{"type": "variants", "title": "Episode 43 announcement", "subtitle": "For Instagram", "items": [{"label": "Version A", "text": "Episode 43 is out: Camille Roy tells how AI changed her mornings as a designer."}, {"label": "Version B", "text": "What if AI gave you your mornings back? Camille Roy opens her studio in episode 43, out tonight."}, {"label": "Version C", "text": "Episode 43, with Camille Roy. Design, AI and morning coffee."}], "submit": "Pick"}
```
