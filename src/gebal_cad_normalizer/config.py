"""Configuration types for future CAD normalization stages."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CadNormalizerConfig:
    """Runtime settings shared by later pipeline stages."""

    warehouse_root: Path
    oda_executable_path: Path | None = None
    output_dxf_version: str = "R2013"
    archive_limit: int = 3
    dimensional_tolerance_percent: float = 1.0
    dimensional_tolerance_mm: float = 5.0
    area_tolerance_percent: float = 2.0
    spline_tessellation_tolerance_mm: float = 2.0
    retain_normalized_dxf: bool = True
    retain_normalized_dwg: bool = True
    retain_work_files: bool = False
    allow_ai_layer_assistance: bool = False
