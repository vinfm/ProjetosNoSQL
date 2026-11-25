import argparse
import heapq
import time
from datetime import date
from decimal import Decimal
from cassandra import util
from cassandra.query import tuple_factory
from common.config import get_cassandra_session, months_between, q_dec
from common.test_runner import run_test

ONE = Decimal(1)


def exec_q3_opt(session, stmt, segment: str, cutoff_str: str, num_buckets: int, concurrency: int = 16):
    t_tot0 = time.perf_counter()
    cutoff = date.fromisoformat(cutoff_str)
    cutoff_days = util.Date(cutoff).days_from_epoch
    months = months_between(1992, 1, cutoff)
    tasks = [(segment, ym, b, cutoff) for ym in months for b in range(num_buckets)]

    t_db = t_app = 0.0
    rev_by_order = {}
    date_str_cache: dict[int, str] = {}
    one = ONE

    for i in range(0, len(tasks), concurrency):
        t0 = time.perf_counter()
        futs = [session.execute_async(stmt, params) for params in tasks[i:i + concurrency]]
        batch_rows = [list(f.result()) for f in futs]
        t_db += (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        for rows in batch_rows:
            for odate, okey, sdate, sprio, extprice, disc in rows:
                if sdate.days_from_epoch > cutoff_days:
                    rev = extprice * (one - disc)
                    rec = rev_by_order.get(okey)
                    if rec is None:
                        od_days = odate.days_from_epoch
                        od_str = date_str_cache.get(od_days)
                        if od_str is None:
                            od_str = odate.date().isoformat()
                            date_str_cache[od_days] = od_str
                        rev_by_order[okey] = [rev, od_str, sprio]
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
    p.add_argument("--buckets", type=int, default=16, choices=[4, 8, 16])
    p.add_argument("--concurrency", type=int, default=16)
    p.add_argument("--fetch-size", type=int, default=50000)
    p.add_argument("--warmup", type=int, default=1)
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--validate", action="store_true")
    args = p.parse_args()

    cluster, session = get_cassandra_session(fetch_size=args.fetch_size)
    session.row_factory = tuple_factory
    stmt = session.prepare(
        "SELECT order_date, orderkey, shipdate, shippriority, extendedprice, discount "
        "FROM q3_lineitems_by_segment_month "
        "WHERE segment = ? AND order_month = ? AND bucket = ? AND order_date < ?"
    )
    run_test(
        "cassandra",
        "q3",
        lambda: exec_q3_opt(session, stmt, args.segment, args.date, args.buckets, args.concurrency),
        args.warmup,
        args.repeat,
        args.validate,
        extra_meta={
            "opt_level": "nivel1",
            "buckets": args.buckets,
            "concurrency": args.concurrency,
            "fetch_size": args.fetch_size,
            "row_factory": "tuple_factory",
        },
        filename_suffix=f"_b{args.buckets}_opt",
    )
    cluster.shutdown()
