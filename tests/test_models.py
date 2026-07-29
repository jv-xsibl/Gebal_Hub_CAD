"""Tests for Stage 1A typed CAD input contracts."""

import pytest
from pydantic import ValidationError

from gebal_cad_normalizer import (
    AdapterResult,
    CadAssetDescriptor,
    CadProcessingRequest,
    ExpectedDimensions,
    ExpectedSafetyData,
    InputQualityIssue,
    ProductIdentity,
)
from gebal_cad_normalizer.adapters.base import CadInputAdapter


def test_cad_processing_request_accepts_prd_cad_fields() -> None:
    request = CadProcessingRequest(
        product=ProductIdentity(
            sku="137132M",
            product_name="Sample product",
            vendor_source_identifier="vendor-a",
        ),
        top_view_cad=CadAssetDescriptor(
            url="https://example.invalid/top-view.dwg",
            file_name="137132M_top_view.dwg",
            file_type="dwg",
            media_id="media-1",
            vendor_revision="4.1",
            vendor_updated_at=1776837754032,
            purpose="top view",
        ),
        expected_dimensions=ExpectedDimensions(
            length_mm=4140,
            width_mm=4680,
            height_mm=3820,
        ),
        expected_safety=ExpectedSafetyData(
            safety_zone_length_mm=7700,
            safety_zone_width_mm=8350,
            falling_space_area_m2=49.1,
            impact_area_m2=47.5,
            free_fall_height_mm=1970,
        ),
    )

    assert request.product.sku == "137132M"
    assert request.top_view_cad.file_name.endswith(".dwg")
    assert request.expected_safety is not None
    assert request.expected_safety.free_fall_height_mm == 1970


def test_questionable_dimensions_are_rejected_not_corrected() -> None:
    with pytest.raises(ValidationError):
        ExpectedDimensions(length_mm=-4140, width_mm=4680)


def test_unknown_fields_are_rejected_at_contract_boundary() -> None:
    with pytest.raises(ValidationError):
        ProductIdentity(sku="137132M", product_name="Sample", color="red")


def test_input_quality_issue_codes_are_stable_literals() -> None:
    issue = InputQualityIssue(
        code="missing_top_view_cad",
        message="Top-view DWG asset is missing.",
        severity="fail",
        field_path="media.top_view_cad_file",
    )

    assert issue.code == "missing_top_view_cad"


def test_adapter_contract_returns_adapter_result() -> None:
    class DummyAdapter:
        def parse(self, payload: dict[str, object]) -> AdapterResult:
            return AdapterResult(
                issues=(
                    InputQualityIssue(
                        code="missing_sku",
                        message="SKU is missing.",
                        severity="fail",
                        field_path="sku",
                    ),
                )
            )

    adapter: CadInputAdapter = DummyAdapter()
    result = adapter.parse({})

    assert result.request is None
    assert result.issues[0].code == "missing_sku"

