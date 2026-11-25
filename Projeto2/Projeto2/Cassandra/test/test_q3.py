import argparse
import heapq
import time
from datetime import date
from decimal import Decimal
from common.config import get_cassandra_session, months_between, q_dec
from common.test_runner import run_test


def exec_q3(session, stmt, segment: str, cutoff_str: str, num_buckets: int):
    t_tot0 = time.perf_counter()
    cutoff = date.fromisoformat(cutoff_str)
    months = months_between(1992, 1, cutoff)
    t_db = t_app = 0.0
    rev_by_order = {}

    for ym in months:
        for b in range(num_buckets):
            t0 = time.perf_counter()
            rows = list(session.execute(stmt, (segment, ym, b, cutoff)))
            t_db += (time.perf_counter() - t0) * 1000.0

            t0 = time.perf_counter()
            for r in rows:
                if r.shipdate.date() > cutoff:
                    rev = r.extendedprice * (Decimal(1) - r.discount)
                    rec = rev_by_order.get(r.orderkey)
                    if rec is None:
                        rev_by_order[r.orderkey] = [rev, r.order_date.date().isoformat(), r.shippriority]
                    else:
                        rec[0] += rev
            t_app += (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    top10 = heapq.nsmallest(
        10,
        ((v[0], v[1], k, v[2]) for k, v in rev_by_order.items()),
        key=lambda x: (-x[0], x[1], x[2]),
    )
    res = [
        {
            "orderkey": x[2],
            "revenue": q_dec(x[0], "0.0001"),
            "orderdate": x[1],
            "shippriority": x[3],
        }
        for x in top10
    ]
    t_app += (time.perf_counter() - t0) * 1000.0
    return res, {"t_db_ms": t_db, "t_app_ms": t_app, "t_total_ms": (time.perf_counter() - t_tot0) * 1000.0}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--segment", default="BUILDING")
    p.add_argument("--date", default="1995-03-15")
    p.add_argument("--buckets", type=int, default=8, choices=[4, 8, 16])
    p.add_argument("--warmup", type=int, default=1)
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--validate", action="store_true")
    args = p.parse_args()

    cluster, session = get_cassandra_session()
    stmt = session.prepare(
        "SELECT order_date, orderkey, shipdate, shippriority, extendedprice, discount "
        "FROM q3_lineitems_by_segment_month "
        "WHERE segment = ? AND order_month = ? AND bucket = ? AND order_date < ?"
    )
    run_test(
        "cassandra",
        "q3",
        lambda: exec_q3(session, stmt, args.segment, args.date, args.buckets),
        args.warmup,
        args.repeat,
        args.validate,
        extra_meta={"buckets": args.buckets},
        filename_suffix=f"_b{args.buckets}",
    )
    cluster.shutdown()