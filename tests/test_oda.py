"""Tests for Stage 4 ODA File Converter wrapper."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gebal_cad_normalizer.config import CadNormalizerConfig
from gebal_cad_normalizer.cad import (
    OdaConversionErrorCode,
    OdaConversionRequest,
    OdaConverter,
    OdaDirection,
    OdaFileConverterError,
    resolve_oda_executable,
)


def _exe(tmp_path: Path) -> Path:
    path = tmp_path / "ODAFileConverter.exe"
    path.write_bytes(b"exe")
    return path


def _source(tmp_path: Path, name: str = "source.dwg", content: bytes = b"AC1027-source") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def _completed(stdout: str = "ok", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["oda"], returncode=returncode, stdout=stdout, stderr=stderr)


def _mock_success(monkeypatch: pytest.MonkeyPatch, suffix: str, content: bytes = b"converted") -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"args": args, "kwargs": kwargs})
        output_dir = Path(args[2])
        input_file = next(Path(args[1]).iterdir())
        (output_dir / f"{input_file.stem}{suffix}").write_bytes(content)
        return _completed()

    monkeypatch.setattr("gebal_cad_normalizer.cad.oda.subprocess.run", fake_run)
    return calls


def test_missing_oda_executable(tmp_path: Path) -> None:
    with pytest.raises(OdaFileConverterError) as excinfo:
        resolve_oda_executable(tmp_path / "missing.exe")

    assert excinfo.value.code == OdaConversionErrorCode.EXECUTABLE_MISSING


def test_missing_source_file(tmp_path: Path) -> None:
    converter = OdaConverter(_exe(tmp_path))

    with pytest.raises(OdaFileConverterError) as excinfo:
        converter.convert(OdaConversionRequest(source_path=tmp_path / "missing.dwg", destination_path=tmp_path / "out.dxf"))

    assert excinfo.value.code == OdaConversionErrorCode.SOURCE_MISSING


def test_invalid_extension_pair(tmp_path: Path) -> None:
    with pytest.raises(OdaFileConverterError) as excinfo:
        OdaConversionRequest(source_path=tmp_path / "source.dwg", destination_path=tmp_path / "out.pdf")

    assert excinfo.value.code == OdaConversionErrorCode.INVALID_EXTENSION


def test_successful_dwg_to_dxf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_success(monkeypatch, ".dxf", b"dxf-data")
    source = _source(tmp_path, "in.dwg")
    destination = tmp_path / "out.dxf"

    result = OdaConverter(_exe(tmp_path)).convert(OdaConversionRequest(source_path=source, destination_path=destination))

    assert result.direction == OdaDirection.DWG_TO_DXF
    assert result.source_path == source
    assert result.destination_path == destination
    assert result.requested_cad_version == "R2013"
    assert result.exit_code == 0
    assert result.stdout == "ok"
    assert result.output_size_bytes == len(b"dxf-data")
    assert destination.read_bytes() == b"dxf-data"
    assert calls[0]["args"][3:5] == ["ACAD2013", "DXF"]


def test_successful_dxf_to_dwg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_success(monkeypatch, ".dwg", b"dwg-data")
    source = _source(tmp_path, "in.dxf", b"dxf-source")
    destination = tmp_path / "out.dwg"

    result = OdaConverter(_exe(tmp_path)).convert(OdaConversionRequest(source_path=source, destination_path=destination, target_version="R2018"))

    assert result.direction == OdaDirection.DXF_TO_DWG
    assert result.requested_cad_version == "R2018"
    assert destination.read_bytes() == b"dwg-data"



def test_friendly_r2013_invokes_oda_with_acad2013(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_success(monkeypatch, ".dxf")
    source = _source(tmp_path, "in.dwg")

    result = OdaConverter(_exe(tmp_path)).convert(
        OdaConversionRequest(source_path=source, destination_path=tmp_path / "out.dxf", target_version="R2013")
    )

    assert result.requested_cad_version == "R2013"
    assert calls[0]["args"][3] == "ACAD2013"


def test_acad_target_version_token_passes_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_success(monkeypatch, ".dxf")
    source = _source(tmp_path, "in.dwg")

    result = OdaConverter(_exe(tmp_path)).convert(
        OdaConversionRequest(source_path=source, destination_path=tmp_path / "out.dxf", target_version="ACAD2018")
    )

    assert result.requested_cad_version == "ACAD2018"
    assert calls[0]["args"][3] == "ACAD2018"


def test_unsupported_target_version_rejected(tmp_path: Path) -> None:
    with pytest.raises(OdaFileConverterError) as excinfo:
        OdaConversionRequest(source_path=tmp_path / "source.dwg", destination_path=tmp_path / "out.dxf", target_version="R2025")

    assert excinfo.value.code == OdaConversionErrorCode.UNSUPPORTED_TARGET_VERSION
    assert "Unsupported ODA target version" in str(excinfo.value)



def test_converter_config_target_version_is_normalized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_success(monkeypatch, ".dxf")
    config = CadNormalizerConfig(warehouse_root=tmp_path / "warehouse", output_dxf_version="R2018")

    result = OdaConverter(_exe(tmp_path), config=config).convert(
        OdaConversionRequest(source_path=_source(tmp_path), destination_path=tmp_path / "out.dxf")
    )

    assert result.requested_cad_version == "R2018"
    assert calls[0]["args"][3] == "ACAD2018"


def test_timeout_handling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args, timeout=1, output=b"partial", stderr=b"slow")

    monkeypatch.setattr("gebal_cad_normalizer.cad.oda.subprocess.run", fake_run)

    with pytest.raises(OdaFileConverterError) as excinfo:
        OdaConverter(_exe(tmp_path)).convert(OdaConversionRequest(source_path=_source(tmp_path), destination_path=tmp_path / "out.dxf", timeout_seconds=1))

    assert excinfo.value.code == OdaConversionErrorCode.TIMEOUT
    assert excinfo.value.stdout == "partial"
    assert excinfo.value.stderr == "slow"


def test_non_zero_exit_handling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gebal_cad_normalizer.cad.oda.subprocess.run", lambda *args, **kwargs: _completed("bad", "failed", 7))

    with pytest.raises(OdaFileConverterError) as excinfo:
        OdaConverter(_exe(tmp_path)).convert(OdaConversionRequest(source_path=_source(tmp_path), destination_path=tmp_path / "out.dxf"))

    assert excinfo.value.code == OdaConversionErrorCode.PROCESS_FAILED
    assert excinfo.value.exit_code == 7
    assert excinfo.value.stdout == "bad"
    assert excinfo.value.stderr == "failed"


def test_exit_zero_but_output_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gebal_cad_normalizer.cad.oda.subprocess.run", lambda *args, **kwargs: _completed())

    with pytest.raises(OdaFileConverterError) as excinfo:
        OdaConverter(_exe(tmp_path)).convert(OdaConversionRequest(source_path=_source(tmp_path), destination_path=tmp_path / "out.dxf"))

    assert excinfo.value.code == OdaConversionErrorCode.OUTPUT_MISSING


def test_empty_output_rejection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_success(monkeypatch, ".dxf", b"")

    with pytest.raises(OdaFileConverterError) as excinfo:
        OdaConverter(_exe(tmp_path)).convert(OdaConversionRequest(source_path=_source(tmp_path), destination_path=tmp_path / "out.dxf"))

    assert excinfo.value.code == OdaConversionErrorCode.OUTPUT_EMPTY


def test_existing_destination_preserved_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = tmp_path / "out.dxf"
    destination.write_bytes(b"existing")
    monkeypatch.setattr("gebal_cad_normalizer.cad.oda.subprocess.run", lambda *args, **kwargs: _completed(returncode=9))

    with pytest.raises(OdaFileConverterError):
        OdaConverter(_exe(tmp_path)).convert(OdaConversionRequest(source_path=_source(tmp_path), destination_path=destination))

    assert destination.read_bytes() == b"existing"


def test_successful_destination_replacement_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = tmp_path / "out.dxf"
    destination.write_bytes(b"existing")
    calls = _mock_success(monkeypatch, ".dxf", b"new")

    OdaConverter(_exe(tmp_path)).convert(OdaConversionRequest(source_path=_source(tmp_path), destination_path=destination))

    assert destination.read_bytes() == b"new"
    assert not (tmp_path / ".out.dxf.oda_tmp").exists()
    assert calls


def test_temp_folders_cleaned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen_temp_root: list[Path] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        input_dir = Path(args[1])
        seen_temp_root.append(input_dir.parent)
        (Path(args[2]) / "source.dxf").write_bytes(b"converted")
        return _completed()

    monkeypatch.setattr("gebal_cad_normalizer.cad.oda.subprocess.run", fake_run)
    OdaConverter(_exe(tmp_path)).convert(OdaConversionRequest(source_path=_source(tmp_path), destination_path=tmp_path / "out.dxf"))

    assert seen_temp_root
    assert not seen_temp_root[0].exists()


def test_subprocess_uses_argument_list_and_shell_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_success(monkeypatch, ".dxf")

    OdaConverter(_exe(tmp_path)).convert(OdaConversionRequest(source_path=_source(tmp_path), destination_path=tmp_path / "out.dxf"))

    assert isinstance(calls[0]["args"], list)
    assert calls[0]["kwargs"]["shell"] is False
    assert calls[0]["kwargs"]["capture_output"] is True
    assert calls[0]["kwargs"]["check"] is False


def test_source_input_is_not_mutated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_success(monkeypatch, ".dxf")
    source = _source(tmp_path, content=b"original-source")
    before = source.read_bytes()

    OdaConverter(_exe(tmp_path)).convert(OdaConversionRequest(source_path=source, destination_path=tmp_path / "out.dxf"))

    assert source.read_bytes() == before
