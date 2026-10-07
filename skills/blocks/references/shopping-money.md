<!-- Généré par scripts/sync-blocks-skill.py depuis docs/app/blocs/ : ne pas modifier à la main. -->

# Shopping and money

Load this sheet before your first receipt or product block in a conversation; each block gives its fields (required ones in bold, other accepted names in parentheses), its preview, its full view on a tap, its buttons and two examples, French then English.

These blocks are read-only: nothing in Sheldon pays or buys. Paying, ordering or subscribing is a consequence: propose it with `sheldon_propose`, never from a block.

### `receipt`

A receipt, an invoice, a payment: to whom, how much, and what happened to it. For an order on its way,
use `object` with a `kind`; for a product and its price, `product`.

Fields: **`merchant`** (`store`), **`total`** (`amount`), `currency`, `date`, `status` (`paid`, `due`,
`refunded`, `failed`), `due` (when an invoice must be paid), `items` [{`label`, `quantity`, `amount`}],
20 at most, `method` ("Visa •••• 4242"), `reference`, `file` (the invoice, by the id of a file you
attached).

Preview: the merchant, the total, then what happened and when ("Free · 29,99 € · payé le 2 oct."),
with an orange chip for a failed payment and a gray one for an invoice to pay. Full view: every line,
the total, the date, the payment method, the reference and the invoice.

Buttons: none, whatever you write (`action`, `secondary` and `actions` are ignored). To pay an invoice,
use `sheldon_propose`. Sheldon shows only the last four digits of any longer number in `method`; never
write a full card number, in this block or anywhere else.

Examples:

```sheldon
{"type": "receipt", "merchant": "Studio Nord", "total": "240,00 $", "currency": "CAD", "status": "paid", "date": "2026-10-02", "method": "Visa •••• 4242", "reference": "F-2026-118", "items": [{"label": "Mixage de l’épisode 42", "quantity": "1", "amount": "180,00 $"}, {"label": "Mastering", "quantity": "1", "amount": "60,00 $"}]}
```

```sheldon
{"type": "receipt", "merchant": "Studio Nord", "total": "$240.00", "currency": "CAD", "status": "paid", "date": "2026-10-02", "method": "Visa •••• 4242", "reference": "F-2026-118", "items": [{"label": "Mixing episode 42", "quantity": "1", "amount": "$180.00"}, {"label": "Mastering", "quantity": "1", "amount": "$60.00"}]}
```

### `product`

A product and its price as you follow it: what it costs now, what it cost before, where. For something
already paid, use `receipt`.

Fields: **`name`**, **`price`** ("219 $"), `previous` (the price before, for the change), `store`,
`availability` ("En stock, livré jeudi"), `image` (the id of a file you attached, never a URL), `url`
(the product page, https or http), `action`.

Preview: the store, the name, the price, the previous price struck through and the change in a chip
("−12 %"), which Sheldon computes when both prices read as numbers. Full view: the image, the
availability and "Ouvrir la page".

Buttons: one, `action`. A label answers you as `label · name` ("Me prévenir s’il baisse · Casque
Studio"); it never buys anything. `{label, url}` only opens the link. To buy, use `sheldon_propose`.

Examples:

```sheldon
{"type": "product", "name": "Micro Studio Pro", "price": "219 $", "previous": "249 $", "store": "Boutique Nord", "availability": "En stock, livré jeudi", "url": "https://example.com/micro-studio-pro", "action": "Me prévenir s’il baisse"}
```

```sheldon
{"type": "product", "name": "Studio Pro microphone", "price": "$219", "previous": "$249", "store": "Boutique Nord", "availability": "In stock, delivered Thursday", "url": "https://example.com/studio-pro-microphone", "action": "Tell me if it drops"}
```
