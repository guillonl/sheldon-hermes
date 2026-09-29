---
name: blocks
description: Visual blocks for the Sheldon app (cards, key figures, charts, lists, events, actions). Load before writing a sheldon block.
version: "1"
metadata:
  hermes:
    tags: [sheldon, ui, blocks]
---

<!-- Généré par scripts/sync-blocks-skill.py depuis docs/app/BLOCS.md : ne pas modifier à la main. -->

# Visual blocks for Sheldon (format version 1)

Sheldon, Léo's iPhone and Mac app, draws charts, cards and two-button questions natively when your
reply contains a **block**: a fenced code block whose language is `sheldon`, holding one JSON object
with a `"type"` (and `"version": 1`). Everything around it is ordinary Markdown.

````markdown
L’épisode 42 a bien marché.

```sheldon
{"type": "metric", "version": 1, "subtitle": "Épisode 42 · 7 derniers jours", "value": 1284, "unit": "écoutes", "delta": "+18 %"}
```

Le pic de samedi vient de ta story Instagram.
````

## Rules

- Numbers go in a block, not in a sentence that lists them. Add one sentence before or after that says
  what matters.
- Something Léo must decide: an `ask` block whose `primary` button names the action ("Ajouter",
  "Programmer", "Télécharger") and whose `secondary` button declines ("Plus tard", "Pas maintenant").
- An appointment, a place, a file, a draft: the block of that object (`event`, `place`, `file`,
  `draft`), with its buttons.
- At most three blocks per message; beyond that, one `card` that groups them.
- Put `highlight` on the value that matters: Sheldon draws it in ink, the others in gray.
- Several blocks: several fences, or a JSON array of objects in one fence. An object without `type`
  but with `blocks` is a card.
- JSON may be a little loose (trailing comma, comment, unquoted key). A number may be written `1284`,
  `"1 284"` or `"12,5"`.
- A number inside text (a `center`, a chip, a title): in French, thousands are separated by a narrow
  no-break space, U+202F (`1 284 écoutes`), never a plain space; in English, by a comma
  (`1,284 listens`).
- Dates: `2026-09-26`, `2026-09-26T20:00` (device time), `2026-09-26T20:00:00Z`,
  `2026-09-26T20:00+02:00`. Times alone: `09:30`, `9h30`, `14h`.
- `title`, `subtitle` (the context: "Épisode 42 · 7 derniers jours") and `footer` (the source) frame
  almost every block.
- Write in Léo's language (French): non-breaking space before `:`, `?`, `!`, `%` and units, and inside
  « », and the typographic apostrophe `’` (`l’épisode`, `C’est fait`), never `'` outside code.
- A button without a link sends Léo's answer back to you as an ordinary message,
  `label · subject` (for example `Programmer · Publier l’épisode 43 demain à 8 h ?`). Treat it as his
  answer to your previous message. A button `{"label", "url"}` only opens the link (https, http or
  maps); it sends nothing.
- Never put anything else in a `sheldon` fence. An unknown type or a missing required field is shown
  to Léo as plain text: nothing breaks, but nothing is drawn either.

## Catalogue

Each type: its fields (required ones in bold), an example, when to use it. Other accepted names are in
parentheses.

### Figures

**`metric`** (`kpi`): one key figure. **`value`** (number or text), `unit`, `label`, `delta`
("+18 %", its sign gives the direction), `trend` (`up`, `down`, `flat`), `good` (the direction that is
good news, `up` by default), `caption`, `spark` (up to 60 numbers for a small line).

```sheldon
{"type": "metric", "subtitle": "Épisode 42 · 7 derniers jours", "value": 1284, "unit": "écoutes", "delta": "+18 % par rapport à l’épisode 41", "caption": "Le pic de samedi vient de ta story Instagram."}
```

**`stats`**: two to four figures side by side. **`items`** (`[{label, value, delta?}]`, four at most).

```sheldon
{"type": "stats", "items": [{"label": "lus", "value": 14}, {"label": "archivés", "value": 3}, {"label": "brouillons", "value": 2}]}
```

### Change over time

**`line`**: a line over time. **`points`** (`[{label, value}]`, 2 to 60) or **`values`** + `labels`,
`unit`, `highlight` (index of the point to name, the last by default), `area` (`true` by default).

```sheldon
{"type": "line", "subtitle": "Écoutes par épisode · 30 jours", "points": [{"label": "Ép. 38", "value": 820}, {"label": "39", "value": 910}, {"label": "40", "value": 760}, {"label": "41", "value": 1090}, {"label": "42", "value": 1284}]}
```

**`bars`**: vertical bars (days, weeks). **`bars`** (`[{label, value}]`, 24 at most) or **`values`** +
`labels`, `unit`, `highlight`, `source`.

```sheldon
{"type": "bars", "subtitle": "Écoutes par jour", "values": [440, 590, 510, 670, 560, 1284, 790], "labels": ["L", "M", "M", "J", "V", "S", "D"], "highlight": 5}
```

**`groupedBars`**: two or three series per label. **`labels`**, **`series`** (`[{name, values}]`,
three at most), `unit`.

```sheldon
{"type": "groupedBars", "subtitle": "Heures par jour", "labels": ["Lun", "Mar", "Mer", "Jeu", "Ven"], "series": [{"name": "Réunions", "values": [3, 5, 2, 4, 1]}, {"name": "Concentration", "values": [4, 2, 5, 3, 6]}], "unit": "h"}
```

**`range`**: a low and a high per row (weather, prices). **`ranges`** (`[{label, low, high}]`), `unit`,
`highlight`, `current` (today's value, as a dot on the highlighted row).

```sheldon
{"type": "range", "subtitle": "Paris · 5 jours", "unit": "°", "highlight": 0, "current": 19, "ranges": [{"label": "Auj.", "low": 13, "high": 22}, {"label": "Ven", "low": 12, "high": 20}, {"label": "Sam", "low": 14, "high": 24}]}
```

### Parts of a whole

**`donut`** (`pie`): parts of a whole. **`parts`** (`[{label, value}]`, at least 2; beyond 6 the last
ones become "Autres"), `center` (text in the middle), `unit`.

```sheldon
{"type": "donut", "subtitle": "D’où viennent les écoutes", "center": "1 284", "parts": [{"label": "Spotify", "value": 610}, {"label": "Apple Podcasts", "value": 380}, {"label": "YouTube", "value": 210}, {"label": "Autres applis", "value": 84}]}
```

**`meter`** (`gauge`): a gauge (disk, battery, quota). **`value`** (or `percent`), `max` (100 by
default), `label`, `caption`.

```sheldon
{"type": "meter", "title": "Disque de l'ordinateur d'Hermes", "value": 72, "label": "Utilisé", "caption": "140 Go libres sur 512 Go"}
```

### Progress

**`progress`**: a progress bar. **`value`** and `total` (100 by default), or **`percent`**; `label`,
`caption`.

```sheldon
{"type": "progress", "title": "Montage de l’épisode 43", "value": 3, "total": 4, "label": "Étapes", "caption": "Reste la miniature"}
```

**`ring`**: a goal as a ring. **`value`**, `total` (or `goal`), `label`, `center`, `caption`.

```sheldon
{"type": "ring", "value": 1284, "total": 1500, "label": "Objectif du mois", "center": "86 %", "caption": "216 écoutes à trouver"}
```

**`task`** (`checklist`): a task in steps. **`steps`** (`[{title, state}]`, `state`: `done`,
`running`, `pending`, `failed`; 20 at most), or **`total`** and `done` without detail.

```sheldon
{"type": "task", "title": "Résumer la veille design", "steps": [{"title": "Collecter 42 articles", "state": "done"}, {"title": "Écrire les résumés", "state": "running"}, {"title": "T’envoyer la sélection", "state": "pending"}]}
```

### Ranking

**`horizontalBars`**: a ranking, longest bar first. Same fields as `bars`.

```sheldon
{"type": "horizontalBars", "subtitle": "Sujets les plus cités cette semaine", "bars": [{"label": "IA et design", "value": 18}, {"label": "Liquid Glass", "value": 11}, {"label": "Accessibilité", "value": 7}], "highlight": 0, "source": "42 articles, 16-23 sept."}
```

### Regularity

**`heatmap`**: a grid of days, like an activity calendar. **`days`** (numbers, oldest first, or
`[{date, value}]`; 371 at most) or **`values`**, `start` (date of the first day), `headline`.

```sheldon
{"type": "heatmap", "subtitle": "Jours où tu as écouté un épisode", "headline": "21 jours", "start": "2026-07-27", "days": [1, 0, 2, 1, 0, 0, 3, 1, 1, 0, 2, 2, 0, 1]}
```

### Time

**`timeline`**: what happened, what comes next. **`steps`** (`[{title, time?, detail?, state}]`,
`state`: `done`, `current`, `upcoming`, `failed`).

```sheldon
{"type": "timeline", "steps": [{"title": "Repéré dans Messages", "time": "18:02", "state": "done"}, {"title": "Proposé par Hermes", "time": "18:03", "state": "done"}, {"title": "À toi de décider", "time": "maintenant", "state": "current"}]}
```

**`schedule`**: one day, with its free slots. **`events`** (`[{title, start, end?, place?}]`), `now`.

```sheldon
{"type": "schedule", "subtitle": "Demain, jeudi", "now": "08:40", "events": [{"title": "Point d’équipe", "start": "09:30", "end": "10:00"}, {"title": "Déjeuner avec Patrick", "start": "13:00", "end": "14:00", "place": "Le Mary Céleste"}]}
```

### Comparison

**`compare`**: two or three options, one recommended. **`options`**
(`[{name, value?, points?, pick?, badge?}]`, 2 to 3; `points`: 4 at most).

```sheldon
{"type": "compare", "title": "Quel outil pour le prototype ?", "options": [{"name": "Figma Make", "value": "Le plus fidèle", "points": ["Prototype interactif", "Reprend ton design system"], "pick": true}, {"name": "v0", "value": "Le plus rapide", "points": ["Code React prêt"]}]}
```

**`table`**: a table, check marks included. **`columns`** (5 at most, the first may be empty),
**`rows`** (30 at most; `true` and `false` become check marks), `highlight` (index of the recommended
column).

```sheldon
{"type": "table", "columns": ["", "Figma Make", "Stitch", "v0"], "highlight": 1, "rows": [["Prototype", true, true, false], ["Code", false, false, true], ["Prix", "20 $", "0 $", "20 $"]]}
```

### Everyday objects

These four may carry two buttons: `action` (the main one) and `secondary`, or `actions` (an array of
two). A button is a label (it answers you) or `{label, url}` (it opens the link).

**`event`** (`calendar`): an appointment. **`title`**, **`start`**, `end`, `place`, `note`.

```sheldon
{"type": "event", "title": "Dîner avec Paul", "start": "2026-09-26T20:00", "end": "2026-09-26T22:00", "place": "Le Mary Céleste, Paris", "action": "Ajouter", "secondary": "Plus tard"}
```

**`place`** (`location`, `map`): a place, with its map when coordinates are given. **`name`**,
`address`, `latitude` and `longitude`, `note`.

```sheldon
{"type": "place", "name": "Le Mary Céleste", "address": "1 rue Commines, 75003 Paris", "latitude": 48.8625, "longitude": 2.3656, "note": "12 min à pied", "action": {"label": "Y aller", "url": "https://maps.apple.com/?daddr=48.8625,2.3656"}}
```

**`file`**: a file you produced or found. **`name`**, `kind` (`pdf`, `audio`, `video`, `image`,
`document`, `sheet`, `slides`, `archive`, `code`, `other`; otherwise from the extension), `detail`
("38 min · 52 Mo"), `url`.

```sheldon
{"type": "file", "name": "episode-43.mp3", "detail": "38 min · 52 Mo", "action": {"label": "Télécharger", "url": "https://example.com/episode-43.mp3"}}
```

**`draft`**: a draft ready to go. **`body`**, `channel` (`mail`, `message`, `post`; `mail` by default),
`to`, `subject`.

```sheldon
{"type": "draft", "channel": "mail", "to": "Marie", "subject": "Re : jeudi", "body": "Oui pour jeudi, je t’envoie la maquette ce soir.", "action": "Envoyer", "secondary": "Garder en brouillon"}
```

### Status

**`status`**: the state of services, problems first. **`items`** (`[{label, state, detail?}]`,
`state`: `ok`, `warning`, `error`, `off`, `info`), `summary`.

```sheldon
{"type": "status", "title": "Ordinateur d’Hermes", "summary": "Une sauvegarde à relancer", "items": [{"label": "Passerelle d’Hermes", "state": "ok"}, {"label": "Sauvegarde Time Machine", "state": "error", "detail": "Disque externe absent depuis 3 h 12"}]}
```

### Diagrams

**`flow`**: steps that follow each other. **`steps`** (at least 2; strings or `[{title, detail?}]`).

```sheldon
{"type": "flow", "title": "Comment je fais ta veille", "steps": [{"title": "Collecte", "detail": "42 articles"}, {"title": "Tri", "detail": "5 retenus"}, {"title": "Résumé"}, {"title": "Envoi", "detail": "7 h 30"}]}
```

**`graph`**: linked topics, one in the middle. **`nodes`** (`[{id, label, main?}]`, 2 to 12), `edges`
(`[{from, to}]`).

```sheldon
{"type": "graph", "title": "Les sujets de la semaine", "nodes": [{"id": "ia", "label": "IA", "main": true}, {"id": "ux", "label": "UX"}, {"id": "figma", "label": "Figma"}], "edges": [{"from": "ia", "to": "ux"}, {"from": "ia", "to": "figma"}]}
```

### Question

**`ask`** (`question`): a two-button question inside the chat. **`question`**, **`primary`** (the
button that names the action), `secondary` (the button that declines; "Pas maintenant" by default),
`detail`, `id`, `deadline`.

```sheldon
{"type": "ask", "question": "Publier l’épisode 43 demain à 8 h ?", "detail": "Spotify, Apple Podcasts, YouTube", "primary": "Programmer", "secondary": "Pas maintenant"}
```

A request that must wait for Léo outside the chat (Requests tab, orange badge, notification) goes
through your `clarify` tool or the `sheldon_propose` tool, not through `ask`.

### Layout

**`list`**: rows, each with a link or a button. **`items`** (`[{title, subtitle?, value?, url?, action?}]`,
20 at most).

```sheldon
{"type": "list", "title": "Archivés", "items": [{"title": "Figma", "subtitle": "Les replays de Config sont en ligne", "action": "Restaurer"}, {"title": "Medium", "subtitle": "Ta sélection de la semaine", "action": "Restaurer"}]}
```

**`card`**: several blocks in one frame (one level: a card inside a card is flattened). **`blocks`**.

```sheldon
{"type": "card", "subtitle": "Épisode 42 · 7 derniers jours", "blocks": [{"type": "metric", "value": 1284, "unit": "écoutes", "delta": "+18 %"}, {"type": "bars", "values": [440, 590, 510, 670, 560, 1284, 790], "labels": ["L", "M", "M", "J", "V", "S", "D"], "highlight": 5}]}
```

**`text`**: a framed paragraph (inline Markdown allowed). **`text`**, `title`.

```sheldon
{"type": "text", "title": "En bref", "text": "Le montage est **fini**, il reste la miniature."}
```
