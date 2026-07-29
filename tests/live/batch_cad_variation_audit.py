"""Read-only batch CAD variation audit for TEST_EXAMPLES.

This script is intentionally audit-only. It does not write to, rename, move, or
overwrite files in the source archive. All generated DXFs, reports, and optional
SVG evidence are written under the explicit output root.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from gebal_cad_normalizer.cad import (
    OdaConversionRequest,
    OdaConverter,
    LayerClassificationConfig,
    SvgExportConfig,
    canonicalize_dxf,
    classify_layers,
    convert_regions,
    export_layer_svgs,
    inventory_dxf,
)


DEFAULT_SOURCE_ROOT = Path(r"C:\Users\jvsin\Documents\Draftermath Archive\CAD_Examples\TEST_EXAMPLES")
DEFAULT_OUTPUT_ROOT = Path("tests/output/cad_variation_audit")
DEFAULT_ODA_EXE = Path(r"E:\ODA\ODAFileConverter.exe")
EXAMPLE_NAMES = tuple(f"Example{index}" for index in range(1, 6))
CAD_SUFFIXES = {".dwg", ".dxf"}
IGNORED_NAME_PARTS = ("product_model", "product-model", "side_view", "side-view", "side view", "output")
IGNORED_SUFFIXES = (".bak", ".original")
SKU_RE = re.compile(r"(?i)(?<![A-Z0-9])([A-Z]?\d{5,6}_?M?|\d{5,6})(?![A-Z0-9])")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a read-only batch CAD variation audit.")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--oda-exe", type=Path, default=DEFAULT_ODA_EXE)
    parser.add_argument("--target-version", default="ACAD2013")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--svg-limit", type=int, default=10)
    parser.add_argument("--vendor-profile", help="Optional built-in Stage 8 vendor layer profile, for example bluestone_playground.")
    args = parser.parse_args()

    started = time.perf_counter()
    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    dxf_root = output_root / "converted_dxfs"
    unusual_root = output_root / "unusual_cases"
    dxf_root.mkdir(parents=True, exist_ok=True)

    products = discover_products(source_root)
    converter = OdaConverter(args.oda_exe)
    details: list[dict[str, Any]] = []

    for index, product in enumerate(products, start=1):
        print(f"[{index}/{len(products)}] {product['example_folder']}\\{product['product_folder']}")
        details.append(audit_product(product, converter, dxf_root, args.target_version, args.timeout_seconds, args.vendor_profile))

    unusual = select_unusual_cases(details, args.svg_limit)
    export_unusual_svgs(unusual, unusual_root, args.oda_exe, args.svg_limit)

    summary = build_summary(details, products, source_root, output_root, time.perf_counter() - started)
    payload = {
        "source_root": str(source_root),
        "output_root": str(output_root),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "vendor_profile": args.vendor_profile,
        "summary": summary,
        "details": details,
    }

    write_json(output_root / "audit_results.json", payload)
    write_matrix(output_root / "audit_matrix.csv", details)
    write_markdown(output_root / "audit_summary.md", summary, details, unusual)
    print(json.dumps({
        "products_scanned": summary["total_product_folders_scanned"],
        "selected_cad_files": summary["selected_top_view_files"],
        "conversion_success": summary["conversion_success_count"],
        "conversion_failed": summary["conversion_failure_count"],
        "output_root": str(output_root),
    }, indent=2))
    return 0


def discover_products(source_root: Path) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for example_name in EXAMPLE_NAMES:
        example_dir = source_root / example_name
        if not example_dir.is_dir():
            continue
        for child in sorted((item for item in example_dir.iterdir() if item.is_dir()), key=lambda item: item.name.casefold()):
            if child.name.lower() in {"debug_crops", "outputs", "output"}:
                continue
            files = [path for path in child.iterdir() if path.is_file()]
            json_files = sorted([path for path in files if path.suffix.lower() == ".json"], key=lambda item: item.name.casefold())
            cad_files = sorted([path for path in files if _is_cad_like(path)], key=lambda item: item.name.casefold())
            if not json_files and not cad_files:
                continue
            products.append(
                {
                    "example_folder": example_name,
                    "product_folder": child.name,
                    "product_path": child,
                    "sku_from_folder": child.name,
                    "json_files": json_files,
                    "cad_files": cad_files,
                }
            )
    return products


def audit_product(product: dict[str, Any], converter: OdaConverter, dxf_root: Path, target_version: str, timeout_seconds: float, vendor_profile: str | None) -> dict[str, Any]:
    start = time.perf_counter()
    json_sku, json_error = extract_json_sku(product["json_files"])
    selected, competing, selection_issues = select_top_view(product["cad_files"], product["sku_from_folder"])
    detail: dict[str, Any] = {
        "example_folder": product["example_folder"],
        "product_folder": product["product_folder"],
        "product_path": str(product["product_path"]),
        "sku_from_folder": product["sku_from_folder"],
        "sku_from_json": json_sku,
        "json_files": [path.name for path in product["json_files"]],
        "json_error": json_error,
        "selected_top_view_cad_filename": selected.name if selected else None,
        "selected_top_view_cad_path": str(selected) if selected else None,
        "all_competing_cad_candidates": [path.name for path in competing],
        "ignored_cad_files": [path.name for path in product["cad_files"] if path not in competing],
        "filename_sku_mismatch": bool(selected and not filename_matches_sku(selected.name, product["sku_from_folder"])),
        "missing_top_view_file": selected is None,
        "ambiguous_top_view_file": "ambiguous_top_view_file" in selection_issues,
        "selection_issue_codes": selection_issues,
        "conversion": {"success": False, "error": None},
        "stages": {},
        "runtime_seconds": None,
        "families": [],
        "error_details": [],
    }

    if selected is None:
        detail["runtime_seconds"] = round(time.perf_counter() - start, 4)
        detail["families"] = families_for(detail)
        return detail

    source_sha_before = sha256(selected)
    destination = dxf_root / product["example_folder"] / sanitize(product["product_folder"]) / f"{selected.stem}.dxf"
    try:
        if selected.suffix.lower() == ".dxf":
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(selected.read_bytes())
            conversion_result = None
        else:
            conversion_result = converter.convert(
                OdaConversionRequest(
                    source_path=selected,
                    destination_path=destination,
                    target_version=target_version,
                    timeout_seconds=timeout_seconds,
                )
            )
        source_sha_after = sha256(selected)
        detail["conversion"] = {
            "success": True,
            "source_checksum_before": source_sha_before,
            "source_checksum_after": source_sha_after,
            "source_checksum_preserved": source_sha_before == source_sha_after,
            "dwg_size_bytes": selected.stat().st_size,
            "dxf_path": str(destination),
            "dxf_size_bytes": destination.stat().st_size,
            "oda_elapsed_seconds": round(conversion_result.elapsed_seconds, 4) if conversion_result else 0.0,
            "oda_stdout": conversion_result.stdout if conversion_result else "",
            "oda_stderr": conversion_result.stderr if conversion_result else "",
        }
    except Exception as exc:
        detail["conversion"] = {
            "success": False,
            "source_checksum_before": source_sha_before,
            "source_checksum_after": sha256(selected),
            "source_checksum_preserved": source_sha_before == sha256(selected),
            "dwg_size_bytes": selected.stat().st_size if selected.exists() else None,
            "dxf_path": str(destination),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "error_code": str(getattr(getattr(exc, "code", None), "value", getattr(exc, "code", "")) or ""),
            "stdout": getattr(exc, "stdout", ""),
            "stderr": getattr(exc, "stderr", ""),
        }
        detail["error_details"].append({"stage": "conversion", "type": type(exc).__name__, "message": str(exc)})
        detail["runtime_seconds"] = round(time.perf_counter() - start, 4)
        detail["families"] = families_for(detail)
        return detail

    run_stage_reports(detail, destination, vendor_profile)
    detail["runtime_seconds"] = round(time.perf_counter() - start, 4)
    detail["families"] = families_for(detail)
    return detail


def run_stage_reports(detail: dict[str, Any], dxf_path: Path, vendor_profile: str | None = None) -> None:
    try:
        inv = inventory_dxf(dxf_path)
        detail["stages"]["inventory"] = inv.model_dump(mode="json")
    except Exception as exc:
        detail["stages"]["inventory_error"] = {"type": type(exc).__name__, "message": str(exc), "code": str(getattr(getattr(exc, "code", None), "value", getattr(exc, "code", "")) or "")}
        detail["error_details"].append({"stage": "inventory", "type": type(exc).__name__, "message": str(exc)})
        return

    try:
        canonical = canonicalize_dxf(dxf_path)
        detail["stages"]["canonical"] = summarize_canonical(canonical)
    except Exception as exc:
        canonical = None
        detail["stages"]["canonical_error"] = {"type": type(exc).__name__, "message": str(exc)}
        detail["error_details"].append({"stage": "canonical", "type": type(exc).__name__, "message": str(exc)})

    try:
        regions = convert_regions(dxf_path)
        detail["stages"]["regions"] = regions.model_dump(mode="json")
    except Exception as exc:
        detail["stages"]["regions_error"] = {"type": type(exc).__name__, "message": str(exc)}
        detail["error_details"].append({"stage": "regions", "type": type(exc).__name__, "message": str(exc)})

    try:
        config = LayerClassificationConfig(vendor_profile=vendor_profile) if vendor_profile else None
        classification = classify_layers(inv, canonical, config)
        detail["stages"]["classification"] = classification.model_dump(mode="json")
    except Exception as exc:
        detail["stages"]["classification_error"] = {"type": type(exc).__name__, "message": str(exc)}
        detail["error_details"].append({"stage": "classification", "type": type(exc).__name__, "message": str(exc)})


def summarize_canonical(canonical: Any) -> dict[str, Any]:
    data = canonical.model_dump(mode="json")
    entities = data.pop("entities", [])
    data["entity_sample_count"] = min(len(entities), 25)
    data["entity_samples"] = entities[:25]
    return data


def select_top_view(cad_files: list[Path], sku: str) -> tuple[Path | None, list[Path], list[str]]:
    candidates = [path for path in cad_files if not ignored_cad_name(path)]
    top_candidates = [path for path in candidates if "top" in path.name.casefold() and "view" in path.name.casefold()]
    issues: list[str] = []
    if not top_candidates:
        return None, candidates, ["missing_top_view_file"]

    scored = sorted(((top_view_score(path, sku), path) for path in top_candidates), key=lambda item: (-item[0], item[1].name.casefold()))
    best_score = scored[0][0]
    selected = scored[0][1]
    tied = [path for score, path in scored if score == best_score]
    if len(top_candidates) > 1:
        issues.append("multiple_top_view_candidates")
    if len(tied) > 1:
        issues.append("ambiguous_top_view_file")
    if not filename_matches_sku(selected.name, sku):
        issues.append("filename_sku_mismatch")
    return selected, candidates, issues


def top_view_score(path: Path, sku: str) -> int:
    name = path.name.casefold()
    score = 0
    if path.suffix.lower() == ".dwg":
        score += 20
    if name == "top_view.dwg":
        score += 30
    if "top_view" in name or "top view" in name:
        score += 20
    if normalize_sku(sku) and normalize_sku(sku) in normalize_sku(name):
        score += 10
    return score


def extract_json_sku(json_files: list[Path]) -> tuple[str | None, str | None]:
    if not json_files:
        return None, None
    for path in json_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            return None, f"{path.name}: {type(exc).__name__}: {exc}"
        found = find_sku_value(data)
        if found:
            return found, None
    return None, None


def find_sku_value(value: Any) -> str | None:
    if isinstance(value, dict):
        priority = ("sku", "productNumber", "product_number", "productNo", "product_no", "itemNumber", "code")
        for key in priority:
            if key in value and isinstance(value[key], (str, int)):
                text = str(value[key]).strip()
                if SKU_RE.search(text):
                    return text
        for child in value.values():
            found = find_sku_value(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_sku_value(child)
            if found:
                return found
    return None


def build_summary(details: list[dict[str, Any]], products: list[dict[str, Any]], source_root: Path, output_root: Path, elapsed: float) -> dict[str, Any]:
    unique_skus = sorted({item["sku_from_folder"] for item in details}, key=str.casefold)
    selected = [item for item in details if item["selected_top_view_cad_filename"]]
    successful = [item for item in details if item["conversion"].get("success")]
    failed = [item for item in selected if not item["conversion"].get("success")]
    entity_freq: Counter[str] = Counter()
    issue_codes: Counter[str] = Counter()
    units: Counter[str] = Counter()
    layer_names: Counter[str] = Counter()
    families: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    ambiguous_layers: list[dict[str, str]] = []

    for item in details:
        families.update(item.get("families", []))
        inv = item.get("stages", {}).get("inventory")
        if inv:
            entity_freq.update({row["dxf_type"]: row["count"] for row in inv.get("entity_counts", [])})
            issue_codes.update(issue["code"] for issue in inv.get("issues", []))
            units.update([inv.get("drawing_units", "unknown")])
            layer_names.update(layer["name"] for layer in inv.get("layers", []))
        canonical = item.get("stages", {}).get("canonical")
        if canonical:
            issue_codes.update(issue["code"] for issue in canonical.get("issues", []))
        regions = item.get("stages", {}).get("regions")
        if regions:
            issue_codes.update(issue["code"] for issue in regions.get("issues", []))
        classification = item.get("stages", {}).get("classification")
        if classification:
            role_counts.update(classification.get("role_counts", {}))
            issue_codes.update(issue["code"] for issue in classification.get("issues", []))
            for layer in classification.get("layers", []):
                if layer.get("assigned_role") in {"ambiguous", "review_required"}:
                    ambiguous_layers.append({
                        "example": item["example_folder"],
                        "product": item["product_folder"],
                        "layer": layer.get("original_layer_name", ""),
                        "role": layer.get("assigned_role", ""),
                        "reason": layer.get("review_reason") or "",
                    })

    repeated_skus = {sku: count for sku, count in Counter(item["sku_from_folder"] for item in details).items() if count > 1}
    return {
        "source_root": str(source_root),
        "output_root": str(output_root),
        "runtime_seconds": round(elapsed, 4),
        "total_product_folders_scanned": len(products),
        "json_files_found": sum(len(item["json_files"]) for item in products),
        "cad_like_files_found_in_product_folders": sum(len(item["cad_files"]) for item in products),
        "selected_top_view_files": len(selected),
        "unique_skus": len(unique_skus),
        "repeated_skus": dict(sorted(repeated_skus.items())),
        "conversion_success_count": len(successful),
        "conversion_failure_count": len(failed),
        "top_view_selection_problem_count": sum(1 for item in details if item.get("selection_issue_codes")),
        "missing_top_view_count": sum(1 for item in details if item.get("missing_top_view_file")),
        "ambiguous_top_view_count": sum(1 for item in details if item.get("ambiguous_top_view_file")),
        "filename_sku_mismatch_count": sum(1 for item in details if item.get("filename_sku_mismatch")),
        "entity_type_frequency": dict(entity_freq.most_common()),
        "layer_name_frequency": dict(layer_names.most_common()),
        "units_distribution": dict(units.most_common()),
        "issue_code_frequency": dict(issue_codes.most_common()),
        "family_counts": dict(families.most_common()),
        "classification_role_counts": dict(role_counts.most_common()),
        "ambiguous_or_review_layers_count": len(ambiguous_layers),
        "ambiguous_or_review_layer_samples": ambiguous_layers[:50],
    }


def families_for(item: dict[str, Any]) -> list[str]:
    families: set[str] = set()
    inv = item.get("stages", {}).get("inventory")
    classification = item.get("stages", {}).get("classification")
    regions = item.get("stages", {}).get("regions")
    conversion = item.get("conversion", {})
    if item.get("missing_top_view_file"):
        families.add("missing top-view file")
    if item.get("ambiguous_top_view_file"):
        families.add("ambiguous top-view selection")
    if item.get("filename_sku_mismatch"):
        families.add("filename/SKU mismatch")
    if not conversion.get("success") and item.get("selected_top_view_cad_filename"):
        families.add("empty or malformed drawings")
    if not inv:
        return sorted(families)

    counts = {row["dxf_type"]: row["count"] for row in inv.get("entity_counts", [])}
    total = max(1, sum(counts.values()))
    if counts.get("LINE", 0) + counts.get("LWPOLYLINE", 0) + counts.get("POLYLINE", 0) > total * 0.65:
        families.add("simple 2D line/polyline drawings")
    if counts.get("SPLINE", 0) + counts.get("ELLIPSE", 0) > 0:
        families.add("spline/ellipse-heavy drawings")
    if counts.get("INSERT", 0) > 0 or sum(block.get("insert_count", 0) for block in inv.get("blocks", [])) > 0:
        families.add("block-heavy drawings")
    if inv.get("xref_indicators") or counts.get("ACAD_PROXY_ENTITY", 0) > 0:
        families.add("proxy/XREF-containing drawings")
    if inv.get("has_3d_geometry") or inv.get("has_nonzero_z_geometry"):
        families.add("3D/non-zero-Z contaminated drawings")
    if inv.get("drawing_units") in {"missing", "unknown"} or inv.get("insunits") in {None, 0}:
        families.add("unknown-unit drawings")
    if counts.get("REGION", 0) > 0 or (regions and regions.get("region_count", 0) > 0):
        families.add("REGION-containing drawings")
    if classification and any(layer.get("assigned_role") in {"ambiguous", "review_required"} for layer in classification.get("layers", [])):
        families.add("inconsistent layer-name schemes")
    if inv.get("modelspace_entity_count", 0) == 0:
        families.add("empty or malformed drawings")
    return sorted(families)


def select_unusual_cases(details: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    scored = sorted(((unusual_score(item), item) for item in details), key=lambda pair: (-pair[0], pair[1]["example_folder"], pair[1]["product_folder"]))
    return [item for score, item in scored if score > 0 and item.get("conversion", {}).get("success")][:limit]


def unusual_score(item: dict[str, Any]) -> int:
    score = 0
    score += 20 * int(bool(item.get("error_details")))
    score += 10 * int(item.get("filename_sku_mismatch", False))
    score += 8 * int(item.get("ambiguous_top_view_file", False))
    inv = item.get("stages", {}).get("inventory") or {}
    counts = {row["dxf_type"]: row["count"] for row in inv.get("entity_counts", [])}
    score += 5 * int(inv.get("has_3d_geometry", False) or inv.get("has_nonzero_z_geometry", False))
    score += 5 * int(bool(inv.get("xref_indicators")) or counts.get("ACAD_PROXY_ENTITY", 0) > 0)
    score += 5 * int(counts.get("REGION", 0) > 0)
    score += 3 * int(counts.get("SPLINE", 0) + counts.get("ELLIPSE", 0) > 0)
    score += len(item.get("families", []))
    classification = item.get("stages", {}).get("classification") or {}
    score += sum(1 for layer in classification.get("layers", []) if layer.get("assigned_role") in {"ambiguous", "review_required"})
    return score


def export_unusual_svgs(unusual: list[dict[str, Any]], unusual_root: Path, oda_exe: Path, limit: int) -> None:
    if limit <= 0:
        return
    for item in unusual[:limit]:
        dxf = item.get("conversion", {}).get("dxf_path")
        if not dxf:
            continue
        out = unusual_root / sanitize(f"{item['example_folder']}_{item['product_folder']}")
        try:
            result = export_layer_svgs(Path(dxf), out, config=SvgExportConfig(include_combined=True), oda_executable_path=oda_exe)
            item["svg_preview_dir"] = str(result.output_dir)
        except Exception as exc:
            item["svg_preview_error"] = {"type": type(exc).__name__, "message": str(exc)}


def write_matrix(path: Path, details: list[dict[str, Any]]) -> None:
    rows = []
    for item in details:
        inv = item.get("stages", {}).get("inventory") or {}
        canonical = item.get("stages", {}).get("canonical") or {}
        regions = item.get("stages", {}).get("regions") or {}
        classification = item.get("stages", {}).get("classification") or {}
        counts = {row["dxf_type"]: row["count"] for row in inv.get("entity_counts", [])}
        rows.append({
            "example_folder": item["example_folder"],
            "product_folder": item["product_folder"],
            "sku_from_folder": item["sku_from_folder"],
            "sku_from_json": item.get("sku_from_json") or "",
            "selected_top_view_cad_filename": item.get("selected_top_view_cad_filename") or "",
            "competing_cad_candidates": ";".join(item.get("all_competing_cad_candidates", [])),
            "filename_sku_mismatch": item.get("filename_sku_mismatch", False),
            "missing_top_view_file": item.get("missing_top_view_file", False),
            "ambiguous_top_view_file": item.get("ambiguous_top_view_file", False),
            "conversion_success": item.get("conversion", {}).get("success", False),
            "dwg_size_bytes": item.get("conversion", {}).get("dwg_size_bytes", ""),
            "dxf_size_bytes": item.get("conversion", {}).get("dxf_size_bytes", ""),
            "dxf_version": inv.get("dxf_version", ""),
            "insunits": inv.get("insunits", ""),
            "drawing_units": inv.get("drawing_units", ""),
            "layer_count": len(inv.get("layers", [])),
            "layer_names": ";".join(layer.get("name", "") for layer in inv.get("layers", [])),
            "total_entity_count": inv.get("total_entity_count", ""),
            "modelspace_entity_count": inv.get("modelspace_entity_count", ""),
            "block_count": len(inv.get("blocks", [])),
            "insert_count": counts.get("INSERT", 0),
            "region_count": counts.get("REGION", 0),
            "has_proxy_xref": bool(inv.get("xref_indicators") or counts.get("ACAD_PROXY_ENTITY", 0)),
            "has_raster_underlay": any(key in counts for key in ("IMAGE", "UNDERLAY", "PDFUNDERLAY", "DGNUNDERLAY", "DWFUNDERLAY")),
            "has_3d_or_nonzero_z": bool(inv.get("has_3d_geometry") or inv.get("has_nonzero_z_geometry")),
            "unsupported_entity_types": ";".join(row["dxf_type"] for row in inv.get("entity_counts", []) if row.get("category") == "unsupported"),
            "canonical_status_counts": json.dumps(canonical.get("counts_by_status", {}), sort_keys=True),
            "classification_role_counts": json.dumps(classification.get("role_counts", {}), sort_keys=True),
            "ambiguous_review_layers": ";".join(layer.get("original_layer_name", "") for layer in classification.get("layers", []) if layer.get("assigned_role") in {"ambiguous", "review_required"}),
            "issue_codes": ";".join(sorted(issue_codes_for(item))),
            "families": ";".join(item.get("families", [])),
            "runtime_seconds": item.get("runtime_seconds", ""),
            "error_details": json.dumps(item.get("error_details", []), sort_keys=True),
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, summary: dict[str, Any], details: list[dict[str, Any]], unusual: list[dict[str, Any]]) -> None:
    failures = [item for item in details if item.get("selected_top_view_cad_filename") and not item.get("conversion", {}).get("success")]
    selection_problems = [item for item in details if item.get("selection_issue_codes")]
    regression = recommended_fixtures(details)
    lines = [
        "# CAD Variation Audit Summary",
        "",
        f"- Source root: `{summary['source_root']}`",
        f"- Output root: `{summary['output_root']}`",
        f"- Total product folders scanned: {summary['total_product_folders_scanned']}",
        f"- JSON files found: {summary['json_files_found']}",
        f"- CAD-like files found in product folders: {summary['cad_like_files_found_in_product_folders']}",
        f"- Selected top-view files: {summary['selected_top_view_files']}",
        f"- Unique SKUs: {summary['unique_skus']}",
        f"- Successful conversions: {summary['conversion_success_count']}",
        f"- Failed conversions: {summary['conversion_failure_count']}",
        f"- Runtime seconds: {summary['runtime_seconds']}",
        "",
        "## Top-View Selection Problems",
        f"- Problems: {summary['top_view_selection_problem_count']}",
        f"- Missing top-view files: {summary['missing_top_view_count']}",
        f"- Ambiguous top-view files: {summary['ambiguous_top_view_count']}",
        f"- Filename/SKU mismatches: {summary['filename_sku_mismatch_count']}",
        *[f"- `{item['example_folder']}/{item['product_folder']}`: {', '.join(item['selection_issue_codes'])}; selected `{item.get('selected_top_view_cad_filename')}`; candidates `{', '.join(item.get('all_competing_cad_candidates', []))}`" for item in selection_problems[:30]],
        "",
        "## CAD Variation Families",
        *[f"- {name}: {count}" for name, count in summary["family_counts"].items()],
        "",
        "## Entity-Type Frequency",
        *[f"- `{name}`: {count}" for name, count in list(summary["entity_type_frequency"].items())[:40]],
        "",
        "## Layer-Name Variation Patterns",
        *[f"- `{name}`: {count}" for name, count in list(summary["layer_name_frequency"].items())[:50]],
        "",
        "## Units Distribution",
        *[f"- `{name}`: {count}" for name, count in summary["units_distribution"].items()],
        "",
        "## Proxy/XREF/3D/REGION Prevalence",
        f"- Proxy/XREF family count: {summary['family_counts'].get('proxy/XREF-containing drawings', 0)}",
        f"- 3D/non-zero-Z family count: {summary['family_counts'].get('3D/non-zero-Z contaminated drawings', 0)}",
        f"- REGION family count: {summary['family_counts'].get('REGION-containing drawings', 0)}",
        "",
        "## Classification Confidence Problems",
        f"- Ambiguous/review-required layer assignments: {summary['ambiguous_or_review_layers_count']}",
        *[f"- `{row['example']}/{row['product']}` layer `{row['layer']}` -> `{row['role']}`: {row['reason']}" for row in summary["ambiguous_or_review_layer_samples"][:30]],
        "",
        "## Most Unusual Files",
        *[f"- `{item['example_folder']}/{item['product_folder']}/{item.get('selected_top_view_cad_filename')}`: {', '.join(item.get('families', []))}" for item in unusual[:10]],
        "",
        "## Failures",
        *([f"- `{item['example_folder']}/{item['product_folder']}`: {item.get('conversion', {}).get('error')}" for item in failures] or ["- None"]),
        "",
        "## Risks For Stages 10-12",
        "- Unknown or missing units are common enough that measurement must report unit inference explicitly and avoid silent millimetre assumptions.",
        "- Ambiguous/review-required layer assignments mean footprint and safety-zone measurement should require confidence evidence, not just layer names.",
        "- Block-heavy drawings require measurement to decide whether to measure block references, block definitions, or both without double-counting.",
        "- Curve-heavy drawings need controlled tessellation policy before area or perimeter validation.",
        "- Filename/SKU mismatches and repeated SKUs require reports to preserve occurrence paths, not only SKU-level aggregation.",
        "",
        "## Recommendations",
        "- Add Stage 10 measurement with explicit unit status, bounding-box evidence, closed-geometry evidence, and block traversal policy.",
        "- Keep CAD-to-JSON validation tolerant to rotated dimensions and explicit about unverifiable top-view fields.",
        "- Report safety-zone/product-footprint candidates separately when both are plausible rather than forcing one semantic winner.",
        "- Preserve source checksum before/after every live processing run in batch reports.",
        "- Add fixture coverage for conversion failure, filename/SKU mismatch, unknown units, block-heavy, curve-heavy, ambiguous layers, and repeated SKU occurrences.",
        "",
        "## Recommended Regression Fixtures",
        *[f"- `{item['example_folder']}/{item['product_folder']}/{item.get('selected_top_view_cad_filename')}`: {reason}" for item, reason in regression],
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def recommended_fixtures(details: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str]]:
    picks: list[tuple[dict[str, Any], str]] = []
    wanted = [
        ("filename/SKU mismatch", lambda item: item.get("filename_sku_mismatch")),
        ("unknown-unit drawing", lambda item: "unknown-unit drawings" in item.get("families", [])),
        ("block-heavy drawing", lambda item: "block-heavy drawings" in item.get("families", [])),
        ("spline/ellipse-heavy drawing", lambda item: "spline/ellipse-heavy drawings" in item.get("families", [])),
        ("3D/non-zero-Z contaminated drawing", lambda item: "3D/non-zero-Z contaminated drawings" in item.get("families", [])),
        ("REGION-containing drawing", lambda item: "REGION-containing drawings" in item.get("families", [])),
        ("ambiguous/review-required layers", lambda item: "inconsistent layer-name schemes" in item.get("families", [])),
        ("conversion failure", lambda item: item.get("selected_top_view_cad_filename") and not item.get("conversion", {}).get("success")),
    ]
    used: set[str] = set()
    for reason, predicate in wanted:
        for item in details:
            key = f"{item['example_folder']}/{item['product_folder']}"
            if key not in used and predicate(item):
                picks.append((item, reason))
                used.add(key)
                break
    return picks[:12]


def issue_codes_for(item: dict[str, Any]) -> set[str]:
    codes = set(item.get("selection_issue_codes", []))
    if item.get("conversion", {}).get("error_code"):
        codes.add(str(item["conversion"]["error_code"]))
    for stage in item.get("stages", {}).values():
        if isinstance(stage, dict):
            codes.update(issue.get("code", "") for issue in stage.get("issues", []) if isinstance(issue, dict))
    return {code for code in codes if code}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _is_cad_like(path: Path) -> bool:
    name = path.name.casefold()
    return any(part in name for part in (".dwg", ".dxf")) or path.suffix.lower() in CAD_SUFFIXES


def ignored_cad_name(path: Path) -> bool:
    name = path.name.casefold()
    return name.endswith(IGNORED_SUFFIXES) or any(part in name for part in IGNORED_NAME_PARTS)


def filename_matches_sku(filename: str, sku: str) -> bool:
    found = SKU_RE.findall(filename)
    if not found:
        return True
    normalized = normalize_sku(sku)
    return any(normalize_sku(match) == normalized for match in found)


def normalize_sku(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def sanitize(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return clean.strip("._-") or "item"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())


