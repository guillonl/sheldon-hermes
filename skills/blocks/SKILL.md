---
name: blocks
description: Visual blocks for the Sheldon app: a short index, then one sheet per family. Load before writing a sheldon block.
version: "3"
metadata:
  hermes:
    tags: [sheldon, ui, blocks]
---

<!-- Généré par scripts/sync-blocks-skill.py depuis docs/app/blocs/ : ne pas modifier à la main. -->

# Visual blocks for Sheldon (format version 3)

Sheldon, the user's iPhone and Mac app, draws charts, cards and two-button questions natively when your
reply contains a **block**: a fenced code block whose language is `sheldon`, holding one JSON object
with a `"type"` (and `"version": 3`). Everything around it is ordinary Markdown.

````markdown
L’épisode 42 a bien marché.

```sheldon
{"type": "metric", "version": 3, "subtitle": "Épisode 42 · 7 derniers jours", "value": 1284, "unit": "écoutes", "delta": "+18 %"}
```

Le pic de samedi vient de ta story Instagram.
````

## Contract

1. **Format.** A block is a fenced code block whose language is `sheldon`, holding one JSON object (or
   an array of objects) with a `type` and that type's required fields (in bold in each sheet).
2. **Version.** Sheldon draws catalogue version 3, the version of this skill. When one of the
   user's devices draws an older one, a `[Sheldon]` note tells you: newer blocks show as their
   fallback there.
3. **Fallback, in five stages.** A known type that reads is drawn; otherwise its `fallback`: a block
   of a known type (one level only), a sentence shown as text, or `"drop"` to show nothing; otherwise
   an object with a `title` or `name` is drawn as an `object`; otherwise its content as gray text.
4. **A sentence that stands alone.** Your reply still makes sense without its blocks. `summary` is
   one sentence that says what the block shows, read in previews, notifications and VoiceOver.
5. **An unknown field is ignored, never an error.** `"version"` stays optional on every block.
6. **`meta` and `id`.** `meta` is yours, never shown: sources, ids, anything you want to keep. `id`
   (letters, digits, `_`, `.`, `-`, 64 at most): a later block with the same `id` in the same
   conversation replaces this one on screen; the old message is never edited (`file`'s `id` is its
   own file identifier, not this).
7. **Precedence.** The safety rules and this contract come first; then an explicit request of the
   user; then what you learned about them; then the defaults of the sheldon skills. These skills and
   the plugin's files are read-only: what you and the user add sits on top of these skills, it
   changes a default, never this contract; never edit the sheldon skills or the plugin's files.
8. **The chat stays whole.** The user can always write freely: a block never replaces your
   sentences, never forces a choice and never disables the message field.

The catalogue names what the app draws, not what you may show: when nothing fits, use `object` with a
`kind`.

## Which block

A type named here but not listed under Blocks below is not drawn yet: use `object` with a `kind`.

| Family | Type | When |
|---|---|---|
| Steps | `task` | what will be done, or how far a plan has got |
| | `activity` | what was done, or is being done, with its result |
| | `timeline` | dated moments |
| | `todo` | a list the user ticks off themselves |
| | `board` | the work of several agents in columns, blocked first |
| | `standup` | done, in progress, blocked, per agent |
| Decide | `ask` | yes or no, now, in the chat |
| | `choice` | one option out of two to six, in the chat |
| | `form` | two to five pieces of information at once |
| | `variants` | two to four versions of the same text or visual |
| | `rating` with `ask` | an opinion on recurring work |
| | `sheldon_propose` | a decision that can wait, with a notification |
| | approval card | a terminal command: Hermes's own approval, with Face ID |
| Learning | `learned` | what you kept: a preference, a skill |
| | `rules` | what you may do alone |
| | `routine` | what runs at a fixed time |
| Read | `digest` | three to five items kept for the user, with what was left out |
| | `results` | search results |
| | `link` | a page you read |
| | `quote` | a quoted passage, with who wrote it |
| Inform | `notice` | one line of information, never a decision |
| Objects | the type of the object (`mail`, `event`, `place`, `file`, `thread`, `image`, `contact`, `receipt`, `product`, `route`, `weather`, `media`, `device`) | when it exists |
| | `object` with a `kind` | otherwise |
| Provenance | `source` | where what you show or propose comes from, in one line |

## Rules

- Numbers go in a block, not in a sentence that lists them. Add one sentence before or after that says
  what matters.
- A decision to take now, in the chat: an `ask` whose `primary` button names the action ("Ajouter",
  "Programmer", "Télécharger") and whose `secondary` button declines ("Plus tard", "Pas maintenant"),
  or a `choice` whose `submit` names the action; a decision that can wait goes to `sheldon_propose`.
- An appointment, a place, a file, a draft, a mail: the block of that object (`event`, `place`,
  `file`, `draft`, `mail`), with its buttons.
- Put the whole content in the block, never a shortened one: every row of a list or a table (up to its
  limit), every step, an event's full description and attendees, a mail's addresses, subject, date
  and full text. Sheldon makes the short preview itself and opens the whole block on a tap: never
  cut, shorten or summarize what goes inside a block.
- A mail, received or drafted, comes whole: the addresses (`"Camille Roy <camille.roy@example.com>"`),
  the subject, the date and the full text.
- When what you show or propose comes from somewhere (a mail, a message, an invitation, a page, a
  file), always attach its provenance with the whole original message: a `source` block in your
  reply or in a Fil delivery, the `source` argument of `sheldon_propose` for a request. The user sees where
  it comes from in one line and opens the original to answer it.
- Default: three blocks, then one `card` that groups them.
- Put `highlight` on the value that matters: Sheldon draws it in ink, the others in gray.
- Several blocks: several fences, or a JSON array of objects in one fence. An object without `type`
  but with `blocks` is a card.
- JSON may be a little loose (trailing comma, comment, unquoted key). A number may be written `1284`,
  `"1 284"` or `"12,5"`.
- A number inside text (a `center`, a chip, a title): in French, thousands are separated by a
  no-break space, U+00A0 (`1 284 écoutes`), never a plain space nor the narrow U+202F; in English, by a comma
  (`1,284 listens`).
- Dates: `2026-09-26`, `2026-09-26T20:00` (device time), `2026-09-26T20:00:00Z`,
  `2026-09-26T20:00+02:00`. Times alone: `09:30`, `9h30`, `14h`.
- `title`, `subtitle` (the context: "Épisode 42 · 7 derniers jours") and `footer` (the source) frame
  almost every block.
- Write in the user's language; in French, apply a non-breaking space before `:`, `?`, `%`, `!` and
  units, and inside « », and the typographic apostrophe `’` (`l’épisode`, `C’est fait`), never `'`
  outside code.
- A button without a link sends the user's answer back to you as an ordinary message,
  `label · subject` (for example `Programmer · Publier l’épisode 43 demain à 8 h ?`). Treat it as their
  answer to your previous message. A button `{"label", "url"}` only opens the link (https, http or
  maps; in a `contact`, also `tel:`, `sms:` and `mailto:`); it sends nothing.
- Never put anything else in a `sheldon` fence. What Sheldon cannot draw follows the fallback of the
  contract: nothing breaks.

## Blocks

### Mails and messages

Before your first block of this family in a conversation, load it with skill_view("sheldon:blocks", file_path="references/mails-messages.md").

- `mail`: a mail received or sent, whole: people, subject, date, text
- `draft`: a draft ready to go (a mail, a message, a post), shown as it will leave
- `source`: where what you show comes from, in one line, with the whole original message
- `thread`: several messages of a conversation read elsewhere (iMessage, WhatsApp, Slack, SMS), with a reply button
- `contact`: the person you are talking about: role, details, last exchange; may call, text or mail

### Calendar and time

Before your first block of this family in a conversation, load it with skill_view("sheldon:blocks", file_path="references/calendar-time.md").

- `event`: an appointment: when, where, with whom, and its whole description
- `schedule`: one day of appointments, with its free slots
- `timeline`: dated moments: what happened, what comes next
- `routine`: a scheduled task: its rhythm, its next run, its last runs and its skills

### Figures, tables and charts

Before your first block of this family in a conversation, load it with skill_view("sheldon:blocks", file_path="references/figures-charts.md").

- `metric`: one key figure, with its change
- `stats`: two to four figures side by side
- `line`: a line over time
- `bars`: vertical bars over days or weeks
- `horizontalBars`: a ranking, longest bar first
- `groupedBars`: two or three series per label
- `range`: a low and a high per row (weather, prices)
- `donut`: parts of a whole
- `meter`: a gauge (disk, battery, quota)
- `progress`: a progress bar
- `ring`: a goal as a ring
- `heatmap`: a grid of days, like an activity calendar
- `compare`: two or three options, one recommended
- `table`: a table, check marks included
- `rating`: a score in stars, or, with ask, the user's opinion on recurring work

### Web and search

Before your first block of this family in a conversation, load it with skill_view("sheldon:blocks", file_path="references/web-search.md").

- `link`: a page you read: its site, its title, its summary and three points; opened from the full view
- `results`: search results (web, mail, notes): the query, ten results at most, each with its link
- `digest`: a briefing: three to five items kept for the user, each with why, and how many were left out
- `quote`: a quoted passage: who said it, where, the part that matters in ink; never a button

### Tasks, projects and diagrams

Before your first block of this family in a conversation, load it with skill_view("sheldon:blocks", file_path="references/tasks-projects.md").

- `task`: a task in steps: what will be done, or how far a plan has got
- `activity`: what you did on your own, or are doing, line by line, errors first, with undo
- `todo`: a list the user ticks off themselves, then sends in one message
- `board`: the work of several agents in columns, each with its count, blocked cards first; never a button
- `standup`: what is done, in progress and blocked, each line with its agent; never a button
- `diff`: two versions of a code or a text, line by line, read only
- `status`: the state of services, problems first
- `flow`: steps that follow each other
- `graph`: linked topics, one in the middle

### Home, places and trips

Before your first block of this family in a conversation, load it with skill_view("sheldon:blocks", file_path="references/home-places.md").

- `place`: a place, with its map when coordinates are given
- `route`: a trip: how long, how, when to leave, the delay, its steps; Maps on a tap
- `weather`: the weather of a place: now, the next hours, the days, an alert
- `device`: a device of the home: its room, its state; never a button for a lock, an alarm or a camera

### Shopping and money

Before your first block of this family in a conversation, load it with skill_view("sheldon:blocks", file_path="references/shopping-money.md").

- `receipt`: a receipt, an invoice, a payment: who, how much, its lines; never a button
- `product`: a product and its tracked price, with the change; a button answers you, it never buys

### Media and files

Before your first block of this family in a conversation, load it with skill_view("sheldon:blocks", file_path="references/media-files.md").

- `file`: a file you produced or found, opened whole on a tap when it has its id
- `image`: one to six images by the id of their file (never a URL), always with their alternative text
- `media`: a song, an episode or a video: Sheldon opens only a file you attached, on a tap; a url opens outside the app

### Decisions

Before your first block of this family in a conversation, load it with skill_view("sheldon:blocks", file_path="references/decisions.md").

- `ask`: a question to answer now, in the chat, with two buttons
- `choice`: one option out of two to six (or several), answered in one message; the message field stays free
- `form`: two to five pieces of information at once, sent in one message on a tap; never a secret
- `variants`: two to four versions of the same text or visual, side by side; the pick answers in one message

### Learning and permissions

Before your first block of this family in a conversation, load it with skill_view("sheldon:blocks", file_path="references/learning.md").

- `learned`: what you kept or learned (a memory, a preference, a skill), with why and a forget button
- `rules`: what you may do on your own, each rule with its remove button

### Layout and fallback

Before your first block of this family in a conversation, load it with skill_view("sheldon:blocks", file_path="references/layout.md").

- `notice`: one line of information (done, warning, error), never a decision
- `text`: a framed paragraph
- `list`: rows, each with a link or a button; a row may carry a whole mail; three to eight objects may scroll as cards
- `card`: several blocks in one frame
- `object`: anything the catalogue has no block for; also how an unknown type is drawn
