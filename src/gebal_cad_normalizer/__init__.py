"""Public package metadata and typed CAD input contracts."""

from gebal_cad_normalizer.assets import (
    CadAssetDownloader,
    DownloadResult,
    SourceManifest,
    SourceStatus,
    SourceVersionManager,
    VersionUpdateResult,
)
from gebal_cad_normalizer.models import (
    AdapterResult,
    CadAssetDescriptor,
    CadProcessingRequest,
    ExpectedDimensions,
    ExpectedSafetyData,
    InputQualityIssue,
    ProductIdentity,
    ValidationStatus,
)

__version__ = "0.1.0"

__all__ = [
    "AdapterResult",
    "CadAssetDownloader",
    "DownloadResult",
    "SourceManifest",
    "SourceStatus",
    "SourceVersionManager",
    "VersionUpdateResult",
    "CadAssetDescriptor",
    "CadProcessingRequest",
    "ExpectedDimensions",
    "ExpectedSafetyData",
    "InputQualityIssue",
    "ProductIdentity",
    "ValidationStatus",
    "__version__",
]


