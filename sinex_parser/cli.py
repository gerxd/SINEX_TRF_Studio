# cli.py
import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

from . import __version__
from .analysis import datum, normal, reporting
from .core import logger, ensure_suffix
from .io import create_parsers, export, reader

COVA_KEYS = ("SOLUTION/MATRIX_ESTIMATE L COVA", "SOLUTION/MATRIX_ESTIMATE U COVA")
APRIORI_KEYS = ("SOLUTION/MATRIX_APRIORI L COVA", "SOLUTION/MATRIX_APRIORI U COVA")


class CliError(Exception):
    pass


def _configure_logging(verbosity):
    level = logging.WARNING if verbosity == 0 else logging.INFO if verbosity == 1 else logging.DEBUG
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)


def _load(path):
    path = Path(path)
    if not path.is_file():
        raise CliError(f"no such file: {path}")
    return reader.parse_sinex_file(path, create_parsers())


def _first_block(blocks, keys):
    for key in keys:
        value = blocks.get(key)
        if value is not None:
            return key, value
    return None, None


def _require_cova(blocks):
    key, Cx = _first_block(blocks, COVA_KEYS)
    if Cx is None:
        raise CliError("no SOLUTION/MATRIX_ESTIMATE COVA block in this file")
    return Cx


def _require_estimate(blocks):
    sol = blocks.get("SOLUTION/ESTIMATE")
    if not sol:
        raise CliError("no SOLUTION/ESTIMATE block in this file")
    return sol


def _applied_filter(args):
    if args.no_filter:
        return datum.AppliedFilter(False, args.pos_threshold, args.vel_threshold, False, frozenset())
    return datum.AppliedFilter(True, args.pos_threshold, args.vel_threshold, False, frozenset())


def _num(value):
    value = float(value)
    return value if np.isfinite(value) else None


def _print_json(summary):
    print(json.dumps(summary, indent=2))


def _formats(text):
    formats = [f.strip().lstrip(".").lower() for f in text.split(",") if f.strip()]
    unknown = [f for f in formats if f not in ("xlsx", "csv", "txt", "npy")]
    if unknown:
        raise CliError(f"unknown format: {', '.join(unknown)}")
    return formats


def _write(matrix, out_dir, stem, prefix, tag, formats):
    written = []
    if "xlsx" in formats and np.size(matrix) > export.XLSX_MAX_CELLS:
        raise CliError(export.XLSX_TOO_LARGE)
    for fmt in formats:
        extension = f".{fmt}"
        out_file = ensure_suffix(str(Path(out_dir) / f"{stem}_{prefix}{tag}{extension}"), extension)
        export.write_datum_matrix(matrix, out_file, extension)
        written.append(out_file)
    return written


def cmd_parse(args):
    data = _load(args.file)
    blocks = data["blocks"]
    if args.json:
        summary = {"command": "parse", "version": __version__,
                   "file": data["metadata"]["filename"], "blocks": {}}
        for name, value in blocks.items():
            if isinstance(value, np.ndarray):
                summary["blocks"][name] = {"shape": list(value.shape), "dtype": str(value.dtype)}
            elif isinstance(value, list):
                summary["blocks"][name] = {"entries": len(value)}
            else:
                summary["blocks"][name] = {"value": _num(value) if isinstance(value, (int, float)) else repr(value)}
        sol = blocks.get("SOLUTION/ESTIMATE")
        if sol:
            summary["parameters"] = len(sol)
            summary["station_episodes"] = len(datum.parse_station_coordinates(sol))
        _print_json(summary)
        return 0
    print(f"file: {data['metadata']['filename']}")
    print(f"blocks: {len(blocks)}")
    for name in blocks:
        value = blocks[name]
        if isinstance(value, np.ndarray):
            shape = "x".join(str(d) for d in value.shape)
            print(f"  {name}: array {shape} {value.dtype}")
        elif isinstance(value, list):
            print(f"  {name}: {len(value)} entries")
        else:
            print(f"  {name}: {value!r}")
    sol = blocks.get("SOLUTION/ESTIMATE")
    if sol:
        print(f"parameters: {len(sol)}")
        print(f"station episodes with complete xyz: {len(datum.parse_station_coordinates(sol))}")
    return 0


def cmd_datum(args):
    data = _load(args.file)
    blocks = data["blocks"]
    Cx = _require_cova(blocks)
    sol = datum.index_ordered(_require_estimate(blocks), Cx.shape[0])

    applied = _applied_filter(args)
    excluded, details = datum.select_excluded_episodes(sol, Cx, applied)
    result = datum.sigma_theta_from_covariance(sol, Cx, excluded)
    cross_corr = datum.cross_correlations(result.sigma_theta)
    helmert = datum.helmert_parameters(result.sigma_theta)

    tag = datum.filter_tag(applied, details)
    stem = Path(data["metadata"]["filename"]).stem
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    formats = _formats(args.formats)

    written = []
    written += _write(result.sigma_theta, out_dir, stem, "sigma_theta", tag, formats)
    written += _write(cross_corr, out_dir, stem, "cross_correlations", tag, formats)
    written += _write(helmert, out_dir, stem, "helmert_parameters", tag, formats)

    report = reporting.build_stats_report(
        data["metadata"]["filename"], blocks.get("SOLUTION/STATISTICS"), tag,
        result.sigma_theta, cross_corr,
        helmert, sol, details, datum.is_filtered(applied, details),
    )
    report_file = out_dir / f"{stem}_datum_stats{tag}.txt"
    report_file.write_text(report, encoding="utf-8")
    written.append(str(report_file))

    if args.plots:
        import matplotlib
        matplotlib.use("Agg")
        from .io import figures
        written += figures.save_datum_figures(
            out_dir, stem, tag, result.sigma_theta, cross_corr, helmert,
            datum.filter_disp(applied, details))

    if args.json:
        diag = np.diag(result.sigma_theta)
        _print_json({
            "command": "datum", "version": __version__,
            "file": data["metadata"]["filename"],
            "filtered": datum.is_filtered(applied, details),
            "episodes_excluded": len(details),
            "episodes_used": result.n_episodes,
            "negative_sigma_theta_diagonal": int(np.count_nonzero(diag < 0.0)),
            "helmert": {name: _num(v) for name, v in
                        zip(reporting.helmert_names(helmert.size), helmert.ravel())},
            "written": [str(name) for name in written],
        })
        return 0
    print(f"episodes excluded: {len(details)}")
    print(f"episodes used: {result.n_episodes}")
    print(f"sigma theta: {result.sigma_theta.shape[0]}x{result.sigma_theta.shape[1]}")
    for name in written:
        print(f"wrote {name}")
    return 0


def cmd_normal(args):
    data = _load(args.file)
    blocks = data["blocks"]
    Cx = _require_cova(blocks)

    var_factor = args.variance_factor
    if var_factor is None:
        var_factor = blocks.get("SOLUTION/STATISTICS")
    if var_factor is None:
        raise CliError(
            "this file has no SOLUTION/STATISTICS block, so there is no variance "
            "factor to read. Pass --variance-factor explicitly."
        )

    apr_key, apr_data = _first_block(blocks, APRIORI_KEYS)
    if apr_key is None:
        apr_key = APRIORI_KEYS[0]
    N, apriori = normal.build_normal_matrix(Cx, var_factor, apr_data, apr_key)

    est_data = _require_estimate(blocks)
    apr_est = blocks.get("SOLUTION/APRIORI")
    u = dx = None
    if not apr_est:
        if not args.json:
            print("no SOLUTION/APRIORI block, skipping the u vector and the recomputation check")
    elif N.shape[0] != len(est_data):
        raise CliError("dimension mismatch between the normal matrix and the parameter count")
    else:
        u, dx = normal.compute_u(
            N, datum.index_ordered(est_data, N.shape[0], normal.NormalMatrixError), apr_est)

    stem = Path(data["metadata"]["filename"]).stem
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    formats = _formats(args.formats)

    written = _write(N, out_dir, stem, "NormalMatrix", "", formats)
    if u is not None:
        written += _write(u, out_dir, stem, "u_Vector", "", formats)
    if apriori is not None:
        written += _write(apriori, out_dir, stem, "Covariance_Apriori_Matrix", "", formats)

    check = normal.recomputation_check(N, u, dx) if u is not None else None
    rank_n = elapsed = None
    if args.rank:
        rank_n, elapsed = normal.rank_of_normal_matrix(N)
    if args.json:
        summary = {"command": "normal", "version": __version__,
                   "file": data["metadata"]["filename"],
                   "variance_factor": _num(var_factor), "n": N.shape[0],
                   "recomputation_check": [ln.strip() for ln in check] if check else None,
                   "written": [str(name) for name in written]}
        if rank_n is not None:
            summary["rank"] = int(rank_n)
            summary["rank_deficiency"] = N.shape[0] - int(rank_n)
        _print_json(summary)
        return 0
    print(f"variance factor: {var_factor}")
    print(f"N: {N.shape[0]}x{N.shape[1]}")
    if check is not None:
        for line in check:
            print(line)
    if rank_n is not None:
        print(f"rank(N) = {rank_n} of {N.shape[0]}, deficiency {N.shape[0] - int(rank_n)}, {elapsed:.3f} s")
    for name in written:
        print(f"wrote {name}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="sinex-trf",
        description="SINEX TRF Studio without the desktop window.")
    parser.add_argument("--version", action="version", version=f"SINEX TRF Studio {__version__}")
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help="log progress to stderr, twice for debug")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("parse", help="report the blocks a file contains")
    p.add_argument("file")
    p.add_argument("--json", action="store_true", help="print the summary as JSON")
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("datum", help="sigma theta, cross correlations and Helmert parameters")
    p.add_argument("file")
    p.add_argument("--out", default=".", help="directory to write products into")
    p.add_argument("--formats", default="csv",
                   help="comma separated list of xlsx, csv, txt, npy")
    p.add_argument("--pos-threshold", type=float, default=0.05,
                   help="position sigma threshold in metres")
    p.add_argument("--vel-threshold", type=float, default=0.003,
                   help="velocity sigma threshold in metres per year")
    p.add_argument("--no-filter", action="store_true", help="use every episode")
    p.add_argument("--plots", action="store_true", help="also save figures as png")
    p.add_argument("--json", action="store_true", help="print the summary as JSON")
    p.set_defaults(func=cmd_datum)

    p = sub.add_parser("normal", help="normal matrix, u vector and the recomputation check")
    p.add_argument("file")
    p.add_argument("--out", default=".", help="directory to write products into")
    p.add_argument("--formats", default="csv",
                   help="comma separated list of xlsx, csv, txt, npy")
    p.add_argument("--variance-factor", type=float, default=None,
                   help="required for files with no SOLUTION/STATISTICS block")
    p.add_argument("--rank", action="store_true",
                   help="also compute rank(N) by SVD, which costs O(n^3)")
    p.add_argument("--json", action="store_true", help="print the summary as JSON")
    p.set_defaults(func=cmd_normal)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    try:
        return args.func(args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (datum.DatumError, normal.NormalMatrixError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
