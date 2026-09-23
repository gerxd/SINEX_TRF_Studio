# io/export.py
import numpy as np
import pandas as pd

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


def write_datum_matrix(matrix, out_file, extension):
    if extension == '.xlsx':
        df = pd.DataFrame(matrix)
        df.to_excel(out_file, index=False, header=False)
    elif extension == '.csv':
        np.savetxt(out_file, matrix, delimiter=',', fmt='%.17g')
    elif extension == '.txt':
        np.savetxt(out_file, matrix, fmt='%.17g')
    elif extension == '.npy':
        np.save(out_file, matrix)
