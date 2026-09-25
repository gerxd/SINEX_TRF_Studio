# Changelog

## 1.5.0

Changes:
- The statistics report is now the diagnostics report, written as text and JSON. It holds
  the provenance of the file and the run, Sigma Theta with its checks, the ten largest
  cross correlations, the Helmert sigmas in SI and literature units (mm, ppb, mas) beside
  the unfiltered ones, a table in the literature parameter order, the network geometry
  and the input precision
- The command line has a `report` command with the same filter flags as `datum`. `datum`
  writes the diagnostics report where it wrote the statistics report
- `.gz` SINEX files open directly, in the window and on the command line
- Loaded files are kept in a library of binary copies, so the next load skips the text
  parse. A library entry is opened memory mapped: the matrix stays on disk and is read
  as the computations need it. On ITRF2020-u2023-IGS-TRF (9.9 GB, n = 27426) the first
  load takes about 110 s and writes the covariance straight to disk, with 0.4 GiB of
  private memory. The entry is 5.6 GiB on disk and opens in about 0.1 s, and reading the
  whole matrix from it takes 4 to 6 s
- Sigma Theta with the episode filter on builds the filtered covariance in one copy
  instead of two. On ITRF2020-u2023-IGS-TRF at 10 mm and 1 mm/yr its peak memory went
  from 8.1 to 4.1 GiB and its time from 10.1 to 8.2 s
- The Library window, opened from the Library button, lists the kept files. An entry can
  be loaded or deleted from there. Settings, Library has "Library folder..." and "Keep a
  binary copy of loaded files", which is on by default
- SOLUTION/EPOCHS is parsed when "Skip parsing SOLUTION/EPOCHS" is off
- "Load discontinuity list" in the Stations tab reads an ITRF discontinuity file
  (SOLUTION/DISCONTINUITY), plain or .gz. Every episode shows its data span and the reason
  for the break that starts it, in the filtered episodes dialog, the diagnostics report,
  the map popups and the station panel
- The map shows one marker per episode, coloured kept or filtered. Episodes at the same
  site sit in a small ring around it
- The map is built once per file. Selecting a station, the kept and filtered toggles, a new
  Sigma Theta and a discontinuity list update the open map instead of reloading it. At IGS
  scale a station click takes about 0.1 s instead of about 11 s
- Sigma Theta runs beside the window, and the progress bar names the running step
- A load from the library logs each block with its size and the time the load took
- The station list is sorted alphabetically, and the first station in that order is
  selected after a load
- The Library window has an "Open folder" button that opens the library folder in the
  file manager
- The Visualizer options keep their size when the window is made smaller. The panel
  scrolls instead of squashing its controls, and the two PyQtGraph buttons share a row
- Dependencies are pinned to exact versions, updated to numpy 2.5.3, pandas 3.0.6,
  matplotlib 3.11.2 and the Qt 6.11.2 runtime. The launcher rebuilds the environment
  once on the next start
- "Record benchmark", "Skip file validation" and "Skip parsing SOLUTION/EPOCHS" moved from
  the header into the Settings menu. The two skip options are in Settings, Parsing, which
  replaces "Parsing defaults". "Record benchmark" and "Export benchmark report..." sit
  together in Settings. The Select SINEX File and Library buttons use the freed space
- These options are kept across runs. A change made with a file loaded applies to the
  next load, and the log says so

Fixes:
- The "NEW FILE" and "New File" lines and "Cleared previous results" appear only when a
  file was loaded before. A .gz file is named by its .gz name in the load lines
- In the station list, the Windows 11 selection marker no longer covers the station code

## 1.4.2

Changes:
- The Raw export tab is renamed Block Export
- The preview of SOLUTION/ESTIMATE and SOLUTION/APRIORI shades each sigma cell by its
  rank among the sigmas of the same parameter type, so the largest sigmas of STAX, VELZ
  and the others stand out. Hovering a cell shows the percentile
- The preview shows every row of a table block, not only the first 50
- Filter tags in file names keep sub-millimetre thresholds apart, for example
  `_filtered_p1p5mm_v2p5mmyr`. Whole millimetre thresholds keep the tags they had
- A manual episode selection is tagged `_manual_<N>excl`, where N is the number of
  excluded episodes, and the plot titles and the statistics report say manual
- With "Remember filter thresholds" off, a new window starts at the default thresholds
- On Windows, errors from the application after the launcher starts it are written to
  `.venv/launch.log`. A failed install step names the command and its exit code

Fixes:
- In the block list and the preview, the Windows 11 selection marker was drawn over the
  first letter of every cell in the selected row. Both tables now highlight the whole row
- A SOLUTION/ESTIMATE block listed out of INDEX order gives the same Sigma Theta, cross
  correlations, Helmert parameters and u as the same block in order. A block whose INDEX
  values are not the numbers 1 to n is refused
- A SOLUTION/ESTIMATE or SOLUTION/APRIORI line with a blank field, such as PT or SOLN, is
  read by the SINEX column positions. A line that cannot be read is skipped and counted in
  the log, and an unreadable number is no longer read as 0. When a SOLUTION/ESTIMATE line
  is skipped in a file with a covariance block, the load stops with a message that says so
- The datum computation logs a warning when the station positions it uses have more than
  one reference epoch
- A covariance block that mixes E and D exponents no longer stops the load
- After a failed load, the Covariance tab, the datum station list, the Block Export tab
  and the file information are cleared. The file name in the header changes when the
  parse completes, not when the file is chosen
- "Save full 1:1 plot" writes an n by n image with row 0 at the top. The interactive
  PyQtGraph plot also shows row 0 at the top

## 1.4.1

Changes:
- Settings has "Create desktop shortcut", which puts a shortcut to `run_windows.bat` on
  the Windows desktop, or to `run_macos_linux.sh` on macOS and Linux. The entry for the
  other system's run file is shown but disabled. The shortcut uses the window icon on
  Windows and Linux

## 1.4.0

New features:
- Large covariance blocks are parsed in up to four worker processes. At IGS scale,
  17616 parameters and 4.09 GB, a load takes about 28 s instead of 163 s and needs
  about 0.35 GiB more memory while it runs. Blocks under 64 MB are parsed as before.
  Settings, Parsing defaults turns it off or sets 2, 4 or 8 workers. The default is on, with 4 workers
- The normal matrix, the recomputation check, the rank, the eigenvalue plots and large
  exports run in the background, so the window keeps responding. A result that finishes
  after a new file is loaded is discarded
- `--json` on `parse`, `datum` and `normal` prints a machine readable summary

Changes:
- The window title shows the version number
- Log messages are coloured by kind: warnings amber, errors orange, finished steps
  green. The filter status line and the file information labels use the same colours
- The file is read once instead of twice
- Loading a new file closes the pyqtgraph preview and frees its copy of the matrix
- The normal matrix frees Qx as soon as it is inverted
- Raw block csv export is about 3 times faster, with identical output. .npy and .txt
  exports no longer copy the matrix first
- .xlsx export is refused above 5 million cells. Use .npy, .csv or .txt
- The negative Sigma Theta diagonal message is logged as a warning, so the command line
  shows it
- The Raw export tab is rebuilt. It lists the blocks the file holds with their size and an
  estimated output size, and shows a preview of the selected block
- Raw export writes several blocks in one action, into one folder, with fixed file names
- Each exported matrix can get a `_params.csv` that names the parameter of every row, taken
  from the INDEX column of SOLUTION/ESTIMATE, or SOLUTION/APRIORI for an apriori matrix
- Raw export writes `export_manifest.json` with the source file, the app version, and the
  size and SHA-256 of every file written
- Blocks too large for .xlsx are marked and left out before the export starts, instead of
  being refused after the save dialog

Fixes:
- A station less than one degree south of the equator was placed north of it on the map,
  and a station between 180 and 181 degrees east was left off the map
- Choosing a second file while one is parsing no longer breaks the second load
- Log messages from background work now reach the log panel
- A new variance factor clears the normal matrix and u computed with the old one
- A stray non UTF-8 byte no longer aborts the load
- A SOLUTION/ESTIMATE block out of INDEX order, and parameter lines without the ten
  standard fields, are reported in the log
- The recomputation check no longer reports PASS when the error is NaN
- The command line refuses unknown `--formats` names and exits with code 2 on a file it
  cannot parse
- Raw export adds the file extension
- The window no longer closes and reopens the first time the Stations tab is opened
- .npy exports of SITE/ID, SOLUTION/ESTIMATE and SOLUTION/APRIORI needed `allow_pickle=True` to
  load. They are now plain structured arrays with text columns

## 1.3.4

Changes:
- The datum computation no longer computes an unused pseudoinverse on every run

## 1.3.3

Fixes:
- Sigma Theta is refused when the episode filter leaves too few stations for the Helmert parameters. The message gives the episode count, the rank and the parameter count, and asks you to relax the thresholds. Earlier versions computed and exported invalid Sigma Theta, cross correlations and Helmert parameters in this case

## 1.3.2

Fixes:
- Exporting `SOLUTION/STATISTICS` as `.npy` or `.xlsx` wrote a text file with that extension. It now writes a valid `.npy` or `.xlsx` file
- The raw export tab lists the blocks in the loaded file instead of a fixed list, and updates when another file is loaded
- Discarding a loaded file no longer leaves the raw export tab empty until restart

## 1.3.1

Fixes:
- The command line statistics report reads the variance factor from `SOLUTION/STATISTICS` instead of writing `n/a`
- `-v` and `-vv` control the command line output again
- The command line no longer creates `sinex_parser.log` in the working directory
- The Sigma Theta log line no longer raises `UnicodeEncodeError` on a Windows console
- `normal` accepts files without a `SOLUTION/APRIORI` block, such as the ITRF files, and skips only the u vector and the recomputation check
- Non finite cross correlations are refused with a message that Sigma Theta has negative diagonal entries

Changes:
- Settings has log file controls (on or off, location, size limit, clear), desktop notifications, export defaults (format, folder, figure dpi), parsing defaults, and a console log level
- The export dropdowns default to NumPy (.npy)
- The standalone build works again

## 1.3.0

New features:
- Command line, run with `python -m sinex_parser.cli`, with the commands `parse`, `datum` and `normal`
- `datum` writes Sigma Theta, the cross correlations, the Helmert parameters and the statistics report, with the same file names as the window. `--plots` also saves the figures as png
- `normal` writes the normal matrix, the u vector and the apriori covariance, and prints the recomputation check. `--rank` adds the rank, which is slow at ITRF size
- `--variance-factor` sets the variance factor for files without a `SOLUTION/STATISTICS` block

Changes:
- `sinex_parser/parsers` moved to `sinex_parser/io`

## 1.2.0

Bug fixes:
- Export filenames match the filter thresholds used in the computation, even if the thresholds change afterward
- Text exports carry full float64 precision instead of 13 significant digits
- The normal matrix, apriori covariance, u vector and raw block .xlsx exports are no longer limited to 13 significant digits
- Opening the filter options with no file loaded says that no file is loaded and disables the manual option
- The filtering status updates when the filter dialog is accepted

New features:
- Settings menu, next to Select SINEX File, with theme, a log panel toggle, and remember window size, all kept between runs
- Dark theme for the interface
- Filter options and manual episode selection are in one window, with a radio pair to choose between them
- A filtering status line under the datum controls shows the thresholds the last computation used

Changes:
- The application is built on PyQt6. Requirements and the standalone build script are updated
- The covariance and datum tabs each have a log panel, and the datum text output moved into it
- The covariance buttons and the visualizer share one column, and the datum controls sit in a sidebar of the same width
- The default window is 1600 by 1000
- The stations map fills the remaining space
- Plot Matrix is next to Export Statistics Report
- The Nuitka build script no longer passes --mingw64, which Nuitka rejects on Python 3.13 and later

Known limitations:
- The dark theme covers the interface widgets only. The station map, the matplotlib figures and the pyqtgraph plots stay light
- .xlsx carries 16 significant digits, so a value that needs 17 loses about 1e-16 relative. Use .npy for exact values

## 1.1.2

New features:
- One-click setup and launch. Run `run_windows.bat` on Windows or `run_macos_linux.sh` on macOS and Linux. The first run creates a private environment and installs the dependencies, and later runs open the application directly.

Changes:
- The setup no longer uses `pip` or `setup.py`. `uv` installs the dependencies into a private environment.
- The setup reports folder paths it cannot use instead of failing.
- On Linux the setup names the system libraries it needs.
- The application no longer depends on the window it was started from.
- The license file now carries the full GPLv3 text.

## 1.1.1

- Added citation info

Bug fixes:
- Fixed typo

## 1.1.0

Bug fixes:
- Fixed widget state leaking between files so computed results now reset when a new file is loaded
- Fixed the recomputation check reporting PASS when nothing was actually compared
- Fixed stale Sigma Theta, cross correlation, and Helmert results being shown or exported after a recompute
- Previous file-parse workers no longer overwrite newer results
- A warning is now shown when the Sigma Theta diagonal contains negative entries, which makes the Helmert parameters NaN
- Sigma Theta now stops when the design matrix E is rank deficient, which numpy does not raise on
- A Helmert calculation error no longer reports itself as a cross correlation error
- The sigma filter now reports parameters with sigma 0, which it cannot flag

New features:
- Added a datum statistics report export
- Benchmarking: time and process memory for each parse and computation step, with a text export
- The app now remembers the last used folder across file open/save dialogs

Changes:
- Removed the obsolete station dropdown from the datum tab
- Centralized version number
- Removed the unused xlrd dependency
- Rank of N is no longer computed automatically with the normal matrix to guard against large files
- The station map is only redrawn when filtering actually changed it
- Old plots are closed when a new file is loaded or a new plot is generated
- Improved and cleaned up Helmert bar plot
- .xlsx export is rejected when the matrix has more columns than the format can support
- The seaborn plot is refused for very large matrices, which ran out of memory
- Text exports now carry full float64 precision. Earlier versions wrote 13 significant digits. Use .npy when a value must survive exactly.
