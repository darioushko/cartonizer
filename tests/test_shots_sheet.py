#!/usr/bin/env python3
"""Prompt templates, CAD sheet geometry, pack math. No live Codex."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import serve  # noqa: E402


class PackMath(unittest.TestCase):
    def test_120_fill(self):
        box = {"l": 100, "w": 50, "h": 40}
        carton = {"l": 400, "w": 300, "h": 200}
        p = serve.pack_carton(box, carton)
        self.assertEqual(p["pcs_per_carton"], 120)
        self.assertEqual(p["grid_LWH"], [4, 6, 5])
        self.assertEqual(p["leftover_mm"], [0, 0, 0])
        self.assertEqual(p["fill_pct"], 100.0)


class Shots(unittest.TestCase):
    def test_five_ids(self):
        ids = [s["id"] for s in serve.SHOTS]
        self.assertEqual(
            ids,
            ["closed-34", "closed-front", "open-front-tray", "open-carton", "shelf"],
        )

    def test_prompt_includes_mm_and_name(self):
        state = {
            "name": "OKIO 1L",
            "shape": "box",
            "box": {"l": 170, "w": 40, "h": 65},
            "carton": {"l": 160, "w": 170, "h": 195},
        }
        text = serve.shot_prompt("open-front-tray", state)
        self.assertIn("OKIO 1L", text)
        self.assertIn("170", text)
        self.assertIn("40", text)
        self.assertIn("65", text)
        self.assertIn("SIDE", text)
        self.assertIn("stacked", text.lower())
        self.assertIn("no invented logos", text.lower())

    def test_open_carton_side_uses_grid(self):
        text = serve.shot_prompt(
            "open-front-tray",
            {
                "name": "OKIO 1L",
                "shape": "box",
                "box": {"l": 170, "w": 40, "h": 65},
                "carton": {"l": 160, "w": 170, "h": 195},
            },
        )
        self.assertIn("3 stacked", text)
        self.assertIn("not a supermarket shelf", text.lower())

    def test_unknown_shot(self):
        with self.assertRaises(ValueError):
            serve.shot_prompt("hero-drone", {"box": {"l": 10, "w": 10, "h": 10}})

    def test_faces_beat_dieline(self):
        state = {
            "textureFront": "/mockups/a/front.jpg",
            "textureBack": "/mockups/a/back.jpg",
            "textureSide": "/mockups/a/side.jpg",
            "textureUrl": "/mockups/a/wrap.jpg",
        }
        extras = ["data:image/png;base64,xx"]
        roles = [r["role"] for r in serve.plan_shot_refs(state, extras)]
        self.assertEqual(roles, ["front", "back", "side"])

    def test_layout_appended_to_faces(self):
        state = {
            "textureFront": "/mockups/a/front.jpg",
            "textureBack": "/mockups/a/back.jpg",
            "textureSide": "/mockups/a/side.jpg",
        }
        roles = [r["role"] for r in serve.plan_shot_refs(state, layouts=["data:image/png;base64,aa"])]
        self.assertEqual(roles, ["front", "back", "side", "layout"])

    def test_dieline_only_when_no_faces(self):
        roles = [r["role"] for r in serve.plan_shot_refs({}, ["data:image/jpeg;base64,yy"])]
        self.assertEqual(roles, ["dieline"])

    def test_prompt_names_attached_faces(self):
        refs = [{"role": "front"}, {"role": "back"}]
        text = serve.shot_prompt(
            "closed-front",
            {"name": "OKIO 1L", "shape": "box", "box": {"l": 170, "w": 40, "h": 65}},
            refs,
        )
        self.assertIn("Attached image 1 is the FRONT", text)
        self.assertIn("Attached image 2 is the BACK", text)
        self.assertIn("Use ONLY this attached print", text)


class Sheet(unittest.TestCase):
    def test_glue_and_tuck(self):
        g = serve.sheet_geometry(170, 40, 65)
        self.assertEqual(g["glue"], 15)
        self.assertEqual(g["tuck"], 24)
        self.assertEqual(g["dust"], 20)

    def test_narrow_glue(self):
        g = serve.sheet_geometry(80, 20, 50)
        self.assertEqual(g["glue"], 12)
        self.assertEqual(g["tuck"], 12)
        self.assertEqual(g["dust"], 10)

    def test_svg_has_callouts(self):
        svg = serve.sheet_svg(170, 40, 65, "OKIO 1L")
        self.assertTrue(svg.startswith("<?xml"))
        self.assertIn("OKIO 1L", svg)
        self.assertIn("170", svg)
        self.assertIn("40", svg)
        self.assertIn("65", svg)
        self.assertIn('class="cut"', svg)
        self.assertIn('class="crease"', svg)
        self.assertIn("GLUE", svg)
        self.assertIn("FRONT", svg)
        self.assertIn("1 unit = 1 mm", svg)

    def test_rejects_zero(self):
        with self.assertRaises(ValueError):
            serve.sheet_geometry(170, 0, 65)


class ProductScope(unittest.TestCase):
    """Saved views & shots belong to one selected product."""

    def test_1l_view_does_not_match_10l_product(self):
        self.assertFalse(
            serve.view_matches_product(
                {"name": "OKIO 1L Zipbeutel"},
                {
                    "productId": "okio-10l-cosmetic-12-pack",
                    "name": "OKIO 10L",
                    "sku": "OKIO-10L",
                },
            )
        )

    def test_25l_name_matches_25l_product(self):
        self.assertTrue(
            serve.view_matches_product(
                {"name": "OKIO 25L"},
                {
                    "productId": "okio-25l-9-pack-window",
                    "name": "OKIO 25L",
                    "sku": "OKIO-25L",
                },
            )
        )

    def test_different_product_ids_do_not_match_even_with_sku(self):
        self.assertFalse(
            serve.view_matches_product(
                {
                    "productId": "okio-25l-9-pack-window",
                    "sku": "OKIO-25L",
                    "name": "OKIO 25L",
                },
                {
                    "productId": "okio-1l-12-pack",
                    "sku": "OKIO-25L",
                    "name": "OKIO 1L Zipbeutel",
                },
            )
        )

    def test_same_product_id_matches(self):
        self.assertTrue(
            serve.view_matches_product(
                {"productId": "okio-1l-12-pack", "name": ""},
                {"productId": "okio-1l-12-pack", "name": "OKIO 1L Zipbeutel"},
            )
        )

    def test_empty_view_hidden_when_product_selected(self):
        self.assertFalse(
            serve.view_matches_product(
                {"name": ""},
                {"productId": "okio-1l-12-pack", "name": "OKIO 1L Zipbeutel"},
            )
        )

    def test_no_product_selected_shows_all(self):
        self.assertTrue(
            serve.view_matches_product(
                {"name": "OKIO 1L Zipbeutel"},
                {"productId": "", "name": "", "sku": ""},
            )
        )

    def test_sku_match(self):
        self.assertTrue(
            serve.view_matches_product(
                {"sku": "OKIO-50L", "name": ""},
                {"productId": "okio-50l-9-pack-window", "sku": "OKIO-50L", "name": "OKIO 50L"},
            )
        )

    def test_codex_run_infers_name_from_prompt(self):
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        old = serve.CODEX_SHOTS_DIR
        serve.CODEX_SHOTS_DIR = tmp
        try:
            folder = tmp / "s123"
            folder.mkdir()
            (folder / "closed-front.png").write_bytes(b"\x89PNG")
            (folder / "closed-front.txt").write_text(
                "The product is OKIO 1L Zipbeutel, a folded printed carton\n"
            )
            runs = serve.list_codex_runs()
            self.assertEqual(runs[0]["name"], "OKIO 1L Zipbeutel")
        finally:
            serve.CODEX_SHOTS_DIR = old

    def test_save_view_stores_product_fields(self):
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        old_views = serve.VIEWS_DIR
        old_codex = serve.CODEX_SHOTS_DIR
        serve.VIEWS_DIR = tmp
        serve.CODEX_SHOTS_DIR = tmp / "codex-none"
        try:
            rec = serve.save_view(
                "3d",
                "3D - Front",
                "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
                name="OKIO 10L",
                sku="OKIO-10L",
                product_id="okio-10l-cosmetic-12-pack",
            )
            self.assertEqual(rec["productId"], "okio-10l-cosmetic-12-pack")
            self.assertEqual(rec["sku"], "OKIO-10L")
            listed = serve.list_views()
            hit = next(v for v in listed if v["id"] == rec["id"])
            self.assertEqual(hit["productId"], "okio-10l-cosmetic-12-pack")
            self.assertEqual(hit["sku"], "OKIO-10L")
        finally:
            serve.VIEWS_DIR = old_views
            serve.CODEX_SHOTS_DIR = old_codex


class CodexParse(unittest.TestCase):
    def test_sse_image_generation_call(self):
        raw = (
            "data: {\"type\":\"ping\"}\n"
            "data: {\"item\": {\"type\": \"image_generation_call\", \"result\": \""
            + ("A" * 120)
            + "\"}}\n"
            "data: [DONE]\n"
        )
        self.assertEqual(serve.extract_image_b64(raw), "A" * 120)

    def test_empty(self):
        self.assertIsNone(serve.extract_image_b64("data: {\"type\":\"done\"}\n"))


if __name__ == "__main__":
    unittest.main()
