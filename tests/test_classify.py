"""Tests for Stage 8 deterministic layer classification."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from gebal_cad_normalizer.cad import (
    CanonicalEntity,
    CanonicalGeometryResult,
    CanonicalLine,
    CanonicalPoint,
    CanonicalPolyline,
    CanonicalPolylineVertex,
    DxfInventoryResult,
    EntityTypeCount,
    LayerClassificationConfig,
    LayerClassificationOverride,
    LayerInventory,
    classify_layers,
    write_classification_json,
)


def _count(dxf_type: str, count: int) -> EntityTypeCount:
    categories = {
        "LINE": "known_supported",
        "LWPOLYLINE": "known_supported",
        "POLYLINE": "known_supported",
        "ARC": "known_supported",
        "CIRCLE": "known_supported",
        "POINT": "known_supported",
        "DIMENSION": "ignored_non_operational",
        "TEXT": "ignored_non_operational",
        "MTEXT": "ignored_non_operational",
        "HATCH": "convertible_later",
        "INSERT": "convertible_later",
        "ACAD_PROXY_ENTITY": "review_required",
        "PDFUNDERLAY": "review_required",
    }
    return EntityTypeCount(dxf_type=dxf_type, count=count, category=categories.get(dxf_type, "unsupported"))


def _layer(name: str, count: int, *, on: bool = True, frozen: bool = False, plot: bool = True) -> LayerInventory:
    return LayerInventory(name=name, color=7, linetype="CONTINUOUS", is_on=on, is_frozen=frozen, is_locked=False, is_plottable=plot, entity_count=count)


def _inventory(
    layers: list[LayerInventory],
    by_layer: dict[str, tuple[EntityTypeCount, ...]],
    *,
    units: str = "mm",
    insunits: int | None = 4,
    xrefs: tuple[str, ...] = (),
) -> DxfInventoryResult:
    global_counts: dict[str, int] = {}
    for counts in by_layer.values():
        for item in counts:
            global_counts[item.dxf_type] = global_counts.get(item.dxf_type, 0) + item.count
    return DxfInventoryResult(
        source_path=Path("fixture.dxf"),
        source_sha256="abc123",
        dxf_version="AC1024",
        insunits=insunits,
        drawing_units=units,
        modelspace_present=True,
        paperspace_present=False,
        layers=tuple(layers),
        blocks=(),
        entity_counts=tuple(_count(name, count) for name, count in sorted(global_counts.items())),
        entity_counts_by_layer=by_layer,
        nested_entity_counts=(),
        modelspace_entity_count=sum(layer.entity_count for layer in layers),
        total_entity_count=sum(layer.entity_count for layer in layers),
        modelspace_extents={"min_x": 0.0, "min_y": 0.0, "min_z": 0.0, "max_x": 10.0, "max_y": 10.0, "max_z": 0.0},
        layouts=("Model",),
        xref_indicators=xrefs,
        text_style_dimension_hatch_usage={"text": 0, "styles": 0, "dimensions": 0, "hatches": 0},
        flagged_entity_presence={},
        has_nonzero_z_geometry=False,
        has_3d_geometry=False,
        unresolved_block_references=(),
        issues=(),
    )


def _poly(layer: str, *, closed: bool = True, order: int = 0) -> CanonicalEntity:
    return CanonicalEntity(
        order_key=(order,),
        source_handle=str(order),
        original_dxf_type="LWPOLYLINE",
        canonical_type="polyline",
        status="canonicalized",
        layer=layer,
        color=None,
        linetype=None,
        is_closed=closed,
        geometry=CanonicalPolyline(
            vertices=(
                CanonicalPolylineVertex(point=CanonicalPoint(x=0, y=0)),
                CanonicalPolylineVertex(point=CanonicalPoint(x=10, y=0)),
                CanonicalPolylineVertex(point=CanonicalPoint(x=10, y=5)),
                CanonicalPolylineVertex(point=CanonicalPoint(x=0, y=5)),
            ),
            is_closed=closed,
        ),
    )


def _line(layer: str, *, z: float = 0.0, order: int = 0) -> CanonicalEntity:
    return CanonicalEntity(
        order_key=(order,),
        source_handle=str(order),
        original_dxf_type="LINE",
        canonical_type="line",
        status="canonicalized",
        layer=layer,
        color=None,
        linetype=None,
        is_closed=False,
        z_values=(z, z) if z else (),
        geometry=CanonicalLine(start=CanonicalPoint(x=0, y=0, z=z), end=CanonicalPoint(x=1, y=0, z=z)),
    )


def _canonical(entities: list[CanonicalEntity], *, units: str = "mm", insunits: int | None = 4) -> CanonicalGeometryResult:
    return CanonicalGeometryResult(
        source_path=None,
        source_sha256=None,
        source_identity="fixture",
        dxf_version="AC1024",
        insunits=insunits,
        drawing_units=units,
        total_source_entities_visited=len(entities),
        total_canonical_entities=len(entities),
        counts_by_original_dxf_type={},
        counts_by_canonical_type={},
        counts_by_status={},
        canonical_extents=None,
        issues=(),
        entities=tuple(entities),
        tessellation_enabled=False,
        tessellation_tolerance=None,
    )


def _classify_one(name: str, counts: tuple[EntityTypeCount, ...], entities: list[CanonicalEntity] | None = None, **kwargs):
    inventory = _inventory([_layer(name, sum(item.count for item in counts), on=kwargs.pop("on", True), frozen=kwargs.pop("frozen", False), plot=kwargs.pop("plot", True))], {name: counts}, **kwargs)
    canonical = _canonical(entities or [])
    return classify_layers(inventory, canonical).layers[0]


def test_obvious_product_layer() -> None:
    layer = _classify_one("PRODUCT_FOOTPRINT", (_count("LWPOLYLINE", 1),), [_poly("PRODUCT_FOOTPRINT")])
    assert layer.assigned_role == "product_geometry"
    assert layer.confidence >= 0.8


def test_safety_fall_impact_area_layer() -> None:
    layer = _classify_one("FALL_IMPACT_AREA", (_count("LWPOLYLINE", 1),), [_poly("FALL_IMPACT_AREA")])
    assert layer.assigned_role == "safety_zone"


def test_dimensions_layer() -> None:
    layer = _classify_one("DIMENSIONS", (_count("DIMENSION", 3),))
    assert layer.assigned_role == "dimensions"


def test_text_only_layer() -> None:
    layer = _classify_one("NOTES", (_count("TEXT", 2), _count("MTEXT", 1)))
    assert layer.assigned_role == "text_annotation"


def test_hatch_only_layer() -> None:
    layer = _classify_one("SURFACE_HATCH", (_count("HATCH", 2),))
    assert layer.assigned_role == "hatch_or_fill"


def test_foundation_install_layer() -> None:
    layer = _classify_one("INSTALL_ANCHORS", (_count("LWPOLYLINE", 1),), [_poly("INSTALL_ANCHORS")])
    assert layer.assigned_role == "foundation_or_installation"


def test_xref_proxy_layer() -> None:
    layer = _classify_one("XREF_VENDOR", (_count("ACAD_PROXY_ENTITY", 1),), xrefs=("XREF_VENDOR:path.dwg",))
    assert layer.assigned_role == "external_or_proxy"
    assert {issue.code for issue in layer.issues} == {"external_reference_content", "proxy_content"}


def test_hidden_non_plot_reference_layer() -> None:
    layer = _classify_one("REFERENCE_GEOMETRY", (_count("LINE", 1),), [_line("REFERENCE_GEOMETRY")], on=False, plot=False)
    assert layer.assigned_role == "construction_or_reference"


def test_mixed_content_layer_becomes_review_required() -> None:
    layer = _classify_one("PRODUCT_WITH_NOTES", (_count("LWPOLYLINE", 1), _count("TEXT", 1)), [_poly("PRODUCT_WITH_NOTES")])
    assert layer.assigned_role == "review_required"
    assert "mixed_operational_content" in {issue.code for issue in layer.issues}


def test_contradictory_name_vs_geometry() -> None:
    layer = _classify_one("SAFETY_ZONE", (_count("DIMENSION", 2),))
    assert layer.assigned_role == "review_required"
    assert "conflicting_evidence" in {issue.code for issue in layer.issues}


def test_unknown_units_preserved_as_issue() -> None:
    inventory = _inventory([_layer("PRODUCT", 1)], {"PRODUCT": (_count("LWPOLYLINE", 1),)}, units="unknown", insunits=999)
    result = classify_layers(inventory, _canonical([_poly("PRODUCT")]))
    assert "unknown_units" in {issue.code for issue in result.issues}
    assert result.drawing_units == "unknown"


def test_deterministic_ordering_and_serialization(tmp_path: Path) -> None:
    inventory = _inventory(
        [_layer("Z_TEXT", 1), _layer("A_PRODUCT", 1)],
        {"Z_TEXT": (_count("TEXT", 1),), "A_PRODUCT": (_count("LWPOLYLINE", 1),)},
    )
    canonical = _canonical([_poly("A_PRODUCT")])
    first = classify_layers(inventory, canonical)
    second = classify_layers(inventory, canonical)
    path = write_classification_json(first, tmp_path / "classification.json")

    assert [layer.original_layer_name for layer in first.layers] == ["A_PRODUCT", "Z_TEXT"]
    assert first.to_deterministic_json() == second.to_deterministic_json()
    assert json.loads(path.read_text(encoding="utf-8")) == json.loads(first.to_deterministic_json())


def test_source_inventory_and_canonical_inputs_not_mutated() -> None:
    inventory = _inventory([_layer("PRODUCT", 1)], {"PRODUCT": (_count("LWPOLYLINE", 1),)})
    canonical = _canonical([_poly("PRODUCT")])
    before_inventory = copy.deepcopy(inventory.model_dump(mode="json"))
    before_canonical = copy.deepcopy(canonical.model_dump(mode="json"))

    classify_layers(inventory, canonical)

    assert inventory.model_dump(mode="json") == before_inventory
    assert canonical.model_dump(mode="json") == before_canonical


def test_configurable_vendor_override() -> None:
    inventory = _inventory([_layer("VENDOR_SAFE_BOUNDARY", 1)], {"VENDOR_SAFE_BOUNDARY": (_count("LWPOLYLINE", 1),)})
    canonical = _canonical([_poly("VENDOR_SAFE_BOUNDARY")])
    config = LayerClassificationConfig(
        vendor="acme",
        vendor_overrides=(LayerClassificationOverride(vendor="acme", pattern="SAFE_BOUNDARY", role="safety_zone", weight=0.9, reason="acme safe boundary"),),
    )
    result = classify_layers(inventory, canonical, config)
    assert result.layers[0].assigned_role == "safety_zone"
    assert any(match.source == "vendor_override" for match in result.layers[0].matched_rules)


def test_low_confidence_ambiguity() -> None:
    layer = _classify_one("MISC", (_count("LINE", 1),), [_line("MISC")])
    assert layer.assigned_role == "ambiguous"
    assert "insufficient_evidence" in {issue.code for issue in layer.issues}



def _bluestone_config() -> LayerClassificationConfig:
    return LayerClassificationConfig(vendor_profile="bluestone_playground")


def _classify_one_with_config(name: str, counts: tuple[EntityTypeCount, ...], entities: list[CanonicalEntity] | None = None, config: LayerClassificationConfig | None = None):
    inventory = _inventory([_layer(name, sum(item.count for item in counts))], {name: counts})
    canonical = _canonical(entities or [])
    return classify_layers(inventory, canonical, config).layers[0]


def test_bluestone_primary_aliases_map_to_expected_roles() -> None:
    expected = {
        "Lg_prod": "product_geometry",
        "Lg_area": "safety_zone",
        "Lg_falling": "safety_zone",
        "LCPROD_FALLINGSPACE": "safety_zone",
        "LCPROD_ENSAFETYREGION": "safety_zone",
        "Lg_dim": "dimensions",
        "DIMENSION": "dimensions",
        "Lg_txt": "text_annotation",
        "Lg_boundary": "construction_or_reference",
        "lc_ground": "foundation_or_installation",
        "Defpoints": "non_operational",
        "ASHADE": "hatch_or_fill",
    }
    content = {
        "product_geometry": ((_count("LWPOLYLINE", 1),), [_poly("placeholder")]),
        "safety_zone": ((_count("LWPOLYLINE", 1),), [_poly("placeholder")]),
        "dimensions": ((_count("DIMENSION", 1),), []),
        "text_annotation": ((_count("TEXT", 1),), []),
        "construction_or_reference": ((_count("LINE", 1),), [_line("placeholder")]),
        "foundation_or_installation": ((_count("LWPOLYLINE", 1),), [_poly("placeholder")]),
        "non_operational": tuple(),
        "hatch_or_fill": ((_count("HATCH", 1),), []),
    }
    for alias, role in expected.items():
        value = content[role]
        if value:
            counts, entities = value
            entities = [entity.model_copy(update={"layer": alias}) for entity in entities]
        else:
            counts, entities = (), []
        layer = _classify_one_with_config(alias, counts, entities, _bluestone_config())
        assert layer.assigned_role == role
        assert layer.evidence.vendor_profile_name == "bluestone_playground"
        assert layer.evidence.matched_vendor_alias is not None
        assert any(match.source == "vendor_alias" for match in layer.matched_rules)


def test_bluestone_aliases_are_case_insensitive() -> None:
    layer = _classify_one_with_config("lg_PROD", (_count("LWPOLYLINE", 1),), [_poly("lg_PROD")], _bluestone_config())
    assert layer.assigned_role == "product_geometry"
    assert layer.evidence.matched_vendor_alias is not None


def test_bluestone_alias_plus_matching_geometry_gives_high_confidence() -> None:
    layer = _classify_one_with_config("Lg_prod", (_count("LWPOLYLINE", 1),), [_poly("Lg_prod")], _bluestone_config())
    assert layer.assigned_role == "product_geometry"
    assert layer.confidence >= 0.9


def test_bluestone_alias_alone_gives_moderate_confidence() -> None:
    layer = _classify_one_with_config("Lg_prod", (_count("LWPOLYLINE", 1),), [], _bluestone_config())
    assert layer.assigned_role == "product_geometry"
    assert 0.62 <= layer.confidence < 0.8


def test_bluestone_contradictory_geometry_becomes_review_required() -> None:
    layer = _classify_one_with_config("Lg_prod", (_count("DIMENSION", 2),), [], _bluestone_config())
    assert layer.assigned_role == "review_required"
    assert "vendor_alias_conflict" in {issue.code for issue in layer.issues}
    assert layer.review_reason == "conflicting layer-name and geometry evidence"


def test_bluestone_mixed_content_remains_review_required() -> None:
    layer = _classify_one_with_config("Lg_prod", (_count("LWPOLYLINE", 1), _count("TEXT", 1)), [_poly("Lg_prod")], _bluestone_config())
    assert layer.assigned_role == "review_required"
    assert "mixed_operational_content" in {issue.code for issue in layer.issues}


def test_generic_mode_without_vendor_profile_remains_unchanged_for_vendor_alias() -> None:
    layer = _classify_one_with_config("Lg_prod", (_count("LINE", 1),), [_line("Lg_prod")], None)
    assert layer.assigned_role == "ambiguous"
    assert layer.evidence.vendor_profile_name is None
    assert layer.evidence.matched_vendor_alias is None
    assert not any(match.source == "vendor_alias" for match in layer.matched_rules)


def test_unknown_vendor_profile_fails_clearly() -> None:
    inventory = _inventory([_layer("Lg_prod", 0)], {"Lg_prod": ()})
    try:
        classify_layers(inventory, _canonical([]), LayerClassificationConfig(vendor_profile="unknown_profile"))
    except ValueError as exc:
        assert "Unknown vendor layer profile: unknown_profile" in str(exc)
        assert "bluestone_playground" in str(exc)
    else:
        raise AssertionError("unknown vendor profile did not fail")


def test_bluestone_vendor_profile_serialization_is_deterministic() -> None:
    inventory = _inventory([_layer("Lg_prod", 1), _layer("ASHADE", 1)], {"Lg_prod": (_count("LWPOLYLINE", 1),), "ASHADE": (_count("HATCH", 1),)})
    canonical = _canonical([_poly("Lg_prod")])
    config = _bluestone_config()
    first = classify_layers(inventory, canonical, config)
    second = classify_layers(inventory, canonical, config)
    assert first.to_deterministic_json() == second.to_deterministic_json()


def test_bluestone_vendor_profile_does_not_mutate_source_inputs() -> None:
    inventory = _inventory([_layer("Lg_prod", 1)], {"Lg_prod": (_count("LWPOLYLINE", 1),)})
    canonical = _canonical([_poly("Lg_prod")])
    config = _bluestone_config()
    before_inventory = copy.deepcopy(inventory.model_dump(mode="json"))
    before_canonical = copy.deepcopy(canonical.model_dump(mode="json"))
    before_config = copy.deepcopy(config.model_dump(mode="json"))

    classify_layers(inventory, canonical, config)

    assert inventory.model_dump(mode="json") == before_inventory
    assert canonical.model_dump(mode="json") == before_canonical
    assert config.model_dump(mode="json") == before_config


def test_bluestone_proxy_evidence_retains_priority_over_alias() -> None:
    layer = _classify_one_with_config("Lg_prod", (_count("ACAD_PROXY_ENTITY", 1),), [], _bluestone_config())
    assert layer.assigned_role == "external_or_proxy"
    assert "proxy_content" in {issue.code for issue in layer.issues}
