"""Unified Gebal product schema adapter."""

from typing import Any, Mapping

from gebal_cad_normalizer.models import (
    AdapterResult,
    CadAssetDescriptor,
    CadProcessingRequest,
    ExpectedDimensions,
    ExpectedSafetyData,
    InputQualityIssue,
    ProductIdentity,
)


def _issue(code: str, message: str, field_path: str | None = None, severity: str = "fail") -> InputQualityIssue:
    return InputQualityIssue(code=code, message=message, severity=severity, field_path=field_path)  # type: ignore[arg-type]


def _get(mapping: Mapping[str, Any], path: str) -> Any:
    current: Any = mapping
    for key in path.split("."):
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _text(mapping: Mapping[str, Any], path: str) -> str | None:
    value = _get(mapping, path)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(mapping: Mapping[str, Any], path: str, issues: list[InputQualityIssue], *, required: bool = False) -> float | None:
    value = _get(mapping, path)
    invalid_code = "invalid_safety_data" if path.startswith("safety.") else "invalid_dimension"
    if isinstance(value, str) and value.strip() == "":
        issues.append(_issue("blank_string", "Blank string converted to None.", path, "warning"))
        return None
    if value is None:
        if required:
            issues.append(_issue("missing_dimension", "Required dimension is missing.", path))
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        issues.append(_issue(invalid_code, "Dimension is not numeric.", path))
        return None
    if number <= 0:
        issues.append(_issue(invalid_code, "Dimension must be greater than zero.", path))
        return None
    return number


class UnifiedAdapter:
    """Parse normalized Gebal product fields into CAD input contracts."""

    def parse(self, payload: Mapping[str, Any]) -> AdapterResult:
        issues: list[InputQualityIssue] = []
        sku = _text(payload, "sku")
        if sku is None:
            issues.append(_issue("missing_sku", "Product SKU is missing.", "sku"))

        asset = _get(payload, "media.top_view_cad_file")
        cad_descriptor: CadAssetDescriptor | None = None
        if not isinstance(asset, Mapping):
            issues.append(_issue("missing_top_view_cad", "Top-view CAD asset is missing.", "media.top_view_cad_file"))
        else:
            file_name = _text(asset, "file_name") or _text(asset, "fileName")
            url = _text(asset, "url")
            file_type = (_text(asset, "file_type") or _text(asset, "fileType") or "").lower()
            if file_name is None or url is None:
                issues.append(_issue("missing_top_view_cad", "Top-view CAD asset requires file_name and url.", "media.top_view_cad_file"))
            elif file_type and file_type != "dwg":
                issues.append(_issue("invalid_cad_asset_type", "Top-view CAD asset must be a DWG.", "media.top_view_cad_file.file_type"))
            else:
                cad_descriptor = CadAssetDescriptor(
                    url=url,
                    file_name=file_name,
                    file_type=file_type or "dwg",
                    media_id=_text(asset, "media_id") or _text(asset, "mediaId"),
                    vendor_revision=_text(asset, "vendor_revision") or _text(asset, "vendorRevision"),
                    vendor_updated_at=_get(asset, "vendor_updated_at") or _get(asset, "vendorUpdatedAt"),
                    asset_name=_text(asset, "asset_name") or _text(asset, "assetName"),
                    description=_text(asset, "description"),
                    content_type=_text(asset, "content_type") or _text(asset, "contentType"),
                    document_information=_text(asset, "document_information") or _text(asset, "documentInformation"),
                    purpose=_text(asset, "purpose"),
                    vendor_asset_classification=_text(asset, "vendor_asset_classification") or _text(asset, "vendorAssetClassification"),
                )

        length = _number(payload, "technical.dimensions.length_mm", issues, required=True)
        width = _number(payload, "technical.dimensions.width_mm", issues, required=True)
        height = _number(payload, "technical.dimensions.height_mm", issues)
        dimensions = ExpectedDimensions(length_mm=length, width_mm=width, height_mm=height) if length and width else None

        safety_values = {
            "safety_zone_length_mm": _number(payload, "safety.safety_zone.length_mm", issues),
            "safety_zone_width_mm": _number(payload, "safety.safety_zone.width_mm", issues),
            "free_fall_height_mm": _number(payload, "safety.cfh_mm", issues),
        }
        safety = ExpectedSafetyData(**safety_values) if any(value is not None for value in safety_values.values()) else None

        if sku is None or cad_descriptor is None:
            return AdapterResult(issues=tuple(issues))

        request = CadProcessingRequest(
            product=ProductIdentity(sku=sku, product_name=_text(payload, "name")),
            top_view_cad=cad_descriptor,
            expected_dimensions=dimensions,
            expected_safety=safety,
        )
        return AdapterResult(request=request, issues=tuple(issues))
