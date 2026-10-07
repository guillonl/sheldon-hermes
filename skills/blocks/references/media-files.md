<!-- Généré par scripts/sync-blocks-skill.py depuis docs/app/blocs/ : ne pas modifier à la main. -->

# Media and files

Load this sheet before your first file, image or media block in a conversation; each block gives its fields (required ones in bold, other accepted names in parentheses), its preview, its full view on a tap, its buttons and two examples, French then English.

### `file`

A file you produced or found.

Fields: **`name`**, `kind` (`pdf`, `audio`, `video`, `image`, `document`, `sheet`, `slides`,
`archive`, `code`, `other`; otherwise from the extension), `detail` ("38 min · 52 Mo"), `url`, `id`
(the id of a file you attached, as Sheldon lists it in Créations).

Preview: its icon or thumbnail, its name and its detail. Full view: with an `id`, the file whole in
Créations (a PDF page by page, an image large, a document with its blocks); without one, none, the
block shows everything.

Buttons: two at most, `action` (the main one) and `secondary`, or `actions` (an array of two). A button
is a label (it answers you) or `{label, url}` (it opens the link).

Examples:

```sheldon
{"type": "file", "name": "episode-43.mp3", "detail": "38 min · 52 Mo", "action": {"label": "Télécharger", "url": "https://example.com/episode-43.mp3"}}
```

```sheldon
{"type": "file", "name": "episode-43.mp3", "detail": "38 min · 52 MB", "action": {"label": "Download", "url": "https://example.com/episode-43.mp3"}}
```

### `image`

One or several images: a screenshot of your browser, an image you generated, a photo you read. Always
by the id of a file you attached (`/v1/files/{id}`), never by a URL: a `url` is ignored, and without a
file id there is no block.

Fields: **`file`** (one id) or **`files`** (ids, 6 at most), **`alt`** (always: what the image shows,
read by VoiceOver and shown while it loads), `caption`, `source` (where it comes from, as the `source`
block: the page of a screenshot, the message of a photo).

Preview: the thumbnail, or a grid of two to six squares, then the caption. Full view: each image in the
Créations preview, with "Partager".

Buttons: two at most, `action` and `secondary`. Each answers you as `label · caption` (or `alt`).

Examples:

```sheldon
{"type": "image", "file": "5ce7a1b2c3d4e5f60718293a4b5c6d7e", "alt": "Capture de la page de paiement du fournisseur : le formulaire est rempli, le bouton Payer est grisé.", "caption": "Le bouton Payer reste grisé, même avec la carte enregistrée.", "action": "Réessayer", "secondary": "Laisser", "source": {"kind": "web", "url": "https://example.com/paiement"}}
```

```sheldon
{"type": "image", "files": ["0a1b2c3d4e5f60718293a4b5c6d7e8f9", "1b2c3d4e5f60718293a4b5c6d7e8f90a", "2c3d4e5f60718293a4b5c6d7e8f90a1b"], "alt": "Three logo directions for Sam Tremblay’s studio: a monogram, a wordmark and a badge.", "caption": "Three directions, same palette.", "action": "Keep the second"}
```

### `media`

A song, a podcast episode or a video. Sheldon reads only a file you attached, by its id: its play
button opens it in Créations, where a sound plays when the user taps it and a video goes to another
app through "Partager". Nothing plays on its own. A `url` (https or http) only opens the page outside
Sheldon, never inside it.

Fields: **`title`**, `artist` (or `show` for an episode), `kind` (`song`, `episode`, `video`),
`duration` ("42:10", or a number of seconds), `position` (where the user stopped, "12:30"), `file`
(the id of a file you attached), `url` (the page of the song or the episode), `action`.

Preview: the title, the artist and the duration, with the play button when there is a `file`.
Full view: the kind, the duration, the position, and "Ouvrir" for a `url`.

Buttons: one, `action`. A label answers you as `label · title` ("Écouter plus tard · Épisode 43");
`{label, url}` only opens the link.

Examples:

```sheldon
{"type": "media", "title": "Épisode 43 : prototyper avec l’IA", "show": "Les Bavards", "kind": "episode", "duration": "42:10", "position": "12:30", "url": "https://example.com/les-bavards/43", "action": "Écouter plus tard"}
```

```sheldon
{"type": "media", "title": "Episode 43: prototyping with AI", "show": "Les Bavards", "kind": "episode", "duration": "42:10", "position": "12:30", "url": "https://example.com/les-bavards/43", "action": "Listen later"}
```
