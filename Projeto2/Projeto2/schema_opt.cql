USE tpch;

CREATE TABLE IF NOT EXISTS q3_zonemap (
    segment text,
    order_month text,
    max_shipdate date,
    PRIMARY KEY ((segment), order_month)
) WITH CLUSTERING ORDER BY (order_month ASC);
