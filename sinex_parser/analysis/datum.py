# analysis/datum.py
from typing import Dict, NamedTuple, Optional

import numpy as np

from ..core import logger


def parse_station_coordinates(solution_estimate_data):

    #take all station episodes (code, pt, soln) with complete STAX/STAY/STAZ
    #
    #  dict[label] -> {'code','pt','soln','x','y','z'}
    #format: CODE.PT.SOLN (e.g., 'SODA.A.3'), or 'CODE.PT' if soln is None.

    episodes = {}

    for p in solution_estimate_data:
        ptype = p.get("type", "")
        code = p.get("code", "")
        if ptype not in ("STAX", "STAY", "STAZ") or not code:
            continue

        pt = p.get("pt", "") or ""
        soln = p.get("soln", None)
        label = f"{code}.{pt}.{soln}" if soln is not None else f"{code}.{pt}"

        ep = episodes.get(label)
        if ep is None:
            ep = {
                "code": code,
                "pt": pt,
                "soln": soln,
                "x": None,
                "y": None,
                "z": None,
            }
            episodes[label] = ep

        v = p.get("value", 0.0)
        if ptype == "STAX":
            ep["x"] = v
        elif ptype == "STAY":
            ep["y"] = v
        elif ptype == "STAZ":
            ep["z"] = v

    # keep only complete x,y,z
    return {
        label: meta
        for label, meta in episodes.items()
        if (meta["x"] is not None and meta["y"] is not None and meta["z"] is not None)
    }

def create_matrix_Ei(x, y, z):
    #{1,0,0,x,0,z,-y}
    #{0,1,0,y,-z,0,x}
    #{0,0,1,z,y,-x,0}

    matrix = np.zeros((3, 7))
    matrix[0, 0] = 1
    matrix[1, 1] = 1
    matrix[2, 2] = 1

    matrix[0, 3] = x
    matrix[0, 5] = z
    matrix[0, 6] = -y

    matrix[1, 3] = y
    matrix[1, 4] = -z
    matrix[1, 6] = x

    matrix[2, 3] = z
    matrix[2, 4] = y
    matrix[2, 5] = -x

    return matrix

def collect_indices(sol):
    idx_map, coords, have_pos, have_vel = {}, {}, set(), set()
    for i, p in enumerate(sol):
        code, ptype = p.get("code"), p.get("type")
        if not code or not ptype: continue
        pt, soln = p.get("pt", ""), p.get("soln")
        param_key = (code, ptype, pt, soln)
        idx_map[param_key] = i
        episode_key = (code, pt, soln)
        if ptype in ("STAX", "STAY", "STAZ"):
            have_pos.add(episode_key)
            c = coords.setdefault(episode_key, {"x": None, "y": None, "z": None})
            if ptype == "STAX": c["x"] = p.get("value", 0.0)
            elif ptype == "STAY": c["y"] = p.get("value", 0.0)
            elif ptype == "STAZ": c["z"] = p.get("value", 0.0)
        if ptype in ("VELX", "VELY", "VELZ"):
            have_vel.add(episode_key)
    return idx_map, coords, have_pos, have_vel

def build_E(sol, include_vel=True, exclude_episodes=None):
    if exclude_episodes is None: exclude_episodes = set()
    idx_map, coords, have_pos, have_vel = collect_indices(sol)
    use_vel_cols = include_vel and len(have_vel - exclude_episodes) > 0
    k = 14 if use_vel_cols else 7
    rows, row_idx, used_episodes = [], [], 0
    for episode in sorted(have_pos):
        if episode in exclude_episodes: continue
        c = coords.get(episode)
        if not c or None in (c["x"], c["y"], c["z"]): continue
        x, y, z = c["x"], c["y"], c["z"]
        Ei = create_matrix_Ei(x, y, z)
        code, pt, soln = episode
        try:
            ix, iy, iz = idx_map[(code, "STAX", pt, soln)], idx_map[(code, "STAY", pt, soln)], idx_map[(code, "STAZ", pt, soln)]
        except KeyError: continue
        if k == 7: rows.append(Ei)
        else:
            Ei_pos = np.zeros((3, 14)); Ei_pos[:, :7] = Ei
            rows.append(Ei_pos)
        row_idx.extend([ix, iy, iz])
        used_episodes += 1
        if k == 14 and episode in have_vel:
            try:
                ivx, ivy, ivz = idx_map[(code, "VELX", pt, soln)], idx_map[(code, "VELY", pt, soln)], idx_map[(code, "VELZ", pt, soln)]
            except KeyError: continue
            Ei_vel = np.zeros((3, 14)); Ei_vel[:, 7:] = Ei
            rows.append(Ei_vel)
            row_idx.extend([ivx, ivy, ivz])
    if not rows: return np.zeros((0, k)), np.zeros((0,), dtype=int), k, 0
    E = np.vstack(rows)
    return E, np.asarray(row_idx, int), k, used_episodes


def detect_bad_episodes_cx(
        sol: list,
        Cx: np.ndarray,
        pos_threshold_m: float = 0.05,
        vel_threshold_m_per_y: float = 0.003,
        include_vel: bool = True,
):
    """
    Detect episodes with excessive coordinate uncertainties by examining diagonal covariance elements.

    Filters episodes where position (STAX/STAY/STAZ) or velocity (VELX/VELY/VELZ) standard deviations
    exceed specified thresholds. Uses the maximum component uncertainty for each episode.

    Returns (excluded_episodes_set, details_list).
    details_list contains dicts with episode label, sds, thresholds, and exceedance/score.
    """
    # Map parameter indices and identify which episodes have position/velocity data
    idx_map, coords, have_pos, have_vel = collect_indices(sol)

    def _sd(i: int) -> float:
        """Extract standard deviation from diagonal covariance element, clamping negatives to zero."""
        v = float(Cx[i, i])
        if v < 0.0:
            v = 0.0
        return float(np.sqrt(v))

    excluded = set()
    details = []

    # Statistics for reporting
    n_total_pos = 0
    n_excl_pos = 0
    n_considered_vel = 0
    n_excl_vel = 0

    for episode in sorted(have_pos):
        code, pt, soln = episode
        try:
            # Retrieve matrix indices for X, Y, Z position components
            ix = idx_map[(code, "STAX", pt, soln)]
            iy = idx_map[(code, "STAY", pt, soln)]
            iz = idx_map[(code, "STAZ", pt, soln)]
        except KeyError:
            continue

        n_total_pos += 1
        # Compute standard deviations from covariance diagonal
        sx, sy, sz = _sd(ix), _sd(iy), _sd(iz)
        pos_max = max(sx, sy, sz)
        pos_excess = max(0.0, pos_max - pos_threshold_m)

        # Check velocity
        vel_present = include_vel and (episode in have_vel)
        svx = svy = svz = np.nan
        vel_max = 0.0
        vel_excess = 0.0
        if vel_present:
            ivx = idx_map.get((code, "VELX", pt, soln))
            ivy = idx_map.get((code, "VELY", pt, soln))
            ivz = idx_map.get((code, "VELZ", pt, soln))
            if None not in (ivx, ivy, ivz):
                n_considered_vel += 1
                svx, svy, svz = _sd(ivx), _sd(ivy), _sd(ivz)
                vel_max = max(svx, svy, svz)
                vel_excess = max(0.0, vel_max - vel_threshold_m_per_y)

        # Determine if episode should be excluded and which component triggered it
        trig = ""
        exclude = False
        if pos_excess > 0.0:
            exclude = True
            trig = "pos"
            n_excl_pos += 1
        if vel_present and vel_excess > 0.0:
            exclude = True
            trig = "vel" if vel_excess >= pos_excess else trig
            if trig == "vel":
                n_excl_vel += 1

        if exclude:
            excluded.add(episode)

        # Compute normalized exceedance score (how many times over threshold)
        score = 0.0
        if pos_threshold_m > 0.0 and pos_excess > 0.0:
            score = max(score, pos_excess / pos_threshold_m)
        if vel_threshold_m_per_y > 0.0 and vel_excess > 0.0:
            score = max(score, vel_excess / vel_threshold_m_per_y)

        details.append(
            {
                "label": f"{code}.{pt}.{soln}",
                "code": code,
                "pt": pt,
                "soln": soln,
                "trigger": trig if exclude else "",
                "pos_max": pos_max,
                "pos_thr": pos_threshold_m,
                "pos_excess": pos_excess,
                "vel_max": vel_max if vel_present else np.nan,
                "vel_thr": vel_threshold_m_per_y if vel_present else np.nan,
                "vel_excess": vel_excess if vel_present else np.nan,
                "sx": sx,
                "sy": sy,
                "sz": sz,
                "svx": svx,
                "svy": svy,
                "svz": svz,
                "score": score if exclude else 0.0,
                "excluded": exclude,
            }
        )

    logger.info(
        "[Filter] episode filter (Cx diag): "
        f"pos_total={n_total_pos}, pos_excluded={n_excl_pos}, "
        f"vel_considered={n_considered_vel}, vel_excluded={n_excl_vel}"
    )
    return excluded, details

def build_keep_mask(sol: list, episodes_to_exclude: set, remove_vel: bool = True) -> np.ndarray:
    """
    Build boolean mask over sol est values.
    Omits STAX/STAY/STAZ/VELX/VELY/VELZ of excluded episodes.
    If remove_vel is True, omits all VELX/VELY/VELZ regardless.
    """
    station_pos = {"STAX", "STAY", "STAZ"}
    station_vel = {"VELX", "VELY", "VELZ"}
    keep = np.ones(len(sol), dtype=bool)
    filtered_pos = 0
    filtered_vel = 0

    for i, p in enumerate(sol):
        t = p.get("type", "")

        # Only check parameters that are part of an episode
        if t in station_pos or t in station_vel:
            key = (p.get("code"), p.get("pt", ""), p.get("soln"))

            # Condition 1: Exclude if the episode is in the bad list
            if key in episodes_to_exclude:
                keep[i] = False
                if t in station_pos:
                    filtered_pos += 1
                else:
                    filtered_vel += 1

            # Condition 2: ALSO exclude if it's a velocity and remove_vel is flagged
            elif remove_vel and t in station_vel:
                keep[i] = False
                filtered_vel += 1

    logger.info(
        f"[Filter] filter mask: kept={int(keep.sum())}/{len(keep)} "
        f"(filtered_pos={filtered_pos}, filtered_vel={filtered_vel})"
    )
    return keep

def detect_bad_episodes(sol, threshold=0.05):
    """
    Flag episodes where any parameter's sigma exceeds the threshold.
    Returns a set of episode keys (station_code, point_code, solution_id).
    """
    # Group parameters by episode: each episode may have multiple parameter types (STAX, STAY, etc.)
    episodes: Dict[tuple, list] = {}
    for p in sol:
        key = (p.get("code"), p.get("pt"), p.get("soln"))
        episodes.setdefault(key, []).append(p)

    excluded_episodes = set()
    logger.info(f"[Filter] filtering episodes with sigma > {threshold:.3f}")

    zero_sigma = sum(1 for p in sol if p.get("sigma", 0.0) == 0.0)
    if zero_sigma:
        message = f"[Filter] {zero_sigma} of {len(sol)} parameters have sigma 0 and cannot be flagged by this filter"
        logger.warning(message)

    # For each episode, check if any parameter exceeds the threshold and flag entire episode if any single parameter is bad
    for key, params in episodes.items():
        for p in params:
            sigma = p.get("sigma", float("inf"))
            if sigma > threshold:
                excluded_episodes.add(key)
                logger.debug(
                    f"[Filter] Flagging episode {key} for exclusion (sigma = {sigma:.3f})"
                )
                break  # No point checking remaining parameterss for this episode

    if excluded_episodes:
        logger.info(
            f"[Filter] flagged {len(excluded_episodes)} episodes for exclusion"
        )
        logger.info(
            f"[Filter] flagged {len(excluded_episodes)} episodes for exclusion"
        )
    return excluded_episodes


class DatumError(Exception):
    pass


class AppliedFilter(NamedTuple):
    enabled: bool
    pos_threshold_m: float
    vel_threshold_m_per_y: float
    manual_enabled: bool
    manual_episodes: frozenset


class SigmaThetaResult(NamedTuple):
    sigma_theta: np.ndarray
    filtered_sol: list
    row_idx: np.ndarray
    n_episodes: int


def select_excluded_episodes(sol, Cx, applied):
    # Check if manual filtering is enabled
    if applied.manual_enabled:
        # Manual mode: invert selection (selected = keep, all others = exclude)
        all_episodes = set()
        for p in sol:
            ptype = p.get("type", "")
            if ptype in ("STAX", "STAY", "STAZ", "VELX", "VELY", "VELZ"):
                episode = (p.get("code"), p.get("pt", ""), p.get("soln", ""))
                all_episodes.add(episode)

        episodes_to_exclude = all_episodes - applied.manual_episodes
        logger.info(
            f"[Filter] Manual selection: {len(applied.manual_episodes)} included, "
            f"{len(episodes_to_exclude)} excluded"
        )

        # Build details for excluded episodes (for filtered stations dialog)
        idx_map, coords, have_pos, have_vel = collect_indices(sol)
        val_map = {
            (p.get("code"), p.get("type"), p.get("pt", ""), p.get("soln")): p.get("value", np.nan)
            for p in sol
        }

        def _sd(i: Optional[int]) -> float:
            if i is None:
                return float("nan")
            v = float(Cx[i, i])
            if v < 0.0:
                v = 0.0
            return float(np.sqrt(v))

        details = []
        for ep in sorted(episodes_to_exclude):
            code, pt, soln = ep
            ix = idx_map.get((code, "STAX", pt, soln))
            iy = idx_map.get((code, "STAY", pt, soln))
            iz = idx_map.get((code, "STAZ", pt, soln))
            ivx = idx_map.get((code, "VELX", pt, soln))
            ivy = idx_map.get((code, "VELY", pt, soln))
            ivz = idx_map.get((code, "VELZ", pt, soln))

            sx, sy, sz = _sd(ix), _sd(iy), _sd(iz)
            svx, svy, svz = _sd(ivx), _sd(ivy), _sd(ivz)

            x = val_map.get((code, "STAX", pt, soln), np.nan)
            y = val_map.get((code, "STAY", pt, soln), np.nan)
            z = val_map.get((code, "STAZ", pt, soln), np.nan)
            vx = val_map.get((code, "VELX", pt, soln), np.nan)
            vy = val_map.get((code, "VELY", pt, soln), np.nan)
            vz = val_map.get((code, "VELZ", pt, soln), np.nan)

            details.append({
                "label": f"{code}.{pt}.{soln}",
                "code": code, "pt": pt, "soln": soln,
                "pos_excess": float("nan"),  # No sigma threshold in manual mode
                "vel_excess": float("nan"),
                "x": x, "y": y, "z": z,
                "vx": vx, "vy": vy, "vz": vz,
                "sx": sx, "sy": sy, "sz": sz,
                "svx": svx, "svy": svy, "svz": svz,
            })

    #Combined filtering: Cx diag + STD_DEV column
    elif applied.enabled:
        pos_thresh = applied.pos_threshold_m  # m
        vel_thresh = applied.vel_threshold_m_per_y  # m/yr

        # Cx
        episodes_cx, _ = detect_bad_episodes_cx(
            sol=sol,
            Cx=Cx,
            pos_threshold_m=pos_thresh,
            vel_threshold_m_per_y=vel_thresh,
            include_vel=True,
        )

        # STD_DEV
        pos_params = [p for p in sol if p.get("type") in ("STAX", "STAY", "STAZ")]
        vel_params = [p for p in sol if p.get("type") in ("VELX", "VELY", "VELZ")]
        episodes_sigma_pos = detect_bad_episodes(pos_params, threshold=pos_thresh)
        episodes_sigma_vel = detect_bad_episodes(vel_params, threshold=vel_thresh)
        # Union
        episodes_to_exclude = set(episodes_cx) | set(episodes_sigma_pos) | set(episodes_sigma_vel)
        # Prepare lookups for building details (parameter values and sigmas)
        idx_map, coords, have_pos, have_vel = collect_indices(sol)
        # values map
        val_map = {
            (p.get("code"), p.get("type"), p.get("pt", ""), p.get("soln")): p.get("value", np.nan)
            for p in sol
        }

        def _sd(i: Optional[int]) -> float:
            if i is None:
                return float("nan")
            v = float(Cx[i, i])
            if v < 0.0:
                v = 0.0
            return float(np.sqrt(v))

        details = []
        for ep in sorted(episodes_to_exclude):
            code, pt, soln = ep
            # sigma (from Cx)
            ix = idx_map.get((code, "STAX", pt, soln))
            iy = idx_map.get((code, "STAY", pt, soln))
            iz = idx_map.get((code, "STAZ", pt, soln))
            ivx = idx_map.get((code, "VELX", pt, soln))
            ivy = idx_map.get((code, "VELY", pt, soln))
            ivz = idx_map.get((code, "VELZ", pt, soln))

            sx, sy, sz = _sd(ix), _sd(iy), _sd(iz)
            svx, svy, svz = _sd(ivx), _sd(ivy), _sd(ivz)
            pos_max = np.nanmax([sx, sy, sz]) if not np.isnan([sx, sy, sz]).all() else float("nan")
            vel_max = np.nanmax([svx, svy, svz]) if not np.isnan([svx, svy, svz]).all() else float("nan")
            pos_excess = max(0.0, pos_max - pos_thresh) if np.isfinite(pos_max) else float("nan")
            vel_excess = max(0.0, vel_max - vel_thresh) if np.isfinite(vel_max) else float("nan")

            # parameter values from SOLUTION/ESTIMATE
            x = val_map.get((code, "STAX", pt, soln), np.nan)
            y = val_map.get((code, "STAY", pt, soln), np.nan)
            z = val_map.get((code, "STAZ", pt, soln), np.nan)
            vx = val_map.get((code, "VELX", pt, soln), np.nan)
            vy = val_map.get((code, "VELY", pt, soln), np.nan)
            vz = val_map.get((code, "VELZ", pt, soln), np.nan)

            details.append(
                {
                    "label": f"{code}.{pt}.{soln}",
                    "code": code,
                    "pt": pt,
                    "soln": soln,
                    # excesses
                    "pos_excess": pos_excess,
                    "vel_excess": vel_excess,
                    # values (sol block estimate)
                    "x": x, "y": y, "z": z,
                    "vx": vx, "vy": vy, "vz": vz,
                    # sigmas (from Cx)
                    "sx": sx, "sy": sy, "sz": sz,
                    "svx": svx, "svy": svy, "svz": svz,
                }
            )
    else:
        episodes_to_exclude, details = set(), []
        logger.info("[Filter] filtering disabled")
    return episodes_to_exclude, details


def sigma_theta_from_covariance(sol, Cx, episodes_to_exclude):
    # Create boolean mask: True = keep parameter, False = exclude parameter
    # Filters out STAX/STAY/STAZ (and optionally VELX/VELY/VELZ) for flagged episodes
    keep_mask = build_keep_mask(
        sol=sol, episodes_to_exclude=episodes_to_exclude, remove_vel=False
    )
    if keep_mask.sum() == 0:
        raise DatumError("All parameters were filtered out. Adjust thresholds.")


    if keep_mask.all():
        filtered_sol = sol
        filtered_Cx = Cx
    else:
        filtered_sol = [p for p, k in zip(sol, keep_mask) if k]
        filtered_Cx = Cx[np.ix_(keep_mask, keep_mask)]


    # filtered_sol = [p for p, k in zip(sol, keep_mask) if k] removed with v1.1
    # filtered_Cx = Cx[np.ix_(keep_mask, keep_mask)] removed with v1.1


    # Build transformation matrix E from filtered data: maps station episodes to Helmert parameters
    # row_idx identifies which rows/cols of filtered_Cx correspond to used station coordinates
    E, row_idx, kdim, n_episodes = build_E(
        filtered_sol, include_vel=True, exclude_episodes=None
    )
    if E.size == 0:
        raise DatumError("No stations remained after filtering.")
    # Extract covariance submatrix for coordinates actually used in Helmert transformation

    if row_idx.size == filtered_Cx.shape[0] and np.array_equal(
            row_idx, np.arange(filtered_Cx.shape[0])):
        Cx_sub = filtered_Cx
    else:
        Cx_sub = filtered_Cx[np.ix_(row_idx, row_idx)]

    # Cx_sub = filtered_Cx[np.ix_(row_idx, row_idx)] removed with v1.1

    logger.info(f"[Filter] episodes used after filtering: {n_episodes}")
    logger.info(f"E shape: {E.shape}, Cx_sub shape: {Cx_sub.shape}")

    # inv does not raise on a singular E^T E. Columns are scaled to unit norm
    # first because their units differ and that alone breaks matrix_rank.
    col_norms = np.linalg.norm(E, axis=0)
    col_norms[col_norms == 0.0] = 1.0
    rank_E = np.linalg.matrix_rank(E / col_norms)
    if rank_E < E.shape[1]:
        raise DatumError(
            f"The datum is underdetermined. {n_episodes} station episodes remain "
            f"after filtering, which gives E rank {rank_E} for {E.shape[1]} "
            "parameters, so E^T E is singular and Sigma Theta would be meaningless. "
            "Relax the filter thresholds so that more episodes survive."
        )

    At = E.T
    AtA = At @ E
    logger.info(f"cond(E^T E): {np.linalg.cond(AtA):.2e}")
    #print(At)
    #print("---------")
    #print(AtA)
    #print("---------")
    #print(E)
    AtCxA = (At @ Cx_sub) @ E
    AtA_inv = np.linalg.inv(AtA)
    sigma_theta = (AtA_inv @ AtCxA) @ AtA_inv
    logger.info(f"cond(sigma_theta): {np.linalg.cond(sigma_theta):.2e}")
    #if 

    # self.filtered_Cx = filtered_Cx obsolete and left substantial memory footprint - removed with v1.1
    diag = np.diag(sigma_theta)
    logger.info(
        f"Σθ diag stats: min={diag.min():.6e}, max={diag.max():.6e}, mean={diag.mean():.6e}"
    )

    negative = int(np.count_nonzero(diag < 0.0))
    if negative:
        logger.warning(f"WARNING: {negative} of {diag.size} Σθ diagonal entries are negative, the solution is degenerate and Helmert parameters will be NaN")
    return SigmaThetaResult(sigma_theta, filtered_sol, row_idx, n_episodes)


def cross_correlations(sigma_theta):
    n = sigma_theta.shape[0]
    logger.info(f"sigma theta shape: {sigma_theta.shape}")
    diagonal_elements = np.diag(sigma_theta)
    std_devs = np.sqrt(diagonal_elements)
    logger.info(
        f"std dev range: {np.min(std_devs):.6e} to {np.max(std_devs):.6e}"
    )
    cross_corr = np.zeros_like(sigma_theta)

    for i in range(n):
        for j in range(n):
            if std_devs[i] <= 1e-15 or std_devs[j] <= 1e-15:
                logger.info(
                    f"Warning: Near-zero std dev for indices {i}, {j}"
                )
            with np.errstate(divide='ignore', invalid='ignore'):
                cross_corr[i, j] = sigma_theta[i, j] / (std_devs[i] * std_devs[j])

    if not np.all(np.isfinite(cross_corr)):
        raise DatumError(
            "cross correlations are not finite. Sigma theta has negative or zero "
            "diagonal entries, so the standard deviations are not real. Tighten or "
            "relax the episode filter and calculate Sigma Theta again."
        )

    off_diagonal_mask = ~np.eye(n, dtype=bool)
    off_diagonal_values = cross_corr[off_diagonal_mask]
    min_corr = np.min(off_diagonal_values)
    max_corr = np.max(off_diagonal_values)
    mean_abs_corr = np.mean(np.abs(off_diagonal_values))

    logger.info(
        "cross correlation stats: "
        f"shape={cross_corr.shape}, min={min_corr:.6f}, "
        f"max={max_corr:.6f}, mean_abs={mean_abs_corr:.6f}"
    )
    try:
        logger.info(f"cond(R): {np.linalg.cond(cross_corr):.2e}")
    except np.linalg.LinAlgError:
        logger.info("cond(R): not available, the decomposition did not converge")
    symmetry_error = np.max(np.abs(cross_corr - cross_corr.T))
    if symmetry_error > 1e-12:
        logger.info(
            f"matrix symmetry delta {symmetry_error:.6e} (exceeds tolerance)"
        )
    else:
        logger.info(f"matrix symmetry passed check: max|R - R^T| = {symmetry_error:.6e}")
    return cross_corr


def helmert_parameters(sigma_theta):
    st_diag = np.diag(sigma_theta)
    return np.sqrt(st_diag).reshape(-1, 1)


def is_filtered(applied, filtered_episodes_info):
    if applied is None or not applied.enabled:
        return False
    if not filtered_episodes_info:
        return False
    return True


def filter_tag(applied, filtered_episodes_info):
    try:
        if is_filtered(applied, filtered_episodes_info):
            pos_mm = int(round(applied.pos_threshold_m * 1000.0))
            vel_mm = int(round(applied.vel_threshold_m_per_y * 1000.0))
            return f"_filtered_p{pos_mm}mm_v{vel_mm}mmyr"
    except Exception:
        pass
    return ""


def filter_disp(applied, filtered_episodes_info):
    try:
        if is_filtered(applied, filtered_episodes_info):
            p = applied.pos_threshold_m
            v = applied.vel_threshold_m_per_y
            return f" [filtered p={p:.3f} m, v={v:.3f} m/yr]"
    except Exception:
        pass
    return ""
