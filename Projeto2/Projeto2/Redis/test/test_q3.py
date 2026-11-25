import argparse
import heapq
import time
from datetime import date
from decimal import Decimal
import redis
from common.codecs import calc_q3_order_revenue, q3_day_key, q3_days_key
from common.config import date_to_int, get_redis_client, q_dec
from common.test_runner import run_test


def exec_q3(r: redis.Redis, segment: str, cutoff_str: str):
    t_tot0 = time.perf_counter()
    cutoff_int = date_to_int(date.fromisoformat(cutoff_str))
    t_db = t_app = 0.0

    t0 = time.perf_counter()
    days = r.zrangebyscore(q3_days_key(segment), "-inf", f"({cutoff_int}")
    t_db += (time.perf_counter() - t0) * 1000.0

    candidates = []
    for i in range(0, len(days), 30):
        chunk = days[i:i + 30]
        t0 = time.perf_counter()
        pipe = r.pipeline(transaction=False)
        for d in chunk:
            pipe.hgetall(q3_day_key(segment, d.decode()))
        hashes = pipe.execute()
        t_db += (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        for d_b, h in zip(chunk, hashes):
            d_str = d_b.decode()
            for okey_b, blob in h.items():
                sprio, rev_1e4 = calc_q3_order_revenue(blob, cutoff_int)
                if rev_1e4 > 0:
                    candidates.append((rev_1e4, d_str, int(okey_b), sprio))
        t_app += (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    top10 = heapq.nsmallest(10, candidates, key=lambda x: (-x[0], x[1], x[2]))
    res = [
        {
            "orderkey": x[2],
            "revenue": q_dec(Decimal(x[0]) / Decimal(10000), "0.0001"),
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
    p.add_argument("--warmup", type=int, default=1)
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--validate", action="store_true")
    args = p.parse_args()

    r = get_redis_client()
    run_test("redis", "q3", lambda: exec_q3(r, args.segment, args.date), args.warmup, args.repeat, args.validate)