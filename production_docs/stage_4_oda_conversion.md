# Stage 4 ODA Conversion

## Objective

Implement only a deterministic, testable ODA File Converter wrapper for one-file DWG to DXF and DXF to DWG conversion. Stage 4 does not inspect DXF contents, process geometry, convert REGION entities, measure CAD, validate product data, or use AI.

## ODA CLI Assumptions

The wrapper assumes the locally installed ODA File Converter accepts the standard folder-based command shape:

```text
<oda_executable> <input_folder> <output_folder> <target_version> <output_type> <recursive> <audit>
```

Stage 4 invokes it as an argument list with:

- `target_version`: configurable, public default `R2013`, normalized to ODA CLI token `ACAD2013`;
- `output_type`: `DXF` for DWG to DXF, `DWG` for DXF to DWG;
- `recursive`: `0`;
- `audit`: `1`.

No shell command string is used.

## Configuration

The executable path can be supplied directly on `OdaConverter`, directly on `OdaConversionRequest`, through `CadNormalizerConfig.oda_executable_path`, or through the `GEBAL_ODA_FILE_CONVERTER` environment variable.

The target CAD version is configurable on the request. The public default remains friendly `R2013`, matching the PRD configuration, but Stage 4 normalizes friendly versions to ODA CLI tokens before subprocess invocation. Supported mappings are `R2010` -> `ACAD2010`, `R2013` -> `ACAD2013`, and `R2018` -> `ACAD2018`. Already valid `ACAD2010`, `ACAD2013`, and `ACAD2018` tokens pass through unchanged. Unsupported versions are rejected before ODA is invoked.

The timeout is configurable per request and defaults to 120 seconds.

## Conversion Flow

1. Validate the executable path.
2. Validate that the source exists.
3. Validate the extension pair:
   - `.dwg` to `.dxf`;
   - `.dxf` to `.dwg`.
4. Create isolated temporary input and output directories.
5. Copy the source file into the temporary input directory.
6. Normalize the configured target version to an ODA CLI token, for example `R2013` to `ACAD2013`.
7. Invoke ODA with `subprocess.run` using a list of arguments and `shell=False`.
8. Capture stdout, stderr, exit code, and elapsed time.
9. Require exit code `0`.
10. Require the expected output filename in the temporary output directory.
11. Require the output to be non-empty.
12. Atomically promote the converted file to the requested destination.
13. Clean temporary directories after success or failure.

## Atomic Safety Decisions

- The source file is copied into a temporary input folder and is never passed as a mutable destination.
- ODA writes only to an isolated temporary output folder.
- Stale output files are rejected by using a fresh temporary output directory per run.
- Existing destination files are not touched until conversion succeeds and the temporary output is non-empty.
- Successful promotion uses a temporary destination sibling and `os.replace`.
- Failed conversion paths preserve existing destination files.

## Error Codes

- `executable_missing`: executable path is missing or not a file.
- `invalid_extension`: unsupported source/destination extension pair.
- `source_missing`: source CAD file does not exist.
- `timeout`: ODA exceeded the configured timeout.
- `process_failed`: ODA returned a non-zero exit code.
- `output_missing`: ODA exited successfully but did not create the expected output file.
- `output_empty`: ODA created a zero-byte output file.
- `promotion_failed`: converted output could not be moved into the requested destination.
- `unsupported_target_version`: target CAD version is not one of the supported friendly versions or ODA CLI tokens.

## Tests and Results

Mocked unit tests cover:

- missing ODA executable;
- missing source file;
- invalid extension pair;
- successful DWG to DXF with `R2013` invoking ODA as `ACAD2013`;
- successful DXF to DWG;
- already valid `ACAD...` target token pass-through;
- unsupported target-version rejection;
- timeout handling;
- non-zero exit handling;
- exit 0 but output missing;
- empty output rejection;
- existing destination preserved on failure;
- successful destination replacement;
- temporary folder cleanup;
- subprocess argument list and `shell=False`;
- source input not mutated.

Verification performed:

```text
python -m pytest
python -c "from gebal_cad_normalizer.cad import OdaConverter, OdaConversionRequest; print('import ok')"
python -m py_compile tests/live/stage4_live_oda.py
```

## Live-Test Instructions

The live verification script is manual and is not part of the default pytest suite.

```powershell
python tests/live/stage4_live_oda.py --oda-exe "C:\Path\To\ODAFileConverter.exe" --input "C:\Path\To\input.dwg" --output "C:\Path\To\output.dxf" --target-version R2013
```

Use `.dxf` input with `.dwg` output to verify the reverse direction. The live runner accepts friendly versions such as `R2013`; the wrapper sends `ACAD2013` to ODA, which produces DXF version `AC1027` for that target.

## Known Limitations

- No DXF inspection is implemented.
- No geometry processing is implemented.
- No REGION conversion is implemented.
- No CAD measurement or JSON-versus-CAD validation is implemented.
- The wrapper assumes ODA preserves the input stem when writing the converted output.
- Live conversion depends on a locally installed ODA File Converter binary.

## Exit-Gate Status

Passed. Stage 4 adds only the ODA conversion wrapper, mocked tests, manual live runner, and public configuration/usage documentation. Post-live-verification fix: friendly target versions are normalized to real ODA CLI tokens so `R2013` invokes ODA as `ACAD2013`.


