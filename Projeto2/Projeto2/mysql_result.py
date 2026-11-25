import argparse
from datetime import date, timedelta
from common.config import RESULTS_DIR, TABLES, get_mysql_conn, q_dec, save_json


def run_q1(cur, delta: int):
    cutoff = date(1998, 12, 1) - timedelta(days=delta)
    sql = f"""
        SELECT
            l_returnflag, l_linestatus,
            SUM(l_quantity), SUM(l_extendedprice),
            SUM(l_extendedprice * (1 - l_discount)),
            SUM(l_extendedprice * (1 - l_discount) * (1 + l_tax)),
            AVG(l_quantity), AVG(l_extendedprice), AVG(l_discount), COUNT(*)
        FROM {TABLES['lineitem']}
        WHERE l_shipdate <= %s
        GROUP BY l_returnflag, l_linestatus
        ORDER BY l_returnflag, l_linestatus
    """
    cur.execute(sql, (cutoff,))
    return [
        {
            "returnflag": r[0], "linestatus": r[1],
            "sum_qty": q_dec(r[2], "0.01"), "sum_base_price": q_dec(r[3], "0.01"),
            "sum_disc_price": q_dec(r[4], "0.0001"), "sum_charge": q_dec(r[5], "0.000001"),
            "avg_qty": q_dec(r[6], "0.0001"), "avg_price": q_dec(r[7], "0.0001"),
            "avg_disc": q_dec(r[8], "0.0001"), "count_order": int(r[9]),
        }
        for r in cur.fetchall()
    ]


def run_q2(cur, region: str, size: int, suffix: str):
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
            s.s_acctbal, s.s_name, n.n_name, p.p_partkey,
            p.p_mfgr, s.s_address, s.s_phone, s.s_comment
        FROM {TABLES['part']} p
        JOIN {TABLES['partsupp']} ps ON ps.ps_partkey = p.p_partkey
        JOIN min_cost mc ON mc.ps_partkey = ps.ps_partkey AND mc.min_sc = ps.ps_supplycost
        JOIN {TABLES['supplier']} s ON s.s_suppkey = ps.ps_suppkey
        JOIN {TABLES['nation']} n ON n.n_nationkey = s.s_nationkey
        JOIN {TABLES['region']} r ON r.r_regionkey = n.n_regionkey
        WHERE p.p_size = %s AND p.p_type LIKE CONCAT('%', %s) AND r.r_name = %s
        ORDER BY s.s_acctbal DESC, n.n_name ASC, s.s_name ASC, p.p_partkey ASC
        LIMIT 100
    """
    cur.execute(sql, (region, size, suffix, region))
    return [
        {
            "acctbal": q_dec(r[0], "0.01"), "supplier_name": r[1].strip(),
            "nation_name": r[2].strip(), "partkey": int(r[3]), "mfgr": r[4].strip(),
            "address": r[5].strip(), "phone": r[6].strip(), "supplier_comment": r[7].strip(),
        }
        for r in cur.fetchall()
    ]


def run_q3(cur, segment: str, cutoff_str: str):
    sql = f"""
        SELECT
            l.l_orderkey, SUM(l.l_extendedprice * (1 - l.l_discount)) AS revenue,
            o.o_orderdate, o.o_shippriority
        FROM {TABLES['customer']} c
        JOIN {TABLES['orders']} o ON c.c_custkey = o.o_custkey
        JOIN {TABLES['lineitem']} l ON l.l_orderkey = o.o_orderkey
        WHERE c.c_mktsegment = %s AND o.o_orderdate < %s AND l.l_shipdate > %s
        GROUP BY l.l_orderkey, o.o_orderdate, o.o_shippriority
        ORDER BY revenue DESC, o.o_orderdate ASC
        LIMIT 10
    """
    cur.execute(sql, (segment, cutoff_str, cutoff_str))
    return [
        {
            "orderkey": int(r[0]), "revenue": q_dec(r[1], "0.0001"),
            "orderdate": r[2].isoformat(), "shippriority": int(r[3]),
        }
        for r in cur.fetchall()
    ]


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--delta", type=int, default=90)
    p.add_argument("--region", default="EUROPE")
    p.add_argument("--size", type=int, default=15)
    p.add_argument("--suffix", default="BRASS")
    p.add_argument("--segment", default="BUILDING")
    p.add_argument("--date", default="1995-03-15")
    args = p.parse_args()

    conn = get_mysql_conn()
    cur = conn.cursor()
    for name, fn in [
        ("ref_q1.json", lambda: run_q1(cur, args.delta)),
        ("ref_q2.json", lambda: run_q2(cur, args.region, args.size, args.suffix)),
        ("ref_q3.json", lambda: run_q3(cur, args.segment, args.date)),
    ]:
        rows = fn()
        save_json(RESULTS_DIR / name, rows)
        print(f"Salvo {name}: {len(rows)} linhas")
    cur.close()
    conn.close()