# analysis/reporting.py
import numpy as np

from . import datum


def helmert_names(size):
    if size == 7:
        return ["tx", "ty", "tz", "ds", "ex", "ey", "ez"]
    if size == 14:
        return ["tx", "ty", "tz", "ds", "ex", "ey", "ez",
                "tx_v", "ty_v", "tz_v", "ds_v", "ex_v", "ey_v", "ez_v"]
    return [f"p{i + 1}" for i in range(size)]


def build_stats_report(filename, var_factor, filter_tag, sigma_theta, cross_corr,
                       helmert, sol, filtered_episodes_info, filtering_active) -> str:
    # plain text report -> collect metrics already computed by the pipeline
    lines = []
    add = lines.append

    add("SINEX TRF Studio - Datum Effect Statistics Report")
    add("=" * 52)

    add(f"File: {filename}")

    add(f"Variance factor: {var_factor if var_factor is not None else 'n/a'}")
    add(f"Filter tag: {filter_tag or 'none'}")
    add("")

    #SigmaTheta stats
    st = sigma_theta
    add("Sigma Theta (Σθ)")
    add("-" * 52)
    if st is not None:
        diag = np.diag(st)
        add(f"  shape: {st.shape[0]}x{st.shape[1]}")
        add(f"  diag min: {diag.min():.6e}")
        add(f"  diag max: {diag.max():.6e}")
        add(f"  diag mean: {diag.mean():.6e}")
        add(f"  cond(Σθ): {np.linalg.cond(st):.6e}")
        sym_err = float(np.max(np.abs(st - st.T)))
        add(f"  symmetry max|Σθ - Σθ^T|: {sym_err:.6e}")
    else:
        add("  not computed")
    add("")

    # cross-corr
    add("Cross Correlations (R)")
    add("-" * 52)
    cc = cross_corr
    if cc is not None:
        n = cc.shape[0]
        off = cc[~np.eye(n, dtype=bool)]
        add(f"  shape: {n}x{n}")
        add(f"  off-diagonal min: {off.min():.6f}")
        add(f"  off-diagonal max: {off.max():.6f}")
        add(f"  mean |off-diagonal|: {np.mean(np.abs(off)):.6f}")
        add(f"  cond(R): {np.linalg.cond(cc):.6e}")
        sym_err = float(np.max(np.abs(cc - cc.T)))
        add(f"  symmetry max|R - R^T|: {sym_err:.6e}")
    else:
        add("  not computed")
    add("")

    # Helmert 
    add("Helmert Parameters")
    add("-" * 52)
    hp = helmert
    if hp is not None:
        flat = np.asarray(hp, dtype=float).flatten()
        for name, val in zip(helmert_names(flat.size), flat):
            add(f"  {name}: {val:.6e}")
    else:
        add("  not computed")
    add("")
    add("Station Metrics")
    add("-" * 52)
    if sol:
        episodes = datum.parse_station_coordinates(sol)
        add(f"  station episodes with complete xyz: {len(episodes)}")
        add(f"  total estimated parameters: {len(sol)}")
        add("")

        add("  (σ) of the estimates, different units ")
        add("")
        add(f"    {'Type':8} {'Unit':10} {'Count':>7} {'Zero':>7} "
            f"{'Min':>13} {'Max':>13} {'Mean':>13}")
        by_type = {}
        for p in sol:
            key = (str(p.get("type", "")), str(p.get("unit", "")))
            by_type.setdefault(key, []).append(p.get("sigma", np.nan))
        for ptype, unit in sorted(by_type):
            vals = np.asarray(by_type[(ptype, unit)], dtype=float)
            finite = vals[np.isfinite(vals)]
            n_zero = int(np.count_nonzero(finite == 0.0))
            usable = finite[finite > 0.0]
            if usable.size:
                stats = (f"{usable.min():13.6e} {usable.max():13.6e} "
                         f"{usable.mean():13.6e}")
            else:
                stats = f"{'n/a':>13} {'n/a':>13} {'n/a':>13}"
            add(f"    {ptype:8} {unit:10} {finite.size:7d} {n_zero:7d} {stats}")
        add("")
        add("  0 = absent or unparseable value - not included in the min, max, and mean")
        add(" ")

    else:
        add("  no SOLUTION/ESTIMATE data")
    add("")
    add("Filtering")
    add("-" * 52)
    info = filtered_episodes_info or []
    add(f"  episodes excluded by filter: {len(info)}")
    add(f"  filtering active: {filtering_active}")
    if filter_tag and filter_tag.startswith("_manual"):
        add("  selection: manual, the auto thresholds were not applied")

    return "\n".join(lines) + "\n"
