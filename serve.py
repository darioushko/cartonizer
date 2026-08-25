#!/usr/bin/env python3
"""Local Cartonizer server. Uses Grok Build login from ~/.grok/auth.json."""

from __future__ import annotations

import base64
import json
import math
import os
import posixpath
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
MOCKUPS = ROOT / "mockups"
FACTORY = ROOT / "factory"
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
AUTH_PATH = Path.home() / ".grok" / "auth.json"
TOKEN_URL = "https://auth.x.ai/oauth2/token"
API = "https://api.x.ai/v1"
CODEX_AUTH_PATH = Path.home() / ".codex" / "auth.json"
CODEX_CONFIG_PATH = Path.home() / ".codex" / "config.toml"
CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
CODEX_SHOTS_DIR = RUNTIME / "codex-shots"
VIEWS_DIR = RUNTIME / "views"
HOST = "127.0.0.1"
PORT = 8765
_auth_lock = threading.Lock()
AR_TO_SIZE = {"1:1": "1024x1024", "3:2": "1536x1024", "2:3": "1024x1536"}
VALID_IMAGE_SIZES = {"1024x1024", "1024x1536", "1536x1024", "auto"}
SHOTS = (
    {
        "id": "closed-34",
        "title": "Closed 3/4",
        "aspect": "3:2",
        "picture": (
            "Photoreal product photo of a sealed retail carton, three-quarter view, "
            "studio, white seamless backdrop, soft box light."
        ),
    },
    {
        "id": "closed-front",
        "title": "Closed front",
        "aspect": "1:1",
        "picture": (
            "Photoreal product photo of a sealed retail carton, camera square-on to the front panel, "
            "studio, white seamless backdrop."
        ),
    },
    {
        "id": "open-front-tray",
        "title": "Open carton side",
        "aspect": "3:2",
        "picture": (
            "Photoreal packing-table photo of an open kraft shipping carton, camera square-on to the SIDE "
            "(looking at the thin W × H faces of the retail packs, the way the 3D model shows the pack "
            "from the side). The near carton wall is open or cut away. You look into the carton from the "
            "side, not at the front print, not from above. White studio packing table. "
            "Not a supermarket shelf, no price rail, no gondola, no second tray."
        ),
    },
    {
        "id": "open-carton",
        "title": "Open carton",
        "aspect": "3:2",
        "picture": (
            "Photoreal packing-table photo of an open kraft shipping carton viewed from above and slightly "
            "in front. The retail packs sit in a regular grid inside. No lid, leftover space is empty."
        ),
    },
    {
        "id": "shelf",
        "title": "Shelf",
        "aspect": "3:2",
        "picture": (
            "Photoreal European drugstore shelf: two kraft open-front trays side by side on a metal shelf "
            "with a price rail. Each tray holds stacked identical retail cartons, faces toward camera, "
            "overhead retail lighting."
        ),
    },
)

SYSTEM = """You are Grok inside Cartonizer, a packing-table tool for household products (cling wrap, zip bags, trash-bag rolls, and similar retail packs).

The user may attach several photos plus text. Typical inputs:
1) iPhone Measure screenshot of a closed shipping/display carton (white overlay with cm)
2) Measure screenshot of the open carton showing how many rolls/boxes sit inside
3) Close-up of a retail pack or roll label (bag size, count, brand)
4) A packaging DIELINE / Stanzform / unfolded carton net (flat CAD with flaps, glue tab, and mm callouts such as 210 mm, 65 mm, 40 mm)

Dieline / unfolded carton:
- This is the retail pack, not the shipping carton, unless the user says otherwise.
- Read every mm/cm callout on the drawing.
- Folded box height = the tall printed face (often the front panel, e.g. 65 mm).
- Folded box depth = the short panel between front and back (often 40 mm), which also matches the side-wall width.
- Folded box length = the long printed face width. If a width arrow spans front PLUS one side wall, subtract the depth (e.g. 210 mm span with 40 mm side → length 170 mm). If the arrow is only the front, use that as length.
- Ignore tuck flaps and glue tabs in L×W×H. They are not extra product size.
- shape is "box". box.l = length, box.w = depth, box.h = height.
- Do not copy dieline millimetres onto the shipping carton. Leave carton as the table value unless the user also gives a master carton.
- texture=true. The 3D model should look like the folded printed carton.

All numbers you output must be millimetres (cm on the overlay × 10).

How to read this:
- Trust Measure overlay numbers (e.g. 19 cm, 18 cm, 11 cm). Those are the carton outer edges in the photo. Subtract ~4 mm per axis for flute if you need inner; if the overlay is on the inner opening, use it as inner.
- Rebuild carton L, W, H from the three measured edges across the photos. Say which photo gave which edge.
- Count the units visible in the open carton. Estimate roll diameter = that inner height (or width) / count along that axis. Estimate roll axis length from the carton edge that matches the roll's white core-to-core length.
- Bag print size (e.g. 40 × 43 cm) is the FLAT BAG, not the roll. Do not use 400 × 430 mm as the roll L×D.
- Piece count (e.g. 37 bags) does not change the outer roll if the user says their roll is the same size as the competitor roll. Then pack that same L × D × D into the reconstructed carton.
- If the user wants a different piece count but the SAME outer roll as the competitor, keep competitor roll millimetres and only mention that bag count is a fill of the roll, not a new diameter, unless they give film thickness.
- shape is "roll" for trash-bag / cling rolls. box.l = axis length, box.w = diameter, box.h = diameter.
- texture=true when a product wrap, label, or dieline artwork is present and retail sizes are complete. Prefer the printed pack, not Measure UI chrome. For a dieline, texture the folded box from that print.
- Never invent millimetres that are not on an overlay, typed by the user, or derived as (carton inner along an axis) / (integer count of units along that axis).
- Never invent certifications or brand claims.

In the readable reply, state: carton mm, roll mm, how you got diameter, and the integer pack (nx × ny × nz and count).

End with exactly one JSON block:

```json
{"apply":false,"texture":false,"name":"","shape":"roll","box":{"l":null,"w":null,"h":null},"carton":{"l":null,"w":null,"h":null},"ask":""}
```

Set apply true when box.l/w/h are positive millimetres. If carton fields are null, keep the carton already on the table (this is the usual dieline case).
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_exp(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_auth() -> tuple[str, dict]:
    data = json.loads(AUTH_PATH.read_text())
    key = next(iter(data))
    return key, data


def _write_auth(top_key: str, data: dict) -> None:
    tmp = AUTH_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(AUTH_PATH)
    os.chmod(AUTH_PATH, 0o600)


def _refresh(entry: dict) -> dict:
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": entry["refresh_token"],
            "client_id": entry["oidc_client_id"],
        }
    ).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        tok = json.loads(r.read())
    entry["key"] = tok["access_token"]
    if tok.get("refresh_token"):
        entry["refresh_token"] = tok["refresh_token"]
    exp = _now() + timedelta(seconds=int(tok.get("expires_in") or 21600))
    entry["expires_at"] = exp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return entry


def bearer() -> str:
    if not AUTH_PATH.exists():
        raise RuntimeError("No Grok Build login. Run `grok login` in a terminal.")
    with _auth_lock:
        top, data = _load_auth()
        entry = data[top]
        exp = _parse_exp(entry.get("expires_at"))
        if not entry.get("key") or (exp and exp <= _now() + timedelta(minutes=2)):
            entry = _refresh(entry)
            data[top] = entry
            _write_auth(top, data)
        return entry["key"]


def api_json(method: str, path: str, payload: dict | None = None, timeout: int = 180) -> dict:
    token = bearer()
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        if e.code == 401:
            with _auth_lock:
                top, data_auth = _load_auth()
                data_auth[top] = _refresh(data_auth[top])
                _write_auth(top, data_auth)
            req.add_header("Authorization", f"Bearer {bearer()}")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        raise RuntimeError(f"xAI {e.code}: {body[:400]}") from e


def extract_text(resp: dict) -> tuple[str, str]:
    thinking: list[str] = []
    texts: list[str] = []
    for item in resp.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "reasoning":
            summary = item.get("summary") or []
            if isinstance(summary, str) and summary.strip():
                thinking.append(summary.strip())
            elif isinstance(summary, list):
                for s in summary:
                    if isinstance(s, dict) and s.get("text"):
                        thinking.append(str(s["text"]).strip())
                    elif isinstance(s, str) and s.strip():
                        thinking.append(s.strip())
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("text"):
                    texts.append(str(c["text"]))
    if resp.get("output_text"):
        texts.append(str(resp["output_text"]))
    return "\n".join(t for t in thinking if t), "\n".join(texts).strip()


JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)
JSON_TAIL = re.compile(r"(\{[^{}]*\"apply\"[^{}]*\})\s*$", re.S)


def parse_apply(text: str) -> dict | None:
    blob = None
    blocks = JSON_BLOCK.findall(text or "")
    if blocks:
        blob = blocks[-1]
    else:
        m = JSON_TAIL.search(text or "")
        if m:
            blob = m.group(1)
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def visible_text(text: str) -> str:
    cleaned = JSON_BLOCK.sub("", text or "").strip()
    return cleaned or text


def mm_triple(obj: dict | None) -> dict | None:
    if not isinstance(obj, dict):
        return None
    try:
        l, w, h = float(obj["l"]), float(obj["w"]), float(obj["h"])
    except (KeyError, TypeError, ValueError):
        return None
    if min(l, w, h) <= 0:
        return None
    return {"l": l, "w": w, "h": h}


def pack_carton(box: dict, carton: dict) -> dict | None:
    if not box or not carton:
        return None
    Cx, Cy, Cz = carton["l"], carton["w"], carton["h"]
    orients = [
        (box["l"], box["w"], box["h"]),
        (box["l"], box["h"], box["w"]),
        (box["w"], box["l"], box["h"]),
        (box["w"], box["h"], box["l"]),
        (box["h"], box["l"], box["w"]),
        (box["h"], box["w"], box["l"]),
    ]
    best = None
    for Bx, By, Bz in orients:
        nx = int(Cx // Bx)
        ny = int(Cy // By)
        nz = int(Cz // Bz)
        count = nx * ny * nz
        leftover = [round(Cx - nx * Bx, 3), round(Cy - ny * By, 3), round(Cz - nz * Bz, 3)]
        vol = Cx * Cy * Cz
        fill = 0 if vol == 0 else (count * Bx * By * Bz) / vol
        if best is None or count > best["pcs_per_carton"]:
            best = {
                "pcs_per_carton": count,
                "grid_LWH": [nx, ny, nz],
                "packed_as_mm": [Bx, By, Bz],
                "leftover_mm": leftover,
                "fill_pct": round(fill * 100, 1),
                "tight_inner_mm": [round(nx * Bx, 3), round(ny * By, 3), round(nz * Bz, 3)],
            }
    return best


def shot_by_id(shot_id: str) -> dict | None:
    for s in SHOTS:
        if s["id"] == shot_id:
            return s
    return None


REF_FACE_KEYS = (
    ("front", "textureFront"),
    ("back", "textureBack"),
    ("side", "textureSide"),
)
ROLE_LOCK = {
    "front": "the FRONT panel print. Put this artwork on the front face only, uncropped, same layout.",
    "back": "the BACK panel print. Put this artwork on the back face only.",
    "side": "the SIDE panel print. Put this artwork on both left and right side faces.",
    "wrap": "the product wrap/label. Keep this print on the pack. Do not redesign it.",
    "dieline": (
        "the unfolded carton net (dieline). Mentally fold it into a 3D carton. "
        "Do not show it flat. The tall printed face with the product photo is the front."
    ),
    "layout": (
        "a 3D pack from Cartonizer. Match this camera, this grid, and this unit count. "
        "Do not add or remove packs."
    ),
}


def plan_shot_refs(state: dict, extras: list | None = None, layouts: list | None = None) -> list[dict]:
    """Faces beat wrap beat dieline. A 3D layout ref may ride along with faces."""
    faces = []
    for role, key in REF_FACE_KEYS:
        url = str((state or {}).get(key) or "").strip()
        if url:
            faces.append({"role": role, "url": url})
    if faces:
        out = faces[:3]
    else:
        wrap = str((state or {}).get("textureUrl") or "").strip()
        if wrap:
            out = [{"role": "wrap", "url": wrap}]
        else:
            out = []
            for u in extras or []:
                if not isinstance(u, str):
                    continue
                if u.startswith("data:image") or u.startswith("/mockups/") or u.startswith("/runtime/"):
                    out.append({"role": "dieline", "url": u})
                if len(out) >= 1:
                    break
    for u in layouts or []:
        if isinstance(u, str) and u.startswith("data:image"):
            out.append({"role": "layout", "url": u})
            break
    return out


def _guess_mime(path: Path, header: str = "") -> str:
    h = (header or "").lower()
    suf = path.suffix.lower() if path else ""
    if "jpeg" in h or "jpg" in h or suf in (".jpg", ".jpeg"):
        return "image/jpeg"
    if "webp" in h or suf == ".webp":
        return "image/webp"
    return "image/png"


def resolve_ref_data(url: str) -> str | None:
    url = (url or "").strip()
    if url.startswith("data:image"):
        return url
    url = url.split("?", 1)[0]
    if url.startswith("/mockups/") or url.startswith("/runtime/"):
        path = (ROOT / url.lstrip("/")).resolve()
        try:
            path.relative_to(ROOT)
        except ValueError:
            return None
        if not path.is_file():
            return None
        raw = path.read_bytes()
        if not raw or len(raw) > 8_000_000:
            return None
        return "data:" + _guess_mime(path) + ";base64," + base64.b64encode(raw).decode("ascii")
    return None


def load_shot_refs(state: dict, extras: list | None = None, layouts: list | None = None) -> list[dict]:
    loaded = []
    for item in plan_shot_refs(state, extras, layouts):
        data = resolve_ref_data(item["url"])
        if data:
            loaded.append({"role": item["role"], "data": data})
    return loaded


def shot_prompt(shot_id: str, state: dict, refs: list | None = None) -> str:
    spec = shot_by_id(shot_id)
    if not spec:
        raise ValueError(f"unknown shot {shot_id}")
    name = str(state.get("name") or "retail pack").strip() or "retail pack"
    box = mm_triple(state.get("box"))
    carton = mm_triple(state.get("carton"))
    pack = pack_carton(box, carton) if box and carton else None
    size = "size unknown"
    if box:
        size = f"{box['l']:g} × {box['w']:g} × {box['h']:g} mm outer"
    count = ""
    if pack and pack["pcs_per_carton"]:
        nx, ny, nz = pack["grid_LWH"]
        count = (
            f" Pack {pack['pcs_per_carton']} units in a {nx} × {ny} × {nz} grid "
            f"(along carton L, W, H)."
        )
        if carton:
            count += f" Shipping carton inner {carton['l']:g} × {carton['w']:g} × {carton['h']:g} mm."
    shape = "cylindrical roll wrap" if state.get("shape") == "roll" else "folded printed carton"
    if shot_id == "open-front-tray" and pack:
        nx, ny, nz = pack["grid_LWH"]
        count += (
            f" From this side camera you see {nx} across and {nz} stacked "
            f"({nz} high, like the 3D pack). Show the SIDE print of each unit, not the front panel. "
            "Exact integer count, no extra boxes."
        )
    lock = (
        "Keep the real printed artwork and brand. No invented logos, certifications, "
        "or extra brands. No hands. Photoreal, sharp, commercial catalog quality."
    )
    if refs:
        lines = []
        for i, ref in enumerate(refs, 1):
            how = ROLE_LOCK.get(ref.get("role") or "", "a print reference. Use this artwork.")
            lines.append(f"Attached image {i} is {how}")
        lock = (
            " ".join(lines)
            + " Use ONLY this attached print. Do not redraw, restyle, or replace it. "
            "Same colors, type, photos, and marks. Only change camera, lighting, "
            "and whether the carton is closed, open-front, or on a shelf."
        )
    return f"{spec['picture']} The product is {name}, a {shape}, {size}.{count} {lock}"


def sheet_geometry(l: float, w: float, h: float) -> dict:
    l, w, h = float(l), float(w), float(h)
    if min(l, w, h) <= 0:
        raise ValueError("L, W, H must be positive millimetres")
    glue = 12.0 if w < 30 else 15.0
    tuck = min(40.0, max(12.0, 0.6 * w))
    dust = min(25.0, max(8.0, 0.5 * w))
    return {"l": l, "w": w, "h": h, "glue": glue, "tuck": tuck, "dust": dust}


def _svg_rect(x, y, w, h, cls, extra=""):
    return (
        f'<rect class="{cls}" x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" {extra}/>'
    )


def _svg_line(x1, y1, x2, y2, cls):
    return (
        f'<line class="{cls}" x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}"/>'
    )


def _svg_text(x, y, text, size=3.2):
    return f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}">{xml_escape(text)}</text>'


def sheet_svg(l: float, w: float, h: float, name: str = "") -> str:
    g = sheet_geometry(l, w, h)
    L, W, H, glue, tuck, dust = g["l"], g["w"], g["h"], g["glue"], g["tuck"], g["dust"]
    m = 18.0
    title_h = 14.0
    gx = m
    body_y = m + title_h + tuck + W
    left_x = gx + glue
    front_x = left_x + W
    right_x = front_x + L
    back_x = right_x + W
    top_y = body_y - W
    tuck_y = top_y - tuck
    bot_y = body_y + H
    bot_tuck_y = bot_y + W
    width = back_x + L + m
    height = bot_tuck_y + tuck + m + 10
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.2f} {height:.2f}" '
        f'width="{width:.2f}mm" height="{height:.2f}mm">',
        "<style>",
        "rect.panel { fill: #f4eee4; stroke: none; }",
        "rect.glue { fill: #e8d7b8; stroke: none; }",
        "line.cut { stroke: #111; stroke-width: 0.35; fill: none; }",
        "line.crease { stroke: #333; stroke-width: 0.25; stroke-dasharray: 2.2 1.1; fill: none; }",
        "text { font-family: 'IBM Plex Mono', ui-monospace, monospace; fill: #222; }",
        "path.cut { stroke: #111; stroke-width: 0.35; fill: none; }",
        "</style>",
        _svg_text(m, m + 6, (name or "Retail box").strip() or "Retail box", 4.2),
        _svg_text(m, m + 11, f"{L:g} × {W:g} × {H:g} mm   reverse-tuck 0210   1 unit = 1 mm", 3),
        _svg_rect(gx, body_y, glue, H, "glue"),
        _svg_rect(left_x, body_y, W, H, "panel"),
        _svg_rect(front_x, body_y, L, H, "panel"),
        _svg_rect(right_x, body_y, W, H, "panel"),
        _svg_rect(back_x, body_y, L, H, "panel"),
        _svg_rect(front_x, top_y, L, W, "panel"),
        _svg_rect(front_x, tuck_y, L, tuck, "panel"),
        _svg_rect(back_x, bot_y, L, W, "panel"),
        _svg_rect(back_x, bot_tuck_y, L, tuck, "panel"),
        _svg_rect(left_x, body_y - dust, W, dust, "panel"),
        _svg_rect(right_x, body_y - dust, W, dust, "panel"),
        _svg_rect(left_x, bot_y, W, dust, "panel"),
        _svg_rect(right_x, bot_y, W, dust, "panel"),
        # creases (fold)
        _svg_line(gx + glue, body_y, gx + glue, body_y + H, "crease"),
        _svg_line(left_x + W, body_y, left_x + W, body_y + H, "crease"),
        _svg_line(front_x + L, body_y, front_x + L, body_y + H, "crease"),
        _svg_line(right_x + W, body_y, right_x + W, body_y + H, "crease"),
        _svg_line(front_x, body_y, front_x + L, body_y, "crease"),
        _svg_line(front_x, top_y, front_x + L, top_y, "crease"),
        _svg_line(back_x, body_y + H, back_x + L, body_y + H, "crease"),
        _svg_line(back_x, bot_y + W, back_x + L, bot_y + W, "crease"),
        _svg_line(left_x, body_y, left_x + W, body_y, "crease"),
        _svg_line(right_x, body_y, right_x + W, body_y, "crease"),
        _svg_line(left_x, body_y + H, left_x + W, body_y + H, "crease"),
        _svg_line(right_x, body_y + H, right_x + W, body_y + H, "crease"),
        # outer cut: glue + body + top/tuck + bottom/tuck + dust
        (
            f'<path class="cut" d="M {front_x:.2f} {tuck_y:.2f} '
            f"H {front_x + L:.2f} V {top_y:.2f} "
            f"H {right_x + W:.2f} V {body_y - dust:.2f} "
            f"H {right_x:.2f} V {body_y:.2f} "
            f"H {back_x + L:.2f} V {bot_tuck_y + tuck:.2f} "
            f"H {back_x:.2f} V {bot_y + W:.2f} "
            f"H {right_x:.2f} V {bot_y + dust:.2f} "
            f"H {right_x + W:.2f} V {bot_y:.2f} "
            f"H {left_x:.2f} V {bot_y + dust:.2f} "
            f"H {left_x + W:.2f} V {bot_y:.2f} "
            f"H {gx:.2f} V {body_y:.2f} "
            f"H {left_x:.2f} V {body_y - dust:.2f} "
            f"H {left_x + W:.2f} V {body_y:.2f} "
            f"H {front_x:.2f} V {top_y:.2f} "
            f'H {front_x:.2f} V {tuck_y:.2f} Z"/>'
        ),
        _svg_text(front_x + 2, body_y + 8, f"FRONT  L {L:g} × H {H:g}"),
        _svg_text(back_x + 2, body_y + 8, f"BACK  L {L:g} × H {H:g}"),
        _svg_text(left_x + 1.5, body_y + H / 2, f"W {W:g}"),
        _svg_text(right_x + 1.5, body_y + H / 2, f"W {W:g}"),
        _svg_text(gx + 0.8, body_y + H / 2, "GLUE"),
        _svg_text(front_x + 2, top_y + 8, f"TOP  L {L:g} × W {W:g}"),
        _svg_text(back_x + 2, bot_y + 8, f"BOTTOM  L {L:g} × W {W:g}"),
        _svg_text(m, height - 6, "Cut = solid   Crease = dashed   Glue tab hatched", 2.8),
        "</svg>",
        "",
    ]
    return "\n".join(parts)


def read_codex_auth() -> dict | None:
    if not CODEX_AUTH_PATH.is_file():
        return None
    try:
        data = json.loads(CODEX_AUTH_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
    token = tokens.get("access_token") or data.get("access_token") or data.get("OPENAI_API_KEY")
    account = tokens.get("account_id") or data.get("account_id")
    if not token:
        return None
    return {"token": token, "accountId": account}


def read_codex_model() -> str:
    try:
        txt = CODEX_CONFIG_PATH.read_text()
    except OSError:
        return "gpt-5.5"
    m = re.search(r'^\s*model\s*=\s*"([^"]+)"', txt, re.M)
    return m.group(1) if m else "gpt-5.5"


def extract_image_b64(raw: str) -> str | None:
    full = None
    last_partial = None

    def consider(evt: dict) -> None:
        nonlocal full, last_partial
        if not isinstance(evt, dict):
            return
        partial = evt.get("partial_image_b64")
        if isinstance(partial, str) and len(partial) > 100:
            last_partial = partial
        item = evt.get("item") if isinstance(evt.get("item"), dict) else None
        if item and item.get("type") == "image_generation_call" and isinstance(item.get("result"), str):
            full = item["result"]
        if isinstance(evt.get("result"), str) and len(evt["result"]) > 100:
            full = evt["result"]
        output = None
        resp = evt.get("response") if isinstance(evt.get("response"), dict) else None
        if resp and isinstance(resp.get("output"), list):
            output = resp["output"]
        elif isinstance(evt.get("output"), list):
            output = evt["output"]
        if not output:
            return
        for o in output:
            if not isinstance(o, dict):
                continue
            if o.get("type") == "image_generation_call" and isinstance(o.get("result"), str):
                full = o["result"]
            for c in o.get("content") or []:
                if not isinstance(c, dict):
                    continue
                url = c.get("image_url")
                if isinstance(url, str) and url.startswith("data:"):
                    full = url.split(",", 1)[-1]
                if isinstance(c.get("b64_json"), str):
                    full = c["b64_json"]

    saw_sse = False
    for line in (raw or "").split("\n"):
        trimmed = line.strip()
        if not trimmed.startswith("data:"):
            continue
        saw_sse = True
        json_str = trimmed[5:].strip()
        if not json_str or json_str == "[DONE]":
            continue
        try:
            consider(json.loads(json_str))
        except json.JSONDecodeError:
            continue
    if not saw_sse:
        try:
            consider(json.loads(raw))
        except json.JSONDecodeError:
            pass
    return full or last_partial


def generate_codex_image(prompt: str, aspect: str = "1:1", images: list | None = None) -> bytes:
    prompt = (prompt or "").strip()
    if not prompt:
        raise RuntimeError("Prompt required.")
    auth = read_codex_auth()
    if not auth:
        raise RuntimeError("Codex session not found. Run `codex login` first.")
    size = AR_TO_SIZE.get(aspect, "auto")
    if size not in VALID_IMAGE_SIZES:
        size = "auto"
    content: list[dict] = []
    for url in (images or [])[:4]:
        if isinstance(url, str) and url.startswith("data:image"):
            content.append({"type": "input_image", "image_url": url, "detail": "high"})
    content.append({"type": "input_text", "text": prompt})
    payload = {
        "model": read_codex_model(),
        "instructions": (
            "You are an image generation assistant. Use the image_generation tool to create "
            "exactly the image the user describes. When reference images are attached, copy "
            "that print onto the carton. Do not invent new artwork. Do not ask clarifying questions."
        ),
        "input": [{"type": "message", "role": "user", "content": content}],
        "tools": [{"type": "image_generation", "size": size}],
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "store": False,
        "stream": True,
    }
    headers = {
        "Authorization": "Bearer " + auth["token"],
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "OpenAI-Beta": "responses=experimental",
        "originator": "codex_cli_rs",
        "User-Agent": "codex_cli_rs",
    }
    if auth.get("accountId"):
        headers["chatgpt-account-id"] = str(auth["accountId"])
    req = urllib.request.Request(
        CODEX_RESPONSES_URL,
        data=json.dumps(payload).encode(),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:400]
        if e.code in (401, 403):
            raise RuntimeError("Codex login expired. Run `codex login`.") from e
        raise RuntimeError(f"Codex backend {e.code}: {body}") from e
    b64 = extract_image_b64(raw)
    if not b64:
        raise RuntimeError("Codex returned no image (image_generation may be unavailable for this account).")
    try:
        return base64.b64decode(b64)
    except Exception as e:
        raise RuntimeError("Codex image was not valid base64.") from e


SIZE_L_RE = re.compile(r"(?<!\d)(\d+)\s*l\b", re.I)


def product_fields(obj: dict | None = None, **extra) -> dict:
    st = obj if isinstance(obj, dict) else {}
    pid = str(
        extra.get("productId")
        or extra.get("product_id")
        or st.get("productId")
        or st.get("product_id")
        or ""
    ).strip()[:80]
    sku = str(extra.get("sku") or st.get("sku") or "").strip()[:80]
    name = str(extra.get("name") or st.get("name") or "").strip()[:80]
    return {"productId": pid, "sku": sku, "name": name}


def _norm_label(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _size_l(s: str) -> str:
    m = SIZE_L_RE.search(s or "")
    return m.group(1) if m else ""


def view_matches_product(view: dict, product: dict) -> bool:
    """True if a saved view/shot belongs to the currently selected product."""
    vf = product_fields(view)
    pf = product_fields(product)
    if not (pf["productId"] or pf["sku"] or pf["name"]):
        return True
    if vf["productId"] and pf["productId"]:
        return vf["productId"].lower() == pf["productId"].lower()
    if vf["sku"] and pf["sku"] and vf["sku"].lower() == pf["sku"].lower():
        return True
    vn = _norm_label(vf["name"])
    pn = _norm_label(pf["name"])
    if vn and pn and vn == pn:
        return True
    vs = _size_l(" ".join(filter(None, [vn, vf["productId"], vf["sku"]])))
    ps = _size_l(" ".join(filter(None, [pn, pf["productId"], pf["sku"]])))
    if vs and ps and vs != ps:
        return False
    if vn and pn and vs and ps and vs == ps and vn.split()[0] == pn.split()[0]:
        return True
    return False


def save_codex_shot(
    shot_id: str,
    state: dict,
    run: str,
    extras: list | None = None,
    layouts: list | None = None,
) -> dict:
    spec = shot_by_id(shot_id)
    if not spec:
        raise ValueError(f"unknown shot {shot_id}")
    if not SAFE_ID.match(run):
        raise ValueError("bad run id")
    box = mm_triple(state.get("box"))
    if not box:
        raise ValueError("need positive retail box L, W, H")
    refs = load_shot_refs(state, extras, layouts)
    prompt = shot_prompt(shot_id, state, refs)
    png = generate_codex_image(prompt, spec["aspect"], [r["data"] for r in refs])
    folder = CODEX_SHOTS_DIR / run
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{shot_id}.png"
    dest.write_bytes(png)
    (folder / f"{shot_id}.txt").write_text(prompt + "\n", encoding="utf-8")
    url = "/runtime/codex-shots/" + run + "/" + dest.name
    meta_path = folder / "run.json"
    fields = product_fields(state)
    meta = {"run": run, **fields, "shots": []}
    if meta_path.is_file():
        try:
            prev = json.loads(meta_path.read_text())
            if isinstance(prev, dict):
                keep = {k: fields[k] or str(prev.get(k) or "") for k in ("productId", "sku", "name")}
                meta = {**meta, **prev, **keep}
        except json.JSONDecodeError:
            pass
    shots = [s for s in (meta.get("shots") or []) if isinstance(s, dict) and s.get("id") != shot_id]
    shots.append({"id": shot_id, "title": spec["title"], "url": url})
    meta["shots"] = shots
    meta["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")
    return {
        "id": shot_id,
        "title": spec["title"],
        "prompt": prompt,
        "url": url,
        "run": run,
        "refs": [r["role"] for r in refs],
    }


def list_codex_runs() -> list[dict]:
    if not CODEX_SHOTS_DIR.is_dir():
        return []
    runs = []
    for folder in sorted(CODEX_SHOTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not folder.is_dir() or not SAFE_ID.match(folder.name):
            continue
        shots = []
        for spec in SHOTS:
            png = folder / f"{spec['id']}.png"
            if png.is_file():
                shots.append(
                    {
                        "id": spec["id"],
                        "title": spec["title"],
                        "url": f"/runtime/codex-shots/{folder.name}/{png.name}",
                    }
                )
        if not shots:
            continue
        fields = {"name": "", "sku": "", "productId": ""}
        meta_path = folder / "run.json"
        if meta_path.is_file():
            try:
                raw = json.loads(meta_path.read_text())
                if isinstance(raw, dict):
                    fields = product_fields(raw)
            except json.JSONDecodeError:
                pass
        if not fields["name"]:
            for spec in SHOTS:
                txt = folder / f"{spec['id']}.txt"
                if not txt.is_file():
                    continue
                m = re.search(
                    r"The product is ([^,]+), a ",
                    txt.read_text(encoding="utf-8", errors="ignore"),
                )
                if m:
                    fields["name"] = m.group(1).strip()[:80]
                    break
        runs.append({"run": folder.name, **fields, "shots": shots})
    return runs[:40]


def save_view(
    kind: str,
    title: str,
    data_url: str,
    name: str = "",
    sku: str = "",
    product_id: str = "",
) -> dict:
    kind = kind if kind in ("3d", "2d", "codex", "verification") else "3d"
    url = (data_url or "").strip()
    if not url.startswith("data:image"):
        raise ValueError("need dataUrl")
    header, b64 = url.split(",", 1)
    raw = base64.b64decode(b64)
    if not raw:
        raise ValueError("empty image")
    vid = "v" + str(int(time.time() * 1000))
    VIEWS_DIR.mkdir(parents=True, exist_ok=True)
    ext = ".jpg" if "jpeg" in header.lower() else ".png"
    (VIEWS_DIR / (vid + ext)).write_bytes(raw)
    fields = product_fields({"name": name, "sku": sku, "productId": product_id})
    rec = {
        "id": vid,
        "kind": kind,
        "title": (title or ("3D view" if kind == "3d" else kind))[:80],
        **fields,
        "url": "/runtime/views/" + vid + ext,
        "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (VIEWS_DIR / (vid + ".json")).write_text(json.dumps(rec, indent=2) + "\n")
    rec["mtime"] = time.time()
    return rec


def list_views() -> list[dict]:
    items: list[dict] = []
    if VIEWS_DIR.is_dir():
        for meta in VIEWS_DIR.glob("*.json"):
            try:
                rec = json.loads(meta.read_text())
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or not rec.get("url"):
                continue
            rec["mtime"] = meta.stat().st_mtime
            rec.setdefault("kind", "3d")
            rec.setdefault("title", rec.get("id") or "view")
            rec.setdefault("productId", "")
            rec.setdefault("sku", "")
            rec.setdefault("name", "")
            items.append(rec)
    for run in list_codex_runs():
        folder = CODEX_SHOTS_DIR / run["run"]
        fields = product_fields(run)
        for shot in run["shots"]:
            png = folder / f"{shot['id']}.png"
            items.append(
                {
                    "id": run["run"] + "-" + shot["id"],
                    "kind": "codex",
                    "title": shot["title"],
                    **fields,
                    "url": shot["url"],
                    "mtime": png.stat().st_mtime if png.is_file() else 0,
                }
            )
    items.sort(key=lambda x: float(x.get("mtime") or 0), reverse=True)
    return items[:200]


GROUPS = ["10L", "25L", "50L", "Ziploc", "Frischhaltefolie"]
GROUP_COUNTS = {
    "10L": [20, 25, 30, 37],
    "25L": [12, 15, 18, 25],
    "50L": [8, 10, 12, 15],
}
FORMULA = "D^2 = D_core^2 + 8 n W L t / (pi * axis). V = n * 2 * W * L * t (two walls). Core from base n and D. Same bag size and film µm."


def infer_group(st: dict, title: str = "", sku: str = "") -> str:
    g = st.get("group") if isinstance(st, dict) else ""
    if g in GROUPS:
        return g
    blob = f"{title} {sku} {(st or {}).get('name') or ''} {(st or {}).get('sku') or ''}".upper()
    cat = (st or {}).get("category") or ""
    if cat == "zip_bag" or "ZIP" in blob:
        return "Ziploc"
    if cat == "cling" or "CLING" in blob or "FRISCHHALTE" in blob:
        return "Frischhaltefolie"
    if "50L" in blob:
        return "50L"
    if "25L" in blob:
        return "25L"
    if "10L" in blob:
        return "10L"
    return ""


def parse_counts(raw) -> list[int] | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (list, tuple)):
        parts = raw
    else:
        parts = str(raw).replace(";", ",").split(",")
    out: list[int] = []
    for p in parts:
        try:
            n = int(float(str(p).strip()))
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in out:
            out.append(n)
    return out or None


def scale_roll_diameter(n: float, n_base: float, d_base: float, t_mm: float, bag_w: float, bag_len: float, axis: float) -> dict:
    """Two-wall bag film, folded to the roll axis. Calibrate core from the base SKU, then D(n).

    Annulus volume π/4 (D² − D_core²) axis equals film volume n · 2 · W · L · t
    when the web is folded to width = axis (extra bag width becomes extra layers).
    """
    if min(n, n_base, d_base, t_mm, bag_w, bag_len, axis) <= 0:
        raise ValueError("roll scale needs positive n, diameter, thickness, bag size, axis")

    def vol(nn: float) -> float:
        return nn * 2.0 * bag_w * bag_len * t_mm

    coeff = 4.0 / (math.pi * axis)
    core_sq = d_base * d_base - coeff * vol(n_base)
    core_clamped = core_sq < 0
    if core_clamped:
        core_sq = 0.0
    d = math.sqrt(core_sq + coeff * vol(n))
    return {
        "n": int(n) if n == int(n) else n,
        "D_mm": round(d, 2),
        "core_D_mm": round(math.sqrt(core_sq), 2),
        "core_clamped": core_clamped,
        "film_volume_mm3": round(vol(n), 1),
        "formula": FORMULA,
    }


def variant_cartons(base: dict, counts: list[int] | None = None) -> dict:
    """base: mockup factory+state. Keep window grid, shrink/grow carton with D(n)."""
    st = base.get("state") or {}
    fac = base.get("factory") or {}
    film = fac.get("product", {}).get("film") or {}
    rp = fac.get("retail_pack") or {}
    pack = fac.get("packing") or {}
    group = infer_group(st, base.get("title") or "", (fac.get("product") or {}).get("sku") or "")
    n_base = _num(st.get("pcs_per_pack")) or _num((fac.get("product") or {}).get("pcs_per_pack"))
    t_mic = _num(film.get("thickness_mic_one_side")) or _num(st.get("film_thickness_mic"))
    t = (t_mic / 1000.0) if t_mic else None
    bag_w = _num(film.get("bag_width_mm")) or _num(st.get("bag_width_mm"))
    bag_len = _num(film.get("bag_length_mm")) or _num(st.get("bag_length_mm"))
    axis = _num(rp.get("L_mm")) or _num((st.get("box") or {}).get("l"))
    d_base = _num(rp.get("W_mm")) or _num((st.get("box") or {}).get("w"))
    sku = (fac.get("product") or {}).get("sku") or st.get("sku") or ""
    counts = parse_counts(counts) or GROUP_COUNTS.get(group)
    missing = [k for k, v in {
        "bags/roll": n_base, "film µm": t, "bag W mm": bag_w,
        "bag L mm": bag_len, "roll axis mm": axis, "roll Ø mm": d_base,
    }.items() if not v]
    if missing:
        return {
            "ok": False,
            "sku": sku,
            "title": base.get("title"),
            "group": group,
            "reason": "need " + ", ".join(missing),
            "counts": counts,
        }
    if not counts:
        return {"ok": False, "sku": sku, "title": base.get("title"), "group": group, "reason": "no bag-count list for this group"}
    grid = pack.get("grid_LWH") or [1, 3, 3]
    leftover = pack.get("leftover_mm") or [0, 0, 0]
    nx, ny, nz = [int(x) for x in grid]
    carton_base = {
        "l": round(nx * axis + leftover[0], 2),
        "w": round(ny * d_base + leftover[1], 2),
        "h": round(nz * d_base + leftover[2], 2),
    }
    vol_base = carton_base["l"] * carton_base["w"] * carton_base["h"]
    rows = []
    for n in counts:
        sc = scale_roll_diameter(n, n_base, d_base, t, bag_w, bag_len, axis)
        d = sc["D_mm"]
        box = {"l": axis, "w": d, "h": d}
        carton = {
            "l": round(nx * axis + leftover[0], 2),
            "w": round(ny * d + leftover[1], 2),
            "h": round(nz * d + leftover[2], 2),
        }
        packed = pack_carton(box, carton)
        vol = carton["l"] * carton["w"] * carton["h"]
        rows.append(
            {
                "bags_per_roll": n,
                "is_base": int(n) == int(n_base),
                "roll": {"L_mm": axis, "D_mm": d},
                "carton_inner_mm": carton,
                "tight_inner_mm": packed.get("tight_inner_mm") if packed else None,
                "packing": packed,
                "core_D_mm": sc["core_D_mm"],
                "film_volume_mm3": sc["film_volume_mm3"],
                "delta_D_mm": round(d - d_base, 2),
                "delta_carton_W_mm": round(carton["w"] - carton_base["w"], 2),
                "delta_carton_H_mm": round(carton["h"] - carton_base["h"], 2),
                "carton_volume_cm3": round(vol / 1000.0, 1),
                "delta_carton_volume_pct": round((vol / vol_base - 1.0) * 100.0, 1) if vol_base else None,
            }
        )
    return {
        "ok": True,
        "sku": sku,
        "title": base.get("title"),
        "group": group,
        "base_bags_per_roll": int(n_base),
        "base_roll": {"L_mm": axis, "D_mm": d_base},
        "base_carton_inner_mm": carton_base,
        "layout": {"grid_LWH": grid, "leftover_mm": leftover, "note": "Same window pack. Diameter scales with bag count; axis and leftover slack stay."},
        "film": {
            "thickness_mic_one_side": t_mic,
            "bag_width_mm": bag_w,
            "bag_length_mm": bag_len,
        },
        "formula": FORMULA,
        "core_D_mm": rows[0]["core_D_mm"] if rows else None,
        "variants": rows,
    }


def variants_markdown(table: dict) -> str:
    lines = [
        f"# {table.get('title') or table.get('sku') or 'Roll scale'}",
        "",
        f"- Group: {table.get('group') or '—'}",
        f"- SKU: {table.get('sku') or '—'}",
        f"- Base: {table.get('base_bags_per_roll')} bags / roll",
        f"- Formula: `{table.get('formula')}`",
        f"- Core (calibrated): {table.get('core_D_mm')} mm",
        f"- Grid: {table.get('layout', {}).get('grid_LWH')}",
        "",
        "| bags/roll | Ø mm | ΔØ mm | carton inner L×W×H mm | tight L×W×H mm | carton cm³ | Δ vol % |",
        "|---|---:|---:|---|---|---:|---:|",
    ]
    for row in table.get("variants") or []:
        c = row.get("carton_inner_mm") or {}
        t = row.get("tight_inner_mm") or []
        mark = " ← base" if row.get("is_base") else ""
        carton = f"{c.get('l')} × {c.get('w')} × {c.get('h')}"
        if isinstance(t, dict):
            tight = f"{t.get('l')} × {t.get('w')} × {t.get('h')}"
        elif isinstance(t, (list, tuple)) and len(t) >= 3:
            tight = f"{t[0]} × {t[1]} × {t[2]}"
        else:
            tight = "—"
        d = (row.get("roll") or {}).get("D_mm")
        lines.append(
            f"| {row.get('bags_per_roll')}{mark} | {d} | {row.get('delta_D_mm')} | {carton} | {tight} | {row.get('carton_volume_cm3')} | {row.get('delta_carton_volume_pct')} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_variants() -> None:
    FACTORY.mkdir(parents=True, exist_ok=True)
    out_dir = FACTORY / "variants"
    out_dir.mkdir(exist_ok=True)
    by_group: dict[str, list] = {g: [] for g in GROUPS}
    for folder in sorted(MOCKUPS.iterdir()):
        meta = folder / "mockup.json"
        if not folder.is_dir() or not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text())
        except json.JSONDecodeError:
            continue
        st = data.get("state") or {}
        sku = (data.get("factory") or {}).get("product", {}).get("sku") or st.get("sku") or folder.name
        group = infer_group(st, data.get("title") or "", sku)
        member = {"id": data.get("id"), "title": data.get("title"), "sku": sku, "path": f"mockups/{folder.name}/mockup.json"}
        if group:
            by_group.setdefault(group, []).append(member)
        if group not in GROUP_COUNTS:
            continue
        table = variant_cartons(data, GROUP_COUNTS[group])
        stem = sku or folder.name
        (out_dir / f"{stem}.json").write_text(json.dumps(table, indent=2, ensure_ascii=False) + "\n")
        if table.get("ok"):
            (out_dir / f"{stem}.md").write_text(variants_markdown(table), encoding="utf-8")
            member["variants"] = f"factory/variants/{stem}.json"
        else:
            member["variants_reason"] = table.get("reason")
    payload = {
        "groups": GROUPS,
        "counts_by_group": GROUP_COUNTS,
        "formula": FORMULA,
        "members": by_group,
        "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "Ziploc and Frischhaltefolie have no bag-count → diameter scale. 10L needs factory film µm + bag W×L before D(n) is computed.",
    }
    (FACTORY / "groups.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    (FACTORY / "ROLL-SCALE.json").write_text(
        json.dumps(
            {
                "formula": FORMULA,
                "symbols": {
                    "n": "bags per roll",
                    "W": "open bag width mm",
                    "L": "open bag length mm including hem",
                    "t": "one-side film thickness in mm (µm / 1000)",
                    "axis": "roll axis length mm (retail L)",
                    "D": "roll diameter mm (retail W = H)",
                    "D_core": "cardboard core + wrap, calibrated from the base SKU",
                },
                "assumptions": [
                    "Same bag size and film thickness as the base SKU.",
                    "Bag is folded so wound width equals the roll axis; extra bag width becomes extra layers.",
                    "Two film walls per bag.",
                    "Window pack grid and leftover slack stay; carton W and H follow  Ny·D and Nz·D.",
                    "Factory carton count and factory outer carton are ignored.",
                ],
                "counts_by_group": GROUP_COUNTS,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )


def _num(v):
    if v in (None, ""):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _contents(state: dict) -> dict:
    cat = state.get("category") or ""
    pcs = _num(state.get("pcs_per_pack"))
    fw = _num(state.get("film_width_cm"))
    fl = _num(state.get("film_length_m"))
    if pcs is not None and pcs == int(pcs):
        pcs = int(pcs)
    out = {"kind": cat or None, "pcs_per_pack": pcs, "film_width_cm": fw, "film_length_m": fl, "label_en": "", "label_zh": ""}
    if cat == "trash_bag" and pcs:
        out["bags_per_roll"] = pcs
        out["label_en"] = f"{pcs} bags / roll"
        out["label_zh"] = f"每卷{pcs}只垃圾袋"
    elif cat == "zip_bag" and pcs:
        out["label_en"] = f"{pcs} pieces / box"
        out["label_zh"] = f"每盒{pcs}只"
    elif cat == "cling" and fw and fl:
        out["label_en"] = f"{fw:g} cm × {fl:g} m film"
        out["label_zh"] = f"保鲜膜 {fw:g} cm × {fl:g} m"
    elif pcs:
        out["label_en"] = f"{pcs} pcs / pack"
        out["label_zh"] = f"每包{pcs}只"
    return out


def _film(state: dict) -> dict | None:
    mic = _num(state.get("film_thickness_mic"))
    bw = _num(state.get("bag_width_mm"))
    bl = _num(state.get("bag_length_mm"))
    color = str(state.get("film_color") or "").strip()
    rope = str(state.get("rope_color") or "").strip()
    if not any([mic, bw, bl, color, rope]):
        return None
    return {
        "thickness_mic_one_side": mic,
        "bag_width_mm": bw,
        "bag_length_mm": bl,
        "bag_length_note": state.get("bag_length_note") or "",
        "color": color,
        "rope_color": rope,
        "printing": state.get("printing") or "",
        "wrap_sheet_gsm": _num(state.get("wrap_sheet_gsm")),
        "wrap_sheet_mm": state.get("wrap_sheet_mm") or None,
        "note_en": "Film spec from factory. Carton size and rolls per carton are ours, not the factory packing list.",
        "note_zh": "薄膜数据来自工厂。外箱尺寸与装箱数用我们自己的测算，不用工厂装箱。",
    }


def factory_block(title: str, state: dict, tex: dict) -> dict:
    box = mm_triple(state.get("box"))
    carton = mm_triple(state.get("carton"))
    pack = pack_carton(box, carton) if box and carton else None
    shape = "roll" if state.get("shape") == "roll" else "box"
    nx = ny = nz = 0
    layout_en = ""
    layout_zh = ""
    if pack:
        nx, ny, nz = pack["grid_LWH"]
        layout_en = f"{nx} along carton L, {ny} along W, {nz} along H"
        layout_zh = f"沿外箱长{nx}个，沿宽{ny}个，沿高{nz}层，共{pack['pcs_per_carton']}个"
    return {
        "schema": "cartonizer-factory-v1",
        "units": "mm",
        "product": {
            "title": title,
            "name_en": state.get("name") or title,
            "name_zh": state.get("name_zh") or "",
            "sku": state.get("sku") or "",
            "group": infer_group(state, title, state.get("sku") or ""),
            "category": state.get("category") or "",
            "pcs_per_pack": state.get("pcs_per_pack") if state.get("pcs_per_pack") not in (None, "") else None,
            "contents": _contents(state),
            "film": _film(state),
            "bag_mm": (
                {"L_mm": float(state["bag_mm"]["l"]), "W_mm": float(state["bag_mm"]["w"])}
                if isinstance(state.get("bag_mm"), dict)
                and state["bag_mm"].get("l")
                and state["bag_mm"].get("w")
                else None
            ),
        },
        "retail_pack": {
            "shape": shape,
            "shape_zh": "卷" if shape == "roll" else "彩盒",
            "L_mm": None if not box else box["l"],
            "W_mm": None if not box else box["w"],
            "H_mm": None if not box else box["h"],
            "note_en": "Roll: L=axis, W=H=diameter. Box: L=length, W=depth/thickness, H=height. Outer size.",
            "note_zh": "卷材：L=轴向长度，W=H=直径。彩盒：L=长，W=厚/深，H=高。均为外尺寸。",
        },
        "shipping_carton": {
            "inner_L_mm": None if not carton else carton["l"],
            "inner_W_mm": None if not carton else carton["w"],
            "inner_H_mm": None if not carton else carton["h"],
            "note_en": "Inner dimensions only. No flute/wall.",
            "note_zh": "外箱内径，不含瓦楞厚度。",
        },
        "packing": None
        if not pack
        else {
            **pack,
            "rolls_per_carton": pack["pcs_per_carton"] if shape == "roll" else None,
            "boxes_per_carton": pack["pcs_per_carton"] if shape == "box" else None,
            "layout_en": layout_en,
            "layout_zh": layout_zh,
            "requested_pcs_per_carton": state.get("requested_pcs_per_carton"),
            "fits_requested": (
                None
                if state.get("requested_pcs_per_carton") in (None, "")
                else pack["pcs_per_carton"] >= int(state["requested_pcs_per_carton"])
            ),
        },
        "artwork": {k: v for k, v in tex.items() if v},
    }


def write_catalog() -> None:
    FACTORY.mkdir(parents=True, exist_ok=True)
    products = []
    MOCKUPS.mkdir(parents=True, exist_ok=True)
    for folder in sorted(MOCKUPS.iterdir()):
        meta = folder / "mockup.json"
        if not folder.is_dir() or not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text())
        except json.JSONDecodeError:
            continue
        fac = data.get("factory") or {}
        pack = fac.get("packing") or {}
        st = data.get("state") or {}
        products.append(
            {
                "id": data.get("id") or folder.name,
                "title": data.get("title") or folder.name,
                "savedAt": data.get("savedAt") or "",
                "name_en": (fac.get("product") or {}).get("name_en") or "",
                "sku": (fac.get("product") or {}).get("sku") or st.get("sku") or "",
                "group": infer_group(st, data.get("title") or "", (fac.get("product") or {}).get("sku") or ""),
                "shape": (fac.get("retail_pack") or {}).get("shape") or "",
                "pcs_per_carton": pack.get("pcs_per_carton"),
                "path": f"mockups/{folder.name}/mockup.json",
            }
        )
    catalog = {
        "schema": "cartonizer-factory-v1",
        "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "export_ready": True,
        "export_done": False,
        "groups": GROUPS,
        "products": products,
    }
    (FACTORY / "catalog.json").write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")
    write_variants()


def grok_chat(user_text: str, images: list[str], table: dict) -> dict:
    content: list[dict] = []
    for url in images[:4]:
        content.append({"type": "input_image", "image_url": url, "detail": "high"})
    table_txt = (
        f"Table now: SKU={table.get('name') or '(none)'}; "
        f"shape={table.get('shape') or 'box'}; "
        f"retail box mm LWH={table.get('box')}; "
        f"carton inner mm LWH={table.get('carton')}.\n"
        f"Attached photos: {len(images)} (order is the order the user added them).\n\n"
        f"{user_text}"
    )
    content.append({"type": "input_text", "text": table_txt})
    resp = api_json(
        "POST",
        "/responses",
        {
            "model": "grok-4.6",
            "instructions": SYSTEM,
            "input": [{"role": "user", "content": content}],
        },
        timeout=360,
    )
    thinking, text = extract_text(resp)
    apply = parse_apply(text) or {}
    return {
        "thinking": thinking,
        "text": visible_text(text),
        "apply": apply,
        "response_id": resp.get("id"),
    }


def _guess_ext(url: str, content_type: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "webp" in ct:
        return ".webp"
    if url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        return Path(url.split("?")[0]).suffix
    return ".png"


def download_image(url: str, token: str) -> Path:
    RUNTIME.mkdir(exist_ok=True)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        blob = r.read()
        ext = _guess_ext(url, r.headers.get("Content-Type", ""))
    path = RUNTIME / f"wrap-{int(time.time())}{ext}"
    path.write_bytes(blob)
    return path


def imagine_wrap(image_data_url: str, name: str, shape: str) -> str | None:
    prompt = (
        f"Flat product wrap texture of this {name or 'household'} pack, "
        f"{'cylindrical roll label unwrapped' if shape == 'roll' else 'retail box front and sides as a print wrap'}. "
        "Orthographic, fill the frame, keep the real printed artwork from the reference, "
        "no studio background, no floor, no extra products, no hands."
    )
    payload = {
        "model": "grok-imagine-image-2.0",
        "prompt": prompt,
        "image": {"url": image_data_url, "type": "image_url"},
    }
    try:
        resp = api_json("POST", "/images/edits", payload, timeout=180)
    except RuntimeError:
        resp = api_json(
            "POST",
            "/images/generations",
            {"model": "grok-imagine-image-2.0", "prompt": prompt},
            timeout=180,
        )
    url = None
    if isinstance(resp.get("data"), list) and resp["data"]:
        first = resp["data"][0] or {}
        url = first.get("url")
        b64 = first.get("b64_json")
        if b64:
            RUNTIME.mkdir(exist_ok=True)
            path = RUNTIME / f"wrap-{int(time.time())}.png"
            import base64

            path.write_bytes(base64.b64decode(b64))
            return "/runtime/" + path.name
    url = url or resp.get("url")
    if not url:
        return None
    if url.startswith("data:"):
        import base64

        header, b64 = url.split(",", 1)
        ext = ".jpg" if "jpeg" in header else ".png"
        RUNTIME.mkdir(exist_ok=True)
        path = RUNTIME / f"wrap-{int(time.time())}{ext}"
        path.write_bytes(base64.b64decode(b64))
        return "/runtime/" + path.name
    path = download_image(url, bearer())
    return "/runtime/" + path.name


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys_stderr = __import__("sys").stderr
        sys_stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _json(self, code: int, obj: dict) -> None:
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 28_000_000:
            raise ValueError("payload too large")
        raw = self.rfile.read(n) if n else b"{}"
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("expected object")
        return data

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/api/health":
            grok_ok, grok_err = False, ""
            try:
                bearer()
                grok_ok = True
            except Exception as e:
                grok_err = str(e)
            codex_ok = read_codex_auth() is not None
            payload = {"ok": grok_ok, "grok": grok_ok, "codex": codex_ok}
            if grok_err:
                payload["error"] = grok_err
            self._json(200 if grok_ok or codex_ok else 503, payload)
            return
        path = posixpath.normpath(urllib.parse.urlparse(self.path).path)
        if path == "/api/codex/shots":
            self._json(200, {"shots": [{"id": s["id"], "title": s["title"], "aspect": s["aspect"]} for s in SHOTS]})
            return
        if path == "/api/codex/runs":
            self._json(200, {"runs": list_codex_runs()})
            return
        if path == "/api/views":
            self._json(200, {"views": list_views()})
            return
        if path == "/api/sheet.svg":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                l = float((qs.get("l") or [""])[0])
                w = float((qs.get("w") or [""])[0])
                h = float((qs.get("h") or [""])[0])
            except (TypeError, ValueError):
                self._json(400, {"error": "need l, w, h millimetres"})
                return
            name = str((qs.get("name") or [""])[0])[:80]
            try:
                svg = sheet_svg(l, w, h, name)
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            raw = svg.encode("utf-8")
            fname = f"cartonizer-{_slug(name) or 'box'}-sheet.svg"
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
            return
        if path == "/api/mockups":
            self._json(200, {"mockups": list_mockups()})
            return
        if path == "/api/groups":
            write_variants()
            gpath = FACTORY / "groups.json"
            if gpath.is_file():
                self._json(200, json.loads(gpath.read_text()))
            else:
                self._json(200, {"groups": GROUPS, "members": {}})
            return
        if path == "/api/roll-scale":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            mid = (qs.get("id") or [""])[0]
            counts = parse_counts((qs.get("counts") or [""])[0])
            data = load_mockup(mid) if mid else None
            if not data:
                self._json(404, {"ok": False, "error": "unknown mockup"})
                return
            table = variant_cartons(data, counts)
            self._json(200, table)
            return
        one = re.match(r"^/api/mockups/([a-z0-9][a-z0-9-]{0,80})$", path)
        if one:
            data = load_mockup(one.group(1))
            if not data:
                self._json(404, {"error": "unknown mockup"})
                return
            self._json(200, data)
            return
        if path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/views":
            try:
                body = self._read_json()
            except Exception as e:
                self._json(400, {"error": str(e)})
                return
            try:
                saved = save_view(
                    str(body.get("kind") or "3d"),
                    str(body.get("title") or ""),
                    str(body.get("dataUrl") or ""),
                    str(body.get("name") or ""),
                    str(body.get("sku") or ""),
                    str(body.get("productId") or ""),
                )
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, saved)
            return
        if path == "/api/codex/shot":
            try:
                body = self._read_json()
            except Exception as e:
                self._json(400, {"error": str(e)})
                return
            shot_id = str(body.get("id") or "")
            run = str(body.get("run") or f"s{int(time.time())}")
            state = body.get("state") if isinstance(body.get("state"), dict) else {}
            extras = body.get("images") if isinstance(body.get("images"), list) else []
            layouts = body.get("layouts") if isinstance(body.get("layouts"), list) else []
            try:
                saved = save_codex_shot(shot_id, state, extras=extras, layouts=layouts, run=run)
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            except RuntimeError as e:
                code = 401 if "login" in str(e).lower() else 502
                self._json(code, {"error": str(e)})
                return
            self._json(200, saved)
            return
        if path == "/api/mockups":
            try:
                saved = save_mockup(self._read_json())
            except Exception as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, saved)
            return
        if path == "/api/snapshot":
            try:
                body = self._read_json()
            except Exception as e:
                self._json(400, {"error": str(e)})
                return
            name = str(body.get("name") or "shot").strip()
            name = re.sub(r"[^a-zA-Z0-9._-]+", "-", name)[:80] or "shot"
            url = str(body.get("dataUrl") or "")
            if not url.startswith("data:image"):
                self._json(400, {"error": "need dataUrl"})
                return
            header, b64 = url.split(",", 1)
            import base64

            ext = ".jpg" if "jpeg" in header else ".png"
            out_dir = FACTORY / "report" / "figures"
            out_dir.mkdir(parents=True, exist_ok=True)
            dest = out_dir / (name + ext)
            dest.write_bytes(base64.b64decode(b64))
            self._json(200, {"ok": True, "path": str(dest.relative_to(ROOT))})
            return
        if path == "/api/roll-scale":
            try:
                body = self._read_json()
            except Exception as e:
                self._json(400, {"error": str(e)})
                return
            mid = str(body.get("id") or "")
            data = load_mockup(mid) if mid else None
            st = body.get("state") if isinstance(body.get("state"), dict) else None
            if st:
                title = str(body.get("title") or st.get("name") or (data or {}).get("title") or "")
                if not st.get("group"):
                    st = {**st, "group": infer_group(st, title, st.get("sku") or "")}
                data = {
                    "title": title,
                    "state": st,
                    "factory": factory_block(title, st, {}),
                }
            if not data:
                self._json(400, {"ok": False, "error": "need mockup id or state"})
                return
            n_base = _num(body.get("n_base"))
            if n_base:
                data["state"]["pcs_per_pack"] = int(n_base) if n_base == int(n_base) else n_base
            counts = parse_counts(body.get("counts"))
            table = variant_cartons(data, counts)
            self._json(200, table)
            return
        if path != "/api/chat":
            self._json(404, {"error": "not found"})
            return
        try:
            body = self._read_json()
        except Exception as e:
            self._json(400, {"error": str(e)})
            return
        text = str(body.get("text") or "").strip()
        images: list[str] = []
        if isinstance(body.get("images"), list):
            images.extend(x for x in body["images"] if isinstance(x, str) and x.startswith("data:image"))
        if isinstance(body.get("image"), str) and body["image"].startswith("data:image"):
            images.append(body["image"])
        images = images[:4]
        if not text and not images:
            self._json(400, {"error": "send a message or a product photo"})
            return
        table = body.get("table") if isinstance(body.get("table"), dict) else {}
        try:
            result = grok_chat(text or "Use the attached photos.", images, table)
        except Exception as e:
            self._json(502, {"error": str(e)})
            return
        apply = result.get("apply") or {}
        want_tex = bool(apply.get("texture")) and bool(images)
        shape = apply.get("shape") if apply.get("shape") in ("box", "roll") else None
        name = apply.get("name") if isinstance(apply.get("name"), str) else ""
        payload = {
            "thinking": result.get("thinking") or "",
            "text": result.get("text") or "",
            "apply": bool(apply.get("apply")),
            "texture": False,
            "textureUrl": None,
            "name": name,
            "shape": shape,
            "box": mm_triple(apply.get("box")),
            "carton": mm_triple(apply.get("carton")),
            "ask": apply.get("ask") if isinstance(apply.get("ask"), str) else "",
        }
        if want_tex and payload["box"]:
            wrap_src = images[-1]
            if len(images) >= 2:
                wrap_src = images[1]
            try:
                payload["textureUrl"] = imagine_wrap(wrap_src, name, shape or "box")
                payload["texture"] = bool(payload["textureUrl"])
            except Exception as e:
                payload["textureError"] = str(e)
        self._json(200, payload)

    def do_DELETE(self) -> None:
        path = posixpath.normpath(urllib.parse.urlparse(self.path).path)
        m = re.match(r"^/api/mockups/([a-z0-9][a-z0-9-]{0,80})$", path)
        if not m:
            self._json(404, {"error": "not found"})
            return
        mid = m.group(1)
        folder = MOCKUPS / mid
        if not folder.is_dir():
            self._json(404, {"error": "unknown mockup"})
            return
        shutil.rmtree(folder)
        write_catalog()
        self._json(200, {"ok": True, "id": mid})


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "mockup").lower()).strip("-")
    return (s or "mockup")[:40]


def _copy_asset(src: str, dest_dir: Path, name: str) -> str:
    if not src or not isinstance(src, str):
        return ""
    if src.startswith("data:"):
        return ""
    rel = src.split("?", 1)[0].lstrip("/")
    path = (ROOT / rel).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError:
        return src
    if not path.is_file():
        return src
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (name + path.suffix)
    shutil.copy2(path, dest)
    return "/mockups/" + dest_dir.name + "/" + dest.name


def list_mockups() -> list[dict]:
    MOCKUPS.mkdir(parents=True, exist_ok=True)
    rows = []
    for folder in sorted(MOCKUPS.iterdir(), reverse=True):
        meta = folder / "mockup.json"
        if not folder.is_dir() or not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text())
        except json.JSONDecodeError:
            continue
        st = data.get("state") or {}
        rows.append(
            {
                "id": folder.name,
                "title": data.get("title") or folder.name,
                "savedAt": data.get("savedAt") or "",
                "name": st.get("name") or "",
                "group": infer_group(st, data.get("title") or "", st.get("sku") or ""),
                "shape": st.get("shape") or "box",
                "pcs_per_carton": ((data.get("factory") or {}).get("packing") or {}).get("pcs_per_carton"),
            }
        )
    rows.sort(key=lambda r: r.get("savedAt") or "", reverse=True)
    return rows


def save_mockup(body: dict) -> dict:
    state = body.get("state") if isinstance(body.get("state"), dict) else {}
    title = str(body.get("title") or state.get("name") or "Mockup").strip()[:80]
    mid = f"{_slug(title)}-{int(time.time())}"
    folder = MOCKUPS / mid
    folder.mkdir(parents=True, exist_ok=True)
    tex = {
        "textureUrl": _copy_asset(state.get("textureUrl") or "", folder, "wrap"),
        "textureFront": _copy_asset(state.get("textureFront") or "", folder, "front"),
        "textureBack": _copy_asset(state.get("textureBack") or "", folder, "back"),
        "textureSide": _copy_asset(state.get("textureSide") or "", folder, "side"),
    }
    st = {
        "name": state.get("name") or title,
        "name_zh": state.get("name_zh") or "",
        "sku": state.get("sku") or "",
        "group": infer_group(state, title, state.get("sku") or ""),
        "category": state.get("category") or "",
        "pcs_per_pack": state.get("pcs_per_pack"),
        "film_width_cm": state.get("film_width_cm"),
        "film_length_m": state.get("film_length_m"),
        "film_thickness_mic": state.get("film_thickness_mic"),
        "bag_width_mm": state.get("bag_width_mm"),
        "bag_length_mm": state.get("bag_length_mm"),
        "bag_length_note": state.get("bag_length_note") or "",
        "film_color": state.get("film_color") or "",
        "rope_color": state.get("rope_color") or "",
        "printing": state.get("printing") or "",
        "wrap_sheet_gsm": state.get("wrap_sheet_gsm"),
        "wrap_sheet_mm": state.get("wrap_sheet_mm"),
        "requested_pcs_per_carton": state.get("requested_pcs_per_carton"),
        "bag_mm": state.get("bag_mm"),
        "unit": "mm",
        "shape": "roll" if state.get("shape") == "roll" else "box",
        "box": mm_triple(state.get("box")),
        "carton": mm_triple(state.get("carton")),
        **tex,
    }
    saved = {
        "schema": "cartonizer-factory-v1",
        "id": mid,
        "title": title,
        "savedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "state": st,
        "factory": factory_block(title, st, tex),
    }
    (folder / "mockup.json").write_text(json.dumps(saved, indent=2, ensure_ascii=False) + "\n")
    write_catalog()
    return saved


def load_mockup(mid: str) -> dict | None:
    if not SAFE_ID.match(mid):
        return None
    meta = MOCKUPS / mid / "mockup.json"
    if not meta.is_file():
        return None
    try:
        data = json.loads(meta.read_text())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def migrate_mockups() -> None:
    old = RUNTIME / "mockups"
    MOCKUPS.mkdir(parents=True, exist_ok=True)
    if old.is_dir():
        for folder in old.iterdir():
            dest = MOCKUPS / folder.name
            if folder.is_dir() and not dest.exists():
                shutil.move(str(folder), str(dest))
    for folder in MOCKUPS.iterdir():
        meta = folder / "mockup.json"
        if not folder.is_dir() or not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text())
        except json.JSONDecodeError:
            continue
        st = data.get("state") if isinstance(data.get("state"), dict) else {}
        title = data.get("title") or folder.name
        sku = str(st.get("sku") or "")
        inferred = infer_group(st, title, sku)
        if inferred:
            st["group"] = inferred
        if not st.get("category"):
            if "Zipbeutel" in title or "zip" in title.lower():
                st["category"] = "zip_bag"
            elif "cling" in title.lower():
                st["category"] = "cling"
            elif any(x in title for x in ("10L", "25L", "50L", "Swirl")) or sku.startswith("OKIO-"):
                st["category"] = "trash_bag"
        if st.get("pcs_per_pack") in (None, "") and st.get("category") == "trash_bag":
            if "50L" in title:
                st["pcs_per_pack"] = 15
            elif "25L" in title:
                st["pcs_per_pack"] = 25
            elif "10L" in title or "Swirl" in title:
                st["pcs_per_pack"] = 37
        if st.get("pcs_per_pack") in (None, "") and st.get("category") == "zip_bag":
            st["pcs_per_pack"] = 30
        sku = str(st.get("sku") or "")
        if sku == "OKIO-25L" or "25L" in title:
            st.setdefault("film_thickness_mic", 20)
            st.setdefault("bag_width_mm", 530)
            st.setdefault("bag_length_mm", 590)
            st.setdefault("bag_length_note", "540+50 mm")
            st.setdefault("film_color", "Orange")
            st.setdefault("rope_color", "Orange")
            st.setdefault("printing", "None (unprinted)")
            st.setdefault("wrap_sheet_gsm", 128)
            st.setdefault("wrap_sheet_mm", [120, 250])
        if sku == "OKIO-50L" or "50L" in title:
            st.setdefault("film_thickness_mic", 25)
            st.setdefault("bag_width_mm", 600)
            st.setdefault("bag_length_mm", 770)
            st.setdefault("bag_length_note", "720+50 mm")
            st.setdefault("film_color", "Purple")
            st.setdefault("rope_color", "Orange")
            st.setdefault("printing", "None (unprinted)")
            st.setdefault("wrap_sheet_gsm", 128)
            st.setdefault("wrap_sheet_mm", [140, 250])
        tex = {
            "textureUrl": str(st.get("textureUrl") or "").replace("/runtime/mockups/", "/mockups/"),
            "textureFront": str(st.get("textureFront") or "").replace("/runtime/mockups/", "/mockups/"),
            "textureBack": str(st.get("textureBack") or "").replace("/runtime/mockups/", "/mockups/"),
            "textureSide": str(st.get("textureSide") or "").replace("/runtime/mockups/", "/mockups/"),
        }
        for k, v in tex.items():
            st[k] = v
        data["schema"] = "cartonizer-factory-v1"
        data["state"] = st
        data["factory"] = factory_block(data.get("title") or folder.name, st, tex)
        meta.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    write_catalog()


def main() -> None:
    RUNTIME.mkdir(exist_ok=True)
    MOCKUPS.mkdir(parents=True, exist_ok=True)
    FACTORY.mkdir(parents=True, exist_ok=True)
    migrate_mockups()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Cartonizer  http://{HOST}:{PORT}/")
    print("Auth: Grok Build login (~/.grok/auth.json)")
    print("Codex shots: ChatGPT login (~/.codex/auth.json)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
