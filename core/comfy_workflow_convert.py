# -*- coding: utf-8 -*-
"""
Convert ComfyUI UI-format workflow JSON → API ``/prompt`` format.

The files in ``infra/runpod/workflows/`` are often saved from the web UI
(``nodes`` / ``links`` / ``widgets_values``), including subgraph wrappers.
The ``/prompt`` endpoint requires the flat API dict::

    { "node_id": { "class_type": "...", "inputs": {...} }, ... }

This module performs that conversion, including subgraph expansion to
``{parent}:{inner}`` node IDs (matching ComfyUI's own export convention).
"""
from __future__ import annotations

import copy
import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)

# Frontend-only seed companion widget values (not present in API inputs)
_CONTROL_AFTER_GENERATE = frozenset(
    {
        "fixed",
        "randomize",
        "increment",
        "decrement",
        "increment for each queue",
        "decrement for each queue",
    }
)

_SKIP_NODE_TYPES = frozenset(
    {
        "Note",
        "MarkdownNote",
        "Reroute",
        "PrimitiveNode",  # rare; values usually wired via PrimitiveInt/Float
    }
)

# Widget-only / UI-upload slots that should not be sent to /prompt
_SKIP_INPUT_TYPES = frozenset(
    {
        "AUDIO_UI",
        "IMAGEUPLOAD",
        "AUDIOUPLOAD",
        "VIDEOUPLOAD",
    }
)


def is_api_format(data: Mapping[str, Any]) -> bool:
    """True when *data* already looks like a ComfyUI API prompt dict."""
    if not data or "nodes" in data:
        return False
    sample = [
        v for k, v in data.items()
        if isinstance(v, dict) and not str(k).startswith("_")
    ]
    if not sample:
        return False
    return all("class_type" in v for v in sample)


def convert_ui_to_api(ui_workflow: Mapping[str, Any]) -> dict[str, Any]:
    """
    Convert a UI workflow (possibly with ``definitions.subgraphs``) to API format.

    Returns a deep-copied API prompt dict ready for ``POST /prompt``.
    """
    if is_api_format(ui_workflow):
        return copy.deepcopy(dict(ui_workflow))

    if "nodes" not in ui_workflow:
        raise ValueError(
            "Workflow is neither API-format nor UI-format (missing 'nodes' and "
            "class_type entries)."
        )

    subgraphs = _index_subgraphs(ui_workflow)
    nodes = list(ui_workflow.get("nodes") or [])
    links = list(ui_workflow.get("links") or [])

    # Expand subgraph wrapper nodes first (mutates working copies)
    nodes, links = _expand_all_subgraphs(nodes, links, subgraphs)

    link_by_id = _index_links(links)
    prompt: dict[str, Any] = {}

    for node in nodes:
        if not isinstance(node, dict):
            continue
        ntype = str(node.get("type") or "")
        if ntype in _SKIP_NODE_TYPES:
            continue
        # mode 2/4 = muted / never in some builds — skip disabled
        if int(node.get("mode") or 0) in (2, 4):
            continue
        # Skip unresolved subgraph wrappers (should have been expanded)
        if ntype in subgraphs:
            logger.warning(
                "Subgraph node %s (%s) was not expanded — skipping",
                node.get("id"), ntype,
            )
            continue

        nid = str(node["id"])
        prompt[nid] = _node_to_api(node, link_by_id)

    if not prompt:
        raise ValueError("UI→API conversion produced an empty prompt graph")
    logger.info("Converted UI workflow → API format (%d nodes)", len(prompt))
    return prompt


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _index_subgraphs(ui_workflow: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    defs = ui_workflow.get("definitions") or {}
    out: dict[str, dict[str, Any]] = {}
    for sg in defs.get("subgraphs") or []:
        if isinstance(sg, dict) and sg.get("id"):
            out[str(sg["id"])] = sg
    return out


def _index_links(links: list[Any]) -> dict[int, tuple[Any, int, Any, int]]:
    """link_id → (origin_id, origin_slot, target_id, target_slot)."""
    by_id: dict[int, tuple[Any, int, Any, int]] = {}
    for link in links:
        if isinstance(link, (list, tuple)) and len(link) >= 5:
            lid, oid, oslot, tid, tslot = link[0], link[1], link[2], link[3], link[4]
            by_id[int(lid)] = (oid, int(oslot), tid, int(tslot))
        elif isinstance(link, dict):
            lid = int(link["id"])
            by_id[lid] = (
                link["origin_id"],
                int(link["origin_slot"]),
                link["target_id"],
                int(link["target_slot"]),
            )
    return by_id


def _links_as_list(links: list[Any]) -> list[list[Any]]:
    out: list[list[Any]] = []
    for link in links:
        if isinstance(link, (list, tuple)) and len(link) >= 5:
            out.append(list(link))
        elif isinstance(link, dict):
            out.append(
                [
                    link["id"],
                    link["origin_id"],
                    link["origin_slot"],
                    link["target_id"],
                    link["target_slot"],
                    link.get("type"),
                ]
            )
    return out


def _expand_all_subgraphs(
    nodes: list[dict[str, Any]],
    links: list[Any],
    subgraphs: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[Any]]:
    """Replace subgraph wrapper nodes with their expanded inner graphs."""
    if not subgraphs:
        return nodes, links

    working_nodes = [copy.deepcopy(n) for n in nodes if isinstance(n, dict)]
    working_links = _links_as_list(links)
    # Repeat until no wrapper nodes remain (nested subgraphs)
    for _ in range(8):
        wrapper = next(
            (
                n for n in working_nodes
                if str(n.get("type") or "") in subgraphs
            ),
            None,
        )
        if wrapper is None:
            break
        working_nodes, working_links = _expand_one_subgraph(
            working_nodes, working_links, wrapper, subgraphs[str(wrapper["type"])]
        )
    return working_nodes, working_links


def _expand_one_subgraph(
    nodes: list[dict[str, Any]],
    links: list[list[Any]],
    wrapper: dict[str, Any],
    subgraph: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[list[Any]]]:
    """
    Expand one subgraph wrapper into ``{wrapper_id}:{inner_id}`` nodes.

    Outer widget values / inbound links feed the subgraph input node (-10).
    Outbound parent links are retargeted from the subgraph output node (-20).
    """
    wid = wrapper["id"]
    prefix = str(wid)
    sg_nodes = [copy.deepcopy(n) for n in (subgraph.get("nodes") or [])]
    sg_links_raw = subgraph.get("links") or []
    # Subgraph links may be dicts
    sg_links: list[list[Any]] = []
    for link in sg_links_raw:
        if isinstance(link, dict):
            sg_links.append(
                [
                    link["id"],
                    link["origin_id"],
                    link["origin_slot"],
                    link["target_id"],
                    link["target_slot"],
                    link.get("type"),
                ]
            )
        elif isinstance(link, (list, tuple)):
            sg_links.append(list(link))

    # Map subgraph input slot index → value (from wrapper widgets / inbound links)
    input_values = _resolve_subgraph_input_values(wrapper, links, subgraph)

    # Map: subgraph output slot → (inner_origin_id, inner_origin_slot)
    output_map: dict[int, tuple[Any, int]] = {}
    for link in sg_links:
        _lid, oid, oslot, tid, tslot = link[0], link[1], link[2], link[3], link[4]
        if tid == -20:
            output_map[int(tslot)] = (oid, int(oslot))

    # Rewrite + materialize inner nodes
    new_inner: list[dict[str, Any]] = []
    for n in sg_nodes:
        n = copy.deepcopy(n)
        n["id"] = f"{prefix}:{n['id']}"
        # Rewrite input links that stay inside the subgraph
        for inp in n.get("inputs") or []:
            if not isinstance(inp, dict) or inp.get("link") is None:
                continue
            # Will be remapped globally below via link rewrite
        new_inner.append(n)

    # Build new link list:
    # 1) Drop parent links that targeted / originated from the wrapper
    # 2) Add rewritten inner links
    # 3) Bridge parent→subgraph-input and subgraph-output→parent
    next_link_id = max((int(l[0]) for l in links), default=0) + 1
    kept_links: list[list[Any]] = []
    # Parent links INTO wrapper → will become bridges into inner targets of -10
    inbound: dict[int, tuple[Any, int]] = {}  # wrapper_input_slot → (origin, oslot)
    outbound: list[list[Any]] = []  # parent links FROM wrapper

    for link in links:
        lid, oid, oslot, tid, tslot = link[0], link[1], link[2], link[3], link[4]
        if tid == wid:
            inbound[int(tslot)] = (oid, int(oslot))
            continue
        if oid == wid:
            outbound.append(link)
            continue
        kept_links.append(link)

    # Inner links (rewrite ids; convert -10 sources into concrete values on nodes)
    for link in sg_links:
        lid, oid, oslot, tid, tslot = link[0], link[1], link[2], link[3], link[4]
        if oid == -10:
            # Value injection — clear the link on the target input and set widget/value
            _inject_input_value(new_inner, f"{prefix}:{tid}", int(tslot), input_values.get(int(oslot)))
            continue
        if tid == -20:
            continue  # handled via output_map / outbound rewrite
        new_lid = next_link_id
        next_link_id += 1
        kept_links.append(
            [
                new_lid,
                f"{prefix}:{oid}",
                int(oslot),
                f"{prefix}:{tid}",
                int(tslot),
                link[5] if len(link) > 5 else None,
            ]
        )
        # Update target node's input.link to new_lid
        _set_input_link(new_inner, f"{prefix}:{tid}", int(tslot), new_lid)

    # Bridge: parent inbound links that override widget defaults
    for slot, (oid, oslot) in inbound.items():
        # Find which inner node/input is fed by subgraph input slot
        for link in sg_links:
            if link[1] == -10 and int(link[2]) == int(slot):
                tid, tslot = link[3], int(link[4])
                new_lid = next_link_id
                next_link_id += 1
                kept_links.append([new_lid, oid, oslot, f"{prefix}:{tid}", tslot, link[5] if len(link) > 5 else None])
                _set_input_link(new_inner, f"{prefix}:{tid}", tslot, new_lid)
                break

    # Bridge: parent outbound from wrapper outputs
    for link in outbound:
        lid, oid, oslot, tid, tslot = link[0], link[1], link[2], link[3], link[4]
        mapped = output_map.get(int(oslot))
        if not mapped:
            logger.warning("No subgraph output mapping for wrapper %s slot %s", wid, oslot)
            continue
        inner_oid, inner_oslot = mapped
        new_lid = next_link_id
        next_link_id += 1
        kept_links.append(
            [new_lid, f"{prefix}:{inner_oid}", inner_oslot, tid, int(tslot), link[5] if len(link) > 5 else None]
        )
        # Update parent target node's link id
        for n in nodes:
            if n.get("id") == tid:
                inputs = n.get("inputs") or []
                if 0 <= int(tslot) < len(inputs) and isinstance(inputs[int(tslot)], dict):
                    inputs[int(tslot)]["link"] = new_lid

    # Replace wrapper with inner nodes
    out_nodes = [n for n in nodes if n.get("id") != wid] + new_inner
    return out_nodes, kept_links


def _resolve_subgraph_input_values(
    wrapper: dict[str, Any],
    parent_links: list[list[Any]],
    subgraph: dict[str, Any],
) -> dict[int, Any]:
    """Map subgraph input-slot index → concrete value from wrapper widgets."""
    values: dict[int, Any] = {}
    widgets = list(wrapper.get("widgets_values") or [])
    # Wrapper inputs that are widgets, in order
    w_idx = 0
    slot = 0
    for inp in wrapper.get("inputs") or []:
        if not isinstance(inp, dict):
            slot += 1
            continue
        if inp.get("link") is not None:
            # linked from parent — value resolved later via inbound bridge
            values[slot] = None
        elif "widget" in inp or inp.get("type") in (
            "STRING", "INT", "FLOAT", "BOOLEAN", "COMBO",
        ):
            if w_idx < len(widgets):
                values[slot] = widgets[w_idx]
                w_idx += 1
        slot += 1
    return values


def _inject_input_value(
    nodes: list[dict[str, Any]],
    node_id: str,
    input_slot: int,
    value: Any,
) -> None:
    if value is None:
        return
    for n in nodes:
        if str(n.get("id")) != str(node_id):
            continue
        inputs = n.get("inputs") or []
        if not (0 <= input_slot < len(inputs)):
            return
        inp = inputs[input_slot]
        if not isinstance(inp, dict):
            return
        # Clear link — becomes a widget value
        inp["link"] = None
        # Ensure widgets_values can hold it: find widget index among widget inputs
        name = inp.get("name")
        # Store on a side channel consumed by _node_to_api
        forced = n.setdefault("_forced_inputs", {})
        forced[name] = value
        return


def _set_input_link(
    nodes: list[dict[str, Any]],
    node_id: str,
    input_slot: int,
    link_id: int,
) -> None:
    for n in nodes:
        if str(n.get("id")) != str(node_id):
            continue
        inputs = n.get("inputs") or []
        if 0 <= input_slot < len(inputs) and isinstance(inputs[input_slot], dict):
            inputs[input_slot]["link"] = link_id
        return


def _node_to_api(
    node: dict[str, Any],
    link_by_id: dict[int, tuple[Any, int, Any, int]],
) -> dict[str, Any]:
    class_type = str(node.get("type") or "")
    api_inputs: dict[str, Any] = {}
    widgets = list(node.get("widgets_values") or [])
    w_idx = 0
    forced = node.get("_forced_inputs") or {}

    for inp in node.get("inputs") or []:
        if not isinstance(inp, dict):
            continue
        name = str(inp.get("name") or "")
        itype = str(inp.get("type") or "")

        if name in forced:
            api_inputs[name] = forced[name]
            # Still advance widget cursor if this input had a widget slot
            if "widget" in inp and w_idx < len(widgets):
                # skip matching widget value
                w_idx = _advance_widget_cursor(widgets, w_idx, name)
            continue

        if itype in _SKIP_INPUT_TYPES or name in ("audioUI", "upload"):
            # Consume matching null/placeholder widget slots when present
            if w_idx < len(widgets):
                w_idx += 1
            continue

        link_id = inp.get("link")
        if link_id is not None:
            meta = link_by_id.get(int(link_id))
            if meta is not None:
                origin_id, origin_slot, _tid, _tslot = meta
                api_inputs[name] = [str(origin_id), int(origin_slot)]
            # ComfyUI keeps default widget values for linked inputs in
            # widgets_values — advance the cursor so later widgets don't shift
            # (e.g. KSamplerAdvanced steps/cfg linked → sampler_name must stay
            # "euler", not the leftover steps integer).
            if "widget" in inp and w_idx < len(widgets):
                w_idx = _advance_widget_cursor(widgets, w_idx, name)
            continue

        # Widget-backed input
        if "widget" in inp or itype in ("STRING", "INT", "FLOAT", "BOOLEAN", "COMBO"):
            if w_idx >= len(widgets):
                continue
            # Skip control_after_generate companions after seed-like widgets
            if name in ("seed", "noise_seed") and w_idx + 1 < len(widgets):
                api_inputs[name] = widgets[w_idx]
                w_idx += 1
                if (
                    w_idx < len(widgets)
                    and isinstance(widgets[w_idx], str)
                    and widgets[w_idx] in _CONTROL_AFTER_GENERATE
                ):
                    w_idx += 1
                continue
            # PrimitiveInt often: [value, "fixed"]
            if class_type == "PrimitiveInt" and name == "value":
                api_inputs[name] = widgets[w_idx]
                w_idx += 1
                if (
                    w_idx < len(widgets)
                    and isinstance(widgets[w_idx], str)
                    and widgets[w_idx] in _CONTROL_AFTER_GENERATE
                ):
                    w_idx += 1
                continue
            api_inputs[name] = widgets[w_idx]
            w_idx += 1

    entry: dict[str, Any] = {"class_type": class_type, "inputs": api_inputs}
    # Preserve UI title for class_type + title_contains resolution
    title = node.get("title")
    if title:
        entry["_meta"] = {"title": str(title)}
    return entry


def _advance_widget_cursor(widgets: list[Any], w_idx: int, name: str) -> int:
    if w_idx >= len(widgets):
        return w_idx
    w_idx += 1
    if name in ("seed", "noise_seed") and w_idx < len(widgets):
        if isinstance(widgets[w_idx], str) and widgets[w_idx] in _CONTROL_AFTER_GENERATE:
            w_idx += 1
    return w_idx
