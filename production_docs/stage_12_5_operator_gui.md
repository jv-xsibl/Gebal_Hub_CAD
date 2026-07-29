# Stage 12.5 Operator GUI

Stage 12.5 adds a basic desktop operator interface over the Stage 12 reporting package API. It is intentionally thin: all CAD conversion, inventory, canonicalization, REGION conversion, classification, rewrite, SVG export, measurement, validation, DWG export, partial-failure packaging, overwrite protection, and report generation remain in `gebal_cad_normalizer.reporting.run_reporting_pipeline(...)`.

## Entry Point

```powershell
python tests\live\stage12_5_run_gui.py
```

The GUI runs directly in the workspace. It does not use Windows Sandbox and does not introduce AI behavior.

## Operator Flow

1. Select the product JSON file.
2. Select the source DWG or DXF file.
3. Select the output folder.
4. Optionally select the ODA File Converter executable.
5. Set vendor profile, unit override, normalized DWG export, and overwrite behavior.
6. Run the pipeline.
7. Review the final status, occurrence ID, package path, log, and output buttons.

## Fields

- Product JSON file
- DWG/DXF file
- Output folder
- ODA executable
- Vendor profile
- Unit override
- Export normalized DWG
- Allow overwrite

## Actions

- Browse buttons use native file/folder dialogs.
- Run pipeline starts a background Qt worker thread and disables Run until completion.
- Cancel is disabled because Stage 12 does not currently expose safe cancellation.
- Open output folder, `report.md`, normalized DXF, and combined SVG use the OS default application.

## Persistence And Safety

The GUI stores only recent non-sensitive paths and settings through `QSettings`: product JSON path, CAD path, output folder, ODA executable path, vendor profile, unit override, and checkbox states. It never stores product JSON contents or credentials.

Source files remain read-only from the GUI perspective. Stage 12 captures source checksums before processing and verifies them before package promotion. Existing occurrence packages are not overwritten unless `Allow overwrite` is checked.

## Result Handling

Stage 12 package statuses are displayed as:

- `pass`
- `warnings` for `pass_with_warnings`
- `review` for `review_required`
- `fail`

Partial-failure packages are handled by reading the returned manifest. Any available package, report, normalized DXF, and combined SVG actions are enabled only when the corresponding file exists.

## Verification Log

Production GUI runs should write packages under the operator-selected output directory. For project verification, use:

```text
tests/output/stage12_live/
```

Use the occurrence package path shown in the GUI as the production log path for a specific run.
