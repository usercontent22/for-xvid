"""Shared tag normalization utilities for the ingestion pipeline."""

from __future__ import annotations

import re

# Normalize punctuation/spacing/case so variants like "X videos", "x-videos",
# and "XVIDEOS.COM" are all recognized as generic site boilerplate.
GENERIC_TAG_KEYS = frozenset({"video", "videos", "xvideo", "xvideos", "xvideoscom"})


def normalized_tag_key(value: object) -> str:
    """Return an alphanumeric, case-insensitive key for matching tag variants."""
    return "".join(char.casefold() for char in str(value) if char.isalnum())


def filter_generic_tags(values):
    """Trim tags, remove generic site boilerplate, and preserve first-seen order."""
    result = []
    seen = set()
    for value in values:
        tag = str(value).strip()
        if not tag:
            continue
        if normalized_tag_key(tag) in GENERIC_TAG_KEYS:
            continue
        if tag not in seen:
            result.append(tag)
            seen.add(tag)
    return result
