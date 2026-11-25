#!/usr/bin/env bash
set -euo pipefail

STAGE="${1:-all}" # all | cassandra | cassandra_b4_zm | redis | redis_test
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE_DIR"
source .venv/bin/activate
if [[ -d "$BASE_DIR/dsbulk-1.11.0/bin" ]]; then
    export PATH="$BASE_DIR/dsbulk-1.11.0/bin:$PATH"
fi

if [[ "$STAGE" != "redis_test" && "$STAGE" != "cassandra_b4_zm" ]]; then
    : "${MYSQL_PASSWORD?Defina MYSQL_PASSWORD antes de rodar (ex: export MYSQL_PASSWORD='sua_senha')}"
    export MYSQL_PASSWORD
fi

run_cassandra_nivel2() {
    docker compose stop redis || true
    docker compose up -d cassandra
    echo "Aguardando Cassandra aceitar conexões CQL..."
    until docker exec tpch_cassandra cqlsh -e "DESCRIBE KEYSPACES;" >/dev/null 2>&1; do
        sleep 3
    done
    docker exec -i tpch_cassandra cqlsh < schema_opt.cql

    echo "Carregando q3_zonemap no Cassandra..."
    PYTHONPATH=. python Cassandra/opt/load_q3_zonemap.py
    docker exec tpch_cassandra nodetool flush tpch q3_zonemap

    echo "Testando Cassandra Q3 b=8 (já carregado) com Zone Map..."
    PYTHONPATH=. python Cassandra/opt/test_q3_nivel2.py --buckets 8 --use-zonemap --validate

    echo "Gerando CSVs b=16 com hash uniforme a partir de exports/q3_b4..."
    PYTHONPATH=. python Cassandra/load/export_q3_csv.py --buckets 16 --from-buckets 4 --bucket-mode hash
    ./Cassandra/load/load_q3_dsbulk.sh 16 hash

    echo "Testando Cassandra Q3 b=16 hash sem Zone Map..."
    PYTHONPATH=. python Cassandra/opt/test_q3_nivel2.py --buckets 16 --bucket-mode hash --validate

    echo "Testando Cassandra Q3 b=16 hash com Zone Map..."
    PYTHONPATH=. python Cassandra/opt/test_q3_nivel2.py --buckets 16 --bucket-mode hash --use-zonemap --validate

    docker compose stop cassandra
}

run_cassandra_b4_zm() {
    [[ -d "$BASE_DIR/exports/q3_b4" ]] || { echo "Diretório não encontrado: $BASE_DIR/exports/q3_b4"; exit 1; }
    docker compose stop redis || true
    docker compose up -d cassandra
    echo "Aguardando Cassandra aceitar conexões CQL..."
    until docker exec tpch_cassandra cqlsh -e "DESCRIBE KEYSPACES;" >/dev/null 2>&1; do
        sleep 3
    done

    if ! docker exec tpch_cassandra cqlsh -e "SELECT order_month FROM tpch.q3_zonemap LIMIT 1;" 2>/dev/null | grep -q "199"; then
        echo "ERRO: tabela tpch.q3_zonemap não encontrada ou vazia. Execute './run_opt_nivel2.sh cassandra' primeiro."
        exit 1
    fi

    echo "Recarregando q3_lineitems_by_segment_month com b=4 (mod) a partir de exports/q3_b4..."
    ./Cassandra/load/load_q3_dsbulk.sh 4

    echo "Testando Cassandra Q3 b=4 com Zone Map..."
    PYTHONPATH=. python Cassandra/opt/test_q3_nivel2.py --buckets 4 --use-zonemap --validate

    docker compose stop cassandra
}

run_redis_zonemap_load() {
    docker compose stop cassandra || true
    BACKUP="./redis_backups/q3.rdb"
    [[ -f "$BACKUP" ]] || { echo "Backup não encontrado: $BACKUP"; exit 1; }

    docker compose stop redis || true
    rm -f ./redis_data/dump.rdb
    cp "$BACKUP" ./redis_data/dump.rdb
    docker compose up -d redis
    echo "Aguardando Redis carregar q3.rdb..."
    until docker exec tpch_redis redis-cli ping 2>/dev/null | grep -q PONG; do sleep 1; done

    echo "Adicionando tpch:q3:zonemap:* ao Redis..."
    PYTHONPATH=. python Redis/opt/load_q3_zonemap.py
    docker exec tpch_redis redis-cli SAVE
    docker exec tpch_redis chmod 644 /data/dump.rdb
    rm -f ./redis_backups/q3_zonemap.rdb
    mv ./redis_data/dump.rdb ./redis_backups/q3_zonemap.rdb
}

run_redis_zonemap_test() {
    ZM_BACKUP="./redis_backups/q3_zonemap.rdb"
    [[ -f "$ZM_BACKUP" ]] || { echo "Backup não encontrado: $ZM_BACKUP"; exit 1; }
    if [[ ! -r "$ZM_BACKUP" ]]; then
        docker run --rm -v "$BASE_DIR/redis_backups:/b" redis:7.4-alpine chmod 644 /b/q3_zonemap.rdb
    fi

    docker compose stop cassandra || true
    docker compose stop redis || true
    rm -f ./redis_data/dump.rdb
    cp "$ZM_BACKUP" ./redis_data/dump.rdb
    docker compose up -d redis
    echo "Aguardando Redis carregar q3_zonemap.rdb..."
    until docker exec tpch_redis redis-cli ping 2>/dev/null | grep -q PONG; do sleep 1; done

    PYTHONPATH=. python Redis/opt/test_q3_zonemap_opt.py --validate
    docker compose stop redis
}

case "$STAGE" in
    all)
        run_cassandra_nivel2
        run_redis_zonemap_load
        run_redis_zonemap_test
        ;;
    cassandra) run_cassandra_nivel2 ;;
    cassandra_b4_zm) run_cassandra_b4_zm ;;
    redis)
        run_redis_zonemap_load
        run_redis_zonemap_test
        ;;
    redis_test) run_redis_zonemap_test ;;
    *)
        echo "Uso: ./run_opt_nivel2.sh [all|cassandra|cassandra_b4_zm|redis|redis_test]"
        exit 1
        ;;
esac

mkdir -p results_opt_nivel2
shopt -s nullglob
for item in results/*zonemap* results/*_zm_opt.json results/*_hash*; do
    cp -r "$item" results_opt_nivel2/
done
shopt -u nullglob

PYTHONPATH=. python - <<'EOF'
import json
import re
from pathlib import Path

def pick_file(name: str, dirs: list[Path]) -> Path | None:
    for d in dirs:
        p = d / name
        if p.is_file():
            return p
    return None

base_dirs = [Path("results_baseline")]
n1_dirs = [Path("results_opt_nivel1"), Path("results")]
n2_dirs = [Path("results_opt_nivel2"), Path("results")]

rows_spec = [
    ("Redis Q3 Baseline",              "test_redis_q3.json",                    base_dirs, None),
    ("Redis Q3 Nível 1",               "test_redis_q3_opt.json",                n1_dirs,   "test_redis_q3.json"),
    ("Redis Q3 Nível 2 (Zonemap)",     "test_redis_q3_zonemap_opt.json",        n2_dirs,   "test_redis_q3.json"),
    ("Cassandra Q3 b=4 Baseline",      "test_cassandra_q3_b4.json",             base_dirs, None),
    ("Cassandra Q3 b=4 Nível 1",       "test_cassandra_q3_b4_opt.json",         n1_dirs,   "test_cassandra_q3_b4.json"),
    ("Cassandra Q3 b=4 Nível 2 (ZM)",  "test_cassandra_q3_b4_zm_opt.json",      n2_dirs,   "test_cassandra_q3_b4.json"),
    ("Cassandra Q3 b=8 Baseline",      "test_cassandra_q3_b8.json",             base_dirs, None),
    ("Cassandra Q3 b=8 Nível 1",       "test_cassandra_q3_b8_opt.json",         n1_dirs,   "test_cassandra_q3_b8.json"),
    ("Cassandra Q3 b=8 Nível 2 (ZM)",  "test_cassandra_q3_b8_zm_opt.json",      n2_dirs,   "test_cassandra_q3_b8.json"),
    ("Cassandra Q3 b=16 mod Base",     "test_cassandra_q3_b16.json",            base_dirs, None),
    ("Cassandra Q3 b=16 mod Nível 1",  "test_cassandra_q3_b16_opt.json",        n1_dirs,   "test_cassandra_q3_b16.json"),
    ("Cassandra Q3 b=16 hash Nível 2", "test_cassandra_q3_b16_hash_opt.json",   n2_dirs,   "test_cassandra_q3_b16.json"),
    ("Cassandra Q3 b=16 hash+ZM N2",   "test_cassandra_q3_b16_hash_zm_opt.json",n2_dirs,   "test_cassandra_q3_b16.json"),
]

print("\n=== COMPARATIVO Q3: BASELINE vs NÍVEL 1 vs NÍVEL 2 (médias em ms) ===")
print(f"{'Cenário':31s} | {'db_ms':>9s} | {'app_ms':>8s} | {'total_ms':>9s} | {'vs Base':>7s} | {'Valid':>5s}")
print("-" * 83)
for label, fname, search_dirs, ref_base in rows_spec:
    fp = pick_file(fname, search_dirs)
    if not fp:
        continue
    d = json.load(open(fp, encoding="utf-8"))
    s = d["summary"]
    db = s["t_db_ms"]["mean"]
    app = s["t_app_ms"]["mean"]
    tot = s["t_total_ms"]["mean"]
    speedup_str = "-"
    if ref_base:
        bp = pick_file(ref_base, base_dirs)
        if bp:
            btot = json.load(open(bp, encoding="utf-8"))["summary"]["t_total_ms"]["mean"]
            if tot > 0:
                speedup_str = f"{btot / tot:.2f}x"
    print(f"{label:31s} | {db:9.2f} | {app:8.2f} | {tot:9.2f} | {speedup_str:>7s} | {str(d['validated']):>5s}")

def parse_stats(p: Path | None) -> dict[str, str]:
    if not p or not p.is_file():
        return {}
    txt = p.read_text(encoding="utf-8")
    out = {}
    for key in (
        "Number of partitions (estimate)",
        "Compacted partition minimum bytes",
        "Compacted partition maximum bytes",
        "Compacted partition mean bytes",
    ):
        m = re.search(rf"{re.escape(key)}:\s*(\S+)", txt)
        if m:
            out[key] = m.group(1)
    return out

s_mod = parse_stats(pick_file("tablestats_q3_b16.txt", base_dirs))
s_hash = parse_stats(pick_file("tablestats_q3_b16_hash.txt", n2_dirs))
if s_mod or s_hash:
    print("\n=== DISTRIBUIÇÃO DE PARTIÇÕES: b=16 mod (TPC-H skew) vs b=16 hash ===")
    for k in (
        "Number of partitions (estimate)",
        "Compacted partition minimum bytes",
        "Compacted partition maximum bytes",
        "Compacted partition mean bytes",
    ):
        print(f"{k:36s}: mod={s_mod.get(k, '-'):>10s}  |  hash={s_hash.get(k, '-'):>10s}")
EOF
