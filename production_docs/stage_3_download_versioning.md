# Stage 3 Download and Source Versioning

## Objective

Implement only CAD source downloading/staging and source version management for selected top-view DWG assets. Stage 3 turns a `CadAssetDescriptor` into a verified staged source file, then promotes it into the controlled warehouse layout when checksum comparison shows the source is new or changed.

Stage 3 does not convert, inspect, normalize, validate, or modify CAD geometry.

## Architecture

Stage 3 adds a focused `gebal_cad_normalizer.assets` package:

- `downloader.py` owns remote streamed downloads and local-file staging for tests/development.
- `version_manager.py` owns warehouse paths, checksum comparison, current-source promotion, archive retention, and manifest writing.
- `assets/__init__.py` exports the public Stage 3 types.

The downloader accepts the existing immutable `CadAssetDescriptor` model. Remote assets are downloaded only when `url` exists. Local assets use `local_path` staging and do not touch the network. Both paths write to a temporary staged file first, validate it, calculate SHA-256, and return `DownloadResult`.

The version manager accepts a staged `DownloadResult`; it never downloads and never writes directly to the current source before promotion.

## Folder Layout

Managed sources are stored under the configured warehouse root:

```text
inventory/SKU_<sku>/
├── source/
│   ├── <sku>_source_current.dwg
│   └── source_manifest.json
└── archive/
```

The manifest records:

- SKU;
- current filename;
- SHA-256;
- source URL;
- media ID;
- revision;
- vendor updated timestamp;
- local download timestamp.

SKU and archive revision tokens are sanitized before being used in paths or filenames.

## Checksum and Update Rules

First file:

- the staged DWG is promoted to `<sku>_source_current.dwg`;
- `source_manifest.json` is created;
- no archive file is created.

Unchanged file:

- the staged checksum is compared with the current manifest checksum;
- duplicate staged download is deleted;
- current source, manifest, and archives are left unchanged;
- status is `unchanged`.

Changed file:

- the existing current file is copied into a pending archive file;
- the staged file is atomically promoted to current;
- the manifest is atomically replaced;
- the archive copy is finalized with timestamp and archived-revision metadata;
- only the newest three archived source files are retained;
- status is `updated`.

Checksum comparison is the final authority. Vendor revision and timestamps are recorded for traceability, not used to bypass checksum comparison.

## Atomic Safety Decisions

Downloader safety:

- uses `httpx` streamed downloads with timeouts;
- enforces configurable maximum file size while streaming;
- sanitizes filenames;
- rejects empty files;
- checks that a staged asset is plausibly DWG by extension, content type, or DWG header signature;
- calculates SHA-256 only on staged content;
- cleans temporary files after download/staging failures;
- never overwrites managed current source paths.

Versioning safety:

- writes managed files only below the warehouse root;
- requires staged paths to be contained inside the warehouse root;
- writes manifest data to a temporary file before replacement;
- uses `os.replace` for promotion and manifest replacement;
- uses pending archive files during changed-source promotion;
- rolls back current source and manifest on promotion failure;
- removes partial temporary and pending files when failures occur.

## Tests and Results

Added downloader tests:

- successful streamed download using mocked HTTP;
- timeout/error handling;
- oversized file rejection;
- empty file rejection;
- temporary file cleanup after failure;
- source metadata preservation;
- local-file staging without network.

Added versioning tests:

- first source creates current and manifest;
- identical checksum returns unchanged and deletes staged duplicate;
- changed source archives current and promotes new;
- only three archived versions are retained;
- failed promotion preserves current and manifest;
- manifest updates correctly;
- source paths are contained within the warehouse root;
- source inputs are not mutated.

Verification run:

```text
python -m pytest -> 40 passed
assets import check -> passed
root package public import check -> passed
```

All previous Stage 1 and Stage 2 tests still pass.

## Known Limitations

- DWG plausibility checks are intentionally shallow; binary DWG validation is deferred to later CAD tooling stages.
- Archive metadata is filename-level only; no archive sidecar manifest is written yet.
- The version manager requires staged files to live under the warehouse root to keep path containment simple and auditable.
- Network retry/backoff policy is not implemented in Stage 3.
- No cloud/object-storage backend is implemented.

## Exit-Gate Status

Passed. Stage 3 safely stages CAD source files, records checksums and source metadata, promotes first/changed sources, detects unchanged sources, preserves current files on failed promotion, and enforces the three-source archive limit.

Explicitly not added: ODA conversion, ezdxf, CAD inspection, REGION conversion, layer normalization, geometry validation, or CAD measurement.