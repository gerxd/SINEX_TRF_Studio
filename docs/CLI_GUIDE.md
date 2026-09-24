# Command line guide

SINEX TRF Studio 1.4 runs its whole analysis without the desktop window. This
guide covers every command, every flag, what gets written, and what the command
line cannot do.

The window and the command line call the same functions in `sinex_parser/analysis/`,
so for the same input and the same settings they produce the same numbers.

## Why it exists

Three reasons, in the order they matter:

1. A machine with no display can run the analysis. Nothing on the analysis path
   imports Qt, matplotlib, pyqtgraph, folium or plyer at module level, so a server
   or a cluster node needs none of them installed for `parse` and the matrix work.
2. A run can be scripted and repeated. The window needs a person to click through
   it, which is not a workflow you can put in a shell loop or a job script.
3. It makes the outputs checkable. Two interfaces producing the same bytes is a
   much stronger statement than one interface producing bytes nobody compares.

## Invoking it

```bash
.venv/Scripts/python.exe -m sinex_parser.cli <command> [options]
```

The program name in help text and errors is `sinex-trf`. There is no installed
console script, so run it through `-m`.

The standalone build produced by `build_nuitka.ps1` is the window only. Its entry
point is `main.py` and it is compiled with the Windows console disabled, so the
command line is not reachable from the executable. A machine that needs the command
line needs the Python install.

```bash
.venv/Scripts/python.exe -m sinex_parser.cli --version    # SINEX TRF Studio 1.4.0
.venv/Scripts/python.exe -m sinex_parser.cli --help
.venv/Scripts/python.exe -m sinex_parser.cli datum --help
```

Three commands: `parse`, `datum`, `normal`.

## Global options

| Option | Effect |
|---|---|
| `--version` | print the version and exit |
| `-v` | set the application logger to INFO |
| `-vv` | set the application logger to DEBUG |
| `-h`, `--help` | help, also available per command |

Both verbosity levels write to stderr, so `2>/dev/null` separates the log from the
results on stdout.

Verbosity applies to the application's own logger only. Third party libraries log
to the root logger, which the application no longer configures, so their output
does not appear whatever you pass.

The command line does not write `sinex_parser.log`. It clears the handlers the
package installs at import and attaches its own stderr handler, so a run leaves no
file behind in the working directory. The window still writes the log file, and
since v1.4 the settings menu controls where it goes, how large it may grow, and
whether it is written at all.

## parse

Reports what a file contains. It reads the file, builds every block, and prints an
inventory. Nothing is written to disk.

```bash
.venv/Scripts/python.exe -m sinex_parser.cli parse examples/BKG08457.SNX
```

```
file: BKG08457.SNX
blocks: 5
  SITE/ID: 13 entries
  SOLUTION/ESTIMATE: 39 entries
  SOLUTION/APRIORI: 39 entries
  SOLUTION/MATRIX_ESTIMATE L COVA: array 39x39 float64
  SOLUTION/MATRIX_APRIORI L COVA: array 39x39 float64
parameters: 39
station episodes with complete xyz: 13
```

List blocks report their entry count, matrix blocks report shape and dtype, and
anything else prints its value. The last two lines are the parameter count from
`SOLUTION/ESTIMATE` and the number of station episodes that carry a complete XYZ
triple, which is what the datum geometry can actually use.

Use `parse` first on an unfamiliar file. It tells you in seconds whether the file
carries a covariance block, an apriori block or a `SOLUTION/STATISTICS` block,
which decides which of the other two commands can run at all.

## datum

The datum effect analysis: sigma theta, the cross correlations and the Helmert
parameters, plus the statistics report. This is the ITRF path, and the one to reach
for on real reference frame files.

```bash
.venv/Scripts/python.exe -m sinex_parser.cli datum FILE.SNX --out DIR [options]
```

| Option | Default | Meaning |
|---|---|---|
| `--out DIR` | `.` | directory to write into, created if missing |
| `--formats LIST` | `csv` | comma separated, any of `xlsx`, `csv`, `txt`, `npy` |
| `--pos-threshold M` | `0.05` | position sigma threshold in metres |
| `--vel-threshold M` | `0.003` | velocity sigma threshold in metres per year |
| `--no-filter` | off | use every episode, skip threshold filtering |
| `--plots` | off | also save the three figures as png |

It needs a `SOLUTION/MATRIX_ESTIMATE L COVA` or `U COVA` block and a
`SOLUTION/ESTIMATE` block. It reads the covariance directly and never builds the
normal matrix, so it works on files with no `SOLUTION/STATISTICS` block, which is
every real ITRF file.

### Filtering

By default the episode filter is on. An episode is excluded when its position sigma
exceeds `--pos-threshold` or its velocity sigma exceeds `--vel-threshold`. Pass
`--no-filter` to keep everything.

The manual episode selection offered by the window is not available here. The
command line filter is threshold based only.

### What it writes

For a file named `STEM.SNX`, and a filter tag described below:

| File | Contents |
|---|---|
| `STEM_sigma_theta<tag>.<ext>` | the sigma theta matrix |
| `STEM_cross_correlations<tag>.<ext>` | the cross correlation matrix |
| `STEM_helmert_parameters<tag>.<ext>` | the Helmert parameters |
| `STEM_datum_stats<tag>.txt` | the statistics report, always txt |

With `--plots`, three more at 150 dpi:

| File |
|---|
| `STEM_sigma_theta<tag>.png` |
| `STEM_cross_correlations<tag>.png` |
| `STEM_helmert_parameters<tag>.png` |

The names are the window's names. That is deliberate, so that output from either
interface drops into the same directory layout and can be compared file by file.

### The filter tag

The tag is empty when nothing was filtered out, either because filtering was off or
because no episode crossed a threshold. When episodes were excluded the tag records
the thresholds in millimetres:

```
_filtered_p{pos_mm}mm_v{vel_mm}mmyr
```

So `--pos-threshold 0.010 --vel-threshold 0.001` gives
`_filtered_p10mm_v1mmyr`, and the default thresholds give `_filtered_p50mm_v3mmyr`.
The thresholds are rounded to whole millimetres for the name only, never for the
mathematics.

A run that excludes nothing writes unsuffixed names. Two runs at different
thresholds that both exclude nothing therefore write to the same filenames and the
second overwrites the first. Give them separate `--out` directories.

### Console output

```
episodes excluded: 0
episodes used: 13
sigma theta: 7x7
wrote <path>
...
```

`episodes used` is the count that reached the geometry, which is what the sigma
theta dimension follows from.

## normal

The normal equation path: N, the u vector, the apriori covariance, and the
recomputation check.

```bash
.venv/Scripts/python.exe -m sinex_parser.cli normal FILE.SNX --out DIR [options]
```

| Option | Default | Meaning |
|---|---|---|
| `--out DIR` | `.` | directory to write into, created if missing |
| `--formats LIST` | `csv` | comma separated, any of `xlsx`, `csv`, `txt`, `npy` |
| `--variance-factor F` | from the file | the a posteriori variance factor |
| `--rank` | off | also compute rank(N) by SVD |

It needs a covariance block, `SOLUTION/ESTIMATE` and `SOLUTION/APRIORI`. It builds
N by inverting the covariance and scaling by the variance factor.

### The variance factor rule

The factor comes from the file's `SOLUTION/STATISTICS` block. Real ITRF files do
not carry one. In that case the command stops with an explanation instead of
producing nothing:

```
error: this file has no SOLUTION/STATISTICS block, so there is no variance factor
to read. Pass --variance-factor explicitly.
```

Pass `--variance-factor 1.0` to proceed. That is a choice about the mathematics,
not a formality: N is scaled by it, so u and dx scale with it too. Passing the flag
when the file does have a block overrides the block.

This is the same rule the window applies, where it asks for the factor in a dialog.

### The recomputation check

Printed on every run. It solves back from N and u and compares to the original dx:

```
------ Performing Recomputation Check ------
Original dx norm: 7.753603e-02
Recomputed dx norm: 7.753603e-02
Difference Norm (L2): 7.696986e-14
Max Absolute Difference: 4.085621e-14
Relative Error: 9.926979e-13 (or 0.0000%)
PASS: relative_error <= 1e-6
```

A FAIL here means the inverse did not round trip, which on a real file points at a
rank deficient or badly conditioned covariance block rather than at a bug.

### Rank

`--rank` is off by default because `np.linalg.matrix_rank` is a full singular value
decomposition, which is O(n^3) and dominates everything else at ITRF size. The
handover measures about 29 minutes at n = 25000. At n = 39 it is immediate:

```
rank(N) = 39 of 39, deficiency 0, 0.000 s
```

Turn it on when you actually need the rank deficiency, which for a datum analysis
is usually to confirm the expected seven parameter deficiency.

### What it writes

| File | Contents |
|---|---|
| `STEM_NormalMatrix.<ext>` | N |
| `STEM_u_Vector.<ext>` | u |
| `STEM_Covariance_Apriori_Matrix.<ext>` | the apriori covariance, when the file carries one |

These names carry no filter tag, because the normal equation path does no episode
filtering.

## Formats and precision

`--formats` takes any combination, so `--formats csv,txt,npy,xlsx` writes all four
in one pass. An unknown name is refused before anything is written. `.xlsx` is
refused for a matrix of more than 5 million cells.

| Format | Written by | Precision | Exact |
|---|---|---|---|
| `.npy` | `np.save` | full float64 | yes |
| `.csv` | `np.savetxt`, comma separated, `%.17g` | full float64, round trips | yes, through text |
| `.txt` | `np.savetxt`, space separated, `%.17g` | full float64, round trips | yes, through text |
| `.xlsx` | `pandas.to_excel` through openpyxl | 16 significant digits | no, up to 1 ULP |

**Use `.npy` when a number has to survive to the next stage of a calculation.** It
is the only format with no text conversion anywhere in the path.

`.xlsx` loses the last bit on values whose shortest exact representation needs 17
significant digits, which is roughly half of them. The error is about 4e-16
relative, far below the millimetre level uncertainties being reported, so it is
fine for reading and for a dissertation appendix and wrong for chaining into
another computation. It is also never byte identical between two runs, because the
format embeds a creation timestamp. Compare spreadsheets by cell value.

Full detail, including the measured effect of the v1.2 precision change, is in
`docs/export_hash_check.md`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 2 | usage error, a missing file, a file that cannot be parsed, a missing required block, or a missing variance factor |
| 3 | the analysis raised, a `DatumError` or a `NormalMatrixError` |

Errors print to stderr with an `error:` prefix. Anything that reaches code 3 is a
mathematical failure on that file rather than a mistake in the command.

## JSON summary

`parse`, `datum` and `normal` take `--json`. The command then prints one JSON object
to stdout instead of its usual lines, and writes the same files. Values that are
not finite are written as `null`.

| Command | Keys |
|---|---|
| `parse` | `command`, `version`, `file`, `blocks` (shape and dtype, entry count, or value), `parameters`, `station_episodes` |
| `datum` | `command`, `version`, `file`, `filtered`, `episodes_excluded`, `episodes_used`, `negative_sigma_theta_diagonal`, `helmert` (by name, `tx` to `ez_v`), `written` |
| `normal` | `command`, `version`, `file`, `variance_factor`, `n`, `recomputation_check` (the lines), `written`, and `rank` and `rank_deficiency` with `--rank` |

```bash
.venv/Scripts/python.exe -m sinex_parser.cli datum examples/BKG08457.SNX --out out --json
```

## Worked examples

Every product of one file in every format, with figures:

```bash
.venv/Scripts/python.exe -m sinex_parser.cli datum "SINEX Test Files/ITRF2020/u2023/ITRF2020-u2023-IDS-TRF.SNX" \
  --out out/IDS --formats csv,txt,npy,xlsx --plots
```

The reference threshold pair used by the committed comparison exports:

```bash
.venv/Scripts/python.exe -m sinex_parser.cli datum FILE.SNX --out out/ \
  --pos-threshold 0.010 --vel-threshold 0.001
```

Unfiltered, for a run that has to include every episode:

```bash
.venv/Scripts/python.exe -m sinex_parser.cli datum FILE.SNX --out out/ --no-filter
```

Normal equations with an assumed unit variance factor, including rank:

```bash
.venv/Scripts/python.exe -m sinex_parser.cli normal FILE.SNX --out out/ \
  --variance-factor 1.0 --rank --formats npy
```

A directory of files, one output directory each, in bash:

```bash
for f in "SINEX Test Files"/*.SNX; do
  stem=$(basename "$f" .SNX)
  .venv/Scripts/python.exe -m sinex_parser.cli datum "$f" --out "out/$stem" --formats npy,csv
done
```

The same in PowerShell:

```powershell
Get-ChildItem "SINEX Test Files\*.SNX" | ForEach-Object {
    & .venv\Scripts\python.exe -m sinex_parser.cli datum $_.FullName `
        --out "out\$($_.BaseName)" --formats npy,csv
}
```

There is no batch mode inside the program. Batch processing, a progress and cancel
path, and a machine readable manifest of what succeeded are the v1.4 scope. Until
then a shell loop is the batch mode, and it has no shared progress reporting and no
manifest.

## Running with no display

The analysis path imports no interface library at module level. In practice:

- `parse` and `normal` need numpy, pandas and openpyxl. No Qt, no matplotlib.
- `datum` without `--plots` is the same.
- `datum --plots` imports matplotlib inside the function and forces the `Agg`
  backend before importing pyplot, so it needs no display either.

Nothing in the command line needs `QT_QPA_PLATFORM` set.

## What the command line does not do

The window keeps these, and there is no plan to move them:

- The station map, which is folium in a web engine view.
- The matrix visualiser, the matrix inspector dialog and the eigenvalue and
  spectrum views.
- Manual episode selection, where you tick episodes individually rather than
  setting a threshold.
- The raw block export tab, which writes any parsed block on its own.
- The covariance matrix browser, the stations table and the info tab.
- Themes and settings.

The command line also does not compute the eigenvalue distribution, and it does not
write the benchmark file the window produces.
