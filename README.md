# Gebal CAD Normalizer

Gebal CAD Normalizer is a deterministic Python application for converting, auditing, standardizing, measuring, and validating vendor top-view CAD files.

It accepts product JSON together with a matching DWG or DXF file and produces an auditable output package containing normalized CAD, SVG previews, technical reports, measurements, validation results, checksums, and a human-readable summary.

The project is designed for conservative CAD automation:

- source files are never modified;
- uncertain geometry is not guessed;
- unknown units are not silently assumed;
- opaque REGION or ACIS geometry is preserved;
- ambiguous content is sent to review;
- processing limits fail visibly instead of hanging;
- no AI is used for geometry processing or final validation.

---

## Current Status

The implemented pipeline currently includes:

- Stage 1 — input models and vendor adapters
- Stage 2 — top-view CAD asset selection
- Stage 3 — CAD download and source versioning
- Stage 4 — DWG/DXF conversion through ODA File Converter
- Stage 5 — DXF inventory and data-quality audit
- Stage 6 — geometry canonicalization
- Stage 7 — deterministic REGION conversion where supported
- Stage 8 — layer and content classification
- Stage 9 — normalized layer rewriting
- Stage 9.5 — layer-by-layer SVG export
- Stage 10 — geometry measurement
- Stage 11 — JSON-versus-CAD validation
- Stage 12 — integrated reporting and package generation
- Stage 12.5 — desktop operator GUI
- Stage 13 — batch end-to-end qualification

The current automated test suite contains more than 230 passing tests.

The Stage 13 qualification batch confirms that all 26 audited occurrences terminate without indefinite hangs. However, the project is not yet approved for unattended production use because several real vendor files still require review due to unknown units, malformed JSON, opaque REGION data, unsupported geometry, source mismatches, or measurement safety-cap breaches.

---

## What the Application Does

The full processing flow is:

```text
Product JSON
    +
Top-view DWG/DXF
    ↓
Input parsing
    ↓
CAD asset selection
    ↓
DWG-to-DXF conversion
    ↓
DXF inventory
    ↓
Geometry canonicalization
    ↓
REGION handling
    ↓
Layer classification
    ↓
Normalized layer rewriting
    ↓
SVG preview export
    ↓
Geometry measurement
    ↓
JSON-versus-CAD validation
    ↓
Manifest + report + normalized CAD package
````

The output is intended to support:

* vendor CAD intake;
* CAD quality checks;
* layer normalization;
* product-footprint identification;
* safety-zone identification;
* JSON-versus-CAD dimensional validation;
* operator review;
* downstream use in Gebal Hub or related CAD workflows.

---

# Quick Start

## Requirements

* Windows
* Python 3.10 or compatible supported version
* ODA File Converter for DWG conversion
* A compatible CAD viewer such as nanoCAD, AutoCAD, BricsCAD, or similar

ODA File Converter is required only when processing DWG files or exporting DWG.

DXF-only processing can run without ODA.

---

## Install

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Run the test suite:

```powershell
python -m pytest
```

---

## Launch the Desktop GUI

The easiest way to operate the application is through the Stage 12.5 desktop GUI:

```powershell
python tests\live\stage12_5_run_gui.py
```

The GUI allows the operator to select:

* product JSON;
* DWG or DXF input;
* output folder;
* ODA File Converter executable;
* vendor classification profile;
* unit override;
* normalized DWG export;
* overwrite behavior.

The pipeline runs on a worker thread so that the interface remains responsive.

After processing, the GUI can open:

* the output package;
* `report.md`;
* the normalized DXF;
* the combined SVG preview.

---

## Recommended GUI Settings for Bluestone Playground CAD

For the audited Bluestone playground files:

```text
Vendor profile: bluestone_playground
Unit override: mm
Export normalized DWG: optional
Allow overwrite: only when intentionally rerunning
```

The `mm` unit must be treated as an explicit operator or vendor policy. The CAD files audited so far commonly use `$INSUNITS = 0`, so the system does not assume millimetres automatically.

---

# Inputs

## Product JSON

The application supports multiple input formats through adapters.

Available adapters include:

* `BluestoneAdapter`
* `UnifiedAdapter`
* `LocalAdapter`

Each adapter converts its source format into the common internal `CadProcessingRequest`.

Example:

```python
from gebal_cad_normalizer.adapters import (
    BluestoneAdapter,
    LocalAdapter,
    UnifiedAdapter,
)

bluestone_result = BluestoneAdapter().parse(vendor_payload)

unified_result = UnifiedAdapter().parse(normalized_product_payload)

local_result = LocalAdapter().parse(
    {
        "sku": "137132M",
        "local_dwg_path": r"C:\path\to\137132M_source.dwg",
        "length_mm": 4140,
        "width_mm": 4680,
    }
)
```

Each adapter returns an `AdapterResult`.

The result can contain:

* a valid processing request;
* warnings;
* source-data issues;
* both a request and non-fatal issues.

---

## CAD Input

Supported source types:

* `.dwg`
* `.dxf`

The expected CAD asset is a top-view 2D product drawing.

The asset selector prefers evidence such as:

* `.dwg` extension;
* DWG MIME type;
* `top_view` filename wording;
* `2dModel` metadata;
* explicit asset purpose;
* vendor revision metadata.

It rejects or penalizes:

* side views;
* product-model drawings;
* PDFs;
* images;
* unrelated 3D assets;
* ambiguous candidates.

When selection is uncertain, the system reports ambiguity instead of guessing.

---

# Output Package

A successful or partially successful Stage 12 run produces an occurrence-scoped package:

```text
<output_root>/
└── <occurrence_id>/
    ├── manifest.json
    ├── report.md
    ├── normalized/
    │   ├── <sku>_normalized.dxf
    │   ├── <sku>_regions.dxf
    │   ├── optional <sku>_normalized.dwg
    │   └── optional converted source DXF
    ├── reports/
    │   ├── inventory.json
    │   ├── canonical.json
    │   ├── region_conversion.json
    │   ├── classification.json
    │   ├── rewrite.json
    │   ├── measurement.json
    │   └── validation.json
    └── svg/
        ├── combined.svg
        ├── manifest.json
        └── layers/
            ├── 001_PRODUCT.svg
            ├── 002_SAFETY_ZONE.svg
            └── ...
```

The exact files present depend on which stages completed.

---

## `manifest.json`

The manifest is the machine-readable audit record.

It includes:

* occurrence ID;
* SKU;
* source paths;
* source checksums;
* output artifact paths;
* output checksums;
* stage statuses;
* stage timings;
* warnings and failures;
* selected measurement candidates;
* validation status;
* filename and SKU mismatch evidence;
* unit source;
* operator overrides;
* partial-package information.

---

## `report.md`

The Markdown report is the human-readable processing summary.

It is intended for:

* operators;
* CAD engineers;
* developers;
* project managers;
* quality reviewers.

It summarizes:

* inputs;
* source checksums;
* conversion result;
* CAD risks;
* layer classifications;
* measurement candidates;
* validation result;
* package status;
* review actions.

---

# Status Meanings

The final package status can be:

## `pass`

The available CAD evidence agrees with the product data and no material uncertainty remains.

## `pass_with_warnings`

The result is acceptable, but non-blocking warnings remain.

Examples:

* explicit unit override;
* minor unsupported preview content;
* non-critical source-data warning.

## `review_required`

Useful output was generated, but a human must review uncertainty.

Examples:

* multiple plausible product footprints;
* ambiguous layer content;
* partial measurement;
* unknown or weakly inferred units;
* proxy or external-reference risks;
* uncertain containment.

## `fail`

The pipeline could not safely validate the input.

Examples:

* malformed JSON;
* clear JSON-versus-CAD mismatch;
* missing required candidate;
* processing safety cap exceeded;
* conversion failure;
* invalid input pairing;
* unsupported geometry prevents safe validation.

## `not_verifiable`

A specific field cannot be proven from top-view CAD.

Examples:

* height;
* weight;
* age range;
* material;
* product capacity;
* free-fall height.

---

# Architecture

## Repository Structure

```text
src/
└── gebal_cad_normalizer/
    ├── adapters/
    │   ├── base.py
    │   ├── bluestone.py
    │   ├── local.py
    │   └── unified.py
    ├── assets/
    │   ├── downloader.py
    │   └── version_manager.py
    ├── cad/
    │   ├── oda.py
    │   ├── inventory.py
    │   ├── canonicalize.py
    │   ├── region_convert.py
    │   ├── classify.py
    │   ├── rewrite.py
    │   ├── svg_export.py
    │   ├── measure.py
    │   └── validate.py
    ├── asset_selector.py
    ├── config.py
    ├── exceptions.py
    ├── fixture_loader.py
    ├── gui.py
    ├── models.py
    ├── pipeline.py
    ├── reporting.py
    └── __init__.py
```

---

# Input Models and Adapters

## `models.py`

This module contains the common typed models used throughout the pipeline.

These models represent:

* SKU;
* product dimensions;
* safety-zone dimensions;
* selected CAD asset;
* source metadata;
* processing request;
* warnings and issues.

The adapter layer prevents vendor-specific JSON structures from leaking into the CAD-processing modules.

---

## `adapters/base.py`

Defines the common adapter contract.

Conceptually:

```python
adapter.parse(payload) -> AdapterResult
```

Every adapter must return the same normalized result shape.

---

## `adapters/bluestone.py`

Handles Bluestone-style vendor JSON.

Responsibilities include:

* nested attribute extraction;
* CAD media discovery;
* source metadata preservation;
* download URL preservation;
* invalid source-data reporting;
* top-view asset selection.

---

## `adapters/unified.py`

Handles product JSON that is already close to the internal Gebal schema.

---

## `adapters/local.py`

Supports explicit local CAD paths for controlled runs and tests.

---

# Download and Version Management

## `assets/downloader.py`

Downloads selected CAD assets safely.

It:

* streams the download;
* writes to a temporary file;
* calculates SHA-256;
* enforces maximum file size;
* avoids promoting incomplete downloads;
* returns typed download results.

Example:

```python
from pathlib import Path

from gebal_cad_normalizer.assets import CadAssetDownloader

downloader = CadAssetDownloader(
    Path("warehouse/work"),
    max_file_size_bytes=50_000_000,
)

staged = downloader.stage(request.top_view_cad)
```

---

## `assets/version_manager.py`

Maintains source-file history.

Example managed layout:

```text
warehouse/
└── inventory/
    └── SKU_137132M/
        └── source/
            ├── current/
            ├── archive/
            └── source_manifest.json
```

The manager:

* creates the first current source;
* detects unchanged files by checksum;
* archives changed revisions;
* retains no more than the configured archive limit;
* preserves the previous valid current file if promotion fails.

Example:

```python
from pathlib import Path

from gebal_cad_normalizer.assets import SourceVersionManager

result = SourceVersionManager(
    Path("warehouse"),
    archive_limit=3,
).update_source(
    request.product.sku,
    staged,
)
```

Possible statuses:

* `created`
* `unchanged`
* `updated`

---

# ODA Conversion

## `cad/oda.py`

`ezdxf` cannot read DWG directly, so DWG files are converted through ODA File Converter.

The flow is:

```text
DWG
 ↓ ODA File Converter
DXF
 ↓ ezdxf processing
```

The wrapper:

* validates source and destination extensions;
* uses isolated temporary directories;
* invokes ODA with `shell=False`;
* captures exit code, stdout, stderr, and duration;
* enforces a timeout;
* checks output existence and size;
* atomically promotes successful output;
* terminates timed-out child processes.

Example:

```python
from pathlib import Path

from gebal_cad_normalizer.cad import (
    OdaConversionRequest,
    OdaConverter,
)

converter = OdaConverter(
    r"C:\Path\To\ODAFileConverter.exe"
)

result = converter.convert(
    OdaConversionRequest(
        source_path=Path(
            "warehouse/inventory/SKU_137132M/source/137132M_source_current.dwg"
        ),
        destination_path=Path(
            "warehouse/inventory/SKU_137132M/normalized/137132M_source_current.dxf"
        ),
        target_version="R2013",
        timeout_seconds=120,
    )
)
```

Supported conversion directions:

* DWG to DXF
* DXF to DWG

Version mapping includes:

```text
R2010 -> ACAD2010
R2013 -> ACAD2013
R2018 -> ACAD2018
```

ODA can be configured through:

* an explicit executable path;
* `CadNormalizerConfig.oda_executable_path`;
* the `GEBAL_ODA_FILE_CONVERTER` environment variable.

---

# DXF Inventory

## `cad/inventory.py`

The inventory stage performs a read-only audit.

It does not:

* save the DXF;
* explode blocks;
* flatten geometry;
* rewrite layers;
* change coordinates.

Example:

```python
from pathlib import Path

from gebal_cad_normalizer.cad import (
    inventory_dxf,
    write_inventory_json,
    write_inventory_markdown,
)

result = inventory_dxf(
    Path("source.dxf"),
    z_epsilon=1e-6,
)

write_inventory_json(
    result,
    Path("reports/inventory.json"),
)

write_inventory_markdown(
    result,
    Path("reports/inventory.md"),
)
```

The inventory records:

* DXF version;
* `$INSUNITS`;
* interpreted units;
* layouts;
* layers;
* blocks;
* INSERT counts;
* entity counts;
* entities by layer;
* nested block content;
* extents;
* XREF indicators;
* proxy content;
* raster references;
* unsupported entities;
* 3D indicators;
* non-zero Z evidence;
* unresolved block references.

Near-zero Z values use a configurable tolerance:

```text
z_epsilon = 1e-6
```

Tiny floating-point values below this threshold are treated as planar for audit decisions. Original Z values remain preserved in evidence.

---

# Geometry Canonicalization

## `cad/canonicalize.py`

The canonicalization stage converts supported DXF entities into a common internal geometry representation.

Supported source types include:

* LINE;
* ARC;
* CIRCLE;
* ELLIPSE;
* SPLINE;
* LWPOLYLINE;
* POLYLINE;
* INSERT-derived content.

The result records:

* canonical geometry type;
* original DXF type;
* source handle;
* source layer;
* coordinates;
* closure;
* block ancestry;
* INSERT ancestry;
* Z values;
* confidence;
* conversion issues.

Example:

```python
from pathlib import Path

from gebal_cad_normalizer.cad import (
    canonicalize_dxf,
    write_canonical_json,
)

result = canonicalize_dxf(
    Path("source.dxf"),
    z_epsilon=1e-6,
)

write_canonical_json(
    result,
    Path("reports/canonical.json"),
)
```

Curve tessellation is disabled unless explicitly requested.

Example:

```python
result = canonicalize_dxf(
    Path("source.dxf"),
    tessellate_curves=True,
    tessellation_tolerance=1.0,
)
```

Safety limits protect against runaway processing:

* maximum entity expansion;
* maximum INSERT recursion depth;
* maximum tessellation points.

Exceeding a limit produces an explicit issue rather than silent truncation or indefinite execution.

---

# REGION Handling

## `cad/region_convert.py`

DXF REGION entities may contain opaque ACIS data.

The system converts REGIONs only when deterministic boundary-loop evidence exists.

Example:

```python
from pathlib import Path

from gebal_cad_normalizer.cad import convert_regions

result = convert_regions(
    Path("source.dxf"),
    tolerance=1e-6,
)

written = convert_regions(
    Path("source.dxf"),
    output_path=Path("source_regions_converted.dxf"),
    tolerance=1e-6,
)
```

Supported behavior:

* deterministic boundary loops can become closed polylines;
* arc geometry is preserved through bulges when possible;
* approximated loops are marked as approximated;
* holes remain separate loops;
* opaque ACIS-only REGIONs remain unchanged;
* unsupported REGIONs are reported as failures or review evidence.

The source DXF is never modified.

---

# Layer Classification

## `cad/classify.py`

The classifier assigns advisory roles to source layers.

Roles include:

* `product_geometry`
* `safety_zone`
* `foundation_or_installation`
* `dimensions`
* `text_annotation`
* `hatch_or_fill`
* `construction_or_reference`
* `external_or_proxy`
* `non_operational`
* `ambiguous`
* `review_required`

Evidence can include:

* layer name;
* entity types;
* visibility;
* plot state;
* geometry closure;
* geometry area;
* block ancestry;
* proxy and XREF content;
* text and dimension content;
* non-planar evidence;
* conflicting content.

Example:

```python
from pathlib import Path

from gebal_cad_normalizer.cad import (
    LayerClassificationConfig,
    canonicalize_dxf,
    classify_layers,
    inventory_dxf,
    write_classification_json,
)

source = Path("source.dxf")

inventory = inventory_dxf(source)
canonical = canonicalize_dxf(source)

result = classify_layers(
    inventory,
    canonical,
)

write_classification_json(
    result,
    Path("reports/classification.json"),
)
```

---

## Bluestone Playground Profile

The optional Bluestone profile adds vendor-specific evidence for recurring layer names.

```python
profiled = classify_layers(
    inventory,
    canonical,
    LayerClassificationConfig(
        vendor_profile="bluestone_playground"
    ),
)
```

Common mappings include:

```text
Lg_prod                   -> product_geometry
Lg_area                   -> safety_zone
Lg_falling                -> safety_zone
LCPROD_FALLINGSPACE       -> safety_zone
LCPROD_ENSAFETYREGION     -> safety_zone
Lg_dim                    -> dimensions
DIMENSION                 -> dimensions
Lg_txt                    -> text_annotation
Lg_boundary               -> construction_or_reference
lc_ground                 -> foundation_or_installation
Defpoints                 -> non_operational
ASHADE                    -> hatch_or_fill candidate
```

Vendor aliases are not blindly trusted.

Contradictory geometry or mixed content can still result in:

```text
review_required
```

Unknown profile names raise a clear `ValueError`.

---

# Layer Rewriting

## `cad/rewrite.py`

The rewrite stage creates a new normalized DXF.

It never overwrites the source unless explicitly configured.

Default target layers:

```text
PRODUCT
SAFETY_ZONE
FOUNDATION
DIMENSIONS
TEXT
HATCH
REFERENCE
EXTERNAL
NON_OPERATIONAL
REVIEW_REQUIRED
```

Target names must not start with `GEBAL`.

Example:

```python
from pathlib import Path

from gebal_cad_normalizer.cad import (
    canonicalize_dxf,
    classify_layers,
    inventory_dxf,
    rewrite_layers,
)

source = Path("source.dxf")

classification = classify_layers(
    inventory_dxf(source),
    canonicalize_dxf(source),
)

result = rewrite_layers(
    source,
    classification,
    Path("source_normalized.dxf"),
)
```

The rewrite stage preserves:

* geometry;
* entity counts where possible;
* blocks;
* INSERT transforms;
* text;
* dimensions;
* hatches;
* source evidence;
* original extents.

Uncertain content is assigned to `REVIEW_REQUIRED` or preserved according to configuration.

---

# SVG Export

## `cad/svg_export.py`

The SVG stage creates visual review evidence without modifying CAD.

It can produce:

* one SVG per source or normalized layer;
* a combined SVG;
* a deterministic SVG manifest.

Example:

```python
from pathlib import Path

from gebal_cad_normalizer.cad import (
    SvgExportConfig,
    export_layer_svgs,
)

result = export_layer_svgs(
    Path("source.dxf"),
    Path("reports/svg"),
    config=SvgExportConfig(
        include_combined=True
    ),
)
```

Supported preview geometry includes:

* LINE;
* polylines;
* bulges;
* ARC;
* CIRCLE;
* ELLIPSE;
* SPLINE;
* HATCH boundaries;
* basic TEXT and MTEXT fallback;
* transformed INSERT content.

The SVG transform may fit geometry into a browser-friendly viewBox and invert the Y axis for display. These transforms affect only the preview.

Unsupported entities remain listed in the SVG manifest.

Manual usage:

```powershell
python tests\live\stage9_5_live_svg_export.py `
  --input path\to\source.dxf `
  --output-dir path\to\svg_review `
  --combined
```

For DWG:

```powershell
python tests\live\stage9_5_live_svg_export.py `
  --input path\to\source.dwg `
  --output-dir path\to\svg_review `
  --oda-exe C:\Path\To\ODAFileConverter.exe `
  --combined
```

---

# Geometry Measurement

## `cad/measure.py`

The measurement stage identifies possible product, safety-zone, foundation, and review-required geometry candidates.

It preserves alternatives instead of assuming the first detected shape is correct.

Each candidate can include:

* source layer;
* source role;
* source handles;
* block ancestry;
* bounding box;
* width;
* depth;
* area;
* perimeter;
* centroid;
* closure;
* confidence;
* unit state;
* warnings.

Example:

```python
from pathlib import Path

from gebal_cad_normalizer.cad import (
    MeasurementConfig,
    canonicalize_dxf,
    classify_layers,
    inventory_dxf,
    measure_geometry,
)

source = Path("source.dxf")

inventory = inventory_dxf(source)

canonical = canonicalize_dxf(
    source,
    tessellate_curves=True,
    tessellation_tolerance=1.0,
)

classification = classify_layers(
    inventory,
    canonical,
)

result = measure_geometry(
    inventory,
    canonical,
    classification,
    MeasurementConfig(
        expected_width_mm=4140,
        expected_depth_mm=4680,
    ),
)
```

Supported measurement sources include:

* closed polylines;
* circles;
* ellipses;
* splines;
* connected line and arc chains;
* supported HATCH boundaries;
* transformed INSERT content.

Repeated INSERTs are treated as separate placed instances.

Contained inner loops may be subtracted as holes when containment is deterministic.

---

## Units

Raw drawing-unit measurements are always preserved.

Supported explicit overrides:

* `mm`
* `cm`
* `m`
* `in`

Unit inference can use expected JSON width and depth, but only when configured.

The inference result records:

* selected unit;
* scale factor;
* residual error;
* confidence;
* alternatives.

Ambiguous inference remains unknown.

Manual usage:

```powershell
python tests\live\stage10_live_measure.py `
  --input path\to\source.dxf `
  --vendor-profile bluestone_playground
```

With explicit unit:

```powershell
python tests\live\stage10_live_measure.py `
  --input path\to\source.dxf `
  --unit mm `
  --json-output tests\output\stage10_measure.json
```

---

## Measurement Reliability Limits

The measurement engine has configurable safeguards for:

* curve tessellation points;
* self-intersection comparisons;
* chain combinations;
* candidate count;
* hole-containment comparisons;
* entity expansion;
* INSERT recursion depth.

Exceeding a cap produces explicit `measurement_failed` evidence.

The engine does not silently truncate geometry and does not continue indefinitely.

---

# JSON-versus-CAD Validation

## `cad/validate.py`

The validation stage compares product JSON against all plausible CAD measurement candidates.

It validates only values that top-view CAD can reasonably prove:

* product width;
* product depth;
* product area;
* safety-zone width;
* safety-zone depth;
* safety-zone area;
* product containment inside the safety zone.

Width and depth may be swapped because products can be rotated in the drawing.

Validation considers:

* dimensional residual;
* area residual;
* classification confidence;
* geometry closure;
* unit confidence;
* containment;
* ambiguity;
* proxy or XREF evidence;
* non-planar geometry;
* measurement warnings.

Fields such as height, weight, material, capacity, age range, or free-fall height are reported as not verifiable from top-view CAD.

Typical outcomes:

```text
Trusted candidate and matching dimensions
-> pass
```

```text
Matching dimensions with explicit unit override
-> pass_with_warnings
```

```text
Multiple plausible candidates
-> review_required
```

```text
Clear CAD-versus-JSON mismatch
-> fail
```

Manual usage:

```powershell
python tests\live\stage11_live_validate.py `
  --input path\to\source.dxf `
  --json path\to\product.json `
  --vendor-profile bluestone_playground `
  --unit mm
```

---

# Integrated Reporting Pipeline

## `reporting.py`

The reporting pipeline orchestrates the complete process.

Conceptually:

```python
run_reporting_pipeline(...)
```

runs:

```text
Input preparation
Inventory
Canonicalization
REGION handling
Classification
Layer rewriting
SVG export
Measurement
Validation
Optional DWG export
Manifest creation
Markdown report creation
```

The reporting layer reuses existing stage functions. It does not duplicate CAD logic.

Key reliability features:

* per-stage start and end logging;
* stage timings;
* configurable timeouts;
* child-process isolation for expensive stages;
* child termination on timeout;
* partial package preservation;
* atomic output promotion;
* no silent overwrite;
* source checksum preservation.

Manual usage:

```powershell
python tests\live\stage12_live_pipeline.py `
  --json path\to\product.json `
  --input path\to\top_view.dwg `
  --output-dir tests\output\stage12_live `
  --oda-exe E:\ODA\ODAFileConverter.exe `
  --vendor-profile bluestone_playground `
  --unit mm `
  --export-dwg
```

Use `--allow-overwrite` only when intentionally replacing an existing occurrence package.

---

# Desktop Operator GUI

## `gui.py`

The desktop GUI is a thin interface over the Stage 12 reporting pipeline.

It contains no CAD-processing logic.

Features include:

* path selection;
* required-field validation;
* vendor profile selection;
* unit override;
* optional DWG export;
* overwrite control;
* worker-thread execution;
* current-stage display;
* progress indication;
* scrollable log;
* final status;
* output path;
* open-report action;
* open-normalized-DXF action;
* open-combined-SVG action;
* recent path persistence.

Launch:

```powershell
python tests\live\stage12_5_run_gui.py
```

Safe cancellation is currently disabled because the Stage 12 API does not yet expose user-directed cancellation as a stable public feature.

---

# Batch Qualification

## Stage 13

The Stage 13 batch runner processes a directory of product occurrences through the complete Stage 12 pipeline.

It:

* scans product folders;
* pairs JSON with top-view CAD;
* preserves repeated SKUs as separate occurrences;
* detects filename and SKU mismatches;
* continues after individual failures;
* records source checksums;
* generates one package per valid occurrence;
* summarizes status distribution;
* groups failure causes;
* reports runtime statistics.

Manual usage:

```powershell
python tests\live\stage13_batch_qualification.py `
  --source-root "C:\Path\To\Test_Examples" `
  --output-root tests\output\stage13_qualification `
  --oda-exe E:\ODA\ODAFileConverter.exe `
  --occurrence-timeout 60
```

With an explicit unit policy:

```powershell
python tests\live\stage13_batch_qualification.py `
  --source-root "C:\Path\To\Test_Examples" `
  --output-root tests\output\stage13_qualification_mm `
  --oda-exe E:\ODA\ODAFileConverter.exe `
  --unit mm `
  --occurrence-timeout 60
```

Batch outputs:

```text
qualification_matrix.csv
qualification_results.json
qualification_summary.md
packages/
```

---

# Fixture Loading

## `fixture_loader.py`

Controlled test fixtures can optionally contain `//` comments.

Example:

```python
from gebal_cad_normalizer.fixture_loader import load_json_fixture

loaded = load_json_fixture(
    "tests/fixtures/commented_fixture.json",
    allow_comments=True,
)

assert loaded.tolerant_parsing_used is True
```

The loader always attempts strict JSON first.

Comment-tolerant parsing should be used only for controlled fixtures, not normal production inputs.

---

# Testing

Run the full suite:

```powershell
python -m pytest
```

Run a specific module:

```powershell
python -m pytest tests\test_measure.py
```

Run GUI tests:

```powershell
python -m pytest tests\test_gui.py
```

Compile key modules:

```powershell
python -m py_compile `
  src\gebal_cad_normalizer\reporting.py `
  src\gebal_cad_normalizer\gui.py `
  src\gebal_cad_normalizer\cad\measure.py `
  tests\live\stage12_live_pipeline.py `
  tests\live\stage13_batch_qualification.py
```

Basic import check:

```powershell
python -c "from gebal_cad_normalizer.reporting import run_reporting_pipeline; from gebal_cad_normalizer.cad import inventory_dxf, canonicalize_dxf, classify_layers, rewrite_layers, export_layer_svgs, measure_geometry; print('import ok')"
```

---

# Live Scripts

Manual and diagnostic scripts are located under:

```text
tests/live/
```

Important scripts include:

```text
stage3_live_137132m.py
stage4_live_oda.py
stage5_live_inventory.py
stage6_live_canonicalize.py
stage7_live_region_convert.py
stage7_live_region_verification.py
stage8_live_classify.py
stage9_live_rewrite.py
stage9_5_live_svg_export.py
stage10_live_measure.py
stage11_live_validate.py
stage12_live_pipeline.py
stage12_5_run_gui.py
stage13_batch_qualification.py
batch_cad_variation_audit.py
```

These scripts are not intended to run automatically as part of normal pytest execution.

---

# External Tools

## ODA File Converter

Required for:

* DWG-to-DXF conversion;
* DXF-to-DWG export.

It is not required for:

* DXF-only testing;
* mocked unit tests;
* DXF inventory;
* DXF canonicalization.

---

## ezdxf

Used for:

* reading DXF;
* inventory;
* geometry extraction;
* layer rewriting;
* normalized DXF output.

---

## CAD Viewer

A manual CAD viewer is recommended for reviewing generated DXF and DWG files.

Examples:

* nanoCAD;
* AutoCAD;
* BricsCAD;
* DraftSight;
* compatible third-party CAD viewers.

The viewer is not a Python dependency.

---

# Safety and Design Guarantees

The project follows these rules:

1. Source CAD files remain read-only.
2. Output is written to new files.
3. Existing output is not silently overwritten.
4. Source and output checksums are recorded.
5. Units are not silently assumed.
6. Opaque ACIS REGION geometry is not guessed.
7. Unsupported geometry is preserved where possible.
8. Ambiguous classifications are sent to review.
9. Measurement alternatives are retained.
10. Processing caps report explicit failures.
11. Expensive stages are timeout-protected.
12. No AI determines geometry or validation results.

---

# Known Limitations

## Unknown CAD Units

Many audited vendor drawings have:

```text
$INSUNITS = 0
```

An explicit unit override or confirmed vendor policy is therefore required for reliable dimensional validation.

---

## Opaque REGION Data

Some REGION entities contain binary ACIS data without accessible boundary loops.

These REGIONs remain unchanged and are reported for review.

---

## Mixed Layers

Some source layers contain multiple content types, such as:

```text
product geometry + dimensions + text + hatch
```

These layers may require review rather than automatic normalization.

---

## Complex Geometry

Large circles, arcs, splines, nested blocks, and dense candidate sets may exceed configured reliability limits.

The system reports these cases instead of hanging.

---

## Source-Data Problems

The pipeline can identify but cannot automatically repair:

* malformed product JSON;
* invalid dimensions;
* missing safety data;
* mismatched JSON and CAD;
* incorrect CAD filenames;
* repeated SKUs;
* missing top-view assets.

---

## Production Readiness

The pipeline is currently suitable for:

* controlled operator use;
* engineering review;
* batch qualification;
* source-data diagnostics;
* normalized CAD generation with review.

It is not yet approved for fully unattended production processing across arbitrary vendor CAD.

---

# Production Documentation

Detailed implementation and stage records are available under:

```text
production_docs/
```

Important documents include:

```text
stage_0_scope_lock_and_repo_initial.md
stage_1_input_contracts.md
stage_2_asset_selection.md
stage_3_download_versioning.md
stage_4_oda_conversion.md
stage_5_dxf_inventory.md
stage_6_geometry_canonicalization.md
stage_7_region_conversion.md
stage_8_layer_classification.md
stage_9_layer_rewriting.md
stage_9_5_layer_svg_export.md
stage_10_measurement_engine.md
stage_11_json_cad_validation.md
stage_12_reporting_integration.md
stage_12_5_operator_gui.md
stage_13_end_to_end_qualification.md
```

Use the README for orientation and operation.

Use the production documents for stage-specific implementation details, audit findings, limits, and acceptance criteria.

```
```
