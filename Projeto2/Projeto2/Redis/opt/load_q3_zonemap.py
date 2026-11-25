import json
import time
from concurrent.futures import ProcessPoolExecutor
from common.codecs import get_redis_memory
from common.config import (
    RESULTS_DIR, SEGMENTS, TABLES, date_to_int, get_mysql_conn, get_redis_client, save_json,
)


def _extract_segment_zonemap(seg: str) -> tuple[str, dict[str, str]]:
    sql = f"""
        SELECT o.o_orderdate, MAX(l.l_shipdate)
        FROM {TABLES['customer']} c
        JOIN {TABLES['orders']} o ON c.c_custkey = o.o_custkey
        JOIN {TABLES['lineitem']} l ON l.l_orderkey = o.o_orderkey
        WHERE c.c_mktsegment = %s AND o.o_orderdate >= %s AND o.o_orderdate < %s
        GROUP BY o.o_orderdate
    """
    mapping: dict[str, str] = {}
    conn = get_mysql_conn()
    try:
        for year in range(1992, 1999):
            cur = conn.cursor()
            cur.execute(sql, (seg, f"{year}-01-01", f"{year + 1}-01-01"))
            for odate, max_sdate in cur.fetchall():
                mapping[odate.isoformat()] = str(date_to_int(max_sdate))
            cur.close()
    finally:
        conn.close()
    return seg, mapping


def main():
    r = get_redis_client()
    t_wall0 = time.perf_counter()

    t0 = time.perf_counter()
    by_seg: dict[str, dict[str, str]] = {}
    with ProcessPoolExecutor(max_workers=len(SEGMENTS)) as ex:
        for seg, mapping in ex.map(_extract_segment_zonemap, SEGMENTS):
            by_seg[seg] = mapping
            print(f"  [redis q3_zonemap] {seg}: {len(mapping)} dias")
    t_ext = time.perf_counter() - t0

    t0 = time.perf_counter()
    pipe = r.pipeline(transaction=False)
    total_rows = 0
    for seg, mapping in by_seg.items():
        if mapping:
            pipe.hset(f"tpch:q3:zonemap:{seg}", mapping=mapping)
            total_rows += len(mapping)
    pipe.execute()
    t_load = time.perf_counter() - t0

    stats = {
        "query": "q3_zonemap",
        "rows_loaded": total_rows,
        "t_extract_s": t_ext,
        "t_transform_s": 0.0,
        "t_load_s": t_load,
        "t_wall_total_s": time.perf_counter() - t_wall0,
        "memory": get_redis_memory(r),
    }
    save_json(RESULTS_DIR / "load_redis_q3_zonemap.json", stats)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
