# io/library.py
import gzip
import json
import os
import shutil
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np

from .. import __version__
from ..core import logger
from . import reader
from .parsers import MatrixEstimateParser
from .raw_export import file_sha256

PROGRAM_DIR = None
FOLDER_NAME = "SINEX TRF Studio library"
ENTRY = "entry.json"
BLOCKS = "blocks.json"


def is_compressed(path):
    return Path(path).suffix.lower() == ".gz"


def plain_name(path):
    path = Path(path)
    return path.stem if is_compressed(path) else path.name


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def decompress(path, folder=None):
    path = Path(path)
    tmp = Path(folder or tempfile.gettempdir()) / f".tmp-{uuid.uuid4().hex}"
    tmp.mkdir(parents=True)
    out = tmp / plain_name(path)
    total = max(path.stat().st_size, 1)
    start = time.time()
    logger.info(f"Decompressing {path.name}")
    try:
        step = 10
        with open(path, "rb") as raw, gzip.GzipFile(fileobj=raw) as src, open(out, "wb") as dst:
            while chunk := src.read(16 << 20):
                dst.write(chunk)
                done = raw.tell() * 100 // total
                if done >= step:
                    logger.info(f"Decompressing {path.name}: {done}%")
                    step = done // 10 * 10 + 10
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    logger.info(f"Decompressed {path.name} in {time.time() - start:.1f}s, {out.stat().st_size} bytes")
    return out


def read_meta(folder):
    return json.loads((Path(folder) / ENTRY).read_text(encoding="utf-8"))


def _write_meta(folder, meta):
    tmp = Path(folder) / (ENTRY + ".tmp")
    tmp.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    os.replace(tmp, Path(folder) / ENTRY)


def disk_size(folder):
    return sum(e.stat().st_size for e in os.scandir(folder) if e.is_file())


def entries(root):
    root = Path(root)
    if not root.is_dir():
        return []
    found = []
    for item in sorted(os.scandir(root), key=lambda e: e.name):
        if not item.is_dir():
            continue
        if item.name.startswith(".del-"):
            shutil.rmtree(item.path, ignore_errors=True)
            continue
        if item.name.startswith("."):
            continue
        try:
            meta = read_meta(item.path)
        except (OSError, ValueError):
            meta = None
        found.append({"folder": Path(item.path), "meta": meta, "disk_size": disk_size(item.path)})
    found.sort(key=lambda e: (e["meta"] or {}).get("added", ""))
    return found


def delete(root, name):
    root = Path(root)
    folder = root / name
    if name.startswith(".") or folder.parent != root or not folder.is_dir():
        raise ValueError(f"{name} is not a library entry")
    trash = root / f".del-{name}-{uuid.uuid4().hex[:8]}"
    folder.rename(trash)
    shutil.rmtree(trash, ignore_errors=True)


def open_entry(folder):
    folder = Path(folder)
    meta = read_meta(folder)
    stored = json.loads((folder / BLOCKS).read_text(encoding="utf-8"))
    blocks = {}
    for name in meta["blocks"]:
        file = meta["matrices"].get(name)
        blocks[name] = np.load(folder / file, mmap_mode="r") if file else stored["blocks"][name]
    return {"header": stored["header"], "blocks": blocks, "metadata": stored["metadata"]}


def log_loaded(data, name, folder, seconds):
    for block, value in data["blocks"].items():
        if isinstance(value, np.ndarray):
            logger.info(f"Loaded {block} ({'x'.join(map(str, value.shape))}).")
        elif isinstance(value, list):
            logger.info(f"Loaded {block} with {len(value)} entries.")
        else:
            logger.info(f"Loaded {block}: {value}")
    logger.info(f"Loaded {name} from the library entry {Path(folder).name} in {seconds:.3f}s, "
                f"blocks: {len(data['blocks'])}.")


def touch(folder, path=None):
    try:
        meta = read_meta(folder)
        meta["last_opened"] = _now()
        if path is not None:
            st = Path(path).stat()
            meta["source_name"] = Path(path).name
            meta["source_folder"] = str(Path(path).resolve().parent)
            meta["source_mtime_ns"] = st.st_mtime_ns
        _write_meta(folder, meta)
    except (OSError, ValueError) as exc:
        logger.debug(f"Could not update library entry {Path(folder).name}: {exc}")


def _locate(root, path):
    st = path.stat()
    for item in entries(root):
        meta = item["meta"]
        if (meta and meta.get("source_name") == path.name and meta.get("source_size") == st.st_size
                and meta.get("source_mtime_ns") == st.st_mtime_ns):
            return item["folder"]
    start = time.time()
    digest = file_sha256(path)
    logger.info(f"SHA-256 of {path.name} in {time.time() - start:.1f}s: {digest}")
    return root / digest


def _reuse(folder, path, options):
    start = time.time()
    if not (folder / ENTRY).is_file():
        if folder.exists():
            logger.warning(f"Library entry {folder.name} is incomplete, parsing the file again")
        return None
    try:
        meta = read_meta(folder)
        if meta.get("app_version") != __version__:
            logger.info(f"Library entry was written by version {meta.get('app_version')}, parsing the file again")
            return None
        if meta.get("options") != options:
            logger.info("Library entry was parsed with other options, parsing the file again")
            return None
        data = open_entry(folder)
    except Exception as exc:
        logger.warning(f"Library entry {folder.name} could not be read ({exc}), parsing the file again")
        return None
    touch(folder, path)
    log_loaded(data, path.name, folder, time.time() - start)
    return data


def _write(folder, data, path, options):
    from ..analysis import datum
    blocks = data["blocks"]
    matrices, plain = {}, {}
    for k, (name, value) in enumerate(blocks.items()):
        if isinstance(value, np.memmap):
            value.flush()
            matrices[name] = Path(value.filename).name
        elif isinstance(value, np.ndarray):
            matrices[name] = f"block{k}.npy"
            np.save(folder / matrices[name], value)
        else:
            plain[name] = value
    (folder / BLOCKS).write_text(json.dumps(
        {"header": data["header"], "metadata": data["metadata"], "blocks": plain}), encoding="utf-8")
    st = path.stat()
    sol = blocks.get("SOLUTION/ESTIMATE") or []
    now = _now()
    meta = {
        "app_version": __version__,
        "source_name": path.name,
        "source_folder": str(path.resolve().parent),
        "compressed": is_compressed(path),
        "sha256": folder.name,
        "source_size": st.st_size,
        "source_mtime_ns": st.st_mtime_ns,
        "parameters": len(sol),
        "station_episodes": len(datum.parse_station_coordinates(sol)),
        "blocks": list(blocks),
        "matrices": matrices,
        "sizes": {name: int(v.nbytes) for name, v in blocks.items() if isinstance(v, np.ndarray)},
        "disk_size": disk_size(folder),
        "options": options,
        "added": now,
        "last_opened": now,
    }
    _write_meta(folder, meta)
    logger.info(f"Library entry written for {path.name}, {meta['disk_size']} bytes")


def _discard(folder):
    if folder.exists():
        trash = folder.with_name(f".del-{folder.name}-{uuid.uuid4().hex[:8]}")
        folder.rename(trash)
        shutil.rmtree(trash, ignore_errors=True)


def load(path, parsers, root=None, keep=False, status=None, **options):
    path = Path(path)
    stored = {"skip_validation": bool(options.get("skip_validation")),
              "check_structure": bool(options.get("check_structure")),
              "skip_epochs_block": bool(options.get("skip_epochs_block"))}
    folder = None
    if keep and root is not None:
        try:
            root = Path(root)
            root.mkdir(parents=True, exist_ok=True)
            folder = _locate(root, path)
            data = _reuse(folder, path, stored)
            if data is not None:
                if status is not None:
                    status["reused"] = True
                return data, folder
            _discard(folder)
            folder.mkdir()
        except Exception as exc:
            logger.warning(f"The library is not used for {path.name}: {exc}")
            folder = None
    source = decompress(path, root) if is_compressed(path) else path
    try:
        data = reader.parse_sinex_file(source, parsers, matrix_dir=folder, name=path.name, **options)
        if folder is not None:
            try:
                _write(folder, data, path, stored)
            except Exception as exc:
                logger.warning(f"Library entry for {path.name} not written: {exc}")
                return data, None
            data = None
            return open_entry(folder), folder
        return data, None
    except BaseException:
        for parser in parsers.values():
            if isinstance(parser, MatrixEstimateParser):
                parser._matrix = None
        if folder is not None:
            try:
                _discard(folder)
            except OSError:
                pass
        raise
    finally:
        if source is not path:
            shutil.rmtree(source.parent, ignore_errors=True)
