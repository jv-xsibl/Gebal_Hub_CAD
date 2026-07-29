# Stage 13 End-to-End Qualification

Stage 13 is a read-only batch qualification wrapper over the Stage 12 package API. It discovers product occurrences in the Bluestone playground archive, pairs each product JSON with the selected top-view DWG, runs Stage 12 once per occurrence, and writes a batch matrix, JSON result set, and Markdown summary.

## Entry Point

```powershell
python tests\live\stage13_batch_qualification.py --source-root "C:\Users\jvsin\Documents\DrafterMath Archive\CAD_Examples\Test_Examples" --output-root tests\output\stage13_qualification\no_unit --oda-exe E:\ODA\ODAFileConverter.exe --allow-overwrite
```

Optional explicit unit comparison:

```powershell
python tests\live\stage13_batch_qualification.py --source-root "C:\Users\jvsin\Documents\DrafterMath Archive\CAD_Examples\Test_Examples" --output-root tests\output\stage13_qualification\unit_mm --oda-exe E:\ODA\ODAFileConverter.exe --unit mm --allow-overwrite
```

## Behavior

- Scans only `Example1` through `Example5`.
- Processes each product JSON occurrence separately, so repeated SKUs remain distinct.
- Selects top-view CAD only from product folders.
- Ignores `.bak`, `.original`, side-view, product-model, and unrelated output files.
- Records filename/SKU mismatches before and after Stage 12.
- Uses Stage 12 with `vendor_profile="bluestone_playground"` and `export_dwg=True`.
- Does not assume units when `--unit` is omitted.
- Records `--unit` as an explicit override when supplied.
- Continues after individual occurrence failures.
- Writes only under the requested output root.

## Outputs

```text
<output-root>/
  qualification_matrix.csv
  qualification_results.json
  qualification_summary.md
  packages/
    <stage-12-occurrence-id>/
      manifest.json
      report.md
      normalized/
      reports/
      svg/
```

## Qualification Use

The no-unit run is the production-safety baseline because it shows what the pipeline can prove from source data alone. The `--unit mm` run is a controlled diagnostic for this vendor set when review evidence supports millimetre drawings, and its results must be compared against the baseline rather than treated as an automatic fix.
## Stage 12 Hang Fix Qualification

The previous accepted limitation is fixed in Stage 12. The first observed hanging occurrence, `Example2/137004M`, now identifies `measurement` as the expensive stage and returns a fail package instead of blocking. Root cause evidence is recorded as `measurement_failed`: curve tessellation requested 65,348 circle points and capped at 5,000, then self-intersection processing would have required 12,492,500 comparisons and is capped at 250,000.

Latest no-unit rerun on 2026-07-23 completed all 26 occurrences with no `stage12_hang_bypassed` entries and no occurrence-level timeout. Results: 26 fail, 0 review_required, 0 pass_with_warnings, 0 pass; 25/26 packages generated; source checksums preserved for 26/26. The one non-package case is `Example1/F24706M`, where product JSON parsing fails before Stage 12 can establish the package boundary.
