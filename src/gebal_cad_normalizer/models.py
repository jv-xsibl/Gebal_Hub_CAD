"""Typed CAD input contracts for adapter and pipeline boundaries."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ValidationStatus(str, Enum):
    """Validation outcomes described by the product requirements."""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    UNVERIFIABLE = "unverifiable"


IssueSeverity = Literal["warning", "fail", "unverifiable"]
IssueCode = Literal[
    "missing_sku",
    "missing_top_view_cad",
    "invalid_cad_asset_type",
    "missing_dimension",
    "invalid_dimension",
    "missing_safety_data",
    "invalid_safety_data",
    "ambiguous_cad_asset",
    "blank_string",
    "missing_local_file",
    "invalid_local_file",
    "filename_sku_mismatch",
    "comment_tolerant_json",
    "unverifiable_from_top_view",
]


class StrictModel(BaseModel):
    """Base model that rejects unknown fields at CAD input boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductIdentity(StrictModel):
    """Minimum product identity needed to track a CAD source."""

    sku: str = Field(min_length=1)
    product_name: str | None = None
    vendor_source_identifier: str | None = None


class CadAssetDescriptor(StrictModel):
    """Metadata used to identify a top-view DWG asset."""

    url: str | None = Field(default=None, min_length=1)
    local_path: str | None = Field(default=None, min_length=1)
    file_name: str = Field(min_length=1)
    file_type: str | None = None
    media_id: str | None = None
    vendor_revision: str | None = None
    vendor_updated_at: str | int | None = None
    asset_name: str | None = None
    description: str | None = None
    content_type: str | None = None
    document_information: str | None = None
    purpose: str | None = None
    vendor_asset_classification: str | None = None

    @model_validator(mode="after")
    def require_location(self) -> "CadAssetDescriptor":
        """Require either a remote URL or explicit local path."""

        if self.url is None and self.local_path is None:
            raise ValueError("CadAssetDescriptor requires url or local_path")
        return self


class ExpectedDimensions(StrictModel):
    """Product dimensions expected from vendor or unified product data."""

    length_mm: float = Field(gt=0)
    width_mm: float = Field(gt=0)
    height_mm: float | None = Field(default=None, gt=0)


class ExpectedSafetyData(StrictModel):
    """Safety dimensions and areas expected from product data."""

    safety_zone_length_mm: float | None = Field(default=None, gt=0)
    safety_zone_width_mm: float | None = Field(default=None, gt=0)
    falling_space_area_m2: float | None = Field(default=None, gt=0)
    impact_area_m2: float | None = Field(default=None, gt=0)
    free_fall_height_mm: float | None = Field(default=None, gt=0)


class InputQualityIssue(StrictModel):
    """Stable issue emitted by adapters before any CAD processing exists."""

    code: IssueCode
    message: str = Field(min_length=1)
    severity: IssueSeverity
    field_path: str | None = None


class CadProcessingRequest(StrictModel):
    """Normalized CAD-focused request accepted by future processing stages."""

    product: ProductIdentity
    top_view_cad: CadAssetDescriptor
    expected_dimensions: ExpectedDimensions | None = None
    expected_safety: ExpectedSafetyData | None = None


class AdapterResult(StrictModel):
    """Result of parsing vendor or unified product input into CAD contracts."""

    request: CadProcessingRequest | None = None
    issues: tuple[InputQualityIssue, ...] = ()
