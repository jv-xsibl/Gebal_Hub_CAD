# Stage 6 Geometry Canonicalization and Safe 2D Extraction Preparation

## Objective

Implement only a deterministic, read-only canonicalization stage for supported 2D DXF geometry. Stage 6 converts modelspace geometry into immutable internal Python models for later REGION conversion, classification, centering, measurement, and validation.

Stage 6 does not save or rewrite the source DXF, convert REGION entities, normalize layers, classify product or safety-zone geometry, center geometry, scale geometry, rotate geometry as a normalization policy, or use AI.

## Internal Geometry Schema

Primary result model: `CanonicalGeometryResult`.

Nested models:

- `CanonicalPoint`: exact point coordinate evidence with X/Y/Z.
- `CanonicalLine`: start and end points.
- `CanonicalArc`: center, radius, DXF start/end angles, and CCW direction evidence.
- `CanonicalCircle`: center and radius.
- `CanonicalPolyline`: vertices, open/closed state, elevation, source polyline type, optional tessellation tolerance.
- `CanonicalPolylineVertex`: point, start width, end width, and bulge.
- `CanonicalEllipse`: center, major axis, extrusion, ratio, start/end parameters.
- `CanonicalSplineReference`: spline degree, closed state, control points, fit points, knots, and weights.
- `CanonicalEntity`: source handle, original DXF type, canonical type, status, layer, color, linetype, block ancestry, INSERT handles, Z/elevation evidence, geometry payload, metadata, and confidence.
- `CanonicalizationIssue`: stable issue code, severity, source handle, DXF type, ancestry, message, and evidence.

`CanonicalGeometryResult.to_deterministic_json()` emits sorted compact JSON. Entity ordering follows modelspace traversal order plus nested block traversal order encoded in `order_key`.

## Supported Entities

Direct canonicalization:

- `LINE` to `CanonicalLine` with status `canonicalized`.
- `POINT` to `CanonicalPoint` with status `canonicalized`.
- `LWPOLYLINE` to `CanonicalPolyline` with status `canonicalized`.
- 2D `POLYLINE` to `CanonicalPolyline` with status `canonicalized`.
- `ARC` to `CanonicalArc` with status `convertible_later`.
- `CIRCLE` to `CanonicalCircle` with status `convertible_later`.
- `ELLIPSE` to `CanonicalEllipse` with status `preserved_curve` by default.
- `SPLINE` to `CanonicalSplineReference` with status `preserved_curve` by default.
- `INSERT` through safe modelspace traversal into block-definition content.

Unsupported entities are represented as canonical audit records with status `unsupported` or `review_required`. 3D entities are marked `skipped_3d`.

## Block Traversal and Transform Rules

Stage 6 traverses modelspace only by default. Block-definition geometry is not included unless referenced by a modelspace `INSERT`.

For each modelspace `INSERT`, Stage 6:

- reads the INSERT transform matrix;
- copies referenced block entities in memory;
- applies the INSERT transform to the copied entity;
- canonicalizes the transformed copy;
- preserves the original block child handle where ezdxf copy behavior allows;
- records block ancestry as `BLOCK_NAME:INSERT_HANDLE`;
- records INSERT handles separately;
- detects recursive block references and emits `block_cycle_detected`.

The implementation does not call `explode`, does not add virtual entities to the source document, and does not save the document.

## Curve and Bulge Handling

LWPOLYLINE and 2D POLYLINE bulge values are preserved on each canonical vertex. ARC entities preserve radius, center, start angle, end angle, and direction evidence.

ELLIPSE and SPLINE are not silently flattened. They are preserved as curve/reference records and emit `curve_preserved_not_flattened`.

Optional tessellation exists only when explicitly enabled with a positive tolerance. Tessellated curve output uses `CanonicalPolyline` with canonical type `tessellated_polyline`, stores the tolerance, and is deterministic for identical input and tolerance.

## 3D Handling

Stage 6 does not project 3D geometry into 2D silently. Non-zero Z/elevation on otherwise supported geometry is retained as coordinate/elevation evidence and reported as `nonzero_z_geometry` only when `abs(z) > z_epsilon`. The default `z_epsilon` is `1e-6` drawing units, and callers may configure it per canonicalization call. Raw Z/elevation values remain in canonical entities and issue evidence; Stage 6 does not rewrite or flatten source geometry. Known 3D entities, including `3DFACE`, `3DSOLID`, `BODY`, `SURFACE`, mesh/polyface/polymesh indicators, and 3D POLYLINE entities are skipped and reported as `unsupported_3d_geometry` with status `skipped_3d` regardless of tiny coordinate noise.

## Issue Codes

Stable Stage 6 issue codes:

- `unsupported_entity_type`
- `nonzero_z_geometry`
- `unsupported_3d_geometry`
- `block_cycle_detected`
- `insert_transform_failed`
- `invalid_entity_geometry`
- `curve_preserved_not_flattened`
- `tessellation_failed`
- `canonical_extents_unavailable`

## Tests and Results

Unit tests cover:

- LINE canonicalization;
- open and closed LWPOLYLINE;
- bulge preservation;
- 2D POLYLINE;
- ARC direction and angles;
- CIRCLE;
- ELLIPSE preservation;
- SPLINE preservation without default flattening;
- nested INSERT transform application;
- block ancestry preservation;
- circular block reference handling;
- non-zero Z reporting above epsilon;
- tiny positive/negative Z values below epsilon;
- configurable Z epsilon behavior;
- raw Z evidence preservation;
- 3D entity skipping/reporting;
- unsupported entity reporting;
- deterministic ordering and JSON output;
- canonical extents;
- source file checksum unchanged;
- source ezdxf document not mutated;
- deterministic opt-in tessellation;
- tessellation disabled by default.

Current Stage 6 focused result:

```text
python -m pytest tests\test_inventory.py tests\test_canonicalize.py -> 48 passed
```

Full-suite verification in this workspace:

```text
python -m pytest -> 168 passed
package import check -> passed (`import ok 1e-06 1e-06`)
```

## Live-Test Instructions

The live verification script is manual and is not part of normal pytest discovery.

```powershell
python tests/live/stage6_live_canonicalize.py --input "C:\Path\To\input.dxf"
python tests/live/stage6_live_canonicalize.py --input "C:\Path\To\input.dxf" --json-output "C:\Path\To\canonical.json"
python tests/live/stage6_live_canonicalize.py --input "C:\Path\To\input.dxf" --tessellation-tolerance 0.25
```

The script prints compact JSON with source entity count, canonical entity count, counts by status, counts by canonical type, extents, issue codes, checksum evidence, and tessellation state.

## Known Limitations

- Stage 6 accepts DXF input; DWG input must still pass through Stage 4 ODA conversion first.
- REGION conversion is intentionally not implemented in this stage.
- Extents are based on canonical point evidence. Arc, circle, ellipse, and spline extents are conservative unless tessellated or represented by point evidence.
- SPLINE and ELLIPSE are preserved by default for later controlled conversion; they are not operational polygons yet.
- Block transform support depends on ezdxf transform support for the entity type.
- Source handles for transformed block entities are retained as audit evidence, but transformed entities are in-memory copies rather than source database members.

## Exit-Gate Status

Passed. Stage 6 implementation includes read-only canonical geometry extraction, configurable Z epsilon reporting, regression tests, README updates, refreshed batch audit outputs, and this production log update.

Verification performed:

```text
python -m pytest tests\test_inventory.py tests\test_canonicalize.py -> 48 passed
python -m pytest -> 168 passed
package import check -> passed (`import ok 1e-06 1e-06`)
```

Confirmed design exclusions: no source DXF save or mutation, no REGION conversion, no layer rewriting, no geometry centering/scaling/normalization rotation, no product or safety-zone classification, and no AI logic.
