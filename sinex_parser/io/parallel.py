# io/parallel.py
import multiprocessing
import os
import sys
import types
import warnings
from collections import deque

import numpy as np

PIECE_BYTES = 4 * 1024 * 1024
MIN_PARALLEL_BYTES = 64 * 1024 * 1024
MAX_WORKERS = 4
ENABLED = True

_CLS = np.full(256, 2, dtype=np.uint8)
_CLS[list(b"+-0123456789")] = 1
_CLS[[9, 10, 11, 12, 13, 32]] = 0


def worker_count():
    return max(1, min(os.cpu_count() or 1, MAX_WORKERS))


def make_executor():
    main = sys.modules["__main__"]
    sys.modules["__main__"] = types.ModuleType("__main__")
    try:
        return multiprocessing.get_context("spawn").Pool(worker_count())
    finally:
        sys.modules["__main__"] = main


def _marker(buf):
    hits = [i for i in (buf.find(b"\n-"), buf.find(b"\n+")) if i >= 0]
    return min(hits) if hits else -1


def is_large(f, start):
    f.seek(start)
    buf = b"\n" + f.read(MIN_PARALLEL_BYTES)
    return len(buf) > MIN_PARALLEL_BYTES and _marker(buf) < 0


def _next_piece(f, start):
    f.seek(start)
    chunk = f.read(PIECE_BYTES)
    hit = _marker(b"\n" + chunk)
    if hit >= 0:
        return start + hit, True
    if len(chunk) < PIECE_BYTES:
        return start + len(chunk), True
    nl = chunk.rfind(b"\n")
    if nl >= 0:
        return start + nl + 1, False
    pos = start + len(chunk)
    while True:
        chunk = f.read(PIECE_BYTES)
        if not chunk:
            return pos, True
        nl = chunk.find(b"\n")
        if nl >= 0:
            return pos + nl + 1, False
        pos += len(chunk)


def _first_line_is_fortran(f, start):
    f.seek(start)
    while True:
        raw = f.readline()
        if not raw or raw[:1] in (b"+", b"-"):
            return False
        ln = raw.decode("utf-8", errors="replace").rstrip()
        if not ln or ln[0] == "*":
            continue
        ln = ln.strip()
        if ln.startswith("%"):
            continue
        tokens = ln.split()
        if len(tokens) < 3:
            continue
        return any("d" in s or "D" in s for s in tokens[2:])


def _per_line(body, fortran):
    rows, cols, vals, header = [], [], [], []
    count = 0
    for raw in body.split(b"\n"):
        ln = raw.decode("utf-8", errors="replace").rstrip()
        if not ln:
            continue
        if ln[0] == "*":
            header.append(ln)
            continue
        ln = ln.strip()
        if ln.startswith("%"):
            continue
        tokens = ln.split()
        if len(tokens) < 3:
            continue
        r = int(tokens[0]) - 1
        c = int(tokens[1]) - 1
        count += 1
        for i, v in enumerate(tokens[2:]):
            if fortran:
                v = v.replace("D", "E").replace("d", "e")
            rows.append(r)
            cols.append(c + i)
            vals.append(float(v))
    return (np.array(rows, dtype=np.int64), np.array(cols, dtype=np.int64),
            np.array(vals, dtype=np.float64), header, count, body.count(b"\n"))


def parse_piece(path, start, end, fortran):
    with open(path, "rb") as f:
        f.seek(start)
        body = f.read(end - start)
    if not body.endswith(b"\n"):
        body += b"\n"
    a = np.frombuffer(body, dtype=np.uint8)
    nl = np.flatnonzero(a == 10)
    starts = np.concatenate(([0], nl[:-1] + 1))
    cls = _CLS[a]
    ws = cls == 0
    tok = ~ws
    tok[1:] &= ws[:-1]
    counts = np.add.reduceat(tok, starts, dtype=np.int32)
    counts[starts == nl] = 0
    tok_idx = np.flatnonzero(tok)
    first = np.zeros(starts.size, dtype=np.uint8)
    has = counts > 0
    first[has] = a[tok_idx[np.searchsorted(tok_idx, starts[has])]]
    header_lines = a[starts] == 42
    skip = header_lines | (first == 37) | (counts < 3)
    header = [body[s:e].decode("utf-8", errors="replace").rstrip() for s, e in zip(starts[header_lines], nl[header_lines])]
    header = [h for h in header if h]
    text = body
    if skip.any():
        keep = np.repeat(~skip, np.diff(np.concatenate((starts, [a.size]))))
        text = np.where(keep, a, np.uint8(32)).tobytes()
    counts = counts[~skip]
    if counts.size == 0:
        return (np.zeros(0, np.int32), np.zeros(0, np.int32), np.zeros(0), header, 0, int(nl.size))
    line_starts = starts[~skip]
    third = tok_idx[np.searchsorted(tok_idx, line_starts) + 2]
    bad = np.add.reduceat(cls == 2, np.ravel(np.column_stack((line_starts, third))), dtype=np.int32)
    if bad[::2].any():
        return _per_line(body, fortran)
    if fortran:
        text = text.replace(b"D", b"E").replace(b"d", b"e")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            flat = np.fromstring(text, dtype=np.float64, sep=" ")
    except (ValueError, DeprecationWarning):
        return _per_line(body, fortran)
    counts = counts.astype(np.int64)
    if flat.size != counts.sum():
        return _per_line(body, fortran)
    off = np.concatenate(([0], np.cumsum(counts)[:-1]))
    rows_f, cols_f = flat[off], flat[off + 1]
    k = counts - 2
    j = np.arange(k.sum()) - np.repeat(np.cumsum(k) - k, k)
    rows = np.repeat(rows_f.astype(np.int32) - 1, k)
    cols = (np.repeat(cols_f.astype(np.int64) - 1, k) + j).astype(np.int32)
    vals = flat[np.repeat(off + 2, k) + j]
    return rows, cols, vals, header, int(counts.size), int(nl.size)


def stream_block(f, path, matrix, header, executor):
    start = f.tell()
    fortran = _first_line_is_fortran(f, start)
    pos, ended = start, False
    pending = deque()

    def submit():
        nonlocal pos, ended
        if ended:
            return
        end, ended = _next_piece(f, pos)
        if end > pos:
            pending.append(executor.apply_async(parse_piece, (str(path), pos, end, fortran)))
            pos = end

    while not ended and len(pending) < 2 * worker_count():
        submit()
    lines = physical = 0
    while pending:
        rows, cols, vals, hdr, count, nlines = pending.popleft().get()
        submit()
        if (rows > cols).any() and (rows < cols).any():
            for r, c, v in zip(rows.tolist(), cols.tolist(), vals.tolist()):
                matrix[r, c] = v
                matrix[c, r] = v
        else:
            matrix[rows, cols] = vals
            matrix[cols, rows] = vals
        header.extend(hdr)
        lines += count
        physical += nlines
        del rows, cols, vals
    f.seek(pos)
    return lines, physical, fortran
