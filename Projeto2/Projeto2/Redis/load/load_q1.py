import argparse
import json
import time
from collections import defaultdict
from common.codecs import Q1_DAYS_KEY, get_redis_memory, q1_day_key
from common.config import (
    RESULTS_DIR, TABLES, date_to_int, get_mysql_conn, get_redis_client, save_json,
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--flush", action="store_true")
    args = p.parse_args()

    r = get_redis_client()
    if args.flush:
        r.flushdb()

    conn = get_mysql_conn()
    t_wall0 = time.perf_counter()

    sql = f"""
        SELECT
            l_shipdate, l_returnflag, l_linestatus,
            SUM(l_quantity), SUM(l_extendedprice),
            SUM(l_extendedprice * (1 - l_discount)),
            SUM(l_extendedprice * (1 - l_discount) * (1 + l_tax)),
            SUM(l_discount), COUNT(*)
        FROM {TABLES['lineitem']}
        GROUP BY l_shipdate, l_returnflag, l_linestatus
    """
    cur = conn.cursor()
    t0 = time.perf_counter()
    cur.execute(sql)
    rows = cur.fetchall()
    t_ext = time.perf_counter() - t0
    cur.close()
    conn.close()

    t0 = time.perf_counter()
    by_day = defaultdict(dict)
    zset_days = {}
    for row in rows:
        d_str = row[0].isoformat()
        zset_days[d_str] = date_to_int(row[0])
        by_day[d_str][f"{row[1]}|{row[2]}"] = "|".join(str(x) for x in row[3:])
    t_tr = time.perf_counter() - t0

    t0 = time.perf_counter()
    pipe = r.pipeline(transaction=False)
    pipe.zadd(Q1_DAYS_KEY, zset_days)
    for d_str, mapping in by_day.items():
        pipe.hset(q1_day_key(d_str), mapping=mapping)
    pipe.execute()
    t_load = time.perf_counter() - t0

    stats = {
        "query": "q1", "rows_loaded": len(rows),
        "t_extract_s": t_ext, "t_transform_s": t_tr, "t_load_s": t_load,
        "t_wall_total_s": time.perf_counter() - t_wall0,
        "memory": get_redis_memory(r),
    }
    save_json(RESULTS_DIR / "load_redis_q1.json", stats)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()