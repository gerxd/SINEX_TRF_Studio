# analysis/normal.py
import time

import numpy as np

from ..core import (
    logger, benchmark, apriori_info_subspace, inflate_or_trim_matrix,
)


class NormalMatrixError(Exception):
    pass


def build_normal_matrix(Cov_final, var_factor, apr_data, apr_key):
    Qx = Cov_final / var_factor

    apriori_info = None
    if apr_data is not None:
        final_dim = Qx.shape[0]
        apriori_matrix = inflate_or_trim_matrix(apr_data, final_dim)
        logger.info(f"Apriori covariance block found in {apr_key}, dimension={apr_data.shape}.")
        try:
            N = np.linalg.inv(Qx)
            del Qx
            info, idx = apriori_info_subspace(apr_data, final_dim)
            if idx.size:
                P0_sub = info * var_factor
                N[np.ix_(idx, idx)] -= P0_sub
                apriori_info = (P0_sub, idx)
        except np.linalg.LinAlgError:
            logger.warning("Inversion failed during N = inv(Qx) - inv(Q0), Q0 = C0/var_factor => Normal matrix not set.")
            raise NormalMatrixError("Inversion failed during N = inv(Qx) - inv(Q0), Q0 = C0/var_factor.")
        logger.info("Normal matrix computed as N = inv(Qx) - inv(Q0), Q0 = C0/var_factor.")
    else:
        apriori_matrix = None
        logger.info("No apriori covariance block found.")
        try:
            N = np.linalg.inv(Qx)
        except np.linalg.LinAlgError:
            logger.warning("Failed to invert Qx => Normal matrix not set.")
            raise NormalMatrixError("Inversion of Qx failed.")
        logger.info("Normal matrix computed as N = inv(Qx).")

    return N, apriori_matrix, apriori_info


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


def compute_u(N, est_data, apr_data, P0=None):
    u_bench = benchmark.begin('compute u = inv(Qx)*dx')
    dx = build_dx(est_data, apr_data)
    u = N @ dx
    if P0 is not None:
        P0_sub, idx = P0
        u[idx] += P0_sub @ dx[idx]
    benchmark.end(u_bench)
    return u, dx


def apriori_order_matches(est_data, apr_est):
    def keys(rows):
        ordered = sorted(rows, key=lambda p: p.get("index", 0))
        return [(p.get("code"), p.get("type"), p.get("pt"), p.get("soln")) for p in ordered]
    return all(e == a for e, a in zip(keys(est_data), keys(apr_est)))


def rank_of_normal_matrix(N):
    t0 = time.time()
    with benchmark.span('rank of N (SVD)'):
        rank_n = np.linalg.matrix_rank(N)
    elapsed = time.time() - t0
    logger.info(f"rank(N): {rank_n}/{N.shape[0]}")
    logger.info(f"Rank computed in {elapsed:.3f} seconds.")
    return rank_n, elapsed


def recomputation_check(N, u, dx, P0=None):
    if N is None:
        return ["Error: Normal Matrix (N) not computed yet."]
    if u is None:
        return ["Error: Vector u not computed yet."]
    if dx is None:
        return ["Error: Original dx vector not available."]

    lines = ["\n------ Performing Recomputation Check ------"]
    u_computed = u
    dx_original = dx

    # Verification: dx' = inv(N + P0) * u, with u = inv(Qx) * dx and inv(Qx) = N + P0
    block = None
    if P0 is not None:
        P0_sub, idx = P0
        block = np.ix_(idx, idx)
        saved = N[block]
        N[block] += P0_sub
    try:
        N_inv = np.linalg.inv(N)
    except np.linalg.LinAlgError:
        lines.append("Error: Cannot invert N for verification.")
        return lines
    finally:
        if block is not None:
            N[block] = saved

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
