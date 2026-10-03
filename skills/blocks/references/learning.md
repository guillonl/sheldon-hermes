<!-- Généré par scripts/sync-blocks-skill.py depuis docs/app/blocs/ : ne pas modifier à la main. -->

# Learning and permissions

Load this sheet before your first learned or rules block in a conversation; each block gives its fields (required ones in bold, other accepted names in parentheses), its preview, its full view on a tap, its buttons and two examples, French then English.

### `learned`

What you kept or learned: a memory, a preference, a skill. It makes your learning visible. Only when
what you kept changes a future answer; grouped (one block for several things); never a notification of
its own. A `community` skill, or one from a URL, first comes in a request that carries this block.

Fields: **`kind`** (`memory`, `preference` or `skill`), **`title`** (or `name`), `detail` (what you kept,
or what the skill does), `because` (where the lesson comes from: "d’après tes refus de cette semaine";
never `source`, kept for the provenance), `changes` (`[{label, before?, after?}]`, for a corrected
skill, 5 at most), for a skill `origin` (`hermes`, `hub`, `plugin` or `user`), `trust` (`builtin`,
`official`, `trusted` or `community`), `tools` (the tools it touches, 8 at most) and `uses`, `items`
(`[{kind?, title, detail?}]`, 10 at most, for a grouped view: "2 choses retenues").

Preview: the symbol of its kind, the title and one line of `detail` (or the first three `items`). Full
view: the whole text, why, the changes before and after ("12 → 15"), the origin, the trust and the
tools.

Buttons: two at most, `action` ("Garder") and `secondary` ("Oublier", or "Retirer" for a skill). Each
answers you as `label · title`: you remove the entry yourself, with your own tools.

Examples:

```sheldon
{"type": "learned", "kind": "preference", "title": "Réunions après 10 h", "detail": "Tu refuses les réunions avant 10 h : je ne t’en propose plus avant cette heure.", "because": "d’après tes trois refus de cette semaine", "action": "Garder", "secondary": "Oublier"}
```

```sheldon
{"type": "learned", "kind": "skill", "title": "Design watch", "detail": "Reads the design news every morning and keeps five articles for you.", "because": "after you asked for a lighter watch on Monday", "changes": [{"label": "Sources", "before": "12", "after": "15"}, {"label": "Articles kept", "before": "8", "after": "5"}], "origin": "hub", "trust": "community", "tools": ["browser", "mail"], "uses": 3, "action": "Keep", "secondary": "Remove"}
```

### `rules`

What you may do on your own, with the user's permission: show it when a permission is born ("À
l’avenir, réponds seul aux invitations de Camille"), and when the user asks what you do on your own.
Put the most recent rule first.

Fields: **`items`** (`[{text, scope?, since?, because?, id?}]`, or sentences; 20 at most), `title`,
`remove` (the button label; "Retirer" or "Remove" by default, in the app's language).

Preview: the three most recent rules, each with its button. Full view: every rule, with its scope, its
date and where it comes from.

Buttons: one per rule. It answers you as `remove · text` ("Retirer · Répondre seul aux invitations de
Camille"): drop that permission, without asking again. A rule already removed shows struck through.

Examples:

```sheldon
{"type": "rules", "title": "Ce que je fais seul", "items": [{"text": "Répondre seul aux invitations de Camille Roy", "scope": "Agenda", "since": "2 oct.", "because": "dit le 2 octobre"}, {"text": "Archiver les newsletters non lues depuis un mois", "scope": "Mail", "since": "28 sept.", "because": "après ton oui à ma proposition"}, {"text": "Ranger les factures dans Factures 2026", "scope": "Fichiers", "since": "15 sept."}], "remove": "Retirer"}
```

```sheldon
{"type": "rules", "title": "What I do on my own", "items": [{"text": "Reply on my own to Sam Tremblay’s invitations", "scope": "Calendar", "since": "Oct 2", "because": "said on October 2"}, {"text": "Archive newsletters unread for a month", "scope": "Mail", "since": "Sept 28", "because": "after your yes to my proposal"}, {"text": "File invoices in Invoices 2026", "scope": "Files", "since": "Sept 15"}], "remove": "Remove"}
```
