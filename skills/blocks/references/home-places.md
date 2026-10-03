<!-- Généré par scripts/sync-blocks-skill.py depuis docs/app/blocs/ : ne pas modifier à la main. -->

# Home, places and trips

Load this sheet before your first place block in a conversation; each block gives its fields (required ones in bold, other accepted names in parentheses), its preview, its full view on a tap, its buttons and two examples, French then English.

### `place`

Also `location`, `map`. A place, with its map when coordinates are given.

Fields: **`name`**, `address`, `latitude` and `longitude`, `note`.

Preview: the whole block, with its map. Full view: none, the block shows everything.

Buttons: two at most, `action` (the main one) and `secondary`, or `actions` (an array of two). A button
is a label (it answers you) or `{label, url}` (it opens the link, a `maps` link included).

Examples:

```sheldon
{"type": "place", "name": "Le Mary Céleste", "address": "1 rue Commines, 75003 Paris", "latitude": 48.8625, "longitude": 2.3656, "note": "12 min à pied", "action": {"label": "Y aller", "url": "https://maps.apple.com/?daddr=48.8625,2.3656"}}
```

```sheldon
{"type": "place", "name": "Joe Beef", "address": "2491 Notre-Dame St W, Montréal", "latitude": 45.4807, "longitude": -73.5793, "note": "12 min walk", "action": {"label": "Go there", "url": "https://maps.apple.com/?daddr=45.4807,-73.5793"}}
```
