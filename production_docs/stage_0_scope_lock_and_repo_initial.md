# Stage 0 — Scope Lock and Repository Initialization

## Objective

Establish a clean standalone Python project for the Gebal CAD Normalizer without implementing any CAD-processing functionality.

## Scope Completed

Stage 0 created the initial repository structure and locked the project scope around a reusable Python module.

The module is intended to later:

* accept vendor product data;
* identify and download top-view DWG files;
* manage local CAD versions;
* convert DWG files through ODA File Converter;
* normalize layers and entities;
* convert REGION geometry into polylines;
* validate CAD measurements against product data;
* return normalized CAD files and reports.

No functional CAD logic was implemented during this stage.

## Repository Structure

```text
gebal-cad-normalizer/
├── PRD.md
├── README.md
├── pyproject.toml
├── .gitignore
├── src/
│   └── gebal_cad_normalizer/
│       ├── __init__.py
│       ├── pipeline.py
│       ├── config.py
│       ├── models.py
│       └── exceptions.py
└── tests/
    └── __init__.py
```

## Files Created

* `README.md`
* `pyproject.toml`
* `.gitignore`
* `src/gebal_cad_normalizer/__init__.py`
* `src/gebal_cad_normalizer/pipeline.py`
* `src/gebal_cad_normalizer/config.py`
* `src/gebal_cad_normalizer/models.py`
* `src/gebal_cad_normalizer/exceptions.py`
* `tests/__init__.py`

## Key Decisions

### Standalone Python Package

The CAD normalizer is implemented as an independent Python package rather than as part of a GUI application.

### Source Layout

A `src/` package layout was selected to avoid accidental imports from the repository root and to support clean packaging.

### Minimal Dependencies

No runtime dependencies were added during Stage 0.

Libraries such as `pydantic`, `ezdxf`, and `httpx` were intentionally deferred until their corresponding implementation stages.

### External CAD Tools

ODA File Converter and nanoCAD are treated as external tools rather than Python package dependencies.

### No Premature Implementation

The initial modules contain only minimal package boundaries and documentation. No placeholder functions claim to perform CAD operations.

## Explicit Exclusions

Stage 0 did not implement:

* downloading;
* network access;
* version management;
* ODA conversion;
* DWG or DXF parsing;
* REGION conversion;
* geometry processing;
* layer classification;
* CAD validation;
* GUI functionality;
* database functionality;
* API server functionality;
* AI integration.

## Verification Performed

The package was installed in editable mode.

```bash
python -m pip install -e .
```

The package import was verified.

```bash
python -c "import gebal_cad_normalizer; print('import ok')"
```

The package modules were also imported successfully.

```bash
python -c "import gebal_cad_normalizer.config, gebal_cad_normalizer.models, gebal_cad_normalizer.exceptions, gebal_cad_normalizer.pipeline; print('module imports ok')"
```

## Verification Results

* Editable installation passed.
* Main package import passed.
* Module imports passed.
* No GUI or unrelated dependencies were present.
* Pytest was not configured or required at this stage.
* The folder was not yet initialized as a Git repository.

## Known Limitations

* No processing features exist yet.
* No automated tests beyond import verification were added.
* Git history was unavailable because the folder was not a Git repository.

## Exit Gate

Stage 0 passed.

The repository represents a clean standalone Python package, imports correctly, and contains no premature CAD-processing implementation.
