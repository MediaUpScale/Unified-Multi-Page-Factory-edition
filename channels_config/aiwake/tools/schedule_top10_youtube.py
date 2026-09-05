# -*- coding: utf-8 -*-
"""Compatibility shim. Use ``channels_config.aiwake.tools.schedule_youtube``."""
from __future__ import annotations

from channels_config.aiwake.tools.schedule_youtube import *  # noqa: F403
from channels_config.aiwake.tools.schedule_youtube import main

if __name__ == "__main__":
    raise SystemExit(main())
