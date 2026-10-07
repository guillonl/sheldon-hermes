<!-- Généré par scripts/sync-blocks-skill.py depuis docs/app/blocs/ : ne pas modifier à la main. -->

# Home, places and trips

Load this sheet before your first place, route, weather or device block in a conversation; each block gives its fields (required ones in bold, other accepted names in parentheses), its preview, its full view on a tap, its buttons and two examples, French then English.

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

### `route`

A trip: how long it takes, how, when to leave, and the delay. For the place alone, use `place`.

Fields: `from`, **`to`**, `mode` (`walk`, `transit`, `car`, `bike`), **`duration`** ("23 min"),
`distance`, `departAt` and `arriveAt` ("18:12"), `steps` [{`title`, `duration`}], 8 at most, `delay`
("+6 min"), `fromCoordinate` and `toCoordinate` ({`latitude`, `longitude`}).

Preview: the map of `place` when coordinates are given, the destination, then how long, how and when
("23 min en transport en commun · départ 18:12"), and the delay in an orange chip. Full view: where
from and when, where to and when, the distance, every step, and "Ouvrir dans Plans", which opens the
trip in Apple Maps (by its coordinates when given).

Buttons: none. A question about the trip ("Je pars maintenant ?") is an `ask`.

Examples:

```sheldon
{"type": "route", "from": "Bureau", "to": "Studio Nord", "mode": "transit", "duration": "23 min", "distance": "6,2 km", "departAt": "18:12", "arriveAt": "18:35", "delay": "+6 min", "steps": [{"title": "Marcher jusqu’à Berri-UQAM", "duration": "4 min"}, {"title": "Ligne orange vers Côte-Vertu, 5 arrêts", "duration": "12 min"}, {"title": "Marcher jusqu’au studio", "duration": "7 min"}], "toCoordinate": {"latitude": 45.5236, "longitude": -73.5817}}
```

```sheldon
{"type": "route", "from": "Office", "to": "Studio Nord", "mode": "transit", "duration": "23 min", "distance": "6.2 km", "departAt": "18:12", "arriveAt": "18:35", "delay": "+6 min", "steps": [{"title": "Walk to Berri-UQAM", "duration": "4 min"}, {"title": "Orange line toward Côte-Vertu, 5 stops", "duration": "12 min"}, {"title": "Walk to the studio", "duration": "7 min"}], "toCoordinate": {"latitude": 45.5236, "longitude": -73.5817}}
```

### `weather`

The weather of a place: now, the next hours, the next days, and an alert. For the low and high of
anything else (prices, a week of figures), use `range`.

Fields: **`place`**, **`now`** ({`temperature`, `condition`}, or the temperature alone), `hours`
[{`time`, `temperature`, `condition`}], 12 at most, `days` [{`label`, `low`, `high`, `condition`}], 7
at most, today first, `alert`. `condition`: `clear`, `cloudy`, `rain`, `snow`, `storm`, `fog`, `wind`;
otherwise a neutral thermometer. Temperatures are numbers, in the user's unit.

Preview: a gray symbol, the place, then now and today ("19°, nuageux · 13° à 22°"), and the alert in an
orange chip. Full view: the hours side by side, then the days drawn as a `range`.

Buttons: none.

Examples:

```sheldon
{"type": "weather", "place": "Montréal", "now": {"temperature": 19, "condition": "cloudy"}, "hours": [{"time": "15:00", "temperature": 20, "condition": "cloudy"}, {"time": "16:00", "temperature": 21, "condition": "rain"}, {"time": "17:00", "temperature": 19, "condition": "storm"}, {"time": "18:00", "temperature": 17, "condition": "storm"}], "days": [{"label": "Auj.", "low": 13, "high": 22, "condition": "storm"}, {"label": "Ven.", "low": 12, "high": 20, "condition": "cloudy"}, {"label": "Sam.", "low": 10, "high": 18, "condition": "clear"}], "alert": "Orages ce soir, à partir de 17 h"}
```

```sheldon
{"type": "weather", "place": "Montreal", "now": {"temperature": 19, "condition": "cloudy"}, "hours": [{"time": "15:00", "temperature": 20, "condition": "cloudy"}, {"time": "16:00", "temperature": 21, "condition": "rain"}, {"time": "17:00", "temperature": 19, "condition": "storm"}, {"time": "18:00", "temperature": 17, "condition": "storm"}], "days": [{"label": "Today", "low": 13, "high": 22, "condition": "storm"}, {"label": "Fri", "low": 12, "high": 20, "condition": "cloudy"}, {"label": "Sat", "low": 10, "high": 18, "condition": "clear"}], "alert": "Storms tonight, from 5 pm"}
```

### `device`

A device of the home: a light, a plug, a thermostat, a lock, an alarm, a camera. One block per device;
several in a `card`.

Fields: **`name`**, **`state`** ("allumé", "verrouillée"), `kind` (`light`, `plug`, `thermostat`,
`lock`, `alarm`, `camera`, `other`), `value` ("60 %", "21°"), `room`, `action`.

Preview: one row, the symbol of its kind, the name and the room, the state and the value. Full view:
none, the block shows everything.

Buttons: one, `action`, for a light, a plug or a thermostat: it answers you as `label · name`
("Éteindre · Salon"), and you act. A lock, an alarm or a camera never has a button, whatever you write
(Sheldon also drops any button that locks, unlocks, arms or disarms): to change one, use
`sheldon_propose`.

Examples:

```sheldon
{"type": "device", "name": "Salon", "room": "Rez-de-chaussée", "kind": "light", "state": "allumé", "value": "60 %", "action": "Éteindre"}
```

```sheldon
{"type": "device", "name": "Living room", "room": "Ground floor", "kind": "light", "state": "on", "value": "60%", "action": "Turn off"}
```
