<!-- Généré par scripts/sync-blocks-skill.py depuis docs/app/blocs/ : ne pas modifier à la main. -->

# Mails and messages

Load this sheet before your first mail, draft, thread, contact or provenance block in a conversation; each block gives its fields (required ones in bold, other accepted names in parentheses), its preview, its full view on a tap, its buttons and two examples, French then English.

### `mail`

Also `email`. A mail the user received or sent, to read in full.

Fields: **`body`** (the whole text, `\n` between lines), `from`, `to`, `cc`, `subject`, `date`,
`attachments`. A person is `"Name <address>"`, an address alone, or `{"name", "address"}`; several go
in an array (20 at most). `attachments`: file names, or `[{name, kind?, detail?}]` (10 at most; `kind`
as for `file`).

Preview: the sender, the time, the subject and the first lines, like a row of Mail. Full view: the
whole mail (subject, From, To, Cc, date, the whole text, the attachments).

Buttons: two at most, `action` (the main one) and `secondary`, or `actions` (an array of two). A button
is a label (it answers you) or `{label, url}` (it opens the link).

Examples:

```sheldon
{"type": "mail", "from": "Sam Tremblay <sam.tremblay@example.com>", "to": "Alex Dubois <alex.dubois@example.com>", "subject": "Enregistrement de mardi", "date": "2026-09-29T18:42", "body": "Salut Alex,\n\nC’est bon pour mardi 14 h au studio. J’apporte les deux micros et le plan de l’épisode 44.\n\nÀ mardi,\nSam", "attachments": [{"name": "plan-episode-44.pdf", "detail": "2 pages · 184 ko"}], "action": "Répondre"}
```

```sheldon
{"type": "mail", "from": "Sam Tremblay <sam.tremblay@example.com>", "to": "Alex Dubois <alex.dubois@example.com>", "subject": "Tuesday's recording", "date": "2026-09-29T18:42", "body": "Hi Alex,\n\nTuesday 2 pm at the studio works. I'll bring both mics and the outline of episode 44.\n\nSee you Tuesday,\nSam", "attachments": [{"name": "episode-44-outline.pdf", "detail": "2 pages · 184 KB"}], "action": "Reply"}
```

### `draft`

A draft ready to go.

Fields: **`body`**, `channel` (`mail`, `message`, `post`; `mail` by default), `to`, `subject`. A mail
draft also takes the fields of `mail` (`from`, `cc`, `date`, `attachments`), and its `to` takes
addresses.

Preview: for a mail draft, the recipient, the subject and the first lines, marked "Visible de toi
seul"; otherwise the whole draft. Full view: for a mail draft, the whole mail as it will leave;
otherwise none, the block shows everything.

Buttons: as for `mail`, usually the action ("Envoyer") and the one that keeps it ("Garder en
brouillon").

Examples:

```sheldon
{"type": "draft", "channel": "mail", "from": "Alex Dubois <alex.dubois@example.com>", "to": "Camille Roy <camille.roy@example.com>", "subject": "Re : maquette de jeudi", "date": "2026-10-02T07:12", "body": "Bonjour Camille,\n\nOui pour jeudi, c’est parfait. Je t’envoie la maquette ce soir, avec les deux pistes de couleurs.\n\nBonne journée,\nAlex", "action": "Envoyer", "secondary": "Garder en brouillon"}
```

```sheldon
{"type": "draft", "channel": "mail", "from": "Alex Dubois <alex.dubois@example.com>", "to": "Camille Roy <camille.roy@example.com>", "subject": "Re: Thursday's mockup", "date": "2026-10-02T07:12", "body": "Hi Camille,\n\nThursday works perfectly. I'll send you the mockup tonight, with the two color options.\n\nHave a good day,\nAlex", "action": "Send", "secondary": "Keep as draft"}
```

### `source`

Where what you show comes from, with the whole original message.

Fields: at least one of `from`, `subject`, `body`, `url`. `kind` (`mail`, `message`, `invitation`,
`web`, `file`, or your own label), `app` (Slack, WhatsApp, iMessage, Telegram), `from`, `to`, `cc`
(people as in `mail`, an address or a handle), `subject` (or `title`), `date`, `body` (the whole text,
never cut), `url` (the page), `attachments`. The same object is the `source` argument of
`sheldon_propose`.

Preview: one line ("D’après un mail de Sam Tremblay · il y a 22 min"). Full view: the original
message, whole, with "Répondre", which prepares an answer in the agent's chat; nothing leaves without
the user.

Examples:

```sheldon
{"type": "source", "kind": "message", "app": "Slack", "from": "Sam Tremblay <@sam>", "date": "2026-10-02T15:12", "body": "Je ne peux plus mardi pour l’enregistrement. Mercredi 14 h, ça te va ? Le studio est libre."}
```

```sheldon
{"type": "source", "kind": "message", "app": "Slack", "from": "Sam Tremblay <@sam>", "date": "2026-10-02T15:12", "body": "I can't do Tuesday for the recording anymore. Wednesday 2 pm, does that work for you? The studio is free."}
```

### `thread`

Several messages of one conversation read elsewhere (iMessage, WhatsApp, Slack, SMS, mail), when you
offer to answer. For the origin of one thing in one line, use `source`.

Fields: **`messages`** (`[{from?, text, time?, mine?}]`, `mine` for the user's own; the last 30 are
kept), `channel` (`imessage`, `whatsapp`, `slack`, `sms`, `mail` or `other`), `with`, `unread`,
`source`.

Preview: the channel in words and a neutral symbol (never a brand color), who it is with, the unread
count, the last three messages in small bubbles. Full view: the whole thread.

Buttons: two at most, `action` ("Répondre") and `secondary`. Each answers you as `label · with`:
answer "Répondre" with a `draft` of channel `message`; sending stays a consequence the user approves.

Examples:

```sheldon
{"type": "thread", "channel": "imessage", "with": "Camille Roy", "unread": 2, "messages": [{"from": "Camille Roy", "text": "Tu viens toujours jeudi soir ?", "time": "18:02"}, {"from": "Moi", "text": "Oui, je réserve pour 20 h.", "time": "18:10", "mine": true}, {"from": "Camille Roy", "text": "Super ! On peut décaler à 20 h 30 ?", "time": "19:41"}, {"from": "Camille Roy", "text": "Je finis un peu tard.", "time": "19:42"}], "action": "Répondre", "secondary": "Plus tard"}
```

```sheldon
{"type": "thread", "channel": "slack", "with": "Sam Tremblay", "unread": 1, "messages": [{"from": "Sam Tremblay", "text": "Can we move the review to Wednesday?", "time": "14:02"}, {"from": "Me", "text": "Wednesday works, what time?", "time": "14:05", "mine": true}, {"from": "Sam Tremblay", "text": "2 pm, the studio is free.", "time": "15:12"}], "action": "Reply", "secondary": "Later", "source": {"kind": "message", "app": "Slack", "from": "Sam Tremblay <@sam>"}}
```

### `contact`

The person you are talking about: who they are, how to reach them, when the user last heard from them.
For a conversation with them, use `thread`; for a place, `place`.

Fields: **`name`**, `role`, `company`, `phone`, `email`, `address`, `note`, `last` ("dernier échange le
29 sept."), `action`, `secondary`.

Preview: the initials in a circle, the name, the role and the company, the last exchange. Full view:
every detail, each one selectable.

Buttons: two at most. A button without a link answers you as `label · name`. A button
`{"label", "url"}` only opens: https, http or maps, and in this block only, `tel:`, `sms:` (a number)
and `mailto:` (one address). They open the phone, messages or mail app with nothing sent, and
`mailto:` loses its parameters (`?subject=`, `?body=`).

Examples:

```sheldon
{"type": "contact", "name": "Sam Tremblay", "role": "Ingénieur du son", "company": "Studio Nord", "phone": "+1-514-555-0199", "email": "sam.tremblay@example.com", "address": "4521, boulevard Saint-Laurent, Montréal", "note": "Il a mixé les épisodes 38 à 42.", "last": "dernier échange le 29 sept.", "action": {"label": "Appeler", "url": "tel:+15145550199"}, "secondary": "Demander un devis"}
```

```sheldon
{"type": "contact", "name": "Sam Tremblay", "role": "Sound engineer", "company": "Studio Nord", "phone": "+1-514-555-0199", "email": "sam.tremblay@example.com", "address": "4521 Saint-Laurent Blvd, Montreal", "note": "He mixed episodes 38 to 42.", "last": "last contact on Sept. 29", "action": {"label": "Call", "url": "tel:+15145550199"}, "secondary": "Ask for a quote"}
```
