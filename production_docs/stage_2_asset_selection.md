# Stage 2 Asset Selection

## Objective

Harden top-view DWG asset selection so it is deterministic, explainable, and reusable across vendor adapters. Stage 2 only covers candidate scoring and adapter selection. It does not download files, inspect CAD content, convert CAD formats, or normalize geometry.

## Files Added/Changed

Added:

- `src/gebal_cad_normalizer/asset_selector.py`
- `tests/test_asset_selector.py`
- `production_docs/stage_2_asset_selection.md`

Changed:

- `src/gebal_cad_normalizer/adapters/bluestone.py`
- `README.md`

## Selection Rules

Candidates are scored using configurable constants in `AssetSelectionConfig`.

Positive signals:

- `.dwg` extension from filename or URL.
- DWG content type metadata such as AutoCAD/DWG media types.
- DWG file type metadata.
- asset name, description, or document information indicating `TOP VIEW` or `plan view`.
- purpose or vendor classification indicating `2dModel`.
- explicit top-view purpose metadata.

Rejection or strong penalty signals:

- `SIDE VIEW`, side-view variants, or elevation metadata.
- `3D`, `3dModel`, or `PRODUCT MODEL` metadata.
- image file extensions or image content types.
- PDF file extensions or PDF content types.
- non-DWG file extensions.

Decision rules:

- Select exactly one accepted candidate when it has the highest score.
- Return `missing_top_view_cad` when no valid top-view DWG remains after scoring and rejection.
- Return `ambiguous_cad_asset` when multiple accepted candidates tie for the best score.
- Preserve evidence for every candidate: identifier, score, accepted/rejected status, reasons, and final decision.
- Do not rely on filename alone for top-view classification. A candidate needs non-filename top-view metadata.

## Decisions Made

- The selector is implemented as a standalone module under `gebal_cad_normalizer` so vendor adapters can reuse it.
- Selector result models are frozen dataclasses to avoid expanding the existing Pydantic request boundary for Stage 2.
- Bluestone remains responsible for traversing payload structure and extracting fields into `CadAssetDescriptor`.
- Asset selection evidence is available from `AssetSelectionResult`, while Bluestone maps selector decisions into existing stable adapter issue codes.
- Scoring is deterministic and contains no AI, network, download, or CAD-processing behavior.

## Tests Run and Results

- `python -m pytest` -> 25 passed
- package import checks -> passed

## Known Limitations

- Selection depends on vendor-provided metadata and does not inspect binary DWG contents.
- Only top-view DWG selection is handled in Stage 2.
- Ambiguous equally credible assets are intentionally rejected instead of guessed.
- Adapter results do not yet expose full selector evidence through the public `AdapterResult` model; the reusable selector does expose it directly.

## Exit-Gate Status

Passed. Stage 2 exit gate is complete for deterministic asset selection.

