# Stage 10 Measurement Engine

## Scope

Stage 10 adds a deterministic, read-only CAD measurement engine in `src/gebal_cad_normalizer/cad/measure.py`.

It consumes:

- Stage 5 `DxfInventoryResult`;
- Stage 6 `CanonicalGeometryResult`;
- optional Stage 8 `ClassificationResult`;
- optional explicit unit override;
- optional expected width/depth in millimetres for unit inference.

It does not rewrite layers, transform source CAD, save CAD output, reconstruct opaque ACIS/REGION content, or use AI.

## Measurement Model

The public entry point is `measure_geometry(inventory, canonical, classification=None, config=None)`.

Typed outputs:

- `MeasurementConfig`;
- `MeasurementCandidate`;
- `MeasurementEvidence`;
- `LayerMeasurement`;
- `MeasurementIssue`;
- `MeasurementResult`.

Each candidate records role, source layer, source handles, block ancestry, insert handles, raw Z evidence, bounding box, width, depth, area, perimeter, centroid, closed/open evidence, curve approximation evidence, geometry count, confidence, retained alternatives, unit status, warnings, and review reason.

Candidate roles are `product_geometry`, `safety_zone`, `foundation_or_installation`, and optionally `review_required`.

## Geometry Rules

Measurement is XY-only for top-view footprint work. Raw Z values and elevations remain in evidence. Meaningful non-planar evidence above `z_epsilon` emits `measurement_nonplanar_geometry` and caps confidence.

Supported deterministic geometry:

- closed `LWPOLYLINE` and 2D `POLYLINE`;
- `CIRCLE`;
- closed `ELLIPSE`;
- closed tessellated `SPLINE` references when Stage 6 tessellation is enabled;
- `LINE` and `ARC` chains that close within configured tolerances;
- deterministic transformed `INSERT` content already expanded by Stage 6 canonicalization.

Open geometry is not silently closed. Small endpoint gaps can be joined for measurement evidence only when they are within `max_join_gap`; larger gaps emit `measurement_gap_too_large`.

Curves are flattened deterministically using `curve_flattening_tolerance`; candidates and issues record `measurement_curve_approximated`.

## Block Policy

Stage 10 measures the canonical entities produced by Stage 6. Block definitions are not measured directly. Only actual `INSERT` instances expanded by Stage 6 become measurement candidates, which prevents definition double-counting while allowing repeated inserts to be measured independently.

Block ancestry and insert handles are preserved in `MeasurementEvidence`.

## Holes

Nested closed loops on the same layer and role are treated as inner holes when containment is deterministic. Hole areas are subtracted from the outer-loop area and hole perimeters are included in perimeter evidence. Multiple inner loops emit review evidence through `measurement_hole_relationship_uncertain`.

## Unit Handling

Raw drawing-unit measurements are always reported. Unknown `$INSUNITS` never silently becomes millimetres.

Unit resolution order:

1. explicit override via `MeasurementConfig.explicit_unit`;
2. recognized `$INSUNITS`;
3. optional expected width/depth inference;
4. unknown raw drawing units.

Expected-dimension inference tests only `mm`, `cm`, `m`, and `in`. Width/depth comparison is rotation-tolerant by comparing sorted dimension pairs. The result records inferred unit, scale factor, confidence, residual error, and alternatives. Ambiguous or high-residual inference remains `ambiguous`/`unknown` instead of becoming authoritative.

## Confidence

Candidate confidence combines:

- Stage 8 role confidence;
- closed-loop quality;
- positive enclosed area;
- curve and endpoint-gap penalties;
- review-required cap;
- non-planar cap.

Mixed, ambiguous, proxy/XREF, and review-required evidence cannot automatically become high-confidence product or safety candidates.

## Issue Codes

Stage 10 emits the requested stable issue codes:

- `measurement_no_candidate`
- `measurement_open_geometry`
- `measurement_gap_too_large`
- `measurement_self_intersection`
- `measurement_nonplanar_geometry`
- `measurement_unsupported_geometry`
- `measurement_opaque_region`
- `measurement_block_double_count_risk`
- `measurement_curve_approximated`
- `measurement_hole_relationship_uncertain`
- `measurement_units_unknown`
- `measurement_unit_inference_ambiguous`
- `measurement_unit_override_applied`
- `measurement_multiple_candidates`
- `measurement_low_confidence`
- `measurement_failed`

`measurement_block_double_count_risk` and `measurement_failed` are reserved for deterministic failure/risk cases; the normal Stage 6 expansion path avoids block definition double-counting.

## Tests

`tests/test_measure.py` covers:

- rectangular and rotated product footprints;
- circle, ellipse, spline, line, and arc measurement;
- endpoint gaps below and above tolerance;
- polygon holes;
- nested and repeated inserts;
- product/safety containment;
- multiple plausible candidates;
- review-required low-confidence behavior;
- unknown units, explicit override, mm inference, and ambiguous inference;
- non-planar geometry;
- opaque region evidence;
- deterministic serialization;
- source checksum preservation.

## Live Command

Manual live script:

```powershell
python tests\live\stage10_live_measure.py --input path\to\file.dxf --vendor-profile bluestone_playground
```

Optional unit override:

```powershell
python tests\live\stage10_live_measure.py --input path\to\file.dxf --unit mm
```

Optional expected-dimension inference and JSON output:

```powershell
python tests\live\stage10_live_measure.py --input path\to\file.dxf --width 4140 --depth 4680 --json-output tests\output\stage10_measure.json
```

The script runs Stages 5, 6, and 8 automatically, prints compact JSON, and verifies the source checksum is unchanged.

## Limitations

- Opaque ACIS `REGION`, `BODY`, `3DSOLID`, and `SURFACE` content remains unmeasurable unless deterministic boundaries already exist from an earlier conversion stage.
- HATCH support is limited to deterministic polyline boundary paths. Stage 10 does not reconstruct arbitrary hatch semantics or non-polyline edge paths.
- Spline measurement is approximate when Stage 6 tessellation is enabled.
- Axis-aligned width/depth is reported for top-view footprints; oriented minimum bounding rectangles are not selected automatically.
- Candidate ranking preserves alternatives instead of forcing one semantic winner.

## Exit Gate

Stage 10 is acceptable when:

- targeted Stage 10 tests pass;
- full `python -m pytest` passes;
- package import check passes;
- live script compiles;
- representative live runs complete without source checksum changes;
- outputs preserve unknown units unless override or high-confidence inference is explicit;
- block definitions are not double-counted;
- opaque REGION/ACIS geometry is not reconstructed;
- no CAD geometry or layers are rewritten;
- no AI is used.


