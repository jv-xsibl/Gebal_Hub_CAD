# Stage 7 REGION Conversion

## Objective

Stage 7 converts supported planar DXF `REGION` entities into auditable closed loop geometry represented as `LWPOLYLINE` output when explicitly written. The source DXF or loaded `ezdxf` document is never mutated or saved by the conversion pass.

This stage only adds REGION conversion. It does not classify product or safety-zone geometry, rename layers, center, scale, rotate, compare JSON to CAD, or use AI.

## Conversion Strategy

`ezdxf` exposes `REGION` entities as ACIS containers, not as direct boundary loops. Stage 7 therefore converts only REGIONs with explicit, deterministic boundary-loop evidence attached under the `GEBAL_REGION_BOUNDARY` XDATA app id. Opaque ACIS-only REGIONs are retained and reported with `region_conversion_failed` rather than guessed.

For supported REGIONs, the converter:

- accepts a DXF path or loaded `ezdxf.document.Drawing`;
- visits modelspace entities in stable order and processes only `REGION`;
- preserves source handle, layer, color, linetype, elevation, and conversion evidence in typed results;
- converts each boundary loop into a closed loop model with vertices and bulges;
- writes `LWPOLYLINE` entities only when `output_path` is explicitly provided;
- preserves all non-REGION entities and layers in output copies;
- keeps failed REGION entities in output instead of deleting them.

## Topology Handling

Outer loops and hole loops are represented as separate closed loops. Hole loops receive a `parent_loop_id` by point-in-polygon containment against the available outer loops. Output polylines receive `GEBAL_REGION_CONVERSION` XDATA that records the source REGION handle, loop id, role, and parent loop id.

The converter rejects or flags:

- non-planar REGIONs;
- invalid boundary evidence;
- open loops and zero-length loop segments;
- self-intersecting loops;
- topology with no supported boundary loops or no outer loop;
- area or perimeter deviations beyond tolerance.

## Tolerance Rules

The `tolerance` argument is a positive absolute drawing-unit tolerance used for:

- duplicate closing vertex removal;
- zero-length segment detection;
- line-segment self-intersection robustness;
- area deviation checks;
- perimeter deviation checks.

Arcs are preserved with DXF bulge values when loop evidence provides line/arc segment geometry. Tessellation is only marked when evidence says tessellation was used or contains non-line/non-arc segment types. Tessellated conversions are reported with status `approximated` and issue code `region_curve_approximated`.

## Tests and Results

Implemented in `tests/test_region_convert.py`.

Coverage includes:

- simple rectangular REGION;
- circular REGION with bulges;
- REGION with arcs;
- REGION with inner hole;
- multiple REGIONs per layer;
- layer/style preservation;
- closed output loops;
- area/perimeter tolerance failures;
- non-planar rejection;
- invalid/open/self-intersecting topology;
- deterministic serialization;
- source checksum unchanged;
- explicit output preserving unrelated entities;
- explicit output preserving failed REGIONs in mixed conversion results;
- failed atomic write preserving destination;
- tessellation only when required.

Latest targeted result:

```text
python -m pytest tests\test_region_convert.py
16 passed
```

## Live Command

The live script is intentionally outside the normal pytest suite.

```powershell
python tests\live\stage7_live_region_convert.py --input path\to\input.dxf
python tests\live\stage7_live_region_convert.py --input path\to\input.dxf --output path\to\converted.dxf --tolerance 0.001
python tests\live\stage7_live_region_verification.py --oda-exe E:\ODA\ODAFileConverter.exe --target-version ACAD2013 --output-root tests\output\stage7_region_verification
```

It prints compact JSON with converted count, failed count, loop count, approximation count, area deviations, issue codes, source checksum status, and optional output path.

## Real-World REGION Verification

Run on 2026-07-22 directly in the workspace, without Windows Sandbox, using `E:\ODA\ODAFileConverter.exe` and target `ACAD2013`.

Production log paths:

- `tests/output/stage7_region_verification/stage7_region_verification.json`
- `tests/output/stage7_region_verification/stage7_region_verification.md`

Verified REGION-containing audit files:

| Source DWG | REGION count | Handles / layers | ACIS evidence | Boundary evidence | Stage 7 status |
| --- | ---: | --- | --- | --- | --- |
| `Example2/175551M/top_view.dwg` | 2 | `7339` `Lg_area`; `733A` `Lg_area` | binary ACIS, 5350 and 6322 bytes | no `GEBAL_REGION_BOUNDARY` XDATA | failed, `region_conversion_failed` |
| `Example3/104310M/top_view.dwg` | 3 | `5E75` `Lg_area`; `5E76` `Lg_area`; `5E77` `Lg_area` | binary ACIS, 4806, 4740, and 4740 bytes | no `GEBAL_REGION_BOUNDARY` XDATA | failed, `region_conversion_failed` |
| `Example3/137019M/top_view.dwg` | 2 | `6029` `Lg_area`; `602A` `Lg_falling` | binary ACIS, 6308 and 6364 bytes | no `GEBAL_REGION_BOUNDARY` XDATA | failed, `region_conversion_failed` |
| `Example5/175020/top_view.dwg` | 2 | `B68` `Lg_prod`; `B69` `Lg_area` | binary ACIS, 2489 and 4017 bytes | no `GEBAL_REGION_BOUNDARY` XDATA | failed, `region_conversion_failed` |

All nine real REGION entities are represented after DWG to DXF conversion as opaque binary ACIS bodies. `ezdxf` exposes ACIS bytes for each entity, but no deterministic boundary loops, no Stage 7 boundary XDATA, and no consumable outer/hole loop relationships. Stage 7 therefore correctly preserves these REGION entities unchanged and reports `region_conversion_failed`; no production conversion patch is required.

Read-only verification preserved source DWG checksums, converted DXF checksums, entity counts, and modelspace extents for all four files. Because no REGION converted successfully, the verification did not write any separate Stage 7 output DXF.

A generated regression test now covers mixed output behavior: when one REGION converts and another opaque REGION fails, the output replaces only the converted REGION and preserves the failed REGION handle, layer, and color.

## Limitations

Stage 7 does not parse arbitrary ACIS/SAT/SAB REGION body data into boundary loops. REGIONs without supported loop evidence are reported and preserved. Spline-like or otherwise non-arc curves require evidence-provided tessellated vertices and are marked approximated.

This stage does not perform layer normalization, semantic classification, CAD-to-JSON validation, geometry centering/scaling/rotation, or AI-assisted interpretation.

## Exit Gate

Stage 7 is complete when:

- supported planar REGIONs return typed closed-loop geometry in memory;
- explicit output writes are atomic and replace only converted REGIONs;
- source DXF checksum remains unchanged;
- unrelated entities and layers are preserved;
- all Stage 7 tests pass;
- full package tests pass;
- package import and live-script compile checks pass.



