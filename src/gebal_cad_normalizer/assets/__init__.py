"""Asset download and source-version management."""

from gebal_cad_normalizer.assets.downloader import CadAssetDownloader, DownloadResult
from gebal_cad_normalizer.assets.version_manager import (
    SourceManifest,
    SourceStatus,
    SourceVersionManager,
    VersionUpdateResult,
)

__all__ = [
    "CadAssetDownloader",
    "DownloadResult",
    "SourceManifest",
    "SourceStatus",
    "SourceVersionManager",
    "VersionUpdateResult",
]
