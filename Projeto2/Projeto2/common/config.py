import json
import os
import statistics
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
RESULTS_DIR = BASE_DIR / "results"
EXPORTS_DIR = BASE_DIR / "exports"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

MYSQL_CFG = {
    "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
    "port": int(os.environ.get("MYSQL_PORT", "3306")),
    "user": os.environ.get("MYSQL_USER", "root"),
    "password": os.environ.get("MYSQL_PASSWORD", ""),
    "database": os.environ.get("MYSQL_DATABASE", "tpch"),
}

TABLES = {
    "lineitem": os.environ.get("TBL_LINEITEM", "LINEITEM"),
    "orders": os.environ.get("TBL_ORDERS", "ORDERS"),
    "customer": os.environ.get("TBL_CUSTOMER", "CUSTOMER"),
    "part": os.environ.get("TBL_PART", "PART"),
    "partsupp": os.environ.get("TBL_PARTSUPP", "PARTSUPP"),
    "supplier": os.environ.get("TBL_SUPPLIER", "SUPPLIER"),
    "nation": os.environ.get("TBL_NATION", "NATION"),
    "region": os.environ.get("TBL_REGION", "REGION"),
}

REDIS_CFG = {
    "host": os.environ.get("REDIS_HOST", "127.0.0.1"),
    "port": int(os.environ.get("REDIS_PORT", "6379")),
    "db": 0,
}

CASSANDRA_HOSTS = os.environ.get("CASSANDRA_HOSTS", "127.0.0.1").split(",")
CASSANDRA_KEYSPACE = os.environ.get("CASSANDRA_KEYSPACE", "tpch")

MYSQL_NET_TIMEOUT = int(os.environ.get("MYSQL_NET_TIMEOUT", "600"))
CASSANDRA_TIMEOUT = float(os.environ.get("CASSANDRA_TIMEOUT", "120.0"))
CASSANDRA_FETCH_SIZE = int(os.environ.get("CASSANDRA_FETCH_SIZE", "10000"))
REDIS_SOCKET_TIMEOUT = os.environ.get("REDIS_SOCKET_TIMEOUT")

REGIONS = ["AFRICA", "AMERICA", "ASIA", "EUROPE", "MIDDLE EAST"]
TYPE_SUFFIXES = ["TIN", "NICKEL", "BRASS", "STEEL", "COPPER"]
SEGMENTS = ["AUTOMOBILE", "BUILDING", "FURNITURE", "HOUSEHOLD", "MACHINERY"]


def get_mysql_conn(net_timeout: int = MYSQL_NET_TIMEOUT):
    import mysql.connector
    conn = mysql.connector.connect(**MYSQL_CFG)
    with conn.cursor() as c:
        c.execute(f"SET SESSION net_write_timeout = {int(net_timeout)};")
        c.execute(f"SET SESSION net_read_timeout = {int(net_timeout)};")
    return conn


def get_cassandra_session(
    timeout: float = CASSANDRA_TIMEOUT,
    fetch_size: int = CASSANDRA_FETCH_SIZE,
):
    from cassandra.cluster import Cluster
    cluster = Cluster(CASSANDRA_HOSTS)
    session = cluster.connect(CASSANDRA_KEYSPACE)
    session.default_timeout = timeout
    session.default_fetch_size = fetch_size
    return cluster, session


def get_redis_client(decode_responses: bool = False):
    import redis
    timeout = float(REDIS_SOCKET_TIMEOUT) if REDIS_SOCKET_TIMEOUT else None
    return redis.Redis(
        **REDIS_CFG,
        decode_responses=decode_responses,
        socket_timeout=timeout,
    )


def q_dec(val, places="0.0001") -> str:
    return str(Decimal(val).quantize(Decimal(places), rounding=ROUND_HALF_UP))


def date_to_int(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def months_between(start_y: int, start_m: int, end_d: date) -> list[str]:
    res = []
    y, m = start_y, start_m
    while (y, m) <= (end_d.year, end_d.month):
        res.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            m = 1
            y += 1
    return res


def summarize_runs(runs: list[dict]) -> dict:
    summary = {}
    for key in ("t_db_ms", "t_app_ms", "t_total_ms"):
        vals = [r[key] for r in runs]
        summary[key] = {
            "mean": statistics.mean(vals),
            "median": statistics.median(vals),
            "stdev": statistics.stdev(vals) if len(vals) > 1 else 0.0,
            "min": min(vals),
            "max": max(vals),
        }
    return summary


def save_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)