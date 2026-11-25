#!/usr/bin/python3 -s
"""Gera o consolidado completo de tabelas e gráficos (report_assets/) a partir de
results_baseline/, results_opt_nivel1/ e results_opt_nivel2/.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
sys.path = [p for p in sys.path if "/.local/" not in p]

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
except ImportError as exc:
    print(
        f"ERRO: dependências gráficas ausentes ({exc}). "
        "Execute com o Python do sistema: /usr/bin/python3 -s generate_report.py",
        file=sys.stderr,
    )
    sys.exit(1)

BASE_DIR = Path(__file__).resolve().parent
DIR_BASE = BASE_DIR / "results_baseline"
DIR_N1 = BASE_DIR / "results_opt_nivel1"
DIR_N2 = BASE_DIR / "results_opt_nivel2"
OUT_DIR = BASE_DIR / "report_assets"

COLOR_DB = "#2563EB"
COLOR_APP = "#F59E0B"
COLOR_BASE = "#64748B"
COLOR_N1 = "#2563EB"
COLOR_N2_HASH = "#0EA5E9"
COLOR_N2_ZM = "#059669"
COLOR_REDIS = "#DC2626"
COLOR_CASS = "#1D4ED8"

plt.rcParams.update(
    {
        "font.size": 10,
        "axes.titlesize": 11.5,
        "axes.titleweight": "bold",
        "axes.labelsize": 10,
        "axes.edgecolor": "#CBD5E1",
        "axes.linewidth": 0.9,
        "grid.color": "#E2E8F0",
        "grid.linestyle": "--",
        "grid.linewidth": 0.7,
    }
)


def load_json(path: Path) -> dict | list:
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def fmt_pt(val: float, dec: int = 2) -> str:
    s = f"{val:,.{dec}f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def load_test_metrics(path: Path) -> dict | None:
    if not path.is_file():
        return None
    d = load_json(path)
    s = d["summary"]
    return {
        "db_ms": s["t_db_ms"]["mean"],
        "app_ms": s["t_app_ms"]["mean"],
        "total_ms": s["t_total_ms"]["mean"],
        "median_ms": s["t_total_ms"]["median"],
        "stdev_ms": s["t_total_ms"]["stdev"],
        "min_ms": s["t_total_ms"]["min"],
        "max_ms": s["t_total_ms"]["max"],
        "repeat": d.get("repeat", 0),
        "warmup": d.get("warmup", 0),
        "validated": d.get("validated", False),
        "rows_returned": d.get("rows_returned", 0),
    }


def parse_tablestats(path: Path) -> dict[str, dict[str, str]]:
    """Extrai métricas por tabela de `nodetool tablestats`."""
    tables: dict[str, dict[str, str]] = {}
    if not path.is_file():
        return tables
    current: str | None = None
    keys = (
        "SSTable count",
        "Space used (live)",
        "Space used (total)",
        "Number of partitions (estimate)",
        "Compacted partition minimum bytes",
        "Compacted partition maximum bytes",
        "Compacted partition mean bytes",
    )
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("Table:"):
            current = line.split(":", 1)[1].strip()
            tables[current] = {}
            continue
        if current is None:
            continue
        for k in keys:
            prefix = f"{k}:"
            if line.startswith(prefix):
                tables[current][k] = line.split(":", 1)[1].strip()
                break
    return tables


def check_integrity() -> tuple[list[dict], bool]:
    """Valida todos os output_*.json em results_baseline, N1 e N2 contra ref_q{1,2,3}.json."""
    refs: dict[str, tuple[bytes, object]] = {}
    for q in ("q1", "q2", "q3"):
        rp = DIR_BASE / f"ref_{q}.json"
        raw = rp.read_bytes()
        refs[q] = (raw, json.loads(raw.decode("utf-8")))

    rows: list[dict] = []
    all_ok = True
    specs = [
        ("Baseline", DIR_BASE),
        ("Nível 1", DIR_N1),
        ("Nível 2", DIR_N2),
    ]
    for phase, dpath in specs:
        for out_file in sorted(dpath.glob("output_*.json")):
            m = re.search(r"_(q[123])(?:_|\.json)", out_file.name)
            if not m:
                continue
            q = m.group(1)
            ref_raw, ref_obj = refs[q]
            out_raw = out_file.read_bytes()
            out_obj = json.loads(out_raw.decode("utf-8"))
            bytes_eq = out_raw == ref_raw
            json_eq = out_obj == ref_obj

            test_name = "test_" + out_file.name[len("output_") :]
            test_path = dpath / test_name
            validated_flag = None
            if test_path.is_file():
                validated_flag = bool(load_json(test_path).get("validated"))
            ok = json_eq and (validated_flag is True)
            if not ok:
                all_ok = False
            rows.append(
                {
                    "phase": phase,
                    "output_file": out_file.name,
                    "ref_file": f"ref_{q}.json",
                    "bytes_eq": bytes_eq,
                    "json_eq": json_eq,
                    "validated": validated_flag,
                    "ok": ok,
                }
            )
    return rows, all_ok


def style_axis(ax, ylabel: str | None = None, grid_axis: str = "y") -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis=grid_axis, zorder=0)
    ax.set_axisbelow(True)
    if ylabel:
        ax.set_ylabel(ylabel)


def plot_fig1_q1_q2(metrics: dict[str, dict]) -> None:
    panels = [
        ("Q1 (Agrupamento 90 dias)", "q1"),
        ("Q2 full (Todas ofertas)", "q2_full"),
        ("Q2 min (Pré-filtrado menor custo)", "q2_min"),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(14.5, 5.2), dpi=300)

    for ax, (title, qkey) in zip(axes, panels):
        items = [
            ("Redis\nBase", f"base_redis_{qkey}", None),
            ("Redis\nN1", f"n1_redis_{qkey}", f"base_redis_{qkey}"),
            ("Cass.\nBase", f"base_cass_{qkey}", None),
            ("Cass.\nN1", f"n1_cass_{qkey}", f"base_cass_{qkey}"),
        ]
        labels, db_vals, app_vals, tot_vals, base_refs = [], [], [], [], []
        for lbl, key, bref in items:
            m = metrics[key]
            labels.append(lbl)
            db_vals.append(m["db_ms"])
            app_vals.append(m["app_ms"])
            tot_vals.append(m["total_ms"])
            base_refs.append(metrics[bref]["total_ms"] if bref else None)

        x = np.arange(len(labels))
        w = 0.56
        ax.bar(x, db_vals, width=w, color=COLOR_DB, label="t_db (Banco)", zorder=3)
        ax.bar(
            x,
            app_vals,
            width=w,
            bottom=db_vals,
            color=COLOR_APP,
            label="t_app (Aplicação)",
            zorder=3,
        )

        max_y = max(tot_vals)
        ax.set_ylim(0, max_y * 1.28)
        for xi, tot, bref in zip(x, tot_vals, base_refs):
            dec = 2 if qkey == "q2_min" else 1
            txt = f"{fmt_pt(tot, dec)} ms"
            if bref is not None:
                txt += f"\n({fmt_pt(bref / tot, 2)}×)"
            ax.text(
                xi,
                tot + max_y * 0.025,
                txt,
                ha="center",
                va="bottom",
                fontsize=8.5,
                fontweight="bold",
                color="#1E293B",
            )

        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9)
        ax.set_title(title, pad=10)
        style_axis(ax, "Tempo médio (ms)" if ax is axes[0] else None)

    handles, leg_labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        leg_labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.94),
        ncol=2,
        frameon=False,
        fontsize=10,
    )
    fig.suptitle(
        "Q1 e Q2 (full e min): Baseline vs Nível 1 — Decomposição t_db + t_app",
        fontsize=13,
        fontweight="bold",
        y=0.99,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    fig.savefig(OUT_DIR / "fig1_q1_q2_comparison.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_fig2_q3_evolution(metrics: dict[str, dict], has_b4_zm: bool) -> None:
    fig, (ax_r, ax_c) = plt.subplots(
        1, 2, figsize=(15.5, 5.8), dpi=300, gridspec_kw={"width_ratios": [1, 2.75]}
    )

    # Redis Q3 (ms)
    r_specs = [
        ("Baseline", "base_redis_q3", COLOR_BASE, None),
        ("Nível 1", "n1_redis_q3", COLOR_N1, "base_redis_q3"),
        ("Nível 2\n(Zone Map)", "n2_redis_q3_zm", COLOR_N2_ZM, "base_redis_q3"),
    ]
    rx = np.arange(len(r_specs))
    r_vals = [metrics[k]["total_ms"] for _, k, _, _ in r_specs]
    r_cols = [c for _, _, c, _ in r_specs]
    ax_r.bar(rx, r_vals, width=0.56, color=r_cols, zorder=3)
    max_r = max(r_vals)
    ax_r.set_ylim(0, max_r * 1.24)
    for xi, (_, _, _, bref), val in zip(rx, r_specs, r_vals):
        lbl = f"{fmt_pt(val, 1)} ms"
        if bref:
            lbl += f"\n({fmt_pt(metrics[bref]['total_ms'] / val, 2)}×)"
        ax_r.text(
            xi,
            val + max_r * 0.025,
            lbl,
            ha="center",
            va="bottom",
            fontsize=9,
            fontweight="bold",
            color="#0F172A",
        )
    ax_r.set_xticks(rx)
    ax_r.set_xticklabels([lbl for lbl, _, _, _ in r_specs], fontsize=9.5)
    ax_r.set_title("(a) Redis Q3 (ms)", pad=10)
    style_axis(ax_r, "Tempo total médio (ms)")

    # Cassandra Q3 (s)
    c_specs = [
        ("b=4\nBase", "base_cass_q3_b4", COLOR_BASE, None),
        ("b=4\nN1", "n1_cass_q3_b4", COLOR_N1, "base_cass_q3_b4"),
    ]
    if has_b4_zm:
        c_specs.append(("b=4\nN2+ZM", "n2_cass_q3_b4_zm", COLOR_N2_ZM, "base_cass_q3_b4"))
    c_specs.extend(
        [
            ("b=8\nBase", "base_cass_q3_b8", COLOR_BASE, None),
            ("b=8\nN1", "n1_cass_q3_b8", COLOR_N1, "base_cass_q3_b8"),
            ("b=8\nN2+ZM", "n2_cass_q3_b8_zm", COLOR_N2_ZM, "base_cass_q3_b8"),
            ("b=16 mod\nBase", "base_cass_q3_b16", COLOR_BASE, None),
            ("b=16 mod\nN1", "n1_cass_q3_b16", COLOR_N1, "base_cass_q3_b16"),
            ("b=16 hash\nN2", "n2_cass_q3_b16_hash", COLOR_N2_HASH, "base_cass_q3_b16"),
            ("b=16 hash\nN2+ZM", "n2_cass_q3_b16_hash_zm", COLOR_N2_ZM, "base_cass_q3_b16"),
        ]
    )

    cx = np.arange(len(c_specs))
    c_vals_s = [metrics[k]["total_ms"] / 1000.0 for _, k, _, _ in c_specs]
    c_cols = [c for _, _, c, _ in c_specs]
    ax_c.bar(cx, c_vals_s, width=0.64, color=c_cols, zorder=3)
    max_c = max(c_vals_s)
    ax_c.set_ylim(0, max_c * 1.24)
    for xi, (_, _, _, bref), val in zip(cx, c_specs, c_vals_s):
        lbl = f"{fmt_pt(val, 2)} s"
        if bref:
            base_s = metrics[bref]["total_ms"] / 1000.0
            lbl += f"\n({fmt_pt(base_s / val, 1)}×)"
        ax_c.text(
            xi,
            val + max_c * 0.022,
            lbl,
            ha="center",
            va="bottom",
            fontsize=8.2,
            fontweight="bold",
            color="#0F172A",
        )

    # Divisores sutis entre b=4, b=8 e b=16
    sep1 = 2.5 if has_b4_zm else 1.5
    sep2 = sep1 + 3.0
    for sep in (sep1, sep2):
        ax_c.axvline(sep, color="#CBD5E1", linestyle=":", linewidth=1.2, zorder=1)

    ax_c.set_xticks(cx)
    ax_c.set_xticklabels([lbl for lbl, _, _, _ in c_specs], fontsize=8.5)
    ax_c.set_title("(b) Cassandra Q3 por Bucketing e Estratégia (s)", pad=10)
    style_axis(ax_c, "Tempo total médio (s)")

    from matplotlib.patches import Patch

    legend_handles = [
        Patch(facecolor=COLOR_BASE, label="Baseline"),
        Patch(facecolor=COLOR_N1, label="Nível 1 (Driver / Parsing / Paralelismo)"),
        Patch(facecolor=COLOR_N2_HASH, label="Nível 2 (Hash uniforme sem Zone Map)"),
        Patch(facecolor=COLOR_N2_ZM, label="Nível 2 (Zone Map — poda de dias/meses)"),
    ]
    fig.legend(
        handles=legend_handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.935),
        ncol=4,
        frameon=False,
        fontsize=9.5,
    )
    fig.suptitle(
        "Evolução da Consulta Q3 — Baseline → Nível 1 → Nível 2",
        fontsize=13,
        fontweight="bold",
        y=0.99,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.87))
    fig.savefig(OUT_DIR / "fig2_q3_evolution.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_fig3_q3_db_vs_app(metrics: dict[str, dict], has_b4_zm: bool) -> None:
    fig, (ax_r, ax_c) = plt.subplots(
        1, 2, figsize=(15, 5.5), dpi=300, gridspec_kw={"width_ratios": [1, 2.25]}
    )

    # Redis
    r_items = [
        ("Baseline", "base_redis_q3"),
        ("Nível 1", "n1_redis_q3"),
        ("Nível 2\n(Zone Map)", "n2_redis_q3_zm"),
    ]
    rx = np.arange(len(r_items))
    r_db = [metrics[k]["db_ms"] for _, k in r_items]
    r_app = [metrics[k]["app_ms"] for _, k in r_items]
    r_tot = [metrics[k]["total_ms"] for _, k in r_items]
    ax_r.bar(rx, r_db, width=0.54, color=COLOR_DB, label="t_db (Banco)", zorder=3)
    ax_r.bar(rx, r_app, width=0.54, bottom=r_db, color=COLOR_APP, label="t_app (Aplicação)", zorder=3)
    max_r = max(r_tot)
    ax_r.set_ylim(0, max_r * 1.26)
    for xi, db, app, tot in zip(rx, r_db, r_app, r_tot):
        pct_db = 100.0 * db / tot
        pct_app = 100.0 * app / tot
        ax_r.text(
            xi,
            tot + max_r * 0.02,
            f"{fmt_pt(tot, 1)} ms\n(db {pct_db:.0f}% / app {pct_app:.0f}%)",
            ha="center",
            va="bottom",
            fontsize=8.3,
            fontweight="bold",
            color="#0F172A",
        )
    ax_r.set_xticks(rx)
    ax_r.set_xticklabels([l for l, _ in r_items], fontsize=9.5)
    ax_r.set_title("(a) Redis Q3 (ms)", pad=10)
    style_axis(ax_r, "Tempo médio (ms)")

    # Cassandra principais estágios (s)
    c_items = []
    if has_b4_zm:
        c_items.extend(
            [
                ("b=4\nN2+ZM", "n2_cass_q3_b4_zm"),
            ]
        )
    c_items = [
        ("b=8\nBase", "base_cass_q3_b8"),
        ("b=8\nN1", "n1_cass_q3_b8"),
        ("b=8\nN2+ZM", "n2_cass_q3_b8_zm"),
        ("b=16 mod\nBase", "base_cass_q3_b16"),
        ("b=16 mod\nN1", "n1_cass_q3_b16"),
        ("b=16 hash\nN2", "n2_cass_q3_b16_hash"),
        ("b=16 hash\nN2+ZM", "n2_cass_q3_b16_hash_zm"),
    ]
    if has_b4_zm:
        c_items = [
            ("b=4\nBase", "base_cass_q3_b4"),
            ("b=4\nN1", "n1_cass_q3_b4"),
            ("b=4\nN2+ZM", "n2_cass_q3_b4_zm"),
        ] + c_items

    cx = np.arange(len(c_items))
    c_db = [metrics[k]["db_ms"] / 1000.0 for _, k in c_items]
    c_app = [metrics[k]["app_ms"] / 1000.0 for _, k in c_items]
    c_tot = [metrics[k]["total_ms"] / 1000.0 for _, k in c_items]
    ax_c.bar(cx, c_db, width=0.60, color=COLOR_DB, zorder=3)
    ax_c.bar(cx, c_app, width=0.60, bottom=c_db, color=COLOR_APP, zorder=3)
    max_c = max(c_tot)
    ax_c.set_ylim(0, max_c * 1.25)
    for xi, db, app, tot in zip(cx, c_db, c_app, c_tot):
        pct_app = 100.0 * app / tot
        ax_c.text(
            xi,
            tot + max_c * 0.02,
            f"{fmt_pt(tot, 2)} s\n(app {pct_app:.1f}%)",
            ha="center",
            va="bottom",
            fontsize=8.0,
            fontweight="bold",
            color="#0F172A",
        )
    ax_c.set_xticks(cx)
    ax_c.set_xticklabels([l for l, _ in c_items], fontsize=8.4)
    ax_c.set_title("(b) Cassandra Q3 (s)", pad=10)
    style_axis(ax_c, "Tempo médio (s)")

    handles, leg_labels = ax_r.get_legend_handles_labels()
    fig.legend(
        handles,
        leg_labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.935),
        ncol=2,
        frameon=False,
        fontsize=10,
    )
    fig.suptitle(
        "Q3 — Decomposição Banco (t_db) vs Aplicação (t_app) e Deslocamento do Gargalo",
        fontsize=13,
        fontweight="bold",
        y=0.99,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.87))
    fig.savefig(OUT_DIR / "fig3_q3_db_vs_app.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_fig4_cassandra_partitions(tstats: dict[str, dict[str, str]]) -> None:
    configs = [
        ("b=4 mod", "q3_b4", COLOR_BASE),
        ("b=8 mod", "q3_b8", COLOR_N1),
        ("b=16 mod\n(skew TPC-H)", "q3_b16", "#F97316"),
        ("b=16 hash\n(Knuth)", "q3_b16_hash", COLOR_N2_ZM),
    ]
    labels = [c[0] for c in configs]
    parts = [int(tstats[c[1]]["Number of partitions (estimate)"]) for c in configs]
    mean_kib = [int(tstats[c[1]]["Compacted partition mean bytes"]) / 1024.0 for c in configs]
    max_kib = [int(tstats[c[1]]["Compacted partition maximum bytes"]) / 1024.0 for c in configs]
    colors = [c[2] for c in configs]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.1), dpi=300)

    x = np.arange(len(configs))
    ax1.bar(x, parts, width=0.55, color=colors, zorder=3)
    max_p = max(parts)
    ax1.set_ylim(0, max_p * 1.22)
    for xi, pv in zip(x, parts):
        ax1.text(
            xi,
            pv + max_p * 0.025,
            f"{pv:,}".replace(",", "."),
            ha="center",
            va="bottom",
            fontsize=9.5,
            fontweight="bold",
            color="#0F172A",
        )
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, fontsize=9.5)
    ax1.set_title("(a) Número Estimado de Partições Criadas", pad=10)
    style_axis(ax1, "Partições estimadas")

    w = 0.36
    ax2.bar(x - w / 2, mean_kib, width=w, color="#0EA5E9", label="Tamanho médio (KiB)", zorder=3)
    ax2.bar(x + w / 2, max_kib, width=w, color="#E11D48", label="Tamanho máximo (KiB)", zorder=3)
    max_s = max(max_kib)
    ax2.set_ylim(0, max_s * 1.22)
    for xi, mv, xv in zip(x, mean_kib, max_kib):
        ax2.text(
            xi - w / 2,
            mv + max_s * 0.02,
            fmt_pt(mv, 0),
            ha="center",
            va="bottom",
            fontsize=8.5,
            fontweight="bold",
            color="#0369A1",
        )
        ax2.text(
            xi + w / 2,
            xv + max_s * 0.02,
            fmt_pt(xv, 0),
            ha="center",
            va="bottom",
            fontsize=8.5,
            fontweight="bold",
            color="#9F1239",
        )
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, fontsize=9.5)
    ax2.set_title("(b) Tamanho Médio vs Máximo de Partição (KiB)", pad=10)
    ax2.legend(loc="upper right", frameon=True, facecolor="white", framealpha=0.9, fontsize=9)
    style_axis(ax2, "Tamanho em disco compactado (KiB)")

    fig.suptitle(
        "Cassandra Q3 — Viés do o_orderkey % 16 (TPC-H) vs Uniformização por Hash",
        fontsize=13,
        fontweight="bold",
        y=0.98,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(OUT_DIR / "fig4_cassandra_partitions.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_fig5_load_and_storage(loads: dict[str, dict], tstats: dict[str, dict[str, str]]) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.8, 5.4), dpi=300)

    # (a) t_load_s (escala log10)
    load_cats = ["Q1", "Q2 min", "Q2 full", "Q3 (b=16 hash)", "Q3 Zone Map"]
    r_load = [
        loads["redis_q1"]["t_load_s"],
        loads["redis_q2_min"]["t_load_s"],
        loads["redis_q2_full"]["t_load_s"],
        loads["redis_q3"]["t_load_s"],
        loads["redis_q3_zm"]["t_load_s"],
    ]
    c_load = [
        loads["cass_q1"]["t_load_s"],
        loads["cass_q2_min"]["t_load_s"],
        loads["cass_q2_full"]["t_load_s"],
        loads["cass_q3_b16_hash"]["t_load_s"],
        loads["cass_q3_zm"]["t_load_s"],
    ]
    x = np.arange(len(load_cats))
    w = 0.36
    ax1.bar(x - w / 2, r_load, width=w, color=COLOR_REDIS, label="Redis (pipeline / RDB)", zorder=3)
    ax1.bar(x + w / 2, c_load, width=w, color=COLOR_CASS, label="Cassandra (CQL / DSBulk)", zorder=3)
    ax1.set_yscale("log")
    ax1.set_ylim(0.008, max(c_load) * 5.5)
    for xi, rv, cv in zip(x, r_load, c_load):
        ax1.text(
            xi - w / 2,
            rv * 1.25,
            f"{fmt_pt(rv, 3 if rv < 0.1 else 2)}s",
            ha="center",
            va="bottom",
            fontsize=8.0,
            fontweight="bold",
            color="#991B1B",
        )
        ax1.text(
            xi + w / 2,
            cv * 1.25,
            f"{fmt_pt(cv, 3 if cv < 0.1 else (2 if cv < 10 else 0))}s",
            ha="center",
            va="bottom",
            fontsize=8.0,
            fontweight="bold",
            color="#1E3A8A",
        )
    ax1.set_xticks(x)
    ax1.set_xticklabels(load_cats, fontsize=9)
    ax1.set_title("(a) Tempo de Carga no Banco (t_load_s, escala log)", pad=10)
    ax1.legend(loc="upper right", frameon=True, facecolor="white", framealpha=0.95, fontsize=8.8)
    style_axis(ax1, "Segundos (escala log₁₀)")

    # (b) Armazenamento em MiB
    stor_cats = ["Q1", "Q2 min", "Q2 full", "Q3", "Q3 + ZM"]
    mib = 1024.0 * 1024.0
    r_mem = [
        loads["redis_q1"]["memory"]["used_memory"] / mib,
        loads["redis_q2_min"]["memory"]["used_memory"] / mib,
        loads["redis_q2_full"]["memory"]["used_memory"] / mib,
        loads["redis_q3"]["memory"]["used_memory"] / mib,
        loads["redis_q3_zm"]["memory"]["used_memory"] / mib,
    ]
    c_disk = [
        int(tstats["q1"]["Space used (live)"]) / mib,
        int(tstats["q2_min"]["Space used (live)"]) / mib,
        int(tstats["q2_full"]["Space used (live)"]) / mib,
        int(tstats["q3_b16_hash"]["Space used (live)"]) / mib,
        int(tstats["q3_b16_hash"]["Space used (live)"]) / mib,  # + ~0.01 MiB da q3_zonemap (400 linhas)
    ]
    ax2.bar(x - w / 2, r_mem, width=w, color=COLOR_REDIS, label="Redis RAM (used_memory MiB)", zorder=3)
    ax2.bar(x + w / 2, c_disk, width=w, color=COLOR_CASS, label="Cassandra Disco live (MiB)", zorder=3)
    max_m = max(max(r_mem), max(c_disk))
    ax2.set_ylim(0, max_m * 1.22)
    for xi, rm, cd in zip(x, r_mem, c_disk):
        ax2.text(
            xi - w / 2,
            rm + max_m * 0.02,
            f"{fmt_pt(rm, 1 if rm < 10 else 0)}",
            ha="center",
            va="bottom",
            fontsize=8.2,
            fontweight="bold",
            color="#991B1B",
        )
        ax2.text(
            xi + w / 2,
            cd + max_m * 0.02,
            f"{fmt_pt(cd, 2 if cd < 5 else 0)}",
            ha="center",
            va="bottom",
            fontsize=8.2,
            fontweight="bold",
            color="#1E3A8A",
        )
    ax2.set_xticks(x)
    ax2.set_xticklabels(stor_cats, fontsize=9.5)
    ax2.set_title("(b) Armazenamento: RAM (Redis) vs Disco Live (Cassandra)", pad=10)
    ax2.legend(loc="upper left", frameon=True, facecolor="white", framealpha=0.9, fontsize=8.8)
    style_axis(ax2, "Tamanho (MiB)")

    fig.suptitle(
        "Custos de Carga no Banco e Pegada de Armazenamento",
        fontsize=13,
        fontweight="bold",
        y=0.98,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(OUT_DIR / "fig5_load_and_storage.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def build_markdown(
    metrics: dict[str, dict],
    loads: dict[str, dict],
    exports: dict[str, dict],
    tstats: dict[str, dict[str, str]],
    integrity_rows: list[dict],
    has_b4_zm: bool,
) -> str:
    lines: list[str] = []
    lines.append("# Consolidado Experimental — TPC-H SF=10 (Redis vs Cassandra)\n")
    if not has_b4_zm:
        lines.append(
            "> **Nota:** `test_cassandra_q3_b4_zm_opt.json` ainda não foi executado em "
            "`results_opt_nivel2/`; a linha e barra de `b=4 + Zone Map` serão incluídas "
            "automaticamente após `./run_opt_nivel2.sh cassandra_b4_zm && /usr/bin/python3 generate_report.py`.\n"
        )

    # Tabela 1: Q1 e Q2
    lines.append("## Tabela 1 — Q1 e Q2 (full e min): Baseline vs Nível 1\n")
    lines.append(
        "| Cenário | Banco | Nível | `t_db` (ms) | `t_app` (ms) | `t_total` média (ms) | Mediana (ms) | Desvio (ms) | Speedup vs Base | Validado |"
    )
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|:---:|")
    t1_specs = [
        ("Q1", "Redis", "Baseline", "base_redis_q1", None),
        ("Q1", "Redis", "Nível 1", "n1_redis_q1", "base_redis_q1"),
        ("Q1", "Cassandra", "Baseline", "base_cass_q1", None),
        ("Q1", "Cassandra", "Nível 1", "n1_cass_q1", "base_cass_q1"),
        ("Q2 full", "Redis", "Baseline", "base_redis_q2_full", None),
        ("Q2 full", "Redis", "Nível 1", "n1_redis_q2_full", "base_redis_q2_full"),
        ("Q2 full", "Cassandra", "Baseline", "base_cass_q2_full", None),
        ("Q2 full", "Cassandra", "Nível 1", "n1_cass_q2_full", "base_cass_q2_full"),
        ("Q2 min", "Redis", "Baseline", "base_redis_q2_min", None),
        ("Q2 min", "Redis", "Nível 1", "n1_redis_q2_min", "base_redis_q2_min"),
        ("Q2 min", "Cassandra", "Baseline", "base_cass_q2_min", None),
        ("Q2 min", "Cassandra", "Nível 1", "n1_cass_q2_min", "base_cass_q2_min"),
    ]
    for qname, db, lvl, key, bref in t1_specs:
        m = metrics[key]
        sp = f"**{fmt_pt(metrics[bref]['total_ms'] / m['total_ms'], 2)}×**" if bref else "1,00×"
        val = "Sim" if m["validated"] else "Não"
        lines.append(
            f"| {qname} | {db} | {lvl} | {fmt_pt(m['db_ms'])} | {fmt_pt(m['app_ms'])} | "
            f"**{fmt_pt(m['total_ms'])}** | {fmt_pt(m['median_ms'])} | {fmt_pt(m['stdev_ms'])} | {sp} | {val} |"
        )

    # Tabela 2: Q3 evolução completa
    lines.append("\n## Tabela 2 — Q3: Baseline → Nível 1 → Nível 2\n")
    lines.append(
        "| Cenário | `t_db` (ms) | `t_app` (ms) | `t_total` média (ms) | Mediana (ms) | Desvio (ms) | Ganho vs N1 | Ganho vs Base | Validado |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|:---:|")
    t2_specs = [
        ("Redis Q3 (Baseline)", "base_redis_q3", None, None),
        ("Redis Q3 (Nível 1)", "n1_redis_q3", None, "base_redis_q3"),
        ("Redis Q3 + Zone Map (Nível 2, rep=30)", "n2_redis_q3_zm", "n1_redis_q3", "base_redis_q3"),
        ("Cassandra Q3 `b=4 mod` (Baseline)", "base_cass_q3_b4", None, None),
        ("Cassandra Q3 `b=4 mod` (Nível 1)", "n1_cass_q3_b4", None, "base_cass_q3_b4"),
    ]
    if has_b4_zm:
        t2_specs.append(
            ("Cassandra Q3 `b=4 mod` + Zone Map (Nível 2)", "n2_cass_q3_b4_zm", "n1_cass_q3_b4", "base_cass_q3_b4")
        )
    t2_specs.extend(
        [
            ("Cassandra Q3 `b=8 mod` (Baseline)", "base_cass_q3_b8", None, None),
            ("Cassandra Q3 `b=8 mod` (Nível 1)", "n1_cass_q3_b8", None, "base_cass_q3_b8"),
            ("Cassandra Q3 `b=8 mod` + Zone Map (Nível 2)", "n2_cass_q3_b8_zm", "n1_cass_q3_b8", "base_cass_q3_b8"),
            ("Cassandra Q3 `b=16 mod` (Baseline)", "base_cass_q3_b16", None, None),
            ("Cassandra Q3 `b=16 mod` (Nível 1)", "n1_cass_q3_b16", None, "base_cass_q3_b16"),
            ("Cassandra Q3 `b=16 hash` (Nível 2, sem ZM)", "n2_cass_q3_b16_hash", "n1_cass_q3_b16", "base_cass_q3_b16"),
            ("Cassandra Q3 `b=16 hash` + Zone Map (Nível 2)", "n2_cass_q3_b16_hash_zm", "n1_cass_q3_b16", "base_cass_q3_b16"),
        ]
    )
    for label, key, n1ref, bref in t2_specs:
        m = metrics[key]
        g_n1 = f"**{fmt_pt(metrics[n1ref]['total_ms'] / m['total_ms'], 2)}×**" if n1ref else "—"
        g_base = f"**{fmt_pt(metrics[bref]['total_ms'] / m['total_ms'], 2)}×**" if bref else "1,00×"
        val = "Sim" if m["validated"] else "Não"
        lines.append(
            f"| {label} | {fmt_pt(m['db_ms'])} | {fmt_pt(m['app_ms'])} | "
            f"**{fmt_pt(m['total_ms'])}** | {fmt_pt(m['median_ms'])} | {fmt_pt(m['stdev_ms'])} | "
            f"{g_n1} | {g_base} | {val} |"
        )

    # Tabela 3: Carga e ETL
    lines.append("\n## Tabela 3 — Tempos de Carga e ETL\n")
    lines.append(
        "| Carga / Cenário | Banco | Registros carregados | Extração / Export (s) | Transformação (s) | Carga no banco (s) | Tempo total (s) |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|")

    def fmt_int(n: int) -> str:
        return f"{n:,}".replace(",", ".")

    t3_rows = [
        (
            "Q1",
            "Redis",
            fmt_int(loads["redis_q1"]["rows_loaded"]),
            fmt_pt(loads["redis_q1"]["t_extract_s"]),
            fmt_pt(loads["redis_q1"]["t_transform_s"]),
            fmt_pt(loads["redis_q1"]["t_load_s"], 3),
            fmt_pt(loads["redis_q1"]["t_wall_total_s"]),
        ),
        (
            "Q1",
            "Cassandra",
            fmt_int(loads["cass_q1"]["rows_loaded"]),
            fmt_pt(loads["cass_q1"]["t_extract_s"]),
            fmt_pt(loads["cass_q1"]["t_transform_s"], 3),
            fmt_pt(loads["cass_q1"]["t_load_s"], 3),
            fmt_pt(loads["cass_q1"]["t_wall_total_s"]),
        ),
        (
            "Q2 min",
            "Redis",
            fmt_int(loads["redis_q2_min"]["rows_loaded"]),
            fmt_pt(loads["redis_q2_min"]["t_extract_s"]),
            fmt_pt(loads["redis_q2_min"]["t_transform_s"]),
            fmt_pt(loads["redis_q2_min"]["t_load_s"]),
            fmt_pt(loads["redis_q2_min"]["t_wall_total_s"]),
        ),
        (
            "Q2 min",
            "Cassandra",
            fmt_int(loads["cass_q2_min"]["rows_loaded"]),
            fmt_pt(loads["cass_q2_min"]["t_extract_s"]),
            fmt_pt(loads["cass_q2_min"]["t_transform_s"]),
            fmt_pt(loads["cass_q2_min"]["t_load_s"]),
            fmt_pt(loads["cass_q2_min"]["t_wall_total_s"]),
        ),
        (
            "Q2 full",
            "Redis",
            fmt_int(loads["redis_q2_full"]["rows_loaded"]),
            fmt_pt(loads["redis_q2_full"]["t_extract_s"]),
            fmt_pt(loads["redis_q2_full"]["t_transform_s"]),
            fmt_pt(loads["redis_q2_full"]["t_load_s"]),
            fmt_pt(loads["redis_q2_full"]["t_wall_total_s"]),
        ),
        (
            "Q2 full",
            "Cassandra",
            fmt_int(loads["cass_q2_full"]["rows_loaded"]),
            fmt_pt(loads["cass_q2_full"]["t_extract_s"]),
            fmt_pt(loads["cass_q2_full"]["t_transform_s"]),
            fmt_pt(loads["cass_q2_full"]["t_load_s"]),
            fmt_pt(loads["cass_q2_full"]["t_wall_total_s"]),
        ),
        (
            "Q3 (Orders + Lineitems)",
            "Redis",
            f"{fmt_int(loads['redis_q3']['orders_loaded'])} ped. / {fmt_int(loads['redis_q3']['items_loaded'])} itens",
            fmt_pt(loads["redis_q3"]["t_extract_s"]),
            fmt_pt(loads["redis_q3"]["t_transform_s"]),
            fmt_pt(loads["redis_q3"]["t_load_s"]),
            fmt_pt(loads["redis_q3"]["t_wall_total_s"]),
        ),
        (
            "Q3 `b=4 mod` (MySQL → CSV → DSBulk)",
            "Cassandra",
            fmt_int(exports["q3_b4"]["rows_exported"]),
            fmt_pt(exports["q3_b4"]["t_export_s"]),
            "—",
            fmt_pt(loads["cass_q3_b4"]["t_load_s"]),
            fmt_pt(exports["q3_b4"]["t_export_s"] + loads["cass_q3_b4"]["t_load_s"]),
        ),
        (
            "Q3 `b=8 mod` (Rebucket CSV → DSBulk)",
            "Cassandra",
            fmt_int(exports["q3_b8"]["rows_exported"]),
            fmt_pt(exports["q3_b8"]["t_export_s"]),
            "—",
            fmt_pt(loads["cass_q3_b8"]["t_load_s"]),
            fmt_pt(exports["q3_b8"]["t_export_s"] + loads["cass_q3_b8"]["t_load_s"]),
        ),
        (
            "Q3 `b=16 mod` (Rebucket CSV → DSBulk)",
            "Cassandra",
            fmt_int(exports["q3_b16"]["rows_exported"]),
            fmt_pt(exports["q3_b16"]["t_export_s"]),
            "—",
            fmt_pt(loads["cass_q3_b16"]["t_load_s"]),
            fmt_pt(exports["q3_b16"]["t_export_s"] + loads["cass_q3_b16"]["t_load_s"]),
        ),
        (
            "Q3 `b=16 hash` (Rebucket CSV → DSBulk)",
            "Cassandra",
            fmt_int(exports["q3_b16_hash"]["rows_exported"]),
            fmt_pt(exports["q3_b16_hash"]["t_export_s"]),
            "—",
            fmt_pt(loads["cass_q3_b16_hash"]["t_load_s"]),
            fmt_pt(exports["q3_b16_hash"]["t_export_s"] + loads["cass_q3_b16_hash"]["t_load_s"]),
        ),
        (
            "Q3 Zone Map (`tpch:q3:zonemap:*`)",
            "Redis",
            f"{fmt_int(loads['redis_q3_zm']['rows_loaded'])} dias",
            fmt_pt(loads["redis_q3_zm"]["t_extract_s"]),
            fmt_pt(loads["redis_q3_zm"]["t_transform_s"]),
            fmt_pt(loads["redis_q3_zm"]["t_load_s"], 3),
            fmt_pt(loads["redis_q3_zm"]["t_wall_total_s"]),
        ),
        (
            "Q3 Zone Map (`tpch.q3_zonemap`)",
            "Cassandra",
            f"{fmt_int(loads['cass_q3_zm']['rows_loaded'])} meses",
            fmt_pt(loads["cass_q3_zm"]["t_extract_s"]),
            fmt_pt(loads["cass_q3_zm"]["t_transform_s"]),
            fmt_pt(loads["cass_q3_zm"]["t_load_s"], 3),
            fmt_pt(loads["cass_q3_zm"]["t_wall_total_s"]),
        ),
    ]
    for r in t3_rows:
        lines.append(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} |")

    # Tabela 4: Armazenamento e Distribuição Física
    lines.append("\n## Tabela 4 — Armazenamento e Distribuição Física\n")
    lines.append("### 4a. Redis — Memória RAM (`INFO memory`)\n")
    lines.append("| Cenário | `used_memory` (bytes) | `used_memory` (MiB) | `used_memory_human` | RSS (`used_memory_rss_human`) | Frag. Ratio |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    mib = 1024.0 * 1024.0
    for lbl, lk in [
        ("Q1", "redis_q1"),
        ("Q2 min", "redis_q2_min"),
        ("Q2 full", "redis_q2_full"),
        ("Q3 (Baseline/N1)", "redis_q3"),
        ("Q3 + Zone Map (N2)", "redis_q3_zm"),
    ]:
        mem = loads[lk]["memory"]
        lines.append(
            f"| {lbl} | {fmt_int(mem['used_memory'])} | {fmt_pt(mem['used_memory'] / mib, 2)} | "
            f"{mem['used_memory_human']} | {mem['used_memory_rss_human']} | {fmt_pt(mem['mem_fragmentation_ratio'], 2)} |"
        )
    zm_delta_b = loads["redis_q3_zm"]["memory"]["used_memory"] - loads["redis_q3"]["memory"]["used_memory"]
    zm_delta_pct = 100.0 * zm_delta_b / loads["redis_q3"]["memory"]["used_memory"]
    lines.append(
        f"\n> **Overhead do Zone Map no Redis:** `+{fmt_int(zm_delta_b)}` bytes "
        f"(`+{fmt_pt(zm_delta_b / 1024.0, 1)} KiB` / `+{fmt_pt(zm_delta_pct, 3)}%` sobre o `q3.rdb`).\n"
    )

    lines.append("### 4b. Cassandra — Disco e Distribuição de Partições (`nodetool tablestats`)\n")
    lines.append(
        "| Tabela / Bucketing | SSTables | Live (MiB) | Total (MiB) | Partições (est.) | Mín (B) | Média (KiB) | Máx (KiB) |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    t4b_specs = [
        ("Q1 (`q1_daily_by_month`)", "q1"),
        ("Q2 min (`q2_min_offers_by_filter`)", "q2_min"),
        ("Q2 full (`q2_offers_by_filter`)", "q2_full"),
        ("Q3 `b=4 mod`", "q3_b4"),
        ("Q3 `b=8 mod`", "q3_b8"),
        ("Q3 `b=16 mod` (skew TPC-H)", "q3_b16"),
        ("Q3 `b=16 hash` (Knuth)", "q3_b16_hash"),
    ]
    for lbl, tk in t4b_specs:
        ts = tstats[tk]
        live_mib = int(ts["Space used (live)"]) / mib
        tot_mib = int(ts["Space used (total)"]) / mib
        parts = int(ts["Number of partitions (estimate)"])
        pmin = int(ts["Compacted partition minimum bytes"])
        pmean_kib = int(ts["Compacted partition mean bytes"]) / 1024.0
        pmax_kib = int(ts["Compacted partition maximum bytes"]) / 1024.0
        lines.append(
            f"| {lbl} | {ts['SSTable count']} | {fmt_pt(live_mib, 2)} | {fmt_pt(tot_mib, 2)} | "
            f"{fmt_int(parts)} | {fmt_int(pmin)} | {fmt_pt(pmean_kib, 1)} | {fmt_pt(pmax_kib, 1)} |"
        )
    lines.append(
        "\n> **Viés do TPC-H vs Hash uniforme:** em `b=8 mod` e `b=16 mod`, o número estimado de partições permanece "
        "idêntico (`3.501`) e a partição máxima fica igual (`1.131.752 B` = `1.105,2 KiB`), pois os `o_orderkey` do TPC-H "
        "não ocupam uniformemente as classes módulo 16. Com `b=16 hash`, as partições dobram para `6.883`, a partição máxima "
        "cai pela metade (`545.791 B` = `533,0 KiB`) e a média cai de `346,5 KiB` para `233,9 KiB`.\n"
    )

    # Tabela 5: Integridade
    lines.append("## Tabela 5 — Integridade dos Resultados (`output_*.json` vs `ref_q*.json`)\n")
    lines.append("| Fase | Arquivo de saída | Referência | Bytes idênticos | JSON idêntico | `validated` |")
    lines.append("|---|---|---|:---:|:---:|:---:|")
    for r in integrity_rows:
        lines.append(
            f"| {r['phase']} | `{r['output_file']}` | `{r['ref_file']}` | "
            f"{'Sim' if r['bytes_eq'] else 'Não'} | {'Sim' if r['json_eq'] else 'Não'} | "
            f"{'True' if r['validated'] else str(r['validated'])} |"
        )

    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    test_files = {
        "base_redis_q1": DIR_BASE / "test_redis_q1.json",
        "base_redis_q2_full": DIR_BASE / "test_redis_q2_full.json",
        "base_redis_q2_min": DIR_BASE / "test_redis_q2_min.json",
        "base_redis_q3": DIR_BASE / "test_redis_q3.json",
        "base_cass_q1": DIR_BASE / "test_cassandra_q1.json",
        "base_cass_q2_full": DIR_BASE / "test_cassandra_q2_full.json",
        "base_cass_q2_min": DIR_BASE / "test_cassandra_q2_min.json",
        "base_cass_q3_b4": DIR_BASE / "test_cassandra_q3_b4.json",
        "base_cass_q3_b8": DIR_BASE / "test_cassandra_q3_b8.json",
        "base_cass_q3_b16": DIR_BASE / "test_cassandra_q3_b16.json",
        "n1_redis_q1": DIR_N1 / "test_redis_q1_opt.json",
        "n1_redis_q2_full": DIR_N1 / "test_redis_q2_full_opt.json",
        "n1_redis_q2_min": DIR_N1 / "test_redis_q2_min_opt.json",
        "n1_redis_q3": DIR_N1 / "test_redis_q3_opt.json",
        "n1_cass_q1": DIR_N1 / "test_cassandra_q1_opt.json",
        "n1_cass_q2_full": DIR_N1 / "test_cassandra_q2_full_opt.json",
        "n1_cass_q2_min": DIR_N1 / "test_cassandra_q2_min_opt.json",
        "n1_cass_q3_b4": DIR_N1 / "test_cassandra_q3_b4_opt.json",
        "n1_cass_q3_b8": DIR_N1 / "test_cassandra_q3_b8_opt.json",
        "n1_cass_q3_b16": DIR_N1 / "test_cassandra_q3_b16_opt.json",
        "n2_redis_q3_zm": DIR_N2 / "test_redis_q3_zonemap_opt.json",
        "n2_cass_q3_b8_zm": DIR_N2 / "test_cassandra_q3_b8_zm_opt.json",
        "n2_cass_q3_b16_hash": DIR_N2 / "test_cassandra_q3_b16_hash_opt.json",
        "n2_cass_q3_b16_hash_zm": DIR_N2 / "test_cassandra_q3_b16_hash_zm_opt.json",
        "n2_cass_q3_b4_zm": DIR_N2 / "test_cassandra_q3_b4_zm_opt.json",
    }

    metrics: dict[str, dict] = {}
    for key, path in test_files.items():
        m = load_test_metrics(path)
        if m is None:
            if key == "n2_cass_q3_b4_zm":
                print(
                    "[AVISO] results_opt_nivel2/test_cassandra_q3_b4_zm_opt.json ausente; "
                    "b=4 + Zone Map será omitido até sua execução."
                )
                continue
            print(f"ERRO: arquivo obrigatório ausente: {path}", file=sys.stderr)
            return 1
        metrics[key] = m

    has_b4_zm = "n2_cass_q3_b4_zm" in metrics

    loads = {
        "redis_q1": load_json(DIR_BASE / "load_redis_q1.json"),
        "redis_q2_min": load_json(DIR_BASE / "load_redis_q2_min.json"),
        "redis_q2_full": load_json(DIR_BASE / "load_redis_q2_full.json"),
        "redis_q3": load_json(DIR_BASE / "load_redis_q3.json"),
        "redis_q3_zm": load_json(DIR_N2 / "load_redis_q3_zonemap.json"),
        "cass_q1": load_json(DIR_BASE / "load_cassandra_q1.json"),
        "cass_q2_min": load_json(DIR_BASE / "load_cassandra_q2_min.json"),
        "cass_q2_full": load_json(DIR_BASE / "load_cassandra_q2_full.json"),
        "cass_q3_b4": load_json(DIR_BASE / "load_cassandra_q3_b4.json"),
        "cass_q3_b8": load_json(DIR_BASE / "load_cassandra_q3_b8.json"),
        "cass_q3_b16": load_json(DIR_BASE / "load_cassandra_q3_b16.json"),
        "cass_q3_b16_hash": load_json(DIR_N2 / "load_cassandra_q3_b16_hash.json"),
        "cass_q3_zm": load_json(DIR_N2 / "load_cassandra_q3_zonemap.json"),
    }

    exports = {
        "q3_b4": load_json(DIR_BASE / "export_cassandra_q3_b4.json"),
        "q3_b8": load_json(DIR_BASE / "export_cassandra_q3_b8.json"),
        "q3_b16": load_json(DIR_BASE / "export_cassandra_q3_b16.json"),
        "q3_b16_hash": load_json(DIR_N2 / "export_cassandra_q3_b16_hash.json"),
    }

    ts_q1_q2 = parse_tablestats(DIR_BASE / "tablestats_q1_q2.txt")
    tstats = {
        "q1": ts_q1_q2["q1_daily_by_month"],
        "q2_min": ts_q1_q2["q2_min_offers_by_filter"],
        "q2_full": ts_q1_q2["q2_offers_by_filter"],
        "q3_b4": parse_tablestats(DIR_BASE / "tablestats_q3_b4.txt")["q3_lineitems_by_segment_month"],
        "q3_b8": parse_tablestats(DIR_BASE / "tablestats_q3_b8.txt")["q3_lineitems_by_segment_month"],
        "q3_b16": parse_tablestats(DIR_BASE / "tablestats_q3_b16.txt")["q3_lineitems_by_segment_month"],
        "q3_b16_hash": parse_tablestats(DIR_N2 / "tablestats_q3_b16_hash.txt")["q3_lineitems_by_segment_month"],
    }

    integrity_rows, all_ok = check_integrity()
    if not all_ok:
        print("ERRO: divergência detectada na verificação de integridade dos outputs!", file=sys.stderr)
        for r in integrity_rows:
            if not r["ok"]:
                print(f"  FALHA: {r}", file=sys.stderr)
        return 1

    md_text = build_markdown(metrics, loads, exports, tstats, integrity_rows, has_b4_zm)
    (OUT_DIR / "resumo_completo.md").write_text(md_text, encoding="utf-8")

    plot_fig1_q1_q2(metrics)
    plot_fig2_q3_evolution(metrics, has_b4_zm)
    plot_fig3_q3_db_vs_app(metrics, has_b4_zm)
    plot_fig4_cassandra_partitions(tstats)
    plot_fig5_load_and_storage(loads, tstats)

    print(f"Integridade: {len(integrity_rows)}/{len(integrity_rows)} arquivos output_*.json idênticos às referências (ref_q*.json).")
    print("Arquivos gerados em report_assets/:")
    for p in sorted(OUT_DIR.iterdir()):
        print(f"  - {p.name} ({p.stat().st_size:,} bytes)".replace(",", "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
