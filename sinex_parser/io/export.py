# io/export.py
import numpy as np
import pandas as pd

XLSX_MAX_CELLS = 5_000_000
XLSX_TOO_LARGE = "Too large for .xlsx. Use .npy, .csv or .txt."

FORMAT_EXTENSIONS = {
    'Excel (.xlsx)': '.xlsx',
    'CSV (.csv)': '.csv',
    'Text (.txt)': '.txt',
    'NumPy (.npy)': '.npy',
}

FORMAT_FILTERS = {
    'Excel (.xlsx)': ('Excel Files (*.xlsx)', '.xlsx'),
    'CSV (.csv)': ('CSV Files (*.csv)', '.csv'),
    'Text (.txt)': ('Text Files (*.txt)', '.txt'),
    'NumPy (.npy)': ('NumPy Files (*.npy)', '.npy'),
}


DATUM_NAMES = ["tx", "ty", "tz", "ds", "rx", "ry", "rz"]
DATUM_UNITS = ["m", "m", "m", "-", "rad", "rad", "rad"]
DATUM_RATE_UNITS = ["m/yr", "m/yr", "m/yr", "1/yr", "rad/yr", "rad/yr", "rad/yr"]
SIGMA_THETA_NOTE = "# Entry (i, j) has the unit of row i times column j"


def datum_parameters(size):
    names = DATUM_NAMES + [f"{n}_dot" for n in DATUM_NAMES]
    return names[:size], (DATUM_UNITS + DATUM_RATE_UNITS)[:size]


def datum_table(matrix, kind):
    names, units = datum_parameters(np.shape(matrix)[0])
    if kind == "helmert_parameters":
        return ["parameter", "unit", "sigma"], [[n, u] for n, u in zip(names, units)]
    if kind == "cross_correlations":
        labels = names
    else:
        labels = [f"{n} [{u}]" for n, u in zip(names, units)]
    return ["parameter"] + labels, [[label] for label in labels]


def _field(text, sep):
    return f'"{text}"' if sep == " " and " " in text else text


def write_datum_matrix(matrix, out_file, extension, kind=None):
    if kind is not None and extension != '.npy':
        header, lead = datum_table(matrix, kind)
        if extension == '.xlsx':
            rows = [row + list(values) for row, values in zip(lead, matrix)]
            pd.DataFrame(rows, columns=header).to_excel(out_file, index=False)
            return
        sep = ',' if extension == '.csv' else ' '
        lines = [SIGMA_THETA_NOTE] if kind == "sigma_theta" and extension == '.txt' else []
        lines.append(sep.join(_field(t, sep) for t in header))
        for row, values in zip(lead, matrix):
            lines.append(sep.join([_field(t, sep) for t in row] + ['%.17g' % v for v in values]))
        with open(out_file, 'w', encoding='utf-8', newline='') as f:
            f.write('\n'.join(lines) + '\n')
        return
    if extension == '.xlsx':
        df = pd.DataFrame(matrix)
        df.to_excel(out_file, index=False, header=False)
    elif extension == '.csv':
        np.savetxt(out_file, matrix, delimiter=',', fmt='%.17g')
    elif extension == '.txt':
        np.savetxt(out_file, matrix, fmt='%.17g')
    elif extension == '.npy':
        np.save(out_file, matrix)
