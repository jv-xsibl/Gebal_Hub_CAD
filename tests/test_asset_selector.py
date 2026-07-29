"""Tests for deterministic Stage 2 CAD asset selection."""

import copy

from gebal_cad_normalizer.asset_selector import select_top_view_asset


def _asset(
    media_id: str,
    file_name: str,
    *,
    asset_name: str | None = None,
    content_type: str = "application/acad",
    classification: str | None = None,
    purpose: str | None = None,
) -> dict[str, object]:
    data: dict[str, object] = {
        "id": media_id,
        "fileName": file_name,
        "url": f"https://example.invalid/{file_name}",
        "contentType": content_type,
    }
    if asset_name is not None:
        data["assetName"] = asset_name
    if classification is not None:
        data["classification"] = classification
    if purpose is not None:
        data["purpose"] = purpose
    return data


def test_correct_top_view_dwg_selected_from_mixed_media() -> None:
    result = select_top_view_asset(
        [
            _asset("side", "product_side.dwg", asset_name="SIDE VIEW", classification="2dModel"),
            _asset("model", "product_3d.dwg", asset_name="3D product model", classification="3dModel"),
            _asset("pdf", "installation.pdf", asset_name="Installation PDF", content_type="application/pdf"),
            _asset("image", "preview.png", asset_name="Preview image", content_type="image/png"),
            _asset("top", "product_top_view.dwg", asset_name="TOP VIEW", classification="2dModel"),
        ]
    )

    assert result.decision == "selected"
    assert result.selected_candidate is not None
    assert result.selected_candidate["id"] == "top"


def test_side_view_dwg_rejected() -> None:
    result = select_top_view_asset([_asset("side", "product_side.dwg", asset_name="SIDE VIEW", classification="2dModel")])

    assert result.decision == "missing"
    assert result.issue_code == "missing_top_view_cad"
    assert result.evaluations[0].status == "rejected"
    assert any("side view" in reason for reason in result.evaluations[0].reasons)


def test_3d_product_model_rejected() -> None:
    result = select_top_view_asset([_asset("model", "product_3d.dwg", asset_name="3D product model", classification="3dModel")])

    assert result.decision == "missing"
    assert result.evaluations[0].status == "rejected"
    assert any("3d" in reason or "product model" in reason for reason in result.evaluations[0].reasons)


def test_pdf_and_image_rejected() -> None:
    result = select_top_view_asset(
        [
            _asset("pdf", "top_view.pdf", asset_name="TOP VIEW", content_type="application/pdf"),
            _asset("image", "top_view.png", asset_name="TOP VIEW", content_type="image/png"),
        ]
    )

    assert result.decision == "missing"
    assert {evaluation.status for evaluation in result.evaluations} == {"rejected"}
    assert any("PDF" in reason for reason in result.evaluations[0].reasons)
    assert any("image" in reason for reason in result.evaluations[1].reasons)


def test_explicit_2d_model_preferred() -> None:
    result = select_top_view_asset(
        [
            _asset("plain", "top_plain.dwg", asset_name="TOP VIEW"),
            _asset("2d", "top_2d.dwg", asset_name="TOP VIEW", classification="2dModel"),
        ]
    )

    assert result.decision == "selected"
    assert result.selected_candidate is not None
    assert result.selected_candidate["id"] == "2d"
    assert result.selected_evaluation is not None
    assert any("2dModel" in reason for reason in result.selected_evaluation.reasons)


def test_explicit_top_view_purpose_preferred() -> None:
    result = select_top_view_asset(
        [
            _asset("name", "named_top.dwg", asset_name="TOP VIEW", classification="2dModel"),
            _asset("purpose", "purpose_top.dwg", asset_name="CAD drawing", classification="2dModel", purpose="Top view"),
        ]
    )

    assert result.decision == "selected"
    assert result.selected_candidate is not None
    assert result.selected_candidate["id"] == "purpose"
    assert result.selected_evaluation is not None
    assert any("explicit top-view purpose" in reason for reason in result.selected_evaluation.reasons)


def test_missing_top_view_asset() -> None:
    result = select_top_view_asset([_asset("dwg", "generic.dwg", asset_name="CAD drawing", classification="2dModel")])

    assert result.decision == "missing"
    assert result.issue_code == "missing_top_view_cad"
    assert result.selected_candidate is None


def test_equally_valid_top_view_assets_produce_ambiguity() -> None:
    result = select_top_view_asset(
        [
            _asset("top-a", "a_top.dwg", asset_name="TOP VIEW", classification="2dModel"),
            _asset("top-b", "b_top.dwg", asset_name="TOP VIEW", classification="2dModel"),
        ]
    )

    assert result.decision == "ambiguous"
    assert result.issue_code == "ambiguous_cad_asset"
    assert result.selected_candidate is None


def test_selection_result_contains_reasons_and_scores() -> None:
    result = select_top_view_asset([_asset("top", "product_top_view.dwg", asset_name="TOP VIEW", classification="2dModel")])

    assert result.selected_evaluation is not None
    assert result.selected_evaluation.candidate_identifier == "top"
    assert result.selected_evaluation.score > 0
    assert result.selected_evaluation.status == "accepted"
    assert result.selected_evaluation.reasons
    assert result.message


def test_source_candidate_data_is_not_mutated() -> None:
    candidates = [_asset("top", "product_top_view.dwg", asset_name="TOP VIEW", classification="2dModel")]
    original = copy.deepcopy(candidates)

    select_top_view_asset(candidates)

    assert candidates == original


def test_real_bluestone_image_dwg_top_view_2dmodel_wins_without_accepting_images() -> None:
    result = select_top_view_asset(
        [
            {
                "id": "top-real",
                "downloadUri": "https://example.invalid/asset/top-real/source.DWG",
                "name": "TOP VIEW 137132M",
                "fileName": "137132M:137132MPL.DWG",
                "contentType": "image/dwg",
                "documentInformation": "2dModel",
            },
            {
                "id": "side-real",
                "downloadUri": "https://example.invalid/asset/side-real/source.DWG",
                "name": "SIDE VIEW 137132M",
                "fileName": "137132M:137132MSI.DWG",
                "contentType": "image/dwg",
                "documentInformation": "2dModel",
            },
            {
                "id": "model-real",
                "downloadUri": "https://example.invalid/asset/model-real/source.DWG",
                "name": "PRODUCT MODEL 137132M",
                "fileName": "137132M:137132M.DWG",
                "contentType": "image/dwg",
                "documentInformation": "3dDwg",
            },
            {
                "id": "png-real",
                "downloadUri": "https://example.invalid/asset/png-real/top-view.png",
                "name": "TOP VIEW 137132M",
                "fileName": "137132M:137132MPL-Model.png",
                "contentType": "image/png",
                "documentInformation": "2dModel",
            },
        ]
    )

    assert result.decision == "selected"
    assert result.selected_candidate is not None
    assert result.selected_candidate["id"] == "top-real"
    assert result.selected_evaluation is not None
    assert result.selected_evaluation.score == 90
    assert result.selected_evaluation.reasons == (
        "accepted: .dwg extension",
        "accepted: DWG content type",
        "accepted: name or description indicates top view",
        "accepted: purpose or asset metadata indicates 2dModel",
    )
    statuses = {evaluation.candidate_identifier: evaluation.status for evaluation in result.evaluations}
    assert statuses == {
        "top-real": "accepted",
        "side-real": "rejected",
        "model-real": "rejected",
        "png-real": "rejected",
    }
