"""Deterministic CAD asset selection shared by vendor adapters."""

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class AssetSelectionConfig:
    """Configurable scoring weights for top-view DWG selection."""

    dwg_extension_score: int = 20
    dwg_content_type_score: int = 20
    dwg_file_type_score: int = 15
    top_view_text_score: int = 30
    filename_top_view_score: int = 0
    model_2d_score: int = 20
    explicit_top_purpose_score: int = 35
    rejection_penalty: int = 100
    minimum_selectable_score: int = 60


@dataclass(frozen=True)
class AssetCandidateEvaluation:
    """Explainable score for one candidate media asset."""

    candidate_identifier: str
    score: int
    status: str
    reasons: tuple[str, ...]
    candidate: Mapping[str, Any]


@dataclass(frozen=True)
class AssetSelectionResult:
    """Final top-view DWG selection decision and supporting evidence."""

    decision: str
    selected_candidate: Mapping[str, Any] | None
    selected_evaluation: AssetCandidateEvaluation | None
    issue_code: str | None
    message: str
    evaluations: tuple[AssetCandidateEvaluation, ...]


DEFAULT_ASSET_SELECTION_CONFIG = AssetSelectionConfig()

_ASSET_ID_KEYS = ("media_id", "mediaId", "id", "asset_id", "assetId", "uuid")
_FILENAME_KEYS = ("file_name", "filename", "fileName", "name", "asset_name", "assetName")
_URL_KEYS = ("url", "download_url", "downloadUrl", "downloadUri", "href")
_CONTENT_TYPE_KEYS = ("content_type", "contentType", "mime_type", "mimeType")
_FILE_TYPE_KEYS = ("file_type", "fileType", "extension")
_PURPOSE_KEYS = ("purpose", "document_information", "documentInformation")
_CLASSIFICATION_KEYS = ("vendor_asset_classification", "vendorAssetClassification", "classification", "type")
_DESCRIPTION_KEYS = ("asset_name", "assetName", "name", "description", "document_information", "documentInformation")

_TOP_VIEW_TOKENS = ("top view", "top-view", "top_view", "plan view")
_EXPLICIT_TOP_PURPOSE_TOKENS = ("top view", "top-view", "top_view", "plan view")
_MODEL_2D_TOKENS = ("2dmodel", "2d model", "2-d model", "2d")
_REJECTION_TOKENS = (
    "side view",
    "side-view",
    "side_view",
    "elevation",
    "3d",
    "3-d",
    "3dmodel",
    "3d model",
    "product model",
    "model dwg",
    "preview",
    "image",
    "pdf",
    "installation",
)
_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "svg"}
_DOCUMENT_EXTENSIONS = {"pdf"}
_DWG_CONTENT_TYPE_TOKENS = ("dwg", "acad", "autocad", "vnd.dwg", "x-dwg")
_IMAGE_CONTENT_TYPE_PREFIX = "image/"
_PDF_CONTENT_TYPE = "application/pdf"


def select_top_view_asset(
    candidates: Sequence[Mapping[str, Any]],
    config: AssetSelectionConfig = DEFAULT_ASSET_SELECTION_CONFIG,
) -> AssetSelectionResult:
    """Select one top-view DWG or return a stable missing/ambiguous issue."""

    evaluations = tuple(_evaluate_candidate(candidate, index, config) for index, candidate in enumerate(candidates))
    accepted = [evaluation for evaluation in evaluations if evaluation.status == "accepted"]

    if not accepted:
        return AssetSelectionResult(
            decision="missing",
            selected_candidate=None,
            selected_evaluation=None,
            issue_code="missing_top_view_cad",
            message="No valid top-view DWG asset was found.",
            evaluations=evaluations,
        )

    ordered = sorted(accepted, key=lambda item: (-item.score, item.candidate_identifier))
    best = ordered[0]
    tied_best = [item for item in ordered if item.score == best.score]
    if len(tied_best) > 1:
        return AssetSelectionResult(
            decision="ambiguous",
            selected_candidate=None,
            selected_evaluation=None,
            issue_code="ambiguous_cad_asset",
            message="Multiple top-view DWG assets are equally credible.",
            evaluations=evaluations,
        )

    return AssetSelectionResult(
        decision="selected",
        selected_candidate=best.candidate,
        selected_evaluation=best,
        issue_code=None,
        message="Selected the highest-scoring top-view DWG asset.",
        evaluations=evaluations,
    )


def _evaluate_candidate(
    candidate: Mapping[str, Any],
    index: int,
    config: AssetSelectionConfig,
) -> AssetCandidateEvaluation:
    score = 0
    reasons: list[str] = []
    identifier = _candidate_identifier(candidate, index)
    extension = _extension_from(candidate)
    content_type = _normalize_content_type(_first_text(candidate, _CONTENT_TYPE_KEYS))
    file_type = (_first_text(candidate, _FILE_TYPE_KEYS) or "").strip().lower().lstrip(".")
    descriptive_text = _joined_text(candidate, _DESCRIPTION_KEYS)
    filename_text = (_first_text(candidate, _FILENAME_KEYS) or "").lower()
    purpose_text = _joined_text(candidate, _PURPOSE_KEYS)
    classification_text = _joined_text(candidate, _CLASSIFICATION_KEYS)
    metadata_text = " ".join(text for text in (descriptive_text, filename_text, purpose_text, classification_text, file_type) if text)

    if extension == "dwg":
        score += config.dwg_extension_score
        reasons.append("accepted: .dwg extension")
    if _is_dwg_content_type(content_type):
        score += config.dwg_content_type_score
        reasons.append("accepted: DWG content type")
    if file_type == "dwg":
        score += config.dwg_file_type_score
        reasons.append("accepted: DWG file type metadata")
    if _contains_token(descriptive_text, _TOP_VIEW_TOKENS):
        score += config.top_view_text_score
        reasons.append("accepted: name or description indicates top view")
    if _contains_token(filename_text, _EXPLICIT_TOP_PURPOSE_TOKENS):
        score += config.filename_top_view_score
        reasons.append("accepted: filename indicates top view")
    if _contains_token(classification_text, _MODEL_2D_TOKENS) or _contains_token(purpose_text, _MODEL_2D_TOKENS):
        score += config.model_2d_score
        reasons.append("accepted: purpose or asset metadata indicates 2dModel")
    if _contains_token(purpose_text, _EXPLICIT_TOP_PURPOSE_TOKENS):
        score += config.explicit_top_purpose_score
        reasons.append("accepted: explicit top-view purpose")

    rejection_reasons = _rejection_reasons(extension, content_type, metadata_text)
    if rejection_reasons:
        score -= config.rejection_penalty * len(rejection_reasons)
        reasons.extend(rejection_reasons)

    has_dwg_signal = extension == "dwg" or file_type == "dwg" or _is_dwg_content_type(content_type)
    has_non_filename_top_signal = _contains_token(descriptive_text, _TOP_VIEW_TOKENS) or _contains_token(purpose_text, _EXPLICIT_TOP_PURPOSE_TOKENS)
    has_filename_only_top_signal = _contains_token(filename_text, _EXPLICIT_TOP_PURPOSE_TOKENS) and not has_non_filename_top_signal
    status = "accepted"
    if rejection_reasons:
        status = "rejected"
    elif not has_dwg_signal:
        status = "rejected"
        reasons.append("rejected: candidate has no DWG extension, content type, or file type metadata")
    elif not has_non_filename_top_signal:
        status = "rejected"
        if has_filename_only_top_signal:
            reasons.append("rejected: top-view evidence appears only in filename")
        else:
            reasons.append("rejected: candidate has no top-view metadata")
    elif score < config.minimum_selectable_score:
        status = "rejected"
        reasons.append("rejected: score below minimum selectable threshold")

    return AssetCandidateEvaluation(
        candidate_identifier=identifier,
        score=score,
        status=status,
        reasons=tuple(reasons),
        candidate=candidate,
    )


def _rejection_reasons(extension: str, content_type: str, all_text: str) -> tuple[str, ...]:
    reasons: list[str] = []
    dwg_content_type = _is_dwg_content_type(content_type)
    if extension in _DOCUMENT_EXTENSIONS:
        reasons.append("rejected: PDF asset")
    if extension in _IMAGE_EXTENSIONS:
        reasons.append("rejected: image asset")
    if content_type == _PDF_CONTENT_TYPE:
        reasons.append("rejected: PDF content type")
    if content_type.startswith(_IMAGE_CONTENT_TYPE_PREFIX) and not dwg_content_type:
        reasons.append("rejected: image content type")
    for token in _REJECTION_TOKENS:
        if token in all_text:
            reasons.append(f"rejected: metadata contains {token}")
            break
    if extension and extension not in {"dwg", *_DOCUMENT_EXTENSIONS, *_IMAGE_EXTENSIONS}:
        reasons.append("rejected: non-DWG file extension")
    return tuple(reasons)


def _candidate_identifier(candidate: Mapping[str, Any], index: int) -> str:
    identifier = _first_text(candidate, _ASSET_ID_KEYS)
    if identifier is not None:
        return identifier
    filename = _first_text(candidate, _FILENAME_KEYS)
    if filename is not None:
        return filename
    return f"candidate-{index}"


def _extension_from(candidate: Mapping[str, Any]) -> str:
    name = _first_text(candidate, _FILENAME_KEYS)
    if name:
        suffix = PurePosixPath(name.replace("\\", "/")).suffix.lower().lstrip(".")
        if suffix:
            return suffix
    url = _first_text(candidate, _URL_KEYS)
    if url:
        clean_url = url.split("?", 1)[0].split("#", 1)[0]
        suffix = PurePosixPath(clean_url.replace("\\", "/")).suffix.lower().lstrip(".")
        if suffix:
            return suffix
    file_type = _first_text(candidate, _FILE_TYPE_KEYS)
    return (file_type or "").strip().lower().lstrip(".")


def _first_text(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return None


def _joined_text(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    values = [str(mapping[key]).strip().lower() for key in keys if key in mapping and str(mapping[key]).strip()]
    return " ".join(values)


def _contains_token(text: str, tokens: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in tokens)


def _normalize_content_type(content_type: str | None) -> str:
    if content_type is None:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def _is_dwg_content_type(content_type: str) -> bool:
    lowered = _normalize_content_type(content_type)
    return any(token in lowered for token in _DWG_CONTENT_TYPE_TOKENS)
