# Stage 5 DXF Inventory and Data-Quality Audit

## Objective

Implement only a read-only DXF inventory and audit stage using `ezdxf`. Stage 5 opens a DXF safely, reports structure and risk indicators, and emits machine-readable and Markdown audit reports without modifying geometry.

Stage 5 does not convert REGION entities, classify product footprint or safety-zone geometry, normalize layers, flatten/explode blocks, transform coordinates, center geometry, save the source document, validate product dimensions, or use AI.

## ezdxf Loading Assumptions

The inventory accepts explicit `.dxf` paths only. Missing, unreadable, empty, non-DXF, or corrupt files raise `DxfInventoryError` with stable code `invalid_dxf`.

The implementation uses `ezdxf.readfile(path)` and treats the loaded document as read-only. It never calls `save`, `saveas`, `explode`, flattening helpers, layer rewrite APIs, or coordinate transformation APIs. Reports are written only when explicit output paths are supplied.

`ezdxf` may return default header values for missing variables. To distinguish a missing `$INSUNITS` header from an explicit unitless value, Stage 5 also checks the raw DXF text for `$INSUNITS` before reporting units.

## Inventory Schema

Primary result model: `DxfInventoryResult`.

Nested models:

- `LayerInventory`: name, color, linetype, on/off, frozen, locked, plot status, entity count.
- `BlockInventory`: name, block entity count, direct INSERT count, nested INSERT count, XREF flag, layout-block flag, unresolved nested references.
- `EntityTypeCount`: DXF type, count, audit category.
- `DxfAuditIssue`: stable issue code, severity, message, evidence.

Top-level fields include:

- source path and source SHA-256;
- DXF version;
- `$INSUNITS` and decoded drawing units;
- modelspace and paperspace presence;
- layers;
- block definitions;
- entity counts globally and by layer;
- nested INSERT content counts;
- modelspace entity count and total entity count;
- modelspace extents where safely calculable;
- layouts;
- XREF/external-reference indicators;
- text/style/dimension/hatch usage;
- flagged entity presence;
- non-zero Z and 3D geometry indicators using configurable absolute `z_epsilon` tolerance;
- unresolved block references;
- audit issues.

`DxfInventoryResult.to_deterministic_json()` emits sorted, compact JSON for stable machine comparisons.

## Supported and Flagged Entity Types

Known-supported audit category:

- `LINE`
- `LWPOLYLINE`
- `POLYLINE`
- `ARC`
- `CIRCLE`
- `POINT`

Convertible-later audit category:

- `REGION`
- `SPLINE`
- `ELLIPSE`
- `HATCH`
- `INSERT`

Ignored/non-operational audit category:

- `TEXT`
- `MTEXT`
- `DIMENSION`
- `LEADER`
- `MLEADER`
- `MULTILEADER`
- `ATTRIB`
- `ATTDEF`
- `VIEWPORT`

Review-required audit category:

- `3DSOLID`
- `BODY`
- `SURFACE`
- `WIPEOUT`
- `IMAGE`
- `UNDERLAY`
- `PDFUNDERLAY`
- `DGNUNDERLAY`
- `DWFUNDERLAY`
- `ACAD_PROXY_ENTITY`

Any other entity type is preserved in inventory evidence and flagged as `unsupported_entity_type`. Unsupported does not mean delete; it means the content requires later policy or CAD review.

## Issue Codes

Stable Stage 5 issue codes:

- `invalid_dxf`
- `unsupported_dxf_version`
- `units_missing`
- `units_unknown`
- `empty_modelspace`
- `no_2d_geometry`
- `contains_region`
- `contains_3d_geometry`
- `contains_proxy_entity`
- `contains_external_reference`
- `contains_raster_or_underlay`
- `unsupported_entity_type`
- `nonzero_z_geometry`
- `unresolved_block_reference`
- `extents_unavailable`

Severities are stable strings: `info`, `warning`, `fail`. Current inventory-time content findings are warnings; invalid input raises before returning a result.

## Extents Strategy

Modelspace extents are calculated with `ezdxf.bbox.extents(modelspace, fast=True)`. The calculation is best-effort and non-fatal. Unsupported or malformed entities can cause extents to be unavailable; Stage 5 records `extents_unavailable` and still returns all other inventory evidence.

Nested INSERT content is counted and reported through block-reference traversal, but blocks are not exploded or flattened.

## Z Tolerance Policy

Stage 5 treats `abs(z) <= z_epsilon` as planar zero for non-zero-Z audit indicators. The default is `1e-6` drawing units. Values above the epsilon are still reported as `nonzero_z_geometry`, and the issue evidence preserves sorted unique raw Z/elevation values plus the epsilon used for the decision. Explicit 3D entity types, including `3DFACE`, `3DSOLID`, `BODY`, `SURFACE`, mesh/polyface/polymesh indicators, remain 3D indicators regardless of coordinate noise. Stage 5 does not rewrite, flatten, or normalize source geometry.

## Tests and Results

Unit tests cover:

- missing DXF;
- invalid/corrupt DXF;
- valid simple 2D DXF;
- layers and entity counts;
- block definitions and INSERT counting;
- REGION detection;
- SPLINE, ELLIPSE, and HATCH detection;
- non-zero Z detection above epsilon;
- tiny positive/negative Z values below epsilon;
- configurable Z epsilon behavior;
- raw Z evidence preservation;
- explicit 3D entity handling independent of tiny coordinate noise;
- empty modelspace;
- missing and unknown units;
- proxy-style entity handling;
- raster/underlay indicators;
- extents success;
- extents failure remains non-fatal;
- deterministic JSON serialization;
- source checksum unchanged after audit;
- unresolved block references.

Current Stage 5 focused result:

```text
python -m pytest tests\test_inventory.py -> 22 passed
```

Full-suite verification passed in this workspace.

## Live-Test Instructions

The live verification script is manual and is not part of normal pytest discovery.

```powershell
python tests/live/stage5_live_inventory.py --input "C:\Path\To\input.dxf"
python tests/live/stage5_live_inventory.py --input "C:\Path\To\input.dxf" --json-output "C:\Path\To\audit.json" --markdown-output "C:\Path\To\audit.md"
```

The script prints compact JSON with DXF version, units, layer count, entity count, top entity types, extents, and issue codes. It does not modify the input file.

## Known Limitations

- Stage 5 inventories DXF only; DWG input must first be converted by Stage 4.
- XREF detection is best-effort and depends on what `ezdxf` exposes from block metadata.
- REGION entities with incomplete ACIS/SAT data may not be authorable as normal generated fixtures; tests use a controlled loaded-document substitute for REGION audit handling.
- Extents are bounding-box approximations from `ezdxf`, not semantic product or safety-zone measurements.
- Entity categories are audit categories only; they do not drive deletion, conversion, or normalization.
- Paper/model layout block internals are not double-counted as separate block definitions in global entity counts.

## Exit-Gate Status

Passed. Stage 5 implementation adds only read-only DXF inventory, audit models, report helpers, unit tests, manual live runner, dependency and README updates, and this production log.

Verification performed:

```text
python -m pytest tests\test_inventory.py tests\test_canonicalize.py -> 48 passed
python -m pytest -> 168 passed
package import check -> passed (`import ok 1e-06 1e-06`)
python -m py_compile tests\live\stage5_live_inventory.py -> passed
final tree inspection -> completed
```

Confirmed exclusions: no source DXF save or modification, no REGION conversion, no layer rewriting, no geometry centering or transformation, and no AI logic.
