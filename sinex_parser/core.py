# core.py
import logging
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


file_handler = logging.FileHandler('sinex_parser.log')
console_handler = logging.StreamHandler()
console_handler.setFormatter(ColoredFormatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[file_handler, console_handler]
)

logger = logging.getLogger(__name__)
logging.getLogger('matplotlib.font_manager').setLevel(logging.INFO)

###############################################################################
# 2)Helper
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

###############################################################################
# 3) File Validator
###############################################################################
class SinexFileValidator:
    def validate_block_structure(self, filename: Path) -> bool:
        blk_stack = []
        try:
            with open(filename, 'r') as f:
                for line_num, line in enumerate(f, 1):
                    if line.startswith('+'):
                        blk_stack.append(line[1:].strip())
                    elif line.startswith('-'):
                        if not blk_stack:
                            logger.error(f"Unmatched block end at line {line_num}")
                            return False
                        blk_stack.pop()
            if blk_stack:
                logger.error("Unclosed blocks: " + ", ".join(blk_stack))
                return False
            return True
        except Exception as e:
            logger.error(f"Could not open file {filename}: {e}")
            return False
