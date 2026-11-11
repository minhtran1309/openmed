#!/usr/bin/env python3
"""Refresh the bundled OncoTree snapshot (maintainer tool, network required).

Fetches ``https://oncotree.mskcc.org/api/tumorTypes/tree?version=<v>`` with
the standard library, trims every node to ``code``, ``name``, ``main_type``,
``tissue``, ``level`` and ``parent``, and writes deterministic JSON to
``openmed/data/oncotree/<v>.json`` (OncoTree version identifiers
already start with ``oncotree_``, e.g. ``oncotree_2025_10_03.json``).

An alias such as ``oncotree_latest_stable`` is first resolved through
``/api/versions`` to the dated version with the same release date, so the
snapshot always pins an exact version string.

OncoTree is licensed CC-BY-4.0 (https://github.com/cBioPortal/oncotree).
After a refresh, update ``ONCOTREE_VERSION`` in ``openmed/core/oncotree.py``
and the OncoTree paragraph in ``NOTICE``, and review the diff.

This script is never imported by ``openmed``; runtime code reads only the
bundled snapshot and never calls the network.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

API_BASE = "https://oncotree.mskcc.org/api"
LICENCE = "CC-BY-4.0"
ATTRIBUTION = (
    "OncoTree, cBioPortal / Memorial Sloan Kettering (see source: "
    "https://oncotree.mskcc.org, https://github.com/cBioPortal/oncotree), "
    "licensed under the Creative Commons Attribution 4.0 International "
    "License (https://creativecommons.org/licenses/by/4.0/). Trimmed to "
    "code/name/main type/tissue/level/parent."
)
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[2] / "openmed" / "data" / "oncotree"
_FIELDS = ("code", "name", "main_type", "tissue", "level", "parent")
_TIMEOUT_S = 60


def _walk(node: Mapping[str, Any], out: dict[str, dict[str, Any]]) -> None:
    code = node["code"]
    if code in out:
        raise ValueError(f"duplicate OncoTree code: {code}")
    out[code] = {
        "code": code,
        "name": node.get("name"),
        "main_type": node.get("mainType"),
        "tissue": node.get("tissue"),
        "level": int(node["level"]),
        "parent": node.get("parent") or None,
    }
    for child in (node.get("children") or {}).values():
        _walk(child, out)


def trim_tree(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Trim an OncoTree ``/tumorTypes/tree`` payload to the bundled fields.

    Args:
        payload: The decoded API response: a mapping from the root code
            (``TISSUE``) to a nested node with ``children`` mappings.

    Returns:
        ``{"nodes": [...]}`` with one dict per node holding exactly
        ``code``, ``name``, ``main_type``, ``tissue``, ``level`` and
        ``parent``, sorted by code so the output is deterministic.
    """
    collected: dict[str, dict[str, Any]] = {}
    for root in payload.values():
        _walk(root, collected)
    return {"nodes": [collected[code] for code in sorted(collected)]}


def render_snapshot(meta: Mapping[str, str], trimmed: Mapping[str, Any]) -> str:
    """Render the snapshot as stable JSON with one node per line.

    Args:
        meta: The ``meta`` header (source URL, version, retrieval date,
            licence and attribution).
        trimmed: The output of :func:`trim_tree`.

    Returns:
        The JSON document text, ending with a newline.
    """
    head = json.dumps(dict(meta), sort_keys=True, ensure_ascii=False, indent=2)
    head = head.replace("\n", "\n  ")
    lines = [
        "  " + json.dumps({k: node[k] for k in _FIELDS}, ensure_ascii=False)
        for node in trimmed["nodes"]
    ]
    return (
        '{\n  "meta": '
        + head
        + ',\n  "nodes": [\n'
        + ",\n".join("  " + line for line in lines)
        + "\n  ]\n}\n"
    )


def _get_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:  # noqa: S310
        return json.load(response)


def resolve_version(version: str) -> str:
    """Resolve an OncoTree alias to the dated version it currently points at.

    Args:
        version: A dated ``api_identifier`` or an alias such as
            ``oncotree_latest_stable``.

    Returns:
        The dated ``api_identifier`` with the same release date.
    """
    versions = _get_json(f"{API_BASE}/versions")
    by_id = {entry["api_identifier"]: entry for entry in versions}
    if version not in by_id:
        raise SystemExit(f"unknown OncoTree version: {version}")
    if not version.startswith("oncotree_latest") and version != "oncotree_development":
        return version
    released = by_id[version].get("release_date")
    dated = sorted(
        entry["api_identifier"]
        for entry in versions
        if entry.get("release_date") == released
        and entry["api_identifier"] != version
        and entry["api_identifier"][len("oncotree_") :][:1].isdigit()
    )
    if len(dated) != 1:
        raise SystemExit(f"cannot resolve {version} to one dated version: {dated}")
    return dated[0]


def main(argv: list[str] | None = None) -> int:
    """Fetch, trim and write the OncoTree snapshot.

    Args:
        argv: Command-line arguments (``--version``, ``--out-dir``).

    Returns:
        Process exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", default="oncotree_latest_stable")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args(argv)

    version = resolve_version(args.version)
    query = urllib.parse.urlencode({"version": version})
    source_url = f"{API_BASE}/tumorTypes/tree?{query}"
    trimmed = trim_tree(_get_json(source_url))
    meta = {
        "source_url": source_url,
        "version": version,
        "retrieved": dt.datetime.now(dt.timezone.utc).date().isoformat(),
        "licence": LICENCE,
        "attribution": ATTRIBUTION,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / f"{version}.json"
    out.write_text(render_snapshot(meta, trimmed), encoding="utf-8")
    print(f"{version}: {len(trimmed['nodes'])} nodes -> {out}")
    if args.version != version:
        print(f"resolved {args.version} -> {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
