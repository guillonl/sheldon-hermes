<!-- Généré par scripts/sync-blocks-skill.py depuis docs/app/blocs/ : ne pas modifier à la main. -->

# Layout and fallback

Load this sheet before your first notice, text, list, card or object block in a conversation, or when nothing in the catalogue fits; each block gives its fields (required ones in bold, other accepted names in parentheses), its preview, its full view on a tap, its buttons and two examples, French then English.

### `notice`

One line of information: a backup done, a site that no longer answers, a quota almost reached. Never a
decision: a question stays `ask` or `choice`. One per message by default.

Fields: **`text`** (200 characters at most), `level` (`info`, `success`, `warning` or `error`; `info`
by default), `action` (one button: a label, or `{label, url}`).

Preview: one line in a gray inset, with the symbol of its level; orange for `error` only, green for
`success`. Full view: none, the line shows everything.

Buttons: `action` only, which answers you as `label · text` ("Réessayer · Le site ne répond plus.").

Examples:

```sheldon
{"type": "notice", "level": "error", "text": "Le site de la billetterie ne répond plus depuis 9 h 12. Je réessaie dans une heure.", "action": "Réessayer maintenant"}
```

```sheldon
{"type": "notice", "level": "error", "text": "The ticketing site has not answered since 9:12. I will try again in an hour.", "action": "Try again now"}
```

### `text`

A framed paragraph (inline Markdown allowed).

Fields: **`text`**, `title`.

Preview: the whole block. Full view: none, the block shows everything.

Examples:

```sheldon
{"type": "text", "title": "En bref", "text": "Le montage est **fini**, il reste la miniature."}
```

```sheldon
{"type": "text", "title": "In short", "text": "The edit is **done**, only the thumbnail is left."}
```

### `list`

Rows, each with a link or a button.

Fields: **`items`** (`[{title, subtitle?, value?, url?, action?, mail?, object?}]`, 20 at most),
`style` (`rows` by default, or `carousel`). A row's `mail` (the fields of a `mail` block) opens that mail
in full on a tap: an inbox digest, the newsletters you archived.

Preview: the first 5 rows, then "N autres". Full view: every row; a row with a `mail` opens that mail.

Buttons: one per row, its `action` (a label answers you as `label · row title`) or its `url`.

Carousel: with `"style": "carousel"` and three to eight rows that each carry an `object` (the fields of
an `object` block: three hotels, four flights), Sheldon draws the objects as cards that scroll
sideways, each with its own buttons (`label · object title`); at the largest text sizes, one under the
other. Otherwise it stays a list of rows, which do not show their `object`: always give each row its
`title`, as an older Sheldon shows only the rows.

Examples:

```sheldon
{"type": "list", "title": "Archivés", "items": [{"title": "Figma", "subtitle": "Les replays de Config sont en ligne", "action": "Restaurer", "mail": {"from": "Figma <news@example.com>", "subject": "Les replays de Config sont en ligne", "date": "2026-10-01T16:05", "body": "Les 91 conférences de Config 2026 sont en ligne, en accès libre."}}, {"title": "Medium", "subtitle": "Ta sélection de la semaine", "action": "Restaurer"}]}
```

```sheldon
{"type": "list", "title": "Archived", "items": [{"title": "Figma", "subtitle": "The Config replays are online", "action": "Restore", "mail": {"from": "Figma <news@example.com>", "subject": "The Config replays are online", "date": "2026-10-01T16:05", "body": "All 91 Config 2026 talks are online, free to watch."}}, {"title": "Medium", "subtitle": "Your weekly picks", "action": "Restore"}]}
```

### `card`

Several blocks in one frame (one level: a card inside a card is flattened).

Fields: **`blocks`**.

Preview: each of its blocks, as their own preview. Full view: each block opens as it would alone.

Buttons: those of its blocks.

Examples:

```sheldon
{"type": "card", "subtitle": "Épisode 42 · 7 derniers jours", "blocks": [{"type": "metric", "value": 1284, "unit": "écoutes", "delta": "+18 %"}, {"type": "bars", "values": [440, 590, 510, 670, 560, 1284, 790], "labels": ["L", "M", "M", "J", "V", "S", "D"], "highlight": 5}]}
```

```sheldon
{"type": "card", "subtitle": "Episode 42 · last 7 days", "blocks": [{"type": "metric", "value": 1284, "unit": "listens", "delta": "+18%"}, {"type": "bars", "values": [440, 590, 510, 670, 560, 1284, 790], "labels": ["M", "T", "W", "T", "F", "S", "S"], "highlight": 5}]}
```

### `object`

Anything the catalogue has no block for: a parcel, a ticket, a booking, an offer. Use it when nothing
in the catalogue fits, with a `kind` that names the thing; Sheldon also draws an unknown type that has
a `title` this way.

Fields: **`title`** (or `name`), `kind` (a free word Sheldon does not interpret; VoiceOver reads it),
`subtitle`, `icon` (one of `shippingbox`, `ticket`, `airplane`, `cart`, `doc.text`, `creditcard`,
`bed.double`, `fork.knife`, `briefcase`, `tag`; otherwise a neutral dot), `body` (inline Markdown, 200
characters at most), `fields` (`[{label, value}]`, 6 at most), `badges` (`[{text, tone}]`, `tone`:
`neutral`, `positive` or `urgent`; 3 at most), `image` (the id of a file you attached, as in `file`;
never a URL), `detail` (the whole text), `source` (where it comes from, as the `source` block).

Preview: the icon, the title, the subtitle, two badges and the first two fields. Full view: every
field, the body, the detail and the provenance.

Buttons: two at most, `action` (the main one) and `secondary`, or `actions` (an array of two). A button
is a label (it answers you as `label · title`) or `{label, url}` (it opens the link).

Examples:

```sheldon
{"type": "object", "kind": "colis", "title": "Colis de Camille", "subtitle": "Arrive jeudi", "icon": "shippingbox", "body": "Livré **entre 9 h et 12 h**.", "fields": [{"label": "Transporteur", "value": "Purolator"}, {"label": "Suivi", "value": "PUR-123"}], "badges": [{"text": "En route", "tone": "positive"}, {"text": "Signature", "tone": "urgent"}, {"text": "Fragile"}], "detail": "Le livreur sonne deux fois.", "action": "Suivre", "secondary": {"label": "Voir la page", "url": "https://example.com/suivi"}}
```

```sheldon
{"type": "object", "kind": "ticket", "title": "Train to Québec City", "subtitle": "Friday, 8:10 am", "icon": "ticket", "body": "Car 4, seat **52**, by the window.", "fields": [{"label": "Departure", "value": "Montréal, 8:10 am"}, {"label": "Booking", "value": "VIA-4821"}], "badges": [{"text": "Paid", "tone": "positive"}, {"text": "Non-refundable", "tone": "urgent"}, {"text": "Window"}], "detail": "Board 20 minutes before departure, with your ID.", "action": "Add to calendar", "secondary": {"label": "See the booking", "url": "https://example.com/booking"}}
```
