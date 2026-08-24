# -*- coding: utf-8 -*-
"""Principles of Wealth — library ingest + YouTube publish fabric."""

from core_engine.principles_of_wealth.catalog import (
    CHANNEL_ID,
    EPISODES,
    PLAYLISTS,
    episode_by_number,
    playlist_for_episode,
)

__all__ = [
    "CHANNEL_ID",
    "EPISODES",
    "PLAYLISTS",
    "episode_by_number",
    "playlist_for_episode",
]
