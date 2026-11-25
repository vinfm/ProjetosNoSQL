import struct

ORDER_HDR = struct.Struct("<iH")
ITEM_REC = struct.Struct("<IqH")

Q1_DAYS_KEY = "tpch:q1:days"


def q1_day_key(date_str: str) -> str:
    return f"tpch:q1:day:{date_str}"


def q2_key(mode: str, region: str, size: int, suffix: str, temp: bool = False) -> str:
    prefix = "tpch:q2:offers" if mode == "full" else "tpch:q2:min"
    base = f"{prefix}:{region}:{size}:{suffix}"
    return f"tmp:{base}" if temp else base


def q3_days_key(segment: str) -> str:
    return f"tpch:q3:days:{segment}"


def q3_day_key(segment: str, date_str: str) -> str:
    return f"tpch:q3:day:{segment}:{date_str}"


def pack_q3_order(shippriority: int, items: list[tuple[int, int, int]]) -> bytes:
    buf = bytearray(ORDER_HDR.pack(shippriority, len(items)))
    for it in items:
        buf.extend(ITEM_REC.pack(*it))
    return bytes(buf)


def calc_q3_order_revenue(blob: bytes, cutoff_int: int) -> tuple[int, int]:
    sprio, n_items = ORDER_HDR.unpack_from(blob, 0)
    rev_1e4 = 0
    offset = ORDER_HDR.size
    for _ in range(n_items):
        sdate_int, ext_c, disc_h = ITEM_REC.unpack_from(blob, offset)
        offset += ITEM_REC.size
        if sdate_int > cutoff_int:
            rev_1e4 += ext_c * (100 - disc_h)
    return sprio, rev_1e4


def get_redis_memory(r) -> dict:
    info = r.info("memory")
    return {
        "used_memory": info["used_memory"],
        "used_memory_human": info["used_memory_human"],
        "used_memory_peak": info["used_memory_peak"],
        "used_memory_peak_human": info["used_memory_peak_human"],
        "used_memory_rss": info["used_memory_rss"],
        "used_memory_rss_human": info["used_memory_rss_human"],
        "mem_fragmentation_ratio": info["mem_fragmentation_ratio"],
    }