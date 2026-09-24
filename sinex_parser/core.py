# core.py
import logging
import logging.handlers
import sys
import numpy as np
from pathlib import Path

###############################################################################
# 1) Logging
###############################################################################
class ColoredFormatter(logging.Formatter):
    RED = '\033[91m'
    RESET = '\033[0m'

    def format(self, record):
        formatted = super().format(record)
        if record.levelno >= logging.WARNING:
            return f"{self.RED}{formatted}{self.RESET}"
        return formatted


try:
    sys.stderr.reconfigure(errors='replace')
    sys.stdout.reconfigure(errors='replace')
except Exception:
    pass

LOG_FILENAME = 'sinex_parser.log'


def _make_file_handler(path, max_bytes=0):
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=int(max_bytes), backupCount=1, delay=True, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    return handler


file_handler = _make_file_handler(LOG_FILENAME)
console_handler = logging.StreamHandler()
console_handler.setFormatter(ColoredFormatter('%(asctime)s - %(levelname)s - %(message)s'))

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(file_handler)
logger.addHandler(console_handler)
logger.propagate = False


def log_file_path():
    return Path(file_handler.baseFilename)


def file_logging_enabled():
    return file_handler in logger.handlers


def set_file_logging(enabled):
    if enabled and not file_logging_enabled():
        logger.addHandler(file_handler)
    elif not enabled and file_logging_enabled():
        logger.removeHandler(file_handler)


def set_log_file(path, max_bytes=0):
    global file_handler
    was_on = file_logging_enabled()
    logger.removeHandler(file_handler)
    file_handler.close()
    file_handler = _make_file_handler(path, max_bytes)
    if was_on:
        logger.addHandler(file_handler)


def clear_log_file():
    path = log_file_path()
    file_handler.close()
    if path.exists():
        path.write_text('', encoding='utf-8')

###############################################################################
# 2) Benchmarks
###############################################################################

import threading as _threading
import time as _time

_MB = 1024.0 * 1024.0


def _process_memory_mb():
    try:
        import ctypes
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [
                ('cb', wintypes.DWORD),
                ('PageFaultCount', wintypes.DWORD),
                ('PeakWorkingSetSize', ctypes.c_size_t),
                ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t),
                ('PeakPagefileUsage', ctypes.c_size_t),
            ]

        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        get_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
        get_info.restype = wintypes.BOOL
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if get_info(handle, ctypes.byref(counters), counters.cb):
            return counters.WorkingSetSize / _MB, counters.PeakWorkingSetSize / _MB
    except Exception:
        pass
    return float('nan'), float('nan')


class _BenchmarkSpan:
    __slots__ = ('name', 'generation', 't0', 'rss0')

    def __init__(self, name, generation, t0, rss0):
        self.name = name
        self.generation = generation
        self.t0 = t0
        self.rss0 = rss0


class _NullSpan:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _ActiveSpan:
    __slots__ = ('recorder', 'name', 'generation', 'token')

    def __init__(self, recorder, name, generation):
        self.recorder = recorder
        self.name = name
        self.generation = generation
        self.token = None

    def __enter__(self):
        self.token = self.recorder.begin(self.name, self.generation)
        return self

    def __exit__(self, exc_type, exc, tb):
        self.recorder.end(self.token)
        return False


class BenchmarkRecorder:

    def __init__(self):
        self.enabled = False
        self.generation = 0
        self.on_update = None
        self._lock = _threading.Lock()
        self.reset()

    def reset(self):
        self.file_name = ''
        self.file_size_bytes = 0
        self.peak_rss_mb = float('nan')
        with self._lock:
            self.spans = []

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        if not self.enabled:
            self.reset()
        self._notify()

    def start_file(self, filename: str, size_bytes: int, generation: int = None) -> None:
        self.file_name = filename
        self.file_size_bytes = size_bytes
        self.peak_rss_mb = float('nan')
        with self._lock:
            self.spans = []
            if generation is not None:
                self.generation = generation
        self._notify()

    def begin(self, name: str, generation: int = None):
        if not self.enabled:
            return None
        return _BenchmarkSpan(name, generation, _time.perf_counter(), _process_memory_mb()[0])

    def end(self, token) -> None:
        if token is None:
            return
        seconds = _time.perf_counter() - token.t0
        current, peak = _process_memory_mb()
        with self._lock:
            if token.generation is not None and token.generation != self.generation:
                return
            self.peak_rss_mb = peak
            self.spans.append({
                'name': token.name,
                'seconds': seconds,
                'rss_delta_mb': current - token.rss0,
                'rss_after_mb': current,
            })
        self._notify()

    def cancel(self, token) -> None:
        return

    def span(self, name: str, generation: int = None):
        if not self.enabled:
            return _NullSpan()
        return _ActiveSpan(self, name, generation)

    def has_data(self) -> bool:
        return bool(self.spans)

    def _notify(self) -> None:
        callback = self.on_update
        if callback is not None:
            try:
                callback()
            except Exception:
                pass

    def render_text(self) -> str:
        name_w, time_w, d_w, a_w = 40, 12, 14, 14
        header = (f'{"Operation":{name_w}} {"Time (s)":>{time_w}} '
                  f'{"RSS delta MB":>{d_w}} {"RSS after MB":>{a_w}}')
        width = len(header)
        lines = []
        add = lines.append
        add('SINEX TRF Studio - Benchmark Report')
        add('=' * width)
        add(f'File: {self.file_name or "n/a"}')
        if self.file_size_bytes:
            add(f'File size: {self.file_size_bytes} bytes '
                f'({self.file_size_bytes / _MB:.3f} MiB)')
        else:
            add('File size: n/a')
        add('')

        add(header)
        add('-' * width)
        for s in self.spans:
            delta = s['rss_delta_mb']
            after = s['rss_after_mb']
            d_txt = f'{delta:+.1f}' if delta == delta else 'n/a'
            a_txt = f'{after:.1f}' if after == after else 'n/a'
            add(f"{s['name']:{name_w}} {s['seconds']:{time_w}.3f} "
                f"{d_txt:>{d_w}} {a_txt:>{a_w}}")
        total = sum(s['seconds'] for s in self.spans)
        add('-' * width)
        add(f'{"Total (recorded spans)":{name_w}} {total:{time_w}.3f}')
        peak = self.peak_rss_mb
        add('')
        add(f'Peak for the whole process: '
            f'{peak:.1f} MB' if peak == peak else
            'Peak for the whole process: n/a')
        return '\n'.join(lines) + '\n'


benchmark = BenchmarkRecorder()

###############################################################################
# 3) File dialog directory memory
###############################################################################

import os as _os

#v1.1:keep directory on reset
_SETTINGS_ORG = 'International Hellenic University'
_SETTINGS_APP = 'SINEX TRF Studio'
_SETTINGS_KEY = 'dialogs/last_directory'

_last_dialog_dir = None


def _settings():
    try:
        from PyQt6.QtCore import QSettings
    except ImportError:
        return None
    return QSettings(_SETTINGS_ORG, _SETTINGS_APP)


def get_app_setting(key: str, default=None):
    store = _settings()
    if store is None:
        return default
    return store.value(key, default)


def set_app_setting(key: str, value) -> None:
    store = _settings()
    if store is not None:
        store.setValue(key, value)


def get_dialog_dir() -> str:
    global _last_dialog_dir
    if _last_dialog_dir is None:
        _last_dialog_dir = ''
        store = _settings()
        if store is not None:
            stored = store.value(_SETTINGS_KEY, '')
            if isinstance(stored, str) and stored and _os.path.isdir(stored):
                _last_dialog_dir = stored
    return _last_dialog_dir


def remember_dialog_dir(path: str) -> None:
    global _last_dialog_dir
    if not path:
        return
    d = _os.path.dirname(path)
    if not d:
        return
    _last_dialog_dir = d
    store = _settings()
    if store is not None:
        store.setValue(_SETTINGS_KEY, d)


def default_save_path(default_name: str) -> str:
    directory = default_export_dir() or get_dialog_dir()
    if directory:
        return _os.path.join(directory, default_name)
    return default_name


DEFAULT_EXPORT_FORMAT = 'NumPy (.npy)'


def default_export_format():
    return str(get_app_setting('export/format', DEFAULT_EXPORT_FORMAT) or DEFAULT_EXPORT_FORMAT)


def default_export_dir():
    return str(get_app_setting('export/dir', '') or '')


def figure_dpi():
    try:
        return int(get_app_setting('figures/dpi', 150) or 150)
    except (TypeError, ValueError):
        return 150


def default_pos_threshold():
    try:
        return float(get_app_setting('filter/pos_threshold', 0.050) or 0.050)
    except (TypeError, ValueError):
        return 0.050


def default_vel_threshold():
    try:
        return float(get_app_setting('filter/vel_threshold', 0.003) or 0.003)
    except (TypeError, ValueError):
        return 0.003


def default_skip_validation():
    return get_app_setting('parse/skip_validation', True) not in (False, 'false')


def default_skip_epochs():
    return get_app_setting('parse/skip_epochs', True) not in (False, 'false')


def set_console_level(level):
    console_handler.setLevel(level)


def ensure_suffix(path: str, suffix: str) -> str:
    if path.lower().endswith(suffix.lower()):
        return path
    return path + suffix


###############################################################################
# 4)Helper
###############################################################################

def _extract_active_apriori_subspace(src: np.ndarray, target_dim: int) -> tuple[np.ndarray, np.ndarray]:
    curr_dim = src.shape[0]
    cdim = min(curr_dim, target_dim)
    work = src[:cdim, :cdim]
    base_index = 0

    active_mask = np.any(work != 0.0, axis=0) | np.any(work != 0.0, axis=1)
    active_local = np.flatnonzero(active_mask)

    if active_local.size == 0:
        return np.zeros((0, 0), dtype=src.dtype), active_local

    active_global = active_local + base_index
    active_subspace = work[np.ix_(active_local, active_local)]
    return active_subspace, active_global


def inflate_or_trim_matrix(src: np.ndarray, target_dim: int) -> np.ndarray:
    curr_dim = src.shape[0]
    if curr_dim == target_dim:
        return src
    new_mat = np.zeros((target_dim, target_dim), dtype=src.dtype)
    cdim = min(curr_dim, target_dim)
    new_mat[:cdim, :cdim] = src[:cdim, :cdim]
    if curr_dim < target_dim:
        logger.info(
            f"Apriori matrix smaller ({curr_dim}x{curr_dim}); zero-padding to {target_dim}."
        )
    else:
        logger.info(
            f"Apriori matrix bigger ({curr_dim}x{curr_dim}); slicing to {target_dim}."
        )
    return new_mat


def align_apriori_info_matrix(src: np.ndarray, target_dim: int) -> np.ndarray:
    curr_dim = src.shape[0]
    active_subspace, active_global = _extract_active_apriori_subspace(src, target_dim)
    new_mat = np.zeros((target_dim, target_dim), dtype=float)

    if active_subspace.size == 0:
        logger.info("Apriori covariance contained no active entries; apriori information contribution is zero.")
        return new_mat

    info = np.linalg.inv(active_subspace)
    new_mat[np.ix_(active_global, active_global)] = info

    if active_subspace.shape[0] != min(curr_dim, target_dim):
        logger.info(
            f"Apriori information matrix built on active constrained subspace ({active_subspace.shape[0]}x{active_subspace.shape[0]}) and scattered into target dimension {target_dim}."
        )
    elif curr_dim < target_dim:
        logger.info(
            f"Apriori information matrix built from {curr_dim}x{curr_dim} covariance and zero-padded to {target_dim}."
        )
    elif curr_dim == target_dim:
        logger.info(
            f"Apriori information matrix built from full {curr_dim}x{curr_dim} covariance."
        )
    else:
        logger.info(
            f"Apriori covariance bigger ({curr_dim}x{curr_dim}); slicing to {target_dim} before inversion."
        )
    return new_mat
