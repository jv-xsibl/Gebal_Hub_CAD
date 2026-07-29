# Stage 9 Layer Rewriting

## Objective

Stage 9 writes a new normalized DXF copy that rewrites confidently classified entities onto clear operational layer names while preserving source geometry, source file bytes, and audit evidence.

This stage consumes a `.dxf` file and the Stage 8 `ClassificationResult`. It does not mutate the input file, does not overwrite the input path by default, does not transform coordinates, does not scale, rotate, center, flatten, explode blocks, validate product dimensions, compare JSON to CAD, export DWG, or use AI.

## Default Target Layers

The default target layer names are intentionally human-readable and do not use a `GEBAL` prefix:

- `PRODUCT`
- `SAFETY_ZONE`
- `FOUNDATION`
- `DIMENSIONS`
- `TEXT`
- `HATCH`
- `REFERENCE`
- `EXTERNAL`
- `NON_OPERATIONAL`
- `REVIEW_REQUIRED`

Default Stage 8 role mapping:

- `product_geometry` -> `PRODUCT`
- `safety_zone` -> `SAFETY_ZONE`
- `foundation_or_installation` -> `FOUNDATION`
- `dimensions` -> `DIMENSIONS`
- `text_annotation` -> `TEXT`
- `hatch_or_fill` -> `HATCH`
- `construction_or_reference` -> `REFERENCE`
- `external_or_proxy` -> `EXTERNAL`
- `non_operational` -> `NON_OPERATIONAL`
- `ambiguous` -> `REVIEW_REQUIRED`
- `review_required` -> `REVIEW_REQUIRED`

Many source layers may map to the same target layer. Target names and basic styles are configurable through `LayerRewriteConfig`.

## Rewrite Rules

Entities move only when Stage 8 classification is sufficiently confident and not contradicted by review evidence.

Default uncertainty policy:

- low-confidence layers route to `REVIEW_REQUIRED`;
- ambiguous or review-required layers route to `REVIEW_REQUIRED`;
- mixed operational/non-operational layers route to `REVIEW_REQUIRED`;
- proxy evidence routes to `REVIEW_REQUIRED`;
- unsupported or review-required entity types route to `REVIEW_REQUIRED`;
- external-reference evidence routes to `EXTERNAL` and emits preservation evidence.

`LayerRewriteConfig(uncertain_layer_action="preserve")` preserves uncertain, mixed, proxy, and unsupported content on its original layer instead of routing it to `REVIEW_REQUIRED`.

## Preservation Strategy

Stage 9 opens the source DXF into memory, creates deterministic target layers, rewrites entity `dxf.layer` attributes only, and writes an explicit output path. It iterates modelspace, paperspace layouts, and non-layout block definitions so block content is preserved and can be layer-normalized without exploding INSERTs.

Preserved by design:

- all source entities;
- handles where ezdxf preserves them during copy save;
- geometry, coordinates, Z values, blocks, INSERT transforms, colors, linetypes, dimensions, text, hatches, external-reference/proxy evidence;
- source checksum;
- original layer names in output audit mapping and entity XDATA where ezdxf permits.

Entity XDATA appid:

```text
GEBAL_STAGE9_REWRITE_AUDIT
```

The XDATA payload records source handle, original layer, target layer, and stage number.

## Atomic Output

Stage 9 writes to a same-directory temporary DXF, reopens that temporary file, validates preservation checks, then promotes with `os.replace`.

If save, reopen, entity-count validation, extent validation, source-checksum validation, or promotion fails, the temporary file is cleaned up and an existing destination is preserved.

## Validation

Validation is performed before output promotion and again in the returned result after reopening the final file.

Checks:

- source checksum unchanged;
- modelspace entity count preserved;
- all layout and block entity count preserved;
- modelspace extents preserved within configurable tolerance;
- no entities deleted;
- no geometry transformation detected through extents comparison;
- no configured target layer starts with `GEBAL`;
- AI not used.

Stable Stage 9 issue codes:

- `low_confidence_classification`
- `ambiguous_layer_mapping`
- `mixed_content_preserved`
- `unsupported_entity_preserved`
- `proxy_entity_preserved`
- `external_reference_preserved`
- `entity_move_failed`
- `entity_count_mismatch`
- `geometry_extents_mismatch`
- `output_write_failed`
- `output_validation_failed`

## API

```python
from pathlib import Path

from gebal_cad_normalizer.cad import (
    canonicalize_dxf,
    classify_layers,
    inventory_dxf,
    rewrite_layers,
)

source = Path("warehouse/inventory/SKU_137132M/normalized/source.dxf")
classification = classify_layers(inventory_dxf(source), canonicalize_dxf(source))
result = rewrite_layers(source, classification, Path("warehouse/inventory/SKU_137132M/normalized/source_rewritten.dxf"))
```

The result includes source/output paths and checksums, entity totals before/after, original-to-target layer mapping, moved/unchanged/review counts, warnings/issues, extents before/after, and preservation checks.

## Tests and Results

Implemented in `tests/test_rewrite.py`.

Coverage includes:

- confident product mapping;
- safety-zone mapping;
- dimensions, text, hatch, and foundation mapping;
- several source layers to one target;
- low-confidence routing to review;
- mixed content reviewed or preserved by config;
- proxy and XREF/external evidence preservation;
- blocks and INSERT transforms preserved;
- original-layer evidence retained;
- configurable target names;
- entity count preserved;
- extents preserved;
- source unchanged;
- atomic failure safety;
- deterministic output mapping.

Verification performed:

```text
python -m pytest tests\test_rewrite.py -> 14 passed
```

Full-suite and compile/import verification are run as the final Stage 9 exit gate.

## Live Command

The live script is manual and is not part of pytest discovery.

```powershell
python tests\live\stage9_live_rewrite.py --input path\to\input.dxf --output path\to\normalized.dxf
python tests\live\stage9_live_rewrite.py --input path\to\input.dxf --output path\to\normalized.dxf --confidence-threshold 0.75
python tests\live\stage9_live_rewrite.py --input path\to\input.dxf --output path\to\normalized.dxf --classification-json path\to\classification.json
```

When `--classification-json` is omitted, the script runs Stage 5 inventory, Stage 6 canonicalization, and Stage 8 classification automatically. It prints compact JSON with paths, checksums, entity totals, move/review counts, issue-code counts, and preservation status.

## Limitations

- Stage 9 trusts Stage 8 classification input; it does not introduce new semantic CAD interpretation.
- It does not add REGION conversion logic. Use Stage 7 first when converted REGION output is desired.
- It does not explode INSERTs or flatten block geometry.
- It does not infer unknown units.
- Extents validation is a preservation check, not product dimension validation.
- It does not produce DWG output.
- It does not use AI.

## Exit Gate

Stage 9 is complete when:

- source DXF remains byte-for-byte unchanged;
- no entity disappears silently;
- confident layers rewrite to readable operational layer names;
- ambiguous, low-confidence, mixed, proxy, unsupported, and external evidence is preserved or routed according to config;
- output DXF reopens successfully;
- entity counts and modelspace extents are preserved;
- target layers do not start with `GEBAL`;
- Stage 9 tests, full pytest, package import check, and live-script compile check pass.
