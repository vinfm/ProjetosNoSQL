# Consolidado Experimental — TPC-H SF=10 (Redis vs Cassandra)

## Tabela 1 — Q1 e Q2 (full e min): Baseline vs Nível 1

| Cenário | Banco | Nível | `t_db` (ms) | `t_app` (ms) | `t_total` média (ms) | Mediana (ms) | Desvio (ms) | Speedup vs Base | Validado |
|---|---|---|---:|---:|---:|---:|---:|---:|:---:|
| Q1 | Redis | Baseline | 26,17 | 5,45 | **31,63** | 30,89 | 2,27 | 1,00× | Sim |
| Q1 | Redis | Nível 1 | 13,15 | 4,82 | **17,98** | 17,44 | 2,01 | **1,76×** | Sim |
| Q1 | Cassandra | Baseline | 71,38 | 2,73 | **74,24** | 74,18 | 3,98 | 1,00× | Sim |
| Q1 | Cassandra | Nível 1 | 37,40 | 2,25 | **39,72** | 38,65 | 5,77 | **1,87×** | Sim |
| Q2 full | Redis | Baseline | 8,63 | 22,83 | **31,46** | 31,08 | 3,21 | 1,00× | Sim |
| Q2 full | Redis | Nível 1 | 2,25 | 7,73 | **9,98** | 9,11 | 2,21 | **3,15×** | Sim |
| Q2 full | Cassandra | Baseline | 44,88 | 5,14 | **50,02** | 46,25 | 9,18 | 1,00× | Sim |
| Q2 full | Cassandra | Nível 1 | 40,34 | 4,08 | **44,42** | 43,06 | 3,59 | **1,13×** | Sim |
| Q2 min | Redis | Baseline | 0,25 | 0,28 | **0,53** | 0,51 | 0,09 | 1,00× | Sim |
| Q2 min | Redis | Nível 1 | 0,14 | 0,14 | **0,28** | 0,24 | 0,10 | **1,87×** | Sim |
| Q2 min | Cassandra | Baseline | 1,15 | 0,10 | **1,25** | 1,25 | 0,21 | 1,00× | Sim |
| Q2 min | Cassandra | Nível 1 | 1,40 | 0,10 | **1,50** | 1,47 | 0,22 | **0,84×** | Sim |

## Tabela 2 — Q3: Baseline → Nível 1 → Nível 2

| Cenário | `t_db` (ms) | `t_app` (ms) | `t_total` média (ms) | Mediana (ms) | Desvio (ms) | Ganho vs N1 | Ganho vs Base | Validado |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| Redis Q3 (Baseline) | 3.398,11 | 1.022,77 | **4.421,00** | 4.420,60 | 63,52 | — | 1,00× | Sim |
| Redis Q3 (Nível 1) | 802,16 | 655,36 | **1.457,62** | 1.450,93 | 40,54 | — | **3,03×** | Sim |
| Redis Q3 + Zone Map (Nível 2, rep=30) | 81,90 | 113,54 | **195,45** | 194,20 | 3,72 | **7,46×** | **22,62×** | Sim |
| Cassandra Q3 `b=4 mod` (Baseline) | 33.562,27 | 3.982,34 | **37.545,41** | 37.412,08 | 380,13 | — | 1,00× | Sim |
| Cassandra Q3 `b=4 mod` (Nível 1) | 22.229,73 | 535,63 | **22.765,55** | 22.592,37 | 560,90 | — | **1,65×** | Sim |
| Cassandra Q3 `b=4 mod` + Zone Map (Nível 2) | 2.639,54 | 285,69 | **2.925,31** | 2.910,61 | 104,59 | **7,78×** | **12,83×** | Sim |
| Cassandra Q3 `b=8 mod` (Baseline) | 31.147,84 | 4.253,11 | **35.402,19** | 35.225,16 | 417,64 | — | 1,00× | Sim |
| Cassandra Q3 `b=8 mod` (Nível 1) | 20.718,89 | 532,39 | **21.251,55** | 20.990,14 | 669,22 | — | **1,67×** | Sim |
| Cassandra Q3 `b=8 mod` + Zone Map (Nível 2) | 2.388,52 | 234,98 | **2.623,59** | 2.599,94 | 83,26 | **8,10×** | **13,49×** | Sim |
| Cassandra Q3 `b=16 mod` (Baseline) | 29.755,98 | 4.029,48 | **33.786,76** | 33.444,78 | 598,58 | — | 1,00× | Sim |
| Cassandra Q3 `b=16 mod` (Nível 1) | 19.760,55 | 531,04 | **20.291,92** | 20.151,97 | 378,09 | — | **1,67×** | Sim |
| Cassandra Q3 `b=16 hash` (Nível 2, sem ZM) | 20.038,45 | 592,40 | **20.631,28** | 21.160,49 | 983,82 | **0,98×** | **1,64×** | Sim |
| Cassandra Q3 `b=16 hash` + Zone Map (Nível 2) | 2.290,05 | 229,20 | **2.519,36** | 2.508,55 | 41,45 | **8,05×** | **13,41×** | Sim |

## Tabela 3 — Tempos de Carga e ETL

| Carga / Cenário | Banco | Registros carregados | Extração / Export (s) | Transformação (s) | Carga no banco (s) | Tempo total (s) |
|---|---|---:|---:|---:|---:|---:|
| Q1 | Redis | 3.817 | 74,22 | 0,01 | 0,026 | 74,25 |
| Q1 | Cassandra | 3.817 | 74,83 | 0,005 | 0,701 | 75,54 |
| Q2 min | Redis | 5.904.530 | 341,39 | 17,25 | 6,63 | 365,28 |
| Q2 min | Cassandra | 5.904.530 | 320,61 | 5,34 | 1.070,73 | 1.396,69 |
| Q2 full | Redis | 8.000.000 | 133,21 | 23,47 | 9,21 | 165,90 |
| Q2 full | Cassandra | 8.000.000 | 145,61 | 7,08 | 1.464,49 | 1.617,19 |
| Q3 (Orders + Lineitems) | Redis | 15.000.000 ped. / 59.986.052 itens | 702,54 | 166,93 | 22,92 | 906,31 |
| Q3 `b=4 mod` (MySQL → CSV → DSBulk) | Cassandra | 59.986.052 | 166,56 | — | 268,67 | 435,23 |
| Q3 `b=8 mod` (Rebucket CSV → DSBulk) | Cassandra | 59.986.052 | 9,33 | — | 272,58 | 281,91 |
| Q3 `b=16 mod` (Rebucket CSV → DSBulk) | Cassandra | 59.986.052 | 9,89 | — | 272,84 | 282,73 |
| Q3 `b=16 hash` (Rebucket CSV → DSBulk) | Cassandra | 59.986.052 | 10,09 | — | 295,31 | 305,40 |
| Q3 Zone Map (`tpch:q3:zonemap:*`) | Redis | 12.030 dias | 670,70 | 0,00 | 0,020 | 670,72 |
| Q3 Zone Map (`tpch.q3_zonemap`) | Cassandra | 400 meses | 672,80 | 0,00 | 0,047 | 672,86 |

## Tabela 4 — Armazenamento e Distribuição Física

### 4a. Redis — Memória RAM (`INFO memory`)

| Cenário | `used_memory` (bytes) | `used_memory` (MiB) | `used_memory_human` | RSS (`used_memory_rss_human`) | Frag. Ratio |
|---|---:|---:|---:|---:|---:|
| Q1 | 2.225.472 | 2,12 | 2.12M | 9.20M | 9,19 |
| Q2 min | 1.181.932.856 | 1.127,18 | 1.10G | 1.13G | 1,03 |
| Q2 full | 1.601.092.736 | 1.526,92 | 1.49G | 1.52G | 1,02 |
| Q3 (Baseline/N1) | 1.844.713.008 | 1.759,26 | 1.72G | 1.74G | 1,02 |
| Q3 + Zone Map (N2) | 1.845.514.792 | 1.760,02 | 1.72G | 1.75G | 1,02 |

> **Overhead do Zone Map no Redis:** `+801.784` bytes (`+783,0 KiB` / `+0,043%` sobre o `q3.rdb`).

### 4b. Cassandra — Disco e Distribuição de Partições (`nodetool tablestats`)

| Tabela / Bucketing | SSTables | Live (MiB) | Total (MiB) | Partições (est.) | Mín (B) | Média (KiB) | Máx (KiB) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Q1 (`q1_daily_by_month`) | 1 | 0,18 | 0,18 | 84 | 87 | 4,1 | 6,7 |
| Q2 min (`q2_min_offers_by_filter`) | 5 | 591,70 | 591,70 | 1.250 | 11.865 | 796,8 | 921,0 |
| Q2 full (`q2_offers_by_filter`) | 8 | 858,21 | 858,21 | 1.250 | 24.602 | 1.030,4 | 1.326,3 |
| Q3 `b=4 mod` | 22 | 1.215,42 | 1.265,60 | 1.600 | 87 | 599,2 | 2.291,8 |
| Q3 `b=8 mod` | 19 | 1.219,24 | 1.478,89 | 3.501 | 216 | 344,2 | 1.105,2 |
| Q3 `b=16 mod` (skew TPC-H) | 18 | 1.219,01 | 1.485,61 | 3.501 | 87 | 346,5 | 1.105,2 |
| Q3 `b=16 hash` (Knuth) | 9 | 1.236,35 | 1.293,61 | 6.883 | 125 | 233,9 | 533,0 |

> **Viés do TPC-H vs Hash uniforme:** em `b=8 mod` e `b=16 mod`, o número estimado de partições permanece idêntico (`3.501`) e a partição máxima fica igual (`1.131.752 B` = `1.105,2 KiB`), pois os `o_orderkey` do TPC-H não ocupam uniformemente as classes módulo 16. Com `b=16 hash`, as partições dobram para `6.883`, a partição máxima cai pela metade (`545.791 B` = `533,0 KiB`) e a média cai de `346,5 KiB` para `233,9 KiB`.

## Tabela 5 — Integridade dos Resultados (`output_*.json` vs `ref_q*.json`)

| Fase | Arquivo de saída | Referência | Bytes idênticos | JSON idêntico | `validated` |
|---|---|---|:---:|:---:|:---:|
| Baseline | `output_cassandra_q1.json` | `ref_q1.json` | Sim | Sim | True |
| Baseline | `output_cassandra_q2_full.json` | `ref_q2.json` | Sim | Sim | True |
| Baseline | `output_cassandra_q2_min.json` | `ref_q2.json` | Sim | Sim | True |
| Baseline | `output_cassandra_q3_b16.json` | `ref_q3.json` | Sim | Sim | True |
| Baseline | `output_redis_q1.json` | `ref_q1.json` | Sim | Sim | True |
| Baseline | `output_redis_q2_full.json` | `ref_q2.json` | Sim | Sim | True |
| Baseline | `output_redis_q2_min.json` | `ref_q2.json` | Sim | Sim | True |
| Baseline | `output_redis_q3.json` | `ref_q3.json` | Sim | Sim | True |
| Nível 1 | `output_cassandra_q1_opt.json` | `ref_q1.json` | Sim | Sim | True |
| Nível 1 | `output_cassandra_q2_full_opt.json` | `ref_q2.json` | Sim | Sim | True |
| Nível 1 | `output_cassandra_q2_min_opt.json` | `ref_q2.json` | Sim | Sim | True |
| Nível 1 | `output_cassandra_q3_b16_opt.json` | `ref_q3.json` | Sim | Sim | True |
| Nível 1 | `output_cassandra_q3_b4_opt.json` | `ref_q3.json` | Sim | Sim | True |
| Nível 1 | `output_cassandra_q3_b8_opt.json` | `ref_q3.json` | Sim | Sim | True |
| Nível 1 | `output_redis_q1_opt.json` | `ref_q1.json` | Sim | Sim | True |
| Nível 1 | `output_redis_q2_full_opt.json` | `ref_q2.json` | Sim | Sim | True |
| Nível 1 | `output_redis_q2_min_opt.json` | `ref_q2.json` | Sim | Sim | True |
| Nível 1 | `output_redis_q3_opt.json` | `ref_q3.json` | Sim | Sim | True |
| Nível 2 | `output_cassandra_q3_b16_hash_opt.json` | `ref_q3.json` | Sim | Sim | True |
| Nível 2 | `output_cassandra_q3_b16_hash_zm_opt.json` | `ref_q3.json` | Sim | Sim | True |
| Nível 2 | `output_cassandra_q3_b4_zm_opt.json` | `ref_q3.json` | Sim | Sim | True |
| Nível 2 | `output_cassandra_q3_b8_zm_opt.json` | `ref_q3.json` | Sim | Sim | True |
| Nível 2 | `output_redis_q3_zonemap_opt.json` | `ref_q3.json` | Sim | Sim | True |
