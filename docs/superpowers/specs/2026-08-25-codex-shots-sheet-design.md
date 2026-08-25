# Cartonizer — Codex shots + CAD sheet

**Date:** 2026-08-25
**Status:** approved
**Approach:** structured shot templates + local millimetre SVG (not Codex-authored prompts, not CLI spawn)

## 1. Job

One **Codex shots** click turns the current SKU into five photoreal carton photos via the ChatGPT/Codex plan. A separate **Export sheet** click downloads a CAD-style reverse-tuck dieline of the **retail box**, drawn from the packing-table millimetres.

Grok chat, packing math, and mockup save stay as they are.

## 2. Locked decisions

| Topic | Decision |
|---|---|
| Prompt source | Python templates filled from current state. Codex/GPT Image only renders. |
| Image auth | `~/.codex/auth.json` from `codex login`. Subscription path, not a billed API key. |
| Image API | ChatGPT Codex backend `https://chatgpt.com/backend-api/codex/responses` with the `image_generation` tool (same path as Open Generative AI `generateCodexImage`). |
| Shot count | Five, sequential, so the UI can show `n / 5`. |
| Gallery | Thumbnails in the Grok log. Files under `runtime/codex-shots/` (gitignored). |
| CAD | Retail box reverse-tuck (0210-style). Local SVG. No model. |
| Rolls | Shots still run. Export sheet disabled until shape is Box. |
| Shipping carton blank | Out of scope. |
| DXF / PDF sheet | Out of scope. |

## 3. Shots

| id | Title | Aspect | Picture |
|---|---|---|---|
| `closed-34` | Closed 3/4 | 3:2 | Sealed retail pack, studio, three-quarter |
| `closed-front` | Closed front | 1:1 | Front panel square-on, studio |
| `open-front-tray` | Open-front tray | 3:2 | Kraft PDQ / shelf-ready tray, inner packs stacked, faces to camera (HEMA-style) |
| `open-carton` | Open carton | 3:2 | Open shipping carton, packed grid visible from above |
| `shelf` | Shelf | 3:2 | Two trays on a European drugstore shelf, price rail, retail light |

Each prompt must include SKU name (or “retail pack”), L×W×H mm, piece count and grid when packing is valid, and “keep printed artwork, no invented logos or claims, no extra brands.”

## 4. API

`GET /api/health` → `{ ok, grok, codex, error? }`. UI status `Grok · Codex` when both are true.

`GET /api/codex/shots` → `{ shots: [{ id, title, aspect }] }`

`POST /api/codex/shot`

```text
{ "id": "closed-34", "run": "s1787…", "state": { name, shape, box, carton, … } }
→ { "id", "title", "prompt", "url": "/runtime/codex-shots/<run>/<id>.png" }
```

`run` is `^[a-z0-9][a-z0-9-]{0,80}$`. Missing run: server makes `s<unix>`.

`GET /api/sheet.svg?l=&w=&h=&name=` → `image/svg+xml` attachment. 400 if any of L,W,H ≤ 0.

401/503 on shots: tell the user to run `codex login`. One failed shot does not cancel the others.

## 5. CAD sheet

1 SVG unit = 1 mm. Reverse-tuck 0210-style net:

- Front and back: L × H
- Left and right: W × H
- Top (on front) and bottom (on back): L × W
- Glue tab: 15 mm, or 12 mm if W < 30
- Tuck flaps: 0.6 × W, clamped 12–40 mm
- Dust flaps on left/right: 0.5 × W, clamped 8–25 mm
- Cut: solid `#111`, 0.35 mm
- Crease: dashed `#333`, 0.25 mm
- Glue hatch on the tab
- Text: SKU, `L×W×H mm`, legend Cut / Crease / Glue

Download: `cartonizer-<slug>-sheet.svg`.

## 6. UI

Right panel, under Download PNG:

1. **Codex shots** (primary kraft)
2. **Export sheet** (outline, disabled when shape is roll or box sizes are empty)
3. Save mockup (unchanged)

While running, Codex shots shows `2 / 5`. Each finished shot appends a Grok-log card: image, title, Copy prompt.

## 7. Files

- Modify `serve.py`, `index.html`, `README.md`
- Add `tests/test_shots_sheet.py` (prompts + SVG + pack math, no live Codex)
- Spec: this file
- Generated PNGs stay gitignored via `runtime/`

## 8. Out of scope

Pallet photos, Codex rewriting prompts, shipping-carton blank, DXF, changing packing math, German UI.
