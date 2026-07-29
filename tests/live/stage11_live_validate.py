"""Manual Stage 11 JSON-vs-CAD validation check.

This script is intentionally outside the default pytest suite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from gebal_cad_normalizer.adapters import BluestoneAdapter, UnifiedAdapter
from gebal_cad_normalizer.cad import LayerClassificationConfig, MeasurementConfig, canonicalize_dxf, classify_layers, inventory_dxf, measure_geometry
from gebal_cad_normalizer.cad.validate import validate_json_against_cad, write_validation_json


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_request(payload: dict[str, Any]):
    adapters = (BluestoneAdapter(), UnifiedAdapter()) if "results" in payload else (UnifiedAdapter(), BluestoneAdapter())
    issues = []
    for adapter in adapters:
        result = adapter.parse(payload)
        issues.extend(issue.model_dump(mode="json") for issue in result.issues)
        if result.request is not None:
            return result.request, issues
    return payload, issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Run read-only Stage 11 deterministic JSON-vs-CAD validation.")
    parser.add_argument("--json", required=True, help="Input product JSON path.")
    parser.add_argument("--input", required=True, help="Input .dxf path.")
    parser.add_argument("--vendor-profile", help="Optional built-in vendor layer profile, for example bluestone_playground.")
    parser.add_argument("--unit", choices=("mm", "cm", "m", "in"), help="Explicit drawing-unit override.")
    parser.add_argument("--json-output", help="Optional deterministic validation JSON output path.")
    args = parser.parse_args()

    input_path = Path(args.input)
    try:
        before = _sha256(input_path)
        payload = _load_json(Path(args.json))
        request_or_payload, adapter_issues = _parse_request(payload)
        validation_input = request_or_payload if "results" in payload else payload
        width = getattr(getattr(request_or_payload, "expected_dimensions", None), "width_mm", None)
        depth = getattr(getattr(request_or_payload, "expected_dimensions", None), "length_mm", None)
        inventory = inventory_dxf(input_path)
        canonical = canonicalize_dxf(input_path, tessellate_curves=True, tessellation_tolerance=1.0)
        class_config = LayerClassificationConfig(vendor_profile=args.vendor_profile) if args.vendor_profile else None
        classification = classify_layers(inventory, canonical, class_config)
        measurement = measure_geometry(inventory, canonical, classification, MeasurementConfig(explicit_unit=args.unit, expected_width_mm=width, expected_depth_mm=depth))
        validation = validate_json_against_cad(validation_input, measurement)
        if args.json_output:
            write_validation_json(validation, Path(args.json_output))
        after = _sha256(input_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"success": False, "message": str(exc)}, sort_keys=True, separators=(",", ":")))
        return 1

    status_counts = Counter(check.status for check in validation.checks)
    issue_counts = Counter(issue.code for issue in validation.issues)
    summary = {
        "success": before == after,
        "overall_status": validation.overall_status,
        "status_counts": dict(sorted(status_counts.items())),
        "issue_codes": dict(sorted(issue_counts.items())),
        "candidate_count": len(measurement.candidates),
        "ranked_candidate_count": len(validation.candidate_rankings),
        "unit_status": measurement.unit_status,
        "unit_evidence": measurement.unit_evidence,
        "adapter_issues": adapter_issues,
        "source_checksum_unchanged": before == after,
        "json_output": args.json_output,
        "vendor_profile": args.vendor_profile,
    }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0 if before == after else 1


if __name__ == "__main__":
    sys.exit(main())


