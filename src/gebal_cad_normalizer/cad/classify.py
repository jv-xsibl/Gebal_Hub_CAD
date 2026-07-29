"""Stage 8 deterministic layer and content classification."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from gebal_cad_normalizer.cad.canonicalize import CanonicalEntity, CanonicalGeometryResult
from gebal_cad_normalizer.cad.inventory import DxfInventoryResult, EntityTypeCount, LayerInventory
from gebal_cad_normalizer.exceptions import OutputWriteError
from gebal_cad_normalizer.models import StrictModel


ClassificationRole = Literal[
    "product_geometry",
    "safety_zone",
    "foundation_or_installation",
    "dimensions",
    "text_annotation",
    "hatch_or_fill",
    "construction_or_reference",
    "external_or_proxy",
    "non_operational",
    "ambiguous",
    "review_required",
]
IssueSeverity = Literal["info", "warning", "fail"]


class ClassificationIssueCode(str, Enum):
    """Stable Stage 8 issue codes."""

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    UNKNOWN_UNITS = "unknown_units"
    EXTERNAL_REFERENCE_CONTENT = "external_reference_content"
    PROXY_CONTENT = "proxy_content"
    MIXED_OPERATIONAL_CONTENT = "mixed_operational_content"
    VENDOR_ALIAS_CONFLICT = "vendor_alias_conflict"
    CLASSIFICATION_RULE_ERROR = "classification_rule_error"


class ClassificationIssue(StrictModel):
    """Machine-readable classification issue."""

    code: str
    severity: IssueSeverity
    message: str
    layer_name: str | None = None
    evidence: dict[str, Any] = {}


class ClassificationRuleMatch(StrictModel):
    """One deterministic rule contribution."""

    rule_id: str
    role: ClassificationRole
    weight: float
    evidence: str
    source: Literal["layer_name", "entity_types", "layer_state", "geometry", "block_ancestry", "inventory_issue", "vendor_override", "vendor_alias"]


class ClassificationEvidence(StrictModel):
    """Preserved evidence used by classification rules."""

    normalized_layer_name: str
    vendor_profile_name: str | None = None
    matched_vendor_alias: str | None = None
    entity_counts: dict[str, int]
    layer_color: int | None
    layer_linetype: str | None
    is_visible: bool
    is_plottable: bool
    is_locked: bool
    closed_entity_count: int = 0
    open_entity_count: int = 0
    canonical_entity_count: int = 0
    has_text: bool = False
    has_dimensions: bool = False
    has_hatch: bool = False
    has_operational_geometry: bool = False
    has_external_reference: bool = False
    has_proxy: bool = False
    has_nonzero_z: bool = False
    block_ancestry: tuple[str, ...] = ()
    relative_area_ratio: float | None = None


class LayerClassification(StrictModel):
    """Advisory classification for one original source layer."""

    original_layer_name: str
    assigned_role: ClassificationRole
    confidence: float = Field(ge=0.0, le=1.0)
    matched_rules: tuple[ClassificationRuleMatch, ...]
    positive_evidence: tuple[str, ...]
    negative_evidence: tuple[str, ...]
    alternative_roles: tuple[ClassificationRole, ...]
    entity_counts: dict[str, int]
    review_reason: str | None = None
    evidence: ClassificationEvidence
    issues: tuple[ClassificationIssue, ...] = ()


class LayerClassificationOverride(StrictModel):
    """Vendor-isolated layer classification override."""

    pattern: str
    role: ClassificationRole
    weight: float = Field(default=0.7, ge=0.0, le=1.0)
    vendor: str | None = None
    reason: str = "vendor override"


class VendorLayerAlias(StrictModel):
    """Exact vendor layer alias contribution scoped to one vendor profile."""

    alias: str
    role: ClassificationRole
    weight: float = Field(default=0.66, ge=0.0, le=1.0)
    reason: str


class VendorLayerProfile(StrictModel):
    """Isolated profile for recurring vendor layer aliases."""

    name: str
    aliases: tuple[VendorLayerAlias, ...]


class LayerClassificationConfig(StrictModel):
    """Small deterministic Stage 8 rule configuration."""

    vendor: str | None = None
    vendor_profile: str | None = None
    confidence_threshold: float = Field(default=0.62, ge=0.0, le=1.0)
    review_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    name_weight: float = Field(default=0.42, ge=0.0, le=1.0)
    geometry_weight: float = Field(default=0.45, ge=0.0, le=1.0)
    pure_content_weight: float = Field(default=0.88, ge=0.0, le=1.0)
    vendor_overrides: tuple[LayerClassificationOverride, ...] = ()


class ClassificationResult(StrictModel):
    """Complete Stage 8 layer classification result."""

    source_identity: str
    source_sha256: str | None
    drawing_units: str
    insunits: int | None
    layer_count: int
    role_counts: dict[str, int]
    layers: tuple[LayerClassification, ...]
    issues: tuple[ClassificationIssue, ...]
    config: LayerClassificationConfig

    def to_deterministic_json(self) -> str:
        """Serialize deterministically for reports and regression tests."""

        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


_TEXT_TYPES = {"TEXT", "MTEXT", "LEADER", "MLEADER", "MULTILEADER", "ATTRIB", "ATTDEF"}
_DIMENSION_TYPES = {"DIMENSION"}
_HATCH_TYPES = {"HATCH"}
_EXTERNAL_TYPES = {"IMAGE", "UNDERLAY", "PDFUNDERLAY", "DGNUNDERLAY", "DWFUNDERLAY"}
_PROXY_TYPES = {"ACAD_PROXY_ENTITY"}
_OPERATIONAL_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "POINT", "REGION", "SPLINE", "ELLIPSE", "INSERT"}
_REVIEW_TYPES = {"3DSOLID", "BODY", "SURFACE", "MESH", "POLYFACE", "POLYMESH"}

_KEYWORDS: dict[ClassificationRole, tuple[str, ...]] = {
    "product_geometry": ("product", "equipment", "footprint", "object", "play equipment", "main"),
    "safety_zone": ("safety", "fall", "falling", "impact", "use zone", "clearance", "clear zone"),
    "foundation_or_installation": ("foundation", "install", "installation", "anchor", "footing", "mount", "base plate", "bolt"),
    "dimensions": ("dimension", "dim", "dims", "measure"),
    "text_annotation": ("text", "note", "annotation", "label", "legend"),
    "hatch_or_fill": ("hatch", "fill", "solid", "pattern"),
    "construction_or_reference": ("construction", "reference", "ref", "guide", "centerline", "axis", "xref"),
    "non_operational": ("title", "border", "sheet", "viewport", "logo", "revision"),
}

BLUESTONE_PLAYGROUND_LAYER_PROFILE = VendorLayerProfile(
    name="bluestone_playground",
    aliases=(
        VendorLayerAlias(alias="Lg_prod", role="product_geometry", reason="bluestone_playground alias Lg_prod indicates product geometry"),
        VendorLayerAlias(alias="lg_prod", role="product_geometry", reason="bluestone_playground alias lg_prod indicates product geometry"),
        VendorLayerAlias(alias="Lg_area", role="safety_zone", reason="bluestone_playground alias Lg_area indicates a safety zone candidate"),
        VendorLayerAlias(alias="lg_area", role="safety_zone", reason="bluestone_playground alias lg_area indicates a safety zone candidate"),
        VendorLayerAlias(alias="Lg_falling", role="safety_zone", reason="bluestone_playground alias Lg_falling indicates safety zone"),
        VendorLayerAlias(alias="lg_falling", role="safety_zone", reason="bluestone_playground alias lg_falling indicates safety zone"),
        VendorLayerAlias(alias="LCPROD_FALLINGSPACE", role="safety_zone", reason="bluestone_playground alias LCPROD_FALLINGSPACE indicates safety zone"),
        VendorLayerAlias(alias="LCPROD_ENSAFETYREGION", role="safety_zone", reason="bluestone_playground alias LCPROD_ENSAFETYREGION indicates safety zone"),
        VendorLayerAlias(alias="Lg_dim", role="dimensions", reason="bluestone_playground alias Lg_dim indicates dimensions"),
        VendorLayerAlias(alias="DIMENSION", role="dimensions", reason="bluestone_playground alias DIMENSION indicates dimensions"),
        VendorLayerAlias(alias="Lg_txt", role="text_annotation", reason="bluestone_playground alias Lg_txt indicates text annotation"),
        VendorLayerAlias(alias="Lg_boundary", role="construction_or_reference", reason="bluestone_playground alias Lg_boundary indicates construction/reference boundary candidate"),
        VendorLayerAlias(alias="lc_ground", role="foundation_or_installation", reason="bluestone_playground alias lc_ground indicates foundation or installation"),
        VendorLayerAlias(alias="Defpoints", role="non_operational", reason="bluestone_playground alias Defpoints indicates non-operational CAD layer"),
        VendorLayerAlias(alias="ASHADE", role="hatch_or_fill", reason="bluestone_playground alias ASHADE indicates hatch/fill candidate"),
    ),
)

BUILT_IN_VENDOR_LAYER_PROFILES = {BLUESTONE_PLAYGROUND_LAYER_PROFILE.name: BLUESTONE_PLAYGROUND_LAYER_PROFILE}


def classify_layers(
    inventory: DxfInventoryResult,
    canonical: CanonicalGeometryResult | None = None,
    config: LayerClassificationConfig | None = None,
) -> ClassificationResult:
    """Classify source layers into operational roles without mutating CAD data."""

    cfg = config or LayerClassificationConfig()
    profile = _resolve_vendor_profile(cfg.vendor_profile)
    canonical_by_layer = _canonical_by_layer(canonical)
    total_area = _total_canonical_area(canonical)
    global_issues: list[ClassificationIssue] = []
    if inventory.drawing_units in {"missing", "unknown"} or inventory.insunits in {None, 0}:
        global_issues.append(
            ClassificationIssue(
                code=ClassificationIssueCode.UNKNOWN_UNITS.value,
                severity="warning",
                message="Drawing units are missing or unknown; classification does not infer millimetres.",
                evidence={"drawing_units": inventory.drawing_units, "insunits": inventory.insunits},
            )
        )

    xref_names = tuple(indicator.lower() for indicator in inventory.xref_indicators)
    layers: list[LayerClassification] = []
    for layer in sorted(inventory.layers, key=lambda item: item.name.lower()):
        try:
            layer_result = _classify_layer(layer, inventory, canonical_by_layer.get(layer.name, ()), total_area, xref_names, cfg, profile)
        except Exception as exc:
            issue = ClassificationIssue(
                code=ClassificationIssueCode.CLASSIFICATION_RULE_ERROR.value,
                severity="fail",
                message=f"Classification rule failed for layer {layer.name}: {exc}",
                layer_name=layer.name,
            )
            evidence = _evidence(layer, inventory.entity_counts_by_layer.get(layer.name, ()), (), total_area, xref_names, profile)
            layer_result = LayerClassification(
                original_layer_name=layer.name,
                assigned_role="review_required",
                confidence=0.0,
                matched_rules=(),
                positive_evidence=(),
                negative_evidence=("classification rule error",),
                alternative_roles=(),
                entity_counts=evidence.entity_counts,
                review_reason=issue.message,
                evidence=evidence,
                issues=(issue,),
            )
        layers.append(layer_result)

    all_issues = global_issues + [issue for layer in layers for issue in layer.issues]
    role_counts = Counter(layer.assigned_role for layer in layers)
    return ClassificationResult(
        source_identity=str(inventory.source_path),
        source_sha256=inventory.source_sha256,
        drawing_units=inventory.drawing_units,
        insunits=inventory.insunits,
        layer_count=len(layers),
        role_counts=dict(sorted(role_counts.items())),
        layers=tuple(layers),
        issues=tuple(sorted(all_issues, key=lambda item: (item.severity, item.code, item.layer_name or "", item.message))),
        config=cfg,
    )


def write_classification_json(result: ClassificationResult, output_path: Path | str) -> Path:
    """Write deterministic classification JSON to an explicit path."""

    path = Path(output_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.to_deterministic_json() + "\n", encoding="utf-8")
    except OSError as exc:
        raise OutputWriteError(f"Failed to write layer classification JSON report: {path}") from exc
    return path


def _classify_layer(
    layer: LayerInventory,
    inventory: DxfInventoryResult,
    canonical_entities: tuple[CanonicalEntity, ...],
    total_area: float | None,
    xref_names: tuple[str, ...],
    config: LayerClassificationConfig,
    vendor_profile: VendorLayerProfile | None,
) -> LayerClassification:
    counts = inventory.entity_counts_by_layer.get(layer.name, ())
    evidence = _evidence(layer, counts, canonical_entities, total_area, xref_names, vendor_profile)
    matches = _rule_matches(evidence, layer.name, config, vendor_profile)
    scores: Counter[str] = Counter()
    for match in matches:
        scores[match.role] += match.weight

    positive: list[str] = [match.evidence for match in matches]
    negative: list[str] = []
    issues: list[ClassificationIssue] = []
    review_reason: str | None = None

    if evidence.has_proxy:
        issues.append(_layer_issue("proxy_content", "warning", "Layer contains proxy content.", layer.name, evidence.entity_counts))
    if evidence.has_external_reference:
        issues.append(_layer_issue("external_reference_content", "warning", "Layer contains external reference or underlay content.", layer.name, evidence.entity_counts))
    if evidence.has_nonzero_z:
        negative.append("contains 3D or non-zero-Z evidence")

    if _mixed_operational_content(evidence):
        issues.append(_layer_issue("mixed_operational_content", "warning", "Layer mixes operational geometry with annotation, dimension, or hatch content.", layer.name, evidence.entity_counts))
        review_reason = "mixed operational and non-operational content"

    if _contradictory_name_geometry(matches, evidence):
        conflict_code = "vendor_alias_conflict" if any(match.source == "vendor_alias" for match in matches) else "conflicting_evidence"
        issues.append(_layer_issue(conflict_code, "warning", "Layer-name or vendor alias evidence conflicts with entity or geometry evidence.", layer.name, evidence.entity_counts))
        review_reason = "conflicting layer-name and geometry evidence"

    if layer.entity_count == 0:
        scores["non_operational"] += 0.7
        positive.append("layer has no entities")
    if not scores:
        issues.append(_layer_issue("insufficient_evidence", "warning", "No deterministic rule produced enough evidence for classification.", layer.name, evidence.entity_counts))
        review_reason = "insufficient evidence"

    sorted_scores = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    alternatives = tuple(role for role, _score in sorted_scores[1:4])
    best_role = sorted_scores[0][0] if sorted_scores else "ambiguous"
    best_score = min(sorted_scores[0][1], 1.0) if sorted_scores else 0.0

    if review_reason:
        assigned: ClassificationRole = "review_required"
        confidence = min(best_score, 0.44)
    elif evidence.has_proxy or evidence.has_external_reference:
        assigned = "external_or_proxy"
        confidence = min(max(scores.get("external_or_proxy", 0.0), best_score if best_role == "external_or_proxy" else 0.94), 1.0)
    elif best_score < config.review_threshold:
        assigned = "ambiguous"
        confidence = max(best_score, 0.2)
        issues.append(_layer_issue("insufficient_evidence", "warning", "Classification confidence is below review threshold.", layer.name, evidence.entity_counts))
        review_reason = "low-confidence classification"
    elif best_score < config.confidence_threshold:
        assigned = "ambiguous"
        confidence = best_score
        issues.append(_layer_issue("insufficient_evidence", "warning", "Classification confidence is below assignment threshold.", layer.name, evidence.entity_counts))
        review_reason = "low-confidence classification"
    else:
        assigned = best_role  # type: ignore[assignment]
        confidence = best_score

    if assigned in {"ambiguous", "review_required"} and best_role not in alternatives and best_role not in {"ambiguous", "review_required"}:
        alternatives = (best_role, *alternatives)

    return LayerClassification(
        original_layer_name=layer.name,
        assigned_role=assigned,
        confidence=round(confidence, 4),
        matched_rules=tuple(sorted(matches, key=lambda item: (item.rule_id, item.role, item.evidence))),
        positive_evidence=tuple(sorted(set(positive))),
        negative_evidence=tuple(sorted(set(negative))),
        alternative_roles=tuple(role for role in alternatives if role != assigned),
        entity_counts=evidence.entity_counts,
        review_reason=review_reason,
        evidence=evidence,
        issues=tuple(sorted(issues, key=lambda item: (item.code, item.message))),
    )


def _rule_matches(
    evidence: ClassificationEvidence,
    layer_name: str,
    config: LayerClassificationConfig,
    vendor_profile: VendorLayerProfile | None,
) -> list[ClassificationRuleMatch]:
    matches: list[ClassificationRuleMatch] = []
    normalized = evidence.normalized_layer_name
    for role, keywords in _KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            matches.append(ClassificationRuleMatch(rule_id=f"name_keyword:{role}", role=role, weight=config.name_weight, evidence=f"layer name matches {role} keyword", source="layer_name"))

    for override in config.vendor_overrides:
        if override.vendor is not None and config.vendor is not None and _normalize(override.vendor) != _normalize(config.vendor):
            continue
        if override.vendor is not None and config.vendor is None:
            continue
        if re.search(override.pattern, layer_name, flags=re.IGNORECASE):
            matches.append(ClassificationRuleMatch(rule_id=f"vendor_override:{override.pattern}", role=override.role, weight=override.weight, evidence=override.reason, source="vendor_override"))

    alias = _vendor_alias_for_layer(layer_name, vendor_profile)
    if alias is not None:
        matches.append(
            ClassificationRuleMatch(
                rule_id=f"vendor_alias:{vendor_profile.name}:{_normalize(alias.alias)}",
                role=alias.role,
                weight=alias.weight,
                evidence=f"{alias.reason}; matched alias {alias.alias}; profile {vendor_profile.name}",
                source="vendor_alias",
            )
        )

    counts = evidence.entity_counts
    total = sum(counts.values())
    if total == 0:
        return matches
    if evidence.has_proxy:
        matches.append(ClassificationRuleMatch(rule_id="entity:proxy", role="external_or_proxy", weight=0.96, evidence="proxy entity present", source="entity_types"))
    if evidence.has_external_reference:
        matches.append(ClassificationRuleMatch(rule_id="entity:external_reference", role="external_or_proxy", weight=0.94, evidence="external reference or underlay entity present", source="entity_types"))
    if _only_types(counts, _DIMENSION_TYPES):
        matches.append(ClassificationRuleMatch(rule_id="entity:pure_dimensions", role="dimensions", weight=config.pure_content_weight, evidence="layer contains only dimensions", source="entity_types"))
    if _only_types(counts, _TEXT_TYPES):
        matches.append(ClassificationRuleMatch(rule_id="entity:pure_text", role="text_annotation", weight=config.pure_content_weight, evidence="layer contains only text/annotation entities", source="entity_types"))
    if _only_types(counts, _HATCH_TYPES):
        matches.append(ClassificationRuleMatch(rule_id="entity:pure_hatch", role="hatch_or_fill", weight=config.pure_content_weight, evidence="layer contains only hatch/fill entities", source="entity_types"))
    if not evidence.is_visible or not evidence.is_plottable:
        matches.append(ClassificationRuleMatch(rule_id="state:hidden_or_nonplot", role="construction_or_reference", weight=0.78, evidence="layer is hidden, frozen, or non-plottable", source="layer_state"))
    if evidence.closed_entity_count > 0 and evidence.has_operational_geometry:
        matches.append(ClassificationRuleMatch(rule_id="geometry:closed_operational", role="product_geometry", weight=config.geometry_weight, evidence="closed operational geometry present", source="geometry"))
        matches.append(ClassificationRuleMatch(rule_id="geometry:closed_boundary_candidate", role="safety_zone", weight=0.22, evidence="closed boundary candidate present", source="geometry"))
        if "foundation_or_installation" in {match.role for match in matches if match.source in {"layer_name", "vendor_override"}}:
            matches.append(ClassificationRuleMatch(rule_id="geometry:foundation_closed_support", role="foundation_or_installation", weight=0.28, evidence="closed operational geometry supports foundation/install layer evidence", source="geometry"))
    if evidence.has_operational_geometry and evidence.closed_entity_count == 0 and not evidence.has_text and not evidence.has_dimensions:
        matches.append(ClassificationRuleMatch(rule_id="geometry:open_reference", role="construction_or_reference", weight=0.36, evidence="open operational geometry without closure", source="geometry"))
    if evidence.block_ancestry:
        matches.append(ClassificationRuleMatch(rule_id="block:ancestry", role="construction_or_reference", weight=0.18, evidence="content appears through block ancestry", source="block_ancestry"))
    return matches


def _evidence(
    layer: LayerInventory,
    counts: tuple[EntityTypeCount, ...],
    canonical_entities: tuple[CanonicalEntity, ...],
    total_area: float | None,
    xref_names: tuple[str, ...],
    vendor_profile: VendorLayerProfile | None = None,
) -> ClassificationEvidence:
    entity_counts = {item.dxf_type: item.count for item in counts}
    closed_count = sum(1 for entity in canonical_entities if entity.is_closed is True)
    open_count = sum(1 for entity in canonical_entities if entity.is_closed is False)
    layer_area = _layer_area(canonical_entities)
    normalized = _normalize(layer.name)
    alias = _vendor_alias_for_layer(layer.name, vendor_profile)
    has_xref_name = any(normalized in indicator for indicator in xref_names) or "xref" in normalized
    return ClassificationEvidence(
        normalized_layer_name=normalized,
        vendor_profile_name=vendor_profile.name if vendor_profile else None,
        matched_vendor_alias=alias.alias if alias else None,
        entity_counts=dict(sorted(entity_counts.items())),
        layer_color=layer.color,
        layer_linetype=layer.linetype,
        is_visible=bool(layer.is_on and not layer.is_frozen),
        is_plottable=bool(layer.is_plottable),
        is_locked=bool(layer.is_locked),
        closed_entity_count=closed_count,
        open_entity_count=open_count,
        canonical_entity_count=len(canonical_entities),
        has_text=any(key in _TEXT_TYPES for key in entity_counts),
        has_dimensions=any(key in _DIMENSION_TYPES for key in entity_counts),
        has_hatch=any(key in _HATCH_TYPES for key in entity_counts),
        has_operational_geometry=any(key in _OPERATIONAL_TYPES for key in entity_counts) or any(entity.status == "canonicalized" for entity in canonical_entities),
        has_external_reference=has_xref_name or any(key in _EXTERNAL_TYPES for key in entity_counts),
        has_proxy=any(key in _PROXY_TYPES for key in entity_counts),
        has_nonzero_z=any(key in _REVIEW_TYPES for key in entity_counts) or any(entity.z_values or (entity.elevation not in {None, 0.0}) for entity in canonical_entities),
        block_ancestry=tuple(sorted({ancestor for entity in canonical_entities for ancestor in entity.block_ancestry})),
        relative_area_ratio=(round(layer_area / total_area, 6) if layer_area is not None and total_area and total_area > 0 else None),
    )


def _canonical_by_layer(canonical: CanonicalGeometryResult | None) -> dict[str, tuple[CanonicalEntity, ...]]:
    grouped: defaultdict[str, list[CanonicalEntity]] = defaultdict(list)
    if canonical is None:
        return {}
    for entity in canonical.entities:
        grouped[entity.layer].append(entity)
    return {layer: tuple(sorted(entities, key=lambda item: item.order_key)) for layer, entities in grouped.items()}


def _total_canonical_area(canonical: CanonicalGeometryResult | None) -> float | None:
    if canonical is None:
        return None
    areas = [_layer_area((entity,)) for entity in canonical.entities]
    total = sum(area for area in areas if area is not None)
    return total if total > 0 else None


def _layer_area(entities: tuple[CanonicalEntity, ...]) -> float | None:
    area = 0.0
    for entity in entities:
        geometry = entity.geometry
        vertices = getattr(geometry, "vertices", None)
        if not vertices or not getattr(geometry, "is_closed", False):
            continue
        points = [(float(vertex.point.x), float(vertex.point.y)) for vertex in vertices]
        if len(points) < 3:
            continue
        area += abs(_shoelace(points))
    return area if area > 0 else None


def _shoelace(points: list[tuple[float, float]]) -> float:
    pairs = zip(points, points[1:] + points[:1])
    return sum((x1 * y2) - (x2 * y1) for (x1, y1), (x2, y2) in pairs) / 2.0


def _only_types(counts: dict[str, int], allowed: set[str]) -> bool:
    return bool(counts) and all(key in allowed for key, count in counts.items() if count > 0)


def _mixed_operational_content(evidence: ClassificationEvidence) -> bool:
    non_operational_signals = int(evidence.has_text) + int(evidence.has_dimensions) + int(evidence.has_hatch)
    return evidence.has_operational_geometry and non_operational_signals > 0


def _contradictory_name_geometry(matches: list[ClassificationRuleMatch], evidence: ClassificationEvidence) -> bool:
    name_roles = {match.role for match in matches if match.source in {"layer_name", "vendor_override", "vendor_alias"}}
    vendor_roles = {match.role for match in matches if match.source == "vendor_alias"}
    if not name_roles:
        return False
    if evidence.has_proxy or evidence.has_external_reference:
        return False
    if not evidence.entity_counts:
        return False
    if ("safety_zone" in name_roles or "product_geometry" in name_roles or "foundation_or_installation" in name_roles) and not evidence.has_operational_geometry:
        return True
    if "safety_zone" in name_roles and (evidence.has_dimensions or evidence.has_text) and not evidence.closed_entity_count:
        return True
    if "product_geometry" in name_roles and (evidence.has_dimensions or evidence.has_text) and not evidence.closed_entity_count:
        return True
    if "foundation_or_installation" in vendor_roles and evidence.has_nonzero_z:
        return True
    if "foundation_or_installation" in name_roles and (evidence.has_dimensions or evidence.has_text or evidence.has_hatch) and not evidence.has_operational_geometry:
        return True
    if "dimensions" in name_roles and evidence.has_operational_geometry and not evidence.has_dimensions:
        return True
    if "text_annotation" in name_roles and evidence.has_operational_geometry and not evidence.has_text:
        return True
    if "non_operational" in name_roles and any(key in evidence.entity_counts for key in (_OPERATIONAL_TYPES - {"POINT"})):
        return True
    return False


def _layer_issue(code: str, severity: IssueSeverity, message: str, layer_name: str, evidence: dict[str, int]) -> ClassificationIssue:
    return ClassificationIssue(code=code, severity=severity, message=message, layer_name=layer_name, evidence={"entity_counts": dict(sorted(evidence.items()))})


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _resolve_vendor_profile(profile_name: str | None) -> VendorLayerProfile | None:
    if profile_name is None:
        return None
    normalized = _normalize(profile_name).replace(" ", "_")
    profile = BUILT_IN_VENDOR_LAYER_PROFILES.get(normalized)
    if profile is None:
        known = ", ".join(sorted(BUILT_IN_VENDOR_LAYER_PROFILES))
        raise ValueError(f"Unknown vendor layer profile: {profile_name}. Built-in profiles: {known}")
    return profile


def _vendor_alias_for_layer(layer_name: str, vendor_profile: VendorLayerProfile | None) -> VendorLayerAlias | None:
    if vendor_profile is None:
        return None
    normalized = _normalize(layer_name)
    for alias in vendor_profile.aliases:
        if _normalize(alias.alias) == normalized:
            return alias
    return None

