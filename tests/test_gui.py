"""Tests for the Stage 12.5 operator GUI boundary."""

from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from gebal_cad_normalizer.gui import GuiPipelineConfig, OperatorGui


@pytest.fixture(scope="session")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _window(tmp_path: Path, runner=None) -> OperatorGui:
    return OperatorGui(runner=runner or (lambda **_kwargs: {}), settings_path=tmp_path / "settings.ini", open_url=lambda _url: True)


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    js = tmp_path / "product.json"
    cad = tmp_path / "source.dxf"
    out = tmp_path / "out"
    oda = tmp_path / "ODAFileConverter.exe"
    js.write_text('{"sku":"SKU1","secret":"do-not-store"}', encoding="utf-8")
    cad.write_text("0\nSECTION\n2\nEOF\n", encoding="utf-8")
    oda.write_text("exe", encoding="utf-8")
    return js, cad, out, oda


def _fill(window: OperatorGui, js: Path, cad: Path, out: Path, oda: Path | None = None) -> None:
    window.product_json.setText(str(js))
    window.input_cad.setText(str(cad))
    window.output_dir.setText(str(out))
    if oda:
        window.oda_exe.setText(str(oda))


def _wait(window: OperatorGui, app: QApplication, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while window.worker and window.worker.isRunning() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    if window.worker:
        window.worker.wait(1000)
    app.processEvents()


def test_initial_state(app: QApplication, tmp_path: Path) -> None:
    window = _window(tmp_path)
    assert window.run_button.isEnabled()
    assert not window.cancel_button.isEnabled()
    assert window.stage_label.text() == "Idle"
    assert window.status_label.text() == "Not run"
    assert not window.open_report_button.isEnabled()


def test_required_field_validation(app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window = _window(tmp_path)
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: shown.append("warning"))
    window.run_pipeline()
    assert "Product JSON file is required" in window.error_label.text()
    assert shown == ["warning"]


def test_config_mapping(app: QApplication, tmp_path: Path) -> None:
    js, cad, out, oda = _inputs(tmp_path)
    window = _window(tmp_path)
    _fill(window, js, cad, out, oda)
    window.vendor_profile.setText("bluestone_playground")
    window.unit.setCurrentText("mm")
    window.export_dwg.setChecked(True)
    window.allow_overwrite.setChecked(True)

    config = window.make_config()

    assert config == GuiPipelineConfig(
        json_path=js,
        input_path=cad,
        output_dir=out,
        oda_exe=oda,
        vendor_profile="bluestone_playground",
        unit="mm",
        export_dwg=True,
        allow_overwrite=True,
    )


def test_run_state_enable_disable_and_success_result(app: QApplication, tmp_path: Path) -> None:
    js, cad, out, _oda = _inputs(tmp_path)
    package = out / "SKU1_abcd"

    def runner(**kwargs):
        assert kwargs["json_path"] == js
        package.mkdir(parents=True)
        (package / "report.md").write_text("report", encoding="utf-8")
        (package / "normalized").mkdir()
        (package / "normalized" / "SKU1_normalized.dxf").write_text("dxf", encoding="utf-8")
        (package / "svg").mkdir()
        (package / "svg" / "combined.svg").write_text("svg", encoding="utf-8")
        return {
            "occurrence_id": "SKU1_abcd",
            "overall_status": "pass_with_warnings",
            "artifacts": {"normalized_dxf": {"path": "normalized/SKU1_normalized.dxf"}, "combined_svg": {"path": "svg/combined.svg"}},
        }

    window = _window(tmp_path, runner)
    _fill(window, js, cad, out)
    window.run_pipeline()
    assert not window.run_button.isEnabled()
    _wait(window, app)

    assert window.run_button.isEnabled()
    assert window.status_label.text() == "warnings"
    assert "SKU1_abcd" in window.occurrence_label.text()
    assert window.open_report_button.isEnabled()
    assert window.open_dxf_button.isEnabled()
    assert window.open_svg_button.isEnabled()


def test_review_and_fail_result_mapping(app: QApplication, tmp_path: Path) -> None:
    window = _window(tmp_path)
    window.output_dir.setText(str(tmp_path / "out"))
    window._handle_success({"occurrence_id": "r1", "overall_status": "review_required", "artifacts": {}})
    assert window.status_label.text() == "review"
    window._handle_success({"occurrence_id": "f1", "overall_status": "fail", "artifacts": {}})
    assert window.status_label.text() == "fail"


def test_exception_handling(app: QApplication, tmp_path: Path) -> None:
    js, cad, out, _oda = _inputs(tmp_path)

    def runner(**_kwargs):
        raise RuntimeError("pipeline exploded")

    window = _window(tmp_path, runner)
    _fill(window, js, cad, out)
    window.run_pipeline()
    _wait(window, app)

    assert window.status_label.text() == "fail"
    assert "pipeline exploded" in window.error_label.text()


def test_output_action_availability(app: QApplication, tmp_path: Path) -> None:
    package = tmp_path / "out" / "occ1"
    (package / "normalized").mkdir(parents=True)
    (package / "report.md").write_text("report", encoding="utf-8")
    (package / "normalized" / "occ1_normalized.dxf").write_text("dxf", encoding="utf-8")
    window = _window(tmp_path)
    window.output_dir.setText(str(tmp_path / "out"))

    window._handle_success({"occurrence_id": "occ1", "overall_status": "pass", "artifacts": {"normalized_dxf": {"path": "normalized/occ1_normalized.dxf"}}})

    assert window.open_folder_button.isEnabled()
    assert window.open_report_button.isEnabled()
    assert window.open_dxf_button.isEnabled()
    assert not window.open_svg_button.isEnabled()


def test_settings_persistence_without_product_contents(app: QApplication, tmp_path: Path) -> None:
    js, cad, out, oda = _inputs(tmp_path)
    settings_path = tmp_path / "settings.ini"
    window = OperatorGui(settings_path=settings_path, open_url=lambda _url: True)
    _fill(window, js, cad, out, oda)
    window.unit.setCurrentText("cm")
    window.export_dwg.setChecked(True)
    window._save_settings()

    reopened = OperatorGui(settings_path=settings_path, open_url=lambda _url: True)

    assert reopened.product_json.text() == str(js)
    assert reopened.input_cad.text() == str(cad)
    assert reopened.unit.currentText() == "cm"
    assert reopened.export_dwg.isChecked()
    assert "do-not-store" not in settings_path.read_text(encoding="utf-8")


def test_no_cad_logic_inside_gui_module() -> None:
    source = Path("src/gebal_cad_normalizer/gui.py").read_text(encoding="utf-8")
    forbidden = ("inventory_dxf", "canonicalize_dxf", "convert_regions", "classify_layers", "rewrite_layers", "measure_geometry", "validate_json_against_cad", "ezdxf")
    assert all(name not in source for name in forbidden)
