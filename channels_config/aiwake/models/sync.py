# -*- coding: utf-8 -*-
"""Live OpenRouter model catalog — fetch, cache, closest-slug repair.

OpenRouter retires slugs without warning (``google/gemini-flash-1.5`` is the
canonical example). Hitting a retired slug is an HTTP 404 / "No endpoints found"
and, without this module, aborts a debate before a single word is spoken.

This file is the only place that talks to ``GET /api/v1/models``. Three consumers:

1. ``--sync-models`` — operator-initiated fetch, disk cache, alias health table,
   surgical YAML rewrite of broken mappings.
2. :func:`remap_if_stale` — factory-time check against the *cached* catalog.
   Disk only; a missing cache is not a fetch, because a debate start must not
   depend on OpenRouter's models endpoint being up.
3. The OpenRouter provider's 404 guard — one in-memory refetch, then a remap,
   then a retry. That is what stops a retired slug from burning the retry budget.

The models endpoint is public; a key is sent when present but never required.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    from ..settings import (
        CONFIG_PATH,
        AiwakeSettings,
        ModelSpec,
        OpenRouterConfig,
        resolve_store_dir,
    )
except ImportError:  # pragma: no cover — standalone extraction
    from settings import (  # type: ignore[no-redef]
        CONFIG_PATH,
        AiwakeSettings,
        ModelSpec,
        OpenRouterConfig,
        resolve_store_dir,
    )

_LOG = logging.getLogger("aiwake.models.sync")

MODELS_PATH = "/models"
CACHE_FILENAME = "openrouter_models.json"
DEFAULT_TIMEOUT_S = 20.0

# Vendors the CLI summary table cares about. Keys match the slug prefix.
KEY_VENDORS: tuple[tuple[str, str], ...] = (
    ("google", "Google"),
    ("deepseek", "DeepSeek"),
    ("anthropic", "Anthropic"),
    ("meta-llama", "Meta"),
)

_SUMMARY_PER_VENDOR = 8

# Tokens that mark a catalog entry as not a chat completion target.
_NON_CHAT_MARKERS: tuple[str, ...] = (
    "embed",
    "whisper",
    "tts-",
    "-tts",
    "moderation",
    "rerank",
    "transcribe",
)

# Body markers that mean "this slug is not routable", as opposed to a generic
# 400 (bad payload, context overflow, etc.).
_MISSING_MARKERS: tuple[str, ...] = (
    "no endpoints found",
    "is not a valid model",
    "model not found",
    "not a valid model id",
)

_TOKEN_SPLIT_RE = re.compile(r"[/.\-_:+]+")
_WEAK_TOKENS: frozenset[str] = frozenset(
    {"instruct", "preview", "latest", "chat", "001", "000", "beta", "exp"}
)

# Closest-match below this is a guess, not a successor. Better to 404 than to
# silently send a DeepSeek prompt to a random Google embedding model.
_MIN_MATCH_SCORE = 0.42


class SyncError(RuntimeError):
    """Raised when the live catalog cannot be fetched.

    Attributes:
        timed_out: True when the failure was a socket/read timeout. The CLI
            uses this to print a short message instead of a traceback.
    """

    def __init__(self, detail: str, *, timed_out: bool = False) -> None:
        super().__init__(detail)
        self.timed_out = timed_out
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ModelRecord:
    """One row of the OpenRouter catalog, trimmed to what matching needs."""

    id: str
    name: str = ""
    created: int = 0
    modality: str = ""
    prompt_price: str = ""
    completion_price: str = ""

    @property
    def vendor(self) -> str:
        return self.id.split("/", 1)[0].lower()

    @property
    def is_chat(self) -> bool:
        """False for embeddings, TTS, moderation and similar non-debate models."""
        ident = self.id.lower()
        if any(marker in ident for marker in _NON_CHAT_MARKERS):
            return False
        if self.modality and "embedding" in self.modality.lower():
            return False
        if self.modality and "->text" not in self.modality.lower():
            return False
        return True

    @property
    def is_debate_model(self) -> bool:
        """Chat models that are actually usable as a debate seat.

        ``:batch`` endpoints, image generators (Nano Banana / flash-image) and
        Llama Guard are in the catalog but would be a disastrous closest-match
        for a retired Gemini Flash slug.
        """
        if not self.is_chat:
            return False
        ident = self.id.lower()
        if ident.endswith(":batch") or ":batch" in ident:
            return False
        if "image" in ident or "guard" in ident:
            return False
        return True

    @property
    def is_free_tier(self) -> bool:
        return self.id.endswith(":free")


@dataclass(frozen=True, slots=True)
class AliasRemap:
    """One broken alias and the live slug that should replace it."""

    alias: str
    old_slug: str
    new_slug: str
    score: float


@dataclass
class OpenRouterCatalog:
    """Queryable snapshot of ``GET /api/v1/models``.

    Args:
        records: Chat-capable models, keyed by exact id.
        fetched_at: ISO timestamp of the fetch that produced this snapshot.
        source: URL the snapshot was pulled from.
    """

    records: dict[str, ModelRecord] = field(default_factory=dict)
    fetched_at: str = ""
    source: str = ""

    def __contains__(self, slug: str) -> bool:
        return slug in self.records

    def __len__(self) -> int:
        return len(self.records)

    def ids(self) -> frozenset[str]:
        return frozenset(self.records)

    def closest(self, slug: str) -> str | None:
        """Return the best live successor for ``slug``, or None if none is close.

        Same-vendor is a hard requirement: a retired Gemini slug must never
        remap onto a Claude model, however similar the rest of the name looks.
        Free-tier (``:free``) candidates are only considered when the query
        itself was a free-tier slug.
        """
        slug = slug.strip()
        if not slug:
            return None
        if slug in self.records:
            return slug

        vendor = slug.split("/", 1)[0].lower()
        want_free = slug.endswith(":free")
        best_id: str | None = None
        best_score = -1.0
        best_created = -1

        for record in self.records.values():
            if record.vendor != vendor or not record.is_debate_model:
                continue
            if record.is_free_tier != want_free:
                continue
            score = _slug_score(slug, record.id)
            if score < _MIN_MATCH_SCORE:
                continue
            if score > best_score or (score == best_score and record.created > best_created):
                best_id = record.id
                best_score = score
                best_created = record.created

        if best_id is None:
            return None
        _LOG.debug("closest(%s) -> %s (score %.3f)", slug, best_id, best_score)
        return best_id

    def remap(self, slug: str) -> tuple[str, bool]:
        """Return ``(resolved_slug, changed)``.

        Unchanged when the slug is live, or when nothing in the catalog is close
        enough to trust as a successor.
        """
        if slug in self.records:
            return slug, False
        successor = self.closest(slug)
        if successor is None or successor == slug:
            return slug, False
        return successor, True

    def by_vendor(self, prefix: str) -> list[ModelRecord]:
        """Chat models for ``prefix``, newest first."""
        matched = [
            record
            for record in self.records.values()
            if record.vendor == prefix and record.is_debate_model
        ]
        matched.sort(key=lambda record: (-record.created, record.id))
        return matched


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def _tokens(slug: str) -> set[str]:
    """Split a slug into comparable tokens, dropping weak suffixes."""
    return {part for part in _TOKEN_SPLIT_RE.split(slug.lower()) if part and part not in _WEAK_TOKENS}


def _slug_score(query: str, candidate: str) -> float:
    """0.0–1.0 similarity. Vendor is assumed already equal by the caller.

    Coverage of the query's distinctive tokens dominates: ``gemini-flash-1.5``
    must prefer ``gemini-1.5-flash`` (all tokens present, reordered) over
    ``gemini-2.0-flash-001`` (loses the version tokens). Jaccard and
    SequenceMatcher break remaining ties.
    """
    _, _, query_rest = query.partition("/")
    _, _, cand_rest = candidate.partition("/")
    q_tokens, c_tokens = _tokens(query), _tokens(candidate)
    if not q_tokens or not c_tokens:
        return 0.0

    distinctive = q_tokens - {query.split("/", 1)[0].lower()}
    coverage = (len(distinctive & c_tokens) / len(distinctive)) if distinctive else 0.0
    jaccard = len(q_tokens & c_tokens) / len(q_tokens | c_tokens)
    sequence = SequenceMatcher(None, query_rest.lower(), cand_rest.lower()).ratio()
    return 0.50 * coverage + 0.30 * jaccard + 0.20 * sequence


def match_score(query: str, candidate: str) -> float:
    """Public scoring hook used by tests."""
    return _slug_score(query, candidate)


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def catalog_path() -> Path:
    """On-disk cache location. Always under the module-local store/."""
    return resolve_store_dir() / CACHE_FILENAME


def _parse_records(payload: Sequence[dict[str, Any]]) -> dict[str, ModelRecord]:
    records: dict[str, ModelRecord] = {}
    for item in payload:
        ident = str(item.get("id") or "").strip()
        if not ident:
            continue
        architecture = item.get("architecture") or {}
        pricing = item.get("pricing") or {}
        records[ident] = ModelRecord(
            id=ident,
            name=str(item.get("name") or ident),
            created=int(item.get("created") or 0),
            modality=str(architecture.get("modality") or ""),
            prompt_price=str(pricing.get("prompt") or ""),
            completion_price=str(pricing.get("completion") or ""),
        )
    return records


def catalog_from_payload(payload: dict[str, Any], *, source: str = "") -> OpenRouterCatalog:
    """Build a catalog from a raw OpenRouter (or cached) JSON body."""
    data = payload.get("data")
    if not isinstance(data, list):
        raise SyncError("models payload is missing a 'data' array")
    return OpenRouterCatalog(
        records=_parse_records(data),
        fetched_at=str(payload.get("fetched_at") or ""),
        source=source or str(payload.get("source") or ""),
    )


def load_cached_catalog(path: Path | None = None) -> OpenRouterCatalog | None:
    """Read the on-disk cache. Returns None on absence or corruption.

    Never fetches. A debate start that finds no cache proceeds with the
    authored slug rather than blocking on the network.
    """
    target = path or catalog_path()
    if not target.is_file():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("cache is not a JSON object")
        catalog = catalog_from_payload(raw, source=str(raw.get("source") or ""))
    except Exception as exc:  # noqa: BLE001 — a bad cache must not kill a run
        _LOG.warning("openrouter catalog cache unreadable (%s); ignoring", exc)
        return None
    if not catalog.records:
        return None
    return catalog


def _write_cache(payload: dict[str, Any], path: Path) -> Path:
    """Atomic write so a crash mid-sync never leaves a half JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


# --------------------------------------------------------------------------- #
# Fetch
# --------------------------------------------------------------------------- #
def _models_url(gateway: OpenRouterConfig | None = None) -> str:
    base = (gateway or OpenRouterConfig()).base_url.rstrip("/")
    return f"{base}{MODELS_PATH}"


def _optional_api_key() -> str | None:
    """Best-effort secret. Sync must work before a key is configured."""
    try:
        from ..settings import load_environment  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from settings import load_environment  # type: ignore[no-redef]

    import os

    load_environment()
    value = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    return value or None


def fetch_openrouter_models(
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    gateway: OpenRouterConfig | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """GET the live catalog. Returns the raw JSON object.

    Raises:
        SyncError: Transport failure, timeout, non-JSON body, or missing ``data``.
    """
    try:
        import requests  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise SyncError("requests is not installed") from exc

    url = _models_url(gateway)
    headers = {"Accept": "application/json"}
    key = api_key if api_key is not None else _optional_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    cfg = gateway or OpenRouterConfig()
    headers["HTTP-Referer"] = cfg.referer
    headers["X-Title"] = cfg.title

    try:
        response = requests.get(url, headers=headers, timeout=timeout_s)
    except requests.Timeout as exc:
        raise SyncError(f"timed out after {timeout_s:.0f}s fetching {url}", timed_out=True) from exc
    except requests.RequestException as exc:
        raise SyncError(f"transport error fetching {url}: {exc}") from exc

    if response.status_code >= 400:
        raise SyncError(f"HTTP {response.status_code} fetching {url}: {response.text[:200]}")

    try:
        body = response.json()
    except ValueError as exc:
        raise SyncError("models endpoint did not return JSON") from exc
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise SyncError("models endpoint returned an unexpected shape")
    return body


def sync_openrouter_models(
    *,
    force: bool = True,
    persist: bool = True,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    cache_file: Path | None = None,
    gateway: OpenRouterConfig | None = None,
) -> OpenRouterCatalog:
    """Fetch (or reuse) the live catalog and optionally persist it.

    Args:
        force: When False, return a valid on-disk cache without hitting the
            network. ``--sync-models`` always passes True.
        persist: Write ``store/openrouter_models.json`` on a successful fetch.
        timeout_s: Socket timeout for the GET.
        cache_file: Override the cache path (tests pass a tmp file).
        gateway: OpenRouter base URL / attribution headers.

    Raises:
        SyncError: The live fetch failed and no usable cache was available.
    """
    target = cache_file or catalog_path()
    if not force:
        cached = load_cached_catalog(target)
        if cached is not None:
            return cached

    raw = fetch_openrouter_models(timeout_s=timeout_s, gateway=gateway)
    envelope = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": _models_url(gateway),
        "count": len(raw.get("data") or []),
        "data": raw["data"],
    }
    if persist:
        _write_cache(envelope, target)
        _LOG.info("cached %d openrouter models -> %s", envelope["count"], target)
    return catalog_from_payload(envelope, source=envelope["source"])


# --------------------------------------------------------------------------- #
# Alias health + YAML rewrite
# --------------------------------------------------------------------------- #
def inspect_aliases(settings: AiwakeSettings, catalog: OpenRouterCatalog) -> list[AliasRemap]:
    """Return remaps for every alias whose slug is absent from the catalog."""
    remaps: list[AliasRemap] = []
    for name, alias in settings.model_aliases.items():
        if alias.slug in catalog:
            continue
        successor = catalog.closest(alias.slug)
        if successor is None:
            _LOG.warning("alias %s -> %s is unroutable and no successor was found", name, alias.slug)
            continue
        remaps.append(
            AliasRemap(
                alias=name,
                old_slug=alias.slug,
                new_slug=successor,
                score=_slug_score(alias.slug, successor),
            )
        )
    return remaps


def rewrite_alias_slugs(config_path: Path, remaps: Sequence[AliasRemap]) -> int:
    """Surgically replace broken slugs in ``aiwake_config.yaml``.

    Line-based on purpose: dumping the file through PyYAML would strip every
    comment, which is where the operational notes live. Only the slug token
    itself is rewritten; parameter defaults and surrounding comments stay put.

    Returns:
        Number of aliases whose slug was actually rewritten.
    """
    if not remaps or not config_path.is_file():
        return 0

    original = config_path.read_text(encoding="utf-8-sig")
    lines = original.splitlines(keepends=True)
    pending = {item.alias: item for item in remaps}
    rewritten = 0
    index = 0

    while index < len(lines) and pending:
        line = lines[index]
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        matched_alias: str | None = None
        for alias in pending:
            if stripped.startswith(f"{alias}:"):
                matched_alias = alias
                break
        if matched_alias is None:
            index += 1
            continue

        remap = pending[matched_alias]
        newline = "\n" if line.endswith("\n") else ""
        trailing = _trailing_comment(line)
        rest = stripped[len(matched_alias) + 1 :].strip()
        if trailing:
            rest = rest[: -len(trailing)].strip() if rest.endswith(trailing.strip()) else rest.split(" #", 1)[0].strip()
        quoted = rest.strip("'\"")
        if quoted == remap.old_slug or rest in {remap.old_slug, f'"{remap.old_slug}"', f"'{remap.old_slug}'"}:
            lines[index] = f'{" " * indent}{matched_alias}: "{remap.new_slug}"{trailing}{newline}'
            del pending[matched_alias]
            rewritten += 1
            index += 1
            continue

        # Mapping form: `alias:\n    slug: "old"`
        cursor = index + 1
        while cursor < len(lines):
            inner = lines[cursor]
            inner_stripped = inner.lstrip()
            inner_indent = len(inner) - len(inner_stripped)
            if inner_stripped and not inner_stripped.startswith("#") and inner_indent <= indent:
                break
            if inner_stripped.startswith("slug:"):
                inner_nl = "\n" if inner.endswith("\n") else ""
                inner_trailing = _trailing_comment(inner)
                lines[cursor] = f'{" " * inner_indent}slug: "{remap.new_slug}"{inner_trailing}{inner_nl}'
                del pending[matched_alias]
                rewritten += 1
                break
            cursor += 1
        index += 1

    if rewritten:
        config_path.write_text("".join(lines), encoding="utf-8")
        _LOG.info("rewrote %d alias slug(s) in %s", rewritten, config_path.name)
    return rewritten


def _trailing_comment(line: str) -> str:
    """Return the `  # comment` suffix of a YAML line, including leading spaces.

    Used so a slug rewrite does not throw away the operator notes that sit on
    the same line. Values never contain `#`, so the first ` #` is unambiguous.
    """
    stripped = line.rstrip("\r\n")
    marker = stripped.find(" #")
    if marker == -1:
        return ""
    return stripped[marker:]


def remap_if_stale(spec: ModelSpec, catalog: OpenRouterCatalog | None = None) -> ModelSpec:
    """Return ``spec`` with a live slug if the authored one is retired.

    Disk-only. A missing cache is a no-op so factory construction never waits
    on the network. The 404 guard is what fetches live, once, if this misses.
    """
    if spec.provider != "openrouter":
        return spec
    snapshot = catalog if catalog is not None else load_cached_catalog()
    if snapshot is None:
        return spec
    resolved, changed = snapshot.remap(spec.model)
    if not changed:
        return spec
    _LOG.warning(
        "cached catalog: %s is unroutable; remapping to %s before the API call",
        spec.model,
        resolved,
    )
    return spec.model_copy(update={"model": resolved})


# --------------------------------------------------------------------------- #
# 404 detection (shared with the OpenRouter provider)
# --------------------------------------------------------------------------- #
def looks_like_missing_model(
    status_code: int,
    body: str = "",
    error_code: Any = None,
) -> bool:
    """True when the gateway is telling us the slug itself is the problem."""
    if status_code == 404:
        return True
    if error_code == 404 or str(error_code) == "404":
        return True
    lowered = (body or "").lower()
    return any(marker in lowered for marker in _MISSING_MARKERS)


# --------------------------------------------------------------------------- #
# CLI presentation
# --------------------------------------------------------------------------- #
def _per_million(raw: str) -> str:
    try:
        return f"${float(raw) * 1_000_000:.2f}/M"
    except (TypeError, ValueError):
        return "?"


def format_vendor_table(catalog: OpenRouterCatalog, *, per_vendor: int = _SUMMARY_PER_VENDOR) -> str:
    """ASCII table of the newest chat models for the key vendors."""
    blocks: list[str] = []
    for prefix, label in KEY_VENDORS:
        rows = catalog.by_vendor(prefix)[:per_vendor]
        blocks.append(f"{label.upper()} ({len(catalog.by_vendor(prefix))} debate models)")
        if not rows:
            blocks.append("  (none in catalog)")
            blocks.append("")
            continue
        id_width = max(len(row.id) for row in rows)
        for row in rows:
            price = f"{_per_million(row.prompt_price)} in  {_per_million(row.completion_price)} out"
            name = row.name.replace("\n", " ")[:40]
            blocks.append(f"  {row.id.ljust(id_width)}  {name.ljust(40)}  {price}")
        blocks.append("")
    return "\n".join(blocks).rstrip() + "\n"


def format_alias_health(settings: AiwakeSettings, catalog: OpenRouterCatalog, remaps: Iterable[AliasRemap]) -> str:
    """ASCII health check of every configured alias against the live catalog."""
    remap_by_alias = {item.alias: item for item in remaps}
    rows = sorted(settings.model_aliases.items())
    alias_width = max((len(name) for name, _ in rows), default=5)
    slug_width = max((len(alias.slug) for _, alias in rows), default=4)

    lines = ["ALIAS HEALTH (against live OpenRouter catalog)"]
    for name, alias in rows:
        remap = remap_by_alias.get(name)
        if alias.slug in catalog:
            status = "OK"
        elif remap is not None:
            status = f"MISSING -> {remap.new_slug}"
        else:
            status = "MISSING (no successor found)"
        lines.append(f"  {name.ljust(alias_width)}  {alias.slug.ljust(slug_width)}  {status}")
    return "\n".join(lines) + "\n"


def run_sync_cli(
    settings: AiwakeSettings,
    *,
    rewrite_yaml: bool = True,
    config_path: Path | None = None,
    cache_file: Path | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> OpenRouterCatalog:
    """Operator-facing sync: fetch, cache, print, optionally repair YAML.

    Raises:
        SyncError: The live fetch failed. The CLI turns this into exit code 1.
    """
    catalog = sync_openrouter_models(
        force=True,
        persist=True,
        timeout_s=timeout_s,
        cache_file=cache_file,
        gateway=settings.openrouter,
    )
    remaps = inspect_aliases(settings, catalog)

    print(f"\nSynced {len(catalog)} OpenRouter models -> {cache_file or catalog_path()}")
    if catalog.fetched_at:
        print(f"Fetched at {catalog.fetched_at}")
    print()
    print(format_vendor_table(catalog))
    print(format_alias_health(settings, catalog, remaps))

    if rewrite_yaml and remaps:
        written = rewrite_alias_slugs(config_path or CONFIG_PATH, remaps)
        if written:
            print(f"Updated {written} alias mapping(s) in {(config_path or CONFIG_PATH).name}.")
        else:
            print("Broken aliases detected but the YAML rewrite did not match any lines.")
    elif remaps:
        print("Broken aliases detected; YAML rewrite skipped.")
    else:
        print("All configured aliases resolve to live slugs.")
    print()
    return catalog


__all__ = [
    "AliasRemap",
    "CACHE_FILENAME",
    "ModelRecord",
    "OpenRouterCatalog",
    "SyncError",
    "catalog_from_payload",
    "catalog_path",
    "fetch_openrouter_models",
    "format_alias_health",
    "format_vendor_table",
    "inspect_aliases",
    "load_cached_catalog",
    "looks_like_missing_model",
    "match_score",
    "remap_if_stale",
    "rewrite_alias_slugs",
    "run_sync_cli",
    "sync_openrouter_models",
]
