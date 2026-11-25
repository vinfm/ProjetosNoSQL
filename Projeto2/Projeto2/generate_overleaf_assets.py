#!/usr/bin/python3 -s
"""Gera e organiza todas as figuras para o pacote Overleaf em overleaf/imagens/
a partir dos resultados reais de results_baseline/, results_opt_nivel1/,
results_opt_nivel2/ e report_assets/.
"""

from __future__ import annotations

import json
import os
import shutil
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
        "Execute com: /usr/bin/python3 -s generate_overleaf_assets.py",
        file=sys.stderr,
    )
    sys.exit(1)

BASE_DIR = Path(__file__).resolve().parent
DIR_BASE = BASE_DIR / "results_baseline"
DIR_N1 = BASE_DIR / "results_opt_nivel1"
DIR_N2 = BASE_DIR / "results_opt_nivel2"
DIR_REP = BASE_DIR / "report_assets"

OVERLEAF_DIR = BASE_DIR / "overleaf"
IMG_DIR = OVERLEAF_DIR / "imagens"
IMG_REDIS = IMG_DIR / "Redis"
IMG_CASS = IMG_DIR / "Cassandra"

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


def style_axis(ax, ylabel: str | None = None, grid_axis: str = "y") -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis=grid_axis, zorder=0)
    ax.set_axisbelow(True)
    if ylabel:
        ax.set_ylabel(ylabel)


def parse_tablestats(path: Path) -> dict[str, dict[str, str]]:
    tables: dict[str, dict[str, str]] = {}
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


def parse_tablehistograms(path: Path) -> dict[str, int]:
    """Retorna {percentil: partition_size_bytes} de `nodetool tablehistograms`."""
    out: dict[str, int] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        tokens = raw_line.strip().split()
        if len(tokens) == 6 and tokens[0] in ("50%", "75%", "95%", "98%", "99%", "Min", "Max"):
            out[tokens[0]] = int(tokens[4])
    return out


def plot_redis_memoria_carga(loads: dict[str, dict]) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14.8, 5.5), dpi=300)
    mib = 1024.0 * 1024.0

    # (a) Memória RAM no Redis (used_memory vs used_memory_rss)
    mem_specs = [
        ("Q1", "redis_q1"),
        ("Q2 min", "redis_q2_min"),
        ("Q2 full", "redis_q2_full"),
        ("Q3\n(Base/N1)", "redis_q3"),
        ("Q3 + ZM\n(Nível 2)", "redis_q3_zm"),
    ]
    labels = [lbl for lbl, _ in mem_specs]
    used_mib = [loads[k]["memory"]["used_memory"] / mib for _, k in mem_specs]
    rss_mib = [loads[k]["memory"]["used_memory_rss"] / mib for _, k in mem_specs]
    frags = [loads[k]["memory"]["mem_fragmentation_ratio"] for _, k in mem_specs]

    x = np.arange(len(labels))
    w = 0.36
    ax1.bar(x - w / 2, used_mib, width=w, color="#DC2626", label="used_memory (MiB)", zorder=3)
    ax1.bar(x + w / 2, rss_mib, width=w, color="#FB7185", label="used_memory_rss (MiB)", zorder=3)

    max_mem = max(rss_mib)
    ax1.set_ylim(0, max_mem * 1.26)
    for xi, um, rm, fr in zip(x, used_mib, rss_mib, frags):
        u_txt = fmt_pt(um, 1 if um < 20 else 0)
        r_txt = fmt_pt(rm, 1 if rm < 20 else 0)
        ax1.text(
            xi - w / 2,
            um + max_mem * 0.018,
            u_txt,
            ha="center",
            va="bottom",
            fontsize=8.2,
            fontweight="bold",
            color="#991B1B",
        )
        ax1.text(
            xi + w / 2,
            rm + max_mem * 0.018,
            f"{r_txt}\n({fmt_pt(fr, 2)}×)" if um >= 20 else r_txt,
            ha="center",
            va="bottom",
            fontsize=7.9,
            fontweight="bold",
            color="#9F1239",
        )

    zm_delta_kib = (
        loads["redis_q3_zm"]["memory"]["used_memory"] - loads["redis_q3"]["memory"]["used_memory"]
    ) / 1024.0
    ax1.text(
        0.02,
        0.74,
        f"Overhead Zone Map:\n+{fmt_pt(zm_delta_kib, 1)} KiB (+0,043%)\nFrag. ratio Q2/Q3: 1,02–1,03",
        transform=ax1.transAxes,
        fontsize=8.5,
        bbox={"boxstyle": "round,pad=0.4", "facecolor": "#FFF1F2", "edgecolor": "#FECDD3"},
        color="#881337",
    )
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, fontsize=9.2)
    ax1.set_title("(a) Consumo de Memória RAM (INFO memory)", pad=10)
    ax1.legend(loc="upper left", frameon=True, facecolor="white", framealpha=0.95, fontsize=8.8)
    style_axis(ax1, "Memória (MiB)")

    # (b) Decomposição do ETL no Redis
    etl_specs = [
        ("Q1", "redis_q1"),
        ("Q2 full", "redis_q2_full"),
        ("Q2 min", "redis_q2_min"),
        ("Q3", "redis_q3"),
        ("Q3 ZM", "redis_q3_zm"),
    ]
    elabels = [lbl for lbl, _ in etl_specs]
    ext_s = [loads[k]["t_extract_s"] for _, k in etl_specs]
    tr_s = [loads[k]["t_transform_s"] for _, k in etl_specs]
    ld_s = [loads[k]["t_load_s"] for _, k in etl_specs]
    tot_s = [loads[k]["t_wall_total_s"] for _, k in etl_specs]

    x2 = np.arange(len(elabels))
    w2 = 0.54
    ax2.bar(x2, ext_s, width=w2, color="#475569", label="Extração MySQL (t_extract_s)", zorder=3)
    ax2.bar(x2, tr_s, width=w2, bottom=ext_s, color="#F59E0B", label="Transformação Python (t_transform_s)", zorder=3)
    bottom_ld = [e + t for e, t in zip(ext_s, tr_s)]
    ax2.bar(x2, ld_s, width=w2, bottom=bottom_ld, color="#DC2626", label="Carga Redis (t_load_s)", zorder=3)

    max_etl = max(tot_s)
    ax2.set_ylim(0, max_etl * 1.24)
    for xi, tot, ld in zip(x2, tot_s, ld_s):
        ld_txt = fmt_pt(ld, 3 if ld < 0.1 else 2)
        ax2.text(
            xi,
            tot + max_etl * 0.02,
            f"{fmt_pt(tot, 1)} s\n(load: {ld_txt} s)",
            ha="center",
            va="bottom",
            fontsize=8.2,
            fontweight="bold",
            color="#0F172A",
        )

    ax2.set_xticks(x2)
    ax2.set_xticklabels(elabels, fontsize=9.5)
    ax2.set_title("(b) Decomposição do Pipeline ETL no Redis (s)", pad=10)
    ax2.legend(loc="upper left", frameon=True, facecolor="white", framealpha=0.95, fontsize=8.5)
    style_axis(ax2, "Tempo (segundos)")

    fig.suptitle(
        "Redis 7.4 (SF=10) — Pegada de Memória RAM e Custos de Extração, Transformação e Carga",
        fontsize=12.8,
        fontweight="bold",
        y=0.98,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(IMG_REDIS / "redis_memoria_carga.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_redis_evolucao(metrics: dict[str, dict]) -> None:
    fig, (ax1, ax2, ax3) = plt.subplots(
        1, 3, figsize=(15.4, 5.3), dpi=300, gridspec_kw={"width_ratios": [1.35, 0.9, 1.35]}
    )

    def draw_stacked(ax, items, title, ylabel, dec=1, show_pct=False):
        labels = [it[0] for it in items]
        db_v = [metrics[it[1]]["db_ms"] for it in items]
        app_v = [metrics[it[1]]["app_ms"] for it in items]
        tot_v = [metrics[it[1]]["total_ms"] for it in items]
        brefs = [it[2] for it in items]

        x = np.arange(len(labels))
        w = 0.54
        ax.bar(x, db_v, width=w, color=COLOR_DB, label="t_db (Banco / RESP)", zorder=3)
        ax.bar(x, app_v, width=w, bottom=db_v, color=COLOR_APP, label="t_app (Aplicação Python)", zorder=3)

        max_y = max(tot_v)
        ax.set_ylim(0, max_y * 1.28)
        for xi, dv, av, tv, bref in zip(x, db_v, app_v, tot_v, brefs):
            lbl = f"{fmt_pt(tv, dec)} ms"
            if bref is not None:
                lbl += f"\n({fmt_pt(metrics[bref]['total_ms'] / tv, 2)}×)"
            if show_pct:
                db_pct = 100.0 * dv / tv if tv > 0 else 0.0
                lbl += f"\n[db {db_pct:.0f}%]"
            ax.text(
                xi,
                tv + max_y * 0.022,
                lbl,
                ha="center",
                va="bottom",
                fontsize=8.2,
                fontweight="bold",
                color="#0F172A",
            )
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=8.8)
        ax.set_title(title, pad=10)
        style_axis(ax, ylabel)

    draw_stacked(
        ax1,
        [
            ("Q1\nBase", "base_redis_q1", None),
            ("Q1\nNível 1", "n1_redis_q1", "base_redis_q1"),
            ("Q2 full\nBase", "base_redis_q2_full", None),
            ("Q2 full\nNível 1", "n1_redis_q2_full", "base_redis_q2_full"),
        ],
        "(a) Q1 e Q2 full (ms)",
        "Tempo médio (ms)",
        dec=2,
    )
    draw_stacked(
        ax2,
        [
            ("Q2 min\nBase", "base_redis_q2_min", None),
            ("Q2 min\nNível 1", "n1_redis_q2_min", "base_redis_q2_min"),
        ],
        "(b) Q2 min (ms)",
        None,
        dec=2,
    )
    draw_stacked(
        ax3,
        [
            ("Q3\nBaseline", "base_redis_q3", None),
            ("Q3\nNível 1", "n1_redis_q3", "base_redis_q3"),
            ("Q3\nN2 (Zone Map)", "n2_redis_q3_zm", "base_redis_q3"),
        ],
        "(c) Q3: Baseline → N1 → N2 (ms)",
        None,
        dec=1,
        show_pct=True,
    )

    handles, leg_labels = ax1.get_legend_handles_labels()
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
        "Redis 7.4 — Evolução das Otimizações (hiredis, orjson, struct em bloco e Zone Map)",
        fontsize=12.8,
        fontweight="bold",
        y=0.99,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    fig.savefig(IMG_REDIS / "redis_evolucao_otimizacoes.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_cassandra_histograms(
    hists: dict[str, dict[str, int]], tstats: dict[str, dict[str, str]]
) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.4, 5.5), dpi=300)

    # (a) Percentis de tamanho de partição (nodetool tablehistograms) em KiB
    pcts = ["50%", "75%", "95%", "99%", "Max"]
    pct_labels = ["p50", "p75", "p95", "p99", "Máx"]
    series = [
        ("b=4 mod (Base)", "b4", "#64748B"),
        ("b=8 mod (Base)", "b8", "#2563EB"),
        ("b=16 mod (skew)", "b16", "#F97316"),
        ("b=16 hash (Knuth)", "b16_hash", "#059669"),
    ]
    x = np.arange(len(pcts))
    w = 0.20
    offsets = [-1.5 * w, -0.5 * w, 0.5 * w, 1.5 * w]
    all_vals = []
    for (lbl, hk, col), off in zip(series, offsets):
        vals_kib = [hists[hk][p] / 1024.0 for p in pcts]
        all_vals.extend(vals_kib)
        ax1.bar(x + off, vals_kib, width=w, color=col, label=lbl, zorder=3)
        for xi, v in zip(x, vals_kib):
            if p_needed := (hk in ("b4", "b16_hash") or (hk == "b16" and xi in (0, 2, 4))):
                ax1.text(
                    xi + off,
                    v + 35,
                    fmt_pt(v, 0),
                    ha="center",
                    va="bottom",
                    fontsize=7.3,
                    fontweight="bold",
                    color="#0F172A",
                    rotation=90 if v > 1500 else 0,
                )

    max_h = max(all_vals)
    ax1.set_ylim(0, max_h * 1.24)
    ax1.set_xticks(x)
    ax1.set_xticklabels(pct_labels, fontsize=9.8)
    ax1.set_title("(a) Histograma de Tamanho de Partição na Q3 (KiB)", pad=10)
    ax1.legend(loc="upper left", frameon=True, facecolor="white", framealpha=0.95, fontsize=8.6)
    style_axis(ax1, "Tamanho de partição (KiB)")

    # (b) Espaço em disco (live vs total) e contagem de SSTables
    ts_specs = [
        ("Q1", "q1"),
        ("Q2\nmin", "q2_min"),
        ("Q2\nfull", "q2_full"),
        ("Q3\nb=4", "q3_b4"),
        ("Q3\nb=8", "q3_b8"),
        ("Q3\nb=16 mod", "q3_b16"),
        ("Q3\nb=16 hash", "q3_b16_hash"),
    ]
    mib = 1024.0 * 1024.0
    tlabels = [lbl for lbl, _ in ts_specs]
    live_mib = [int(tstats[k]["Space used (live)"]) / mib for _, k in ts_specs]
    tot_mib = [int(tstats[k]["Space used (total)"]) / mib for _, k in ts_specs]
    ssts = [int(tstats[k]["SSTable count"]) for _, k in ts_specs]

    x2 = np.arange(len(tlabels))
    w2 = 0.36
    ax2.bar(x2 - w2 / 2, live_mib, width=w2, color="#1D4ED8", label="Space used (live, MiB)", zorder=3)
    ax2.bar(x2 + w2 / 2, tot_mib, width=w2, color="#93C5FD", label="Space used (total, MiB)", zorder=3)

    max_d = max(tot_mib)
    ax2.set_ylim(0, max_d * 1.28)
    for xi, lv, tv, sst in zip(x2, live_mib, tot_mib, ssts):
        top = max(lv, tv)
        if abs(tv - lv) > 1.0:
            lbl_txt = f"live: {fmt_pt(lv, 0)}\ntot: {fmt_pt(tv, 0)} MiB\n({sst} SST)"
        else:
            lbl_txt = f"{fmt_pt(lv, 2 if lv < 1 else 0)} MiB\n({sst} SST)"
        ax2.text(
            xi,
            top + max_d * 0.02,
            lbl_txt,
            ha="center",
            va="bottom",
            fontsize=7.3,
            fontweight="bold",
            color="#1E3A8A",
        )
    ax2.set_xticks(x2)
    ax2.set_xticklabels(tlabels, fontsize=8.8)
    ax2.set_title("(b) Armazenamento em Disco e SSTables (tablestats)", pad=10)
    ax2.legend(loc="upper left", frameon=True, facecolor="white", framealpha=0.95, fontsize=8.8)
    style_axis(ax2, "Espaço em disco (MiB)")

    fig.suptitle(
        "Apache Cassandra 5.0 — Distribuição Física de Partições (tablehistograms) e Uso de Disco (tablestats)",
        fontsize=12.6,
        fontweight="bold",
        y=0.98,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(IMG_CASS / "cassandra_histograms_q3.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_cassandra_q3_opt(raw_tests: dict[str, dict]) -> None:
    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(15.4, 5.5), dpi=300, gridspec_kw={"width_ratios": [1.48, 1.0]}
    )

    # (a) Warm-up (execução fria inicial) vs Média medida (s) sem Zone Map
    specs_a = [
        ("b=4\nBase", "base_b4"),
        ("b=4\nN1", "n1_b4"),
        ("b=8\nBase", "base_b8"),
        ("b=8\nN1", "n1_b8"),
        ("b=16 mod\nBase", "base_b16"),
        ("b=16 mod\nN1", "n1_b16"),
        ("b=16 hash\nN2 (s/ ZM)", "n2_b16_hash"),
    ]
    labels_a = [lbl for lbl, _ in specs_a]
    warm_s = [raw_tests[k]["warmup_runs"][0]["t_total_ms"] / 1000.0 for _, k in specs_a]
    mean_s = [raw_tests[k]["summary"]["t_total_ms"]["mean"] / 1000.0 for _, k in specs_a]

    x1 = np.arange(len(labels_a))
    w1 = 0.36
    ax1.bar(x1 - w1 / 2, warm_s, width=w1, color="#94A3B8", label="1ª execução (Warm-up, s)", zorder=3)
    ax1.bar(x1 + w1 / 2, mean_s, width=w1, color="#2563EB", label="Média medida (3 rep., s)", zorder=3)

    max_a = max(warm_s)
    ax1.set_ylim(0, max_a * 1.20)
    for xi, ws, ms in zip(x1, warm_s, mean_s):
        ax1.text(
            xi - w1 / 2,
            ws + max_a * 0.018,
            f"{fmt_pt(ws, 1)}",
            ha="center",
            va="bottom",
            fontsize=7.8,
            fontweight="bold",
            color="#334155",
        )
        ax1.text(
            xi + w1 / 2,
            ms + max_a * 0.018,
            f"{fmt_pt(ms, 1)}",
            ha="center",
            va="bottom",
            fontsize=7.8,
            fontweight="bold",
            color="#1E3A8A",
        )
    ax1.set_xticks(x1)
    ax1.set_xticklabels(labels_a, fontsize=8.5)
    ax1.set_title("(a) Q3 sem Zone Map: Warm-up vs Média Medida (s)", pad=10)
    ax1.legend(loc="upper right", frameon=True, facecolor="white", framealpha=0.95, fontsize=8.6)
    style_axis(ax1, "Tempo total (segundos)")

    # (b) Zoom nos cenários com Zone Map (Nível 2, ms) com desvio padrão
    specs_b = [
        ("b=4 mod\n+ Zone Map", "n2_b4_zm", "base_b4"),
        ("b=8 mod\n+ Zone Map", "n2_b8_zm", "base_b8"),
        ("b=16 hash\n+ Zone Map", "n2_b16_hash_zm", "base_b16"),
    ]
    labels_b = [lbl for lbl, _, _ in specs_b]
    db_ms = [raw_tests[k]["summary"]["t_db_ms"]["mean"] for _, k, _ in specs_b]
    app_ms = [raw_tests[k]["summary"]["t_app_ms"]["mean"] for _, k, _ in specs_b]
    tot_ms = [raw_tests[k]["summary"]["t_total_ms"]["mean"] for _, k, _ in specs_b]
    std_ms = [raw_tests[k]["summary"]["t_total_ms"]["stdev"] for _, k, _ in specs_b]
    base_ms = [raw_tests[b]["summary"]["t_total_ms"]["mean"] for _, _, b in specs_b]

    x2 = np.arange(len(labels_b))
    w2 = 0.50
    ax2.bar(x2, db_ms, width=w2, color=COLOR_DB, label="t_db (Banco CQL)", zorder=3)
    ax2.bar(x2, app_ms, width=w2, bottom=db_ms, color=COLOR_APP, label="t_app (Python)", zorder=3)
    ax2.errorbar(
        x2,
        tot_ms,
        yerr=std_ms,
        fmt="none",
        ecolor="#0F172A",
        elinewidth=1.4,
        capsize=5,
        zorder=4,
        label="±1 desvio padrão",
    )

    max_b = max(t + s for t, s in zip(tot_ms, std_ms))
    ax2.set_ylim(0, max_b * 1.28)
    for xi, tv, sv, bv in zip(x2, tot_ms, std_ms, base_ms):
        ax2.text(
            xi,
            tv + sv + max_b * 0.025,
            f"{fmt_pt(tv, 1)} ± {fmt_pt(sv, 1)} ms\n({fmt_pt(bv / tv, 2)}× vs Base)",
            ha="center",
            va="bottom",
            fontsize=8.3,
            fontweight="bold",
            color="#065F46",
        )
    ax2.set_xticks(x2)
    ax2.set_xticklabels(labels_b, fontsize=9.2)
    ax2.set_title("(b) Zoom Nível 2 + Zone Map (ms) e Desvio Padrão", pad=10)
    ax2.legend(loc="upper right", frameon=True, facecolor="white", framealpha=0.95, fontsize=8.3)
    style_axis(ax2, "Tempo médio (ms)")

    fig.suptitle(
        "Apache Cassandra 5.0 — Impacto do Aquecimento, Nível 1 e Zone Map por Bucketing na Q3",
        fontsize=12.6,
        fontweight="bold",
        y=0.98,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(IMG_CASS / "cassandra_q3_otimizacoes.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    IMG_REDIS.mkdir(parents=True, exist_ok=True)
    IMG_CASS.mkdir(parents=True, exist_ok=True)

    # 1. Copiar figuras comparativas existentes de report_assets/
    for fname in (
        "fig1_q1_q2_comparison.png",
        "fig2_q3_evolution.png",
        "fig3_q3_db_vs_app.png",
        "fig5_load_and_storage.png",
    ):
        shutil.copy2(DIR_REP / fname, IMG_DIR / fname)
    shutil.copy2(DIR_REP / "fig4_cassandra_partitions.png", IMG_CASS / "fig4_cassandra_partitions.png")

    # 2. Carregar métricas de carga e testes reais
    loads = {
        "redis_q1": load_json(DIR_BASE / "load_redis_q1.json"),
        "redis_q2_min": load_json(DIR_BASE / "load_redis_q2_min.json"),
        "redis_q2_full": load_json(DIR_BASE / "load_redis_q2_full.json"),
        "redis_q3": load_json(DIR_BASE / "load_redis_q3.json"),
        "redis_q3_zm": load_json(DIR_N2 / "load_redis_q3_zonemap.json"),
    }

    redis_test_paths = {
        "base_redis_q1": DIR_BASE / "test_redis_q1.json",
        "base_redis_q2_full": DIR_BASE / "test_redis_q2_full.json",
        "base_redis_q2_min": DIR_BASE / "test_redis_q2_min.json",
        "base_redis_q3": DIR_BASE / "test_redis_q3.json",
        "n1_redis_q1": DIR_N1 / "test_redis_q1_opt.json",
        "n1_redis_q2_full": DIR_N1 / "test_redis_q2_full_opt.json",
        "n1_redis_q2_min": DIR_N1 / "test_redis_q2_min_opt.json",
        "n1_redis_q3": DIR_N1 / "test_redis_q3_opt.json",
        "n2_redis_q3_zm": DIR_N2 / "test_redis_q3_zonemap_opt.json",
    }
    redis_metrics = {}
    for k, p in redis_test_paths.items():
        s = load_json(p)["summary"]
        redis_metrics[k] = {
            "db_ms": s["t_db_ms"]["mean"],
            "app_ms": s["t_app_ms"]["mean"],
            "total_ms": s["t_total_ms"]["mean"],
        }

    cass_raw = {
        "base_b4": load_json(DIR_BASE / "test_cassandra_q3_b4.json"),
        "base_b8": load_json(DIR_BASE / "test_cassandra_q3_b8.json"),
        "base_b16": load_json(DIR_BASE / "test_cassandra_q3_b16.json"),
        "n1_b4": load_json(DIR_N1 / "test_cassandra_q3_b4_opt.json"),
        "n1_b8": load_json(DIR_N1 / "test_cassandra_q3_b8_opt.json"),
        "n1_b16": load_json(DIR_N1 / "test_cassandra_q3_b16_opt.json"),
        "n2_b16_hash": load_json(DIR_N2 / "test_cassandra_q3_b16_hash_opt.json"),
        "n2_b4_zm": load_json(DIR_N2 / "test_cassandra_q3_b4_zm_opt.json"),
        "n2_b8_zm": load_json(DIR_N2 / "test_cassandra_q3_b8_zm_opt.json"),
        "n2_b16_hash_zm": load_json(DIR_N2 / "test_cassandra_q3_b16_hash_zm_opt.json"),
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
    hists = {
        "b4": parse_tablehistograms(DIR_BASE / "tablehistograms_q3_b4.txt"),
        "b8": parse_tablehistograms(DIR_BASE / "tablehistograms_q3_b8.txt"),
        "b16": parse_tablehistograms(DIR_BASE / "tablehistograms_q3_b16.txt"),
        "b16_hash": parse_tablehistograms(DIR_N2 / "tablehistograms_q3_b16_hash.txt"),
    }

    # 3. Gerar gráficos dedicados
    plot_redis_memoria_carga(loads)
    plot_redis_evolucao(redis_metrics)
    plot_cassandra_histograms(hists, tstats)
    plot_cassandra_q3_opt(cass_raw)

    print("Imagens Overleaf geradas com sucesso em overleaf/imagens/:")
    for p in sorted(IMG_DIR.rglob("*.png")):
        print(f"  - {p.relative_to(OVERLEAF_DIR)} ({p.stat().st_size:,} bytes)".replace(",", "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
