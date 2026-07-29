# Stage 8 Layer Classification

## Objective

Stage 8 classifies original drawing layers and their content into advisory operational roles using deterministic Python rules only. It consumes the Stage 5 `DxfInventoryResult` and may use optional Stage 6 `CanonicalGeometryResult` evidence.

This stage does not rename layers, move entities, delete content, flatten blocks, explode inserts, rewrite geometry, save CAD files, infer units as millimetres, or use AI.

## Roles

- `product_geometry`
- `safety_zone`
- `foundation_or_installation`
- `dimensions`
- `text_annotation`
- `hatch_or_fill`
- `construction_or_reference`
- `external_or_proxy`
- `non_operational`
- `ambiguous`
- `review_required`

Each `LayerClassification` preserves the original layer name, assigned role, confidence, matched rules, positive and negative evidence, alternative roles, entity counts, review reason, detailed evidence, and issues.

## Evidence Hierarchy

The classifier combines multiple evidence types:

- entity types and counts from Stage 5;
- layer table metadata: color, linetype, visibility, frozen/locked state, and plot state;
- optional closed/open geometry counts and relative area evidence from Stage 6;
- block ancestry from Stage 6;
- XREF, underlay, proxy, and 3D/non-zero-Z indicators;
- normalized case-insensitive layer-name keywords;
- isolated vendor override rules;
- optional isolated vendor alias profiles.

Layer-name and vendor-alias evidence are never sufficient when entity or geometry evidence contradicts them. For example, a layer named `SAFETY_ZONE` that contains only `DIMENSION` entities becomes `review_required`, not `safety_zone`. Proxy/XREF evidence retains priority, mixed operational content remains `review_required`, and strong vendor-alias geometry contradictions use `vendor_alias_conflict`.

## Confidence Rules

Rules contribute deterministic weights by role. Pure entity evidence is strong for dimensions, text, hatch/fill, proxy, and external content. Closed operational geometry supports product and safety candidates, and supports foundation/install candidates only when matching foundation/install evidence exists.

Assignments below the configured assignment threshold become `ambiguous`. Conflicting or mixed operational/non-operational evidence becomes `review_required`. Alternative candidates are retained in deterministic score order.

Unknown or missing units are reported with `unknown_units`; Stage 8 does not assume millimetres or perform measurement validation.

## Issue Codes

Stable Stage 8 issue codes:

- `insufficient_evidence`
- `conflicting_evidence`
- `unknown_units`
- `external_reference_content`
- `proxy_content`
- `mixed_operational_content`
- `vendor_alias_conflict`
- `classification_rule_error`

## Configuration

`LayerClassificationConfig` keeps the default rules generic and deterministic. Vendor-specific overrides use `LayerClassificationOverride` with an optional `vendor` field and regex pattern. Overrides are isolated; a vendor-specific override only applies when the classifier config names the same vendor.

Built-in vendor alias profiles are selected with `LayerClassificationConfig(vendor_profile="bluestone_playground")`. The profile is optional and isolated from generic defaults. Aliases are exact after case-insensitive normalization, and the classification evidence preserves `vendor_profile_name`, `matched_vendor_alias`, vendor alias rule matches, positive evidence, negative evidence, alternatives, issues, and review reasons. Unknown profile names fail with a clear `ValueError`.

`bluestone_playground` aliases: `Lg_prod`/`lg_prod` -> `product_geometry`; `Lg_area`/`lg_area`, `Lg_falling`/`lg_falling`, `LCPROD_FALLINGSPACE`, and `LCPROD_ENSAFETYREGION` -> `safety_zone`; `Lg_dim` and `DIMENSION` -> `dimensions`; `Lg_txt` -> `text_annotation`; `Lg_boundary` -> `construction_or_reference`; `lc_ground` -> `foundation_or_installation`; `Defpoints` -> `non_operational`; `ASHADE` -> `hatch_or_fill`.

## Tests and Results

Implemented in `tests/test_classify.py`.

Coverage includes:

- obvious product layer;
- safety/fall/impact-area layer;
- dimensions layer;
- text-only layer;
- hatch-only layer;
- foundation/install layer;
- XREF/proxy layer;
- hidden/non-plot reference layer;
- mixed-content layer becoming `review_required`;
- contradictory name versus geometry;
- unknown units preserved as an issue;
- deterministic ordering and serialization;
- source inventory and canonical inputs not mutated;
- configurable vendor override;
- low-confidence ambiguity;
- built-in Bluestone playground alias mapping;
- case-insensitive aliases;
- alias plus matching geometry high confidence;
- alias without canonical geometry moderate confidence;
- vendor alias contradiction becoming `review_required`;
- vendor mixed content remaining `review_required`;
- generic mode remaining unchanged without a vendor profile;
- unknown vendor profile failure;
- deterministic vendor profile serialization;
- source inventory, canonical evidence, and config not mutated.

Verification performed:

```text
python -m pytest tests\test_classify.py -> 26 passed
python -m pytest -> 179 passed
package import check -> passed
python -m py_compile src\gebal_cad_normalizer\cad\classify.py src\gebal_cad_normalizer\cad\__init__.py tests\live\stage8_live_classify.py tests\live\batch_cad_variation_audit.py tests\test_classify.py -> passed
```

Batch classification audit comparison over 26 selected top-view CAD files:

- generic profile: 166 ambiguous/review-required layer assignments;
- `bluestone_playground`: 103 ambiguous/review-required layer assignments;
- improved layers: 74;
- still unresolved: 103;
- intentional review escalations: 11 `lc_ground` layers with 3DFACE/non-zero-Z evidence;
- main remaining unresolved families: `Lg_txt` mixed text/INSERT/geometry, `Lg_dim` mixed dimensions/geometry/SOLID, `Lg_area`/`Lg_falling` layers with text mixed into safety geometry, colored hatch/product layers with mixed HATCH/MTEXT/POLYLINE, layer `0` proxy/SOLID/mixed content, and `lc_ground` 3DFACE content.

Audit outputs were refreshed in `tests/output/cad_variation_audit_generic` and `tests/output/cad_variation_audit_bluestone`.

## Live Command

The live script is manual and is not part of normal pytest discovery.

```powershell
python tests\live\stage8_live_classify.py --input path\to\input.dxf
python tests\live\stage8_live_classify.py --input path\to\input.dxf --json-output path\to\classification.json
python tests\live\stage8_live_classify.py --input path\to\input.dxf --skip-canonical
python tests\live\stage8_live_classify.py --input path\to\input.dxf --vendor-profile bluestone_playground
```

It runs Stage 5 inventory, optional Stage 6 canonicalization, then Stage 8 classification. The compact JSON summary includes layer count, role counts, ambiguous/review layers, confidence range/average, issue codes, whether canonical evidence was used, and whether the source checksum remained unchanged.

## Limitations

- Classification is advisory and evidence-backed; it does not normalize layers or modify CAD content.
- Text-label semantic extraction is limited to evidence exposed by prior stages; Stage 8 does not parse arbitrary annotation strings yet.
- Relative scale evidence depends on optional Stage 6 canonical geometry and available closed polylines.
- XREF detection is based on Stage 5 indicators and entity-type evidence exposed by `ezdxf`.
- AI-assisted classification remains intentionally absent.

## Exit Gate

Stage 8 is complete when:

- every layer receives a deterministic advisory classification;
- matched rules and evidence are preserved;
- low-confidence and conflicting evidence becomes `ambiguous` or `review_required`;
- source inventory and canonical inputs are not mutated;
- no layer renaming or geometry rewriting occurs;
- Stage 8 tests, full pytest, import checks, and live-script compile checks pass.

