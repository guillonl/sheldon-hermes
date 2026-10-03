<!-- Généré par scripts/sync-blocks-skill.py depuis docs/app/blocs/ : ne pas modifier à la main. -->

# Figures, tables and charts

Load this sheet before your first figure, chart, comparison or table block in a conversation; each block gives its fields (required ones in bold, other accepted names in parentheses), its preview, its full view on a tap and two examples, French then English. Only `rating` with `ask` has buttons.

### `metric`

Also `kpi`. One key figure.

Fields: **`value`** (number or text), `unit`, `label`, `delta` ("+18 %", its sign gives the
direction), `trend` (`up`, `down`, `flat`), `good` (the direction that is good news, `up` by default),
`caption`, `spark` (up to 60 numbers for a small line).

Preview: the whole block. Full view: none, the block shows everything.

Examples:

```sheldon
{"type": "metric", "subtitle": "Épisode 42 · 7 derniers jours", "value": 1284, "unit": "écoutes", "delta": "+18 % par rapport à l’épisode 41", "caption": "Le pic de samedi vient de ta story Instagram."}
```

```sheldon
{"type": "metric", "subtitle": "Episode 42 · last 7 days", "value": 1284, "unit": "listens", "delta": "+18% on episode 41", "caption": "Saturday's peak comes from your Instagram story."}
```

### `stats`

Two to four figures side by side.

Fields: **`items`** (`[{label, value, delta?}]`, four at most).

Preview: the whole block. Full view: none, the block shows everything.

Examples:

```sheldon
{"type": "stats", "items": [{"label": "lus", "value": 14}, {"label": "archivés", "value": 3}, {"label": "brouillons", "value": 2}]}
```

```sheldon
{"type": "stats", "items": [{"label": "read", "value": 14}, {"label": "archived", "value": 3}, {"label": "drafts", "value": 2}]}
```

### `line`

A line over time.

Fields: **`points`** (`[{label, value}]`, 2 to 60) or **`values`** + `labels`, `unit`, `highlight`
(index of the point to name, the last by default), `area` (`true` by default).

Preview: the whole block. Full view: none, the block shows everything.

Examples:

```sheldon
{"type": "line", "subtitle": "Écoutes par épisode · 30 jours", "points": [{"label": "Ép. 38", "value": 820}, {"label": "39", "value": 910}, {"label": "40", "value": 760}, {"label": "41", "value": 1090}, {"label": "42", "value": 1284}]}
```

```sheldon
{"type": "line", "subtitle": "Listens per episode · 30 days", "points": [{"label": "Ep. 38", "value": 820}, {"label": "39", "value": 910}, {"label": "40", "value": 760}, {"label": "41", "value": 1090}, {"label": "42", "value": 1284}]}
```

### `bars`

Vertical bars (days, weeks).

Fields: **`bars`** (`[{label, value}]`, 24 at most) or **`values`** + `labels`, `unit`, `highlight`,
`source`.

Preview: the whole block. Full view: none, the block shows everything.

Examples:

```sheldon
{"type": "bars", "subtitle": "Écoutes par jour", "values": [440, 590, 510, 670, 560, 1284, 790], "labels": ["L", "M", "M", "J", "V", "S", "D"], "highlight": 5}
```

```sheldon
{"type": "bars", "subtitle": "Listens per day", "values": [440, 590, 510, 670, 560, 1284, 790], "labels": ["M", "T", "W", "T", "F", "S", "S"], "highlight": 5}
```

### `horizontalBars`

A ranking, longest bar first.

Fields: the same as `bars`.

Preview: the whole block. Full view: none, the block shows everything.

Examples:

```sheldon
{"type": "horizontalBars", "subtitle": "Sujets les plus cités cette semaine", "bars": [{"label": "IA et design", "value": 18}, {"label": "Liquid Glass", "value": 11}, {"label": "Accessibilité", "value": 7}], "highlight": 0, "source": "42 articles, 16-23 sept."}
```

```sheldon
{"type": "horizontalBars", "subtitle": "Most cited topics this week", "bars": [{"label": "AI and design", "value": 18}, {"label": "Liquid Glass", "value": 11}, {"label": "Accessibility", "value": 7}], "highlight": 0, "source": "42 articles, Sept 16-23"}
```

### `groupedBars`

Two or three series per label.

Fields: **`labels`**, **`series`** (`[{name, values}]`, three at most), `unit`.

Preview: the whole block. Full view: none, the block shows everything.

Examples:

```sheldon
{"type": "groupedBars", "subtitle": "Heures par jour", "labels": ["Lun", "Mar", "Mer", "Jeu", "Ven"], "series": [{"name": "Réunions", "values": [3, 5, 2, 4, 1]}, {"name": "Concentration", "values": [4, 2, 5, 3, 6]}], "unit": "h"}
```

```sheldon
{"type": "groupedBars", "subtitle": "Hours per day", "labels": ["Mon", "Tue", "Wed", "Thu", "Fri"], "series": [{"name": "Meetings", "values": [3, 5, 2, 4, 1]}, {"name": "Focus", "values": [4, 2, 5, 3, 6]}], "unit": "h"}
```

### `range`

A low and a high per row (weather, prices).

Fields: **`ranges`** (`[{label, low, high}]`), `unit`, `highlight`, `current` (today's value, as a dot
on the highlighted row).

Preview: the whole block. Full view: none, the block shows everything.

Examples:

```sheldon
{"type": "range", "subtitle": "Paris · 5 jours", "unit": "°", "highlight": 0, "current": 19, "ranges": [{"label": "Auj.", "low": 13, "high": 22}, {"label": "Ven", "low": 12, "high": 20}, {"label": "Sam", "low": 14, "high": 24}]}
```

```sheldon
{"type": "range", "subtitle": "Montréal · 5 days", "unit": "°", "highlight": 0, "current": 19, "ranges": [{"label": "Today", "low": 13, "high": 22}, {"label": "Fri", "low": 12, "high": 20}, {"label": "Sat", "low": 14, "high": 24}]}
```

### `donut`

Also `pie`. Parts of a whole.

Fields: **`parts`** (`[{label, value}]`, at least 2; beyond 6 the last ones become "Autres"), `center`
(text in the middle), `unit`.

Preview: the whole block. Full view: none, the block shows everything.

Examples:

```sheldon
{"type": "donut", "subtitle": "D’où viennent les écoutes", "center": "1 284", "parts": [{"label": "Spotify", "value": 610}, {"label": "Apple Podcasts", "value": 380}, {"label": "YouTube", "value": 210}, {"label": "Autres applis", "value": 84}]}
```

```sheldon
{"type": "donut", "subtitle": "Where the listens come from", "center": "1,284", "parts": [{"label": "Spotify", "value": 610}, {"label": "Apple Podcasts", "value": 380}, {"label": "YouTube", "value": 210}, {"label": "Other apps", "value": 84}]}
```

### `meter`

Also `gauge`. A gauge (disk, battery, quota).

Fields: **`value`** (or `percent`), `max` (100 by default), `label`, `caption`.

Preview: the whole block. Full view: none, the block shows everything.

Examples:

```sheldon
{"type": "meter", "title": "Disque de l'ordinateur d'Hermes", "value": 72, "label": "Utilisé", "caption": "140 Go libres sur 512 Go"}
```

```sheldon
{"type": "meter", "title": "Disk of Hermes's computer", "value": 72, "label": "Used", "caption": "140 GB free of 512 GB"}
```

### `progress`

A progress bar.

Fields: **`value`** and `total` (100 by default), or **`percent`**; `label`, `caption`.

Preview: the whole block. Full view: none, the block shows everything.

Examples:

```sheldon
{"type": "progress", "title": "Montage de l’épisode 43", "value": 3, "total": 4, "label": "Étapes", "caption": "Reste la miniature"}
```

```sheldon
{"type": "progress", "title": "Editing episode 43", "value": 3, "total": 4, "label": "Steps", "caption": "The thumbnail is left"}
```

### `ring`

A goal as a ring.

Fields: **`value`**, `total` (or `goal`), `label`, `center`, `caption`.

Preview: the whole block. Full view: none, the block shows everything.

Examples:

```sheldon
{"type": "ring", "value": 1284, "total": 1500, "label": "Objectif du mois", "center": "86 %", "caption": "216 écoutes à trouver"}
```

```sheldon
{"type": "ring", "value": 1284, "total": 1500, "label": "Goal of the month", "center": "86%", "caption": "216 listens to go"}
```

### `heatmap`

A grid of days, like an activity calendar.

Fields: **`days`** (numbers, oldest first, or `[{date, value}]`; 371 at most) or **`values`**, `start`
(date of the first day), `headline`.

Preview: the whole block. Full view: none, the block shows everything.

Examples:

```sheldon
{"type": "heatmap", "subtitle": "Jours où tu as écouté un épisode", "headline": "21 jours", "start": "2026-07-27", "days": [1, 0, 2, 1, 0, 0, 3, 1, 1, 0, 2, 2, 0, 1]}
```

```sheldon
{"type": "heatmap", "subtitle": "Days you listened to an episode", "headline": "21 days", "start": "2026-07-27", "days": [1, 0, 2, 1, 0, 0, 3, 1, 1, 0, 2, 2, 0, 1]}
```

### `compare`

Two or three options, one recommended.

Fields: **`options`** (`[{name, value?, points?, pick?, badge?}]`, 2 to 3; `points`: 4 at most).

Preview: the whole block. Full view: none, the block shows everything.

Examples:

```sheldon
{"type": "compare", "title": "Quel outil pour le prototype ?", "options": [{"name": "Figma Make", "value": "Le plus fidèle", "points": ["Prototype interactif", "Reprend ton design system"], "pick": true}, {"name": "v0", "value": "Le plus rapide", "points": ["Code React prêt"]}]}
```

```sheldon
{"type": "compare", "title": "Which tool for the prototype?", "options": [{"name": "Figma Make", "value": "The most faithful", "points": ["Interactive prototype", "Uses your design system"], "pick": true}, {"name": "v0", "value": "The fastest", "points": ["React code ready"]}]}
```

### `table`

A table, check marks included.

Fields: **`columns`** (5 at most, the first may be empty), **`rows`** (30 at most; `true` and `false`
become check marks), `highlight` (index of the recommended column).

Preview: the first 5 rows, then "N autres". Full view: every row.

Examples:

```sheldon
{"type": "table", "columns": ["", "Figma Make", "Stitch", "v0"], "highlight": 1, "rows": [["Prototype", true, true, false], ["Code", false, false, true], ["Prix", "20 $", "0 $", "20 $"]]}
```

```sheldon
{"type": "table", "columns": ["", "Figma Make", "Stitch", "v0"], "highlight": 1, "rows": [["Prototype", true, true, false], ["Code", false, false, true], ["Price", "$20", "$0", "$20"]]}
```

### `rating`

A score to show (a restaurant, an app, a product), or, with `ask`, the user's opinion on recurring work
(a morning sort, a weekly report). Ask only about recurring work, never after every answer.

Fields: to show, **`value`**, `max` (5 by default, 1 to 10), `count` (the number of reviews), `scope`
("Google"); to ask, **`ask`** (`true`), `scale` (`thumbs` by default, or `stars`), `question`; and
`title`.

Preview: the score, stars in ink (half stars included), the reviews and their scope; or the question,
then two thumbs or five stars. Full view: none, the block shows everything.

Buttons: with `ask` only. One tap answers you in one message, `answer · title`: "Utile · Tri du
matin", "Pas utile · Tri du matin" or "4 sur 5 · Tri du matin". Treat it as a preference for that
recurring work. Once answered, the block shows the answer. On a locked iPhone, nothing is sent.

Examples:

```sheldon
{"type": "rating", "ask": true, "scale": "thumbs", "title": "Tri du matin", "question": "Le tri de ce matin t’a servi ?"}
```

```sheldon
{"type": "rating", "ask": true, "scale": "thumbs", "title": "Morning sort", "question": "Was this morning’s sort useful?"}
```
