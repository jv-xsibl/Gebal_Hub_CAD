# Stage 13 End-to-End Qualification Summary

- Source root: `C:\Users\jvsin\Documents\DrafterMath Archive\CAD_Examples\Test_Examples`
- Output root: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\hang_diagnosis`
- Folders scanned: 26
- Occurrences processed: 1
- Unit override: `none`; explicit override: `False`
- Status counts: {"fail": 1, "pass": 0, "pass_with_warnings": 0, "review_required": 0}
- Conversion success rate: 1/1 (100.0%)
- Package-generation success rate: 1/1 (100.0%)
- Source checksum preservation: 1/1 (100.0%)
- Average runtime seconds: 9.1446
- Slowest runtime: `Example2/137004M` at 9.144616s

## Failures By Category
- source_data: {"missing_sku": 1, "missing_top_view_cad": 1}
- cad: {"contains_external_reference": 1, "contains_proxy_entity": 1, "mixed_content_preserved": 10, "proxy_entity_preserved": 26, "svg_entity_unsupported": 3, "svg_text_fallback": 4, "units_unknown": 1, "unsupported_entity_type": 9}
- units: {"measurement_units_unknown": 1, "units_unknown": 1, "unknown_units": 1, "validation_units_unknown": 4}
- classification: {"mixed_operational_content": 4, "proxy_content": 1, "unknown_units": 1, "vendor_alias_conflict": 1}
- measurement: {"measurement_curve_approximated": 3, "measurement_failed": 5, "measurement_gap_too_large": 18, "measurement_hole_relationship_uncertain": 1, "measurement_low_confidence": 2, "measurement_multiple_candidates": 1, "measurement_open_geometry": 11, "measurement_units_unknown": 1, "measurement_unsupported_geometry": 10}
- validation: {"unverifiable_field": 11, "validation_cad_risk_evidence": 4, "validation_missing_cad_candidate": 3, "validation_missing_json_value": 2, "validation_top_view_not_verifiable": 6, "validation_units_unknown": 4}
- pipeline_errors: none

## Cleanest Successful Demo Cases
- `Example2/137004M` 137004M: `fail`, issues 146, CAD `top_view.dwg`

## Most Important Review/Fail Cases
- `Example2/137004M` 137004M: `fail`, issues 146, CAD `top_view.dwg`

## Repeated SKU Cases
- `None` : ``, issues 0, CAD ``

## Filename Mismatch Cases
- `None` : ``, issues 0, CAD ``

## Recommended Regression Fixtures
- `Example2/137004M` (137004M): unit handling without silent assumption; CAD `top_view.dwg`

## Remaining Blockers Before Production Use
- Vendor CAD units are unknown in the source drawings; production must require explicit units or validated metadata.
- Stage 12 validation failures remain and must be reviewed before unattended production use.

## Package Log Paths
- `Example2/137004M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\hang_diagnosis\packages\137004M_77975161a0`
