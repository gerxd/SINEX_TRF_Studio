# SINEX TRF Studio
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.22548394.svg)](https://doi.org/10.5281/zenodo.22548394) [![Python](https://img.shields.io/badge/python-3.12-blue?logo=python&logoColor=white)](https://www.python.org/) [![Visitors](https://visitor-badge.laobi.icu/badge?page_id=gerxd.SINEX_TRF_Studio)](https://github.com/gerxd/SINEX_TRF_Studio)

SINEX TRF Studio is a PyQt6-based desktop Processing Software with Applications to Global and Regional Terrestrial Reference Frames for the SINEX File Format. The software is intended to support inspection of solution estimates, covariance structures, normal-equation recovery, and datum-related diagnostics within a single desktop workflow.

This software is developed as part of a dissertation for the International Hellenic University

## Installation

There are 2 OS-specific executables included. They install the dependencies and set an active virtual environment on first run.

Manual Install:

```bash
git clone https://github.com/gerxd/SINEX_TRF_Studio
uv venv .venv --python 3.12
uv pip install -r requirements.txt
uv run main.py
```

A public example dataset is provided at `examples/BKG08457.SNX`.

## Core Capabilities

- Import `.sinex` and `.snx` files.
- Inspect estimate, apriori, and covariance data products.
- Recover normal matrices and derived vectors.
- Perform datum-effect analysis, including SigmaTheta and Helmert-parameter uncertainty evaluation.
- Visualize station distributions through an offline map interface.
- Export parsed or computed results to `.xlsx`, `.csv`, `.txt`, and `.npy`.


## Usage Guide

1. Launch the application and click **Select SINEX File**.
2. Open a `.snx` file. 
3. Wait for parsing to finish. The header panel will show the detected active variance factor, the number of stations found, and whether an apriori covariance block is available.
4. If the variance factor is missing, use **Edit** next to **Variance Factor** to compute the normal matrix. The bundled example `examples/BKG08457.SNX` requires this.

### Typical workflow

The User Interface is split between tabs, each for a specific workflow:

- **Covariance Matrix**: inspect the loaded covariance, compute the normal matrix, compute `u = N * (Xest - Xapr)`, run the recomputation check, and export the resulting arrays.
- **Datum Effect**: calculate SigmaTheta, optionally apply a STDEV filtering pass or **manually select episodes** using the Manual Episode Selection Button, then compute cross correlations and Helmert parameters using the corresponding buttons.
- **Stations**: review station records and inspect the station map. Filtered stations can be highlighted after datum filtering. 
- **Raw export**: review the detected blocks and export a selected block as-is.
- **Info**: check version and dependency information.

### Exporting results

- Parsed blocks and computed products can be exported as `.xlsx`, `.csv`, `.txt`, or `.npy`.
- Default output names are based on the loaded SINEX filename and the selected block or computed product.
- For full precision, use `.npy` as it uses the raw float64 values.`.csv` and `.txt` are also at full float64 precision. `.xlsx` stores 16 digits.



## License and Citation

SINEX TRF Studio is distributed under the GPL-3.0 license. Citation metadata, version information, and author details are provided in `CITATION.cff`.
Use 10.5281/zenodo.22548394 for citations.

## Acknowledgements

Special thanks to professor Ampatzidis D. for his contribution to the development of this software.
