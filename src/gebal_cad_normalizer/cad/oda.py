"""Safe wrapper around ODA File Converter folder-based CLI."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from enum import Enum
from pathlib import Path

from pydantic import Field, model_validator

from gebal_cad_normalizer.config import CadNormalizerConfig
from gebal_cad_normalizer.exceptions import OdaConversionError
from gebal_cad_normalizer.models import StrictModel

DEFAULT_ODA_ENV_VAR = "GEBAL_ODA_FILE_CONVERTER"
DEFAULT_TARGET_VERSION = "R2013"
_TARGET_VERSION_CLI_TOKENS = {
    "R2010": "ACAD2010",
    "R2013": "ACAD2013",
    "R2018": "ACAD2018",
    "ACAD2010": "ACAD2010",
    "ACAD2013": "ACAD2013",
    "ACAD2018": "ACAD2018",
}
_SUPPORTED_PAIRS = {
    (".dwg", ".dxf"): ("DXF", "dwg_to_dxf"),
    (".dxf", ".dwg"): ("DWG", "dxf_to_dwg"),
}


class OdaDirection(str, Enum):
    """Supported ODA conversion directions."""

    DWG_TO_DXF = "dwg_to_dxf"
    DXF_TO_DWG = "dxf_to_dwg"


class OdaConversionErrorCode(str, Enum):
    """Stable error codes emitted by the ODA wrapper."""

    EXECUTABLE_MISSING = "executable_missing"
    INVALID_EXTENSION = "invalid_extension"
    SOURCE_MISSING = "source_missing"
    TIMEOUT = "timeout"
    PROCESS_FAILED = "process_failed"
    OUTPUT_MISSING = "output_missing"
    OUTPUT_EMPTY = "output_empty"
    PROMOTION_FAILED = "promotion_failed"
    UNSUPPORTED_TARGET_VERSION = "unsupported_target_version"


class OdaFileConverterError(OdaConversionError):
    """Raised when ODA conversion fails with a stable error code."""

    def __init__(
        self,
        code: OdaConversionErrorCode,
        message: str,
        *,
        exit_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
        elapsed_seconds: float = 0.0,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.elapsed_seconds = elapsed_seconds


class OdaConversionRequest(StrictModel):
    """Input contract for one ODA conversion."""

    source_path: Path
    destination_path: Path
    oda_executable_path: Path | None = None
    target_version: str = Field(default=DEFAULT_TARGET_VERSION, min_length=1)
    timeout_seconds: float = Field(default=120.0, gt=0)

    @model_validator(mode="after")
    def validate_extensions(self) -> "OdaConversionRequest":
        """Reject unsupported conversion directions at the API boundary."""

        _direction_and_output_type(self.source_path, self.destination_path)
        _oda_cli_target_version(self.target_version)
        return self


class OdaConversionResult(StrictModel):
    """Captured outcome from a successful ODA conversion."""

    source_path: Path
    destination_path: Path
    direction: OdaDirection
    requested_cad_version: str
    exit_code: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    output_size_bytes: int


class OdaConverter:
    """Convert one DWG/DXF file by isolating ODA's folder-based interface."""

    def __init__(
        self,
        oda_executable_path: Path | str | None = None,
        *,
        config: CadNormalizerConfig | None = None,
        timeout_seconds: float = 120.0,
        target_version: str | None = None,
    ) -> None:
        self.oda_executable_path = oda_executable_path or (config.oda_executable_path if config is not None else None)
        self.timeout_seconds = timeout_seconds
        self.target_version = target_version or (config.output_dxf_version if config is not None else DEFAULT_TARGET_VERSION)

    def convert(self, request: OdaConversionRequest) -> OdaConversionResult:
        """Run one supported conversion and atomically promote the result."""

        source_path = Path(request.source_path)
        destination_path = Path(request.destination_path)
        direction, output_type = _direction_and_output_type(source_path, destination_path)
        executable = resolve_oda_executable(request.oda_executable_path or self.oda_executable_path)
        if not source_path.is_file():
            raise OdaFileConverterError(OdaConversionErrorCode.SOURCE_MISSING, f"Source CAD file does not exist: {source_path}")

        target_version = request.target_version if "target_version" in request.model_fields_set else self.target_version
        oda_target_version = _oda_cli_target_version(target_version)
        timeout_seconds = request.timeout_seconds or self.timeout_seconds
        started = time.monotonic()

        try:
            with tempfile.TemporaryDirectory(prefix="gebal_oda_") as temp_root:
                temp_path = Path(temp_root)
                input_dir = temp_path / "input"
                output_dir = temp_path / "output"
                input_dir.mkdir()
                output_dir.mkdir()
                isolated_source = input_dir / source_path.name
                shutil.copy2(source_path, isolated_source)
                expected_temp_output = output_dir / f"{isolated_source.stem}{destination_path.suffix.lower()}"

                command = [str(executable), str(input_dir), str(output_dir), oda_target_version, output_type, "0", "1"]
                completed = _run_oda(command, timeout_seconds)
                elapsed = time.monotonic() - started

                if completed.returncode != 0:
                    raise OdaFileConverterError(
                        OdaConversionErrorCode.PROCESS_FAILED,
                        f"ODA File Converter exited with code {completed.returncode}.",
                        exit_code=completed.returncode,
                        stdout=completed.stdout,
                        stderr=completed.stderr,
                        elapsed_seconds=elapsed,
                    )
                if not expected_temp_output.exists():
                    raise OdaFileConverterError(
                        OdaConversionErrorCode.OUTPUT_MISSING,
                        f"ODA did not create expected output: {expected_temp_output.name}",
                        exit_code=completed.returncode,
                        stdout=completed.stdout,
                        stderr=completed.stderr,
                        elapsed_seconds=elapsed,
                    )
                output_size = expected_temp_output.stat().st_size
                if output_size <= 0:
                    raise OdaFileConverterError(
                        OdaConversionErrorCode.OUTPUT_EMPTY,
                        f"ODA created an empty output: {expected_temp_output.name}",
                        exit_code=completed.returncode,
                        stdout=completed.stdout,
                        stderr=completed.stderr,
                        elapsed_seconds=elapsed,
                    )

                _promote_output(expected_temp_output, destination_path)
                return OdaConversionResult(
                    source_path=source_path,
                    destination_path=destination_path,
                    direction=direction,
                    requested_cad_version=target_version,
                    exit_code=completed.returncode,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                    elapsed_seconds=elapsed,
                    output_size_bytes=output_size,
                )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - started
            raise OdaFileConverterError(
                OdaConversionErrorCode.TIMEOUT,
                f"ODA File Converter timed out after {timeout_seconds} seconds.",
                stdout=_decode_output(exc.stdout),
                stderr=_decode_output(exc.stderr),
                elapsed_seconds=elapsed,
            ) from exc


def resolve_oda_executable(
    explicit_path: Path | str | None = None,
    *,
    config: CadNormalizerConfig | None = None,
    env_var: str = DEFAULT_ODA_ENV_VAR,
) -> Path:
    """Resolve and validate the ODA executable path."""

    candidate = explicit_path or (config.oda_executable_path if config is not None else None) or os.environ.get(env_var)
    if candidate is None or str(candidate).strip() == "":
        raise OdaFileConverterError(
            OdaConversionErrorCode.EXECUTABLE_MISSING,
            f"ODA executable path is required via explicit path, config, or {env_var}.",
        )
    path = Path(candidate)
    if not path.is_file():
        raise OdaFileConverterError(OdaConversionErrorCode.EXECUTABLE_MISSING, f"ODA executable is missing or invalid: {path}")
    return path


def _oda_cli_target_version(target_version: str) -> str:
    normalized = target_version.strip().upper()
    if normalized not in _TARGET_VERSION_CLI_TOKENS:
        supported = ", ".join(sorted(_TARGET_VERSION_CLI_TOKENS))
        raise OdaFileConverterError(
            OdaConversionErrorCode.UNSUPPORTED_TARGET_VERSION,
            f"Unsupported ODA target version: {target_version}. Supported versions: {supported}.",
        )
    return _TARGET_VERSION_CLI_TOKENS[normalized]



def _direction_and_output_type(source_path: Path, destination_path: Path) -> tuple[OdaDirection, str]:
    pair = (source_path.suffix.lower(), destination_path.suffix.lower())
    if pair not in _SUPPORTED_PAIRS:
        raise OdaFileConverterError(
            OdaConversionErrorCode.INVALID_EXTENSION,
            f"Unsupported ODA conversion extension pair: {source_path.suffix} -> {destination_path.suffix}",
        )
    output_type, direction = _SUPPORTED_PAIRS[pair]
    return OdaDirection(direction), output_type


def _run_oda(command: list[str], timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        shell=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )


def _promote_output(converted_path: Path, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temp_destination = destination_path.with_name(f".{destination_path.name}.oda_tmp")
    try:
        if temp_destination.exists():
            temp_destination.unlink()
        shutil.move(str(converted_path), str(temp_destination))
        os.replace(temp_destination, destination_path)
    except Exception as exc:
        try:
            temp_destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise OdaFileConverterError(
            OdaConversionErrorCode.PROMOTION_FAILED,
            f"Failed to promote converted output to destination: {exc}",
        ) from exc


def _decode_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)




