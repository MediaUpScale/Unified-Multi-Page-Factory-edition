# -*- coding: utf-8 -*-
"""
pinterest_engine
----------------
Agnostic multi-channel Pinterest Sales & Recycling Engine.

Brand CTAs, URLs, and persona prompts live only in each channel's
``channels_config/<channel_id>/config.json`` (or channel ``.env``) — never in core code.

Modules:
    config             -- Channel-aware env, paths, and content-pack loading.
    image_transformer  -- Convert library assets to 1000x1500 (2:3) pins.
    publisher          -- Pinterest API v5 pin creation.
    scheduler          -- Safe-drip scheduling with per-channel history ledger.
    inventory          -- master_inventory.json merge / publication tracking.
"""
