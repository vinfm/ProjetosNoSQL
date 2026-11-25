import argparse
import time
from cassandra.query import tuple_factory
from common.config import get_cassandra_session, q_dec
from common.test_runner import run_test


def format_row_full(r) -> dict:
    return {
        "acctbal": q_dec(r[3], "0.01"), "supplier_name": r[4],
        "nation_name": r[5], "partkey": r[0], "mfgr": r[6],
        "address": r[7], "phone": r[8], "supplier_comment": r[9],
    }


def format_row_min(r) -> dict:
    return {
        "acctbal": q_dec(r[0], "0.01"), "supplier_name": r[1],
        "nation_name": r[2], "partkey": r[3], "mfgr": r[4],
        "address": r[5], "phone": r[6], "supplier_comment": r[7],
    }


def exec_q2_opt(session, stmt, mode: str, region: str, size: int, suffix: str):
    t_tot0 = time.perf_counter()
    t0 = time.perf_counter()
    rows = list(session.execute(stmt, (region, size, suffix)))
    t_db = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    if mode == "full":
        winners = []
        append_w = winners.append
        cur_pk = None
        cur_min = None
        for r in rows:
            pk = r[0]
            sc = r[1]
            if pk != cur_pk:
                cur_pk = pk
                cur_min = sc
                append_w(r)
            elif sc == cur_min:
                append_w(r)
        winners.sort(key=lambda r: (-r[3], r[5], r[4], r[0], r[2]))
        res = [format_row_full(r) for r in winners[:100]]
    else:
        res = [format_row_min(r) for r in rows]
    t_app = (time.perf_counter() - t0) * 1000.0
    return res, {"t_db_ms": t_db, "t_app_ms": t_app, "t_total_ms": (time.perf_counter() - t_tot0) * 1000.0}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--mode", required=True, choices=["full", "min"])
    p.add_argument("--region", default="EUROPE")
    p.add_argument("--size", type=int, default=15)
    p.add_argument("--suffix", default="BRASS")
    p.add_argument("--fetch-size", type=int, default=50000)
    p.add_argument("--warmup", type=int, default=3)
    p.add_argument("--repeat", type=int, default=30)
    p.add_argument("--validate", action="store_true")
    args = p.parse_args()

    cluster, session = get_cassandra_session(fetch_size=args.fetch_size)
    session.row_factory = tuple_factory
    if args.mode == "full":
        stmt = session.prepare(
            "SELECT partkey, supplycost, suppkey, acctbal, supplier_name, nation_name, "
            "mfgr, address, phone, supplier_comment FROM q2_offers_by_filter "
            "WHERE region_name = ? AND part_size = ? AND type_suffix = ?"
        )
    else:
        stmt = session.prepare(
            "SELECT acctbal, supplier_name, nation_name, partkey, mfgr, address, phone, "
            "supplier_comment FROM q2_min_offers_by_filter "
            "WHERE region_name = ? AND part_size = ? AND type_suffix = ? LIMIT 100"
        )

    fn = lambda: exec_q2_opt(session, stmt, args.mode, args.region, args.size, args.suffix)
    run_test(
        "cassandra",
        f"q2_{args.mode}",
        fn,
        args.warmup,
        args.repeat,
        args.validate,
        extra_meta={"opt_level": "nivel1", "fetch_size": args.fetch_size, "row_factory": "tuple_factory"},
        filename_suffix="_opt",
    )
    cluster.shutdown()
