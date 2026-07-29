"""Real-world Stage 7 REGION verification.

This script is intentionally outside the default pytest suite. It converts the
known REGION-containing DWGs with ODA, runs Stage 7 read-only verification, and
writes detailed JSON/Markdown logs under the requested output root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf import bbox

from gebal_cad_normalizer.cad import OdaConversionRequest, OdaConverter, convert_regions, inventory_dxf
from gebal_cad_normalizer.cad.region_convert import REGION_EVIDENCE_APPID


DEFAULT_SOURCE_ROOT = Path(r"C:\Users\jvsin\Documents\DrafterMath Archive\CAD_Examples\Test_Examples")
DEFAULT_ODA_EXE = Path(r"E:\ODA\ODAFileConverter.exe")
DEFAULT_OUTPUT_ROOT = Path("tests/output/stage7_region_verification")
DEFAULT_CASES = (
    Path(r"Example2\175551M\top_view.dwg"),
    Path(r"Example3\104310M\top_view.dwg"),
    Path(r"Example3\137019M\top_view.dwg"),
    Path(r"Example5\175020\top_view.dwg"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify real-world DXF REGION representation after ODA conversion.")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--oda-exe", type=Path, default=DEFAULT_ODA_EXE)
    parser.add_argument("--target-version", default="ACAD2013")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--tolerance", type=float, default=1e-6)
    parser.add_argument("--case", action="append", type=Path, help="Relative or absolute DWG path. May be repeated.")
    args = parser.parse_args()

    started = time.perf_counter()
    output_root = args.output_root.resolve()
    dxf_root = output_root / "converted_dxfs"
    stage7_root = output_root / "stage7_outputs"
    output_root.mkdir(parents=True, exist_ok=True)
    dxf_root.mkdir(parents=True, exist_ok=True)
    stage7_root.mkdir(parents=True, exist_ok=True)

    cases = tuple(args.case) if args.case else DEFAULT_CASES
    converter = OdaConverter(args.oda_exe)
    files = [
        verify_file(
            resolve_case(args.source_root, case),
            converter,
            dxf_root,
            stage7_root,
            args.target_version,
            args.timeout_seconds,
            args.tolerance,
        )
        for case in cases
    ]

    summary = summarize(files, output_root, started)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "oda_executable": str(args.oda_exe),
        "target_version": args.target_version,
        "tolerance": args.tolerance,
        "source_root": str(args.source_root),
        "output_root": str(output_root),
        "summary": summary,
        "files": files,
    }
    json_path = output_root / "stage7_region_verification.json"
    markdown_path = output_root / "stage7_region_verification.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(payload, json_path), encoding="utf-8")
    print(json.dumps({"success": all(item["success"] for item in files), "json_path": str(json_path), "markdown_path": str(markdown_path), **summary}, sort_keys=True))
    return 0 if all(item["success"] for item in files) else 1


def verify_file(source_path: Path, converter: OdaConverter, dxf_root: Path, stage7_root: Path, target_version: str, timeout_seconds: float, tolerance: float) -> dict[str, Any]:
    source_before = sha256(source_path)
    relative_key = source_path.parts[-3:]
    dxf_path = dxf_root.joinpath(*relative_key).with_suffix(".dxf")
    conversion = converter.convert(
        OdaConversionRequest(source_path=source_path, destination_path=dxf_path, target_version=target_version, timeout_seconds=timeout_seconds)
    )
    source_after = sha256(source_path)
    dxf_before = sha256(dxf_path)
    before_inventory = inventory_dxf(dxf_path)
    before_counts = entity_counts(before_inventory)
    before_extents = before_inventory.modelspace_extents

    stage7 = convert_regions(dxf_path, tolerance=tolerance)
    dxf_after_read_only = sha256(dxf_path)
    after_inventory = inventory_dxf(dxf_path)
    after_counts = entity_counts(after_inventory)
    after_extents = after_inventory.modelspace_extents
    regions = inspect_regions(dxf_path, stage7.model_dump(mode="json"))

    stage7_output_path: str | None = None
    output_counts: dict[str, int] | None = None
    output_extents: dict[str, float] | None = None
    if stage7.converted_count > 0:
        destination = stage7_root.joinpath(*relative_key).with_suffix(".stage7.dxf")
        convert_regions(dxf_path, output_path=destination, tolerance=tolerance)
        stage7_output_path = str(destination)
        output_inventory = inventory_dxf(destination)
        output_counts = entity_counts(output_inventory)
        output_extents = output_inventory.modelspace_extents

    return {
        "success": source_before == source_after and dxf_before == dxf_after_read_only,
        "source_path": str(source_path),
        "source_sha256_before": source_before,
        "source_sha256_after": source_after,
        "source_checksum_preserved": source_before == source_after,
        "dxf_path": str(dxf_path),
        "dxf_sha256_before_stage7": dxf_before,
        "dxf_sha256_after_stage7_read_only": dxf_after_read_only,
        "dxf_checksum_preserved": dxf_before == dxf_after_read_only,
        "oda_elapsed_seconds": round(conversion.elapsed_seconds, 4),
        "oda_output_size_bytes": conversion.output_size_bytes,
        "entity_counts_before": before_counts,
        "entity_counts_after_read_only": after_counts,
        "entity_counts_preserved_read_only": before_counts == after_counts,
        "extents_before": before_extents,
        "extents_after_read_only": after_extents,
        "extents_preserved_read_only": before_extents == after_extents,
        "stage7_output_path": stage7_output_path,
        "stage7_output_entity_counts": output_counts,
        "stage7_output_extents": output_extents,
        "stage7": stage7.model_dump(mode="json"),
        "region_count": stage7.region_count,
        "regions": regions,
    }


def inspect_regions(dxf_path: Path, stage7: dict[str, Any]) -> list[dict[str, Any]]:
    doc = ezdxf.readfile(dxf_path)
    stage7_by_handle = {region.get("source_handle"): region for region in stage7.get("regions", [])}
    rows: list[dict[str, Any]] = []
    for index, entity in enumerate(entity for entity in doc.modelspace() if entity.dxftype() == "REGION"):
        handle = str(entity.dxf.handle)
        result = stage7_by_handle.get(handle, {})
        boundary = boundary_evidence(entity)
        acis = acis_evidence(entity, boundary)
        rows.append(
            {
                "index": index,
                "handle": handle,
                "layer": str(entity.dxf.get("layer", "0")),
                "color": entity.dxf.get("color"),
                "linetype": entity.dxf.get("linetype"),
                "extents": entity_extents(entity),
                "acis_evidence": acis,
                "boundary_evidence": boundary,
                "representation": classify_representation(acis, boundary, result),
                "conversion_status": result.get("status"),
                "issue_codes": result.get("issue_codes", []),
                "reason": reason(result, acis, boundary),
                "loops": loop_summary(result.get("loops", [])),
            }
        )
    return rows


def acis_evidence(entity: Any, boundary: dict[str, Any]) -> dict[str, Any]:
    try:
        data = bytes(entity.acis_data)
    except Exception:
        data = b""
    text = data.decode("latin-1", errors="ignore")
    keywords = sorted({name for name in ("ACIS BinaryFile", "body", "lump", "shell", "face", "loop", "coedge", "edge", "vertex", "spline", "ellipse", "plane") if name.lower() in text.lower()})
    sat = getattr(entity, "sat", ()) or ()
    return {
        "storage": "binary_acis" if data.startswith(b"ACIS BinaryFile") else ("text_sat" if sat else "none"),
        "byte_count": len(data),
        "sha256": hashlib.sha256(data).hexdigest() if data else None,
        "sat_string_count": len(sat),
        "keywords": keywords,
        "is_opaque_to_stage7": bool(data or sat) and not boundary["has_supported_xdata"],
    }


def boundary_evidence(entity: Any) -> dict[str, Any]:
    try:
        tags = entity.get_xdata(REGION_EVIDENCE_APPID)
    except Exception:
        tags = []
    payload = "".join(str(tag.value) for tag in tags if int(tag.code) == 1000)
    loop_count = None
    roles: list[str] = []
    if payload:
        try:
            parsed = json.loads(payload)
            loops = parsed.get("loops") if isinstance(parsed, dict) else None
            if isinstance(loops, list):
                loop_count = len(loops)
                roles = sorted(str(loop.get("role", "outer")) for loop in loops if isinstance(loop, dict))
        except json.JSONDecodeError:
            pass
    return {"supported_appid": REGION_EVIDENCE_APPID, "has_supported_xdata": bool(payload), "payload_bytes": len(payload.encode("utf-8")), "loop_count": loop_count, "loop_roles": roles}


def classify_representation(acis: dict[str, Any], boundary: dict[str, Any], result: dict[str, Any]) -> str:
    if boundary["has_supported_xdata"] and result.get("status") in {"converted", "approximated"}:
        return "convertible_closed_loops"
    if boundary["has_supported_xdata"]:
        return "malformed_or_unsupported_boundary_evidence"
    if acis["storage"] in {"binary_acis", "text_sat"}:
        return "opaque_acis_region"
    return "malformed_or_unsupported_region"


def reason(result: dict[str, Any], acis: dict[str, Any], boundary: dict[str, Any]) -> str:
    if result.get("status") in {"converted", "approximated"}:
        return "Supported deterministic boundary loops were consumed."
    if not boundary["has_supported_xdata"] and acis["storage"] == "binary_acis":
        return "ODA preserved the REGION as binary ACIS body data only; no supported boundary-loop evidence is available."
    issues = result.get("issue_codes") or []
    return "Stage 7 reported: " + ", ".join(issues) if issues else "No supported conversion evidence was found."


def loop_summary(loops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "loop_id": loop.get("loop_id"),
            "role": loop.get("role"),
            "parent_loop_id": loop.get("parent_loop_id"),
            "vertex_count": len(loop.get("vertices") or []),
            "is_closed": loop.get("is_closed"),
            "area": loop.get("area"),
            "perimeter": loop.get("perimeter"),
        }
        for loop in loops
    ]


def entity_extents(entity: Any) -> dict[str, float] | None:
    try:
        ext = bbox.extents([entity], fast=True)
    except Exception:
        return None
    if not ext.has_data:
        return None
    return {"min_x": float(ext.extmin.x), "min_y": float(ext.extmin.y), "min_z": float(ext.extmin.z), "max_x": float(ext.extmax.x), "max_y": float(ext.extmax.y), "max_z": float(ext.extmax.z)}


def summarize(files: list[dict[str, Any]], output_root: Path, started: float) -> dict[str, Any]:
    statuses = Counter(region["conversion_status"] for item in files for region in item["regions"])
    representations = Counter(region["representation"] for item in files for region in item["regions"])
    return {
        "files_verified": len(files),
        "region_count": sum(item["region_count"] for item in files),
        "counts_by_status": dict(sorted(statuses.items())),
        "counts_by_representation": dict(sorted(representations.items())),
        "all_source_checksums_preserved": all(item["source_checksum_preserved"] for item in files),
        "all_dxf_checksums_preserved_read_only": all(item["dxf_checksum_preserved"] for item in files),
        "all_entity_counts_preserved_read_only": all(item["entity_counts_preserved_read_only"] for item in files),
        "all_extents_preserved_read_only": all(item["extents_preserved_read_only"] for item in files),
        "stage7_outputs_written": sum(1 for item in files if item["stage7_output_path"]),
        "runtime_seconds": round(time.perf_counter() - started, 4),
        "output_root": str(output_root),
    }


def render_markdown(payload: dict[str, Any], json_path: Path) -> str:
    lines = [
        "# Stage 7 Live REGION Verification",
        "",
        f"- JSON log: `{json_path}`",
        f"- ODA executable: `{payload['oda_executable']}`",
        f"- Target version: `{payload['target_version']}`",
        f"- REGION count: {payload['summary']['region_count']}",
        f"- Status counts: `{payload['summary']['counts_by_status']}`",
        f"- Representation counts: `{payload['summary']['counts_by_representation']}`",
        f"- Source checksums preserved: {payload['summary']['all_source_checksums_preserved']}",
        f"- DXF checksums preserved by read-only Stage 7: {payload['summary']['all_dxf_checksums_preserved_read_only']}",
        f"- Entity counts preserved by read-only Stage 7: {payload['summary']['all_entity_counts_preserved_read_only']}",
        f"- Extents preserved by read-only Stage 7: {payload['summary']['all_extents_preserved_read_only']}",
        "",
        "## Files",
    ]
    for item in payload["files"]:
        source = Path(item["source_path"])
        lines.extend(
            [
                "",
                f"### {source.parts[-3]}/{source.parts[-2]}/{source.name}",
                f"- REGIONs: {item['region_count']}",
                f"- Converted DXF: `{item['dxf_path']}`",
                f"- Source checksum preserved: {item['source_checksum_preserved']}",
                f"- DXF checksum preserved: {item['dxf_checksum_preserved']}",
                f"- Entity counts preserved: {item['entity_counts_preserved_read_only']}",
                f"- Extents preserved: {item['extents_preserved_read_only']}",
            ]
        )
        for region in item["regions"]:
            lines.append(
                f"- REGION `{region['handle']}` layer `{region['layer']}`: {region['representation']}; "
                f"status `{region['conversion_status']}`; issues `{', '.join(region['issue_codes'])}`; "
                f"ACIS `{region['acis_evidence']['storage']}` {region['acis_evidence']['byte_count']} bytes; "
                f"boundary XDATA {region['boundary_evidence']['has_supported_xdata']}; {region['reason']}"
            )
    return "\n".join(lines) + "\n"


def entity_counts(inventory: Any) -> dict[str, int]:
    return {item.dxf_type: item.count for item in inventory.entity_counts}


def resolve_case(source_root: Path, case: Path) -> Path:
    path = case if case.is_absolute() else source_root / case
    return Path(str(path).replace("TEST_EXAMPLES", "Test_Examples"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    sys.exit(main())
