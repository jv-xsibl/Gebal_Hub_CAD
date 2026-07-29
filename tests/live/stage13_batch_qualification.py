"""Stage 13 read-only batch end-to-end qualification.

Runs Stage 12 once per discovered product occurrence and aggregates the
resulting package manifests. Source archive files are only read.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any



DEFAULT_SOURCE_ROOT = Path(r"C:\Users\jvsin\Documents\DrafterMath Archive\CAD_Examples\Test_Examples")
DEFAULT_OUTPUT_ROOT = Path("tests/output/stage13_qualification")
DEFAULT_ODA_EXE = Path(r"E:\ODA\ODAFileConverter.exe")
EXAMPLES = tuple(f"Example{index}" for index in range(1, 6))
IGNORED_DIRS = {"debug_crops", "output", "outputs", "__pycache__"}
IGNORED_NAME_PARTS = ("side_view", "side-view", "side view", "product_model", "product-model", "output")
IGNORED_SUFFIXES = (".bak", ".original")
SKU_RE = re.compile(r"(?i)(?<![A-Z0-9])([A-Z]?\d{5,6}_?M?|\d{5,6})(?![A-Z0-9])")
STATUSES = ("pass", "pass_with_warnings", "review_required", "fail")
CATEGORIES = ("source_data", "cad", "units", "classification", "measurement", "validation", "pipeline_errors")


@dataclass(frozen=True)
class Occurrence:
    example: str
    product_folder: str
    json_path: Path
    cad_path: Path | None
    sku: str
    selected_cad_filename: str | None
    competing_cad_filenames: tuple[str, ...]
    ignored_cad_filenames: tuple[str, ...]
    selection_issues: tuple[str, ...]
    repeated_sku: bool = False

    @property
    def key(self) -> str:
        return f"{self.example}/{self.product_folder}/{self.json_path.name}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Stage 13 batch end-to-end qualification.")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--oda-exe", type=Path, default=DEFAULT_ODA_EXE)
    parser.add_argument("--unit", choices=("mm", "cm", "m", "in"), help="Explicit Stage 12 drawing-unit override.")
    parser.add_argument("--allow-overwrite", action="store_true")
    parser.add_argument("--occurrence-timeout", type=float, default=240.0, help="Seconds allowed for one Stage 12 occurrence.")
    parser.add_argument("--sku", action="append", help="Optional SKU filter. Can be repeated.")
    parser.add_argument("--bypass-sku", action="append", help="Record matching SKU as a bypassed Stage 12 timeout/fail case without running it.")
    parser.add_argument("--example", action="append", choices=EXAMPLES, help="Optional example-folder filter. Can be repeated.")
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    all_occurrences = discover_occurrences(source_root)
    occurrences = filter_occurrences(all_occurrences, args.sku, args.example)
    details = []

    for index, occurrence in enumerate(occurrences, start=1):
        print(f"[{index}/{len(occurrences)}] {occurrence.example}/{occurrence.product_folder} {occurrence.sku}")
        details.append(run_occurrence(occurrence, output_root, args.oda_exe, args.unit, args.allow_overwrite, args.occurrence_timeout))

    summary = build_summary(details, all_occurrences, source_root, output_root, args.unit, time.perf_counter() - started)
    payload = {
        "stage": "13",
        "source_root": str(source_root),
        "output_root": str(output_root),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "vendor_profile": "bluestone_playground",
        "ai_used": False,
        "unit_override": args.unit,
        "unit_override_explicit": args.unit is not None,
        "summary": summary,
        "details": details,
    }
    write_json(output_root / "qualification_results.json", payload)
    write_matrix(output_root / "qualification_matrix.csv", details)
    write_summary_md(output_root / "qualification_summary.md", summary, details)
    print(json.dumps({"occurrences": len(details), "statuses": summary["status_counts"], "output_root": str(output_root)}, indent=2, sort_keys=True))
    return 0


def discover_occurrences(source_root: Path) -> list[Occurrence]:
    raw: list[Occurrence] = []
    for example in EXAMPLES:
        example_dir = source_root / example
        if not example_dir.is_dir():
            continue
        for product_dir in sorted((item for item in example_dir.iterdir() if item.is_dir()), key=lambda item: item.name.casefold()):
            if product_dir.name.casefold() in IGNORED_DIRS:
                continue
            files = [path for path in product_dir.iterdir() if path.is_file()]
            json_files = sorted((path for path in files if path.suffix.casefold() == ".json"), key=lambda item: item.name.casefold())
            cad_files = sorted((path for path in files if is_cad_like(path)), key=lambda item: item.name.casefold())
            if not json_files:
                continue
            for json_path in json_files:
                sku = extract_json_sku(json_path) or product_dir.name
                selected, competing, ignored, issues = select_top_view(cad_files, sku)
                raw.append(
                    Occurrence(
                        example=example,
                        product_folder=product_dir.name,
                        json_path=json_path,
                        cad_path=selected,
                        sku=sku,
                        selected_cad_filename=selected.name if selected else None,
                        competing_cad_filenames=tuple(path.name for path in competing),
                        ignored_cad_filenames=tuple(path.name for path in ignored),
                        selection_issues=tuple(issues),
                    )
                )
    sku_counts = Counter(normalize_sku(item.sku) for item in raw)
    return [Occurrence(**(item.__dict__ | {"repeated_sku": sku_counts[normalize_sku(item.sku)] > 1})) for item in raw]


def filter_occurrences(occurrences: list[Occurrence], sku_filters: list[str] | None, example_filters: list[str] | None) -> list[Occurrence]:
    sku_set = {normalize_sku(item) for item in sku_filters or []}
    example_set = set(example_filters or [])
    return [
        item
        for item in occurrences
        if (not sku_set or normalize_sku(item.sku) in sku_set or normalize_sku(item.product_folder) in sku_set)
        and (not example_set or item.example in example_set)
    ]


def run_occurrence(occurrence: Occurrence, output_root: Path, oda_exe: Path, unit: str | None, allow_overwrite: bool, occurrence_timeout: float = 240.0) -> dict[str, Any]:
    start = time.perf_counter()
    base = occurrence_record(occurrence)
    base["unit_override"] = unit
    base["unit_override_explicit"] = unit is not None
    base["source_checksums"] = {"json_sha256_before": sha256(occurrence.json_path)}
    if occurrence.cad_path:
        base["source_checksums"]["cad_sha256_before"] = sha256(occurrence.cad_path)
    if occurrence.cad_path is None:
        base.update({"overall_status": "fail", "package_generated": False, "pipeline_errors": [{"code": "missing_top_view_file", "message": "No valid top-view CAD file was found."}]})
        base["runtime_seconds"] = round(time.perf_counter() - start, 6)
        base["failure_categories"] = categorize_failures(base)
        return base

    try:
        packages_root = output_root / "packages"
        command = [
            sys.executable,
            str(Path(__file__).with_name("stage12_live_pipeline.py")),
            "--json",
            str(occurrence.json_path),
            "--input",
            str(occurrence.cad_path),
            "--output-dir",
            str(packages_root),
            "--oda-exe",
            str(oda_exe),
            "--vendor-profile",
            "bluestone_playground",
            "--export-dwg",
        ]
        if unit:
            command.extend(["--unit", unit])
        if allow_overwrite:
            command.append("--allow-overwrite")
        completed = subprocess.run(command, cwd=Path.cwd(), capture_output=True, text=True, timeout=occurrence_timeout)
        base["stage12_command"] = command
        base["stage12_returncode"] = completed.returncode
        base["stage12_stdout_tail"] = completed.stdout[-4000:]
        base["stage12_stderr_tail"] = completed.stderr[-4000:]
        manifest = find_manifest(packages_root, occurrence)
        if manifest is None:
            raise RuntimeError(f"Stage 12 did not produce a manifest; return code {completed.returncode}")
        package_path = output_root / "packages" / manifest["occurrence_id"]
        base.update(
            {
                "overall_status": manifest.get("overall_status", "fail"),
                "package_generated": package_path.exists(),
                "package_path": str(package_path),
                "manifest_path": str(package_path / "manifest.json"),
                "report_path": str(package_path / "report.md"),
                "stage_statuses": manifest.get("stage_statuses", {}),
                "issue_counts": manifest.get("issue_counts", {}),
                "source_checksums_unchanged": manifest.get("source_checksums_unchanged"),
                "filename_mismatch": manifest.get("filename_mismatch_evidence", {}).get("mismatch"),
                "occurrence_id": manifest.get("occurrence_id"),
                "stage12_manifest": summarize_manifest(manifest),
            }
        )
        if completed.returncode != 0:
            base.setdefault("pipeline_errors", []).append({"code": "stage12_nonzero_exit", "message": f"Stage 12 exited with {completed.returncode}."})
    except Exception as exc:
        code = "stage12_occurrence_timeout" if isinstance(exc, subprocess.TimeoutExpired) else type(exc).__name__
        message = f"Stage 12 exceeded {occurrence_timeout}s." if isinstance(exc, subprocess.TimeoutExpired) else str(exc)
        base.update(
            {
                "overall_status": "fail",
                "package_generated": False,
                "pipeline_errors": [{"code": code, "message": message}],
                "source_checksums_unchanged": occurrence.json_path.exists() and occurrence.cad_path.exists() and base["source_checksums"]["json_sha256_before"] == sha256(occurrence.json_path) and base["source_checksums"]["cad_sha256_before"] == sha256(occurrence.cad_path),
            }
        )
    finally:
        base["source_checksums"]["json_sha256_after"] = sha256(occurrence.json_path)
        if occurrence.cad_path:
            base["source_checksums"]["cad_sha256_after"] = sha256(occurrence.cad_path)
        base["runtime_seconds"] = round(time.perf_counter() - start, 6)
        base["failure_categories"] = categorize_failures(base)
    return base


def find_manifest(packages_root: Path, occurrence: Occurrence) -> dict[str, Any] | None:
    if not packages_root.exists():
        return None
    expected_json = str(occurrence.json_path)
    expected_cad = str(occurrence.cad_path)
    matches: list[tuple[float, dict[str, Any]]] = []
    for path in packages_root.glob("*/manifest.json"):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        source_paths = manifest.get("source_paths", {})
        if source_paths.get("json") == expected_json and source_paths.get("cad") == expected_cad:
            matches.append((path.stat().st_mtime, manifest))
    return sorted(matches, key=lambda item: item[0])[-1][1] if matches else None

def occurrence_record(occurrence: Occurrence) -> dict[str, Any]:
    return {
        "example_folder": occurrence.example,
        "product_folder": occurrence.product_folder,
        "sku": occurrence.sku,
        "json_path": str(occurrence.json_path),
        "cad_path": str(occurrence.cad_path) if occurrence.cad_path else None,
        "selected_cad_filename": occurrence.selected_cad_filename,
        "competing_cad_filenames": list(occurrence.competing_cad_filenames),
        "ignored_cad_filenames": list(occurrence.ignored_cad_filenames),
        "selection_issues": list(occurrence.selection_issues),
        "repeated_sku": occurrence.repeated_sku,
        "filename_mismatch": bool(occurrence.cad_path and not filename_matches_sku(occurrence.cad_path.name, occurrence.sku)),
    }


def summarize_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "sku": manifest.get("sku"),
        "occurrence_id": manifest.get("occurrence_id"),
        "overall_status": manifest.get("overall_status"),
        "elapsed_seconds": manifest.get("elapsed_seconds"),
        "stage_statuses": manifest.get("stage_statuses", {}),
        "issue_counts": manifest.get("issue_counts", {}),
        "source_checksums_unchanged": manifest.get("source_checksums_unchanged"),
        "artifacts": manifest.get("artifacts", {}),
    }


def build_summary(details: list[dict[str, Any]], occurrences: list[Occurrence], source_root: Path, output_root: Path, unit: str | None, elapsed: float) -> dict[str, Any]:
    status_counts = {status: sum(1 for item in details if item.get("overall_status") == status) for status in STATUSES}
    conversion_success = sum(1 for item in details if item.get("stage_statuses", {}).get("input_conversion") == "pass")
    package_success = sum(1 for item in details if item.get("package_generated"))
    source_preserved = sum(1 for item in details if item.get("source_checksums_unchanged") is True)
    failures_by_category = {category: dict(counter) for category, counter in grouped_failure_counts(details).items()}
    runtimes = [float(item.get("runtime_seconds", 0)) for item in details]
    return {
        "source_root": str(source_root),
        "output_root": str(output_root),
        "folders_scanned": len({f"{item.example}/{item.product_folder}" for item in occurrences}),
        "examples_scanned": sorted({item.example for item in occurrences}),
        "occurrences_processed": len(details),
        "status_counts": status_counts,
        "conversion_success_rate": rate(conversion_success, len(details)),
        "package_generation_success_rate": rate(package_success, len(details)),
        "source_checksum_preservation_rate": rate(source_preserved, len(details)),
        "source_checksums_preserved": source_preserved,
        "failures_by_category": failures_by_category,
        "cleanest_successful_demo_cases": cleanest_cases(details),
        "most_important_review_fail_cases": important_cases(details),
        "repeated_sku_cases": repeated_sku_cases(details),
        "filename_mismatch_cases": filename_mismatch_cases(details),
        "average_runtime_seconds": round(sum(runtimes) / len(runtimes), 4) if runtimes else 0.0,
        "slowest_runtime": slowest_runtime(details),
        "recommended_regression_fixtures": recommended_fixtures(details),
        "remaining_blockers_before_production": remaining_blockers(details, unit),
        "unit_override": unit,
        "unit_override_explicit": unit is not None,
        "runtime_seconds": round(elapsed, 4),
    }


def grouped_failure_counts(details: list[dict[str, Any]]) -> dict[str, Counter[str]]:
    grouped: dict[str, Counter[str]] = {category: Counter() for category in CATEGORIES}
    for item in details:
        for issue in item.get("selection_issues", []):
            grouped["source_data"][issue] += 1
        for error in item.get("pipeline_errors", []):
            grouped["pipeline_errors"][error.get("code", "pipeline_error")] += 1
        for category, counts in item.get("issue_counts", {}).items():
            target = category if category in grouped else "pipeline_errors"
            for code, count in counts.items():
                grouped[target][code] += count
                if "unit" in code:
                    grouped["units"][code] += count
        if item.get("overall_status") == "fail" and not item.get("issue_counts") and not item.get("pipeline_errors"):
            grouped["pipeline_errors"]["uncategorized_fail"] += 1
    return grouped


def categorize_failures(item: dict[str, Any]) -> list[str]:
    categories = set()
    if item.get("selection_issues"):
        categories.add("source_data")
    if item.get("pipeline_errors"):
        categories.add("pipeline_errors")
    for category, counts in item.get("issue_counts", {}).items():
        if counts:
            categories.add(category)
        if any("unit" in code for code in counts):
            categories.add("units")
    return sorted(categories)


def cleanest_cases(details: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    candidates = [item for item in details if item.get("package_generated") and item.get("stage_statuses", {}).get("input_conversion") == "pass"]
    ranked = sorted(candidates, key=lambda item: (status_rank(item.get("overall_status")), issue_total(item), item.get("runtime_seconds", 0), item["example_folder"], item["product_folder"]))
    return [case_summary(item) for item in ranked[:limit]]


def important_cases(details: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    candidates = [item for item in details if item.get("overall_status") in {"review_required", "fail"}]
    ranked = sorted(candidates, key=lambda item: (-importance_score(item), item["example_folder"], item["product_folder"]))
    return [case_summary(item) for item in ranked[:limit]]


def recommended_fixtures(details: list[dict[str, Any]]) -> list[dict[str, str]]:
    checks = [
        ("unit handling without silent assumption", lambda item: "units" in item.get("failure_categories", [])),
        ("filename/SKU mismatch", lambda item: item.get("filename_mismatch")),
        ("repeated SKU occurrence identity", lambda item: item.get("repeated_sku")),
        ("classification review pressure", lambda item: bool(item.get("issue_counts", {}).get("classification"))),
        ("measurement gap or ambiguity", lambda item: bool(item.get("issue_counts", {}).get("measurement"))),
        ("validation mismatch", lambda item: bool(item.get("issue_counts", {}).get("validation"))),
        ("package failure continuation", lambda item: item.get("pipeline_errors")),
    ]
    picks = []
    used = set()
    for reason, predicate in checks:
        for item in details:
            key = f"{item['example_folder']}/{item['product_folder']}"
            if key not in used and predicate(item):
                picks.append({"case": key, "sku": item["sku"], "cad": item.get("selected_cad_filename") or "", "reason": reason})
                used.add(key)
                break
    return picks


def remaining_blockers(details: list[dict[str, Any]], unit: str | None) -> list[str]:
    blockers = []
    if unit is None and any("units" in item.get("failure_categories", []) for item in details):
        blockers.append("Vendor CAD units are unknown in the source drawings; production must require explicit units or validated metadata.")
    if any(item.get("overall_status") == "fail" for item in details):
        blockers.append("Stage 12 validation failures remain and must be reviewed before unattended production use.")
    if any(item.get("filename_mismatch") for item in details):
        blockers.append("Filename/SKU mismatches need operator or vendor-data resolution.")
    if any(item.get("repeated_sku") for item in details):
        blockers.append("Repeated SKUs must stay occurrence-scoped in production logs and regression fixtures.")
    if any(item.get("source_checksums_unchanged") is not True for item in details):
        blockers.append("Any source checksum preservation failure blocks production promotion.")
    return blockers or ["No batch-level blocker found beyond case-level review items."]


def write_matrix(path: Path, details: list[dict[str, Any]]) -> None:
    fields = [
        "example_folder",
        "product_folder",
        "sku",
        "selected_cad_filename",
        "overall_status",
        "package_generated",
        "source_checksums_unchanged",
        "filename_mismatch",
        "repeated_sku",
        "unit_override",
        "input_conversion",
        "dwg_export",
        "issue_total",
        "failure_categories",
        "runtime_seconds",
        "package_path",
        "report_path",
        "selection_issues",
    ]
    rows = []
    for item in details:
        rows.append(
            {
                "example_folder": item["example_folder"],
                "product_folder": item["product_folder"],
                "sku": item["sku"],
                "selected_cad_filename": item.get("selected_cad_filename") or "",
                "overall_status": item.get("overall_status", ""),
                "package_generated": item.get("package_generated", False),
                "source_checksums_unchanged": item.get("source_checksums_unchanged", ""),
                "filename_mismatch": item.get("filename_mismatch", False),
                "repeated_sku": item.get("repeated_sku", False),
                "unit_override": item.get("unit_override") or "",
                "input_conversion": item.get("stage_statuses", {}).get("input_conversion", ""),
                "dwg_export": item.get("stage_statuses", {}).get("dwg_export", ""),
                "issue_total": issue_total(item),
                "failure_categories": ";".join(item.get("failure_categories", [])),
                "runtime_seconds": item.get("runtime_seconds", ""),
                "package_path": item.get("package_path", ""),
                "report_path": item.get("report_path", ""),
                "selection_issues": ";".join(item.get("selection_issues", [])),
            }
        )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_summary_md(path: Path, summary: dict[str, Any], details: list[dict[str, Any]]) -> None:
    lines = [
        "# Stage 13 End-to-End Qualification Summary",
        "",
        f"- Source root: `{summary['source_root']}`",
        f"- Output root: `{summary['output_root']}`",
        f"- Folders scanned: {summary['folders_scanned']}",
        f"- Occurrences processed: {summary['occurrences_processed']}",
        f"- Unit override: `{summary['unit_override'] or 'none'}`; explicit override: `{summary['unit_override_explicit']}`",
        f"- Status counts: {json.dumps(summary['status_counts'], sort_keys=True)}",
        f"- Conversion success rate: {summary['conversion_success_rate']}",
        f"- Package-generation success rate: {summary['package_generation_success_rate']}",
        f"- Source checksum preservation: {summary['source_checksum_preservation_rate']}",
        f"- Average runtime seconds: {summary['average_runtime_seconds']}",
        f"- Slowest runtime: `{summary['slowest_runtime'].get('case', '')}` at {summary['slowest_runtime'].get('runtime_seconds', 0)}s",
        "",
        "## Failures By Category",
    ]
    for category in CATEGORIES:
        counts = summary["failures_by_category"].get(category, {})
        lines.extend([f"- {category}: {json.dumps(counts, sort_keys=True) if counts else 'none'}"])
    lines.extend(["", "## Cleanest Successful Demo Cases"])
    lines.extend(case_line(item) for item in summary["cleanest_successful_demo_cases"])
    lines.extend(["", "## Most Important Review/Fail Cases"])
    lines.extend(case_line(item) for item in summary["most_important_review_fail_cases"])
    lines.extend(["", "## Repeated SKU Cases"])
    lines.extend(case_line(item) for item in summary["repeated_sku_cases"] or [{"case": "None", "status": "", "issue_total": 0, "cad": ""}])
    lines.extend(["", "## Filename Mismatch Cases"])
    lines.extend(case_line(item) for item in summary["filename_mismatch_cases"] or [{"case": "None", "status": "", "issue_total": 0, "cad": ""}])
    lines.extend(["", "## Recommended Regression Fixtures"])
    lines.extend(f"- `{item['case']}` ({item['sku']}): {item['reason']}; CAD `{item['cad']}`" for item in summary["recommended_regression_fixtures"])
    lines.extend(["", "## Remaining Blockers Before Production Use"])
    lines.extend(f"- {item}" for item in summary["remaining_blockers_before_production"])
    lines.extend(["", "## Package Log Paths"])
    lines.extend(f"- `{item['example_folder']}/{item['product_folder']}`: `{item.get('package_path', '')}`" for item in details if item.get("package_path"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def select_top_view(cad_files: list[Path], sku: str) -> tuple[Path | None, list[Path], list[Path], list[str]]:
    ignored = [path for path in cad_files if ignored_cad_name(path)]
    candidates = [path for path in cad_files if path not in ignored]
    top = [path for path in candidates if "top" in path.name.casefold() and "view" in path.name.casefold()]
    issues = []
    if not top:
        return None, candidates, ignored, ["missing_top_view_file"]
    scored = sorted(((top_view_score(path, sku), path) for path in top), key=lambda pair: (-pair[0], pair[1].name.casefold()))
    selected = scored[0][1]
    if len(top) > 1:
        issues.append("multiple_top_view_candidates")
    if sum(1 for score, _path in scored if score == scored[0][0]) > 1:
        issues.append("ambiguous_top_view_file")
    if not filename_matches_sku(selected.name, sku):
        issues.append("filename_sku_mismatch")
    return selected, candidates, ignored, issues


def top_view_score(path: Path, sku: str) -> int:
    name = path.name.casefold()
    score = 0
    if path.suffix.casefold() == ".dwg":
        score += 20
    if name == "top_view.dwg":
        score += 30
    if "top_view" in name or "top view" in name:
        score += 20
    if normalize_sku(sku) and normalize_sku(sku) in normalize_sku(name):
        score += 10
    return score


def is_cad_like(path: Path) -> bool:
    name = path.name.casefold()
    return path.suffix.casefold() in {".dwg", ".dxf"} or ".dwg" in name or ".dxf" in name


def ignored_cad_name(path: Path) -> bool:
    name = path.name.casefold()
    return name.endswith(IGNORED_SUFFIXES) or any(part in name for part in IGNORED_NAME_PARTS)


def extract_json_sku(path: Path) -> str | None:
    try:
        return find_sku(json.loads(path.read_text(encoding="utf-8-sig")))
    except Exception:
        return None


def find_sku(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("sku", "productNumber", "product_number", "productNo", "product_no", "itemNumber", "code"):
            if key in value and isinstance(value[key], (str, int)):
                text = str(value[key]).strip()
                if SKU_RE.search(text):
                    return text
        for child in value.values():
            found = find_sku(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_sku(child)
            if found:
                return found
    return None


def filename_matches_sku(filename: str, sku: str) -> bool:
    found = SKU_RE.findall(filename)
    return not found or any(normalize_sku(match) == normalize_sku(sku) for match in found)


def normalize_sku(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def issue_total(item: dict[str, Any]) -> int:
    return sum(sum(counts.values()) for counts in item.get("issue_counts", {}).values()) + len(item.get("pipeline_errors", [])) + len(item.get("selection_issues", []))


def importance_score(item: dict[str, Any]) -> int:
    return issue_total(item) + 100 * int(item.get("overall_status") == "fail") + 10 * int(item.get("filename_mismatch", False))


def status_rank(status: str | None) -> int:
    return {"pass": 0, "pass_with_warnings": 1, "review_required": 2, "fail": 3}.get(status or "fail", 3)


def case_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "case": f"{item['example_folder']}/{item['product_folder']}",
        "sku": item["sku"],
        "cad": item.get("selected_cad_filename") or "",
        "status": item.get("overall_status", ""),
        "issue_total": issue_total(item),
        "runtime_seconds": item.get("runtime_seconds", 0),
        "package_path": item.get("package_path", ""),
    }


def repeated_sku_cases(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [case_summary(item) for item in details if item.get("repeated_sku")]


def filename_mismatch_cases(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [case_summary(item) for item in details if item.get("filename_mismatch")]


def slowest_runtime(details: list[dict[str, Any]]) -> dict[str, Any]:
    return case_summary(max(details, key=lambda item: item.get("runtime_seconds", 0))) if details else {}


def case_line(item: dict[str, Any]) -> str:
    return f"- `{item.get('case', '')}` {item.get('sku', '')}: `{item.get('status', '')}`, issues {item.get('issue_total', 0)}, CAD `{item.get('cad', '')}`"


def rate(value: int, total: int) -> str:
    return f"{value}/{total} ({(value / total * 100):.1f}%)" if total else "0/0 (0.0%)"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())







