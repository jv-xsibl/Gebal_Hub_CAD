"""Bluestone vendor payload adapter."""

from typing import Any, Mapping

from pydantic import ValidationError

from gebal_cad_normalizer.asset_selector import select_top_view_asset
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


def _first_text(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return None


def _first_number(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = mapping.get(key)
        if value is None or (isinstance(value, str) and value.strip() == ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _number_from_product(product: Mapping[str, Any], keys: tuple[str, ...], attribute_numbers: tuple[str, ...]) -> float | None:
    direct = _first_number(product, keys)
    if direct is not None:
        return direct
    for number in attribute_numbers:
        value = _attribute_value(product, number)
        if value is None:
            continue
        try:
            return float(value)
        except ValueError:
            continue
    return None


def _filename(asset: Mapping[str, Any]) -> str | None:
    return _first_text(asset, ("file_name", "filename", "fileName", "name", "asset_name", "assetName"))


def _url(asset: Mapping[str, Any]) -> str | None:
    return _first_text(asset, ("url", "download_url", "downloadUrl", "downloadUri", "href"))


def _normalize_asset(asset: Mapping[str, Any]) -> Mapping[str, Any]:
    normalized: dict[str, Any] = dict(asset)
    document_info = _attribute_value(asset, "ATON_DOC_INFO")
    revision = _attribute_value(asset, "ATON_DOC_REV")
    last_modified = _attribute_value(asset, "ATON_LAST_MODIFIED")
    if document_info is not None:
        normalized.setdefault("documentInformation", document_info)
        normalized.setdefault("classification", document_info)
    if revision is not None:
        normalized.setdefault("revision", revision)
    if last_modified is not None:
        normalized.setdefault("vendor_updated_at", last_modified)
    return normalized


def _attribute_value(asset: Mapping[str, Any], number: str) -> str | None:
    attributes = asset.get("attributes")
    if not isinstance(attributes, list):
        return None
    for attribute in attributes:
        if not isinstance(attribute, Mapping) or attribute.get("number") != number:
            continue
        values = attribute.get("values")
        if isinstance(values, list):
            for value in values:
                if value is not None and str(value).strip():
                    return str(value).strip()
        select = attribute.get("select")
        if isinstance(select, list):
            for option in select:
                if isinstance(option, Mapping):
                    value = _first_text(option, ("value", "name", "number"))
                    if value is not None:
                        return value
    return None


def _collect_assets(value: Any) -> list[Mapping[str, Any]]:
    assets: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        marker_keys = {
            "file_name",
            "filename",
            "fileName",
            "url",
            "download_url",
            "downloadUrl",
            "downloadUri",
            "content_type",
            "contentType",
            "asset_name",
            "assetName",
            "purpose",
            "document_information",
            "documentInformation",
            "media_id",
            "mediaId",
        }
        if marker_keys.intersection(value.keys()):
            assets.append(_normalize_asset(value))
        for child in value.values():
            assets.extend(_collect_assets(child))
    elif isinstance(value, list):
        for child in value:
            assets.extend(_collect_assets(child))
    return assets


class BluestoneAdapter:
    """Parse the first Bluestone product into CAD input contracts."""

    def parse(self, payload: Mapping[str, Any]) -> AdapterResult:
        issues: list[InputQualityIssue] = []
        results = payload.get("results")
        if not isinstance(results, list) or not results or not isinstance(results[0], Mapping):
            return AdapterResult(issues=(_issue("missing_sku", "Bluestone payload has no first product in results.", "results"),))

        product = results[0]
        sku = _first_text(product, ("sku", "productNumber", "product_number", "number", "code"))
        if sku is None:
            issues.append(_issue("missing_sku", "Product SKU is missing.", "results[0]"))

        selection = select_top_view_asset(_collect_assets(product))
        if selection.decision == "ambiguous":
            issues.append(_issue("ambiguous_cad_asset", selection.message, "results[0]"))
            return AdapterResult(issues=tuple(issues))
        if selection.decision == "missing" or selection.selected_candidate is None:
            issues.append(_issue("missing_top_view_cad", selection.message, "results[0]"))
            return AdapterResult(issues=tuple(issues))
        if sku is None:
            return AdapterResult(issues=tuple(issues))

        asset = selection.selected_candidate
        try:
            request = CadProcessingRequest(
                product=ProductIdentity(
                    sku=sku,
                    product_name=_first_text(product, ("name", "productName", "product_name")),
                    vendor_source_identifier=_first_text(product, ("vendor", "vendorSourceIdentifier", "vendor_source_identifier")),
                ),
                top_view_cad=CadAssetDescriptor(
                    url=_url(asset),
                    file_name=_filename(asset) or "top_view.dwg",
                    file_type=_first_text(asset, ("file_type", "fileType", "extension")) or "dwg",
                    media_id=_first_text(asset, ("media_id", "mediaId", "id")),
                    vendor_revision=_first_text(asset, ("vendor_revision", "vendorRevision", "revision")),
                    vendor_updated_at=asset.get("vendor_updated_at") or asset.get("updatedAt") or asset.get("createdAt"),
                    asset_name=_first_text(asset, ("asset_name", "assetName", "name")),
                    description=_first_text(asset, ("description",)),
                    content_type=_first_text(asset, ("content_type", "contentType")),
                    document_information=_first_text(asset, ("document_information", "documentInformation")),
                    purpose=_first_text(asset, ("purpose",)),
                    vendor_asset_classification=_first_text(asset, ("vendor_asset_classification", "classification", "type")),
                ),
                expected_dimensions=ExpectedDimensions(
                    length_mm=_number_from_product(product, ("length_mm", "lengthMm", "productLengthMm", "length"), ("LENGTH_MM",)),
                    width_mm=_number_from_product(product, ("width_mm", "widthMm", "productWidthMm", "width"), ("WIDTH_MM",)),
                    height_mm=_number_from_product(product, ("height_mm", "heightMm", "productHeightMm", "height"), ("HEIGHT_MM",)),
                ),
                expected_safety=ExpectedSafetyData(
                    safety_zone_length_mm=_number_from_product(product, ("safety_zone_length_mm", "safetyZoneLengthMm", "safetyZoneLength"), ("SAFETY_AREA_LENGTH_MM", "FALLING_SPACE_LENGTH_MM")),
                    safety_zone_width_mm=_number_from_product(product, ("safety_zone_width_mm", "safetyZoneWidthMm", "safetyZoneWidth"), ("SAFETY_AREA_WIDTH_MM", "FALLING_SPACE_WIDTH_MM")),
                    falling_space_area_m2=_number_from_product(product, ("falling_space_area_m2", "fallingSpaceAreaM2", "fallingSpaceArea"), ("FALLING_SPACE_M2",)),
                    impact_area_m2=_number_from_product(product, ("impact_area_m2", "impactAreaM2", "impactArea"), ("IMPACT_AREA_M2",)),
                    free_fall_height_mm=_number_from_product(product, ("free_fall_height_mm", "freeFallHeightMm", "cfh_mm", "cfhMm", "cfh"), ("MAX_FREE_FALL_HEIGHT_PLAY_MM",)),
                ),
            )
        except ValidationError as exc:
            issues.append(_issue("invalid_dimension", str(exc), "results[0]"))
            return AdapterResult(issues=tuple(issues))
        return AdapterResult(request=request, issues=tuple(issues))
