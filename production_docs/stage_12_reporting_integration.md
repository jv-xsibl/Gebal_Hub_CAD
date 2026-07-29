# Stage 12 Reporting Integration

Stage 12 is the durable package boundary for one product occurrence. It orchestrates the existing deterministic stages and does not duplicate CAD inventory, canonicalization, REGION conversion, classification, layer rewrite, SVG export, measurement, validation, or ODA conversion logic.

## Entry Points

- Python API: `gebal_cad_normalizer.reporting.run_reporting_pipeline(...)`
- Live CLI: `python tests/live/stage12_live_pipeline.py --json <file> --input <dwg-or-dxf> --output-dir <dir> [--oda-exe ...] [--vendor-profile bluestone_playground] [--unit ...] [--export-dwg]`

Inputs are read-only. The source JSON and source CAD SHA-256 values are captured before processing and checked again before package promotion.

## Package Layout

Each run writes one occurrence directory:

```text
<output>/<occurrence_id>/
  manifest.json
  report.md
  normalized/
    <sku>_normalized.dxf
    <sku>_normalized.dwg        # only when --export-dwg succeeds
    <source>_source.dxf         # only when input DWG conversion is needed
    <sku>_regions.dxf          # when Stage 7 writes a converted copy
  svg/
    manifest.json
    combined.svg
    layers/*.svg
  reports/
    inventory.json
    canonical.json
    region_conversion.json
    classification.json
    rewrite.json
    measurement.json
    validation.json
```

The final directory is promoted atomically from a staging directory. Existing occurrence packages are not overwritten unless `allow_overwrite=True` or `--allow-overwrite` is supplied.

## Manifest Content

`manifest.json` records:

- occurrence ID and SKU;
- selected source JSON and CAD paths;
- selected CAD candidate, alternatives, and selection evaluations when present in JSON;
- filename/SKU mismatch evidence;
- source checksums and checksum-preservation status;
- stage statuses and timings;
- generated artifact paths and checksums;
- categorized source-data, CAD, classification, measurement, and validation issues;
- final file list with checksums and sizes;
- `ai_used=false`.

## Status Logic

Overall status uses the required vocabulary: `pass`, `pass_with_warnings`, `review_required`, `fail`.

- `fail`: any required stage fails, source checksum changes, or Stage 11 returns `fail`.
- `review_required`: Stage 11 returns `review_required` or only not-verifiable top-view checks are available.
- `pass_with_warnings`: no failure, but warnings, inferred/unknown units, unverifiable fields, or validation warnings are present.
- `pass`: all stages complete without warning/fail issues and validation passes.

Stage 11 `not_verifiable` is mapped to Stage 12 `review_required` at the overall package level so operators do not mistake missing verification for a clean pass.

## Partial Failures

Every processing step is wrapped. If a stage fails, Stage 12 records the exception type, stable error code when available, message, stage status, and category. It still writes `manifest.json` and `report.md` and promotes the package, unless the failure occurs before input files can be read or an existing output package blocks promotion.

## Review Evidence

The report and manifest explicitly preserve:

- unknown or missing units from inventory, classification, measurement, and validation;
- unverifiable top-view fields such as height and free-fall height;
- selected candidates and alternatives;
- filename/SKU mismatch tokens;
- measurement candidates, validation rankings, and validation issue codes in `reports/measurement.json` and `reports/validation.json`.


## Hang Diagnosis And Reliability

Stage 12 records `stage_events`, `stage_timeouts_seconds`, and per-stage `timings_seconds` in each manifest. Expensive stages run with configurable child-process timeouts where safely possible: input conversion, canonicalization, REGION conversion, SVG export, measurement, and DWG export. If a child stage exceeds its timeout, Stage 12 terminates it, records `stage_timeout` as a fail issue, writes any partial manifest/report evidence available, and promotes the occurrence package unless failure happened before the package boundary could be established.

Stage 10/Stage 6/Stage 9.5 reliability caps now report fail/review issues instead of silently truncating expensive processing. The Stage 13 regression occurrence `Example2/137004M` previously blocked in Stage 10 measurement: large curve flattening fed O(n^2) self-intersection checks. The fixed run fails cleanly with `measurement_failed` evidence for curve tessellation and self-intersection comparison caps.

## Verification Log

Representative live verification output should be written under:

```text
tests/output/stage12_live/
```

Use `tests/output/stage12_live/stage12_live_summary.json` for the production run summary when recording real-pair findings.
