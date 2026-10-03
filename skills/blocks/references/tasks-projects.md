<!-- Généré par scripts/sync-blocks-skill.py depuis docs/app/blocs/ : ne pas modifier à la main. -->

# Tasks, projects and diagrams

Load this sheet before your first task, activity, todo, diff, status or diagram block in a conversation; each block gives its fields (required ones in bold, other accepted names in parentheses), its preview, its full view on a tap, its buttons and two examples, French then English. Only `activity` and `todo` have buttons.

### `task`

Also `checklist`. A task in steps.

Fields: **`steps`** (`[{title, state}]`, `state`: `done`, `running`, `pending`, `failed`; 20 at most),
or **`total`** and `done` without detail.

Preview: the first 5 steps, then "N autres". Full view: every step.

Examples:

```sheldon
{"type": "task", "title": "Résumer la veille design", "steps": [{"title": "Collecter 42 articles", "state": "done"}, {"title": "Écrire les résumés", "state": "running"}, {"title": "T’envoyer la sélection", "state": "pending"}]}
```

```sheldon
{"type": "task", "title": "Summarize the design watch", "steps": [{"title": "Collect 42 articles", "state": "done"}, {"title": "Write the summaries", "state": "running"}, {"title": "Send you the selection", "state": "pending"}]}
```

### `activity`

What you did on your own, or are doing now, with its result: "14 actions · 1 erreur". One `activity`
per task, never one message per action, never a notification of its own. What you do under a standing
permission is reported in the next `activity`.

Fields: **`items`** (`[{label, tool?, target?, time?, state?, detail?, url?}]`, `state`: `done`,
`failed`, `skipped`, `running`, `done` by default; 50 at most), `summary` (the count, "4 actions ·
1 erreur"), `cost` (a text, when you know it), `undo` (a button label, "Tout restaurer"), `stop` (a
button label, "Arrêter", shown only while a line is `running`).

Preview: the count, then three lines, errors first, then what runs, then the rest; the cost. Full
view: every line, grouped by tool, with its time, its detail and its page (`url`, https or http).

Buttons: `undo`, then `stop` while a line runs. Each answers you as `label · title`: you undo or stop
the work yourself.

Examples:

```sheldon
{"type": "activity", "title": "Tri du matin", "summary": "4 actions · 1 erreur", "cost": "0,04 $", "undo": "Tout restaurer", "items": [{"label": "Archivé « Figma Weekly »", "tool": "Mail", "time": "07:02", "state": "done"}, {"label": "Brouillon pour Camille Roy", "tool": "Mail", "time": "07:03", "state": "done", "detail": "Réponse à l’invitation de jeudi, rien n’est envoyé."}, {"label": "Page du fournisseur", "tool": "Navigateur", "time": "07:04", "state": "failed", "detail": "Délai dépassé", "url": "https://exemple.com/factures"}, {"label": "Facture rangée", "tool": "Fichiers", "target": "Factures 2026", "state": "skipped", "detail": "Déjà dans le dossier."}]}
```

```sheldon
{"type": "activity", "title": "Morning sort", "summary": "4 actions · 1 error", "cost": "$0.04", "undo": "Restore all", "items": [{"label": "Archived “Figma Weekly”", "tool": "Mail", "time": "07:02", "state": "done"}, {"label": "Draft for Camille Roy", "tool": "Mail", "time": "07:03", "state": "done", "detail": "Reply to Thursday’s invitation, nothing sent."}, {"label": "Supplier page", "tool": "Browser", "time": "07:04", "state": "failed", "detail": "Timed out", "url": "https://example.com/invoices"}, {"label": "Invoice filed", "tool": "Files", "target": "Invoices 2026", "state": "skipped", "detail": "Already in the folder."}]}
```

### `todo`

A list the user ticks off themselves: groceries, a packing list, what is left before a trip. Use `task`
for steps you carry out (`checklist` stays a `task`).

Fields: **`items`** (`[{title, done?, due?, note?, id?}]`, or titles; 20 at most), `title`, `submit`
(the button label; "Valider" by default). An item you know is `done` stays ticked.

Preview: two lines to tick, then "N autres" and "Valider (3)". Full view: every line, to tick as
well. A ticked line turns gray, never struck through.

Buttons: ticking sends nothing; the button sends one message for everything newly ticked,
`Fait · title : items` ("Fait · Courses : lait, pain, œufs"), never one message per line. Once sent, the
block shows what was done. On a locked iPhone, nothing can be ticked or sent.

Examples:

```sheldon
{"type": "todo", "title": "Valise pour Lisbonne", "items": [{"title": "Passeports", "done": true}, {"title": "Adaptateur de prise"}, {"title": "Crème solaire"}, {"title": "Chargeurs"}, {"title": "Réserver le transfert de l’aéroport", "due": "vendredi", "note": "Camille Roy peut nous déposer à l’aller."}]}
```

```sheldon
{"type": "todo", "title": "Lisbon packing list", "items": [{"title": "Passports", "done": true}, {"title": "Plug adapter"}, {"title": "Sunscreen"}, {"title": "Chargers"}, {"title": "Book the airport transfer", "due": "Friday", "note": "Camille Roy can drop us off on the way out."}]}
```

### `diff`

Two versions of a code or a text, read only: what you changed in a file, a draft or a page. Applying
the change stays a terminal command, with its own approval.

Fields: **`files`** (`[{name, language?, added?, removed?, hunks: [{lines: [{kind, text}]}]}]`, `kind`:
`add`, `remove` or `same`; a line may also be written `"+text"`, `"-text"` or `" text"`; 5 files and
200 lines in all at most; without `added` and `removed`, Sheldon counts them), or **`before`** and
`after` (two versions of one text, compared line by line), `title`.

Preview: "+12 −3 · 2 fichiers", then six lines in a fixed-width font, from just before the first
change, then "N autres". Full view: every file and every line, scrolling sideways. Each line keeps its
sign, "+" or "−": the color is never the only cue. An invisible character is shown by its code, as in
a command.

Examples:

```sheldon
{"type": "diff", "title": "Notes de l’épisode 43", "subtitle": "Ce que j’ai changé", "files": [{"name": "notes-episode-43.md", "language": "markdown", "hunks": [{"lines": [{"kind": "same", "text": "# Épisode 43 : le design à l’heure de l’IA"}, {"kind": "remove", "text": "Avec Camille Roy, on parle de design."}, {"kind": "add", "text": "Avec Camille Roy, on parle de design et d’IA au quotidien."}, {"kind": "same", "text": ""}, {"kind": "add", "text": "- 04:12 Ses outils du matin"}, {"kind": "add", "text": "- 18:40 Ce qu’elle ne délègue jamais"}, {"kind": "same", "text": "À écouter dès jeudi."}]}]}]}
```

```sheldon
{"type": "diff", "title": "Episode 43 show notes", "subtitle": "What I changed", "files": [{"name": "episode-43-notes.md", "language": "markdown", "hunks": [{"lines": [{"kind": "same", "text": "# Episode 43: design in the age of AI"}, {"kind": "remove", "text": "Camille Roy and I talk about design."}, {"kind": "add", "text": "Camille Roy and I talk about design and everyday AI."}, {"kind": "same", "text": ""}, {"kind": "add", "text": "- 04:12 Her morning tools"}, {"kind": "add", "text": "- 18:40 What she never delegates"}, {"kind": "same", "text": "Out on Thursday."}]}]}]}
```

### `status`

The state of services, problems first.

Fields: **`items`** (`[{label, state, detail?}]`, `state`: `ok`, `warning`, `error`, `off`, `info`),
`summary`.

Preview: the first 5 rows, problems first, then "N autres". Full view: every row.

Examples:

```sheldon
{"type": "status", "title": "Ordinateur d’Hermes", "summary": "Une sauvegarde à relancer", "items": [{"label": "Passerelle d’Hermes", "state": "ok"}, {"label": "Sauvegarde Time Machine", "state": "error", "detail": "Disque externe absent depuis 3 h 12"}]}
```

```sheldon
{"type": "status", "title": "Hermes's computer", "summary": "One backup to restart", "items": [{"label": "Hermes gateway", "state": "ok"}, {"label": "Time Machine backup", "state": "error", "detail": "External disk missing for 3 h 12 min"}]}
```

### `flow`

Steps that follow each other.

Fields: **`steps`** (at least 2; strings or `[{title, detail?}]`).

Preview: the whole block. Full view: none, the block shows everything.

Examples:

```sheldon
{"type": "flow", "title": "Comment je fais ta veille", "steps": [{"title": "Collecte", "detail": "42 articles"}, {"title": "Tri", "detail": "5 retenus"}, {"title": "Résumé"}, {"title": "Envoi", "detail": "7 h 30"}]}
```

```sheldon
{"type": "flow", "title": "How I run your watch", "steps": [{"title": "Collect", "detail": "42 articles"}, {"title": "Sort", "detail": "5 kept"}, {"title": "Summary"}, {"title": "Send", "detail": "7:30 am"}]}
```

### `graph`

Linked topics, one in the middle.

Fields: **`nodes`** (`[{id, label, main?}]`, 2 to 12), `edges` (`[{from, to}]`).

Preview: the whole block. Full view: none, the block shows everything.

Examples:

```sheldon
{"type": "graph", "title": "Les sujets de la semaine", "nodes": [{"id": "ia", "label": "IA", "main": true}, {"id": "ux", "label": "UX"}, {"id": "figma", "label": "Figma"}], "edges": [{"from": "ia", "to": "ux"}, {"from": "ia", "to": "figma"}]}
```

```sheldon
{"type": "graph", "title": "This week's topics", "nodes": [{"id": "ai", "label": "AI", "main": true}, {"id": "ux", "label": "UX"}, {"id": "figma", "label": "Figma"}], "edges": [{"from": "ai", "to": "ux"}, {"from": "ai", "to": "figma"}]}
```
