# Sample runs: COE08827 and CRI23527

These are the expected results of the User Manual walkthrough (Chapters 3 and 4), from SINEX TRF Studio 1.5.2 on macOS 27 (Apple silicon), with Python 3.12.14 and NumPy 2.5.3. 

The input files are COE08827.SNX (CODE, GPS week 882) and CRI23527.SNX (IGN Costa Rica, SIRGAS, GPS week 2352).

Each file has the following outputs:
- `*_normal.json` holds the normal-matrix run. COE08827 uses `--variance-factor 0.00000467` and CRI23527 uses the variance factor of the file. The JSON gives the rank of N and the recomputation check (PASS).
- `normal/` holds N, u and the a priori covariance matrix as CSV.
- `datum_unfiltered/` holds the diagnostics report, with Σθ, R and the Helmert parameter sigmas as CSV.
- `datum_filtered_1mm/` (COE08827 only) holds the same products with the position threshold at 1 mm, which excludes 15 of 37 episodes.

The window cannot filter CRI23527, because its position threshold has a resolution of 1 mm and all CRI23527 sigmas are below 1 mm.
