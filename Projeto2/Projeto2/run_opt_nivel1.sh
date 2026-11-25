#!/usr/bin/env bash
set -euo pipefail

STAGE="${1:-all}" # all | redis | cassandra | cassandra_fast
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE_DIR"
source .venv/bin/activate
if [[ -d "$BASE_DIR/dsbulk-1.11.0/bin" ]]; then
    export PATH="$BASE_DIR/dsbulk-1.11.0/bin:$PATH"
fi

run_redis_opt() {
    docker compose stop cassandra || true
    for q in q1 q2_full q2_min q3; do
        BACKUP="./redis_backups/${q}.rdb"
        [[ -f "$BACKUP" ]] || { echo "Backup não encontrado: $BACKUP"; exit 1; }
        docker compose stop redis || true
        rm -f ./redis_data/dump.rdb
        cp "$BACKUP" ./redis_data/dump.rdb
        docker compose up -d redis
        echo "Aguardando Redis carregar ${q}.rdb..."
        until docker exec tpch_redis redis-cli ping 2>/dev/null | grep -q PONG; do sleep 1; done

        case "$q" in
            q1)      PYTHONPATH=. python Redis/opt/test_q1_opt.py --validate ;;
            q2_full) PYTHONPATH=. python Redis/opt/test_q2_opt.py --mode full --validate ;;
            q2_min)  PYTHONPATH=. python Redis/opt/test_q2_opt.py --mode min --validate ;;
            q3)      PYTHONPATH=. python Redis/opt/test_q3_opt.py --validate ;;
        esac
    done
    docker compose stop redis
}

ensure_cassandra_up() {
    docker compose stop redis || true
    docker compose up -d cassandra
    echo "Aguardando Cassandra aceitar conexões CQL..."
    until docker exec tpch_cassandra cqlsh -e "DESCRIBE KEYSPACES;" >/dev/null 2>&1; do
        sleep 3
    done
}

run_cassandra_opt_fast() {
    ensure_cassandra_up
    PYTHONPATH=. python Cassandra/opt/test_q1_opt.py --validate
    PYTHONPATH=. python Cassandra/opt/test_q2_opt.py --mode full --validate
    PYTHONPATH=. python Cassandra/opt/test_q2_opt.py --mode min --validate
    # b=16 já está carregado em cassandra_data/ — testa primeiro sem recarregar
    PYTHONPATH=. python Cassandra/opt/test_q3_opt.py --buckets 16 --validate
}

run_cassandra_opt_all() {
    run_cassandra_opt_fast
    # Recarrega b=4 e b=8 direto dos CSVs já exportados em exports/ (sem consultar MySQL)
    for b in 4 8; do
        ./Cassandra/load/load_q3_dsbulk.sh "$b"
        PYTHONPATH=. python Cassandra/opt/test_q3_opt.py --buckets "$b" --validate
    done
}

case "$STAGE" in
    all)
        run_redis_opt
        run_cassandra_opt_all
        ;;
    redis)          run_redis_opt ;;
    cassandra)      run_cassandra_opt_all ;;
    cassandra_fast) run_cassandra_opt_fast ;;
    *)
        echo "Uso: ./run_opt_nivel1.sh [all|redis|cassandra|cassandra_fast]"
        exit 1
        ;;
esac

mkdir -p results_opt_nivel1
cp results/test_*_opt.json results/output_*_opt.json results_opt_nivel1/ 2>/dev/null || true

PYTHONPATH=. python - <<'EOF'
import json
from pathlib import Path

base_dir = Path("results_baseline")
opt_dir = Path("results")
pairs = [
    ("Redis Q1",            "test_redis_q1.json",            "test_redis_q1_opt.json"),
    ("Redis Q2 full",       "test_redis_q2_full.json",       "test_redis_q2_full_opt.json"),
    ("Redis Q2 min",        "test_redis_q2_min.json",        "test_redis_q2_min_opt.json"),
    ("Redis Q3",            "test_redis_q3.json",            "test_redis_q3_opt.json"),
    ("Cassandra Q1",        "test_cassandra_q1.json",        "test_cassandra_q1_opt.json"),
    ("Cassandra Q2 full",   "test_cassandra_q2_full.json",   "test_cassandra_q2_full_opt.json"),
    ("Cassandra Q2 min",    "test_cassandra_q2_min.json",    "test_cassandra_q2_min_opt.json"),
    ("Cassandra Q3 (b=4)",  "test_cassandra_q3_b4.json",     "test_cassandra_q3_b4_opt.json"),
    ("Cassandra Q3 (b=8)",  "test_cassandra_q3_b8.json",     "test_cassandra_q3_b8_opt.json"),
    ("Cassandra Q3 (b=16)", "test_cassandra_q3_b16.json",    "test_cassandra_q3_b16_opt.json"),
]
print("\n=== COMPARATIVO BASELINE vs NÍVEL 1 (médias em ms) ===")
print(f"{'Query':22s} {'Base tot':>10s} {'Opt tot':>10s} {'Opt db':>10s} {'Opt app':>9s} {'Ganho':>7s} {'Valid':>6s}")
for label, bf, of in pairs:
    bp, op = base_dir / bf, opt_dir / of
    if bp.is_file() and op.is_file():
        with open(bp, encoding="utf-8") as fb:
            b = json.load(fb)["summary"]["t_total_ms"]["mean"]
        with open(op, encoding="utf-8") as fo:
            od = json.load(fo)
        s = od["summary"]
        o = s["t_total_ms"]["mean"]
        odb = s["t_db_ms"]["mean"]
        oapp = s["t_app_ms"]["mean"]
        speedup = b / o if o > 0 else 0.0
        valid_str = str(od["validated"])
        print(f"{label:22s} {b:9.2f} {o:9.2f} {odb:9.2f} {oapp:8.2f} {speedup:6.2f}x {valid_str:>6s}")
EOF
