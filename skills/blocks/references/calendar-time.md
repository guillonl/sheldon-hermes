<!-- Généré par scripts/sync-blocks-skill.py depuis docs/app/blocs/ : ne pas modifier à la main. -->

# Calendar and time

Load this sheet before your first appointment, day plan, timeline or scheduled task block in a conversation; each block gives its fields (required ones in bold, other accepted names in parentheses), its preview, its full view on a tap, its buttons and two examples, French then English.

### `event`

Also `calendar`. An appointment.

Fields: **`title`**, **`start`**, `end`, `place`, `note` (the whole description, `description` also
works), `attendees` (people as in `mail`, 20 at most), `url` (the video call or the event's page).

Preview: the day, the time, the place and the start of the note. Full view: the whole event (When,
Where, With, with names and addresses, the link, the whole description).

Buttons: two at most, `action` (the main one) and `secondary`, or `actions` (an array of two). A button
is a label (it answers you) or `{label, url}` (it opens the link).

Examples:

```sheldon
{"type": "event", "title": "Dîner avec Alex", "start": "2026-09-26T20:00", "end": "2026-09-26T22:00", "place": "Le Mary Céleste, Paris", "attendees": ["Alex Dubois <alex.dubois@exemple.com>"], "note": "Table pour deux réservée au nom d’Alex. Le restaurant garde la table quinze minutes.", "action": "Ajouter", "secondary": "Plus tard"}
```

```sheldon
{"type": "event", "title": "Dinner with Sam", "start": "2026-09-26T20:00", "end": "2026-09-26T22:00", "place": "Joe Beef, Montréal", "attendees": ["Sam Tremblay <sam.tremblay@example.com>"], "note": "Table for two booked under Sam's name. The restaurant holds the table for fifteen minutes.", "action": "Add", "secondary": "Later"}
```

### `schedule`

One day, with its free slots.

Fields: **`events`** (`[{title, start, end?, place?}]`), `now`.

Preview: the day and its slots. Full view: none, the block shows everything.

Examples:

```sheldon
{"type": "schedule", "subtitle": "Demain, jeudi", "now": "08:40", "events": [{"title": "Point d’équipe", "start": "09:30", "end": "10:00"}, {"title": "Déjeuner avec Sam", "start": "13:00", "end": "14:00", "place": "Le Mary Céleste"}]}
```

```sheldon
{"type": "schedule", "subtitle": "Tomorrow, Thursday", "now": "08:40", "events": [{"title": "Team check-in", "start": "09:30", "end": "10:00"}, {"title": "Lunch with Sam", "start": "13:00", "end": "14:00", "place": "Café Olimpico"}]}
```

### `timeline`

What happened, what comes next.

Fields: **`steps`** (`[{title, time?, detail?, state}]`, `state`: `done`, `current`, `upcoming`,
`failed`).

Preview: the first 5 steps, then "N autres". Full view: every step.

Examples:

```sheldon
{"type": "timeline", "steps": [{"title": "Repéré dans Messages", "time": "18:02", "state": "done"}, {"title": "Proposé par Hermes", "time": "18:03", "state": "done"}, {"title": "À toi de décider", "time": "maintenant", "state": "current"}]}
```

```sheldon
{"type": "timeline", "steps": [{"title": "Spotted in Messages", "time": "18:02", "state": "done"}, {"title": "Proposed by Hermes", "time": "18:03", "state": "done"}, {"title": "Your call", "time": "now", "state": "current"}]}
```

### `routine`

A scheduled task (a cron job): what runs at a fixed time, and how its last runs went.

Fields: **`title`** (or `name`), **`schedule`** (in plain words, "chaque jour à 7 h 30"), `next` (the
next run), `last` (`{time, state, summary}`, `state`: `done`, `failed`, `skipped`, `running`), `runs`
(the last runs, same shape, 10 at most), `paused`, `skills` (the attached skills, 10 at most), `deliver`
(the chat it delivers to).

Preview: the title, the rhythm, the next run, an orange chip when the last run failed, a gray one when
paused. Full view: the last runs line by line, the attached skills (`sheldon:blocks` is marked) and
where it delivers.

Buttons: two at most, `action` ("Mettre en pause" or "Reprendre") and `secondary` ("Modifier"). Each
answers you as `label · title`; you pause, resume or edit the job yourself.

Examples:

```sheldon
{"type": "routine", "title": "Veille design", "schedule": "chaque jour à 7 h 30", "next": "demain 7 h 30", "last": {"time": "aujourd’hui 7 h 30", "state": "failed", "summary": "Flux RSS injoignable"}, "runs": [{"time": "aujourd’hui 7 h 30", "state": "failed", "summary": "Flux RSS injoignable"}, {"time": "hier 7 h 30", "state": "done", "summary": "5 articles gardés sur 42"}], "skills": ["sheldon:blocks", "veille-design"], "deliver": "sheldon:veille-design", "action": "Mettre en pause", "secondary": "Modifier"}
```

```sheldon
{"type": "routine", "title": "Design watch", "schedule": "every day at 7:30 am", "next": "tomorrow 7:30 am", "last": {"time": "today 7:30 am", "state": "failed", "summary": "RSS feed unreachable"}, "runs": [{"time": "today 7:30 am", "state": "failed", "summary": "RSS feed unreachable"}, {"time": "yesterday 7:30 am", "state": "done", "summary": "5 articles kept out of 42"}], "skills": ["sheldon:blocks", "design-watch"], "deliver": "sheldon:design-watch", "action": "Pause", "secondary": "Edit"}
```
