# Stage 13 End-to-End Qualification Summary

- Source root: `C:\Users\jvsin\Documents\DrafterMath Archive\CAD_Examples\Test_Examples`
- Output root: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit`
- Folders scanned: 26
- Occurrences processed: 26
- Unit override: `none`; explicit override: `False`
- Status counts: {"fail": 26, "pass": 0, "pass_with_warnings": 0, "review_required": 0}
- Conversion success rate: 25/26 (96.2%)
- Package-generation success rate: 25/26 (96.2%)
- Source checksum preservation: 26/26 (100.0%)
- Average runtime seconds: 10.6516
- Slowest runtime: `Example5/137055M` at 30.200987s

## Failures By Category
- source_data: {"filename_sku_mismatch": 1, "missing_sku": 25, "missing_top_view_cad": 25}
- cad: {"ambiguous_layer_mapping": 72, "contains_3d_geometry": 12, "contains_external_reference": 23, "contains_proxy_entity": 24, "contains_region": 4, "mixed_content_preserved": 960, "nonzero_z_geometry": 1078, "proxy_entity_preserved": 1487, "region_conversion_failed": 9, "svg_entity_render_failed": 75, "svg_entity_unsupported": 178, "svg_text_fallback": 111, "tessellation_failed": 3, "units_unknown": 25, "unsupported_3d_geometry": 70, "unsupported_entity_type": 336}
- units: {"measurement_units_unknown": 25, "units_unknown": 25, "unknown_units": 25, "validation_units_unknown": 652}
- classification: {"insufficient_evidence": 2, "mixed_operational_content": 89, "proxy_content": 24, "unknown_units": 25, "vendor_alias_conflict": 22}
- measurement: {"measurement_curve_approximated": 326, "measurement_failed": 91, "measurement_gap_too_large": 1721, "measurement_hole_relationship_uncertain": 12, "measurement_low_confidence": 82, "measurement_multiple_candidates": 24, "measurement_no_candidate": 1, "measurement_nonplanar_geometry": 1077, "measurement_opaque_region": 13, "measurement_open_geometry": 4981, "measurement_self_intersection": 99, "measurement_units_unknown": 25, "measurement_unsupported_geometry": 429}
- validation: {"unverifiable_field": 305, "validation_ambiguous_candidates": 42, "validation_cad_risk_evidence": 659, "validation_missing_cad_candidate": 28, "validation_missing_json_value": 50, "validation_top_view_not_verifiable": 150, "validation_units_unknown": 652, "validation_weak_classification": 38}
- pipeline_errors: {"RuntimeError": 1}

## Cleanest Successful Demo Cases
- `Example1/137210M` 137210M: `fail`, issues 139, CAD `top_view.dwg`
- `Example2/137004M` 137004M: `fail`, issues 146, CAD `top_view.dwg`
- `Example3/F24602M` F24602M: `fail`, issues 182, CAD `F24602M_Top_View.dwg`
- `Example1/137017M` 137017M: `fail`, issues 212, CAD `top_view.dwg`
- `Example3/137019M` 137019M: `fail`, issues 261, CAD `top_view.dwg`
- `Example2/175551M` 175551M: `fail`, issues 274, CAD `top_view.dwg`
- `Example3/137033M` 137033M: `fail`, issues 278, CAD `top_view.dwg`
- `Example5/175020` 175020: `fail`, issues 291, CAD `top_view.dwg`

## Most Important Review/Fail Cases
- `Example3/175532M` 175532M: `fail`, issues 2850, CAD `top_view_175332M.dwg`
- `Example5/137055M` 137055M: `fail`, issues 2326, CAD `top_view.dwg`
- `Example5/200230` 200230: `fail`, issues 1714, CAD `top_view.dwg`
- `Example4/137401M` 137401M: `fail`, issues 1012, CAD `top_view.dwg`
- `Example4/137417_M` 137417_M: `fail`, issues 635, CAD `top_view.dwg`
- `Example5/137417_M` 137417_M: `fail`, issues 635, CAD `top_view.dwg`
- `Example2/175592M` 175592M: `fail`, issues 581, CAD `top_view.dwg`
- `Example3/104310M` 104310M: `fail`, issues 530, CAD `top_view.dwg`
- `Example2/175050` 175050: `fail`, issues 495, CAD `top_view.dwg`
- `Example4/175050` 175050: `fail`, issues 492, CAD `top_view.dwg`

## Repeated SKU Cases
- `Example2/175050` 175050: `fail`, issues 495, CAD `top_view.dwg`
- `Example4/137417_M` 137417_M: `fail`, issues 635, CAD `top_view.dwg`
- `Example4/175050` 175050: `fail`, issues 492, CAD `top_view.dwg`
- `Example5/137417_M` 137417_M: `fail`, issues 635, CAD `top_view.dwg`

## Filename Mismatch Cases
- `Example3/175532M` 175532M: `fail`, issues 2850, CAD `top_view_175332M.dwg`

## Recommended Regression Fixtures
- `Example1/104512M` (104512M): unit handling without silent assumption; CAD `top_view_104512M.dwg`
- `Example3/175532M` (175532M): filename/SKU mismatch; CAD `top_view_175332M.dwg`
- `Example2/175050` (175050): repeated SKU occurrence identity; CAD `top_view.dwg`
- `Example1/137017M` (137017M): classification review pressure; CAD `top_view.dwg`
- `Example1/137210M` (137210M): measurement gap or ambiguity; CAD `top_view.dwg`
- `Example1/175531M` (175531M): validation mismatch; CAD `175531M_Top_View.DWG`
- `Example1/F24706M` (F24706M): package failure continuation; CAD `F24706M_Top_View.dwg`

## Remaining Blockers Before Production Use
- Vendor CAD units are unknown in the source drawings; production must require explicit units or validated metadata.
- Stage 12 validation failures remain and must be reviewed before unattended production use.
- Filename/SKU mismatches need operator or vendor-data resolution.
- Repeated SKUs must stay occurrence-scoped in production logs and regression fixtures.

## Package Log Paths
- `Example1/104512M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\104512M_4081fce124`
- `Example1/137017M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\137017M_bba85e578b`
- `Example1/137210M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\137210M_49bae9a5df`
- `Example1/175531M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\175531M_6f32273365`
- `Example2/137004M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\137004M_77975161a0`
- `Example2/137225M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\137225M_5c637c539e`
- `Example2/137407M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\137407M_f43c1b2e0b`
- `Example2/175050`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\175050_aa4de49287`
- `Example2/175551M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\175551M_e6422fe5ab`
- `Example2/175592M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\175592M_03cc3f2042`
- `Example3/104310M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\104310M_6e9021b92e`
- `Example3/137019M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\137019M_697506195b`
- `Example3/137033M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\137033M_0f6ac91ebe`
- `Example3/175532M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\175532M_99d2687d75`
- `Example3/175535M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\175535M_41a5df5ab9`
- `Example3/F24602M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\F24602M_3e6a353280`
- `Example4/137035M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\137035M_026845a831`
- `Example4/137401M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\137401M_5e8a743049`
- `Example4/137417_M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\137417_M_ff1584d6f6`
- `Example4/175050`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\175050_31620cedfd`
- `Example4/175591M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\175591M_ce97949f88`
- `Example5/137055M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\137055M_acf74950a9`
- `Example5/137417_M`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\137417_M_e9fe654cd3`
- `Example5/175020`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\175020_2bb6348e04`
- `Example5/200230`: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\stage13_qualification\no_unit\packages\200230_69f8f19aa3`
