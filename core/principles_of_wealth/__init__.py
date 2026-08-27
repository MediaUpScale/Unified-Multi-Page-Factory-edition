# -*- coding: utf-8 -*-
"""Principles of Wealth — library ingest + YouTube publish fabric."""

from core.principles_of_wealth.catalog import (
    CHANNEL_ID,
    EPISODES,
    PLAYLISTS,
    episode_by_number,
    playlist_for_episode,
)
from core.principles_of_wealth.scanner import scan_source_directory

__all__ = [
    "CHANNEL_ID",
    "EPISODES",
    "PLAYLISTS",
    "episode_by_number",
    "playlist_for_episode",
    "scan_source_directory",
]
