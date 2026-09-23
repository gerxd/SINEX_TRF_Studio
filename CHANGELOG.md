# Changelog

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
