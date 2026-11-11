"""OncoTree snapshot and cancer generalisation (mt.3 E11).

OncoTree names are terminology, not PHI, so they appear verbatim here.
Expected outputs are read from the bundled snapshot rather than hard-coded,
so a snapshot refresh only changes data, not tests.
"""

from __future__ import annotations

import importlib.util
import json
import re
import socket
import sys
from pathlib import Path

import pytest

from openmed.core import oncotree
from openmed.core.oncotree import (
    BENIGN_NAMES,
    ONCOTREE_VERSION,
    OncoNode,
    generalise_cancer,
    is_cancer_match,
    load_oncotree,
    lookup,
    oncotree_meta,
)

ROOT = Path(__file__).resolve().parents[3]
REFRESH_SCRIPT = ROOT / "scripts" / "fork" / "refresh_oncotree.py"


def _ancestor_at_level(node: OncoNode, level: int) -> OncoNode:
    tree = load_oncotree()
    current = node
    while current.level > level:
        assert current.parent is not None
        current = tree[current.parent]
    return current


def _node_named(name: str) -> OncoNode:
    matches = [node for node in load_oncotree().values() if node.name == name]
    assert len(matches) == 1, name
    return matches[0]


def test_subtype_maps_to_parent() -> None:
    """A level-3 subtype renders its level-2 (main type level) ancestor."""
    node = _node_named("Lung Adenocarcinoma")
    assert node.level >= 3
    expected = _ancestor_at_level(node, 2).name
    assert expected == "Non-Small Cell Lung Cancer"
    assert generalise_cancer("Lung Adenocarcinoma", allow_code=False) == expected


def test_rare_fusion_defined_subtype_maps_up() -> None:
    """NUT carcinoma (NUTM1-rearranged), the user's rare-case example.

    The pinned snapshot names it ``NUT Carcinoma of the Lung`` (code
    ``NUTCL``), a child of ``Non-Small Cell Lung Cancer``; per E11 a level-3
    node renders its level-2 ancestor.
    """
    node = _node_named("NUT Carcinoma of the Lung")
    expected = (
        _ancestor_at_level(node, 2).name
        if node.level >= 3
        else _ancestor_at_level(node, 1).name
    )
    assert generalise_cancer("NUT carcinoma of the lung", allow_code=False) == expected
    assert expected != node.name


def test_level2_maps_to_tissue() -> None:
    node = _node_named("Non-Small Cell Lung Cancer")
    assert node.level == 2
    tissue = _ancestor_at_level(node, 1).name
    assert generalise_cancer("Non-Small Cell Lung Cancer", allow_code=False) == tissue


def test_tissue_unchanged() -> None:
    tissues = [node for node in load_oncotree().values() if node.level == 1]
    assert tissues
    for node in tissues:
        assert generalise_cancer(node.name, allow_code=False) == node.name


def test_main_type_maps_to_tissue() -> None:
    """A main type that is not itself a node name renders its tissue."""
    tree = load_oncotree()
    names = {oncotree._normalise(node.name) for node in tree.values()}
    candidates = sorted(
        (node for node in tree.values() if node.main_type),
        key=lambda node: (node.level, node.code),
    )
    hit = next(
        node for node in candidates if oncotree._normalise(node.main_type) not in names
    )
    assert generalise_cancer(hit.main_type, allow_code=False) == hit.tissue


def test_case_and_whitespace_insensitive() -> None:
    expected = generalise_cancer("Lung Adenocarcinoma", allow_code=False)
    assert expected is not None
    for variant in (
        "lung adenocarcinoma",
        "  LUNG   ADENOCARCINOMA ",
        "Lung\tAdenocarcinoma.",
        "(lung adenocarcinoma)",
    ):
        assert generalise_cancer(variant, allow_code=False) == expected
    nsclc = generalise_cancer("Non-Small Cell Lung Cancer", allow_code=False)
    assert generalise_cancer("non small cell lung cancer", allow_code=False) == nsclc
    assert generalise_cancer("Non–Small Cell Lung Cancer", allow_code=False) == (nsclc)


def test_no_substring_or_fuzzy_match() -> None:
    assert lookup("Lung Adenocarcinom", allow_code=False) is None
    assert lookup("left lung adenocarcinoma", allow_code=False) is None


def test_code_only_when_allowed() -> None:
    assert lookup("LUAD", allow_code=False) is None
    assert generalise_cancer("LUAD", allow_code=False) is None
    luad = lookup("LUAD", allow_code=True)
    assert luad is not None and luad.code == "LUAD"
    expected = _ancestor_at_level(luad, 2).name
    assert generalise_cancer("LUAD", allow_code=True) == expected
    # Codes must be all upper-case alphanumeric surfaces.
    assert lookup("luad", allow_code=True) is None
    assert lookup("Luad", allow_code=True) is None


def test_unmapped_returns_none() -> None:
    assert lookup("Stage IV mystery tumour", allow_code=True) is None
    assert generalise_cancer("Stage IV mystery tumour", allow_code=True) is None
    assert generalise_cancer("", allow_code=True) is None
    assert generalise_cancer("   ", allow_code=False) is None


def test_non_cancer_not_found() -> None:
    for text in ("Type 2 diabetes mellitus", "asthma"):
        assert lookup(text, allow_code=False) is None
        assert generalise_cancer(text, allow_code=False) is None


def test_root_is_not_matchable() -> None:
    assert lookup("Tissue", allow_code=False) is None
    assert lookup("TISSUE", allow_code=True) is None


def test_snapshot_meta() -> None:
    meta = oncotree_meta()
    assert meta["licence"] == "CC-BY-4.0"
    assert meta["version"] == ONCOTREE_VERSION
    assert meta["source_url"].startswith(
        "https://oncotree.mskcc.org/api/tumorTypes/tree"
    )
    assert ONCOTREE_VERSION in meta["source_url"]
    assert meta["retrieved"]
    assert "OncoTree" in meta["attribution"]
    with pytest.raises(TypeError):
        meta["licence"] = "other"  # type: ignore[index]

    tree = load_oncotree()
    assert len(tree) > 100
    roots = [node for node in tree.values() if node.parent is None]
    assert len(roots) == 1
    for node in tree.values():
        seen: set[str] = set()
        current = node
        while current.parent is not None:
            assert current.code not in seen
            seen.add(current.code)
            parent = tree[current.parent]
            assert parent.level == current.level - 1
            current = parent
        assert current is roots[0]


def test_tree_is_read_only_and_cached() -> None:
    tree = load_oncotree()
    assert load_oncotree() is tree
    with pytest.raises(TypeError):
        tree["X"] = tree["LUAD"]  # type: ignore[index]
    with pytest.raises(AttributeError):
        tree["LUAD"].name = "x"  # type: ignore[misc]


def _clear_caches() -> None:
    for fn in (load_oncotree, oncotree_meta, oncotree._snapshot, oncotree._indexes):
        fn.cache_clear()


def test_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    _clear_caches()
    try:
        assert load_oncotree()
        assert oncotree_meta()["version"] == ONCOTREE_VERSION
        assert generalise_cancer("Lung Adenocarcinoma", allow_code=False)
    finally:
        _clear_caches()


def test_openmed_never_imports_refresh_script() -> None:
    pattern = re.compile(r"^\s*(?:from|import)\s[^\n]*refresh_oncotree", re.M)
    for path in (ROOT / "openmed").rglob("*.py"):
        assert not pattern.search(path.read_text(encoding="utf-8")), path


def _load_refresh_module():
    spec = importlib.util.spec_from_file_location("refresh_oncotree", REFRESH_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


_CANNED = {
    "TISSUE": {
        "code": "TISSUE",
        "name": "Tissue",
        "mainType": None,
        "tissue": None,
        "parent": None,
        "level": 0,
        "color": "Black",
        "history": [],
        "children": {
            "ZZ": {
                "code": "ZZ",
                "name": "Zed Tissue",
                "mainType": None,
                "tissue": "Zed Tissue",
                "parent": "TISSUE",
                "level": 1,
                "externalReferences": {"UMLS": ["C0000000"]},
                "children": {
                    "ZZMT": {
                        "code": "ZZMT",
                        "name": "Zed Main Type",
                        "mainType": "Zed Main Type",
                        "tissue": "Zed Tissue",
                        "parent": "ZZ",
                        "level": 2,
                        "children": {
                            "ZZB": {
                                "code": "ZZB",
                                "name": "Zed Subtype B",
                                "mainType": "Zed Main Type",
                                "tissue": "Zed Tissue",
                                "parent": "ZZMT",
                                "level": 3,
                                "children": {},
                            },
                            "ZZA": {
                                "code": "ZZA",
                                "name": "Zed Subtype A",
                                "mainType": "Zed Main Type",
                                "tissue": "Zed Tissue",
                                "parent": "ZZMT",
                                "level": 3,
                                "children": {},
                            },
                        },
                    }
                },
            },
            "AA": {
                "code": "AA",
                "name": "Aye Tissue",
                "mainType": None,
                "tissue": "Aye Tissue",
                "parent": "TISSUE",
                "level": 1,
                "children": {},
            },
        },
    }
}


def test_refresh_trim_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", _blocked)
    module = _load_refresh_module()
    trimmed = module.trim_tree(_CANNED)
    nodes = trimmed["nodes"]
    assert [node["code"] for node in nodes] == [
        "AA",
        "TISSUE",
        "ZZ",
        "ZZA",
        "ZZB",
        "ZZMT",
    ]
    fields = {"code", "name", "main_type", "tissue", "level", "parent"}
    assert all(set(node) == fields for node in nodes)
    assert nodes[3] == {
        "code": "ZZA",
        "name": "Zed Subtype A",
        "main_type": "Zed Main Type",
        "tissue": "Zed Tissue",
        "level": 3,
        "parent": "ZZMT",
    }
    # Deterministic: same input, same bytes, regardless of dict order.
    reordered = json.loads(json.dumps(_CANNED))
    children = reordered["TISSUE"]["children"]
    reordered["TISSUE"]["children"] = dict(reversed(list(children.items())))
    assert json.dumps(module.trim_tree(reordered)) == json.dumps(trimmed)


def test_refresh_render_snapshot_round_trips() -> None:
    module = _load_refresh_module()
    meta = {
        "source_url": "https://oncotree.mskcc.org/api/tumorTypes/tree?version=v1",
        "version": "v1",
        "retrieved": "2026-01-01",
        "licence": "CC-BY-4.0",
        "attribution": "OncoTree",
    }
    text = module.render_snapshot(meta, module.trim_tree(_CANNED))
    assert text.endswith("\n")
    data = json.loads(text)
    assert data["meta"] == meta
    assert len(data["nodes"]) == 6
    assert module.render_snapshot(meta, module.trim_tree(_CANNED)) == text


def test_ambiguous_surface_fails_closed() -> None:
    """``Choriocarcinoma`` is a node under several tissues with different
    level-2 ancestors, so it is recognised as cancer but not generalised;
    the caller then renders a tag (fail closed)."""
    tree = load_oncotree()
    hits = [node for node in tree.values() if node.name == "Choriocarcinoma"]
    assert len({_ancestor_at_level(node, 2).name for node in hits}) > 1
    assert lookup("choriocarcinoma", allow_code=False) is not None
    assert generalise_cancer("choriocarcinoma", allow_code=False) is None


def test_snapshot_resolves_through_importlib_resources() -> None:
    """Packaging guard: the snapshot is package data under ``openmed``."""
    from importlib import resources

    resource = resources.files("openmed").joinpath(
        f"data/oncotree/{ONCOTREE_VERSION}.json"
    )
    assert resource.is_file()
    assert json.loads(resource.read_text(encoding="utf-8"))["meta"]["version"] == (
        ONCOTREE_VERSION
    )


@pytest.mark.parametrize(
    "surface",
    [
        "Lung Adenocarcinoma",
        "Non-Small Cell Lung Cancer",
        "Lung Cancer",
        "Colorectal Cancer",
        "Melanoma",
        "Choriocarcinoma",
        "NUT Carcinoma of the Lung",
    ],
)
@pytest.mark.parametrize("label", ["CANCER", "DISEASE", "CONDITION"])
def test_is_cancer_match_accepts_cancers(surface: str, label: str) -> None:
    assert is_cancer_match(surface, label) is True


@pytest.mark.parametrize(
    "surface",
    ["Skin", "Bone", "Lung", "Other", "Liver", "Kidney", "Eye", "Breast", "Myeloid"],
)
def test_is_cancer_match_rejects_tissues_unless_cancer(surface: str) -> None:
    assert lookup(surface, allow_code=False) is not None
    assert is_cancer_match(surface, "DISEASE") is False
    assert is_cancer_match(surface, "CONDITION") is False
    assert is_cancer_match(surface, "CANCER") is True


@pytest.mark.parametrize("surface", ["IgA", "IgG", "IgM", "Amyloidosis", "igg"])
def test_is_cancer_match_deny_list(surface: str) -> None:
    assert lookup(surface, allow_code=False) is not None
    assert is_cancer_match(surface, "DISEASE") is False
    assert is_cancer_match(surface, "CONDITION") is False
    assert is_cancer_match(surface, "CANCER") is True


def test_benign_names_are_snapshot_entities() -> None:
    """Every curated benign name is an OncoTree node name in the pinned
    snapshot, so the list cannot drift silently on a refresh."""
    names = {oncotree._normalise(node.name) for node in load_oncotree().values()}
    assert BENIGN_NAMES
    for name in BENIGN_NAMES:
        assert oncotree._normalise(name) in names, name


@pytest.mark.parametrize(
    "surface",
    [
        "Leiomyoma",
        "Hemangioma",
        "Dermatofibroma",
        "Fibroadenoma",
        "Schwannoma",
        "Meningioma",
        "Pituitary Adenoma",
        "Neurofibroma",
        "Mature Teratoma",
        "Adrenocortical Adenoma",
        "Tubular Adenoma of the Colon",
    ],
)
def test_is_cancer_match_rejects_benign_unless_cancer(surface: str) -> None:
    assert surface in BENIGN_NAMES
    assert lookup(surface, allow_code=False) is not None
    assert is_cancer_match(surface, "DISEASE") is False
    assert is_cancer_match(surface, "CONDITION") is False
    assert is_cancer_match(surface, "CANCER") is True


def test_is_cancer_match_codes_only_for_cancer_label() -> None:
    assert is_cancer_match("LUAD", "CANCER") is True
    assert is_cancer_match("LUAD", "DISEASE") is False
    assert is_cancer_match("BRCA", "DISEASE") is False


@pytest.mark.parametrize(
    ("surface", "label"),
    [
        ("asthma", "DISEASE"),
        ("Type 2 diabetes mellitus", "CONDITION"),
        ("Stage IV mystery tumour", "CANCER"),
        ("Lung Adenocarcinoma", "PERSON"),
        ("Lung Adenocarcinoma", "ORGANIZATION"),
        ("", "CANCER"),
    ],
)
def test_is_cancer_match_rejects_non_cancer(surface: str, label: str) -> None:
    assert is_cancer_match(surface, label) is False


def test_snapshot_attribution_matches_refresh_script() -> None:
    module = _load_refresh_module()
    assert oncotree_meta()["attribution"] == module.ATTRIBUTION
    assert oncotree_meta()["licence"] == module.LICENCE
