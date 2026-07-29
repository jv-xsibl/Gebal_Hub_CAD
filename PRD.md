# Product Requirements Document

## Project: Gebal CAD Normalizer and Validator

**Document Version:** 1.0
**Status:** Draft for Engineering Review
**Target Runtime:** Python 3.10+
**Primary Integration:** Gebal Hub and downstream CAD compiler
**Primary CAD Tools:** ODA File Converter, ezdxf
**Reference CAD Viewer:** nanoCAD

---

## 1. Executive Summary

The Gebal CAD Normalizer and Validator is a standalone Python module that downloads vendor-provided DWG files referenced inside Product Information Management JSON payloads, maintains controlled local file versions, converts and cleans CAD geometry, standardizes drawing layers, converts unsupported REGION entities into usable closed polylines, and validates CAD measurements against vendor product data.

The module will be designed as an independent integration component. The main Gebal Hub application or another engineering system will call the module through a stable Python interface and receive normalized CAD files, versioning results, and a structured discrepancy report.

The first version will prioritize deterministic Python-based processing. AI may later be introduced only as an optional aid for ambiguous layer classification. AI will not control geometry transformation, measurement, versioning, or validation decisions.

---

## 2. Problem Statement

Vendor DWG files are not consistently structured for direct use by Gebal Hub or the downstream CAD compiler.

Common issues include:

* inconsistent or meaningless layer names;
* important geometry distributed across multiple layers;
* unsupported REGION entities;
* annotations, dimensions, title blocks, hatches, and reference elements mixed with operational geometry;
* uncertainty over which geometry represents the product footprint or safety zone;
* dimensional conflicts between vendor JSON data and the attached DWG;
* vendor DWG files changing over time without controlled local version tracking;
* multiple DWG assets in the same PIM payload, including top view, side view, and 3D model files.

The sample vendor payload includes separate top-view, side-view, and product-model DWG assets. The top-view CAD must therefore be selected through asset metadata rather than merely by file extension.

---

## 3. Product Goal

Create a reusable Python module that transforms a vendor top-view DWG into a standardized, machine-readable CAD asset suitable for Gebal Hub and the future CAD compiler.

The module must:

1. identify the correct vendor top-view DWG from the input JSON;
2. download and store it locally;
3. determine whether the vendor source file has changed;
4. retain one current source file and no more than three previous versions;
5. convert DWG to DXF using ODA File Converter;
6. inspect all layers, blocks, and entities;
7. convert REGION geometry into valid closed polylines;
8. classify and move relevant geometry into standardized layers;
9. preserve uncertain geometry rather than silently deleting it;
10. calculate product and safety-zone measurements;
11. compare CAD-derived measurements with vendor JSON values;
12. generate normalized DXF and DWG outputs;
13. return a structured validation and processing report.

---

## 4. Scope

### 4.1 In Scope

* Vendor JSON parsing.
* Top-view DWG asset identification.
* Remote DWG downloading.
* Local source-file storage.
* SHA-256 file comparison.
* Source-file version archiving.
* Maximum retention of three archived source versions.
* DWG-to-DXF conversion using ODA File Converter.
* DXF inspection using ezdxf.
* Layer and entity inventory.
* Block and INSERT inspection.
* REGION-to-polyline conversion.
* Standardized layer creation.
* Geometry reassignment to normalized layers.
* Controlled removal or quarantine of non-operational entities.
* Product footprint measurement.
* Safety-zone measurement.
* Area calculation where suitable closed geometry exists.
* Vendor JSON versus CAD discrepancy validation.
* Machine-readable JSON reporting.
* Human-readable summary reporting.
* Normalized DXF output.
* Normalized DWG output through ODA reconversion.
* Stable Python API for integration.

### 4.2 Out of Scope

* Full Gebal Hub desktop interface.
* Product schema transformation outside CAD-related fields.
* Live webhook monitoring.
* CAD nesting.
* Spatial placement.
* Automatic repair of incorrect product geometry.
* Reconstruction of missing CAD geometry.
* 3D product-model normalization in v1.
* Side-view DWG normalization in v1.
* Direct binary DWG editing without conversion.
* Manufacturer communication or automated email generation.
* AI-controlled geometry modification.

---

## 5. Primary Users

### 5.1 Main Software Engineer

Integrates the module into Gebal Hub or another product-data pipeline.

### 5.2 CAD Engineer or Reviewer

Uses nanoCAD or another CAD viewer to inspect the normalized output and verify flagged discrepancies.

### 5.3 Data Quality Reviewer

Reviews the generated report to determine whether the vendor JSON and CAD drawing agree.

---

## 6. Input Requirements

### 6.1 Vendor JSON

The module will initially accept the raw vendor PIM JSON payload.

The vendor adapter must extract:

* SKU or product number;
* product name;
* vendor source identifier where available;
* top-view DWG URL;
* top-view DWG filename;
* media ID;
* vendor asset revision;
* media creation or update timestamp;
* product length;
* product width;
* product height;
* safety-zone length;
* safety-zone width;
* safety or falling-space area where available;
* impact area where available;
* free-fall height where available.

For product `137132M`, the sample vendor payload provides:

* product length: 4140 mm;
* product width: 4680 mm;
* product height: 3820 mm;
* safety-zone length: 7700 mm;
* safety-zone width: 8350 mm;
* falling-space area: 49.1 m²;
* impact area: 47.5 m²;
* free-fall height: 1970 mm.

### 6.2 Unified Schema

The module must also support normalized product data passed by the main application.

Relevant unified fields include:

```text
sku
technical.dimensions.length_mm
technical.dimensions.width_mm
technical.dimensions.height_mm
safety.safety_zone.length_mm
safety.safety_zone.width_mm
safety.cfh_mm
media.top_view_cad_file.file_name
media.top_view_cad_file.file_type
media.top_view_cad_file.url
```

The unified product schema treats the top-view CAD file and base-product CAD file as separate required assets.

### 6.3 CAD Input

The primary v1 CAD input is the vendor top-view DWG.

The module must not select:

* side-view drawings;
* 3D product models;
* preview images;
* product PDFs;
* installation documents.

Asset selection should use metadata such as:

* asset name;
* description;
* content type;
* document information;
* purpose;
* filename;
* vendor asset classification.

---

## 7. Proposed System Architecture

```text
Vendor JSON
    │
    ▼
Vendor Adapter
    │
    ▼
CAD Asset Selector
    │
    ▼
Asset Downloader
    │
    ▼
Version Manager
    │
    ▼
ODA Converter: DWG → DXF
    │
    ▼
DXF Inspector
    │
    ▼
REGION Converter
    │
    ▼
Layer Classifier and Normalizer
    │
    ▼
Geometry Measurement Engine
    │
    ▼
JSON-to-CAD Validator
    │
    ▼
Normalized DXF
    │
    ▼
ODA Converter: DXF → DWG
    │
    ▼
Processing and Discrepancy Reports
```

---

## 8. Recommended Package Structure

```text
cad_normalizer/
├── __init__.py
├── pipeline.py
├── models.py
├── config.py
├── exceptions.py
│
├── adapters/
│   ├── __init__.py
│   ├── base_vendor_adapter.py
│   ├── bluestone_adapter.py
│   └── unified_schema_adapter.py
│
├── assets/
│   ├── downloader.py
│   ├── asset_selector.py
│   └── version_manager.py
│
├── conversion/
│   └── oda_converter.py
│
├── cad/
│   ├── dxf_inspector.py
│   ├── region_converter.py
│   ├── layer_classifier.py
│   ├── layer_normalizer.py
│   ├── geometry_measurement.py
│   └── dxf_writer.py
│
├── validation/
│   ├── dimension_validator.py
│   ├── area_validator.py
│   └── report_builder.py
│
└── tests/
    ├── fixtures/
    ├── test_asset_selection.py
    ├── test_version_manager.py
    ├── test_region_converter.py
    ├── test_layer_normalizer.py
    └── test_validation.py
```

---

## 9. Public Integration Interface

The main application should be able to call the module through one public entry point.

```python
from cad_normalizer import process_vendor_cad

result = process_vendor_cad(
    vendor_json=vendor_payload,
    warehouse_root="gebal_hub_warehouse",
    check_for_update=True,
    force_reprocess=False,
)
```

### 9.1 Proposed Return Object

```python
{
    "success": True,
    "sku": "137132M",
    "source_status": "updated",
    "source_dwg_path": "...",
    "normalized_dxf_path": "...",
    "normalized_dwg_path": "...",
    "report_path": "...",
    "overall_validation_status": "warning",
    "warnings": [],
    "errors": []
}
```

The production implementation should use typed dataclasses or Pydantic models rather than an unstructured dictionary.

---

## 10. Local Storage Structure

```text
warehouse/
└── inventory/
    └── SKU_137132M/
        ├── source/
        │   ├── 137132M_source_current.dwg
        │   └── source_manifest.json
        │
        ├── normalized/
        │   ├── 137132M_normalized.dxf
        │   └── 137132M_normalized.dwg
        │
        ├── reports/
        │   ├── 137132M_cad_validation.json
        │   └── 137132M_cad_validation.md
        │
        ├── archive/
        │   ├── 20260720_143000_source.dwg
        │   ├── 20260615_091500_source.dwg
        │   └── 20260501_173000_source.dwg
        │
        └── work/
            └── temporary processing files
```

Temporary processing files should be deleted after successful completion unless debug retention is enabled.

---

## 11. Source Download and Versioning Requirements

### 11.1 Update Check

When `check_for_update=True`, the module must:

1. parse the vendor JSON;
2. locate the top-view DWG URL;
3. download the file into a temporary location;
4. calculate its SHA-256 checksum;
5. compare it with the checksum stored for the current source DWG.

### 11.2 No-Change Behaviour

When the downloaded file matches the current source checksum:

* delete the newly downloaded temporary file;
* do not create an archive;
* do not replace the current source;
* do not overwrite normalized outputs unless `force_reprocess=True`;
* return `source_status="unchanged"`.

### 11.3 Changed-File Behaviour

When the downloaded file differs:

1. rename and move the current source DWG into the archive;
2. promote the new DWG to the current source location;
3. update the source manifest;
4. retain only the newest three archived source files;
5. delete older archive files beyond the retention limit;
6. run the complete normalization and validation pipeline;
7. return `source_status="updated"`.

### 11.4 First-Time Download

When no current source exists:

* save the downloaded file as the current source;
* create the manifest;
* do not create an archive;
* run normalization and validation;
* return `source_status="created"`.

### 11.5 Source Manifest

```json
{
  "sku": "137132M",
  "current_file": "137132M_source_current.dwg",
  "sha256": "...",
  "media_id": "...",
  "vendor_revision": "4.1",
  "vendor_updated_at": 1776837754032,
  "downloaded_at": "2026-07-21T12:00:00+04:00",
  "source_url": "..."
}
```

Vendor metadata may be used as an early signal, but checksum comparison must be the final authority.

---

## 12. ODA Conversion Requirements

### 12.1 DWG-to-DXF

The module must invoke ODA File Converter through a safe subprocess wrapper.

Requirements:

* configurable executable path;
* validated input and output directories;
* no unsanitized shell string execution;
* captured standard output and error;
* configurable timeout;
* verification that expected output exists;
* meaningful conversion exceptions;
* support for a defined DXF version compatible with ezdxf and nanoCAD.

### 12.2 DXF-to-DWG

After normalization, the module must convert the normalized DXF back to DWG.

Both outputs should be retained:

* DXF for Gebal Hub and downstream programmatic processing;
* DWG for CAD review and interoperability.

---

## 13. DXF Inspection Requirements

The inspection stage must collect:

* drawing units;
* DXF version;
* model-space entity count;
* paper-space layouts;
* layer names;
* layer visibility;
* layer colours;
* layer line types;
* entity types per layer;
* block definitions;
* INSERT references;
* nested block usage;
* bounding boxes;
* REGION count;
* HATCH count;
* unsupported or proxy entity count;
* closed-polyline count;
* open-polyline count;
* text and dimension entity count.

The inspection result must be retained inside the final report.

---

## 14. REGION-to-Polyline Conversion

### 14.1 Requirement

All operational REGION entities must be converted into geometry that Gebal Hub and the downstream CAD compiler can recognize.

The preferred output is:

```text
LWPOLYLINE
```

A standard POLYLINE may be used where LWPOLYLINE cannot represent the source geometry safely.

### 14.2 Conversion Behaviour

For every REGION:

1. extract boundary loops;
2. identify outer boundaries;
3. identify inner holes;
4. convert lines and arcs into polyline segments;
5. preserve arcs using bulge values where reliable;
6. approximate unsupported curves using controlled tessellation;
7. create closed polylines;
8. assign the resulting geometry to the appropriate normalized layer;
9. compare converted area against source-region area;
10. record conversion results.

### 14.3 Validation Rules

A successful converted polyline must:

* be closed;
* contain sufficient vertices;
* preserve drawing coordinates;
* preserve drawing units;
* remain planar;
* avoid invalid zero-length segments;
* avoid duplicate consecutive vertices;
* avoid unintended self-intersections;
* remain within the configured area tolerance.

### 14.4 Failure Behaviour

When a REGION cannot be converted reliably:

* retain or copy it into a quarantine output where technically possible;
* assign related output to `PM_UNCLASSIFIED`;
* record the reason;
* mark the normalization result as warning or failure depending on severity;
* never silently delete it.

---

## 15. Standard Layer Model

Initial normalized layers:

```text
PM_PRODUCT_FOOTPRINT
PM_SAFETY_ZONE
PM_CLEARANCE_LIMITS
PM_FOUNDATION
PM_REFERENCE
PM_UNCLASSIFIED
```

The final list will remain configurable until more vendor samples are evaluated.

### 15.1 Layer Intent

**PM_PRODUCT_FOOTPRINT**
Closed geometry representing the physical top-view extent of the equipment.

**PM_SAFETY_ZONE**
Closed geometry representing the required safety or falling-space perimeter.

**PM_CLEARANCE_LIMITS**
Additional clearance, access, circulation, or operational boundaries.

**PM_FOUNDATION**
Foundation, footing, anchor, or mounting geometry needed downstream.

**PM_REFERENCE**
Useful but non-operational reference geometry.

**PM_UNCLASSIFIED**
Geometry that cannot be classified with sufficient confidence.

---

## 16. Layer Classification Strategy

Classification must be deterministic and configurable.

Signals may include:

* source layer name;
* layer description;
* entity types;
* colour;
* line type;
* closed versus open geometry;
* relative geometry size;
* enclosed area;
* geometry position;
* bounding-box relationship;
* keywords such as:

  * safety;
  * fall;
  * use zone;
  * impact;
  * equipment;
  * product;
  * footprint;
  * foundation;
  * clearance;
  * base.

### 16.1 Classification Confidence

Each mapping should receive:

* classification;
* confidence score;
* reasons;
* source layer;
* target layer.

Example:

```json
{
  "source_layer": "FALL_AREA",
  "target_layer": "PM_SAFETY_ZONE",
  "confidence": 0.96,
  "reasons": [
    "layer name contains FALL",
    "largest closed perimeter",
    "dimensions closely match JSON safety zone"
  ]
}
```

### 16.2 Ambiguous Geometry

Ambiguous geometry must be retained under `PM_UNCLASSIFIED`.

Optional AI-assisted classification may be added later, but AI recommendations must:

* remain advisory;
* include confidence;
* never directly modify geometry without deterministic validation;
* be disabled by default.

---

## 17. Entity Handling Policy

### 17.1 Preferred Operational Entities

* LWPOLYLINE;
* POLYLINE;
* LINE;
* ARC;
* CIRCLE;
* INSERT where supported and resolved predictably.

### 17.2 Convert Where Possible

* REGION;
* SPLINE;
* ELLIPSE;
* supported closed HATCH boundaries;
* nested block geometry where required.

### 17.3 Quarantine or Remove from Operational Layers

* TEXT;
* MTEXT;
* DIMENSION;
* LEADER;
* MLEADER;
* title-block geometry;
* decorative HATCH;
* logos;
* revision tables;
* paper-space annotations;
* unsupported proxy objects.

Removal should occur only from the normalized operational output. The original vendor DWG must always remain available as the current or archived source.

---

## 18. Geometry Measurement Requirements

### 18.1 Product Footprint

The module must calculate:

* minimum X;
* maximum X;
* minimum Y;
* maximum Y;
* width in drawing units;
* height in drawing units;
* converted width in millimetres;
* converted length in millimetres;
* enclosed area where available;
* centroid where available.

### 18.2 Safety Zone

The module must calculate:

* safety-zone bounding dimensions;
* enclosed area;
* centroid;
* relationship to product footprint;
* whether the safety zone fully encloses the product footprint.

### 18.3 Unit Handling

The module must detect or infer drawing units.

Supported initial unit cases:

* millimetres;
* metres;
* centimetres;
* unitless drawings with configurable inference.

Any unit inference must be reported explicitly.

---

## 19. JSON-to-CAD Validation

### 19.1 Product Dimension Checks

Compare CAD product-footprint dimensions against:

```text
technical.dimensions.length_mm
technical.dimensions.width_mm
```

The comparison must account for 90-degree drawing rotation by comparing sorted dimension pairs.

Example:

```python
json_dimensions = sorted([length_mm, width_mm])
cad_dimensions = sorted([cad_x_mm, cad_y_mm])
```

### 19.2 Safety-Zone Checks

Compare CAD safety-zone dimensions against:

```text
safety.safety_zone.length_mm
safety.safety_zone.width_mm
```

### 19.3 Area Checks

Where reliable closed geometry exists, compare CAD area against available vendor fields such as:

* falling-space area;
* impact area;
* safety-zone area.

Area comparisons must only be made when the semantic meaning of the CAD geometry and JSON field is sufficiently clear.

### 19.4 Height and Free-Fall Height

The top-view drawing usually cannot validate:

```text
technical.dimensions.height_mm
safety.cfh_mm
```

These fields must be marked:

```text
unverifiable_from_top_view
```

They must not be marked as passed or failed without suitable CAD evidence.

### 19.5 Missing Geometry

If the JSON contains safety dimensions but no safety-zone geometry can be identified:

* mark the check as failed or unresolved;
* record the missing CAD evidence;
* do not generate replacement geometry automatically.

---

## 20. Validation Status Model

### 20.1 Pass

* geometry identified;
* comparison completed;
* result within accepted tolerance.

### 20.2 Warning

* comparison slightly outside preferred tolerance;
* units inferred;
* ambiguous layer mapping;
* partial REGION conversion;
* unclassified geometry remains;
* area comparison unavailable;
* optional information cannot be verified.

### 20.3 Fail

* required top-view DWG missing;
* ODA conversion failed;
* source file corrupt;
* product footprint cannot be identified;
* safety-zone geometry required but absent;
* dimensional discrepancy exceeds fatal tolerance;
* REGION conversion destroys or materially changes required geometry;
* normalized file cannot be written.

### 20.4 Unverifiable

* field cannot be validated using the available top-view CAD;
* relevant geometry or metadata is not present;
* semantic mapping remains uncertain.

---

## 21. Tolerance Configuration

Initial tolerances must remain configurable pending multi-vendor testing.

Proposed starting values:

```text
Preferred dimensional tolerance: ±0.5%
Warning dimensional tolerance:   >0.5% to ±1.0%
Failure dimensional tolerance:   >1.0%

Preferred area tolerance:        ±1.0%
Warning area tolerance:          >1.0% to ±2.0%
Failure area tolerance:          >2.0%
```

The module must also support an absolute millimetre tolerance to prevent very small numerical differences from producing false warnings.

Example:

```text
absolute dimensional tolerance: 5 mm
```

A comparison should pass when either the percentage tolerance or permitted absolute tolerance is satisfied, according to the final configured rule.

---

## 22. Processing Report

### 22.1 JSON Report

```json
{
  "sku": "137132M",
  "source": {
    "status": "updated",
    "media_id": "...",
    "revision": "4.1",
    "sha256": "...",
    "source_path": "..."
  },
  "conversion": {
    "dwg_to_dxf": "success",
    "dxf_to_dwg": "success"
  },
  "inspection": {
    "drawing_units": "mm",
    "layers_found": 12,
    "regions_found": 4,
    "blocks_found": 6,
    "unsupported_entities": 0
  },
  "region_conversion": {
    "converted": 4,
    "failed": 0
  },
  "layer_mapping": [],
  "measurements": {
    "product": {},
    "safety_zone": {}
  },
  "checks": [],
  "unverifiable_fields": [
    "technical.dimensions.height_mm",
    "safety.cfh_mm"
  ],
  "unclassified_layers": [],
  "overall_status": "warning"
}
```

### 22.2 Human-Readable Report

A Markdown summary should include:

* product identity;
* source revision;
* source update status;
* layer inventory;
* entity summary;
* REGION conversion summary;
* layer mappings;
* measured dimensions;
* JSON dimensions;
* differences;
* pass, warning, fail, and unverifiable checks;
* retained unclassified geometry;
* processing errors;
* output locations.

---

## 23. Logging and Error Handling

The module must use structured logging.

Required events include:

* JSON parsed;
* top-view asset selected;
* download started;
* download completed;
* checksum calculated;
* source unchanged;
* source archived;
* archive trimmed;
* ODA conversion started;
* ODA conversion completed;
* DXF inspection completed;
* REGION conversion completed;
* layer normalization completed;
* validation completed;
* output generated;
* temporary files deleted.

Errors must use specific exception classes, such as:

```text
VendorPayloadError
CadAssetNotFoundError
DownloadError
VersioningError
OdaConversionError
DxfReadError
RegionConversionError
LayerNormalizationError
CadValidationError
OutputWriteError
```

---

## 24. Security and File Safety

* Validate all local paths.
* Restrict writes to the configured warehouse root.
* Sanitize filenames.
* Do not pass user-controlled values into shell commands.
* Use subprocess argument arrays rather than shell strings.
* Apply network timeouts.
* Limit download size.
* Verify downloaded content is a CAD file.
* Avoid overwriting the source before successful download and checksum calculation.
* Use atomic file moves where possible.
* Preserve the current source if processing fails.
* Never delete archived files before confirming archive ordering and retention logic.

---

## 25. Configuration

```python
CadNormalizerConfig(
    oda_executable_path="...",
    output_dxf_version="R2013",
    archive_limit=3,
    dimensional_tolerance_percent=1.0,
    dimensional_tolerance_mm=5.0,
    area_tolerance_percent=2.0,
    spline_tessellation_tolerance_mm=2.0,
    retain_normalized_dxf=True,
    retain_normalized_dwg=True,
    retain_work_files=False,
    allow_ai_layer_assistance=False,
)
```

---

## 26. Functional Requirements

### FR-01

The module shall accept a raw vendor JSON payload or normalized input model.

### FR-02

The module shall identify the top-view DWG using vendor asset metadata.

### FR-03

The module shall download the selected DWG.

### FR-04

The module shall compare the downloaded file with the current source using SHA-256.

### FR-05

The module shall delete the new download when the source is unchanged.

### FR-06

The module shall archive the previous source when a changed source is received.

### FR-07

The module shall retain no more than three previous source versions.

### FR-08

The module shall preserve one current source version.

### FR-09

The module shall convert source DWG to DXF through ODA File Converter.

### FR-10

The module shall inspect all DXF layers and entity types.

### FR-11

The module shall convert operational REGION entities into closed polylines.

### FR-12

The module shall validate converted REGION geometry.

### FR-13

The module shall create standardized PM-prefixed layers.

### FR-14

The module shall retain ambiguous geometry under `PM_UNCLASSIFIED`.

### FR-15

The module shall derive product-footprint dimensions.

### FR-16

The module shall derive safety-zone dimensions where suitable geometry exists.

### FR-17

The module shall compare CAD-derived dimensions with vendor JSON values.

### FR-18

The module shall distinguish pass, warning, fail, and unverifiable results.

### FR-19

The module shall produce a normalized DXF.

### FR-20

The module shall produce a normalized DWG.

### FR-21

The module shall generate JSON and Markdown reports.

### FR-22

The module shall expose a stable integration API.

---

## 27. Non-Functional Requirements

### NFR-01: Determinism

The same source DWG, input data, and configuration must produce the same normalized result.

### NFR-02: Modularity

Vendor parsing, downloading, versioning, conversion, geometry processing, and validation must remain separate.

### NFR-03: Traceability

Every geometry classification and validation result must be explainable through the report.

### NFR-04: Data Preservation

Original vendor source files must not be modified directly.

### NFR-05: Compatibility

Outputs must open correctly in nanoCAD and remain readable by ezdxf.

### NFR-06: Integration

The module must not depend on a GUI.

### NFR-07: Testability

Core geometry, versioning, and validation functions must be independently testable.

### NFR-08: Extensibility

New vendor adapters and layer rules must be addable without changing the central pipeline.

---

## 28. Acceptance Criteria for v1

The v1 module will be accepted when it can process the supplied `137132M` sample and:

1. identify the correct top-view DWG;
2. download or ingest the source successfully;
3. create a current source file;
4. detect an unchanged source using checksum comparison;
5. delete an unchanged duplicate download;
6. archive a changed current file;
7. enforce the three-version archive limit;
8. convert the DWG into a readable DXF;
9. enumerate all source layers and entities;
10. detect all REGION entities;
11. convert eligible REGION entities into valid closed polylines;
12. preserve failed or ambiguous geometry;
13. create standardized PM layers;
14. identify or report inability to identify the product footprint;
15. identify or report inability to identify the safety zone;
16. compare CAD dimensions against vendor values;
17. mark height and free-fall height as unverifiable where appropriate;
18. write normalized DXF and DWG outputs;
19. open the normalized DWG successfully in nanoCAD;
20. produce complete JSON and Markdown reports;
21. complete without modifying the original source DWG.

---

## 29. Testing Strategy

### 29.1 Unit Tests

* vendor attribute extraction;
* DWG asset selection;
* checksum comparison;
* archive rotation;
* unit conversion;
* dimension-pair comparison;
* tolerance calculation;
* layer-name rule matching;
* report generation.

### 29.2 Geometry Tests

* simple REGION conversion;
* REGION with arcs;
* REGION with holes;
* multiple disjoint REGION entities;
* self-intersecting output detection;
* area-preservation comparison;
* open-polyline detection;
* rotated footprint measurement;
* metre-to-millimetre conversion.

### 29.3 Integration Tests

* raw JSON to downloaded DWG;
* DWG to DXF through ODA;
* DXF normalization;
* DXF to DWG through ODA;
* final output opening in nanoCAD.

### 29.4 Data Quality Evaluation

Additional vendor samples will be used to evaluate:

* layer-name variability;
* entity-type variability;
* unit consistency;
* REGION conversion reliability;
* safety-zone identification;
* product-footprint identification;
* tolerance suitability;
* vendor metadata reliability;
* false positive and false negative classifications.

The results of this evaluation will determine the final v1 layer rules and tolerance settings.

---

## 30. Open Technical Decisions

The following decisions remain intentionally open until additional CAD samples are reviewed:

1. final standardized layer names;
2. exact dimensional tolerance;
3. exact area tolerance;
4. preferred output DXF version;
5. how block geometry should be flattened or retained;
6. how safety-zone geometry should be selected when several candidate boundaries exist;
7. whether HATCH boundaries should be converted automatically;
8. maximum spline tessellation density;
9. whether normalized output should contain only operational layers or also reference layers;
10. whether ambiguous classifications should pause the pipeline or produce warnings;
11. whether vendor revision metadata should prevent unnecessary downloads before checksum verification;
12. final handling of holes inside safety-zone and footprint polygons.

---

## 31. Future Enhancements

* side-view CAD validation;
* 3D model processing;
* manufacturer-specific layer-rule libraries;
* AI-assisted layer classification;
* visual before-and-after comparison;
* SVG preview generation;
* manual classification override files;
* batch processing of multiple products;
* cloud object storage;
* supplier discrepancy-sheet generation;
* CAD-quality scoring;
* automatic comparison between multiple source revisions;
* event-based synchronization with the main PIM system.

---

## 32. v1 Delivery Principle

The module must favor preservation and transparent reporting over aggressive cleanup.

When uncertain, it should:

* keep geometry;
* place it in `PM_UNCLASSIFIED`;
* explain the uncertainty;
* avoid manufacturing missing geometry;
* avoid silently accepting discrepancies;
* avoid silently deleting vendor information.

The primary v1 objective is not perfect automatic interpretation of every vendor DWG. It is a reliable, auditable pipeline that safely standardizes known geometry, exposes uncertainty, and validates measurable CAD data against product information.
