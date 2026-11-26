import datetime
from decimal import Decimal
import mysql.connector
from pymongo import MongoClient, ReplaceOne, WriteConcern, ASCENDING

MYSQL_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "1234",
    "database": "tpch",
    "charset": "utf8mb4",
}

MONGO_URI = "mongodb://localhost:27021,localhost:27022,localhost:27023/?replicaSet=rs0"
MONGO_DB_NAME = "tpch"
BATCH_SIZE = 20000


def format_val(val):
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, datetime.date) and not isinstance(val, datetime.datetime):
        return datetime.datetime(val.year, val.month, val.day)
    return val


def get_col(row, key):
    val = row.get(key.upper())
    if val is not None:
        return val
    return row.get(key.lower())


def migrate_orders(mysql_conn, mongo_db):
    cursor = mysql_conn.cursor(dictionary=True)
    orders_coll = mongo_db.get_collection("orders", write_concern=WriteConcern(w=1))
    last_key = -1

    while True:
        query_orders = """
            SELECT 
                o.O_ORDERKEY, o.O_ORDERSTATUS, o.O_TOTALPRICE, o.O_ORDERDATE,
                o.O_ORDERPRIORITY, o.O_SHIPPRIORITY,
                c.C_CUSTKEY, c.C_MKTSEGMENT
            FROM ORDERS o
            INNER JOIN CUSTOMER c ON o.O_CUSTKEY = c.C_CUSTKEY
            WHERE o.O_ORDERKEY > %s
            ORDER BY o.O_ORDERKEY ASC
            LIMIT %s
        """
        cursor.execute(query_orders, (last_key, BATCH_SIZE))
        orders_rows = cursor.fetchall()

        if not orders_rows:
            break

        order_keys = [get_col(o, "O_ORDERKEY") for o in orders_rows]
        last_key = order_keys[-1]
        format_keys = ",".join(["%s"] * len(order_keys))

        query_items = f"""
            SELECT 
                l.L_ORDERKEY, l.L_LINENUMBER, l.L_QUANTITY, l.L_EXTENDEDPRICE,
                l.L_DISCOUNT, l.L_TAX, l.L_RETURNFLAG, l.L_LINESTATUS, l.L_SHIPDATE
            FROM LINEITEM l
            WHERE l.L_ORDERKEY IN ({format_keys})
            ORDER BY l.L_ORDERKEY ASC, l.L_LINENUMBER ASC
        """

        cursor.execute(query_items, tuple(order_keys))
        items_rows = cursor.fetchall()

        items_by_order = {}
        for item in items_rows:
            okey = get_col(item, "L_ORDERKEY")
            items_by_order.setdefault(okey, []).append({
                "linenumber": get_col(item, "L_LINENUMBER"),
                "quantity": format_val(get_col(item, "L_QUANTITY")),
                "extended_price": format_val(get_col(item, "L_EXTENDEDPRICE")),
                "discount": format_val(get_col(item, "L_DISCOUNT")),
                "tax": format_val(get_col(item, "L_TAX")),
                "return_flag": get_col(item, "L_RETURNFLAG"),
                "line_status": get_col(item, "L_LINESTATUS"),
                "ship_date": format_val(get_col(item, "L_SHIPDATE")),
            })
        
        bulk_ops = []
        for o in orders_rows:
            okey = get_col(o, "O_ORDERKEY")
            doc = {
                "_id": okey,
                "order_status": get_col(o, "O_ORDERSTATUS"),
                "total_price": format_val(get_col(o, "O_TOTALPRICE")),
                "order_date": format_val(get_col(o, "O_ORDERDATE")),
                "order_priority": get_col(o, "O_ORDERPRIORITY"),
                "ship_priority": get_col(o, "O_SHIPPRIORITY"),
                "customer": {
                    "custkey": get_col(o, "C_CUSTKEY"),
                    "mktsegment": get_col(o, "C_MKTSEGMENT"),
                },
                "line_items": items_by_order.get(okey, []),
            }
            bulk_ops.append(ReplaceOne({"_id": okey}, doc, upsert=True))

        if bulk_ops:
            orders_coll.bulk_write(bulk_ops, ordered=False)

    orders_coll.create_index([("line_items.ship_date", ASCENDING)])
    orders_coll.create_index([
        ("customer.mktsegment", ASCENDING),
        ("order_date", ASCENDING),
        ("line_items.ship_date", ASCENDING)
    ])
    cursor.close()

def migrate_lineitems_plus(mysql_conn, mongo_db):
    cursor = mysql_conn.cursor(dictionary=True, buffered=False)
    li_coll = mongo_db.get_collection("lineitems_plus", write_concern=WriteConcern(w=1))
    batch_limit = int(BATCH_SIZE * 1.5)
    query = """
            SELECT 
                l.L_ORDERKEY, l.L_LINENUMBER, l.L_QUANTITY, l.L_EXTENDEDPRICE,
                l.L_DISCOUNT, l.L_TAX, l.L_RETURNFLAG, l.L_LINESTATUS, l.L_SHIPDATE,
                o.O_ORDERDATE, o.O_SHIPPRIORITY,
                c.C_MKTSEGMENT
            FROM LINEITEM l
            INNER JOIN ORDERS o ON l.L_ORDERKEY = o.O_ORDERKEY
            INNER JOIN CUSTOMER c ON o.O_CUSTKEY = c.C_CUSTKEY
        """
    cursor.execute(query)

    while True:
        rows = cursor.fetchmany(batch_limit)

        if not rows:
            break

        bulk_ops = []
        for r in rows:
            okey = get_col(r, "L_ORDERKEY")
            lnum = get_col(r, "L_LINENUMBER")
            doc_id = f"{okey}_{lnum}"
            doc = {
                "_id": doc_id,
                "orderkey": okey,
                "linenumber": lnum,
                "quantity": format_val(get_col(r, "L_QUANTITY")),
                "extended_price": format_val(get_col(r, "L_EXTENDEDPRICE")),
                "discount": format_val(get_col(r, "L_DISCOUNT")),
                "tax": format_val(get_col(r, "L_TAX")),
                "return_flag": get_col(r, "L_RETURNFLAG"),
                "line_status": get_col(r, "L_LINESTATUS"),
                "ship_date": format_val(get_col(r, "L_SHIPDATE")),
                "order": {
                    "order_date": format_val(get_col(r, "O_ORDERDATE")),
                    "ship_priority": get_col(r, "O_SHIPPRIORITY"),
                    "customer_mktsegment": get_col(r, "C_MKTSEGMENT"),
                }
            }
            bulk_ops.append(ReplaceOne({"_id": doc_id}, doc, upsert=True))

        if bulk_ops:
            li_coll.bulk_write(bulk_ops, ordered=False)

    li_coll.create_index([("ship_date", ASCENDING)])
    li_coll.create_index([
        ("order.customer_mktsegment", ASCENDING),
        ("order.order_date", ASCENDING),
        ("ship_date", ASCENDING)
    ])
    cursor.close()

def migrate_lineitems_flat(mysql_conn, mongo_db):
    cursor = mysql_conn.cursor(dictionary=True)
    li_flat_coll = mongo_db.get_collection("lineitems_flat", write_concern=WriteConcern(w=1))
    last_orderkey = 19700000
    last_linenum = -1

    while True:
        query_flat = """
            SELECT 
                L_ORDERKEY, L_LINENUMBER, L_QUANTITY, L_EXTENDEDPRICE,
                L_DISCOUNT, L_TAX, L_RETURNFLAG, L_LINESTATUS, L_SHIPDATE
            FROM LINEITEM
            WHERE (L_ORDERKEY > %s)
               OR (L_ORDERKEY = %s AND L_LINENUMBER > %s)
            ORDER BY L_ORDERKEY ASC, L_LINENUMBER ASC
            LIMIT %s
        """
        cursor.execute(query_flat, (last_orderkey, last_orderkey, last_linenum, BATCH_SIZE * 2))
        rows = cursor.fetchall()

        if not rows:
            break

        last_orderkey = get_col(rows[-1], "L_ORDERKEY")
        last_linenum = get_col(rows[-1], "L_LINENUMBER")

        bulk_ops = []
        for r in rows:
            okey = get_col(r, "L_ORDERKEY")
            lnum = get_col(r, "L_LINENUMBER")
            doc_id = f"{okey}_{lnum}"
            doc = {
                "_id": doc_id,
                "quantity": format_val(get_col(r, "L_QUANTITY")),
                "extended_price": format_val(get_col(r, "L_EXTENDEDPRICE")),
                "discount": format_val(get_col(r, "L_DISCOUNT")),
                "tax": format_val(get_col(r, "L_TAX")),
                "return_flag": get_col(r, "L_RETURNFLAG"),
                "line_status": get_col(r, "L_LINESTATUS"),
                "ship_date": format_val(get_col(r, "L_SHIPDATE")),
            }
            bulk_ops.append(ReplaceOne({"_id": doc_id}, doc, upsert=True))

        if bulk_ops:
            li_flat_coll.bulk_write(bulk_ops, ordered=False)

    li_flat_coll.create_index([("ship_date", ASCENDING)])
    cursor.close()

def migrate_parts(mysql_conn, mongo_db):
    cursor = mysql_conn.cursor(dictionary=True)
    parts_coll = mongo_db.get_collection("parts", write_concern=WriteConcern(w=1))
    last_key = -1

    while True:
        query_parts = """
            SELECT P_PARTKEY, P_MFGR, P_TYPE, P_SIZE
            FROM PART
            WHERE P_PARTKEY > %s
            ORDER BY P_PARTKEY ASC
            LIMIT %s
        """
        cursor.execute(query_parts, (last_key, BATCH_SIZE))
        part_rows = cursor.fetchall()

        if not part_rows:
            break

        part_keys = [get_col(p, "P_PARTKEY") for p in part_rows]
        last_key = part_keys[-1]
        format_keys = ",".join(["%s"] * len(part_keys))

        query_supps = f"""
            SELECT 
                ps.PS_PARTKEY, ps.PS_SUPPLYCOST,
                s.S_SUPPKEY, s.S_NAME, s.S_ADDRESS, s.S_PHONE, s.S_ACCTBAL, s.S_COMMENT,
                n.N_NAME AS nation_name,
                r.R_NAME AS region_name
            FROM PARTSUPP ps
            INNER JOIN SUPPLIER s ON ps.PS_SUPPKEY = s.S_SUPPKEY
            INNER JOIN NATION n ON s.S_NATIONKEY = n.N_NATIONKEY
            INNER JOIN REGION r ON n.N_REGIONKEY = r.R_REGIONKEY
            WHERE ps.PS_PARTKEY IN ({format_keys})
        """
        cursor.execute(query_supps, tuple(part_keys))
        supp_rows = cursor.fetchall()

        supps_by_part = {}
        for sr in supp_rows:
            pkey = get_col(sr, "PS_PARTKEY")
            supps_by_part.setdefault(pkey, []).append({
                "suppkey": get_col(sr, "S_SUPPKEY"),
                "supply_cost": format_val(get_col(sr, "PS_SUPPLYCOST")),
                "acctbal": format_val(get_col(sr, "S_ACCTBAL")),
                "name": get_col(sr, "S_NAME"),
                "address": get_col(sr, "S_ADDRESS"),
                "phone": get_col(sr, "S_PHONE"),
                "comment": get_col(sr, "S_COMMENT"),
                "nation": get_col(sr, "nation_name"),
                "region": get_col(sr, "region_name"),
            })

        bulk_ops = []
        for p in part_rows:
            pkey = get_col(p, "P_PARTKEY")
            doc = {
                "_id": pkey,
                "mfgr": get_col(p, "P_MFGR"),
                "type": get_col(p, "P_TYPE"),
                "size": get_col(p, "P_SIZE"),
                "suppliers": supps_by_part.get(pkey, []),
            }
            bulk_ops.append(ReplaceOne({"_id": pkey}, doc, upsert=True))

        if bulk_ops:
            parts_coll.bulk_write(bulk_ops, ordered=False)

    parts_coll.create_index([
        ("size", ASCENDING),
        ("suppliers.region", ASCENDING),
        ("type", ASCENDING)
    ])
    cursor.close()

def main():
    mysql_conn = mysql.connector.connect(**MYSQL_CONFIG)
    mongo_client = MongoClient(MONGO_URI)
    mongo_db = mongo_client[MONGO_DB_NAME]

    try:
        migrate_orders(mysql_conn, mongo_db)
        print("Coleção 'orders' migrada com sucesso!")
        migrate_lineitems_flat(mysql_conn, mongo_db)
        print("Coleção 'lineitems_flat' migrada com sucesso!")
        migrate_parts(mysql_conn, mongo_db)
        print("Coleção 'parts' migrada com sucesso!")
        migrate_lineitems_plus(mysql_conn, mongo_db)
        print("Todas as 4 coleções foram migradas com sucesso!")
    finally:
        mysql_conn.close()
        mongo_client.close()


if __name__ == "__main__":
    main()