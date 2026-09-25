# analysis/reporting.py
import datetime
import gzip
import hashlib
import json
import math
import platform
import textwrap
from pathlib import Path

import numpy as np

from .. import __version__
from . import datum

R_EARTH = 6378137.0
MM_AT_SURFACE = R_EARTH * 1e3
MAS_PER_RAD = 180.0 / math.pi * 3600.0 * 1000.0
BLOCKS = (("translation", 0, 3), ("scale", 3, 4), ("rotation", 4, 7))
CHUNK = 2048
LITERATURE_NAMES = ["tx", "ty", "tz", "rx", "ry", "rz", "ds"]
LITERATURE_ORDER = [0, 1, 2, 4, 5, 6, 3]
CONVENTION = (
    "Rotations follow the IERS sign convention. The literature order is tx, ty, tz, rx, ry, "
    "rz, ds, then the same rates, where rx, ry, rz are ex, ey, ez here. The paper control "
    "comparison uses that order and the opposite rotation sign, which flips the sign of every "
    "correlation between a rotation and another parameter and leaves the sigmas unchanged."
)


def helmert_names(size):
    if size == 7:
        return ["tx", "ty", "tz", "ds", "ex", "ey", "ez"]
    if size == 14:
        return ["tx", "ty", "tz", "ds", "ex", "ey", "ez",
                "tx_v", "ty_v", "tz_v", "ds_v", "ex_v", "ey_v", "ez_v"]
    return [f"p{i + 1}" for i in range(size)]


def block_traces(sigma_theta):
    diag = np.diag(sigma_theta)
    traces = {}
    for offset, suffix in ((0, ""), (7, "_rate")):
        if offset >= diag.size:
            break
        for name, start, stop in BLOCKS:
            traces[name + suffix] = float(diag[offset + start:offset + stop].sum())
    return traces


def _used_rows(sol, excluded):
    keep = datum.build_keep_mask(sol=sol, episodes_to_exclude=excluded, remove_vel=False)
    filtered_sol = [p for p, k in zip(sol, keep) if k]
    E, row_idx, k, n_episodes = datum.build_E(filtered_sol, include_vel=True, exclude_episodes=None)
    return E, np.flatnonzero(keep)[row_idx], filtered_sol, row_idx


def si_units(size):
    units = ["m", "m", "m", "1", "rad", "rad", "rad"]
    return (units + [f"{u}/yr" for u in units])[:size]


def literature_units(size):
    rows = []
    for i in range(size):
        rate = "/yr" if i >= 7 else ""
        base = i % 7
        if base < 3:
            rows.append(("mm" + rate, 1e3, False))
        elif base == 3:
            rows.append(("ppb" + rate, 1e9, True))
        else:
            rows.append(("mas" + rate, MAS_PER_RAD, True))
    return rows


def _stats(values):
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return {"count": 0, "min": None, "median": None, "mean": None, "max": None}
    return {"count": int(values.size), "min": float(values.min()), "median": float(np.median(values)),
            "mean": float(values.mean()), "max": float(values.max())}


def _sd(Cx, i):
    return float(np.sqrt(max(float(Cx[i, i]), 0.0)))


def _header_line(source, header):
    for line in header or []:
        if line.startswith("%=SNX"):
            return line
    if source is None or not Path(source).is_file():
        return None
    with open(source, "rb") as f:
        magic = f.read(2)
    opener = gzip.open if magic == b"\x1f\x8b" else open
    with opener(source, "rb") as f:
        line = f.readline(200).decode("ascii", errors="replace").rstrip()
    return line if line.startswith("%=SNX") else None


def _source_info(filename, source, header):
    info = {"name": filename or (Path(source).name if source else ""), "size": None,
            "sha256": None, "sinex_version": None, "agency": None, "created": None,
            "data_agency": None, "data_start": None, "data_end": None}
    if source is not None and Path(source).is_file():
        digest = hashlib.sha256()
        with open(source, "rb") as f:
            while chunk := f.read(1 << 24):
                digest.update(chunk)
        info.update(size=Path(source).stat().st_size, sha256=digest.hexdigest())
    line = _header_line(source, header)
    if line:
        keys = ("sinex_version", "agency", "created", "data_agency", "data_start", "data_end")
        info.update(dict(zip(keys, line.split()[1:7])))
    return info


def _environment():
    blas = np.show_config("dicts").get("Build Dependencies", {}).get("blas", {})
    return {"application": __version__, "python": platform.python_version(),
            "numpy": np.__version__, "blas": f"{blas.get('name')} {blas.get('version')}",
            "date": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}


def _filter_info(applied, details):
    tag = datum.filter_tag(applied, details)
    excluded = len(details or [])
    if applied is None or not (applied.enabled or applied.manual_enabled):
        return {"mode": "none", "tag": tag, "excluded": excluded}
    if applied.manual_enabled:
        return {"mode": "manual", "tag": tag, "episodes_chosen": len(applied.manual_episodes),
                "excluded": excluded}
    return {"mode": "auto", "tag": tag, "position_threshold_m": applied.pos_threshold_m,
            "velocity_threshold_m_per_yr": applied.vel_threshold_m_per_y, "excluded": excluded}


def _kind(ptype):
    if ptype in ("STAX", "STAY", "STAZ"):
        return "position"
    if ptype in ("VELX", "VELY", "VELZ"):
        return "velocity"
    return None


def _largest(values):
    values = np.asarray(values, dtype=float)
    return None if np.isnan(values).all() else float(np.nanmax(values))


def _exclusions(sol, applied, details, labels=None):
    sigmas = {}
    for p in sol:
        kind = _kind(p.get("type"))
        if kind:
            key = (p.get("code"), p.get("pt"), p.get("soln"), kind)
            sigmas[key] = max(sigmas.get(key, 0.0), p.get("sigma", 0.0))
    rows = []
    for d in details or []:
        pos = _largest([d.get(k, np.nan) for k in ("sx", "sy", "sz")])
        vel = _largest([d.get(k, np.nan) for k in ("svx", "svy", "svz")])
        reasons, trigger = [], None
        if applied.manual_enabled:
            reasons.append("not in the manual selection")
        else:
            if pos is not None and pos > applied.pos_threshold_m:
                reasons.append("position sigma")
                trigger = pos
            if vel is not None and vel > applied.vel_threshold_m_per_y:
                reasons.append("velocity sigma")
                trigger = vel if trigger is None else trigger
            if not reasons:
                key = (d["code"], d["pt"], d["soln"])
                for kind, limit in (("position", applied.pos_threshold_m),
                                    ("velocity", applied.vel_threshold_m_per_y)):
                    value = sigmas.get(key + (kind,))
                    if value is not None and value > limit:
                        reasons.append(f"STD_DEV {kind} sigma")
                        trigger = value if trigger is None else trigger
        rows.append({"episode": d["label"], "reason": ", ".join(reasons), "sigma": trigger,
                     "position_sigma": pos, "velocity_sigma": vel})
        if labels:
            rows[-1].update(labels.get((d["code"], d["pt"], d["soln"])) or {"span": "", "break": ""})
    return rows


def _covariance_symmetry(Cx, rows):
    worst = 0.0
    for start in range(0, rows.size, CHUNK):
        part = rows[start:start + CHUNK]
        worst = max(worst, float(np.max(np.abs(Cx[np.ix_(part, rows)] - Cx[np.ix_(rows, part)].T))))
    return worst


def _helmert_rows(helmert, unfiltered):
    flat = np.asarray(helmert, dtype=float).ravel()
    same = unfiltered is not None and unfiltered.size == flat.size
    rows = []
    for i, (name, si_unit, (unit, factor, surface)) in enumerate(
            zip(helmert_names(flat.size), si_units(flat.size), literature_units(flat.size))):
        value = flat[i]
        other = float(unfiltered[i]) if same else None
        rows.append({
            "name": name, "si_unit": si_unit, "si": float(value),
            "unit": unit, "value": float(value * factor),
            "surface_mm": float(value * MM_AT_SURFACE) if surface else None,
            "si_unfiltered": other,
            "ratio": float(value / other) if same and other != 0.0 else None,
        })
    return rows


def _literature_order(helmert):
    flat = np.asarray(helmert, dtype=float).ravel()
    order = LITERATURE_ORDER + ([i + 7 for i in LITERATURE_ORDER] if flat.size == 14 else [])
    rows = []
    for position, i in enumerate(order):
        rate = position >= 7
        factor = 1e3 if i % 7 < 3 else MM_AT_SURFACE
        rows.append({"name": LITERATURE_NAMES[position % 7] + ("_v" if rate else ""),
                     "unit": "mm/yr" if rate else "mm", "sigma": float(flat[i] * factor)})
    return rows


def _geometry(sol, Cx, excluded, n_episodes, applied, details, labels=None):
    E, orig, filtered_sol, row_idx = _used_rows(sol, excluded)
    norms = np.linalg.norm(E, axis=0)
    norms[norms == 0.0] = 1.0
    scaled = E / norms
    xyz, pos_codes, vel_codes = [], set(), set()
    for i in range(0, row_idx.size, 3):
        p = filtered_sol[row_idx[i]]
        if p.get("type") == "STAX":
            xyz.append([filtered_sol[row_idx[i + j]]["value"] for j in range(3)])
            pos_codes.add(p.get("code"))
        else:
            vel_codes.add(p.get("code"))
    xyz = np.asarray(xyz, dtype=float)
    lat = np.degrees(np.arctan2(xyz[:, 2], np.hypot(xyz[:, 0], xyz[:, 1])))
    lon = np.degrees(np.arctan2(xyz[:, 1], xyz[:, 0]))
    unit = xyz / np.linalg.norm(xyz, axis=1)[:, None]
    return orig, {
        "episodes_used": int(n_episodes),
        "stations_used": len(pos_codes),
        "stations_with_velocities": len(vel_codes),
        "rows_of_E": int(E.shape[0]),
        "columns_of_E": int(E.shape[1]),
        "rank_E": int(np.linalg.matrix_rank(scaled)),
        "cond_EtE": float(np.linalg.cond(E.T @ E)),
        "cond_EtE_column_scaled": float(np.linalg.cond(scaled.T @ scaled)),
        "north": int(np.count_nonzero(lat >= 0.0)), "south": int(np.count_nonzero(lat < 0.0)),
        "east": int(np.count_nonzero(lon >= 0.0)), "west": int(np.count_nonzero(lon < 0.0)),
        "latitude_range_deg": [float(lat.min()), float(lat.max())],
        "longitude_range_deg": [float(lon.min()), float(lon.max())],
        "unit_vector_eigenvalues": np.linalg.eigvalsh(unit.T @ unit / unit.shape[0]).tolist(),
        "excluded_episodes": _exclusions(sol, applied, details, labels),
    }


def _precision(sol, Cx, excluded, orig):
    by_type = {}
    for p in sol:
        key = (str(p.get("type", "")), str(p.get("unit", "")))
        by_type.setdefault(key, []).append(p.get("sigma", np.nan))
    table = []
    for ptype, unit in sorted(by_type):
        vals = np.asarray(by_type[(ptype, unit)], dtype=float)
        finite = vals[np.isfinite(vals)]
        row = {"type": ptype, "unit": unit, "count": int(finite.size),
               "zero": int(np.count_nonzero(finite == 0.0))}
        row.update({k: v for k, v in _stats(finite[finite > 0.0]).items() if k != "count"})
        table.append(row)
    used = set(orig.tolist())
    groups = {"used_position": [], "used_velocity": [], "excluded_position": [], "excluded_velocity": []}
    for i, p in enumerate(sol):
        kind = _kind(p.get("type"))
        if kind is None:
            continue
        if i in used:
            groups[f"used_{kind}"].append(_sd(Cx, i))
        elif (p.get("code"), p.get("pt", ""), p.get("soln")) in excluded:
            groups[f"excluded_{kind}"].append(_sd(Cx, i))
    diag = np.array([Cx[i, i] for i in orig], dtype=float)
    return {
        "sigma_by_type": table,
        "covariance_sigmas": {k: _stats(v) for k, v in groups.items()},
        "covariance_rows_used": {"rows": int(orig.size), "smallest_diagonal": float(diag.min()),
                                 "non_positive_diagonal": int(np.count_nonzero(diag <= 0.0)),
                                 "symmetry_error": _covariance_symmetry(Cx, orig)},
    }


def build_diagnostics(sol, Cx, applied, excluded, details, result, cross_corr, helmert,
                      filename="", source=None, header=None, labels=None):
    st = result.sigma_theta
    names = helmert_names(st.shape[0])
    episodes = datum.parse_station_coordinates(sol)
    epochs = {}
    for i in result.row_idx:
        p = result.filtered_sol[i]
        if p.get("type") in ("STAX", "STAY", "STAZ"):
            epochs[p.get("epoch")] = epochs.get(p.get("epoch"), 0) + 1

    unfiltered, unfiltered_error = None, None
    if not excluded:
        unfiltered = np.asarray(helmert, dtype=float).ravel()
    else:
        try:
            unfiltered = datum.helmert_parameters(
                datum.sigma_theta_from_covariance(sol, Cx, set()).sigma_theta).ravel()
        except (datum.DatumError, np.linalg.LinAlgError) as exc:
            unfiltered_error = str(exc)

    orig, geometry = _geometry(sol, Cx, excluded, result.n_episodes, applied, details, labels)
    iu = np.triu_indices(cross_corr.shape[0], 1)
    pairs = cross_corr[iu]
    top = np.argsort(-np.abs(pairs), kind="stable")[:10]
    diag = np.diag(st)
    eigenvalues = np.linalg.eigvalsh(st)
    return {
        "report": "SINEX TRF Studio diagnostics report",
        "provenance": {
            "source": _source_info(filename, source, header),
            "parameters": len(sol),
            "stations": len({e["code"] for e in episodes.values()}),
            "station_episodes": len(episodes),
            "environment": _environment(),
            "reference_epochs": epochs,
            "filter": _filter_info(applied, details),
        },
        "conventions": CONVENTION,
        "sigma_theta": {
            "names": names,
            "matrix": st.tolist(),
            "diagonal": diag.tolist(),
            "condition_number": float(np.linalg.cond(st)),
            "symmetry_error": float(np.max(np.abs(st - st.T))),
            "eigenvalues": eigenvalues.tolist(),
            "smallest_eigenvalue": float(eigenvalues[0]),
            "negative_diagonal": int(np.count_nonzero(diag < 0.0)),
            "block_traces": block_traces(st),
        },
        "cross_correlations": {
            "matrix": cross_corr.tolist(),
            "largest_pairs": [{"a": names[iu[0][i]], "b": names[iu[1][i]], "r": float(pairs[i])}
                              for i in top],
            "pairs_above_0_5": int(np.count_nonzero(np.abs(pairs) > 0.5)),
            "pairs_above_0_9": int(np.count_nonzero(np.abs(pairs) > 0.9)),
        },
        "helmert": {
            "values": np.asarray(helmert, dtype=float).ravel().tolist(),
            "parameters": _helmert_rows(helmert, unfiltered),
            "unfiltered_error": unfiltered_error,
            "earth_radius_m": R_EARTH,
        },
        "literature_order": _literature_order(helmert),
        "network": geometry,
        "input_precision": _precision(sol, Cx, excluded, orig),
    }


def _clean(value):
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    return value


def to_json(report):
    return json.dumps(_clean(report), indent=1, allow_nan=False) + "\n"


def _g(value):
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _matrix(add, names, matrix):
    add("  " + " " * 6 + "".join(f"{n:>14}" for n in names))
    for name, row in zip(names, matrix):
        add(f"  {name:6}" + "".join(f"{_g(v):>14}" for v in row))


def render_text(report):
    lines = []
    add = lines.append

    def section(title):
        add("")
        add(title)
        add("-" * 72)

    prov = report["provenance"]
    src, env, flt = prov["source"], prov["environment"], prov["filter"]
    add(report["report"])
    add("=" * 72)
    section("Provenance")
    add(f"  file: {src['name']}")
    add(f"  size: {_g(src['size'])} bytes")
    add(f"  SHA-256: {_g(src['sha256'])}")
    add(f"  SINEX header: agency {_g(src['agency'])}, data agency {_g(src['data_agency'])}, "
        f"data span {_g(src['data_start'])} to {_g(src['data_end'])}")
    add(f"  parameters: {prov['parameters']}, stations: {prov['stations']}, "
        f"station episodes: {prov['station_episodes']}")
    add(f"  SINEX TRF Studio {env['application']}, Python {env['python']}, numpy {env['numpy']}, "
        f"BLAS {env['blas']}")
    add(f"  date: {env['date']}")
    epochs = prov["reference_epochs"]
    add("  reference epochs of the positions used: "
        + ", ".join(f"{e} ({n})" for e, n in sorted(epochs.items(), key=lambda kv: str(kv[0]))))
    if len(epochs) > 1:
        add(f"  WARNING: the positions used have {len(epochs)} different reference epochs. "
            "The datum model assumes one shared epoch.")
    if flt["mode"] == "auto":
        add(f"  filter: auto, position {flt['position_threshold_m']:.6g} m, velocity "
            f"{flt['velocity_threshold_m_per_yr']:.6g} m/yr, {flt['excluded']} episodes excluded")
    elif flt["mode"] == "manual":
        add(f"  filter: manual, {flt['episodes_chosen']} episodes chosen, {flt['excluded']} excluded")
    else:
        add("  filter: none")
    add(f"  filter tag: {flt['tag'] or 'none'}")
    add("")
    add(textwrap.fill(report["conventions"], 90, initial_indent="  ", subsequent_indent="  "))

    st = report["sigma_theta"]
    names = st["names"]
    section("Sigma Theta")
    _matrix(add, names, st["matrix"])
    add("  diagonal: " + " ".join(_g(v) for v in st["diagonal"]))
    add(f"  condition number: {_g(st['condition_number'])}")
    add(f"  symmetry error max|S - S^T|: {_g(st['symmetry_error'])}")
    add("  eigenvalues: " + " ".join(_g(v) for v in st["eigenvalues"]))
    add(f"  smallest eigenvalue: {_g(st['smallest_eigenvalue'])}")
    add(f"  negative diagonal entries: {st['negative_diagonal']}")
    add("  block traces: " + ", ".join(f"{k} {_g(v)}" for k, v in st["block_traces"].items()))

    cc = report["cross_correlations"]
    section("Cross correlations")
    _matrix(add, names, cc["matrix"])
    add("  largest |r| between different parameters:")
    for p in cc["largest_pairs"]:
        add(f"    {p['a']:>5} {p['b']:<5} {_g(p['r']):>10}")
    add(f"  pairs with |r| > 0.5: {cc['pairs_above_0_5']}, |r| > 0.9: {cc['pairs_above_0_9']}")

    hp = report["helmert"]
    section("Helmert parameter sigmas")
    add(f"  {'name':6} {'SI':>12} {'unit':6} {'unfiltered':>12} {'ratio':>9}   "
        f"{'literature':>12} {'unit':7} {'at surface':>12}")
    for r in hp["parameters"]:
        per_year = "/yr" if r["unit"].endswith("/yr") else ""
        surface = f"{_g(r['surface_mm']):>12} mm{per_year}" if r["surface_mm"] is not None else ""
        add(f"  {r['name']:6} {_g(r['si']):>12} {r['si_unit']:6} {_g(r['si_unfiltered']):>12} "
            f"{_g(r['ratio']):>9}   {_g(r['value']):>12} {r['unit']:7} {surface}")
    add(f"  at surface: multiplied by the equatorial radius, {hp['earth_radius_m']:.0f} m; "
        "rates are per year")
    if hp["unfiltered_error"]:
        add(f"  unfiltered Sigma Theta not available: {hp['unfiltered_error']}")
    add("")
    add("  literature order, mm and mm/yr at the surface:")
    add("  " + " ".join(f"{r['name']:>9}" for r in report["literature_order"]))
    add("  " + " ".join(f"{_g(r['sigma']):>9}" for r in report["literature_order"]))

    net = report["network"]
    section("Network geometry")
    add(f"  episodes used: {net['episodes_used']}, stations: {net['stations_used']}, "
        f"stations with velocities: {net['stations_with_velocities']}")
    add(f"  E: {net['rows_of_E']} x {net['columns_of_E']}, rank {net['rank_E']} (column scaled)")
    add(f"  cond(E^T E): {_g(net['cond_EtE'])}, column scaled: {_g(net['cond_EtE_column_scaled'])}")
    add(f"  episodes north {net['north']}, south {net['south']}, east {net['east']}, west {net['west']}")
    lat, lon = net["latitude_range_deg"], net["longitude_range_deg"]
    add(f"  geocentric latitude {_g(lat[0])} to {_g(lat[1])} deg, longitude {_g(lon[0])} to {_g(lon[1])} deg")
    add("  eigenvalues of the mean outer product of the station unit vectors: "
        + " ".join(_g(v) for v in net["unit_vector_eigenvalues"]))
    add(f"  excluded episodes: {len(net['excluded_episodes'])}")
    for e in net["excluded_episodes"]:
        line = f"    {e['episode']:16} {e['reason']:32} {_g(e['sigma']):>12}"
        extra = "  ".join(t for t in (e.get("span"), e.get("break")) if t)
        add(f"{line}  {extra}" if extra else line)

    pr = report["input_precision"]
    section("Input precision")
    add(f"  {'type':8} {'unit':10} {'count':>7} {'zero':>7} {'min':>12} {'median':>12} "
        f"{'mean':>12} {'max':>12}")
    for r in pr["sigma_by_type"]:
        add(f"  {r['type']:8} {r['unit']:10} {r['count']:7d} {r['zero']:7d} {_g(r['min']):>12} "
            f"{_g(r['median']):>12} {_g(r['mean']):>12} {_g(r['max']):>12}")
    add("  zero sigmas are left out of min, median, mean and max")
    add("")
    add("  covariance sigmas, used against excluded episodes:")
    add(f"    {'':18} {'count':>7} {'min':>12} {'median':>12} {'mean':>12} {'max':>12}")
    for name, s in pr["covariance_sigmas"].items():
        add(f"    {name.replace('_', ' '):18} {s['count']:7d} {_g(s['min']):>12} {_g(s['median']):>12} "
            f"{_g(s['mean']):>12} {_g(s['max']):>12}")
    cov = pr["covariance_rows_used"]
    add(f"  covariance rows used: {cov['rows']}, smallest diagonal {_g(cov['smallest_diagonal'])}, "
        f"non positive diagonal {cov['non_positive_diagonal']}, symmetry error {_g(cov['symmetry_error'])}")
    return "\n".join(lines) + "\n"


def report_name(stem, tag):
    return f"{stem}_diagnostics{tag}.txt"


def write_report(report, txt_path):
    txt_path = Path(txt_path)
    json_path = txt_path.with_suffix(".json")
    txt_path.write_text(render_text(report), encoding="utf-8")
    json_path.write_text(to_json(report), encoding="utf-8")
    return [str(txt_path), str(json_path)]
