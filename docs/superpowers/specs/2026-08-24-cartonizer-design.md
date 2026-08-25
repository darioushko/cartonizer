# Cartonizer — retail box into shipping carton

**Date:** 2026-08-24
**Status:** draft for user review
**Form:** one local `index.html`, double-click to open, no server

## 1. Job

A packing-table tool for household SKUs (cling wrap, zip bags, trash bags, and similar). You type the **retail box** size and the **shipping carton inner** size. The tool finds how many identical boxes fit, shows that pack in 3D, and lets you screenshot it.

It does **not** design the retail pack, fold a dieline, mix SKUs, or stack cartons on a pallet.

## 2. Locked decisions

| Topic | Decision |
|---|---|
| Problem | One retail box SKU into one shipping carton |
| Carton measure | Inner L × W × H only (no wall thickness) |
| Orientation | Try all 6 axis-aligned rotations; pick densest grid |
| Packing | Axis-aligned orthogonal grid. Every box uses the same winning orientation. No stagger, no mixed orientations |
| Delivery | Single local HTML file |
| Layout | Inputs left, 3D center, fit stats right |
| Look | Workshop: kraft open-top carton, solid retail boxes, leftover is empty space |
| Snapshot | PNG download of the current 3D view |
| Persistence | Last inputs in `localStorage` |
| Units | Millimetres internally. UI can show mm or cm |

## 3. Data

All lengths stored as millimetres (positive numbers).

```text
state = {
  name: string,          // optional SKU label, default ""
  unit: "mm" | "cm",
  box:  { l, w, h },     // retail box, mm
  carton: { l, w, h },   // inner carton, mm
}
```

`localStorage` key: `cartonizer:v1`. JSON of `state`. Load on boot. Save on every valid edit.

Product chips **Cling wrap / Zip bags / Trash bags / Custom** only set `name` (Custom clears it to `""`). They do **not** inject invented dimensions.

## 4. Packing math

User fields map as `Cx = carton.l`, `Cy = carton.w`, `Cz = carton.h`.
Treat one retail box as `(Bx, By, Bz)` in its current orientation, aligned to those same carton axes.

```text
nx = floor(Cx / Bx)
ny = floor(Cy / By)
nz = floor(Cz / Bz)
count = nx * ny * nz
leftover = (Cx - nx*Bx, Cy - ny*By, Cz - nz*Bz)
fill = (count * Bx * By * Bz) / (Cx * Cy * Cz)   // 0 if carton volume is 0
```

Try these six orientations of the input `(L, W, H)`, in this order:

1. `(L, W, H)`
2. `(L, H, W)`
3. `(W, L, H)`
4. `(W, H, L)`
5. `(H, L, W)`
6. `(H, W, L)`

**Winner:** maximum `count`. On a tie, keep the **first** in the list above (deterministic, no extra UI).

If any of L, W, H, Cx, Cy, Cz is missing, not a number, or `<= 0`: do not pack. Show an empty carton (or no carton) and a short “enter positive sizes” note. Count is `—`.

If the box is larger than the carton in every orientation: `count = 0`, empty carton, right panel says which current dimensions overflow (box edge > carton edge on that axis for the identity orientation, plus “0 in all 6 rotations”).

World mapping (so the carton sits on a table, height up):

- Carton inner L → Three.js X
- Carton inner H → Three.js Y (up)
- Carton inner W → Three.js Z

Packing counts `nx, ny, nz` in the math are along **box axes** `(Bx, By, Bz)`, which are already aligned to carton `(Cx, Cy, Cz)` in that orientation. When drawing, map those carton axes to the world mapping above: `Cx→X`, `Cz→Y-up`, `Cy→Z`. Boxes fill from the inner back-left-bottom corner in a regular lattice. Do not lay the carton on its side.

A **visual gap** between rendered boxes is allowed so the grid is readable. The gap is **not** in the math. Suggested: about 1% of the smallest box edge, clamped so it never changes the integer count.

## 5. UI

One page. Dark workshop chrome, kraft/amber accent. No build step. English UI. Numbers use a space as thousands separator if needed; no invented product claims in copy.

**Left panel**

- SKU name text field
- Four chips: Cling wrap, Zip bags, Trash bags, Custom
- Retail box L, W, H
- Carton inner L, W, H
- Unit toggle mm | cm (converts displayed values; storage stays mm)
- Live recalc on input (no Calculate button)

**Center**

- Full-height Three.js canvas
- Orbit (drag), zoom (wheel), pan (right-drag or two-finger)
- Open-top kraft carton: floor + four walls, walls translucent, no lid
- Solid retail boxes in the winning grid, slightly different face shade so edges read
- Empty leftover pocket is just unfilled carton volume (no red leftover solid)
- A ground shadow/plane so the carton sits on a table, not in a void

**Right panel**

- Large integer: boxes per carton
- Grid: `nx × ny × nz`
- Packed box orientation: the winning `(Bx × By × Bz)` in the active unit
- Leftover: L / W / H in the active unit, always on the **carton** axes as entered (unused strip along carton L, W, H), not on the rotated box axes
- Fill: volume percent, one decimal
- PNG button: `Download PNG`

Inputs and stats stay usable at laptop width (~1280px). Below ~900px the side panels stack under the canvas; packing still works.

## 6. 3D scene

- Three.js r160 from CDN via import map (same approach as the restaurant planner). `OrbitControls`.
- Carton scale: 1 scene unit = 1 mm.
- Camera frames the carton on first load and whenever **carton** inner size changes. Changing only the retail box keeps the current orbit. Never steal the camera mid-drag.
- Dispose geometries on rebuild. Share materials.
- `preserveDrawingBuffer: true` on the renderer so PNG capture is reliable.

## 7. PNG snapshot

Button on the right panel. Exports the current canvas as `cartonizer-<count>pcs.png` (or `cartonizer.png` if count is not a number). No overlay chrome required in v1; the 3D view is the image.

## 8. File layout

```text
index.html          // the whole app (HTML + CSS + JS module)
docs/superpowers/specs/2026-08-24-cartonizer-design.md
```

Open `index.html` via `file://` or a tiny static server. ES modules from a CDN work on `file://` when all app JS is inline in one `<script type="module">`. Do not split into sibling `.js` files for v1 (browsers block those on `file://`).

## 9. Out of scope (v1)

- Pallet / container load
- Mixed SKUs in one carton
- Carton wall / flute thickness
- Packing gap as a user input
- Manual drag of individual boxes
- PDF, Excel, GLB export
- Hosted URL / login
- Real N&E factory dimensions as presets
- German UI (can be added later)

## 10. Acceptance

1. Double-click `index.html` (or open it locally) and the 3D view appears without a build.
2. Enter box `100 × 50 × 40` mm and carton `400 × 300 × 200` mm. Count is **120** (orientation `(100,50,40)` → `4 × 6 × 5`). Right panel shows that grid, leftover `0 / 0 / 0`, fill `100.0%`.
3. Same box in carton `410 × 300 × 200`. Count is still **120**. Leftover L is `10` mm.
4. Swap box to `200 × 200 × 200` and carton to `100 × 100 × 100`. Count is **0**. Carton is empty. Message states it does not fit.
5. Changing mm ↔ cm does not change the count, only the displayed numbers.
6. Reload the file: last sizes and name come back.
7. PNG button downloads an image of the packed carton.
8. Drag-orbit still works after a size change.

## 11. Implementation note

Keep packing math in a pure function (`packCarton(box, carton) → result`) at the top of the script so it can be checked with a few `console.assert` cases on load. Rendering only consumes that result.
