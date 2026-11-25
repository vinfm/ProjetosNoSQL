import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from common.config import EXPORTS_DIR, RESULTS_DIR, SEGMENTS, TABLES, get_mysql_conn, save_json

HEADER_LINE = (
    b"segment,order_month,bucket,order_date,orderkey,"
    b"linenumber,shipdate,shippriority,extendedprice,discount\n"
)
EXPECTED_HEADER = HEADER_LINE.strip()


def _export_segment_mysql(args_tuple: tuple[str, int, str, str]) -> tuple[str, int]:
    seg, buckets, bucket_mode, out_dir_str = args_tuple
    out_dir = Path(out_dir_str)
    csv_path = out_dir / f"{seg.lower()}.csv"
    seg_b = seg.encode("ascii")
    bucket_b = [str(i).encode("ascii") for i in range(buckets)]
    use_hash = bucket_mode == "hash"

    sql = f"""
        SELECT
            o.o_orderdate, o.o_orderkey, l.l_linenumber,
            l.l_shipdate, o.o_shippriority, l.l_extendedprice, l.l_discount
        FROM {TABLES['customer']} c
        JOIN {TABLES['orders']} o ON c.c_custkey = o.o_custkey
        JOIN {TABLES['lineitem']} l ON l.l_orderkey = o.o_orderkey
        WHERE c.c_mktsegment = %s AND o.o_orderdate >= %s AND o.o_orderdate < %s
    """

    seg_rows = 0
    conn = get_mysql_conn()
    try:
        with open(csv_path, "wb", buffering=1024 * 1024) as f:
            f.write(HEADER_LINE)
            for year in range(1992, 1999):
                cur = conn.cursor(raw=True)
                cur.execute(sql, (seg, f"{year}-01-01", f"{year + 1}-01-01"))
                while True:
                    batch = cur.fetchmany(100000)
                    if not batch:
                        break
                    if use_hash:
                        lines = [
                            b"%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n"
                            % (
                                seg_b,
                                r[0][:7],
                                bucket_b[((int(r[1]) * 2654435761) >> 16) % buckets],
                                r[0],
                                r[1],
                                r[2],
                                r[3],
                                r[4],
                                r[5],
                                r[6],
                            )
                            for r in batch
                        ]
                    else:
                        lines = [
                            b"%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n"
                            % (
                                seg_b,
                                r[0][:7],
                                bucket_b[int(r[1]) % buckets],
                                r[0],
                                r[1],
                                r[2],
                                r[3],
                                r[4],
                                r[5],
                                r[6],
                            )
                            for r in batch
                        ]
                    f.write(b"".join(lines))
                    seg_rows += len(batch)
                cur.close()
    finally:
        conn.close()

    return csv_path.name, seg_rows


def _rebucket_segment_csv(args_tuple: tuple[str, int, str, str, str]) -> tuple[str, int]:
    seg, buckets, bucket_mode, src_dir_str, out_dir_str = args_tuple
    src_path = Path(src_dir_str) / f"{seg.lower()}.csv"
    dst_path = Path(out_dir_str) / f"{seg.lower()}.csv"
    bucket_b = [str(i).encode("ascii") for i in range(buckets)]
    use_hash = bucket_mode == "hash"

    seg_rows = 0
    with open(src_path, "rb", buffering=1024 * 1024) as fin, open(
        dst_path, "wb", buffering=1024 * 1024
    ) as fout:
        header = fin.readline().rstrip(b"\r\n")
        if header != EXPECTED_HEADER:
            raise ValueError(f"Cabeçalho inválido em {src_path}: {header!r}")
        fout.write(HEADER_LINE)
        while True:
            chunk = fin.readlines(16 * 1024 * 1024)
            if not chunk:
                break
            out = []
            if use_hash:
                for line in chunk:
                    p0, p1, _old_b, p3, p4, rest = line.split(b",", 5)
                    out.append(
                        b"%s,%s,%s,%s,%s,%s"
                        % (p0, p1, bucket_b[((int(p4) * 2654435761) >> 16) % buckets], p3, p4, rest)
                    )
            else:
                for line in chunk:
                    p0, p1, _old_b, p3, p4, rest = line.split(b",", 5)
                    out.append(
                        b"%s,%s,%s,%s,%s,%s"
                        % (p0, p1, bucket_b[int(p4) % buckets], p3, p4, rest)
                    )
            fout.write(b"".join(out))
            seg_rows += len(chunk)

    return dst_path.name, seg_rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--buckets", type=int, required=True, choices=[4, 8, 16])
    p.add_argument("--bucket-mode", default="mod", choices=["mod", "hash"])
    p.add_argument(
        "--from-buckets",
        type=int,
        default=None,
        choices=[4, 8, 16],
        help="Reutiliza os CSVs já exportados de exports/q3_b<N>, recalculando apenas a coluna bucket.",
    )
    p.add_argument("--workers", type=int, default=len(SEGMENTS))
    args = p.parse_args()

    tag = f"b{args.buckets}" if args.bucket_mode == "mod" else f"b{args.buckets}_{args.bucket_mode}"
    out_dir = EXPORTS_DIR / f"q3_{tag}"

    t_wall0 = time.perf_counter()
    workers = max(1, min(args.workers, len(SEGMENTS)))
    total_rows = 0

    if args.from_buckets is not None:
        src_dir = EXPORTS_DIR / f"q3_b{args.from_buckets}"
        if src_dir == out_dir:
            raise ValueError("Diretório de origem e destino não podem ser iguais")
        for seg in SEGMENTS:
            src_file = src_dir / f"{seg.lower()}.csv"
            if not src_file.is_file():
                raise FileNotFoundError(f"CSV origem não encontrado: {src_file}")
        out_dir.mkdir(parents=True, exist_ok=True)
        tasks = [
            (seg, args.buckets, args.bucket_mode, str(src_dir), str(out_dir))
            for seg in SEGMENTS
        ]
        worker_fn = _rebucket_segment_csv
        source = f"rebucket_from_b{args.from_buckets}"
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
        tasks = [(seg, args.buckets, args.bucket_mode, str(out_dir)) for seg in SEGMENTS]
        worker_fn = _export_segment_mysql
        source = "mysql"

    with ProcessPoolExecutor(max_workers=workers) as ex:
        for fname, seg_rows in ex.map(worker_fn, tasks):
            total_rows += seg_rows
            print(f"  [export {tag} ({source})] {fname}: {seg_rows} linhas")

    stats = {
        "buckets": args.buckets,
        "bucket_mode": args.bucket_mode,
        "source": source,
        "rows_exported": total_rows,
        "t_export_s": time.perf_counter() - t_wall0,
        "export_dir": str(out_dir),
    }
    save_json(RESULTS_DIR / f"export_cassandra_q3_{tag}.json", stats)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
