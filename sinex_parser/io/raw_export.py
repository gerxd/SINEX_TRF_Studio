import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np

from .. import __version__
from ..core import logger
from .export import XLSX_MAX_CELLS, XLSX_TOO_LARGE
from .parsers import create_parsers

APP_NAME = 'SINEX TRF Studio'
BLOCK_ORDER = tuple(create_parsers())
XLSX_MAX_COLUMNS = 16384
XLSX_MAX_ROWS = 1048576
MANIFEST_NAME = 'export_manifest.json'
PARAM_HEADER = ('row', 'index', 'type', 'code', 'pt', 'soln', 'epoch', 'unit')
NPY_HEADER_BYTES = 128
MATRIX_NUMBER_BYTES = {'csv': 24, 'txt': 24, 'xlsx': 12}
XLSX_BASE_BYTES = 5000
TABLE_ROW_BYTES = {
    'SITE/ID': {'csv': 60, 'txt': 60, 'xlsx': 45, 'npy': 80},
    'parameters': {'csv': 65, 'txt': 65, 'xlsx': 55, 'npy': 135},
}


def block_kind(data):
    if isinstance(data, np.ndarray) and data.ndim == 2:
        return 'matrix'
    if isinstance(data, list):
        return 'table'
    return 'value'


def block_shape(data):
    kind = block_kind(data)
    if kind == 'matrix':
        return [int(data.shape[0]), int(data.shape[1])]
    if kind == 'table':
        return [len(data), len(data[0]) if data else 0]
    return []


def list_blocks(blocks):
    return [{'block': key, 'kind': block_kind(blocks[key]), 'shape': block_shape(blocks[key])}
            for key in BLOCK_ORDER if blocks.get(key) is not None]


def describe_shape(data):
    kind = block_kind(data)
    shape = block_shape(data)
    if kind == 'matrix':
        return f"{shape[0]} x {shape[1]}"
    if kind == 'table':
        return f"{shape[0]} rows"
    return "1 value"


def estimate_size(block, data, ext):
    kind = block_kind(data)
    if kind == 'matrix':
        if ext == 'npy':
            return data.nbytes + NPY_HEADER_BYTES, True
        base = XLSX_BASE_BYTES if ext == 'xlsx' else 0
        return data.size * MATRIX_NUMBER_BYTES[ext] + base, False
    if kind == 'table':
        per_row = TABLE_ROW_BYTES.get(block, TABLE_ROW_BYTES['parameters'])[ext]
        base = {'xlsx': XLSX_BASE_BYTES, 'npy': NPY_HEADER_BYTES}.get(ext, 0)
        return len(data) * per_row + base, False
    if ext == 'npy':
        return NPY_HEADER_BYTES + 8, True
    if ext == 'xlsx':
        return XLSX_BASE_BYTES, False
    return len(f"VARIANCE FACTOR: {data}\n"), True


def format_size(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f"{n:.0f} {unit}" if unit == 'B' else f"{n:.1f} {unit}"
        n /= 1024.0


def describe_estimate(block, data, ext):
    n, exact = estimate_size(block, data, ext)
    return format_size(n) if exact else f"about {format_size(n)}"


def xlsx_problem(data):
    kind = block_kind(data)
    if kind == 'matrix':
        if data.shape[1] > XLSX_MAX_COLUMNS:
            return (f"{data.shape[1]} columns, more than the {XLSX_MAX_COLUMNS} "
                    f"an .xlsx file can hold. Use .npy, .csv or .txt.")
        if data.size > XLSX_MAX_CELLS:
            return XLSX_TOO_LARGE
    if kind == 'table' and len(data) + 1 > XLSX_MAX_ROWS:
        return f"{len(data)} rows, more than an .xlsx sheet can hold. Use .npy, .csv or .txt."
    return None


def file_stem(source_name, block):
    return f"{Path(source_name).stem}_{block.replace('/', '_').replace(' ', '_')}"


def export_filename(source_name, block, ext):
    return f"{file_stem(source_name, block)}.{ext}"


def params_filename(source_name, block):
    return f"{file_stem(source_name, block)}_params.csv"


def planned_files(blocks, keys, ext, source_name, parameter_labels=True, manifest=True):
    names = []
    for key in keys:
        names.append(export_filename(source_name, key, ext))
        if parameter_labels and block_kind(blocks.get(key)) == 'matrix':
            names.append(params_filename(source_name, key))
    if manifest:
        names.append(MANIFEST_NAME)
    return names


def parameter_table(blocks, block):
    warnings = []
    source = 'SOLUTION/ESTIMATE'
    if 'APRIORI' in block:
        if blocks.get('SOLUTION/APRIORI'):
            source = 'SOLUTION/APRIORI'
        else:
            warnings.append(f"{block}: SOLUTION/APRIORI is missing, labels taken from SOLUTION/ESTIMATE")
    params = sorted(blocks.get(source) or [], key=lambda p: p['index'])
    n = blocks[block].shape[0]
    if len(params) != n:
        warnings.append(f"{block}: {source} has {len(params)} parameters for {n} matrix rows")
    elif [p['index'] for p in params] != list(range(1, n + 1)):
        warnings.append(f"{block}: the INDEX values of {source} are not 1 to {n}")
    return source, params, warnings


def write_parameter_labels(blocks, block, path):
    source, params, warnings = parameter_table(blocks, block)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, lineterminator='\n')
        writer.writerow(PARAM_HEADER)
        for p in params:
            row = [p['index'] - 1] + [p.get(k) for k in PARAM_HEADER[1:]]
            writer.writerow(['' if v is None else v for v in row])
    logger.info(f"Exported parameter labels from {source} => {path}")
    return len(params), warnings


def file_sha256(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        while block := f.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def _file_entry(path, block, kind, shape):
    return {'file': path.name, 'block': block, 'kind': kind, 'shape': shape,
            'size': path.stat().st_size, 'sha256': file_sha256(path)}


def write_manifest(folder, ext, source_name, source_path, written, failures, warnings):
    source = {'name': Path(source_name).name, 'size': None, 'sha256': None}
    if source_path is not None and Path(source_path).is_file():
        source['size'] = Path(source_path).stat().st_size
        source['sha256'] = file_sha256(source_path)
    manifest = {
        'app': APP_NAME,
        'version': __version__,
        'created': datetime.now().astimezone().isoformat(timespec='seconds'),
        'source': source,
        'format': ext,
        'files': written,
        'failures': failures,
        'warnings': warnings,
    }
    path = Path(folder) / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    logger.info(f"Exported manifest => {path}")
    return path


def export_blocks(blocks, keys, folder, ext, source_name, source_path=None,
                  parameter_labels=True, manifest=True):
    folder = Path(folder)
    parsers = create_parsers()
    written, failures, warnings = [], [], []
    for key in keys:
        data = blocks.get(key)
        path = folder / export_filename(source_name, key, ext)
        try:
            if data is None or key not in parsers:
                raise ValueError("not in the loaded data")
            if ext == 'xlsx' and xlsx_problem(data):
                raise ValueError(xlsx_problem(data))
            parsers[key].export(data, path, ext)
            written.append(_file_entry(path, key, block_kind(data), block_shape(data)))
        except Exception as exc:
            logger.error(f"Export of {key} failed: {type(exc).__name__}: {exc}")
            failures.append({'block': key, 'error': f"{type(exc).__name__}: {exc}"})
            continue
        if parameter_labels and block_kind(data) == 'matrix':
            labels = folder / params_filename(source_name, key)
            try:
                rows, notes = write_parameter_labels(blocks, key, labels)
                written.append(_file_entry(labels, key, 'parameter labels', [rows, len(PARAM_HEADER)]))
                for note in notes:
                    logger.warning(note)
                warnings.extend(notes)
            except Exception as exc:
                logger.error(f"Parameter labels for {key} failed: {type(exc).__name__}: {exc}")
                failures.append({'block': key, 'file': labels.name,
                                 'error': f"{type(exc).__name__}: {exc}"})
    result = {'folder': str(folder), 'format': ext, 'written': written,
              'failures': failures, 'warnings': warnings, 'manifest': None}
    if manifest:
        try:
            result['manifest'] = str(write_manifest(folder, ext, source_name, source_path,
                                                    written, failures, warnings))
        except Exception as exc:
            logger.error(f"Manifest failed: {type(exc).__name__}: {exc}")
            failures.append({'block': None, 'file': MANIFEST_NAME,
                             'error': f"{type(exc).__name__}: {exc}"})
    return result


def _cell(value):
    if value is None:
        return ''
    if isinstance(value, (float, np.floating)):
        return repr(float(value))
    return str(value)


def preview(data, rows=50, cols=8):
    kind = block_kind(data)
    if kind == 'matrix':
        r, c = min(rows, data.shape[0]), min(cols, data.shape[1])
        cells = [[_cell(v) for v in line] for line in data[:r, :c].tolist()]
        caption = f"{data.shape[0]} x {data.shape[1]} matrix, top left {r} x {c} shown"
        return [str(j + 1) for j in range(c)], [str(i + 1) for i in range(r)], cells, caption
    if kind == 'table':
        headers = list(data[0]) if data else []
        shown = data[:rows]
        cells = [[_cell(item.get(h)) for h in headers] for item in shown]
        caption = f"{len(data)} rows, first {len(shown)} shown"
        return headers, [str(i + 1) for i in range(len(shown))], cells, caption
    return ['value'], ['VARIANCE FACTOR'], [[_cell(data)]], "Single value"
