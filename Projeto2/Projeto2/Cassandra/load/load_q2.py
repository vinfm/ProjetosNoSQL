import argparse
import json
import time
from cassandra.concurrent import execute_concurrent_with_args
from common.config import (
    REGIONS, RESULTS_DIR, TABLES, TYPE_SUFFIXES,
    get_cassandra_session, get_mysql_conn, save_json,
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", required=True, choices=["full", "min"])
    args = p.parse_args()

    cluster, session = get_cassandra_session()
    conn = get_mysql_conn()

    if args.mode == "full":
        sql = f"""
            SELECT
                p.p_size, p.p_partkey, ps.ps_supplycost, s.s_suppkey,
                s.s_acctbal, s.s_name, n.n_name, p.p_mfgr,
                s.s_address, s.s_phone, s.s_comment
            FROM {TABLES['part']} p
            JOIN {TABLES['partsupp']} ps ON ps.ps_partkey = p.p_partkey
            JOIN {TABLES['supplier']} s ON s.s_suppkey = ps.ps_suppkey
            JOIN {TABLES['nation']} n ON n.n_nationkey = s.s_nationkey
            JOIN {TABLES['region']} r ON r.r_regionkey = n.n_regionkey
            WHERE r.r_name = %s AND p.p_type LIKE CONCAT('%', %s)
        """
        stmt = session.prepare("""
            INSERT INTO q2_offers_by_filter (
                region_name, part_size, type_suffix, partkey, supplycost, suppkey,
                acctbal, supplier_name, nation_name, mfgr, address, phone, supplier_comment
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)
    else:
        sql = f"""
            WITH min_cost AS (
                SELECT ps.ps_partkey, MIN(ps.ps_supplycost) AS min_sc
                FROM {TABLES['partsupp']} ps
                JOIN {TABLES['supplier']} s ON s.s_suppkey = ps.ps_suppkey
                JOIN {TABLES['nation']} n ON n.n_nationkey = s.s_nationkey
                JOIN {TABLES['region']} r ON r.r_regionkey = n.n_regionkey
                WHERE r.r_name = %s
                GROUP BY ps.ps_partkey
            )
            SELECT
                p.p_size, p.p_partkey, ps.ps_supplycost, s.s_suppkey,
                s.s_acctbal, s.s_name, n.n_name, p.p_mfgr,
                s.s_address, s.s_phone, s.s_comment
            FROM {TABLES['part']} p
            JOIN {TABLES['partsupp']} ps ON ps.ps_partkey = p.p_partkey
            JOIN min_cost mc ON mc.ps_partkey = ps.ps_partkey AND mc.min_sc = ps.ps_supplycost
            JOIN {TABLES['supplier']} s ON s.s_suppkey = ps.ps_suppkey
            JOIN {TABLES['nation']} n ON n.n_nationkey = s.s_nationkey
            JOIN {TABLES['region']} r ON r.r_regionkey = n.n_regionkey
            WHERE r.r_name = %s AND p.p_type LIKE CONCAT('%', %s)
        """
        stmt = session.prepare("""
            INSERT INTO q2_min_offers_by_filter (
                region_name, part_size, type_suffix, acctbal, nation_name, supplier_name,
                partkey, suppkey, mfgr, address, phone, supplier_comment, supplycost
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)

    t_wall0 = time.perf_counter()
    t_ext = t_tr = t_load = 0.0
    total_rows = 0

    for region in REGIONS:
        for suffix in TYPE_SUFFIXES:
            cur = conn.cursor()
            t0 = time.perf_counter()
            cur.execute(sql, (region, suffix) if args.mode == "full" else (region, region, suffix))
            rows = cur.fetchall()
            t_ext += time.perf_counter() - t0
            cur.close()
            total_rows += len(rows)

            t0 = time.perf_counter()
            if args.mode == "full":
                params = [
                    (
                        region, int(r[0]), suffix, int(r[1]), r[2], int(r[3]),
                        r[4], r[5].strip(), r[6].strip(), r[7].strip(),
                        r[8].strip(), r[9].strip(), r[10].strip(),
                    )
                    for r in rows
                ]
            else:
                params = [
                    (
                        region, int(r[0]), suffix, r[4], r[6].strip(), r[5].strip(),
                        int(r[1]), int(r[3]), r[7].strip(), r[8].strip(),
                        r[9].strip(), r[10].strip(), r[2],
                    )
                    for r in rows
                ]
            t_tr += time.perf_counter() - t0

            t0 = time.perf_counter()
            execute_concurrent_with_args(session, stmt, params, concurrency=64)
            t_load += time.perf_counter() - t0
            print(f"  [cassandra q2_{args.mode}] ({region}, {suffix}): {len(params)} linhas")

    conn.close()
    cluster.shutdown()
    stats = {
        "query": f"q2_{args.mode}", "rows_loaded": total_rows,
        "t_extract_s": t_ext, "t_transform_s": t_tr, "t_load_s": t_load,
        "t_wall_total_s": time.perf_counter() - t_wall0,
    }
    save_json(RESULTS_DIR / f"load_cassandra_q2_{args.mode}.json", stats)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()