# Patch Notes

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
