# analysis/normal.py
import time

import numpy as np

from ..core import (
    logger, benchmark, align_apriori_info_matrix, inflate_or_trim_matrix,
)


class NormalMatrixError(Exception):
    pass


def build_normal_matrix(Cov_final, var_factor, apr_data, apr_key):
    Qx = Cov_final / var_factor

    if apr_data is not None:
        final_dim = Qx.shape[0]
        apriori_matrix = inflate_or_trim_matrix(apr_data, final_dim)
        logger.info(f"Apriori covariance block found in {apr_key}, dimension={apr_data.shape}.")
        # N = inv(Qx) - inv(C0): remove apriori constraint in information space
        try:
            Qinv = np.linalg.inv(Qx)
            del Qx
            N = Qinv - align_apriori_info_matrix(apr_data, final_dim)
        except np.linalg.LinAlgError:
            logger.warning("Inversion failed during N = inv(Qx) - inv(C0) => Normal matrix not set.")
            raise NormalMatrixError("Inversion failed during N = inv(Qx) - inv(C0).")
        logger.info("Normal matrix computed as N = inv(Qx) - inv(C0).")
    else:
        apriori_matrix = None
        logger.info("No apriori covariance block found.")
        try:
            N = np.linalg.inv(Qx)
        except np.linalg.LinAlgError:
            logger.warning("Failed to invert Qx => Normal matrix not set.")
            raise NormalMatrixError("Inversion of Qx failed.")
        logger.info("Normal matrix computed as N = inv(Qx).")

    return N, apriori_matrix


def build_dx(est_data, apr_data):
    apr_lookup = {
        (p['code'], p['type'], p['pt'], p['soln']): p['value']
        for p in apr_data
    }

    n = len(est_data)
    dx = np.zeros((n, 1), dtype=float)
    unmatched_count = 0

    # make dx by going through the estimate data and matching each parameter to its a priori value.
    for i, p_est in enumerate(est_data):
        key = (p_est['code'], p_est['type'], p_est['pt'], p_est['soln'])
        val_apr = apr_lookup.get(key, 0.0)
        if key not in apr_lookup:
            unmatched_count += 1
        dx[i, 0] = p_est['value'] - val_apr

    if unmatched_count > 0:
        logger.warning(
            f"{unmatched_count} parameters in ESTIMATE were not found in APRIORI. "
            "Their a priori values were assumed to be 0."
        )

    return dx


def compute_u(N, est_data, apr_data):
    u_bench = benchmark.begin('compute u = N*dx')
    dx = build_dx(est_data, apr_data)
    u = N @ dx
    benchmark.end(u_bench)
    return u, dx


def rank_of_normal_matrix(N):
    t0 = time.time()
    with benchmark.span('rank of N (SVD)'):
        rank_n = np.linalg.matrix_rank(N)
    elapsed = time.time() - t0
    logger.info(f"rank(N): {rank_n}/{N.shape[0]}")
    logger.info(f"Rank computed in {elapsed:.3f} seconds.")
    return rank_n, elapsed


def recomputation_check(N, u, dx):
    if N is None:
        return ["Error: Normal Matrix (N) not computed yet."]
    if u is None:
        return ["Error: Vector u not computed yet."]
    if dx is None:
        return ["Error: Original dx vector not available."]

    lines = ["\n------ Performing Recomputation Check ------"]
    u_computed = u
    dx_original = dx

    # Verification: dx' = N^-1 * u  (since u = N * dx must never give dx)
    try:
        N_inv = np.linalg.inv(N)
    except np.linalg.LinAlgError:
        lines.append("Error: Cannot invert N for verification.")
        return lines

    dx_recomputed = N_inv @ u_computed
    difference_vector = dx_original - dx_recomputed
    #for informative purposes mostly
    # L2 Norm
    l2_norm_of_difference = np.linalg.norm(difference_vector)
    # Infinity Norm
    inf_norm_of_difference = np.linalg.norm(difference_vector, ord=np.inf)
    # Relative error
    norm_of_original = np.linalg.norm(dx_original)
    lines.append(f"Original dx norm: {norm_of_original:.6e}")
    lines.append(f"Recomputed dx norm: {np.linalg.norm(dx_recomputed):.6e}")
    lines.append(f"Difference Norm (L2): {l2_norm_of_difference:.6e}")
    lines.append(f"Max Absolute Difference: {inf_norm_of_difference:.6e}")
    if norm_of_original == 0:
        # if dx is zero u = N*dx is zero for any N
        lines.append(
            "recomp check is invalid because dx is zero "
        )
        return lines
    relative_error = l2_norm_of_difference / norm_of_original
    lines.append(f"Relative Error: {relative_error:.6e} (or {relative_error:.4%})")
    if not np.isfinite(relative_error) or relative_error > 1e-6:
        lines.append(
            "WARNING: relative_error > 1e-6")
    else:
        lines.append("PASS: relative_error <= 1e-6")
    return lines
