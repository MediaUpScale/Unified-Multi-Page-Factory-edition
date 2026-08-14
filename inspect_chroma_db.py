# -*- coding: utf-8 -*-
"""
Inspect the VisualQA Chroma DB (or JSON fallback) for ancient_knowledge.

Connects to VisualQA_Agent/chroma_db regardless of VISUALQA_USE_CHROMA.
If Chroma is empty, missing, or disabled, dumps
VisualQA_Agent/channel_dna_store.json instead.

Writes chroma_dump.json and chroma_dump.md in the project root.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CHANNEL = "ancient_knowledge"
COLLECTION_NAME = "channel_aesthetics_v2"
CHROMA_DIR = _ROOT / "VisualQA_Agent" / "chroma_db"
JSON_STORE = _ROOT / "VisualQA_Agent" / "channel_dna_store.json"
OUT_JSON = _ROOT / "chroma_dump.json"
OUT_MD = _ROOT / "chroma_dump.md"
EMBED_PREVIEW = 8


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _embedding_structure(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {"present": False}
    try:
        seq = list(raw)
    except TypeError:
        return {"present": True, "type": type(raw).__name__, "repr": repr(raw)[:200]}
    if not seq:
        return {"present": True, "length": 0, "type": type(raw).__name__}
    first = seq[0]
    try:
        first_list = [float(x) for x in list(first)[:EMBED_PREVIEW]]
        dim = len(list(first))
        nested = True
    except TypeError:
        first_list = [float(x) for x in seq[:EMBED_PREVIEW]]
        dim = len(seq)
        nested = False
    return {
        "present": True,
        "nested_per_document": nested,
        "n_vectors": len(seq) if nested else 1,
        "dimensions": dim,
        "value_type": type(first).__name__,
        "preview_first_values": first_list,
        "note": "Full vectors omitted; preview is the first "
        f"{EMBED_PREVIEW} floats of the first embedding.",
    }


def _matches_channel(doc_id: str, metadata: dict[str, Any], document: str) -> bool:
    needle = CHANNEL.lower()
    if needle in (doc_id or "").lower():
        return True
    meta = metadata or {}
    for key in ("channel_name", "channel", "page_id", "channel_id"):
        if str(meta.get(key) or "").lower() == needle:
            return True
    blob = f"{json.dumps(meta, default=str)} {document or ''}".lower()
    return needle in blob


def _try_chroma() -> dict[str, Any]:
    report: dict[str, Any] = {
        "source": "chroma",
        "persist_dir": str(CHROMA_DIR),
        "dir_exists": CHROMA_DIR.is_dir(),
        "collection_name": COLLECTION_NAME,
        "ok": False,
        "reason": "",
        "collections": [],
        "records": [],
        "embedding_structure": {"present": False},
        "count_all": 0,
        "count_ancient_knowledge": 0,
    }
    if not CHROMA_DIR.exists():
        report["reason"] = "chroma_db directory does not exist"
        return report
    try:
        import chromadb
        from chromadb.config import Settings
    except Exception as exc:  # noqa: BLE001
        report["reason"] = f"chromadb import failed: {exc}"
        return report

    try:
        client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
        cols = client.list_collections()
        report["collections"] = [
            {"name": getattr(c, "name", str(c)), "metadata": getattr(c, "metadata", None)}
            for c in cols
        ]
        names = [getattr(c, "name", "") for c in cols]
        target = COLLECTION_NAME if COLLECTION_NAME in names else (names[0] if names else "")
        if not target:
            report["reason"] = "Chroma client opened but no collections exist (empty DB)"
            return report
        col = client.get_collection(name=target)
        report["collection_name"] = target
        data = col.get(include=["documents", "metadatas", "embeddings"])
        ids = list(data.get("ids") or [])
        docs = list(data.get("documents") or [])
        metas = list(data.get("metadatas") or [])
        embeds = data.get("embeddings")
        report["count_all"] = len(ids)
        report["embedding_structure"] = _embedding_structure(embeds)
        embed_rows: list[Any] = []
        if embeds is not None:
            try:
                embed_rows = list(embeds)
            except TypeError:
                embed_rows = []
        records = []
        for i, doc_id in enumerate(ids):
            meta = metas[i] if i < len(metas) else {}
            document = docs[i] if i < len(docs) else ""
            if not _matches_channel(str(doc_id), dict(meta or {}), str(document or "")):
                continue
            rec: dict[str, Any] = {
                "id": doc_id,
                "metadata": dict(meta or {}),
                "document": document,
            }
            if i < len(embed_rows):
                rec["embedding_preview"] = _embedding_structure([embed_rows[i]])
            records.append(rec)
        report["records"] = records
        report["count_ancient_knowledge"] = len(records)
        if not records:
            report["reason"] = (
                f"Collection '{target}' has {len(ids)} document(s) but none match "
                f"{CHANNEL!r}"
            )
            report["ok"] = len(ids) > 0
        else:
            report["ok"] = True
            report["reason"] = ""
        return report
    except Exception as exc:  # noqa: BLE001
        report["reason"] = f"Chroma read failed: {exc}"
        return report


def _json_fallback(chroma_report: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source": "json_fallback",
        "path": str(JSON_STORE),
        "exists": JSON_STORE.is_file(),
        "ok": False,
        "reason": "",
        "ancient_knowledge": None,
        "all_channel_keys": [],
        "chroma_status": {
            "ok": chroma_report.get("ok"),
            "reason": chroma_report.get("reason"),
            "dir_exists": chroma_report.get("dir_exists"),
            "count_all": chroma_report.get("count_all"),
        },
    }
    if not JSON_STORE.is_file():
        payload["reason"] = "channel_dna_store.json is missing"
        return payload
    try:
        store = json.loads(JSON_STORE.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        payload["reason"] = f"JSON store unreadable: {exc}"
        return payload
    if not isinstance(store, dict):
        payload["reason"] = "JSON store is not an object"
        return payload
    payload["all_channel_keys"] = sorted(store.keys())
    ak = store.get(CHANNEL)
    if ak is None:
        payload["reason"] = f"{CHANNEL!r} key not present in JSON store"
        return payload
    payload["ancient_knowledge"] = ak
    payload["ok"] = True
    return payload


def _to_markdown(dump: dict[str, Any]) -> str:
    lines = [
        f"# Chroma / VisualQA dump — `{CHANNEL}`",
        "",
        f"Generated: {dump.get('generated_at', '')}",
        f"Active source: **{dump.get('active_source', '')}**",
        "",
        "## Chroma attempt",
        "",
        f"- Persist dir: `{dump['chroma'].get('persist_dir')}`",
        f"- Directory exists: `{dump['chroma'].get('dir_exists')}`",
        f"- Collection: `{dump['chroma'].get('collection_name')}`",
        f"- Collections found: `{dump['chroma'].get('collections')}`",
        f"- Documents in collection: `{dump['chroma'].get('count_all')}`",
        f"- `{CHANNEL}` matches: `{dump['chroma'].get('count_ancient_knowledge')}`",
        f"- Status: {dump['chroma'].get('reason') or ('ok' if dump['chroma'].get('ok') else 'empty / unavailable')}",
        "",
        "### Embedding structure",
        "",
        "```json",
        json.dumps(dump["chroma"].get("embedding_structure") or {}, indent=2, ensure_ascii=False),
        "```",
        "",
    ]
    records = dump["chroma"].get("records") or []
    if records:
        lines.append("### Matching documents")
        lines.append("")
        for i, rec in enumerate(records, 1):
            lines.append(f"#### Record {i} — `{rec.get('id')}`")
            lines.append("")
            lines.append("Metadata:")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(rec.get("metadata") or {}, indent=2, ensure_ascii=False))
            lines.append("```")
            lines.append("")
            lines.append("Document:")
            lines.append("")
            lines.append("```")
            lines.append(str(rec.get("document") or ""))
            lines.append("```")
            if rec.get("embedding_preview"):
                lines.append("")
                lines.append("Embedding preview:")
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(rec["embedding_preview"], indent=2, ensure_ascii=False))
                lines.append("```")
            lines.append("")
    else:
        lines.append("No Chroma documents matched `ancient_knowledge`.")
        lines.append("")

    fb = dump.get("json_fallback") or {}
    lines.extend(
        [
            "## JSON store (VisualQA_Agent/channel_dna_store.json)",
            "",
            f"- Path: `{fb.get('path')}`",
            f"- Exists: `{fb.get('exists')}`",
            f"- Channel keys: `{fb.get('all_channel_keys')}`",
            f"- Status: {fb.get('reason') or ('ok' if fb.get('ok') else 'not used')}",
            "",
        ]
    )
    if fb.get("ancient_knowledge") is not None:
        lines.append("### `ancient_knowledge` DNA")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(fb["ancient_knowledge"], indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    chroma = _try_chroma()
    json_fb = _json_fallback(chroma)
    chroma_has_ak = bool(chroma.get("ok") and chroma.get("count_ancient_knowledge"))
    if chroma_has_ak:
        active = "chroma"
    elif json_fb.get("ok"):
        active = "json_fallback"
    elif chroma.get("ok"):
        active = "chroma_empty_for_channel"
    else:
        active = "unavailable"

    dump = {
        "generated_at": _now(),
        "channel": CHANNEL,
        "active_source": active,
        "chroma": chroma,
        "json_fallback": json_fb,
    }
    OUT_JSON.write_text(json.dumps(dump, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    OUT_MD.write_text(_to_markdown(dump), encoding="utf-8")
    print(f"active_source={active}")
    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_MD}")
    if chroma.get("reason"):
        print(f"chroma: {chroma['reason']}")
    if not chroma_has_ak and json_fb.get("ok"):
        print("Chroma empty/disabled — dumped channel_dna_store.json for ancient_knowledge")
    return 0 if active in {"chroma", "json_fallback"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
