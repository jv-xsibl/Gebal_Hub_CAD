# Stage 11 JSON-CAD Validation

## Scope

Stage 11 adds deterministic, read-only JSON-vs-CAD validation in `src/gebal_cad_normalizer/cad/validate.py`.

It consumes normalized JSON expectations and Stage 10 `MeasurementResult` candidates. It does not modify JSON, modify CAD, rewrite layers, infer geometry semantics beyond Stage 10 roles, repair source data, use Windows Sandbox, or use AI.

## Validated Top-View Fields

Stage 11 validates only:

- product top-view length/width;
- safety-zone top-view length/width;
- falling-space or impact area when a safety candidate has reliable area evidence;
- product-inside-safety containment from candidate bounding boxes.

Product height, weight, age range, materials, free-fall height, and similar non-plan-view fields are reported as `not_verifiable`.

## Statuses

Stage 11 uses the requested status model:

- `pass`
- `pass_with_warnings`
- `review_required`
- `fail`
- `not_verifiable`

Invalid or zero JSON safety values are emitted as source-data issues and are never corrected from CAD values.

## Ranking Logic

All plausible Stage 10 product and safety candidates are compared and preserved in the output alternatives.

Candidate rank is deterministic:

1. validation status bucket;
2. relative residual;
3. absolute residual;
4. inverse confidence;
5. role, source layer, and candidate id as stable tie breakers.

Width/depth rotation is allowed by comparing sorted JSON and CAD dimension pairs.

Equivalent best candidates are marked `review_required` with `validation_ambiguous_candidates`; alternatives remain in the check result.

## Tolerances

Defaults are configurable through `ValidationConfig`:

- dimension pass: `5 mm` absolute or `0.5%` relative;
- dimension warning: `10 mm` absolute or `1.0%` relative;
- area pass: `0.1 m2` absolute or `1.0%` relative;
- area warning: `0.25 m2` absolute or `2.0%` relative.

A close match inside warning tolerance becomes `pass_with_warnings`, not a forced pass.

## Review and Unverifiable Gates

Stage 11 does not force pass decisions when evidence is weak. The following become review or unverifiable gates:

- weak or review-required Stage 10 candidate classification;
- unknown or ambiguous CAD units;
- explicit unit override or unit inference evidence;
- opaque REGION/ACIS evidence;
- proxy, XREF, underlay, unsupported geometry evidence;
- non-planar geometry evidence;
- missing CAD candidates;
- missing JSON values.

## Stable Issue Codes

- `validation_missing_json_value`
- `validation_invalid_source_json_value`
- `validation_missing_cad_candidate`
- `validation_dimension_mismatch`
- `validation_area_mismatch`
- `validation_close_match`
- `validation_ambiguous_candidates`
- `validation_weak_classification`
- `validation_units_unknown`
- `validation_unit_inference_used`
- `validation_unit_override_used`
- `validation_cad_risk_evidence`
- `validation_containment_mismatch`
- `validation_top_view_not_verifiable`

## API

```python
from gebal_cad_normalizer.cad.validate import validate_json_against_cad

validation = validate_json_against_cad(cad_processing_request, measurement_result)
```

Use `write_validation_json(validation, path)` for deterministic JSON output.

## Live Command

```powershell
python tests\live\stage11_live_validate.py --json product.json --input drawing.dxf --vendor-profile bluestone_playground --json-output tests\output\stage11_validation.json
```

Optional unit override:

```powershell
python tests\live\stage11_live_validate.py --json product.json --input drawing.dxf --unit mm
```

The script hashes the input DXF before and after and reports whether the source checksum remained unchanged.

## Tests

Implemented in `tests/test_validate.py`.

Coverage includes:

- exact, rotated, and tolerance dimension matches;
- dimension mismatch;
- area match and mismatch;
- product and safety candidate separation;
- product-inside-safety containment;
- ambiguous candidate preservation;
- unknown, inferred, and overridden units;
- invalid zero safety JSON;
- missing JSON values and missing CAD candidates;
- opaque REGION, non-planar, proxy/XREF-style risk evidence;
- non-top-view fields;
- deterministic output;
- input immutability.

## Limitations

Stage 11 validates Stage 10 measured candidates. It does not reconstruct opaque geometry, inspect side views, validate 3D properties, or decide whether an ambiguous CAD layer is semantically correct. Unknown units remain unverifiable unless Stage 10 has explicit or high-confidence unit evidence.
