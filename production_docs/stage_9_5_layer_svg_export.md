# Stage 9.5 Layer SVG Export

## Objective

Stage 9.5 creates review evidence: one deterministic standalone SVG per source CAD layer, plus an optional `combined.svg`, without modifying the DXF/DWG source drawing.

This stage does not rewrite layers, classify layers, transform CAD geometry, center the drawing, measure product dimensions, validate against JSON, call AI, or prepare Stage 10 measurement logic. SVG translation and Y-axis flipping are display-only transforms recorded in the manifest.

## Architecture

Public API:

```python
from gebal_cad_normalizer.cad import SvgExportConfig, export_layer_svgs

result = export_layer_svgs(
    "source.dxf",
    "svg_review",
    config=SvgExportConfig(include_combined=True),
)
```

Output structure:

```text
<output_dir>/
├── manifest.json
├── combined.svg
└── layers/
    ├── 001_0.svg
    ├── 002_PRODUCT.svg
    └── 003_SAFETY_ZONE.svg
```

Layer filenames are ordered deterministically and use sanitized readable source layer names. Names are not prefixed with `GEBAL`; sanitized duplicates receive a stable numeric suffix.

## DWG To DXF Flow

DXF input is opened directly with `ezdxf`.

DWG input is accepted only through the existing Stage 4 `OdaConverter`. Stage 9.5 converts DWG into a temporary DXF and then renders that temporary DXF. The original DWG is never edited or opened for binary modification.

## Rendering Rules

Rendered where safely available:

- `LINE` as native SVG `<line>`;
- `LWPOLYLINE` and 2D `POLYLINE` as `<path>`, preserving open/closed state and bulge arcs as SVG arc commands;
- `ARC` and `CIRCLE` as native path/circle geometry;
- `ELLIPSE` and `SPLINE` as flattened SVG paths for review preview;
- `HATCH` from available polyline boundary paths, with `svg_hatch_simplified` evidence;
- `TEXT` and `MTEXT` as escaped SVG text using a generic built-in font fallback, with `svg_text_fallback` evidence;
- `INSERT` content through ezdxf virtual entity traversal so block transforms are rendered without exploding or mutating the source.

SVGs are standalone, transparent by default, contain no external images, scripts, network assets, or font dependencies, and use non-scaling stroke behavior with a configurable stroke width. `SvgExportConfig(monochrome=True)` forces a single review color; otherwise usable CAD/layer colors are preserved.

## Coordinate Transforms

Per-layer extents are calculated from renderable source coordinates. Each SVG view is fit to visible geometry with configurable padding. Display coordinates use:

```text
svg_x = cad_x - min_x + padding
svg_y = max_y - cad_y + padding
```

The CAD geometry itself is not transformed, centered, scaled, rotated, saved, exploded, or rewritten. The manifest records source extents and the display transform/viewBox used for each SVG.

## Unsupported Content

Unsupported, proxy, external-reference, raster, or render-failed content is reported through stable issue codes instead of being silently dropped:

- `svg_empty_layer`
- `svg_extents_unavailable`
- `svg_entity_unsupported`
- `svg_entity_render_failed`
- `svg_insert_render_failed`
- `svg_text_fallback`
- `svg_hatch_simplified`
- `svg_proxy_skipped`
- `svg_external_reference_skipped`
- `svg_output_failed`

The manifest records entity counts, rendered counts, skipped counts, entity types, source extents, display transforms, warnings, source path, source checksum, DXF version, and units.

## Atomic Output

Stage 9.5 writes to a temporary sibling directory first. Only after every layer SVG, optional `combined.svg`, and `manifest.json` are written does it promote the directory to the requested output path. If the requested path is an existing file or output promotion fails, the requested path is preserved and `SvgExportError(svg_output_failed)` is raised.

## Tests And Results

Implemented in `tests/test_svg_export.py`.

Coverage includes:

- LINE layer;
- polyline with bulge;
- ARC and CIRCLE;
- ELLIPSE and SPLINE;
- text rendering and safe escaping;
- hatch rendering with documented simplification;
- nested INSERT transform;
- multiple layers producing separate SVGs;
- empty layer handling;
- unsupported content reported;
- deterministic SVG and manifest output;
- unchanged source checksum;
- combined SVG containing all renderable layers;
- sanitized duplicate layer filenames remaining unique;
- DWG input routed through mocked ODA conversion;
- output-directory failure safety.

Latest targeted verification:

```text
python -m pytest tests\test_svg_export.py -> 16 passed
```

Full-suite, import, and live-script compile checks are part of the final Stage 9.5 exit gate.

## Live Command

The live script is manual and is not part of pytest discovery.

```powershell
python tests\live\stage9_5_live_svg_export.py --input path\to\source.dxf --output-dir path\to\svg_review --combined
python tests\live\stage9_5_live_svg_export.py --input path\to\source.dwg --output-dir path\to\svg_review --oda-exe C:\Path\To\ODAFileConverter.exe --combined
python tests\live\stage9_5_live_svg_export.py --input path\to\source.dxf --output-dir path\to\svg_review --monochrome --background white
```

The script prints compact JSON with source checksum, output paths, layer count, rendered/skipped counts, issue-code counts, source preservation status, and AI usage status.

## Limitations

- SVG is evidence for human or later advisory AI review, not normalized CAD output.
- HATCH support is boundary-based and reports simplification.
- TEXT/MTEXT uses a generic SVG text fallback and does not depend on external CAD fonts.
- ELLIPSE/SPLINE preview paths are flattened for SVG review while source CAD curves remain unchanged.
- Unsupported, proxy, raster, and external reference content is reported, not reconstructed.
- Paper-space layouts are not exported in this stage.
- Stage 9.5 does not classify layers or decide operational semantics.

## Exit Gate

Stage 9.5 is complete when:

- source CAD checksum remains unchanged;
- one SVG is written for every source layer;
- optional `combined.svg` contains all renderable layers;
- manifest records source checksum, version, units, layer filenames, counts, extents, transforms, warnings, and issue codes;
- no output layer/file names are prefixed with `GEBAL`;
- unsupported content is visible in manifest warnings;
- no AI is called or integrated;
- no Stage 10 measurement or JSON validation logic is introduced;
- targeted tests, full pytest, package import check, and live-script compile check pass.
