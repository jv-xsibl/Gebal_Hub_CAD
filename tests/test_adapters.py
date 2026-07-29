"""Tests for Stage 1B input adapters."""

import copy
from pathlib import Path

from gebal_cad_normalizer.adapters.bluestone import BluestoneAdapter
from gebal_cad_normalizer.adapters.local import LocalAdapter
from gebal_cad_normalizer.adapters.unified import UnifiedAdapter
from gebal_cad_normalizer.fixture_loader import load_json_fixture

FIXTURES = Path(__file__).parent / "fixtures"


def issue_codes(result: object) -> set[str]:
    return {issue.code for issue in result.issues}  # type: ignore[attr-defined]


def issue_paths_by_code(result: object, code: str) -> set[str | None]:
    return {issue.field_path for issue in result.issues if issue.code == code}  # type: ignore[attr-defined]


def test_bluestone_selects_only_top_view_dwg_and_rejects_side_3d_pdf_images() -> None:
    payload = load_json_fixture(FIXTURES / "bluestone_product.json").data
    original = copy.deepcopy(payload)

    result = BluestoneAdapter().parse(payload)

    assert result.request is not None
    assert result.request.product.sku == "137132M"
    assert result.request.top_view_cad.file_name == "137132M_top_view.dwg"
    assert result.request.top_view_cad.media_id == "top-1"
    assert result.request.expected_dimensions is not None
    assert result.request.expected_dimensions.length_mm == 4140
    assert result.request.expected_safety is not None
    assert result.request.expected_safety.falling_space_area_m2 == 49.1
    assert result.request.expected_safety.free_fall_height_mm == 1970
    assert payload == original


def test_bluestone_ambiguous_top_view_returns_error() -> None:
    payload = load_json_fixture(FIXTURES / "bluestone_ambiguous.json").data

    result = BluestoneAdapter().parse(payload)

    assert result.request is None
    assert "ambiguous_cad_asset" in issue_codes(result)


def test_unified_accepts_numeric_strings_blank_strings_and_zero_safety_dimension() -> None:
    payload = {
        "sku": "137132M",
        "name": "Climbing Unit",
        "technical": {
            "dimensions": {
                "length_mm": "4140",
                "width_mm": "4680",
                "height_mm": "",
            }
        },
        "safety": {
            "safety_zone": {
                "length_mm": "0",
                "width_mm": "8350",
            },
            "cfh_mm": "",
        },
        "media": {
            "top_view_cad_file": {
                "file_name": "137132M_top_view.dwg",
                "file_type": "dwg",
                "url": "https://example.invalid/137132M_top_view.dwg",
            },
            "unrelated_bad_asset": {"file_type": "pdf"},
        },
        "unrelated": {"broken": object()},
    }
    original = copy.deepcopy({key: value for key, value in payload.items() if key != "unrelated"})

    result = UnifiedAdapter().parse(payload)

    assert result.request is not None
    assert result.request.expected_dimensions is not None
    assert result.request.expected_dimensions.length_mm == 4140
    assert result.request.expected_dimensions.height_mm is None
    assert result.request.expected_safety is not None
    assert result.request.expected_safety.safety_zone_length_mm is None
    assert result.request.expected_safety.safety_zone_width_mm == 8350
    assert "blank_string" in issue_codes(result)
    assert "invalid_safety_data" in issue_codes(result)
    comparable_payload = {key: value for key, value in payload.items() if key != "unrelated"}
    assert comparable_payload == original


def test_unified_f24706m_preserves_request_and_flags_zero_safety_zone_without_correction() -> None:
    fixture_path = FIXTURES / "F24706M.json"
    source_before = fixture_path.read_text(encoding="utf-8")
    payload = load_json_fixture(fixture_path).data
    original_payload = copy.deepcopy(payload)

    result = UnifiedAdapter().parse(payload)
    source_after = fixture_path.read_text(encoding="utf-8")

    assert result.request is not None
    assert result.request.product.sku == "F24706M"
    assert result.request.product.product_name == "JUHA TODDLER PLAY HOUSE"
    assert result.request.top_view_cad.file_name == "F24706M_top_view.dwg"
    assert result.request.top_view_cad.url == "https://example.invalid/products/F24706M/cad/F24706M_top_view.dwg"
    assert result.request.top_view_cad.file_type == "dwg"
    assert result.request.top_view_cad.media_id == "F24706M-top-view"
    assert result.request.top_view_cad.vendor_revision == "2026-07-21"
    assert result.request.top_view_cad.asset_name == "F24706M top view DWG"
    assert result.request.top_view_cad.document_information == "Top view"
    assert result.request.expected_dimensions is not None
    assert result.request.expected_dimensions.length_mm == 1690
    assert result.request.expected_dimensions.width_mm == 1690
    assert result.request.expected_dimensions.height_mm == 1450
    assert result.request.expected_safety is not None
    assert result.request.expected_safety.safety_zone_length_mm is None
    assert result.request.expected_safety.safety_zone_width_mm is None
    assert result.request.expected_safety.free_fall_height_mm == 600
    assert issue_paths_by_code(result, "invalid_safety_data") == {
        "safety.safety_zone.length_mm",
        "safety.safety_zone.width_mm",
    }
    assert result.request.expected_safety.safety_zone_length_mm != result.request.expected_dimensions.length_mm
    assert result.request.expected_safety.safety_zone_width_mm != result.request.expected_dimensions.width_mm
    assert payload == original_payload
    assert source_after == source_before


def test_unified_invalid_safety_numeric_text_becomes_none_plus_issue() -> None:
    payload = {
        "sku": "BAD-SAFETY-TEXT",
        "technical": {"dimensions": {"length_mm": 1000, "width_mm": 2000}},
        "safety": {
            "safety_zone": {
                "length_mm": "not numeric",
                "width_mm": "still not numeric",
            },
            "cfh_mm": 300,
        },
        "media": {
            "top_view_cad_file": {
                "file_name": "BAD-SAFETY-TEXT_top_view.dwg",
                "file_type": "dwg",
                "url": "https://example.invalid/BAD-SAFETY-TEXT_top_view.dwg",
            }
        },
    }

    result = UnifiedAdapter().parse(payload)

    assert result.request is not None
    assert result.request.expected_safety is not None
    assert result.request.expected_safety.safety_zone_length_mm is None
    assert result.request.expected_safety.safety_zone_width_mm is None
    assert issue_paths_by_code(result, "invalid_safety_data") == {
        "safety.safety_zone.length_mm",
        "safety.safety_zone.width_mm",
    }


def test_unified_reports_missing_cad_asset_and_ignores_unrelated_schema_inconsistencies() -> None:
    payload = {
        "sku": "137132M",
        "technical": {"dimensions": {"length_mm": "4140", "width_mm": "4680"}},
        "media": {"base_product_cad_file": {"file_type": "dwg"}},
        "unrelated": {"bad": object()},
    }

    result = UnifiedAdapter().parse(payload)

    assert result.request is None
    assert issue_codes(result) == {"missing_top_view_cad"}


def test_local_adapter_validates_existing_dwg(tmp_path: Path) -> None:
    dwg_path = tmp_path / "137132M_source.dwg"
    dwg_path.write_bytes(b"not real dwg; adapter only validates path metadata")

    result = LocalAdapter().parse(
        {
            "sku": "137132M",
            "local_dwg_path": str(dwg_path),
            "length_mm": "4140",
            "width_mm": "4680",
        }
    )

    assert result.request is not None
    assert result.request.top_view_cad.local_path == str(dwg_path)
    assert result.request.expected_dimensions is not None
    assert result.request.expected_dimensions.width_mm == 4680


def test_local_adapter_reports_missing_file_and_invalid_extension(tmp_path: Path) -> None:
    missing_path = tmp_path / "137132M_source.pdf"

    result = LocalAdapter().parse({"sku": "137132M", "local_dwg_path": str(missing_path)})

    assert result.request is None
    assert {"missing_local_file", "invalid_local_file"}.issubset(issue_codes(result))


def test_local_adapter_reports_filename_sku_mismatch_without_rejecting(tmp_path: Path) -> None:
    dwg_path = tmp_path / "OTHER_source.dwg"
    dwg_path.write_bytes(b"not real dwg")

    result = LocalAdapter().parse(
        {
            "sku": "137132M",
            "local_dwg_path": str(dwg_path),
            "length_mm": 4140,
            "width_mm": 4680,
        }
    )

    assert result.request is not None
    assert issue_codes(result) == {"filename_sku_mismatch"}


def test_fixture_loader_tolerates_comments_without_modifying_source() -> None:
    fixture_path = FIXTURES / "commented_fixture.json"
    before = fixture_path.read_text(encoding="utf-8")

    loaded = load_json_fixture(fixture_path, allow_comments=True)
    after = fixture_path.read_text(encoding="utf-8")

    assert loaded.tolerant_parsing_used is True
    assert loaded.data["sku"] == "COMMENTED"
    assert before == after


def test_bluestone_real_137132m_raw_selects_image_dwg_top_view_asset() -> None:
    payload = load_json_fixture(FIXTURES / "137132M_raw.json").data
    original = copy.deepcopy(payload)

    result = BluestoneAdapter().parse(payload)

    assert result.issues == ()
    assert result.request is not None
    asset = result.request.top_view_cad
    assert result.request.product.sku == "137132M"
    assert asset.media_id == "8341f099-ed96-4293-9576-7eed7068539e"
    assert asset.asset_name == "TOP VIEW 137132M"
    assert asset.file_name == "137132M:137132MPL.DWG"
    assert asset.content_type == "image/dwg"
    assert asset.document_information == "2dModel"
    assert asset.vendor_asset_classification == "2dModel"
    assert asset.vendor_revision == "4.1"
    assert asset.vendor_updated_at == "2016-10-25 06:00"
    assert asset.url == (
        "https://media.bluestonepim.com/b88ad392-18e6-47b6-aa20-d2ceb5d930ba/"
        "8341f099-ed96-4293-9576-7eed7068539e/oxbGlur6ccJ9RNixKRWvpyLBX/"
        "7iinxX5VofJFHrCxfZcWfluF0.DWG"
    )
    assert "SIDE VIEW" not in (asset.asset_name or "")
    assert "PRODUCT MODEL" not in (asset.asset_name or "")
    assert payload == original
