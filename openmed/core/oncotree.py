"""Cancer generalisation through a bundled OncoTree snapshot (mt.3 E11).

The snapshot ``openmed/data/oncotree/<ONCOTREE_VERSION>.json`` is a trimmed
copy of the OncoTree tumour-type tree (code, name, main type, tissue, level,
parent). OncoTree (cBioPortal / Memorial Sloan Kettering, see source) is
licensed CC-BY-4.0; see ``NOTICE`` and :func:`oncotree_meta`.

Only cancer is generalised. Matching is exact after normalisation (NFKC,
casefold, ``-``/``–`` to a space, collapsed whitespace, surrounding
punctuation stripped) against node names, then main types; a code match
(``LUAD``) is only allowed when the caller asks for it and the surface is
upper-case alphanumeric. There is no fuzzy or substring matching, so non-
cancer diagnoses (``asthma``) never resolve.

The rendering rule is one level up, capped at the main-type level:

* level >= 3 renders its level-2 ancestor's name;
* level 2 renders its level-1 tissue name;
* a level-1 tissue renders its own name (unchanged);
* a main-type hit renders the main type's tissue.

A surface shared by several nodes (``Choriocarcinoma`` exists under three
tissues) is generalised only when every candidate renders the same text;
otherwise :func:`generalise_cancer` returns ``None`` so the caller falls
back to a tag (fail closed).

The snapshot is read with :mod:`importlib.resources`; this module never
touches the network. ``scripts/fork/refresh_oncotree.py`` refreshes the
snapshot and is never imported here.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources
from types import MappingProxyType
from typing import Final

__all__ = [
    "BENIGN_NAMES",
    "NON_MALIGNANT_NAMES",
    "ONCOTREE_VERSION",
    "OncoNode",
    "generalise_cancer",
    "is_cancer_match",
    "load_oncotree",
    "lookup",
    "oncotree_meta",
]

ONCOTREE_VERSION: Final = "oncotree_2025_10_03"
_SNAPSHOT: Final = f"data/oncotree/{ONCOTREE_VERSION}.json"
_DASH_RE: Final = re.compile(r"[-‐‑‒–]")
_CODE_RE: Final = re.compile(r"(?=[A-Z0-9]*[A-Z])[A-Z0-9]+")

# Benign OncoTree entities (verbatim snapshot names). They are tumours, not
# cancer, so a ``DISEASE``/``CONDITION`` span naming one is never generalised.
BENIGN_NAMES: Final[frozenset[str]] = frozenset(
    {
        "Adrenocortical Adenoma",
        "Atypical Nevus",
        "Benign Phyllodes Tumor of the Breast",
        "Brenner Tumor, Benign",
        "Choroid Plexus Papilloma",
        "Congenital Nevus",
        "Dermatofibroma",
        "Fibroadenoma",
        "Ganglioneuroma",
        "Hemangioma",
        "Hepatocellular Adenoma",
        "Hyalinizing Trabecular Adenoma of the Thyroid",
        "Inverted Urothelial Papilloma",
        "Leiomyoma",
        "Lymphoid Benign",
        "Mature Teratoma",
        "Meningioma",
        "Myeloid Benign",
        "Myofibroma",
        "Myofibromatosis",
        "Myxoma",
        "Neurofibroma",
        "Oncocytic Adenoma of the Thyroid",
        "Ovarian Seromucinous Adenoma",
        "Pituitary Adenoma",
        "Pleomorphic Adenoma",
        "Renal Angiomyolipoma",
        "Renal Oncocytoma",
        "Salivary Gland Oncocytoma",
        "Schwannoma",
        "Serous Cystadenoma of the Pancreas",
        "Spiroma/Spiradenoma",
        "Tubular Adenoma of the Colon",
        "Urothelial Papilloma",
        "Uterine Leiomyoma",
    }
)
# Non-diagnostic or non-malignant leaves: MGUS isotypes read as lab
# analytes, and amyloidosis is mostly non-malignant.
NON_MALIGNANT_NAMES: Final[frozenset[str]] = frozenset(
    {"IgA", "IgG", "IgM", "Amyloidosis"}
)
_CANCER_LABEL: Final = "CANCER"
_DIAGNOSIS_LABELS: Final = frozenset({"DISEASE", "CONDITION"})


@dataclass(frozen=True)
class OncoNode:
    """One OncoTree tumour type.

    Attributes:
        code: The OncoTree code, e.g. ``LUAD``.
        name: The verbatim OncoTree name.
        main_type: The node's main type, or ``None`` for the root.
        tissue: The level-1 tissue name, or ``None`` for the root.
        level: Depth in the tree; the root ``TISSUE`` is level 0.
        parent: The parent node's code, or ``None`` for the root.
    """

    code: str
    name: str
    main_type: str | None
    tissue: str | None
    level: int
    parent: str | None


def _normalise(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    value = _DASH_RE.sub(" ", value)
    value = " ".join(value.split())
    start, end = 0, len(value)
    while start < end and _is_edge_punct(value[start]):
        start += 1
    while end > start and _is_edge_punct(value[end - 1]):
        end -= 1
    return " ".join(value[start:end].split())


def _is_edge_punct(char: str) -> bool:
    return char.isspace() or unicodedata.category(char).startswith("P")


@cache
def _snapshot() -> Mapping[str, object]:
    raw = resources.files("openmed").joinpath(_SNAPSHOT).read_text(encoding="utf-8")
    return json.loads(raw)


@cache
def load_oncotree() -> Mapping[str, OncoNode]:
    """Load the bundled OncoTree snapshot.

    Returns:
        A read-only mapping from OncoTree code to :class:`OncoNode`, cached
        for the life of the process.
    """
    nodes = _snapshot()["nodes"]
    tree = {
        item["code"]: OncoNode(
            code=item["code"],
            name=item["name"],
            main_type=item["main_type"],
            tissue=item["tissue"],
            level=int(item["level"]),
            parent=item["parent"],
        )
        for item in nodes  # type: ignore[union-attr]
    }
    return MappingProxyType(tree)


@cache
def oncotree_meta() -> Mapping[str, str]:
    """Return the snapshot's provenance header.

    Returns:
        A read-only mapping with ``source_url``, ``version``, ``retrieved``,
        ``licence`` (``CC-BY-4.0``) and ``attribution``.
    """
    return MappingProxyType(dict(_snapshot()["meta"]))  # type: ignore[arg-type]


def _ranked(nodes: list[OncoNode]) -> tuple[OncoNode, ...]:
    return tuple(sorted(nodes, key=lambda node: (node.level, node.code)))


@cache
def _indexes() -> tuple[
    Mapping[str, tuple[OncoNode, ...]], Mapping[str, tuple[OncoNode, ...]]
]:
    by_name: dict[str, list[OncoNode]] = {}
    by_main_type: dict[str, list[OncoNode]] = {}
    for node in load_oncotree().values():
        if node.level < 1:
            continue
        by_name.setdefault(_normalise(node.name), []).append(node)
        if node.main_type:
            by_main_type.setdefault(_normalise(node.main_type), []).append(node)
    return (
        MappingProxyType({k: _ranked(v) for k, v in by_name.items()}),
        MappingProxyType({k: _ranked(v) for k, v in by_main_type.items()}),
    )


def _code_hit(text: str) -> OncoNode | None:
    surface = text.strip()
    while surface and unicodedata.category(surface[0]).startswith("P"):
        surface = surface[1:]
    while surface and unicodedata.category(surface[-1]).startswith("P"):
        surface = surface[:-1]
    if not _CODE_RE.fullmatch(surface):
        return None
    node = load_oncotree().get(surface)
    return node if node is not None and node.level >= 1 else None


def _resolve(text: str, allow_code: bool) -> tuple[str, tuple[OncoNode, ...]] | None:
    key = _normalise(text)
    if not key:
        return None
    by_name, by_main_type = _indexes()
    if key in by_name:
        return "name", by_name[key]
    if key in by_main_type:
        return "main_type", by_main_type[key]
    if allow_code:
        node = _code_hit(text)
        if node is not None:
            return "name", (node,)
    return None


def lookup(text: str, *, allow_code: bool) -> OncoNode | None:
    """Find the OncoTree node a surface names exactly.

    Args:
        text: The diagnosis surface, e.g. ``Lung Adenocarcinoma``.
        allow_code: Whether an upper-case alphanumeric OncoTree code
            (``LUAD``) may match. Callers pass ``True`` only for the
            ``CANCER`` label.

    Returns:
        The matched node (the shallowest, then lowest code, when several
        share the surface), or ``None`` when the text is not in OncoTree.
    """
    resolved = _resolve(text, allow_code)
    return None if resolved is None else resolved[1][0]


def _ancestor_at(node: OncoNode, level: int) -> OncoNode:
    tree = load_oncotree()
    current = node
    while current.level > level and current.parent is not None:
        current = tree[current.parent]
    return current


def _render(node: OncoNode, kind: str) -> str | None:
    if kind == "main_type":
        return node.tissue
    if node.level >= 3:
        return _ancestor_at(node, 2).name
    if node.level == 2:
        return _ancestor_at(node, 1).name
    return node.name


@cache
def _excluded_keys() -> frozenset[str]:
    return frozenset(_normalise(name) for name in BENIGN_NAMES | NON_MALIGNANT_NAMES)


def is_cancer_match(surface: str, label: str) -> bool:
    """Decide whether a diagnosis span is a cancer cluster (the E11 gate).

    This is the gate Task 4 (``/v1/apply`` under ``generalise``) must call;
    it must not reach into :func:`lookup` directly.

    * ``CANCER``: any OncoTree hit counts (name, main type, or an upper-case
      code such as ``LUAD``).
    * ``DISEASE``/``CONDITION``: only a name hit at level 2 or deeper, or a
      main-type hit. Level-1 tissue names (``Skin``, ``Lung``, ``Other``),
      codes, :data:`NON_MALIGNANT_NAMES` (``IgA``, ``IgG``, ``IgM``,
      ``Amyloidosis``) and :data:`BENIGN_NAMES` (``Leiomyoma``...) never
      count, because only cancer is generalised.
    * Any other label: never.

    A ``True`` result does not guarantee a rendering: when
    :func:`generalise_cancer` returns ``None`` for a matching span (the
    ambiguity fallback, e.g. ``Choriocarcinoma`` under several tissues),
    the caller must render a ``mark`` tag, never ``keep``.

    Args:
        surface: The span text.
        label: The span's canonical label.

    Returns:
        ``True`` when the span is a cancer cluster.
    """
    label = label.upper()
    if label == _CANCER_LABEL:
        return _resolve(surface, True) is not None
    if label not in _DIAGNOSIS_LABELS:
        return False
    if _normalise(surface) in _excluded_keys():
        return False
    resolved = _resolve(surface, False)
    if resolved is None:
        return False
    kind, nodes = resolved
    return kind == "main_type" or all(node.level >= 2 for node in nodes)


def generalise_cancer(text: str, *, allow_code: bool) -> str | None:
    """Generalise a cancer surface one OncoTree level up, capped at main type.

    Args:
        text: The diagnosis surface.
        allow_code: Whether an OncoTree code surface may match (``CANCER``
            label only).

    Returns:
        The verbatim OncoTree name of the generalised group, or ``None``
        when the text is unmapped or its candidates disagree (the caller
        then falls back to a tag).
    """
    resolved = _resolve(text, allow_code)
    if resolved is None:
        return None
    kind, nodes = resolved
    rendered = {_render(node, kind) for node in nodes}
    if len(rendered) != 1:
        return None
    (value,) = rendered
    return value
