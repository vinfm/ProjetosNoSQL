#!/usr/bin/env bash
set -euo pipefail

QUERY="${1:?Uso: ./Redis/run_load_backup.sh <q1|q2_full|q2_min|q3>}"
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE_DIR"
source .venv/bin/activate
mkdir -p ./redis_data ./redis_backups

docker compose rm -sf redis || true
rm -f ./redis_data/dump.rdb
docker compose up -d redis
until docker exec tpch_redis redis-cli ping 2>/dev/null | grep -q PONG; do
  if [[ "$(docker inspect -f '{{.State.Running}}' tpch_redis 2>/dev/null || echo false)" != "true" ]]; then
    echo "ERRO: container tpch_redis não está em execução." >&2
    docker logs --tail 50 tpch_redis >&2 || true
    exit 1
  fi
  sleep 1
done
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if PYTHONPATH=. python -c "from common.config import get_redis_client; assert get_redis_client().ping()" 2>/dev/null; then
    break
  fi
  sleep 1
done
PYTHONPATH=. python -c "from common.config import get_redis_client; assert get_redis_client().ping()"

case "$QUERY" in
  q1)      PYTHONPATH=. python Redis/load/load_q1.py --flush ;;
  q2_full) PYTHONPATH=. python Redis/load/load_q2.py --mode full --flush ;;
  q2_min)  PYTHONPATH=. python Redis/load/load_q2.py --mode min --flush ;;
  q3)      PYTHONPATH=. python Redis/load/load_q3.py --flush ;;
  *)       echo "Query inválida: $QUERY"; exit 1 ;;
esac

echo "Gerando snapshot RDB (SAVE síncrono)..."
docker exec tpch_redis redis-cli SAVE
docker exec tpch_redis chmod 644 /data/dump.rdb
mv -f ./redis_data/dump.rdb "./redis_backups/${QUERY}.rdb"
echo "Backup salvo em ./redis_backups/${QUERY}.rdb"