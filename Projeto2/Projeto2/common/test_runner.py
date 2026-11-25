import json
from common.config import RESULTS_DIR, save_json, summarize_runs


def run_test(
    db_name: str,
    query_name: str,
    exec_fn,
    warmup: int,
    repeat: int,
    validate: bool,
    extra_meta: dict | None = None,
    filename_suffix: str = "",
):
    warmup_runs = []
    res = []
    for _ in range(warmup):
        res, m = exec_fn()
        warmup_runs.append(m)

    measured_runs = []
    for _ in range(repeat):
        res, m = exec_fn()
        measured_runs.append(m)

    valid = None
    if validate:
        ref_file = "ref_q2.json" if query_name.startswith("q2") else f"ref_{query_name}.json"
        with open(RESULTS_DIR / ref_file, encoding="utf-8") as f:
            ref = json.load(f)
        valid = (res == ref)
        print(f"[{db_name}/{query_name}] Validação contra {ref_file}: {'OK' if valid else 'DIVERGENTE'}")

    out_rows_path = RESULTS_DIR / f"output_{db_name}_{query_name}{filename_suffix}.json"
    save_json(out_rows_path, res)

    payload = {
        "db": db_name,
        "query": query_name,
        "warmup": warmup,
        "repeat": repeat,
        "rows_returned": len(res),
        "validated": valid,
        "output_file": out_rows_path.name,
        **(extra_meta or {}),
        "summary": summarize_runs(measured_runs),
        "warmup_runs": warmup_runs,
        "measured_runs": measured_runs,
    }
    out_path = RESULTS_DIR / f"test_{db_name}_{query_name}{filename_suffix}.json"
    save_json(out_path, payload)
    print(json.dumps(payload["summary"], indent=2))