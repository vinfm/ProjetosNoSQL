import argparse
import json
import time
from collections import defaultdict
from decimal import Decimal
from common.codecs import get_redis_memory, pack_q3_order, q3_day_key, q3_days_key
from common.config import (
    RESULTS_DIR, SEGMENTS, TABLES, date_to_int,
    get_mysql_conn, get_redis_client, save_json,
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--flush", action="store_true")
    args = p.parse_args()

    r = get_redis_client()
    if args.flush:
        r.flushdb()

    sql = f"""
        SELECT
            o.o_orderdate, o.o_orderkey, o.o_shippriority,
            l.l_shipdate, l.l_extendedprice, l.l_discount
        FROM {TABLES['customer']} c
        JOIN {TABLES['orders']} o ON c.c_custkey = o.o_custkey
        JOIN {TABLES['lineitem']} l ON l.l_orderkey = o.o_orderkey
        WHERE c.c_mktsegment = %s AND o.o_orderdate >= %s AND o.o_orderdate < %s
    """

    conn = get_mysql_conn()
    t_wall0 = time.perf_counter()
    t_ext = t_tr = t_load = 0.0
    total_orders = total_items = 0

    for seg in SEGMENTS:
        zset_days = {}
        for year in range(1992, 1999):
            cur = conn.cursor()
            t0 = time.perf_counter()
            cur.execute(sql, (seg, f"{year}-01-01", f"{year + 1}-01-01"))
            t_ext += time.perf_counter() - t0

            orders_by_day = defaultdict(dict)
            while True:
                t0 = time.perf_counter()
                batch = cur.fetchmany(50000)
                t_ext += time.perf_counter() - t0
                if not batch:
                    break
                t0 = time.perf_counter()
                for odate, okey, sprio, sdate, extprice, disc in batch:
                    d_str = odate.isoformat()
                    if d_str not in zset_days:
                        zset_days[d_str] = date_to_int(odate)
                    item = (
                        date_to_int(sdate),
                        int((extprice * Decimal("100")).to_integral_value()),
                        int((disc * Decimal("100")).to_integral_value()),
                    )
                    rec = orders_by_day[d_str].get(okey)
                    if rec is None:
                        orders_by_day[d_str][okey] = [int(sprio), [item]]
                    else:
                        rec[1].append(item)
                total_items += len(batch)
                t_tr += time.perf_counter() - t0
            cur.close()

            t0 = time.perf_counter()
            packed_by_day = {}
            for d_str, omap in orders_by_day.items():
                hmap = {str(ok): pack_q3_order(sp, its) for ok, (sp, its) in omap.items()}
                total_orders += len(hmap)
                packed_by_day[d_str] = hmap
            t_tr += time.perf_counter() - t0

            t0 = time.perf_counter()
            pipe = r.pipeline(transaction=False)
            for idx, (d_str, hmap) in enumerate(packed_by_day.items(), 1):
                pipe.hset(q3_day_key(seg, d_str), mapping=hmap)
                if idx % 20 == 0:
                    pipe.execute()
            pipe.execute()
            t_load += time.perf_counter() - t0
            print(f"  [redis q3] {seg} {year}: {len(packed_by_day)} dias")

        if zset_days:
            t0 = time.perf_counter()
            r.zadd(q3_days_key(seg), zset_days)
            t_load += time.perf_counter() - t0

    conn.close()
    stats = {
        "query": "q3", "orders_loaded": total_orders, "items_loaded": total_items,
        "t_extract_s": t_ext, "t_transform_s": t_tr, "t_load_s": t_load,
        "t_wall_total_s": time.perf_counter() - t_wall0,
        "memory": get_redis_memory(r),
    }
    save_json(RESULTS_DIR / "load_redis_q3.json", stats)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()