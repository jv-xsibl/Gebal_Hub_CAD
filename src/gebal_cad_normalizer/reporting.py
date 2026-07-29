"""Stage 12 integrated pipeline packaging and reporting."""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import queue as queue_module
import re
import shutil
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Mapping

from gebal_cad_normalizer.adapters import BluestoneAdapter, UnifiedAdapter
from gebal_cad_normalizer.asset_selector import select_top_view_asset
from gebal_cad_normalizer.cad import (
    LayerClassificationConfig,
    MeasurementConfig,
    OdaConversionRequest,
    OdaConverter,
    SvgExportConfig,
    canonicalize_dxf,
    classify_layers,
    convert_regions,
    export_layer_svgs,
    inventory_dxf,
    measure_geometry,
    rewrite_layers,
    validate_json_against_cad,
    write_canonical_json,
    write_classification_json,
    write_inventory_json,
    write_measurement_json,
    write_rewrite_json,
    write_validation_json,
)
from gebal_cad_normalizer.cad.oda import OdaFileConverterError
from gebal_cad_normalizer.models import CadProcessingRequest

STATUS_ORDER = {"pass": 0, "pass_with_warnings": 1, "review_required": 2, "fail": 3}
DEFAULT_STAGE_TIMEOUTS_SECONDS = {
    "input_conversion": 180.0,
    "canonicalization": 180.0,
    "region_conversion": 120.0,
    "svg_export": 180.0,
    "measurement": 180.0,
    "dwg_export": 180.0,
}
SKU_RE = re.compile(r"(?i)(?<![A-Z0-9])([A-Z]?\d{5,6}_?M?|\d{5,6})(?![A-Z0-9])")


def run_reporting_pipeline(
    *,
    json_path: Path | str,
    input_path: Path | str,
    output_dir: Path | str,
    oda_exe: Path | str | None = None,
    vendor_profile: str | None = None,
    unit: str | None = None,
    export_dwg: bool = False,
    allow_overwrite: bool = False,
    hooks: Mapping[str, Callable[[], None]] | None = None,
    stage_timeouts_seconds: Mapping[str, float | None] | None = None,
) -> dict[str, Any]:
    """Run the existing CAD stages and atomically publish a durable package."""

    started = time.perf_counter()
    source_json = Path(json_path)
    source_cad = Path(input_path)
    root = Path(output_dir)
    hooks = hooks or {}
    stage_timeouts = dict(DEFAULT_STAGE_TIMEOUTS_SECONDS)
    if stage_timeouts_seconds:
        for key, value in stage_timeouts_seconds.items():
            stage_timeouts[key] = None if value is None else float(value)
    payload = _read_json(source_json)
    request, adapter_issues = _parse_request(payload)
    sku = _sku(request, payload) or source_cad.stem
    occurrence_id = _occurrence_id(sku, source_json, source_cad)
    final_dir = root / occurrence_id
    staging = root / f".{occurrence_id}.stage12_tmp"
    if final_dir.exists() and not allow_overwrite:
        raise FileExistsError(f"Output package already exists: {final_dir}")

    source_json_sha = _sha256(source_json)
    source_cad_sha = _sha256(source_cad)
    state: dict[str, Any] = {
        "stage": "12",
        "occurrence_id": occurrence_id,
        "sku": sku,
        "overall_status": "fail",
        "timings_seconds": {},
        "stage_timeouts_seconds": {key: value for key, value in sorted(stage_timeouts.items()) if value is not None},
        "stage_events": [],
        "source_paths": {"json": str(source_json), "cad": str(source_cad)},
        "source_checksums": {"json_sha256": source_json_sha, "cad_sha256": source_cad_sha},
        "source_checksums_unchanged": False,
        "selected_source": _selected_source(source_cad, request),
        "cad_selection": _cad_selection(payload, source_cad),
        "filename_mismatch_evidence": _filename_mismatch(source_cad.name, sku),
        "issues": {"source_data": list(adapter_issues), "cad": [], "classification": [], "measurement": [], "validation": []},
        "stage_statuses": {},
        "artifacts": {},
        "ai_used": False,
    }
    normalized_dir = staging / "normalized"
    reports_dir = staging / "reports"
    svg_dir = staging / "svg"

    try:
        if staging.exists():
            shutil.rmtree(staging)
        reports_dir.mkdir(parents=True)
        normalized_dir.mkdir(parents=True)
        _call_hook(hooks, "prepare_input")
        working_dxf = _stage_action("input_conversion", state, "prepare_input_dxf", (source_cad, normalized_dir, oda_exe), {}, "cad", stage_timeouts.get("input_conversion"))
        inventory = _stage("inventory", state, lambda: inventory_dxf(working_dxf), "cad")
        if inventory is not None:
            write_inventory_json(inventory, reports_dir / "inventory.json")
            _artifact(state, "inventory_json", reports_dir / "inventory.json")
        canonical = _stage_action("canonicalization", state, "canonicalize_dxf", (working_dxf,), {"tessellate_curves": True, "tessellation_tolerance": 1.0}, "cad", stage_timeouts.get("canonicalization"))
        if canonical is not None:
            write_canonical_json(canonical, reports_dir / "canonical.json")
            _artifact(state, "canonical_json", reports_dir / "canonical.json")
        regions = _stage_action("region_conversion", state, "convert_regions", (working_dxf,), {"output_path": normalized_dir / f"{sku}_regions.dxf"}, "cad", stage_timeouts.get("region_conversion"))
        if regions is not None:
            (reports_dir / "region_conversion.json").write_text(regions.to_deterministic_json() + "\n", encoding="utf-8")
            _artifact(state, "region_conversion_json", reports_dir / "region_conversion.json")
            if regions.output_path:
                working_dxf = Path(regions.output_path)
        classification = _stage(
            "classification",
            state,
            lambda: classify_layers(inventory, canonical, LayerClassificationConfig(vendor_profile=vendor_profile) if vendor_profile else None) if inventory is not None else None,
            "classification",
        )
        if classification is not None:
            write_classification_json(classification, reports_dir / "classification.json")
            _artifact(state, "classification_json", reports_dir / "classification.json")
        normalized_dxf = normalized_dir / f"{sku}_normalized.dxf"
        rewrite = _stage("rewrite", state, lambda: rewrite_layers(working_dxf, classification, normalized_dxf) if classification is not None else None, "cad")
        if rewrite is not None:
            write_rewrite_json(rewrite, reports_dir / "rewrite.json")
            _artifact(state, "normalized_dxf", normalized_dxf)
            _artifact(state, "rewrite_json", reports_dir / "rewrite.json")
        svg = _stage_action("svg_export", state, "export_layer_svgs", (normalized_dxf if normalized_dxf.exists() else working_dxf, svg_dir), {"config": SvgExportConfig(include_combined=True), "oda_executable_path": oda_exe}, "cad", stage_timeouts.get("svg_export"))
        if svg is not None:
            _artifact(state, "svg_manifest", svg_dir / "manifest.json")
            if svg.combined_svg_path:
                _artifact(state, "combined_svg", svg.combined_svg_path)
        measurement = _stage_action(
            "measurement",
            state,
            "measure_geometry",
            (inventory, canonical, classification),
            {"config": MeasurementConfig(explicit_unit=unit, expected_width_mm=_expected_width(request), expected_depth_mm=_expected_length(request))},
            "measurement",
            stage_timeouts.get("measurement"),
        ) if inventory is not None and canonical is not None else _stage("measurement", state, lambda: None, "measurement")
        if measurement is not None:
            write_measurement_json(measurement, reports_dir / "measurement.json")
            _artifact(state, "measurement_json", reports_dir / "measurement.json")
        validation = _stage("validation", state, lambda: validate_json_against_cad(request or payload, measurement) if measurement is not None else None, "validation")
        if validation is not None:
            write_validation_json(validation, reports_dir / "validation.json")
            _artifact(state, "validation_json", reports_dir / "validation.json")
        if export_dwg and normalized_dxf.exists():
            dwg = _stage_action("dwg_export", state, "export_dwg", (normalized_dxf, normalized_dir / f"{sku}_normalized.dwg", oda_exe), {}, "cad", stage_timeouts.get("dwg_export"))
            if dwg is not None:
                _artifact(state, "normalized_dwg", dwg.destination_path)
        state["overall_status"] = _overall_status(state, validation)
    finally:
        state["source_checksums_unchanged"] = source_json.exists() and source_cad.exists() and _sha256(source_json) == source_json_sha and _sha256(source_cad) == source_cad_sha
        state["elapsed_seconds"] = round(time.perf_counter() - started, 6)
        _write_manifest_and_report(staging, state)
        if final_dir.exists() and allow_overwrite:
            shutil.rmtree(final_dir)
        root.mkdir(parents=True, exist_ok=True)
        os.replace(staging, final_dir)

    manifest_path = final_dir / "manifest.json"
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _prepare_input_dxf(source: Path, normalized_dir: Path, oda_exe: Path | str | None, hooks: Mapping[str, Callable[[], None]] | None = None) -> Path:
    if hooks:
        _call_hook(hooks, "prepare_input")
    if source.suffix.lower() == ".dxf":
        return source
    converted = normalized_dir / f"{source.stem}_source.dxf"
    result = OdaConverter(oda_exe).convert(OdaConversionRequest(source_path=source, destination_path=converted, oda_executable_path=Path(oda_exe) if oda_exe else None))
    return converted


def _stage(name: str, state: dict[str, Any], func: Callable[[], Any], issue_category: str) -> Any:
    return _record_stage(name, state, issue_category, lambda: func())


def _stage_action(name: str, state: dict[str, Any], action: str, args: tuple[Any, ...], kwargs: dict[str, Any], issue_category: str, timeout_seconds: float | None) -> Any:
    return _record_stage(name, state, issue_category, lambda: _run_stage_action(action, args, kwargs, timeout_seconds))


def _record_stage(name: str, state: dict[str, Any], issue_category: str, runner: Callable[[], Any]) -> Any:
    start = time.perf_counter()
    timeout = state.get("stage_timeouts_seconds", {}).get(name)
    state.setdefault("stage_events", []).append({"stage": name, "event": "start", "timeout_seconds": timeout})
    print(f"[stage12] start {name} timeout={timeout if timeout is not None else 'none'}", flush=True)
    try:
        result = runner()
        if result is None:
            state["stage_statuses"][name] = "fail"
            state["issues"][issue_category].append({"stage": name, "severity": "fail", "code": f"{name}_not_run", "message": f"{name} did not run because prerequisites are missing."})
        else:
            state["stage_statuses"][name] = _stage_result_status(result)
            _collect_result_issues(state, issue_category, name, result)
        return result
    except Exception as exc:
        state["stage_statuses"][name] = "fail"
        state["issues"][issue_category].append({"stage": name, "severity": "fail", "code": _exc_code(exc), "message": str(exc), "type": type(exc).__name__})
        return None
    finally:
        elapsed = round(time.perf_counter() - start, 6)
        state["timings_seconds"][name] = elapsed
        status = state["stage_statuses"].get(name, "fail")
        state.setdefault("stage_events", []).append({"stage": name, "event": "end", "status": status})
        print(f"[stage12] end {name} status={status} elapsed={elapsed}s", flush=True)


class StageTimeoutError(TimeoutError):
    code = "stage_timeout"


def _run_stage_action(action: str, args: tuple[Any, ...], kwargs: dict[str, Any], timeout_seconds: float | None) -> Any:
    if timeout_seconds is None or timeout_seconds <= 0 or _action_should_run_in_process(action):
        return _execute_stage_action(action, args, kwargs)
    ctx = mp.get_context("spawn")
    queue: Any = ctx.Queue(maxsize=1)
    process = ctx.Process(target=_stage_action_worker, args=(queue, action, args, kwargs), daemon=True)
    process.start()
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            status, payload = queue.get(timeout=min(0.1, remaining) if remaining else 0.0)
            process.join(5)
            if status == "ok":
                return payload
            raise RuntimeError(payload)
        except queue_module.Empty:
            if not process.is_alive():
                break
            if time.monotonic() >= deadline:
                process.terminate()
                process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join(5)
                raise StageTimeoutError(f"Stage {action} exceeded {timeout_seconds} seconds and was terminated.")
    try:
        status, payload = queue.get_nowait()
        if status == "ok":
            return payload
        raise RuntimeError(payload)
    except queue_module.Empty:
        if process.exitcode and process.exitcode != 0:
            raise RuntimeError(f"Stage {action} worker exited with code {process.exitcode} before returning a result.")
        raise RuntimeError(f"Stage {action} worker exited without returning a result.")


def _action_should_run_in_process(action: str) -> bool:
    if action == "measure_geometry" and getattr(measure_geometry, "__module__", "") != "gebal_cad_normalizer.cad.measure":
        return True
    if action == "export_dwg" and getattr(OdaConverter, "__module__", "") != "gebal_cad_normalizer.cad.oda":
        return True
    return False


def _stage_action_worker(queue: Any, action: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    try:
        queue.put(("ok", _execute_stage_action(action, args, kwargs)))
    except Exception as exc:
        queue.put(("err", f"{type(exc).__name__}: {exc}"))


def _execute_stage_action(action: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    if action == "prepare_input_dxf":
        return _prepare_input_dxf(*args, **kwargs)
    if action == "canonicalize_dxf":
        return canonicalize_dxf(*args, **kwargs)
    if action == "convert_regions":
        return convert_regions(*args, **kwargs)
    if action == "export_layer_svgs":
        return export_layer_svgs(*args, **kwargs)
    if action == "measure_geometry":
        return measure_geometry(*args, **kwargs)
    if action == "export_dwg":
        return _export_dwg(*args, **kwargs)
    raise RuntimeError(f"Unknown Stage 12 action: {action}")


def _stage_result_status(result: Any) -> str:
    issues = list(getattr(result, "issues", ()) or getattr(result, "warnings", ()) or ())
    statuses = [getattr(item, "severity", "") for item in issues]
    if getattr(result, "overall_status", None):
        status = str(result.overall_status)
        return "review_required" if status == "not_verifiable" else status
    if any(severity == "fail" for severity in statuses):
        return "fail"
    if any(severity in {"warning", "not_verifiable"} for severity in statuses):
        return "pass_with_warnings"
    return "pass"


def _overall_status(state: dict[str, Any], validation: Any | None) -> str:
    if any(status == "fail" for status in state["stage_statuses"].values()):
        return "fail"
    if validation is not None:
        status = str(validation.overall_status)
        if status == "not_verifiable":
            return "review_required"
        if status in STATUS_ORDER:
            return status
    issue_rows = [issue for rows in state["issues"].values() for issue in rows]
    if any(issue.get("severity") == "fail" for issue in issue_rows):
        return "fail"
    if any(issue.get("severity") in {"warning", "not_verifiable"} for issue in issue_rows):
        return "pass_with_warnings"
    return "pass"


def _collect_result_issues(state: dict[str, Any], category: str, stage: str, result: Any) -> None:
    for issue in getattr(result, "issues", ()) or getattr(result, "warnings", ()) or ():
        data = issue.model_dump(mode="json") if hasattr(issue, "model_dump") else dict(issue)
        data.setdefault("stage", stage)
        state["issues"][category].append(data)
    if stage == "validation":
        for check in getattr(result, "checks", ()) or ():
            if str(check.status) == "not_verifiable":
                state["issues"]["validation"].append({"stage": stage, "severity": "not_verifiable", "code": "unverifiable_field", "message": check.message, "field_path": check.field_path})


def _write_manifest_and_report(staging: Path, state: dict[str, Any]) -> None:
    for artifact in state["artifacts"].values():
        path = Path(artifact["path"])
        if path.exists():
            try:
                artifact["path"] = _rel(staging, path)
            except ValueError:
                artifact["path"] = str(path)
    files = sorted(path for path in staging.rglob("*") if path.is_file() and path.name not in {"manifest.json", "report.md"})
    state["files"] = [{"path": _rel(staging, path), "sha256": _sha256(path), "size_bytes": path.stat().st_size} for path in files]
    state["issue_counts"] = {category: dict(Counter(issue.get("code", "unknown") for issue in rows)) for category, rows in state["issues"].items()}
    (staging / "manifest.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (staging / "report.md").write_text(_markdown_report(state), encoding="utf-8")


def _markdown_report(state: dict[str, Any]) -> str:
    lines = [
        "# CAD Normalization Package",
        "",
        f"- Occurrence: `{state['occurrence_id']}`",
        f"- SKU: `{state['sku']}`",
        f"- Overall status: `{state['overall_status']}`",
        f"- Source JSON: `{state['source_paths']['json']}`",
        f"- Source CAD: `{state['source_paths']['cad']}`",
        f"- Source checksums unchanged: `{state['source_checksums_unchanged']}`",
        f"- Filename/SKU mismatch: `{state['filename_mismatch_evidence']['mismatch']}`",
        "",
        "## Stages",
    ]
    lines.extend(f"- `{name}`: `{status}` ({state['timings_seconds'].get(name, 0)}s)" for name, status in sorted(state["stage_statuses"].items()))
    lines.extend(["", "## Artifacts"])
    lines.extend(f"- `{name}`: `{artifact['path']}`" for name, artifact in sorted(state["artifacts"].items()))
    lines.extend(["", "## Issues"])
    for category in ("source_data", "cad", "classification", "measurement", "validation"):
        rows = state["issues"].get(category, [])
        lines.append(f"### {category.replace('_', ' ').title()}")
        if rows:
            lines.extend(f"- `{row.get('severity', '')}` `{row.get('code', '')}`: {row.get('message', '')}" for row in rows)
        else:
            lines.append("- None")
    lines.extend(["", "## Candidate Evidence", "### CAD Selection"])
    lines.append(f"- Selected: `{state['cad_selection'].get('selected')}`")
    lines.extend(f"- Alternative: `{item}`" for item in state["cad_selection"].get("alternatives", []))
    lines.extend(["", "## Explicit Review Notes"])
    lines.append("- Unknown or missing CAD units are listed under CAD, measurement, and validation issues when present.")
    lines.append("- Height, free-fall height, and other non-top-view fields are reported as unverifiable by Stage 11 when present.")
    return "\n".join(lines) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _parse_request(payload: Mapping[str, Any]) -> tuple[CadProcessingRequest | None, list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    adapters = (BluestoneAdapter(), UnifiedAdapter()) if "results" in payload else (UnifiedAdapter(), BluestoneAdapter())
    for adapter in adapters:
        result = adapter.parse(payload)
        issues.extend(issue.model_dump(mode="json") for issue in result.issues)
        if result.request is not None:
            return result.request, issues
    return None, issues


def _cad_selection(payload: Mapping[str, Any], source_cad: Path) -> dict[str, Any]:
    candidates = _collect_assets(payload)
    if not candidates:
        return {"selected": str(source_cad), "alternatives": [], "evaluations": []}
    selection = select_top_view_asset(candidates)
    selected = selection.selected_candidate or {"file_name": source_cad.name}
    selected_id = _candidate_name(selected) or source_cad.name
    alternatives = [_candidate_name(item.candidate) or item.candidate_identifier for item in selection.evaluations if (_candidate_name(item.candidate) or item.candidate_identifier) != selected_id]
    return {
        "decision": selection.decision,
        "selected": selected_id,
        "alternatives": alternatives,
        "evaluations": [item.__dict__ | {"candidate": dict(item.candidate), "reasons": list(item.reasons)} for item in selection.evaluations],
    }


def _collect_assets(value: Any) -> list[Mapping[str, Any]]:
    assets: list[Mapping[str, Any]] = []
    markers = {"file_name", "filename", "fileName", "url", "download_url", "downloadUrl", "content_type", "contentType", "asset_name", "assetName", "media_id", "mediaId"}
    if isinstance(value, Mapping):
        if markers.intersection(value):
            assets.append(value)
        for child in value.values():
            assets.extend(_collect_assets(child))
    elif isinstance(value, list):
        for child in value:
            assets.extend(_collect_assets(child))
    return assets


def _selected_source(source_cad: Path, request: CadProcessingRequest | None) -> dict[str, Any]:
    return {"cad_path": str(source_cad), "json_candidate": request.top_view_cad.model_dump(mode="json") if request else None}


def _filename_mismatch(filename: str, sku: str) -> dict[str, Any]:
    found = SKU_RE.findall(filename)
    normalized = _normalize_sku(sku)
    mismatch = bool(found) and not any(_normalize_sku(match) == normalized for match in found)
    return {"filename": filename, "sku": sku, "sku_tokens_in_filename": found, "mismatch": mismatch}


def _occurrence_id(sku: str, json_path: Path, cad_path: Path) -> str:
    digest = hashlib.sha256(f"{json_path.resolve()}|{cad_path.resolve()}".encode("utf-8")).hexdigest()[:10]
    return f"{_sanitize(sku)}_{digest}"


def _sku(request: CadProcessingRequest | None, payload: Mapping[str, Any]) -> str | None:
    if request is not None:
        return request.product.sku
    value = _find_key(payload, ("sku", "productNumber", "product_number", "code"))
    return str(value).strip() if value else None


def _find_key(value: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(value, Mapping):
        for key in keys:
            if value.get(key):
                return value[key]
        for child in value.values():
            found = _find_key(child, keys)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_key(child, keys)
            if found:
                return found
    return None


def _expected_width(request: CadProcessingRequest | None) -> float | None:
    return request.expected_dimensions.width_mm if request and request.expected_dimensions else None


def _expected_length(request: CadProcessingRequest | None) -> float | None:
    return request.expected_dimensions.length_mm if request and request.expected_dimensions else None


def _export_dwg(source: Path, destination: Path, oda_exe: Path | str | None) -> Any:
    return OdaConverter(oda_exe).convert(OdaConversionRequest(source_path=source, destination_path=destination, oda_executable_path=Path(oda_exe) if oda_exe else None))


def _artifact(state: dict[str, Any], name: str, path: Path) -> None:
    state["artifacts"][name] = {"path": str(path), "sha256": _sha256(path) if path.exists() and path.is_file() else None}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sanitize(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-") or "occurrence"


def _normalize_sku(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _candidate_name(candidate: Mapping[str, Any]) -> str | None:
    for key in ("file_name", "filename", "fileName", "name", "asset_name", "assetName", "id", "media_id", "mediaId"):
        if candidate.get(key):
            return str(candidate[key])
    return None


def _rel(root: Path, path: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _exc_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if isinstance(exc, OdaFileConverterError):
        return code.value
    return getattr(code, "value", None) or str(code or type(exc).__name__)


def _call_hook(hooks: Mapping[str, Callable[[], None]], name: str) -> None:
    hook = hooks.get(name)
    if hook:
        hook()


