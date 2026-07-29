"""Basic Stage 12.5 desktop operator GUI."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QSettings, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from gebal_cad_normalizer.reporting import run_reporting_pipeline


UNIT_OPTIONS = ("", "mm", "cm", "m", "in")
STATUS_LABELS = {"pass": "pass", "pass_with_warnings": "warnings", "review_required": "review", "fail": "fail"}
STAGE12_RUNNER: Callable[..., dict[str, Any]] = run_reporting_pipeline


@dataclass(frozen=True)
class GuiPipelineConfig:
    json_path: Path
    input_path: Path
    output_dir: Path
    oda_exe: Path | None = None
    vendor_profile: str | None = None
    unit: str | None = None
    export_dwg: bool = False
    allow_overwrite: bool = False

    def kwargs(self) -> dict[str, Any]:
        return {
            "json_path": self.json_path,
            "input_path": self.input_path,
            "output_dir": self.output_dir,
            "oda_exe": self.oda_exe,
            "vendor_profile": self.vendor_profile,
            "unit": self.unit,
            "export_dwg": self.export_dwg,
            "allow_overwrite": self.allow_overwrite,
        }


class PipelineWorker(QThread):
    stage_changed = Signal(str)
    log_line = Signal(str)
    succeeded = Signal(dict)
    failed = Signal(str)

    def __init__(self, config: GuiPipelineConfig, runner: Callable[..., dict[str, Any]] = STAGE12_RUNNER) -> None:
        super().__init__()
        self.config = config
        self.runner = runner

    def run(self) -> None:
        try:
            self.stage_changed.emit("Stage 12 reporting pipeline")
            self.log_line.emit("Starting Stage 12 reporting pipeline.")
            manifest = self.runner(**self.config.kwargs(), hooks={"prepare_input": lambda: self.stage_changed.emit("Preparing input CAD")})
            self.succeeded.emit(manifest)
        except Exception as exc:  # pragma: no cover - exercised through Qt signal tests
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class OperatorGui(QMainWindow):
    def __init__(
        self,
        *,
        runner: Callable[..., dict[str, Any]] = STAGE12_RUNNER,
        settings_path: Path | None = None,
        open_url: Callable[[QUrl], bool] | None = None,
    ) -> None:
        super().__init__()
        self.runner = runner
        self.settings = QSettings(str(settings_path), QSettings.IniFormat) if settings_path else QSettings("Gebal", "CAD Normalizer")
        self.open_url = open_url or QDesktopServices.openUrl
        self.worker: PipelineWorker | None = None
        self.last_manifest: dict[str, Any] | None = None
        self.last_package_path: Path | None = None

        self.setWindowTitle("Gebal CAD Normalizer")
        self.resize(900, 680)
        self._build_ui()
        self._load_settings()
        self._set_running(False)
        self._update_output_actions()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        form = QFormLayout()

        self.product_json = QLineEdit()
        self.input_cad = QLineEdit()
        self.output_dir = QLineEdit()
        self.oda_exe = QLineEdit()
        self.vendor_profile = QLineEdit("bluestone_playground")
        self.unit = QComboBox()
        self.unit.addItems(UNIT_OPTIONS)
        self.export_dwg = QCheckBox("Export normalized DWG")
        self.allow_overwrite = QCheckBox("Allow overwrite")

        form.addRow("Product JSON file", self._path_row(self.product_json, "json"))
        form.addRow("DWG/DXF file", self._path_row(self.input_cad, "cad"))
        form.addRow("Output folder", self._path_row(self.output_dir, "folder"))
        form.addRow("ODA executable", self._path_row(self.oda_exe, "exe"))
        form.addRow("Vendor profile", self.vendor_profile)
        form.addRow("Unit override", self.unit)
        form.addRow("", self.export_dwg)
        form.addRow("", self.allow_overwrite)
        layout.addLayout(form)

        actions = QHBoxLayout()
        self.run_button = QPushButton("Run pipeline")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setToolTip("Stage 12 does not expose safe cancellation yet.")
        self.open_folder_button = QPushButton("Open output folder")
        self.open_report_button = QPushButton("Open report.md")
        self.open_dxf_button = QPushButton("Open normalized DXF")
        self.open_svg_button = QPushButton("Open combined SVG")
        for button in (self.run_button, self.cancel_button, self.open_folder_button, self.open_report_button, self.open_dxf_button, self.open_svg_button):
            actions.addWidget(button)
        layout.addLayout(actions)

        self.stage_label = QLabel("Idle")
        self.status_label = QLabel("Not run")
        self.occurrence_label = QLabel("")
        self.path_label = QLabel("")
        self.error_label = QLabel("")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)

        layout.addWidget(QLabel("Current stage"))
        layout.addWidget(self.stage_label)
        layout.addWidget(self.progress)
        layout.addWidget(QLabel("Final status"))
        layout.addWidget(self.status_label)
        layout.addWidget(self.occurrence_label)
        layout.addWidget(self.path_label)
        layout.addWidget(self.error_label)
        layout.addWidget(self.log, stretch=1)
        self.setCentralWidget(root)

        self.run_button.clicked.connect(self.run_pipeline)
        self.open_folder_button.clicked.connect(lambda: self._open_path(self.last_package_path))
        self.open_report_button.clicked.connect(lambda: self._open_artifact("report.md"))
        self.open_dxf_button.clicked.connect(lambda: self._open_artifact("normalized_dxf"))
        self.open_svg_button.clicked.connect(lambda: self._open_artifact("combined_svg"))

    def _path_row(self, line: QLineEdit, mode: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        browse = QPushButton("Browse")
        browse.clicked.connect(lambda: self._browse(line, mode))
        layout.addWidget(line, stretch=1)
        layout.addWidget(browse)
        return row

    def _browse(self, line: QLineEdit, mode: str) -> None:
        if mode == "folder":
            value = QFileDialog.getExistingDirectory(self, "Select output folder", line.text())
        else:
            filters = {
                "json": "JSON files (*.json);;All files (*)",
                "cad": "CAD files (*.dwg *.dxf);;All files (*)",
                "exe": "Executables (*.exe);;All files (*)",
            }
            value, _ = QFileDialog.getOpenFileName(self, "Select file", line.text(), filters.get(mode, "All files (*)"))
        if value:
            line.setText(value)

    def make_config(self) -> GuiPipelineConfig:
        unit = self.unit.currentText().strip() or None
        profile = self.vendor_profile.text().strip() or None
        oda = self.oda_exe.text().strip()
        return GuiPipelineConfig(
            json_path=Path(self.product_json.text().strip()),
            input_path=Path(self.input_cad.text().strip()),
            output_dir=Path(self.output_dir.text().strip()),
            oda_exe=Path(oda) if oda else None,
            vendor_profile=profile,
            unit=unit,
            export_dwg=self.export_dwg.isChecked(),
            allow_overwrite=self.allow_overwrite.isChecked(),
        )

    def validate_inputs(self) -> list[str]:
        errors: list[str] = []
        if not self.product_json.text().strip() or not Path(self.product_json.text().strip()).is_file():
            errors.append("Product JSON file is required and must exist.")
        if not self.input_cad.text().strip() or not Path(self.input_cad.text().strip()).is_file():
            errors.append("DWG/DXF file is required and must exist.")
        if self.input_cad.text().strip() and Path(self.input_cad.text().strip()).suffix.lower() not in {".dwg", ".dxf"}:
            errors.append("DWG/DXF file must end in .dwg or .dxf.")
        if not self.output_dir.text().strip():
            errors.append("Output folder is required.")
        if self.oda_exe.text().strip() and not Path(self.oda_exe.text().strip()).is_file():
            errors.append("ODA executable path must exist when provided.")
        return errors

    def run_pipeline(self) -> None:
        errors = self.validate_inputs()
        if errors:
            self._set_error(" ".join(errors))
            QMessageBox.warning(self, "Missing required inputs", "\n".join(errors))
            return
        self._save_settings()
        self.last_manifest = None
        self.last_package_path = None
        self._update_output_actions()
        self._set_error("")
        self.log.clear()
        self.worker = PipelineWorker(self.make_config(), self.runner)
        self.worker.stage_changed.connect(self._set_stage)
        self.worker.log_line.connect(self._append_log)
        self.worker.succeeded.connect(self._handle_success)
        self.worker.failed.connect(self._handle_failure)
        self.worker.finished.connect(lambda: self._set_running(False))
        self._set_running(True)
        self.worker.start()

    def _handle_success(self, manifest: dict[str, Any]) -> None:
        self.last_manifest = manifest
        self.last_package_path = self.make_config().output_dir / str(manifest.get("occurrence_id", ""))
        status = STATUS_LABELS.get(str(manifest.get("overall_status")), str(manifest.get("overall_status", "unknown")))
        self.status_label.setText(status)
        self.occurrence_label.setText(f"Occurrence ID: {manifest.get('occurrence_id', '')}")
        self.path_label.setText(f"Output path: {self.last_package_path}")
        self._append_log(f"Finished with status: {status}.")
        self._update_output_actions()

    def _handle_failure(self, message: str) -> None:
        self.status_label.setText("fail")
        self._set_error(message)
        self._append_log(message)
        self._update_output_actions()

    def _set_running(self, running: bool) -> None:
        self.run_button.setEnabled(not running)
        self.progress.setRange(0, 0 if running else 1)
        if not running:
            self.progress.setValue(0)

    def _set_stage(self, text: str) -> None:
        self.stage_label.setText(text)
        self._append_log(text)

    def _set_error(self, text: str) -> None:
        self.error_label.setText(f"Error: {text}" if text else "")

    def _append_log(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _artifact_path(self, key: str) -> Path | None:
        if key == "report.md":
            return self.last_package_path / "report.md" if self.last_package_path else None
        if not self.last_manifest or not self.last_package_path:
            return None
        artifact = self.last_manifest.get("artifacts", {}).get(key, {})
        rel = artifact.get("path")
        return self.last_package_path / rel if rel else None

    def _update_output_actions(self) -> None:
        paths = {
            self.open_folder_button: self.last_package_path,
            self.open_report_button: self._artifact_path("report.md"),
            self.open_dxf_button: self._artifact_path("normalized_dxf"),
            self.open_svg_button: self._artifact_path("combined_svg"),
        }
        for button, path in paths.items():
            button.setEnabled(bool(path and path.exists()))

    def _open_artifact(self, key: str) -> None:
        self._open_path(self._artifact_path(key))

    def _open_path(self, path: Path | None) -> None:
        if path and path.exists():
            self.open_url(QUrl.fromLocalFile(str(path.resolve())))

    def _load_settings(self) -> None:
        self.product_json.setText(str(self.settings.value("product_json_path", "")))
        self.input_cad.setText(str(self.settings.value("cad_path", "")))
        self.output_dir.setText(str(self.settings.value("output_dir", "")))
        self.oda_exe.setText(str(self.settings.value("oda_exe", "")))
        self.vendor_profile.setText(str(self.settings.value("vendor_profile", "bluestone_playground")))
        self.unit.setCurrentText(str(self.settings.value("unit", "")))
        self.export_dwg.setChecked(str(self.settings.value("export_dwg", "false")).lower() == "true")
        self.allow_overwrite.setChecked(str(self.settings.value("allow_overwrite", "false")).lower() == "true")

    def _save_settings(self) -> None:
        self.settings.setValue("product_json_path", self.product_json.text().strip())
        self.settings.setValue("cad_path", self.input_cad.text().strip())
        self.settings.setValue("output_dir", self.output_dir.text().strip())
        self.settings.setValue("oda_exe", self.oda_exe.text().strip())
        self.settings.setValue("vendor_profile", self.vendor_profile.text().strip())
        self.settings.setValue("unit", self.unit.currentText().strip())
        self.settings.setValue("export_dwg", self.export_dwg.isChecked())
        self.settings.setValue("allow_overwrite", self.allow_overwrite.isChecked())
        self.settings.sync()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    window = OperatorGui()
    window.show()
    return int(app.exec())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
