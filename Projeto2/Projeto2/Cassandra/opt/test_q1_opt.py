import argparse
import time
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from cassandra.query import tuple_factory
from common.config import get_cassandra_session, months_between, q_dec
from common.test_runner import run_test


def exec_q1_opt(session, stmt, delta: int, concurrency: int = 32):
    t_tot0 = time.perf_counter()
    cutoff = date(1998, 12, 1) - timedelta(days=delta)
    months = months_between(1992, 1, cutoff)
    t_db = t_app = 0.0
    acc = defaultdict(lambda: [Decimal(0), Decimal(0), Decimal(0), Decimal(0), Decimal(0), 0])

    for i in range(0, len(months), concurrency):
        t0 = time.perf_counter()
        futs = [session.execute_async(stmt, (ym, cutoff)) for ym in months[i:i + concurrency]]
        batch_rows = [list(f.result()) for f in futs]
        t_db += (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        for rows in batch_rows:
            for rf, ls, sq, sbp, sdp, sc, sdisc, cnt in rows:
                g = acc[(rf, ls)]
                g[0] += sq
                g[1] += sbp
                g[2] += sdp
                g[3] += sc
                g[4] += sdisc
                g[5] += cnt
        t_app += (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    res = []
    for rf, ls in sorted(acc):
        sq, sbp, sdp, sc, sdisc, cnt = acc[(rf, ls)]
        res.append({
            "returnflag": rf, "linestatus": ls,
            "sum_qty": q_dec(sq, "0.01"), "sum_base_price": q_dec(sbp, "0.01"),
            "sum_disc_price": q_dec(sdp, "0.0001"), "sum_charge": q_dec(sc, "0.000001"),
            "avg_qty": q_dec(sq / cnt, "0.0001"), "avg_price": q_dec(sbp / cnt, "0.0001"),
            "avg_disc": q_dec(sdisc / cnt, "0.0001"), "count_order": cnt,
        })
    t_app += (time.perf_counter() - t0) * 1000.0
    return res, {"t_db_ms": t_db, "t_app_ms": t_app, "t_total_ms": (time.perf_counter() - t_tot0) * 1000.0}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--delta", type=int, default=90)
    p.add_argument("--concurrency", type=int, default=32)
    p.add_argument("--warmup", type=int, default=3)
    p.add_argument("--repeat", type=int, default=30)
    p.add_argument("--validate", action="store_true")
    args = p.parse_args()

    cluster, session = get_cassandra_session()
    session.row_factory = tuple_factory
    stmt = session.prepare(
        "SELECT returnflag, linestatus, sum_qty, sum_base_price, sum_disc_price, "
        "sum_charge, sum_discount, count_order FROM q1_daily_by_month "
        "WHERE ship_month = ? AND shipdate <= ?"
    )
    run_test(
        "cassandra",
        "q1",
        lambda: exec_q1_opt(session, stmt, args.delta, args.concurrency),
        args.warmup,
        args.repeat,
        args.validate,
        extra_meta={"opt_level": "nivel1", "concurrency": args.concurrency, "row_factory": "tuple_factory"},
        filename_suffix="_opt",
    )
    cluster.shutdown()
