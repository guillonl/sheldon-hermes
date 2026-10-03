<!-- Généré par scripts/sync-blocks-skill.py depuis docs/app/blocs/ : ne pas modifier à la main. -->

# Web and search

Load this sheet before your first link, results or digest block in a conversation; each block gives its fields (required ones in bold, other accepted names in parentheses), its preview, its full view on a tap, its buttons and two examples, French then English.

### `link`

A page you read. Sheldon downloads nothing from the page (no favicon, no preview tag): it shows the
first letter of the site in a dot.

Fields: **`url`** (https or http only), **`title`**, `site` (otherwise the host, without "www."),
`summary` (the page's summary, in your words), `points` (3 at most), `image` (the id of a file you
attached, as in `file`; never a URL), `date`, `readingTime` ("6 min"), `source` (where the link comes
from, as the `source` block).

Preview: the site's letter, the site, the title and two lines of the summary. Full view: the whole
summary, the points, the date and the reading time, then "Ouvrir la page".

Buttons: none; "Ouvrir la page" only opens the link, it sends nothing.

Examples:

```sheldon
{"type": "link", "url": "https://exemple.com/liquid-glass-un-an-apres", "title": "Liquid Glass, un an après", "site": "exemple.com", "summary": "Ce qui a tenu (les barres flottantes, les boutons ronds) et ce que les équipes ont retiré (le flou derrière le texte long).", "points": ["Les barres flottantes sont restées partout", "Le flou derrière le texte long a disparu", "Le contraste a gagné deux crans"], "date": "2026-10-01", "readingTime": "6 min"}
```

```sheldon
{"type": "link", "url": "https://example.com/designing-for-agents", "title": "Designing for agents", "site": "example.com", "summary": "Show the decision rather than the work, and let the agent act alone on what can be undone.", "points": ["One decision per screen", "Undo before confirm", "A log the user can read in ten seconds"], "date": "2026-09-30", "readingTime": "8 min", "source": {"kind": "mail", "from": "Sam Tremblay <sam.tremblay@example.com>", "subject": "Worth a read", "body": "Hi,\n\nThis one is worth your ten minutes.\n\nSam"}}
```

### `digest`

A briefing, the report you deliver most often (a morning watch, a news summary): three to five items
kept for the user, and how many you left out. One `digest` per report, never one message per item.

Fields: **`items`** (`[{title, site?, why?, url?}]`, or titles; 5 at most, 3 to 5 is best), `title`,
`subtitle`, `dropped` (how many items you read and left out), `feedback` (true on a recurring report:
adds "Plus de ça" and "Moins de ça"). An item's `site` defaults to the host of its `url` (https or http
only), without "www."; `why` says in one sentence why it is for the user.

Preview: the first three titles, each with the first letter of its site in a dot and the site, then
"37 écartés". Full view: every item with its why, and "Ouvrir" for each page.

Buttons: with `feedback`, "Plus de ça" and "Moins de ça". They never start a turn and send no message:
the user's mark reaches you as a `[Sheldon]` note with their next message in this conversation
(`The user marked your report "Veille design" of 07:30 as "less of this".`). "Ouvrir" only opens the
page, it sends nothing.

Examples:

```sheldon
{"type": "digest", "title": "Veille design", "subtitle": "Ce matin · 42 articles lus", "dropped": 37, "feedback": true, "items": [{"title": "Liquid Glass, un an après", "site": "exemple.com", "why": "Tu suis Liquid Glass depuis sa sortie.", "url": "https://exemple.com/liquid-glass"}, {"title": "Figma Motion, premier essai", "site": "exemple.com", "why": "Tu en parlais hier avec Camille Roy.", "url": "https://exemple.com/figma-motion"}, {"title": "Des graphiques que VoiceOver lit", "site": "exemple.com", "why": "Pour les blocs de chiffres de ton portfolio.", "url": "https://exemple.com/graphiques"}, {"title": "Designer pour les agents", "site": "exemple.com", "why": "Ton sujet du mois.", "url": "https://exemple.com/agents"}, {"title": "La typographie variable", "site": "exemple.com", "why": "Sam Tremblay te l’a conseillée.", "url": "https://exemple.com/typographie"}]}
```

```sheldon
{"type": "digest", "title": "Design watch", "subtitle": "This morning · 42 articles read", "dropped": 37, "feedback": true, "items": [{"title": "Liquid Glass, one year on", "site": "example.com", "why": "You have followed Liquid Glass since it came out.", "url": "https://example.com/liquid-glass"}, {"title": "Figma Motion, a first try", "site": "example.com", "why": "You talked about it with Camille Roy yesterday.", "url": "https://example.com/figma-motion"}, {"title": "Charts that VoiceOver reads", "site": "example.com", "why": "For the figure blocks of your portfolio.", "url": "https://example.com/charts"}, {"title": "Designing for agents", "site": "example.com", "why": "Your topic of the month.", "url": "https://example.com/agents"}, {"title": "Variable type", "site": "example.com", "why": "Sam Tremblay recommended it.", "url": "https://example.com/type"}]}
```

### `results`

Search results, on the web or in the user's mails or notes. Sheldon loads no page: it shows the first
letter of each site in a dot, and each link opens on a tap.

Fields: **`items`** (`[{title, url?, site?, snippet?, date?}]`, or titles; 10 at most), `query`, `total`
(your text, "128 résultats"), `scope` ("Web", "Mail", "Notes"). An item's `site` defaults to the host of
its `url` (https or http only), without "www."; for a mail, give the sender as its `site`.

Preview: the query, the scope and the total, then the first three results, then "7 autres". Full view:
every result with its date and its snippet, and "Ouvrir" for each page.

Buttons: none; "Ouvrir" only opens the page, it sends nothing.

Examples:

```sheldon
{"type": "results", "query": "devis studio", "scope": "Mail", "total": "12 résultats", "items": [{"title": "Devis pour les épisodes 43 à 46", "site": "Camille Roy", "snippet": "Voici le devis pour les quatre prochains enregistrements, au même tarif.", "date": "2026-10-01T09:12"}, {"title": "Re : devis du studio", "site": "Sam Tremblay", "snippet": "Je valide de mon côté, on peut signer.", "date": "2026-10-01T11:40"}, {"title": "Tarifs 2026 du studio", "site": "exemple.com", "url": "https://exemple.com/studio/tarifs", "snippet": "Les nouveaux tarifs à partir de janvier."}]}
```

```sheldon
{"type": "results", "query": "studio quote", "scope": "Mail", "total": "12 results", "items": [{"title": "Quote for episodes 43 to 46", "site": "Camille Roy", "snippet": "Here is the quote for the next four recordings, at the same rate.", "date": "2026-10-01T09:12"}, {"title": "Re: studio quote", "site": "Sam Tremblay", "snippet": "Fine by me, we can sign.", "date": "2026-10-01T11:40"}, {"title": "Studio rates 2026", "site": "example.com", "url": "https://example.com/studio/rates", "snippet": "The new rates from January."}]}
```
