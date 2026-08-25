# Cartonizer

Local packing-table tool for household retail packs (cling wrap, zip bags, trash-bag rolls, and similar).

It does two jobs:

1. **Packaging preview.** From photos, a dieline, or typed sizes, the local Python server asks an image model for wrap/front/back/side artwork so you can see a printed carton before anything is produced.
2. **Industrial carton math.** You enter the retail pack size and the shipping carton inner size. The tool tries all six axis-aligned orientations, picks the densest regular grid, and shows how many units fit, leftover millimetres, and fill percent. Change bag count, film, or carton size to compare configurations.

The published repo is the **plain tool**: app + schema, without any samples. 

## Run

```bash
python3 serve.py
```

Then open [http://127.0.0.1:8765/](http://127.0.0.1:8765/).

Image preview needs a Grok Build login (`grok login`). Auth lives in `~/.grok/auth.json` on your machine and is not part of this repo. The 3D pack-out view in `index.html` also works as a static file without the server.

## What is public vs local

| Public | Stays on your machine (gitignored) |
|---|---|
| `serve.py` | `mockups/` saved designs and generated wraps |
| `index.html` | `examples/` reference photos |
| `factory/SCHEMA.json` | `factory/catalog.json`, `groups.json`, `variants/`, `quotes/`, `report/` |
| this README | `runtime/` cache |

Saving a mockup in the UI writes into those local folders automatically.

## Packing rule

Axis-aligned orthogonal grid only. Every unit uses the same winning orientation. Inner carton L × W × H in millimetres. No stagger, mixed orientations, or pallet load.

## License

Use as you like for your own packing studies.
