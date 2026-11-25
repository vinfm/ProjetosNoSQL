#!/usr/bin/env bash
set -euo pipefail

BUCKETS="${1:?Uso: ./Cassandra/load/load_q3_dsbulk.sh <4|8|16> [tag]}"
TAG="${2:-}"
SUFFIX="b${BUCKETS}${TAG:+_${TAG}}"
DSBULK_TIMEOUT="${DSBULK_TIMEOUT:-60 seconds}"
DSBULK_CONCURRENCY="${DSBULK_CONCURRENCY:-64}"

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$BASE_DIR"
CSV_DIR="$BASE_DIR/exports/q3_${SUFFIX}"
OUT_DIR="$BASE_DIR/results"
mkdir -p "$OUT_DIR"

EXPECTED_HEADER="segment,order_month,bucket,order_date,orderkey,linenumber,shipdate,shippriority,extendedprice,discount"
MAPPING="segment=segment, order_month=order_month, bucket=bucket, order_date=order_date, orderkey=orderkey, linenumber=linenumber, shipdate=shipdate, shippriority=shippriority, extendedprice=extendedprice, discount=discount"

shopt -s nullglob
CSV_FILES=("$CSV_DIR"/*.csv)
if [[ ${#CSV_FILES[@]} -eq 0 ]]; then
    echo "ERRO: Nenhum CSV encontrado em $CSV_DIR"
    exit 1
fi

for f in "${CSV_FILES[@]}"; do
    ACTUAL_HEADER="$(head -n 1 "$f" | tr -d '\r')"
    if [[ "$ACTUAL_HEADER" != "$EXPECTED_HEADER" ]]; then
        echo "ERRO: Cabeçalho inválido em $f"
        echo "Esperado: $EXPECTED_HEADER"
        echo "Obtido:   $ACTUAL_HEADER"
        exit 1
    fi
done
echo "Validação de cabeçalho CSV vs schema.mapping: OK (${#CSV_FILES[@]} arquivos)"

docker exec tpch_cassandra cqlsh -e "TRUNCATE tpch.q3_lineitems_by_segment_month;"
docker exec tpch_cassandra nodetool clearsnapshot tpch || true

T0=$(date +%s.%N)
echo "Carregando ${#CSV_FILES[@]} CSVs de $CSV_DIR em paralelo..."
dsbulk load \
    -h 127.0.0.1 \
    -k tpch \
    -t q3_lineitems_by_segment_month \
    -url "$CSV_DIR" \
    -maxConcurrentFiles 5 \
    -header true \
    --schema.mapping "$MAPPING" \
    --driver.basic.request.timeout "$DSBULK_TIMEOUT" \
    -maxConcurrentQueries "$DSBULK_CONCURRENCY" \
    -logDir "$OUT_DIR/dsbulk_logs_${SUFFIX}"
T1=$(date +%s.%N)
T_LOAD=$(python3 -c "print(round($T1 - $T0, 3))")

docker exec tpch_cassandra nodetool flush tpch q3_lineitems_by_segment_month
docker exec tpch_cassandra nodetool tablestats tpch.q3_lineitems_by_segment_month > "$OUT_DIR/tablestats_q3_${SUFFIX}.txt"
docker exec tpch_cassandra nodetool tablehistograms tpch q3_lineitems_by_segment_month > "$OUT_DIR/tablehistograms_q3_${SUFFIX}.txt"

echo "{\"buckets\": $BUCKETS, \"bucket_mode\": \"${TAG:-mod}\", \"t_load_s\": $T_LOAD}" > "$OUT_DIR/load_cassandra_q3_${SUFFIX}.json"
echo "Concluído (${SUFFIX}) em ${T_LOAD}s."
