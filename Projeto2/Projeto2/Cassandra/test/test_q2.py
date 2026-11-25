import argparse
import time
from common.config import get_cassandra_session, q_dec
from common.test_runner import run_test


def format_row(r) -> dict:
    return {
        "acctbal": q_dec(r.acctbal, "0.01"), "supplier_name": r.supplier_name,
        "nation_name": r.nation_name, "partkey": r.partkey, "mfgr": r.mfgr,
        "address": r.address, "phone": r.phone, "supplier_comment": r.supplier_comment,
    }


def exec_q2(session, stmt, mode: str, region: str, size: int, suffix: str):
    t_tot0 = time.perf_counter()
    t0 = time.perf_counter()
    rows = list(session.execute(stmt, (region, size, suffix)))
    t_db = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    if mode == "full":
        winners = []
        cur_pk = None
        cur_min = None
        for r in rows:
            if r.partkey != cur_pk:
                cur_pk = r.partkey
                cur_min = r.supplycost
                winners.append(r)
            elif r.supplycost == cur_min:
                winners.append(r)
        winners.sort(key=lambda r: (-r.acctbal, r.nation_name, r.supplier_name, r.partkey, r.suppkey))
        res = [format_row(r) for r in winners[:100]]
    else:
        res = [format_row(r) for r in rows]
    t_app = (time.perf_counter() - t0) * 1000.0
    return res, {"t_db_ms": t_db, "t_app_ms": t_app, "t_total_ms": (time.perf_counter() - t_tot0) * 1000.0}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--mode", required=True, choices=["full", "min"])
    p.add_argument("--region", default="EUROPE")
    p.add_argument("--size", type=int, default=15)
    p.add_argument("--suffix", default="BRASS")
    p.add_argument("--warmup", type=int, default=3)
    p.add_argument("--repeat", type=int, default=30)
    p.add_argument("--validate", action="store_true")
    args = p.parse_args()

    cluster, session = get_cassandra_session()
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

    fn = lambda: exec_q2(session, stmt, args.mode, args.region, args.size, args.suffix)
    run_test("cassandra", f"q2_{args.mode}", fn, args.warmup, args.repeat, args.validate)
    cluster.shutdown()