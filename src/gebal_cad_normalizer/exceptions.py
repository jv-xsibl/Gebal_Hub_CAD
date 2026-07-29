"""Exception hierarchy for CAD normalization failures."""


class CadNormalizerError(Exception):
    """Base exception for package-specific failures."""


class VendorPayloadError(CadNormalizerError):
    """Raised when vendor product data cannot be interpreted."""


class CadAssetNotFoundError(CadNormalizerError):
    """Raised when a required top-view CAD asset cannot be found."""


class DownloadError(CadNormalizerError):
    """Raised when source CAD download fails."""


class VersioningError(CadNormalizerError):
    """Raised when source file version management fails."""


class OdaConversionError(CadNormalizerError):
    """Raised when ODA File Converter fails."""


class DxfReadError(CadNormalizerError):
    """Raised when a DXF file cannot be inspected."""


class RegionConversionError(CadNormalizerError):
    """Raised when REGION geometry cannot be converted safely."""


class LayerNormalizationError(CadNormalizerError):
    """Raised when layer normalization fails."""


class CadValidationError(CadNormalizerError):
    """Raised when CAD validation cannot complete."""


class OutputWriteError(CadNormalizerError):
    """Raised when normalized outputs or reports cannot be written."""
