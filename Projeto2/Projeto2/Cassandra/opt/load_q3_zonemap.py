import json
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from cassandra.concurrent import execute_concurrent_with_args
from common.config import (
    RESULTS_DIR, SEGMENTS, TABLES, get_cassandra_session, get_mysql_conn, save_json,
)


def _extract_segment_monthly_zonemap(seg: str) -> list[tuple[str, str, date]]:
    sql = f"""
        SELECT o.o_orderdate, MAX(l.l_shipdate)
        FROM {TABLES['customer']} c
        JOIN {TABLES['orders']} o ON c.c_custkey = o.o_custkey
        JOIN {TABLES['lineitem']} l ON l.l_orderkey = o.o_orderkey
        WHERE c.c_mktsegment = %s AND o.o_orderdate >= %s AND o.o_orderdate < %s
        GROUP BY o.o_orderdate
    """
    max_by_month: dict[str, date] = {}
    conn = get_mysql_conn()
    try:
        for year in range(1992, 1999):
            cur = conn.cursor()
            cur.execute(sql, (seg, f"{year}-01-01", f"{year + 1}-01-01"))
            for odate, max_sdate in cur.fetchall():
                ym = odate.strftime("%Y-%m")
                prev = max_by_month.get(ym)
                if prev is None or max_sdate > prev:
                    max_by_month[ym] = max_sdate
            cur.close()
    finally:
        conn.close()
    return [(seg, ym, max_sd) for ym, max_sd in sorted(max_by_month.items())]


def main():
    t_wall0 = time.perf_counter()

    t0 = time.perf_counter()
    params: list[tuple[str, str, date]] = []
    with ProcessPoolExecutor(max_workers=len(SEGMENTS)) as ex:
        for seg_rows in ex.map(_extract_segment_monthly_zonemap, SEGMENTS):
            params.extend(seg_rows)
    t_ext = time.perf_counter() - t0

    cluster, session = get_cassandra_session()
    stmt = session.prepare(
        "INSERT INTO q3_zonemap (segment, order_month, max_shipdate) VALUES (?, ?, ?)"
    )
    t0 = time.perf_counter()
    execute_concurrent_with_args(session, stmt, params, concurrency=32)
    t_load = time.perf_counter() - t0
    cluster.shutdown()

    stats = {
        "query": "q3_zonemap",
        "rows_loaded": len(params),
        "t_extract_s": t_ext,
        "t_transform_s": 0.0,
        "t_load_s": t_load,
        "t_wall_total_s": time.perf_counter() - t_wall0,
    }
    save_json(RESULTS_DIR / "load_cassandra_q3_zonemap.json", stats)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
