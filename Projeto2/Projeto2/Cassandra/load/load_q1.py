import json
import time
from cassandra.concurrent import execute_concurrent_with_args
from common.config import (
    RESULTS_DIR, TABLES, get_cassandra_session, get_mysql_conn, save_json,
)


def main():
    cluster, session = get_cassandra_session()
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
    params = [
        (r[0].strftime("%Y-%m"), r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], int(r[8]))
        for r in rows
    ]
    t_tr = time.perf_counter() - t0

    stmt = session.prepare("""
        INSERT INTO q1_daily_by_month (
            ship_month, shipdate, returnflag, linestatus,
            sum_qty, sum_base_price, sum_disc_price, sum_charge, sum_discount, count_order
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """)
    t0 = time.perf_counter()
    execute_concurrent_with_args(session, stmt, params, concurrency=64)
    t_load = time.perf_counter() - t0
    cluster.shutdown()

    stats = {
        "query": "q1", "rows_loaded": len(params),
        "t_extract_s": t_ext, "t_transform_s": t_tr, "t_load_s": t_load,
        "t_wall_total_s": time.perf_counter() - t_wall0,
    }
    save_json(RESULTS_DIR / "load_cassandra_q1.json", stats)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()