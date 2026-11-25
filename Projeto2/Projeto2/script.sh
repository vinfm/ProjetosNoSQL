#!/usr/bin/env bash
set -euo pipefail

STAGE="${1:-all}" # all | ref | redis | cassandra_q1_q2 | cassandra_q3
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE_DIR"

chmod +x Redis/*.sh Cassandra/load/*.sh
source .venv/bin/activate
if [[ -d "$BASE_DIR/dsbulk-1.11.0/bin" ]]; then
    export PATH="$BASE_DIR/dsbulk-1.11.0/bin:$PATH"
fi

: "${MYSQL_PASSWORD?Defina MYSQL_PASSWORD antes de rodar (ex: export MYSQL_PASSWORD='sua_senha')}"
export MYSQL_PASSWORD

ensure_cassandra_ready() {
    docker compose stop redis || true
    docker compose up -d cassandra
    echo "Aguardando Cassandra aceitar conexões CQL..."
    until docker exec tpch_cassandra cqlsh -e "DESCRIBE KEYSPACES;" >/dev/null 2>&1; do
        sleep 3
    done
    docker exec -i tpch_cassandra cqlsh < schema.cql
}

run_ref() {
    PYTHONPATH=. python -c "from common.config import BASE_DIR, RESULTS_DIR; print(BASE_DIR); print(RESULTS_DIR)"
    PYTHONPATH=. python mysql_result.py
}

run_redis() {
    docker compose stop cassandra || true
    for q in q1 q2_full q2_min q3; do
        ./Redis/run_load_backup.sh "$q"
        ./Redis/run_test_from_backup.sh "$q"
    done
    docker compose stop redis
}

run_cassandra_q1_q2() {
    ensure_cassandra_ready
    PYTHONPATH=. python Cassandra/load/load_q1.py
    PYTHONPATH=. python Cassandra/load/load_q2.py --mode full
    PYTHONPATH=. python Cassandra/load/load_q2.py --mode min
    docker exec tpch_cassandra nodetool flush tpch
    docker exec tpch_cassandra nodetool tablestats tpch > results/tablestats_q1_q2.txt

    PYTHONPATH=. python Cassandra/test/test_q1.py --validate
    PYTHONPATH=. python Cassandra/test/test_q2.py --mode full --validate
    PYTHONPATH=. python Cassandra/test/test_q2.py --mode min --validate
}

run_cassandra_q3() {
    ensure_cassandra_ready

    PYTHONPATH=. python Cassandra/load/export_q3_csv.py --buckets 4
    ./Cassandra/load/load_q3_dsbulk.sh 4
    PYTHONPATH=. python Cassandra/test/test_q3.py --buckets 4 --validate

    for b in 8 16; do
        PYTHONPATH=. python Cassandra/load/export_q3_csv.py --buckets "$b" --from-buckets 4
        ./Cassandra/load/load_q3_dsbulk.sh "$b"
        PYTHONPATH=. python Cassandra/test/test_q3.py --buckets "$b" --validate
    done
}

case "$STAGE" in
    all)
        run_ref
        run_redis
        run_cassandra_q1_q2
        run_cassandra_q3
        ;;
    ref)             run_ref ;;
    redis)           run_redis ;;
    cassandra_q1_q2) run_cassandra_q1_q2 ;;
    cassandra_q3)    run_cassandra_q3 ;;
    *)
        echo "Uso: ./script.sh [all|ref|redis|cassandra_q1_q2|cassandra_q3]"
        exit 1
        ;;
esac
