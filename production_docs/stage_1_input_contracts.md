# Stage 1 — Input Contracts, Adapters, and Data-Quality Handling

## Objective

Create a stable typed input contract for the future CAD-processing pipeline and isolate vendor-specific payload parsing from downstream CAD logic.

## Scope Completed

Stage 1 introduced:

* typed internal CAD request models;
* a shared adapter contract;
* Bluestone PIM parsing;
* normalized Gebal product parsing;
* local DWG test-input parsing;
* tolerant JSON fixture loading;
* input-quality issue reporting;
* regression tests for malformed and inconsistent data.

No downloading or CAD-processing functionality was implemented.

## Files Added or Changed

### Package Models and Contracts

* `src/gebal_cad_normalizer/models.py`
* `src/gebal_cad_normalizer/pipeline.py`
* `src/gebal_cad_normalizer/__init__.py`
* `src/gebal_cad_normalizer/adapters/__init__.py`
* `src/gebal_cad_normalizer/adapters/base.py`

### Input Adapters

* `src/gebal_cad_normalizer/adapters/bluestone.py`
* `src/gebal_cad_normalizer/adapters/unified.py`
* `src/gebal_cad_normalizer/adapters/local.py`

### Fixture Loading

* `src/gebal_cad_normalizer/fixture_loader.py`

### Tests and Fixtures

* `tests/test_models.py`
* `tests/test_adapters.py`
* `tests/fixtures/bluestone_product.json`
* `tests/fixtures/bluestone_ambiguous.json`
* `tests/fixtures/commented_fixture.json`
* `tests/fixtures/F24706M.json`

### Project Configuration and Documentation

* `pyproject.toml`
* `README.md`

## Internal Models

Stage 1 introduced focused Pydantic models for CAD-related inputs only.

The models cover:

* product identity;
* CAD asset metadata;
* expected product dimensions;
* expected safety-zone data;
* input-quality issues;
* normalized CAD-processing requests;
* adapter results.

The complete product schema is intentionally not duplicated inside the CAD normalizer.

## Adapter Contract

All adapters follow a common parsing boundary:

```python
parse(payload: Mapping[str, Any]) -> AdapterResult
```

Adapters are responsible only for translating external input into the internal request model.

They do not:

* download files;
* access the network;
* inspect DWGs;
* convert CAD files;
* modify geometry;
* silently correct vendor values;
* mutate the source payload.

## Bluestone Adapter

The Bluestone adapter:

* reads the first product under `results`;
* indexes vendor attributes by their `number`;
* extracts SKU and product name;
* extracts expected product dimensions;
* extracts safety-zone dimensions;
* extracts safety-area and free-fall-height data where available;
* identifies top-view DWG candidates using vendor media metadata;
* rejects side-view, image, PDF, and 3D product-model assets;
* reports missing or ambiguous top-view CAD assets.

Top-view selection was later centralized and hardened during Stage 2.

## Unified Adapter

The unified adapter parses normalized Gebal-style product records.

Supported fields include:

```text
sku
vendor
name
technical.dimensions.length_mm
technical.dimensions.width_mm
technical.dimensions.height_mm
safety.safety_zone.length_mm
safety.safety_zone.width_mm
safety.cfh_mm
media.top_view_cad_file
```

The adapter:

* accepts safely convertible numeric strings;
* converts blank strings to `None`;
* records warnings for blank values;
* reports invalid numeric values;
* reports zero or negative dimensions;
* preserves valid product data even when some fields are invalid;
* ignores unrelated schema problems that do not affect CAD processing.

## Local Adapter

The local adapter supports development and testing with an explicit DWG path.

It validates:

* non-empty SKU;
* file existence;
* `.dwg` extension;
* optional expected dimensions;
* possible SKU and filename mismatch.

A filename mismatch produces an issue but does not automatically reject an explicitly supplied local file.

## Tolerant Fixture Loader

The fixture loader:

1. attempts strict JSON parsing;
2. optionally tolerates `//` comments for controlled local fixtures;
3. reports when tolerant parsing was required;
4. never modifies the source file.

This mechanism is intended for local regression fixtures and is not a general JSON5 parser.

## Data-Quality Rules

Stage 1 added detection for:

* missing SKU;
* missing top-view CAD asset;
* ambiguous CAD asset;
* zero or negative product dimensions;
* zero or negative safety-zone dimensions;
* blank strings used instead of null;
* invalid numeric text;
* filename and SKU inconsistencies;
* missing local files;
* invalid local file extensions.

Issues use stable machine-readable codes.

## F24706M Regression Case

A real normalized sample was added to verify handling of invalid safety-zone data.

The input contained:

```text
safety_zone.length_mm = 0
safety_zone.width_mm = 0
```

Final required behavior was verified:

* parsing does not crash;
* a non-null request is returned;
* SKU and valid product data are preserved;
* CAD metadata is preserved where supplied;
* both invalid safety dimensions become `None`;
* both fields generate `invalid_safety_data` issues;
* no automatic replacement with product dimensions occurs;
* the source payload remains unchanged.

This established the rule that adapters sanitize invalid external values before constructing strict internal models.

## Key Decisions

### Strict Internal Models, Tolerant External Adapters

Internal models remain strict.

External adapters are responsible for converting malformed vendor values into:

* safe internal values;
* explicit quality issues.

This prevents one invalid field from destroying an otherwise useful processing request.

### No Silent Correction

The module may detect that data is likely incorrect, but it does not silently repair it.

For example, zero safety-zone dimensions are not automatically replaced with product dimensions.

### Vendor Isolation

Vendor-specific attribute traversal remains inside vendor adapters.

Downstream CAD-processing stages will work only with the shared `CadProcessingRequest`.

## Testing

Stage 1A initially added five model tests.

Stage 1B expanded adapter coverage.

After the F24706M regression fix, the final suite result was:

```text
15 passed
```

Tests cover:

* model creation;
* valid Bluestone parsing;
* top-view CAD selection;
* side-view and 3D rejection;
* ambiguous asset handling;
* normalized numeric parsing;
* blank-string warnings;
* zero safety dimensions;
* missing CAD assets;
* local file validation;
* filename/SKU mismatch;
* comment-tolerant fixture loading;
* source payload immutability;
* F24706M request preservation.

## Verification Commands

```bash
python -m pip install -e .
python -m pytest
python -c "from gebal_cad_normalizer.models import CadProcessingRequest; print('models ok')"
```

## Verification Results

* Editable installation passed.
* Package imports passed.
* Adapter imports passed.
* Fixture-loader imports passed.
* All 15 tests passed.
* No CAD or GUI dependency was required.

## Known Limitations

* Bluestone extraction currently supports known vendor metadata patterns and may require expansion for other vendor formats.
* The comment-tolerant loader supports only controlled `//` comment cases.
* Input issues are diagnostic only at this stage.
* No network or CAD asset access occurs yet.
* Asset selection relies on metadata and does not inspect binary DWG contents.

## Explicit Exclusions

Stage 1 did not implement:

* downloading;
* network operations;
* source-file versioning;
* ODA conversion;
* ezdxf;
* DWG inspection;
* DXF inspection;
* REGION conversion;
* geometry normalization;
* layer rewriting;
* measurement;
* JSON-versus-CAD validation;
* AI assistance.

## Exit Gate

Stage 1 passed.

All known input formats can be translated into the shared CAD request model or return clear issues. Invalid individual fields no longer discard otherwise valid requests, and the full regression test suite passes.
