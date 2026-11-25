import argparse
import json
import time
from decimal import Decimal
import redis
from common.codecs import q2_key
from common.config import get_redis_client, q_dec
from common.test_runner import run_test


def format_row(x) -> dict:
    return {
        "acctbal": q_dec(x[3], "0.01"), "supplier_name": x[5], "nation_name": x[4],
        "partkey": x[0], "mfgr": x[6], "address": x[7], "phone": x[8], "supplier_comment": x[9],
    }


def exec_q2(r: redis.Redis, mode: str, region: str, size: int, suffix: str):
    t_tot0 = time.perf_counter()
    key = q2_key(mode, region, size, suffix)

    t0 = time.perf_counter()
    raw_items = r.lrange(key, 0, -1 if mode == "full" else 99)
    t_db = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    if mode == "full":
        min_by_part = {}
        for raw in raw_items:
            rec = json.loads(raw)
            pk, sc = rec[0], Decimal(rec[1])
            cur = min_by_part.get(pk)
            if cur is None or sc < cur[0]:
                min_by_part[pk] = (sc, [rec])
            elif sc == cur[0]:
                cur[1].append(rec)
        winners = [rec for _, recs in min_by_part.values() for rec in recs]
        winners.sort(key=lambda x: (-Decimal(x[3]), x[4], x[5], x[0], x[2]))
        res = [format_row(x) for x in winners[:100]]
    else:
        res = [format_row(json.loads(raw)) for raw in raw_items]
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

    r = get_redis_client()
    fn = lambda: exec_q2(r, args.mode, args.region, args.size, args.suffix)
    run_test("redis", f"q2_{args.mode}", fn, args.warmup, args.repeat, args.validate)