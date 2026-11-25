import argparse
import time
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
import redis
from common.codecs import Q1_DAYS_KEY, q1_day_key
from common.config import date_to_int, get_redis_client, q_dec
from common.test_runner import run_test


def exec_q1(r: redis.Redis, delta: int):
    t_tot0 = time.perf_counter()
    cutoff_int = date_to_int(date(1998, 12, 1) - timedelta(days=delta))

    t0 = time.perf_counter()
    days = r.zrangebyscore(Q1_DAYS_KEY, "-inf", cutoff_int)
    pipe = r.pipeline(transaction=False)
    for d in days:
        pipe.hgetall(q1_day_key(d.decode()))
    day_hashes = pipe.execute()
    t_db = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    acc = defaultdict(lambda: [Decimal(0), Decimal(0), Decimal(0), Decimal(0), Decimal(0), 0])
    for h in day_hashes:
        for k, v in h.items():
            parts = v.decode().split("|")
            g = acc[k.decode()]
            for i in range(5):
                g[i] += Decimal(parts[i])
            g[5] += int(parts[5])

    res = []
    for k in sorted(acc):
        rf, ls = k.split("|")
        sq, sbp, sdp, sc, sdisc, cnt = acc[k]
        res.append({
            "returnflag": rf, "linestatus": ls,
            "sum_qty": q_dec(sq, "0.01"), "sum_base_price": q_dec(sbp, "0.01"),
            "sum_disc_price": q_dec(sdp, "0.0001"), "sum_charge": q_dec(sc, "0.000001"),
            "avg_qty": q_dec(sq / cnt, "0.0001"), "avg_price": q_dec(sbp / cnt, "0.0001"),
            "avg_disc": q_dec(sdisc / cnt, "0.0001"), "count_order": cnt,
        })
    t_app = (time.perf_counter() - t0) * 1000.0
    return res, {"t_db_ms": t_db, "t_app_ms": t_app, "t_total_ms": (time.perf_counter() - t_tot0) * 1000.0}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--delta", type=int, default=90)
    p.add_argument("--warmup", type=int, default=3)
    p.add_argument("--repeat", type=int, default=30)
    p.add_argument("--validate", action="store_true")
    args = p.parse_args()

    r = get_redis_client()
    run_test("redis", "q1", lambda: exec_q1(r, args.delta), args.warmup, args.repeat, args.validate)