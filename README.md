# SINEX TRF Studio

SINEX TRF Studio is a PyQt5-based desktop Processing Software with Applications to Global and Regional Terrestrial Reference Frames for the SINEX File Format. The software is intended to support inspection of solution estimates, covariance structures, normal-equation recovery, and datum-related diagnostics within a single desktop workflow.

This software is developed as part of a dissertation for the International Hellenic University

## Requirements

Python 3.12 or later and `pip`.

Required Python packages: `numpy`, `pandas`, `matplotlib`, `seaborn`, `PyQt5`, `PyQtWebEngine`, `pyqtgraph`, `folium`, `openpyxl`, `xlrd`, and `plyer`.

## Installation and Execution

```bash
pip install -r sinex_parser/requirements.txt
python main.py
```

A public example dataset is provided at `examples/BKG08457.SNX`.

## Core Capabilities

- Import `.sinex` and `.snx` files.
- Inspect estimate, apriori, and covariance data products.
- Recover normal matrices and derived vectors.
- Perform datum-effect analysis, including SigmaTheta and Helmert-parameter uncertainty evaluation.
- Visualize station distributions through an offline map interface.
- Export parsed or computed results to `.xlsx`, `.csv`, `.txt`, and `.npy`.


## License and Citation

SINEX TRF Studio is distributed under the GPL-3.0 license. Citation metadata, version information, and author details are provided in `CITATION.cff`.

