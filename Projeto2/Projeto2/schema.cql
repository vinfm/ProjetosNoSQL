CREATE KEYSPACE IF NOT EXISTS tpch
WITH replication = {
    'class': 'SimpleStrategy',
    'replication_factor': 1
};

USE tpch;

-- ============================================================
-- Q1: Pré-agregação diária particionada por mês (YYYY-MM)
-- ============================================================
CREATE TABLE IF NOT EXISTS q1_daily_by_month (
    ship_month text,
    shipdate date,
    returnflag text,
    linestatus text,
    sum_qty decimal,
    sum_base_price decimal,
    sum_disc_price decimal,
    sum_charge decimal,
    sum_discount decimal,
    count_order bigint,
    PRIMARY KEY ((ship_month), shipdate, returnflag, linestatus)
) WITH CLUSTERING ORDER BY (shipdate ASC, returnflag ASC, linestatus ASC);

-- ============================================================
-- Q2 (Principal — sem atalho): todas as ofertas por filtro
-- Chega agrupado por partkey e ordenado por supplycost ASC
-- ============================================================
CREATE TABLE IF NOT EXISTS q2_offers_by_filter (
    region_name text,
    part_size int,
    type_suffix text,
    partkey int,
    supplycost decimal,
    suppkey int,
    acctbal decimal,
    supplier_name text,
    nation_name text,
    mfgr text,
    address text,
    phone text,
    supplier_comment text,
    PRIMARY KEY ((region_name, part_size, type_suffix), partkey, supplycost, suppkey)
) WITH CLUSTERING ORDER BY (partkey ASC, supplycost ASC, suppkey ASC);

-- ============================================================
-- Q2 (Comparativa — materializada): apenas ofertas mínimas
-- já ordenadas para leitura direta com LIMIT 100
-- ============================================================
CREATE TABLE IF NOT EXISTS q2_min_offers_by_filter (
    region_name text,
    part_size int,
    type_suffix text,
    acctbal decimal,
    nation_name text,
    supplier_name text,
    partkey int,
    suppkey int,
    mfgr text,
    address text,
    phone text,
    supplier_comment text,
    supplycost decimal,
    PRIMARY KEY ((region_name, part_size, type_suffix), acctbal, nation_name, supplier_name, partkey, suppkey)
) WITH CLUSTERING ORDER BY (acctbal DESC, nation_name ASC, supplier_name ASC, partkey ASC, suppkey ASC);

-- ============================================================
-- Q3 (Principal — tabela única): particionada por segmento,
-- mês do pedido e bucket (orderkey % N)
-- ============================================================
CREATE TABLE IF NOT EXISTS q3_lineitems_by_segment_month (
    segment text,
    order_month text,
    bucket int,
    order_date date,
    orderkey bigint,
    linenumber int,
    shipdate date,
    shippriority int,
    extendedprice decimal,
    discount decimal,
    PRIMARY KEY ((segment, order_month, bucket), order_date, orderkey, linenumber)
) WITH CLUSTERING ORDER BY (order_date ASC, orderkey ASC, linenumber ASC);
