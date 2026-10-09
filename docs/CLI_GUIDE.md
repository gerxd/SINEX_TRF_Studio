# Command line guide

The command line runs the SINEX TRF Studio analysis without the window. The window and the
command line use the same functions in `sinex_parser/analysis/`. With the same input and the
same settings, they give the same numbers.

## Start the command line

On Windows:

```bash
.venv/Scripts/python.exe -m sinex_parser.cli <command> [options]
```

On macOS and Linux, use `.venv/bin/python`. The launcher creates `.venv` when it runs for the
first time.

The program name in the help text and in error messages is `sinex-trf`. There is no installed
console script, so use `-m`.

The standalone build from `build_nuitka.ps1` contains only the window. To use the command
line, use the Python install.

```bash
.venv/Scripts/python.exe -m sinex_parser.cli --version
.venv/Scripts/python.exe -m sinex_parser.cli --help
.venv/Scripts/python.exe -m sinex_parser.cli datum --help
```

| Command | Result |
|---|---|
| `parse` | a list of the blocks in a file |
| `datum` | Sigma Theta, cross correlations, Helmert parameters and the diagnostics report |
| `report` | the diagnostics report only |
| `normal` | the normal matrix, the u vector and the recomputation check |

All commands accept a `.gz` file. The command line decompresses the file into the system
temporary folder, reads it, and then deletes the temporary file. The output names do not
include `.gz`. The command line does not use the library of the window.

## Global options

Put these options before the command.

| Option | Effect |
|---|---|
| `--version` | print the version and stop |
| `-v` | show log messages at level INFO |
| `-vv` | show log messages at level DEBUG |
| `-h`, `--help` | show the help, also for each command |

Log messages go to stderr. Results go to stdout. To hide the log, add `2>/dev/null`.

The `-v` options apply only to the log of the application. Messages from other libraries do
not show. The command line does not write `sinex_parser.log`.

## parse

`parse` reads a file and lists its blocks. It writes no files.

```bash
.venv/Scripts/python.exe -m sinex_parser.cli parse examples/CRI23527.SNX
```

```
file: CRI23527.SNX
blocks: 7
  SOLUTION/STATISTICS: 0.596846752908865
  SITE/ID: 87 entries
  SOLUTION/EPOCHS: 87 entries
  SOLUTION/ESTIMATE: 261 entries
  SOLUTION/APRIORI: 261 entries
  SOLUTION/MATRIX_ESTIMATE L COVA: array 261x261 float64
  SOLUTION/MATRIX_APRIORI L COVA: array 261x261 float64
parameters: 261
station episodes with complete xyz: 87
```

A list block shows its number of entries. A matrix block shows its shape and data type. Other
blocks show their value. The last two lines show:

- The number of parameters in `SOLUTION/ESTIMATE`.
- The number of station episodes with complete X, Y and Z coordinates. The datum calculation
  uses only these episodes.

Use `parse` first on a new file. The output shows if the file has a covariance block, an
apriori block and a `SOLUTION/STATISTICS` block. These blocks decide which commands can run.

### Load checks

Every command reads the file the same way and logs these messages:

- `WARNING <k> non-station parameters (<TYPE:count, ...>). Datum products use the station
  parameters only. Normal matrix products are not available.` Station parameters are `STAX`, `STAY`, `STAZ`, `VELX`, `VELY` and `VELZ`. Every
  other type, such as `RS_RA`, `XPO` or `UT1`, is a non-station parameter.
- `WARNING FILE/COMMENT line <n> starts with a block marker. Read as comment text.` Inside
  `FILE/COMMENT`, only a line that starts with `-FILE/COMMENT` closes the block.
- `WARNING <block> is not read by this version.` The file has a
  `SOLUTION/MATRIX_ESTIMATE` or `SOLUTION/MATRIX_APRIORI` block of a type other than `COVA`,
  or a `SOLUTION/NORMAL_EQUATION_MATRIX` or `SOLUTION/NORMAL_EQUATION_VECTOR` block, next to
  a `COVA` estimate block. The command does not convert these blocks.
- `WARNING Variance factor <s> matches the header RMS of unit weight <x>. It may be sigma0, not
  sigma0^2. If so, enter sigma0^2 as a custom variance factor.` The header or `FILE/COMMENT`
  has a value after `RMS OF UNIT WEIGHT` or `SIGMA0` that is within 5% of the variance
  factor, and that value is not close to 1.
- `INFO Blocks not read: <blocks>`, with `-v`.

If the file has one of the unsupported matrix blocks and no `SOLUTION/MATRIX_ESTIMATE` `COVA`
block, the load stops with exit code 2:

```
error: No covariance block. Found <blocks>, which this version does not read. Covariance and
datum products are not available.
```

## datum

`datum` calculates Sigma Theta, the cross correlations and the Helmert parameters, and writes
the diagnostics report. Use this command for ITRF files.

```bash
.venv/Scripts/python.exe -m sinex_parser.cli datum FILE.SNX --out DIR [options]
```

| Option | Default | Effect |
|---|---|---|
| `--out DIR` | `.` | output folder, created if it does not exist |
| `--formats LIST` | `csv` | a comma separated list of `xlsx`, `csv`, `txt`, `npy` |
| `--pos-threshold M` | `0.05` | position sigma threshold, in metres |
| `--vel-threshold M` | `0.003` | velocity sigma threshold, in metres per year |
| `--no-filter` | off | use all episodes |
| `--plots` | off | also write the three figures as PNG |
| `--dpi N` | `150` | resolution of the PNG figures |
| `--bare` | off | write the matrices without parameter names and units |
| `--json` | off | print a JSON summary, see [JSON summary](#json-summary) |

The file must have a `SOLUTION/MATRIX_ESTIMATE L COVA` or `U COVA` block and a
`SOLUTION/ESTIMATE` block. The command reads the covariance matrix directly and does not
build the normal matrix. For this reason, it does not need a `SOLUTION/STATISTICS` block. ITRF files do
not have this block.

### Episode filter

The filter is on by default. It excludes an episode if the position sigma is more than
`--pos-threshold` or the velocity sigma is more than `--vel-threshold`. To keep all
episodes, use `--no-filter`.

The command line has no manual episode selection. The filter uses the thresholds only.

### Output files

For an input file `STEM.SNX`:

| File | Contents |
|---|---|
| `STEM_sigma_theta<tag>.<ext>` | Sigma Theta |
| `STEM_cross_correlations<tag>.<ext>` | the cross correlation matrix |
| `STEM_helmert_parameters<tag>.<ext>` | the Helmert parameter sigmas |
| `STEM_diagnostics<tag>.txt` | the diagnostics report, see [report](#report) |
| `STEM_diagnostics<tag>.json` | the same report at full precision |

With `--plots`, the command also writes `STEM_sigma_theta<tag>.png`,
`STEM_cross_correlations<tag>.png` and `STEM_helmert_parameters<tag>.png`.

The window uses the same file names. You can compare the output of the two interfaces
file by file. `<tag>` is the filter tag, see [Filter tag](#filter-tag).

### Labels

The `.csv`, `.txt` and `.xlsx` files contain the parameter names `tx`, `ty`, `tz`, `ds`,
`rx`, `ry` and `rz`. For 14 parameters, the rates `tx_dot` to `rz_dot` follow.

- Sigma Theta has a header row and a first column with the name and unit, for example
  `tx [m]`, `ds [-]` and `rx_dot [rad/yr]`. The unit of entry (i, j) is the unit of row i
  multiplied by the unit of column j. In `.txt`, the first line starts with `#` and gives
  this rule.
- The cross correlations have names only.
- The Helmert parameters are a table with the columns `parameter`, `unit` and `sigma`.

The units are `m`, `-` for the scale, `rad`, and `m/yr`, `1/yr` and `rad/yr` for the rates.
In `.txt`, a label that contains a space is in quotes.

The labels do not change the values. `.npy` files have no labels. To write all files without
labels, use `--bare`. In the window, use Settings > Export defaults > Export datum matrices
without labels.

To read a labelled file with pandas and keep all digits, use `float_precision="round_trip"`:

```python
pd.read_csv("STEM_sigma_theta.csv", index_col=0, float_precision="round_trip")
pd.read_csv("STEM_sigma_theta.txt", sep=" ", comment="#", index_col=0,
            float_precision="round_trip")
```

### Filter tag

If the filter excludes no episode, the tag is empty. This occurs when the filter is off, or
when no episode is more than a threshold. If the filter excludes episodes, the tag gives the
thresholds in millimetres:

```
_filtered_p{pos_mm}mm_v{vel_mm}mmyr
```

| Thresholds | Tag |
|---|---|
| `--pos-threshold 0.010 --vel-threshold 0.001` | `_filtered_p10mm_v1mmyr` |
| defaults, 0.05 and 0.003 | `_filtered_p50mm_v3mmyr` |
| `--pos-threshold 0.0015 --vel-threshold 0.0025` | `_filtered_p1p5mm_v2p5mmyr` |

The tag changes only the file name. It does not change the calculation.

Two runs that exclude no episode write the same file names, and the second run overwrites the
first. To keep both, use a different `--out` folder for each run.

### Console output

```
episodes excluded: 0
episodes used: 87
sigma theta: 7x7
wrote <path>
...
```

`episodes used` is the number of episodes in the calculation. The size of Sigma Theta follows
from it.

## report

`report` writes only the diagnostics report. It calculates the same products as `datum`, with
the same filter, and writes them into the report without changes. `datum` writes the same
report next to its products.

```bash
.venv/Scripts/python.exe -m sinex_parser.cli report FILE.SNX --out DIR [options]
```

| Option | Default | Effect |
|---|---|---|
| `--out DIR` | `.` | output folder, created if it does not exist |
| `--pos-threshold M` | `0.05` | position sigma threshold, in metres |
| `--vel-threshold M` | `0.003` | velocity sigma threshold, in metres per year |
| `--no-filter` | off | use all episodes |
| `--json` | off | print a JSON summary |

The filter options are the same as for `datum`. The same options give the same Sigma Theta
and the same filter tag.

### Output files

| File | Contents |
|---|---|
| `STEM_diagnostics<tag>.txt` | the report, with numbers to 6 significant digits |
| `STEM_diagnostics<tag>.json` | the same report at full precision |

The JSON numbers round trip. The Sigma Theta, cross correlation and Helmert values in the JSON
file are bit identical to the `.npy` files from `datum`. Values that are not finite are
`null`.

### Report contents

- Provenance:
  - The file name, size and SHA-256.
  - The agency and data span from the `%=SNX` header line.
  - The number of parameters, stations and station episodes.
  - The non-station parameters by type, or `none`.
  - The application, Python, NumPy and BLAS versions.
  - The date with its UTC offset.
  - The reference epochs of the positions, with a warning if they are not all the same.
  - The filter and its tag.
- Sigma Theta:
  - The matrix and its diagonal.
  - The condition number, the symmetry error and the eigenvalues.
  - The number of negative diagonal entries.
  - The traces of the translation, scale and rotation blocks.
- Cross correlations:
  - The matrix.
  - The ten largest absolute correlations, by parameter name.
  - The number of pairs above 0.5 and above 0.9.
- Helmert parameter sigmas:
  - SI units.
  - Translations in mm, scale in ppb and rotations in mas. Scale and rotations also in mm at
    the surface, with the equatorial radius 6378137 m. Rates per year.
  - The same sigmas with no episode excluded, and the ratio of the two.
  - A table in the literature order tx, ty, tz, rx, ry, rz, ds and the rates, in mm and mm/yr.
    Use it to compare with a published table.
- Network geometry:
  - The episodes, stations and stations with velocities in the calculation.
  - Each excluded episode, with the reason and the sigma that caused it.
  - The rank of E.
  - The condition number of E^T E, without scaling and with unit column scaling.
  - The episodes in each hemisphere, and the range of geocentric latitude and longitude.
  - The eigenvalues of the mean outer product of the station unit vectors.
- Input precision:
  - Sigma statistics for each parameter type, with the median.
  - The covariance sigmas of the used episodes and of the excluded episodes.
  - Checks on the covariance rows in the calculation.

## normal

`normal` calculates the normal matrix N, the u vector and the apriori covariance, and does the
recomputation check.

```bash
.venv/Scripts/python.exe -m sinex_parser.cli normal FILE.SNX --out DIR [options]
```

| Option | Default | Effect |
|---|---|---|
| `--out DIR` | `.` | output folder, created if it does not exist |
| `--formats LIST` | `csv` | a comma separated list of `xlsx`, `csv`, `txt`, `npy` |
| `--variance-factor F` | from the file | the a posteriori variance factor |
| `--rank` | off | also calculate rank(N) by SVD |
| `--json` | off | print a JSON summary |

The file must have a covariance block and a `SOLUTION/ESTIMATE` block. The command calculates

```
N = inv(Qx) - inv(Q0),   Qx = Cx / s,   Q0 = C0 / s
u = inv(Qx) dx,          dx = estimate - apriori value
```

where Cx is the estimate covariance, C0 the apriori covariance (`SOLUTION/MATRIX_APRIORI`) and s
the variance factor. Without an apriori covariance block, N = inv(Qx). Without a
`SOLUTION/APRIORI` block, the command does not calculate u and does not do the recomputation
check. If `SOLUTION/APRIORI` and `SOLUTION/ESTIMATE` list the parameters in different orders, the
command stops with exit code 2, because the apriori constraints cannot be placed.

The command refuses a file with non-station parameters, with exit code 2:

```
error: Normal matrix products need station parameters only. This file has <k> non-station
parameters.
```

The window refuses the same files. The datum commands still run on them and use the station
parameters only.

### Variance factor

The command reads the variance factor from the `SOLUTION/STATISTICS` block. ITRF files do not
have this block. In this case, the command stops with this error:

```
error: this file has no SOLUTION/STATISTICS block, so there is no variance factor
to read. Enter it with --variance-factor.
```

To continue, give the factor, for example `--variance-factor 1.0`. The factor scales Qx and
Q0, so N and u change with it. dx does not. If the file has a `SOLUTION/STATISTICS` block,
`--variance-factor` replaces its value.

The window uses the same rule. It asks for the factor in a dialog.

### Recomputation check

The command prints the check on each run. It solves for dx from N and u and compares the
result with the original dx:

```
------ Performing Recomputation Check ------
Original dx norm: 1.526485e-01
Recomputed dx norm: 1.526485e-01
Difference Norm (L2): 2.333593e-14
Max Absolute Difference: 1.217429e-14
Relative Error: 1.528737e-13 (or 0.0000%)
PASS: relative_error <= 1e-6
```

A FAIL result shows that the inverse did not round trip. On a real file, the usual cause is a
covariance block that is rank deficient or badly conditioned.

### Rank

`--rank` is off by default. `np.linalg.matrix_rank` does a full singular value decomposition,
which is O(n^3). For large ITRF files, it takes much more time than all other steps. For
n = 25000, the estimate is approximately 29 minutes. For n = 261, it is immediate:

```
rank(N) = 261 of 261, deficiency 0, 0.005 s
```

Use `--rank` when you need the rank deficiency, for example to confirm the expected
deficiency of seven parameters.

### Output files

| File | Contents |
|---|---|
| `STEM_NormalMatrix.<ext>` | N |
| `STEM_u_Vector.<ext>` | u |
| `STEM_Covariance_Apriori_Matrix.<ext>` | the apriori covariance, if the file has one |

These names have no filter tag, because `normal` does not filter episodes.

## Formats and precision

`--formats` accepts any combination. For example, `--formats csv,txt,npy,xlsx` writes all
four formats in one run. The command refuses an unknown format before it writes a file. It
refuses `.xlsx` for a matrix with more than 5 million cells.

| Format | Writer | Precision | Exact |
|---|---|---|---|
| `.npy` | `np.save` | full float64 | yes |
| `.csv` | comma separated, `%.17g` | full float64 | yes, after a text round trip |
| `.txt` | space separated, `%.17g` | full float64 | yes, after a text round trip |
| `.xlsx` | `pandas.to_excel` with openpyxl | 16 significant digits | no, up to 1 ULP |

**Use `.npy` for values that go into a further calculation.** It is the only format with no
conversion to text.

`.xlsx` loses the last bit of values that need 17 significant digits, approximately half of
all values. The relative error is approximately 4e-16. Use `.xlsx` to read values, not as
input to a further calculation. Two `.xlsx` files from the same run are never byte identical,
because the format contains a creation time. Compare spreadsheets by cell value.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 2 | usage error, missing file, file that cannot be read, missing required block, missing variance factor, unsupported matrix block without a covariance block, `normal` on a file with non-station parameters, or apriori and estimate blocks in different orders |
| 3 | the calculation failed, with a `DatumError` or a `NormalMatrixError` |

Errors go to stderr, with the prefix `error:`. Code 3 shows a mathematical failure on the file,
not an error in the command.

## JSON summary

All four commands accept `--json`. The command then prints one JSON object to stdout in place
of the usual lines. It writes the same files. Values that are not finite are `null`.
`non_station_parameters` maps each non-station parameter type to its count, and is `{}` when
the file has none.

| Command | Keys |
|---|---|
| `parse` | `command`, `version`, `file`, `blocks` (shape and dtype, number of entries, or value), `parameters`, `station_episodes`, `non_station_parameters` |
| `datum` | `command`, `version`, `file`, `filtered`, `episodes_excluded`, `episodes_used`, `negative_sigma_theta_diagonal`, `non_station_parameters`, `helmert` (by name, `tx` to `rz_dot`), `written` |
| `report` | `command`, `version`, `file`, `filtered`, `episodes_excluded`, `episodes_used`, `non_station_parameters`, `written` |
| `normal` | `command`, `version`, `file`, `variance_factor`, `n`, `recomputation_check` (the lines), `written`, and with `--rank` also `rank` and `rank_deficiency` |

```bash
.venv/Scripts/python.exe -m sinex_parser.cli datum examples/CRI23527.SNX --out out --json
```

## Examples

All products of one file, in all formats, with figures:

```bash
.venv/Scripts/python.exe -m sinex_parser.cli datum ITRF2020-u2023-IDS-TRF.SNX \
  --out out/IDS --formats csv,txt,npy,xlsx --plots
```

Thresholds of 10 mm and 1 mm/yr:

```bash
.venv/Scripts/python.exe -m sinex_parser.cli datum FILE.SNX --out out/ \
  --pos-threshold 0.010 --vel-threshold 0.001
```

All episodes, no filter:

```bash
.venv/Scripts/python.exe -m sinex_parser.cli datum FILE.SNX --out out/ --no-filter
```

Normal equations with a variance factor of 1.0, with the rank:

```bash
.venv/Scripts/python.exe -m sinex_parser.cli normal FILE.SNX --out out/ \
  --variance-factor 1.0 --rank --formats npy
```

All files in a folder, with one output folder for each file, in bash:

```bash
for f in data/*.SNX; do
  stem=$(basename "$f" .SNX)
  .venv/Scripts/python.exe -m sinex_parser.cli datum "$f" --out "out/$stem" --formats npy,csv
done
```

The same in PowerShell:

```powershell
Get-ChildItem "data\*.SNX" | ForEach-Object {
    & .venv\Scripts\python.exe -m sinex_parser.cli datum $_.FullName `
        --out "out\$($_.BaseName)" --formats npy,csv
}
```

The program has no batch mode. Use a shell loop, as in the examples above.

## Use without a display

The analysis does not import an interface library at module level.

- `parse`, `normal`, `report`, and `datum` without `--plots` need NumPy, pandas and openpyxl.
  They do not need Qt or matplotlib.
- `datum --plots` imports matplotlib with the `Agg` backend. It does not need a display.

The command line does not need `QT_QPA_PLATFORM`.

## Window only

These functions are in the window only:

- The station map.
- The matrix visualizer, the matrix inspector and the eigenvalue distribution.
- Manual episode selection.
- The Block Export tab, which writes one parsed block.
- The Covariance Matrix tab, the Stations tab and the Information tab.
- The theme and the settings.
- The benchmark report.
