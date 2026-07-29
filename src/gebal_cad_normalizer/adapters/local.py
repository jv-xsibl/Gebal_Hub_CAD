"""Local explicit DWG adapter."""

from pathlib import Path
from typing import Any, Mapping

from gebal_cad_normalizer.models import (
    AdapterResult,
    CadAssetDescriptor,
    CadProcessingRequest,
    ExpectedDimensions,
    InputQualityIssue,
    ProductIdentity,
)


def _issue(code: str, message: str, field_path: str | None = None, severity: str = "fail") -> InputQualityIssue:
    return InputQualityIssue(code=code, message=message, severity=severity, field_path=field_path)  # type: ignore[arg-type]


def _text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(payload: Mapping[str, Any], key: str, issues: list[InputQualityIssue]) -> float | None:
    value = payload.get(key)
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        issues.append(_issue("invalid_dimension", "Dimension is not numeric.", key))
        return None
    if number <= 0:
        issues.append(_issue("invalid_dimension", "Dimension must be greater than zero.", key))
        return None
    return number


class LocalAdapter:
    """Parse an explicit local DWG path into a CAD request."""

    def parse(self, payload: Mapping[str, Any]) -> AdapterResult:
        issues: list[InputQualityIssue] = []
        sku = _text(payload, "sku")
        if sku is None:
            issues.append(_issue("missing_sku", "Product SKU is missing.", "sku"))

        path_text = _text(payload, "local_dwg_path") or _text(payload, "path")
        if path_text is None:
            issues.append(_issue("missing_local_file", "Local DWG path is missing.", "local_dwg_path"))
            return AdapterResult(issues=tuple(issues))

        path = Path(path_text)
        if not path.exists():
            issues.append(_issue("missing_local_file", "Local DWG path does not exist.", "local_dwg_path"))
        if path.suffix.lower() != ".dwg":
            issues.append(_issue("invalid_local_file", "Local CAD file must use the .dwg extension.", "local_dwg_path"))
        if sku is not None and sku.lower() not in path.stem.lower():
            issues.append(_issue("filename_sku_mismatch", "Local DWG filename does not contain the SKU.", "local_dwg_path", "warning"))

        length = _number(payload, "length_mm", issues)
        width = _number(payload, "width_mm", issues)
        height = _number(payload, "height_mm", issues)
        dimensions = ExpectedDimensions(length_mm=length, width_mm=width, height_mm=height) if length and width else None

        fatal_codes = {"missing_sku", "missing_local_file", "invalid_local_file", "invalid_dimension"}
        if sku is None or any(issue.code in fatal_codes for issue in issues):
            return AdapterResult(issues=tuple(issues))

        request = CadProcessingRequest(
            product=ProductIdentity(sku=sku, product_name=_text(payload, "product_name")),
            top_view_cad=CadAssetDescriptor(local_path=str(path), file_name=path.name, file_type="dwg"),
            expected_dimensions=dimensions,
        )
        return AdapterResult(request=request, issues=tuple(issues))
