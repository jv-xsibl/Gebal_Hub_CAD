# CAD Variation Audit Summary

- Source root: `C:\Users\jvsin\Documents\DrafterMath Archive\CAD_Examples\Test_Examples`
- Output root: `C:\Users\jvsin\Documents\GitHub\Gebal_Hub_CAD\tests\output\cad_variation_audit_bluestone`
- Total product folders scanned: 26
- JSON files found: 26
- CAD-like files found in product folders: 97
- Selected top-view files: 26
- Unique SKUs: 24
- Successful conversions: 26
- Failed conversions: 0
- Runtime seconds: 21.5233

## Top-View Selection Problems
- Problems: 1
- Missing top-view files: 0
- Ambiguous top-view files: 0
- Filename/SKU mismatches: 1
- `Example3/175532M`: filename_sku_mismatch; selected `top_view_175332M.dwg`; candidates `top_view_175332M.dwg`

## CAD Variation Families
- inconsistent layer-name schemes: 26
- unknown-unit drawings: 26
- block-heavy drawings: 24
- proxy/XREF-containing drawings: 24
- spline/ellipse-heavy drawings: 22
- 3D/non-zero-Z contaminated drawings: 12
- simple 2D line/polyline drawings: 10
- REGION-containing drawings: 4
- filename/SKU mismatch: 1

## Entity-Type Frequency
- `LINE`: 13009
- `SPLINE`: 3620
- `ARC`: 3591
- `ELLIPSE`: 1002
- `POINT`: 297
- `INSERT`: 291
- `SOLID`: 198
- `MTEXT`: 132
- `DIMENSION`: 99
- `TEXT`: 87
- `HATCH`: 72
- `POLYLINE`: 71
- `3DFACE`: 70
- `CIRCLE`: 33
- `VIEWPORT`: 26
- `ACAD_PROXY_ENTITY`: 24
- `ATTDEF`: 24
- `LWPOLYLINE`: 24
- `REGION`: 9

## Layer-Name Variation Patterns
- `0`: 26
- `Defpoints`: 26
- `Lg_txt`: 24
- `ASHADE`: 23
- `Lg_area`: 23
- `Lg_prod`: 23
- `Lg_dim`: 19
- `lc_ground`: 11
- `Lg_boundary`: 7
- `Lg_falling`: 7
- `Apple red`: 6
- `Candy fuchsia`: 6
- `Lime green`: 6
- `Liquorice grey`: 6
- `Tuttifrutti`: 6
- `LCPROD_FALLINGSPACE`: 5
- `lg_prod`: 2
- `DIMENSION`: 2
- `LCPROD_ENSAFETYREGION`: 1
- `lg_area`: 1
- `lg_falling`: 1
- `Plan`: 1

## Units Distribution
- `unknown`: 26

## Proxy/XREF/3D/REGION Prevalence
- Proxy/XREF family count: 24
- 3D/non-zero-Z family count: 12
- REGION family count: 4

## Classification Confidence Problems
- Ambiguous/review-required layer assignments: 103
- `Example1/104512M` layer `lc_ground` -> `review_required`: conflicting layer-name and geometry evidence
- `Example1/104512M` layer `Lg_dim` -> `review_required`: mixed operational and non-operational content
- `Example1/137017M` layer `0` -> `review_required`: mixed operational and non-operational content
- `Example1/137210M` layer `0` -> `review_required`: mixed operational and non-operational content
- `Example1/175531M` layer `Lg_dim` -> `review_required`: mixed operational and non-operational content
- `Example1/175531M` layer `Lg_txt` -> `review_required`: mixed operational and non-operational content
- `Example1/F24706M` layer `0` -> `review_required`: insufficient evidence
- `Example2/137004M` layer `0` -> `review_required`: mixed operational and non-operational content
- `Example2/137004M` layer `Lg_area` -> `review_required`: conflicting layer-name and geometry evidence
- `Example2/137004M` layer `Lg_falling` -> `review_required`: mixed operational and non-operational content
- `Example2/137004M` layer `Lg_txt` -> `review_required`: mixed operational and non-operational content
- `Example2/137225M` layer `0` -> `review_required`: mixed operational and non-operational content
- `Example2/137225M` layer `Lg_area` -> `review_required`: conflicting layer-name and geometry evidence
- `Example2/137225M` layer `Lg_dim` -> `review_required`: mixed operational and non-operational content
- `Example2/137225M` layer `Lg_falling` -> `review_required`: conflicting layer-name and geometry evidence
- `Example2/137225M` layer `Lg_txt` -> `review_required`: mixed operational and non-operational content
- `Example2/137407M` layer `Apple red` -> `review_required`: mixed operational and non-operational content
- `Example2/137407M` layer `Candy fuchsia` -> `review_required`: mixed operational and non-operational content
- `Example2/137407M` layer `lc_ground` -> `review_required`: conflicting layer-name and geometry evidence
- `Example2/137407M` layer `Lg_area` -> `review_required`: conflicting layer-name and geometry evidence
- `Example2/137407M` layer `Lg_dim` -> `review_required`: mixed operational and non-operational content
- `Example2/137407M` layer `Lg_falling` -> `review_required`: conflicting layer-name and geometry evidence
- `Example2/137407M` layer `Lg_txt` -> `review_required`: mixed operational and non-operational content
- `Example2/137407M` layer `Lime green` -> `review_required`: mixed operational and non-operational content
- `Example2/137407M` layer `Liquorice grey` -> `review_required`: mixed operational and non-operational content
- `Example2/137407M` layer `Tuttifrutti` -> `review_required`: mixed operational and non-operational content
- `Example2/175050` layer `lc_ground` -> `review_required`: conflicting layer-name and geometry evidence
- `Example2/175050` layer `LCPROD_FALLINGSPACE` -> `review_required`: conflicting layer-name and geometry evidence
- `Example2/175050` layer `Lg_dim` -> `review_required`: mixed operational and non-operational content
- `Example2/175050` layer `Lg_txt` -> `review_required`: mixed operational and non-operational content

## Most Unusual Files

## Failures
- None

## Risks For Stages 10-12
- Unknown or missing units are common enough that measurement must report unit inference explicitly and avoid silent millimetre assumptions.
- Ambiguous/review-required layer assignments mean footprint and safety-zone measurement should require confidence evidence, not just layer names.
- Block-heavy drawings require measurement to decide whether to measure block references, block definitions, or both without double-counting.
- Curve-heavy drawings need controlled tessellation policy before area or perimeter validation.
- Filename/SKU mismatches and repeated SKUs require reports to preserve occurrence paths, not only SKU-level aggregation.

## Recommendations
- Add Stage 10 measurement with explicit unit status, bounding-box evidence, closed-geometry evidence, and block traversal policy.
- Keep CAD-to-JSON validation tolerant to rotated dimensions and explicit about unverifiable top-view fields.
- Report safety-zone/product-footprint candidates separately when both are plausible rather than forcing one semantic winner.
- Preserve source checksum before/after every live processing run in batch reports.
- Add fixture coverage for conversion failure, filename/SKU mismatch, unknown units, block-heavy, curve-heavy, ambiguous layers, and repeated SKU occurrences.

## Recommended Regression Fixtures
- `Example3/175532M/top_view_175332M.dwg`: filename/SKU mismatch
- `Example1/104512M/top_view_104512M.dwg`: unknown-unit drawing
- `Example1/137017M/top_view.dwg`: block-heavy drawing
- `Example1/175531M/175531M_Top_View.DWG`: spline/ellipse-heavy drawing
- `Example2/137407M/top_view.dwg`: 3D/non-zero-Z contaminated drawing
- `Example2/175551M/top_view.dwg`: REGION-containing drawing
- `Example1/137210M/top_view.dwg`: ambiguous/review-required layers
