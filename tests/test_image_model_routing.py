# -*- coding: utf-8 -*-
"""Avatar / Gemini image-model alias + 1K default pricing."""
from __future__ import annotations

import unittest

import config as app_config
from agents.media.providers.image_provider import _gemini_image_chain, _gemini_image_size


class ImageModelAliasTests(unittest.TestCase):
    def test_flash_aliases(self) -> None:
        self.assertEqual(
            app_config.resolve_image_model_alias("flash"),
            "models/gemini-2.5-flash-image",
        )
        self.assertEqual(
            app_config.resolve_image_model_alias("gemini-2.5-flash"),
            "models/gemini-2.5-flash-image",
        )

    def test_pro_alias(self) -> None:
        self.assertEqual(
            app_config.resolve_image_model_alias("gemini-pro"),
            "models/gemini-3-pro-image-preview",
        )

    def test_passthrough_sku(self) -> None:
        self.assertEqual(
            app_config.resolve_image_model_alias("models/gemini-2.5-flash-image"),
            "models/gemini-2.5-flash-image",
        )


class ImageSizeAndCostTests(unittest.TestCase):
    def test_default_size_is_1k(self) -> None:
        self.assertEqual(app_config.GEMINI_IMAGE_SIZE, "1K")
        self.assertEqual(_gemini_image_size(), "1K")

    def test_1k_prices(self) -> None:
        self.assertAlmostEqual(
            app_config.estimate_gemini_image_usd("models/gemini-2.5-flash-image"),
            0.005,
        )
        self.assertAlmostEqual(
            app_config.estimate_gemini_image_usd("models/gemini-3-pro-image-preview"),
            0.03,
        )
        self.assertAlmostEqual(
            app_config.estimate_gemini_image_usd(
                "models/gemini-3-pro-image-preview", image_size="2K"
            ),
            0.134,
        )

    def test_flash_chain_never_escalates_to_pro(self) -> None:
        chain = _gemini_image_chain("models/gemini-2.5-flash-image")
        self.assertTrue(chain)
        self.assertEqual(chain[0], "models/gemini-2.5-flash-image")
        self.assertFalse(any("pro" in m.lower() for m in chain))

    def test_unspecified_chain_defaults_to_flash(self) -> None:
        chain = _gemini_image_chain(None)
        self.assertTrue(any("flash-image" in m for m in chain))
        self.assertNotIn("models/gemini-3-pro-image-preview", chain)


if __name__ == "__main__":
    unittest.main()
