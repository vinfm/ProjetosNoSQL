import argparse
import json
import time
from collections import defaultdict
from common.codecs import get_redis_memory, q2_key
from common.config import (
    REGIONS, RESULTS_DIR, TABLES, TYPE_SUFFIXES,
    get_mysql_conn, get_redis_client, save_json,
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", required=True, choices=["full", "min"])
    p.add_argument("--flush", action="store_true")
    args = p.parse_args()

    r = get_redis_client()
    if args.flush:
        r.flushdb()

    sql_full = f"""
        SELECT
            p.p_size, p.p_partkey, ps.ps_supplycost, s.s_suppkey,
            s.s_acctbal, n.n_name, s.s_name, p.p_mfgr,
            s.s_address, s.s_phone, s.s_comment
        FROM {TABLES['part']} p
        JOIN {TABLES['partsupp']} ps ON ps.ps_partkey = p.p_partkey
        JOIN {TABLES['supplier']} s ON s.s_suppkey = ps.ps_suppkey
        JOIN {TABLES['nation']} n ON n.n_nationkey = s.s_nationkey
        JOIN {TABLES['region']} r ON r.r_regionkey = n.n_regionkey
        WHERE r.r_name = %s AND p.p_type LIKE CONCAT('%', %s)
    """

    sql_min = f"""
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
            s.s_acctbal, n.n_name, s.s_name, p.p_mfgr,
            s.s_address, s.s_phone, s.s_comment
        FROM {TABLES['part']} p
        JOIN {TABLES['partsupp']} ps ON ps.ps_partkey = p.p_partkey
        JOIN min_cost mc ON mc.ps_partkey = ps.ps_partkey AND mc.min_sc = ps.ps_supplycost
        JOIN {TABLES['supplier']} s ON s.s_suppkey = ps.ps_suppkey
        JOIN {TABLES['nation']} n ON n.n_nationkey = s.s_nationkey
        JOIN {TABLES['region']} r ON r.r_regionkey = n.n_regionkey
        WHERE r.r_name = %s AND p.p_type LIKE CONCAT('%', %s)
        ORDER BY s.s_acctbal DESC, n.n_name ASC, s.s_name ASC, p.p_partkey ASC, s.s_suppkey ASC
    """

    conn = get_mysql_conn()
    t_wall0 = time.perf_counter()
    t_ext = t_tr = t_load = 0.0
    total_rows = 0

    for region in REGIONS:
        for suffix in TYPE_SUFFIXES:
            cur = conn.cursor()
            t0 = time.perf_counter()
            cur.execute(sql_full, (region, suffix)) if args.mode == "full" else cur.execute(sql_min, (region, region, suffix))
            rows = cur.fetchall()
            t_ext += time.perf_counter() - t0
            cur.close()
            total_rows += len(rows)

            t0 = time.perf_counter()
            by_size = defaultdict(list)
            for row in rows:
                by_size[int(row[0])].append(json.dumps([
                    int(row[1]), str(row[2]), int(row[3]), str(row[4]),
                    row[5].strip(), row[6].strip(), row[7].strip(),
                    row[8].strip(), row[9].strip(), row[10].strip(),
                ], separators=(",", ":")))
            t_tr += time.perf_counter() - t0

            t0 = time.perf_counter()
            tmp_pipe = r.pipeline(transaction=False)
            for sz in range(1, 51):
                tk = q2_key(args.mode, region, sz, suffix, temp=True)
                tmp_pipe.delete(tk)
                items = by_size.get(sz)
                if items:
                    for i in range(0, len(items), 1000):
                        tmp_pipe.rpush(tk, *items[i:i + 1000])
            tmp_pipe.execute()

            pub_pipe = r.pipeline(transaction=True)
            for sz in range(1, 51):
                tk = q2_key(args.mode, region, sz, suffix, temp=True)
                fk = q2_key(args.mode, region, sz, suffix, temp=False)
                pub_pipe.rename(tk, fk) if sz in by_size else pub_pipe.delete(fk)
            pub_pipe.execute()
            t_load += time.perf_counter() - t0
            print(f"  [redis q2_{args.mode}] ({region}, {suffix}): {len(rows)} linhas")

    conn.close()
    stats = {
        "query": f"q2_{args.mode}", "rows_loaded": total_rows,
        "t_extract_s": t_ext, "t_transform_s": t_tr, "t_load_s": t_load,
        "t_wall_total_s": time.perf_counter() - t_wall0,
        "memory": get_redis_memory(r),
    }
    save_json(RESULTS_DIR / f"load_redis_q2_{args.mode}.json", stats)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()