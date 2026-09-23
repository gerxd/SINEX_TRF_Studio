# io/figures.py
from pathlib import Path

import numpy as np

from ..core import figure_dpi

FOOTER = "produced with SINEX TRF Studio - Dossas G. - IHU"

HELMERT_LABELS_7 = ["tx", "ty", "tz", "δs", "εx", "εy", "εz"]
HELMERT_LABELS_14 = HELMERT_LABELS_7 + ["tx_v", "ty_v", "tz_v", "δs_v", "εx_v", "εy_v", "εz_v"]


def axis_labels(matrix):
    if matrix.shape[0] == 14:
        return list(HELMERT_LABELS_14)
    if matrix.shape[0] == 7:
        return list(HELMERT_LABELS_7)
    return []


def format_cbar_correlation(ax, decimals=2, major_step=0.2, minor_div=2):
    from matplotlib.ticker import FormatStrFormatter, MultipleLocator
    if not ax.collections:
        return
    cbar = ax.collections[0].colorbar
    if cbar is None:
        return
    cbar.set_label("Correlation coefficient r", rotation=90, labelpad=12)
    ticks = np.arange(-1.0, 1.0 + 0.5 * major_step, major_step)
    cbar.set_ticks(ticks)
    cbar.formatter = FormatStrFormatter(f"%.{decimals}f")
    if minor_div > 1:
        minor_step = major_step / minor_div
        cbar.ax.yaxis.set_minor_locator(MultipleLocator(minor_step))
        cbar.ax.minorticks_on()
    cbar.update_ticks()


def format_cbar_sigma(ax, decimals=3, nbins=12, minor_div=2):
    from matplotlib.ticker import FormatStrFormatter, AutoMinorLocator
    if not ax.collections:
        return
    cbar = ax.collections[0].colorbar
    if cbar is None:
        return

    cbar.set_label("Variance", rotation=90, labelpad=12)
    mappable = ax.collections[0]
    vmin, vmax = mappable.get_clim()
    if vmin == vmax:
        vmax = vmin + 1e-12
    ticks = np.linspace(vmin, vmax, nbins)
    cbar.set_ticks(ticks)
    cbar.formatter = FormatStrFormatter(f"%.{decimals}e")
    if minor_div > 1:
        cbar.ax.minorticks_on()
        cbar.ax.yaxis.set_minor_locator(AutoMinorLocator(minor_div))
    cbar.update_ticks()


def build_heatmap(matrix, kind, title, labels=None):
    import matplotlib.pyplot as plt
    import seaborn as sns

    if labels is None:
        labels = axis_labels(matrix)
    fig, ax = plt.subplots(figsize=(12, 10))

    if kind == "correlation":
        sns.heatmap(
            matrix,
            ax=ax,
            cmap="RdBu_r",
            center=0,
            vmin=-1,
            vmax=1,
            square=True,
            cbar=True,
            cbar_kws={"label": "Correlation coefficient r"},
            xticklabels=labels[: matrix.shape[1]] if labels else "auto",
            yticklabels=labels[: matrix.shape[0]] if labels else "auto",
        )
        ax.set_xticklabels(ax.get_xticklabels(), rotation=45, ha="right")
        ax.set_yticklabels(ax.get_yticklabels(), rotation=0)
        format_cbar_correlation(ax)
    elif kind == "sigma":
        sns.heatmap(
            matrix,
            ax=ax,
            cmap="inferno",
            square=True,
            cbar=True,
            cbar_kws={"label": "Variance"},
            xticklabels=labels[: matrix.shape[1]] if labels else "auto",
            yticklabels=labels[: matrix.shape[0]] if labels else "auto",
        )
        ax.set_xticklabels(ax.get_xticklabels(), rotation=45, ha="right")
        ax.set_yticklabels(ax.get_yticklabels(), rotation=0)
        format_cbar_sigma(ax)
    else:
        sns.heatmap(matrix, ax=ax, cmap='viridis', annot=True, fmt='.2e', square=True)

    ax.set_title(f"{title}\nShape: {matrix.shape}")
    return fig, ax


def build_helmert_bar(values, title, subtitle=None):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    arr = np.asarray(values, dtype=float).flatten()
    if arr.size == 0:
        raise ValueError("No Helmert parameters available for plotting")

    if arr.size == 7:
        labels = list(HELMERT_LABELS_7)
    elif arr.size == 14:
        labels = list(HELMERT_LABELS_14)
    else:
        labels = [f"p{i + 1}" for i in range(arr.size)]

    # Display only, stored values stay in SI. Rotation and scale times the Earth
    # radius give the displacement at the surface, so all bars read in mm.
    conv_val = 6378137000.0  # GRS80 semi-major axis in mm
    GROUPS = {
        "translation": (1e3, "mm", "#4c72b0"),
        "scale": (conv_val, "mm", "#dd8452"),
        "rotation": (conv_val, "mm", "#55a868"),
        "other": (1.0, "", "#8172b3"),
    }

    def group_of(label):
        base = label.split("_")[0]
        if base in ("tx", "ty", "tz"):
            return "translation"
        if base in ("δs",):
            return "scale"
        if base in ("εx", "εy", "εz"):
            return "rotation"
        return "other"

    groups = [group_of(l) for l in labels]
    scaled = np.array([arr[i] * GROUPS[g][0] for i, g in enumerate(groups)])
    colors = [GROUPS[g][2] for g in groups]
    rate = [l.endswith("_v") for l in labels]
    tick_labels = [
        f"{l}\n[{GROUPS[g][1]}{'/yr' if r else ''}]" if GROUPS[g][1] else l
        for l, g, r in zip(labels, groups, rate)
    ]

    fig, ax = plt.subplots(figsize=(16, 10))
    ax.format_coord = lambda x, y: ""  # blank the toolbar cursor readout
    bars = ax.bar(tick_labels, scaled, color=colors)
    ax.grid(axis="y", linestyle=":", linewidth=0.6, color="gray")
    ax.set_axisbelow(True)
    ax.axhline(0.0, color="black", linewidth=0.8)
    present = [g for g in GROUPS if g in groups]
    if len(present) > 1:
        ax.legend(
            handles=[Patch(facecolor=GROUPS[g][2],
                           label=f"{g} [{GROUPS[g][1]}]" if GROUPS[g][1] else g)
                     for g in present],
            loc="best",
        )
    if subtitle:
        ax.set_title(f"{title}\n{subtitle}")
    else:
        ax.set_title(title)
    ax.bar_label(bars, fmt="%.4g", padding=6)
    fig.tight_layout()
    return fig, ax


def add_footer(fig):
    fig.text(0.99, 0.01, FOOTER, ha="right", va="bottom",
             fontsize=8, style="italic", color="gray")


def save_datum_figures(out_dir, stem, tag, sigma_theta, cross_corr, helmert, disp_tag=""):
    import matplotlib.pyplot as plt

    out_dir = Path(out_dir)
    written = []

    for matrix, kind, prefix, title in (
        (sigma_theta, "sigma", "sigma_theta", "Sigma Theta Matrix Heatmap"),
        (cross_corr, "correlation", "cross_correlations", "Cross Correlation Matrix Heatmap"),
    ):
        fig, _ = build_heatmap(matrix, kind, f"{title}{disp_tag}")
        fig.tight_layout()
        add_footer(fig)
        path = out_dir / f"{stem}_{prefix}{tag}.png"
        fig.savefig(path, dpi=figure_dpi())
        plt.close(fig)
        written.append(str(path))

    fig, _ = build_helmert_bar(helmert, f"Helmert Parameters{disp_tag}",
                               f"Shape: {helmert.shape}")
    add_footer(fig)
    path = out_dir / f"{stem}_helmert_parameters{tag}.png"
    fig.savefig(path, dpi=figure_dpi())
    plt.close(fig)
    written.append(str(path))

    return written
