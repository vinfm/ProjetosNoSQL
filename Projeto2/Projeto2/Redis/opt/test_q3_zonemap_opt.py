import argparse
import heapq
import struct
import time
from datetime import date
from decimal import Decimal
import redis.connection as rc
from common.codecs import ITEM_REC, ORDER_HDR, calc_q3_order_revenue, q3_day_key, q3_days_key
from common.config import date_to_int, get_redis_client, q_dec
from common.test_runner import run_test

_HDR_SIZE = ORDER_HDR.size
_ITEM_SIZE = ITEM_REC.size
_SPRIO_UNPACK = struct.Struct("<i").unpack_from
_ITEMS_UNPACK = {
    _HDR_SIZE + n * _ITEM_SIZE: struct.Struct("<" + "IqH" * n).unpack_from
    for n in range(1, 8)
}


def exec_q3_zonemap_opt(r, segment: str, cutoff_str: str, pipe_chunk: int = 60):
    t_tot0 = time.perf_counter()
    cutoff_int = date_to_int(date.fromisoformat(cutoff_str))
    t_db = t_app = 0.0

    t0 = time.perf_counter()
    init_pipe = r.pipeline(transaction=False)
    init_pipe.zrangebyscore(q3_days_key(segment), "-inf", f"({cutoff_int}")
    init_pipe.hgetall(f"tpch:q3:zonemap:{segment}")
    days, zm = init_pipe.execute()
    t_db += (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    days = [d for d in days if int(zm.get(d, b"0")) > cutoff_int]
    t_app += (time.perf_counter() - t0) * 1000.0

    candidates = []
    append_cand = candidates.append
    items_unpack_get = _ITEMS_UNPACK.get
    sprio_unpack = _SPRIO_UNPACK
    hdr_size = _HDR_SIZE

    for i in range(0, len(days), pipe_chunk):
        chunk = days[i:i + pipe_chunk]
        t0 = time.perf_counter()
        pipe = r.pipeline(transaction=False)
        for d in chunk:
            pipe.hgetall(q3_day_key(segment, d.decode("ascii")))
        hashes = pipe.execute()
        t_db += (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        for d_b, h in zip(chunk, hashes):
            d_str = None
            for okey_b, blob in h.items():
                unpack_items = items_unpack_get(len(blob))
                if unpack_items is not None:
                    vals = unpack_items(blob, hdr_size)
                    rev_1e4 = 0
                    for j in range(0, len(vals), 3):
                        if vals[j] > cutoff_int:
                            rev_1e4 += vals[j + 1] * (100 - vals[j + 2])
                    if rev_1e4 > 0:
                        if d_str is None:
                            d_str = d_b.decode("ascii")
                        append_cand((rev_1e4, d_str, int(okey_b), sprio_unpack(blob, 0)[0]))
                else:
                    sprio, rev_1e4 = calc_q3_order_revenue(blob, cutoff_int)
                    if rev_1e4 > 0:
                        if d_str is None:
                            d_str = d_b.decode("ascii")
                        append_cand((rev_1e4, d_str, int(okey_b), sprio))
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
    p.add_argument("--pipe-chunk", type=int, default=60)
    p.add_argument("--warmup", type=int, default=3)
    p.add_argument("--repeat", type=int, default=30)
    p.add_argument("--validate", action="store_true")
    args = p.parse_args()

    r = get_redis_client()
    run_test(
        "redis",
        "q3",
        lambda: exec_q3_zonemap_opt(r, args.segment, args.date, args.pipe_chunk),
        args.warmup,
        args.repeat,
        args.validate,
        extra_meta={
            "opt_level": "nivel2",
            "zonemap": True,
            "hiredis": bool(getattr(rc, "HIREDIS_AVAILABLE", False)),
            "pipe_chunk": args.pipe_chunk,
        },
        filename_suffix="_zonemap_opt",
    )
