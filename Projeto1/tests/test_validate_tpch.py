import datetime
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import validate_tpch as vt  # noqa: E402


class TestMongoParser(unittest.TestCase):
    def test_discover_real_mongo_queries(self) -> None:
        specs = vt.discover_mongo_queries(PROJECT_ROOT / "queries_tpch_mongodb")
        self.assertEqual(len(specs), 6)

        actual_pairs = [(s.query_id, s.collection, s.block_index) for s in specs]
        expected_pairs = [
            (1, "lineitems_flat", 1),
            (1, "lineitems_plus", 1),
            (1, "orders", 2),
            (2, "parts", 1),
            (3, "orders", 1),
            (3, "lineitems_plus", 2),
        ]
        self.assertEqual(actual_pairs, expected_pairs)

        # Verifica ISODate em Q1 e Q3 convertido para datetime UTC
        q1_flat = specs[0]
        self.assertEqual(
            q1_flat.pipeline[0]["$match"]["ship_date"]["$lte"],
            datetime.datetime(1998, 9, 2, 0, 0, tzinfo=datetime.timezone.utc),
        )
        self.assertEqual(q1_flat.options, {"allowDiskUse": True, "hint": {"$natural": 1}})
        self.assertEqual(q1_flat.warnings, [])

        q1_orders = specs[2]
        self.assertEqual(
            q1_orders.pipeline[0]["$match"]["line_items.ship_date"]["$lte"],
            datetime.datetime(1998, 9, 2, 0, 0, tzinfo=datetime.timezone.utc),
        )

        q2_parts = specs[3]
        self.assertEqual(q2_parts.options, {"allowDiskUse": True})
        self.assertEqual(q2_parts.warnings, [])

        q3_orders = specs[4]
        self.assertEqual(
            q3_orders.pipeline[0]["$match"]["order_date"]["$lt"],
            datetime.datetime(1995, 3, 15, 0, 0, tzinfo=datetime.timezone.utc),
        )
        self.assertEqual(q3_orders.options, {"allowDiskUse": True, "hint": {"$natural": 1}})
        self.assertEqual(q3_orders.warnings, [])

        # Verifica 3.mongodb bloco 2 com opções válidas em objeto { ... }
        q3_plus = specs[5]
        self.assertEqual(
            q3_plus.pipeline[0]["$match"]["ship_date"]["$gt"],
            datetime.datetime(1995, 3, 15, 0, 0, tzinfo=datetime.timezone.utc),
        )
        self.assertEqual(
            q3_plus.options,
            {
                "allowDiskUse": True,
                "hint": {
                    "order.customer_mktsegment": 1,
                    "order.order_date": 1,
                    "ship_date": 1,
                },
            },
        )
        self.assertEqual(q3_plus.warnings, [])

    def test_strings_with_slashes_and_colons_inside_quotes(self) -> None:
        snippet = """
// db.orders.aggregate([...])
[
  {
    $match: {
      url: "https://example.com:8080/a//b",
      note: 'prefix: value // not a comment',
      dt: new Date("1995-03-15T00:00:00Z"),
      cnt: NumberInt(42),
      big: NumberLong("123456789012"),
      dec: NumberDecimal("99.95")
    }
  }
],
{ allowDiskUse: true }
"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "9.mongodb"
            p.write_text(snippet, encoding="utf-8")
            specs = vt.parse_mongodb_file(p, query_id=9)

        self.assertEqual(len(specs), 1)
        match_doc = specs[0].pipeline[0]["$match"]
        self.assertEqual(match_doc["url"], "https://example.com:8080/a//b")
        self.assertEqual(match_doc["note"], "prefix: value // not a comment")
        self.assertEqual(
            match_doc["dt"],
            datetime.datetime(1995, 3, 15, 0, 0, tzinfo=datetime.timezone.utc),
        )
        self.assertEqual(match_doc["cnt"], 42)
        self.assertEqual(int(match_doc["big"]), 123456789012)
        self.assertEqual(str(match_doc["dec"]), "99.95")

    def test_bare_options_without_braces_emits_warning(self) -> None:
        snippet = """
// db.lineitems_plus.aggregate([...])
[
  { $limit: 10 }
],
  allowDiskUse: true,
  hint: { "order.customer_mktsegment": 1, "order.order_date": 1, "ship_date": 1 }
"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "3.mongodb"
            p.write_text(snippet, encoding="utf-8")
            specs = vt.parse_mongodb_file(p, query_id=3)

        self.assertEqual(len(specs), 1)
        self.assertEqual(
            specs[0].options,
            {
                "allowDiskUse": True,
                "hint": {
                    "order.customer_mktsegment": 1,
                    "order.order_date": 1,
                    "ship_date": 1,
                },
            },
        )
        self.assertEqual(len(specs[0].warnings), 1)
        self.assertIn("sem chaves", specs[0].warnings[0])


class TestComparisonAgainstRefs(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ref_q1 = vt.load_json_rows(PROJECT_ROOT / "ref_q1.json")
        cls.ref_q2 = vt.load_json_rows(PROJECT_ROOT / "ref_q2.json")
        cls.ref_q3 = vt.load_json_rows(PROJECT_ROOT / "ref_q3.json")

    def _make_mongo_q1(self) -> list[dict]:
        docs = []
        for idx, r in enumerate(self.ref_q1):
            # Simula desvio típico de float64 (~1e-13 relativo) e avg sem arredondamento (< 5e-5)
            avg_disc_val = float(r["avg_disc"]) + (0.000018 if idx % 2 == 0 else -0.000021)
            avg_qty_val = float(r["avg_qty"]) + 0.000012
            avg_price_val = float(r["avg_price"]) - 0.000029
            docs.append(
                {
                    "l_returnflag": r["returnflag"],
                    "l_linestatus": r["linestatus"],
                    "sum_qty": float(r["sum_qty"]),
                    "sum_base_price": float(r["sum_base_price"]) * (1.0 + 1e-13),
                    "sum_disc_price": float(r["sum_disc_price"]) * (1.0 - 1e-13),
                    "sum_charge": float(r["sum_charge"]) * (1.0 + 1e-13),
                    "avg_qty": avg_qty_val,
                    "avg_price": avg_price_val,
                    "avg_disc": avg_disc_val,
                    "count_order": r["count_order"],
                }
            )
        return docs

    def _make_mongo_q2(self) -> list[dict]:
        return [
            {
                "s_acctbal": float(r["acctbal"]),
                "s_name": r["supplier_name"],
                "n_name": r["nation_name"],
                "p_partkey": r["partkey"],
                "p_mfgr": r["mfgr"],
                "s_address": r["address"],
                "s_phone": r["phone"],
                "s_comment": r["supplier_comment"],
            }
            for r in self.ref_q2
        ]

    def _make_mongo_q3(self) -> list[dict]:
        docs = []
        for r in self.ref_q3:
            d = datetime.date.fromisoformat(r["orderdate"])
            docs.append(
                {
                    "l_orderkey": r["orderkey"],
                    "revenue": float(r["revenue"]) * (1.0 + 1e-14),
                    # PyMongo devolve datetime ingênuo em UTC por padrão
                    "o_orderdate": datetime.datetime(d.year, d.month, d.day),
                    "o_shippriority": r["shippriority"],
                }
            )
        return docs

    def test_simulated_mongo_passes_for_q1_q2_q3(self) -> None:
        c1 = vt.compare_results(vt.QUERY_SCHEMAS[1], self._make_mongo_q1(), self.ref_q1)
        self.assertTrue(c1.passed, c1.diffs)
        self.assertGreater(c1.max_rel_err, Decimal(0))
        self.assertLess(c1.max_rel_err, Decimal("1e-3"))

        c2 = vt.compare_results(vt.QUERY_SCHEMAS[2], self._make_mongo_q2(), self.ref_q2)
        self.assertTrue(c2.passed, c2.diffs)
        self.assertEqual(c2.row_count_actual, 100)

        c3 = vt.compare_results(vt.QUERY_SCHEMAS[3], self._make_mongo_q3(), self.ref_q3)
        self.assertTrue(c3.passed, c3.diffs)
        self.assertEqual(c3.row_count_actual, 10)

    def test_extended_json_passes(self) -> None:
        ejson_q1 = [
            {
                "l_returnflag": r["returnflag"],
                "l_linestatus": r["linestatus"],
                "sum_qty": {"$numberDecimal": r["sum_qty"]},
                "sum_base_price": {"$numberDecimal": r["sum_base_price"]},
                "sum_disc_price": {"$numberDecimal": r["sum_disc_price"]},
                "sum_charge": {"$numberDecimal": r["sum_charge"]},
                "avg_qty": {"$numberDouble": r["avg_qty"]},
                "avg_price": {"$numberDouble": r["avg_price"]},
                "avg_disc": {"$numberDouble": r["avg_disc"]},
                "count_order": {"$numberLong": str(r["count_order"])},
            }
            for r in self.ref_q1
        ]
        c1 = vt.compare_results(vt.QUERY_SCHEMAS[1], ejson_q1, self.ref_q1, strict=True)
        self.assertTrue(c1.passed, c1.diffs)
        self.assertTrue(c1.exact_match)

        ejson_q3 = [
            {
                "l_orderkey": {"$numberInt": str(r["orderkey"])},
                "revenue": {"$numberDecimal": r["revenue"]},
                "o_orderdate": {"$date": f"{r['orderdate']}T00:00:00Z"},
                "o_shippriority": {"$numberInt": str(r["shippriority"])},
            }
            for r in self.ref_q3
        ]
        c3 = vt.compare_results(vt.QUERY_SCHEMAS[3], ejson_q3, self.ref_q3, strict=True)
        self.assertTrue(c3.passed, c3.diffs)

    def test_failure_count_order_off_by_one(self) -> None:
        rows = self._make_mongo_q1()
        rows[0]["count_order"] += 1
        comp = vt.compare_results(vt.QUERY_SCHEMAS[1], rows, self.ref_q1)
        self.assertFalse(comp.passed)
        self.assertTrue(
            any(d.get("type") == "field_mismatch" and d.get("field") == "count_order" for d in comp.diffs)
        )

    def test_failure_sum_charge_beyond_rel_tol(self) -> None:
        rows = self._make_mongo_q1()
        rows[0]["sum_charge"] = float(self.ref_q1[0]["sum_charge"]) * 1.0001
        comp = vt.compare_results(vt.QUERY_SCHEMAS[1], rows, self.ref_q1)
        self.assertFalse(comp.passed)
        self.assertTrue(
            any(d.get("type") == "field_mismatch" and d.get("field") == "sum_charge" for d in comp.diffs)
        )

    def test_failure_avg_disc_beyond_quantization_step(self) -> None:
        rows = self._make_mongo_q1()
        rows[0]["avg_disc"] = float(self.ref_q1[0]["avg_disc"]) + 0.0002
        comp = vt.compare_results(vt.QUERY_SCHEMAS[1], rows, self.ref_q1)
        self.assertFalse(comp.passed)
        self.assertTrue(
            any(d.get("type") == "field_mismatch" and d.get("field") == "avg_disc" for d in comp.diffs)
        )

    def test_failure_missing_and_extra_row(self) -> None:
        rows_missing = self._make_mongo_q1()[:-1]
        comp_missing = vt.compare_results(vt.QUERY_SCHEMAS[1], rows_missing, self.ref_q1)
        self.assertFalse(comp_missing.passed)
        types_missing = {d.get("type") for d in comp_missing.diffs}
        self.assertIn("row_count", types_missing)
        self.assertIn("missing_row", types_missing)

        rows_extra = self._make_mongo_q1()
        extra_row = dict(rows_extra[0])
        extra_row["l_returnflag"] = "X"
        rows_extra.append(extra_row)
        comp_extra = vt.compare_results(vt.QUERY_SCHEMAS[1], rows_extra, self.ref_q1)
        self.assertFalse(comp_extra.passed)
        types_extra = {d.get("type") for d in comp_extra.diffs}
        self.assertIn("row_count", types_extra)
        self.assertIn("extra_row", types_extra)

    def test_failure_swapped_row_order_flags_order_mismatch(self) -> None:
        rows = self._make_mongo_q2()
        rows[0], rows[1] = rows[1], rows[0]
        comp = vt.compare_results(vt.QUERY_SCHEMAS[2], rows, self.ref_q2)
        self.assertFalse(comp.passed)
        self.assertTrue(any(d.get("type") == "order_mismatch" for d in comp.diffs))

    def test_failure_unexpected_id_field(self) -> None:
        rows = self._make_mongo_q3()
        rows[0]["_id"] = rows[0]["l_orderkey"]
        comp = vt.compare_results(vt.QUERY_SCHEMAS[3], rows, self.ref_q3)
        self.assertFalse(comp.passed)
        self.assertTrue(
            any(d.get("type") == "schema" and "_id" in d.get("extra", []) for d in comp.diffs)
        )

    def test_failure_wrong_date_and_non_midnight_datetime(self) -> None:
        rows_wrong = self._make_mongo_q3()
        rows_wrong[0]["o_orderdate"] = datetime.datetime(1995, 2, 24, 0, 0)
        comp_wrong = vt.compare_results(vt.QUERY_SCHEMAS[3], rows_wrong, self.ref_q3)
        self.assertFalse(comp_wrong.passed)
        self.assertTrue(
            any(d.get("type") == "field_mismatch" and d.get("field") == "o_orderdate" for d in comp_wrong.diffs)
        )

        rows_non_midnight = self._make_mongo_q3()
        rows_non_midnight[0]["o_orderdate"] = datetime.datetime(1995, 2, 23, 3, 0)
        comp_nm = vt.compare_results(vt.QUERY_SCHEMAS[3], rows_non_midnight, self.ref_q3)
        self.assertFalse(comp_nm.passed)
        self.assertTrue(
            any(
                d.get("type") == "field_mismatch"
                and d.get("field") == "o_orderdate"
                and "00:00:00" in d.get("detail", "")
                for d in comp_nm.diffs
            )
        )

    def test_failure_wrong_string(self) -> None:
        rows = self._make_mongo_q2()
        rows[0]["n_name"] = "GERMANY "
        comp = vt.compare_results(vt.QUERY_SCHEMAS[2], rows, self.ref_q2)
        self.assertFalse(comp.passed)
        self.assertTrue(
            any(
                d.get("type") == "field_mismatch"
                and d.get("field") == "n_name"
                and "espaços à direita" in d.get("detail", "")
                for d in comp.diffs
            )
        )

    def test_whitespace_diagnostics_and_strip_strings(self) -> None:
        rows = self._make_mongo_q2()
        # Espaço apenas à esquerda
        rows[0]["s_comment"] = " " + rows[0]["s_comment"]
        # Espaços à esquerda e à direita
        rows[1]["s_comment"] = " " + rows[1]["s_comment"] + " "
        # Espaço apenas à direita
        rows[2]["s_address"] = rows[2]["s_address"] + " "

        comp_default = vt.compare_results(vt.QUERY_SCHEMAS[2], rows, self.ref_q2)
        self.assertFalse(comp_default.passed)
        self.assertEqual(comp_default.total_diffs, 3)
        self.assertEqual(comp_default.whitespace_only_diffs, 3)
        self.assertTrue(all(d.get("whitespace_only") is True for d in comp_default.diffs))
        self.assertIn("espaços à esquerda", comp_default.diffs[0]["detail"])
        self.assertIn("extremidades", comp_default.diffs[1]["detail"])
        self.assertIn("espaços à direita", comp_default.diffs[2]["detail"])

        # Com strip_strings=True deve passar
        comp_stripped = vt.compare_results(
            vt.QUERY_SCHEMAS[2], rows, self.ref_q2, strip_strings=True
        )
        self.assertTrue(comp_stripped.passed, comp_stripped.diffs)
        self.assertFalse(comp_stripped.exact_match)
        self.assertEqual(comp_stripped.whitespace_stripped_matches, 3)

        # Com strict=True mesmo com strip_strings=True deve falhar
        comp_strict = vt.compare_results(
            vt.QUERY_SCHEMAS[2], rows, self.ref_q2, strict=True, strip_strings=True
        )
        self.assertFalse(comp_strict.passed)
        self.assertEqual(comp_strict.total_diffs, 3)

    def test_strip_strings_rejects_internal_whitespace_diff(self) -> None:
        rows = self._make_mongo_q2()
        orig = rows[0]["s_comment"]
        # Altera espaço interno
        rows[0]["s_comment"] = orig.replace(" ", "  ", 1)
        comp = vt.compare_results(
            vt.QUERY_SCHEMAS[2], rows, self.ref_q2, strip_strings=True
        )
        self.assertFalse(comp.passed)
        self.assertEqual(comp.whitespace_only_diffs, 0)
        self.assertEqual(comp.total_diffs, 1)


class TestCLIEndToEnd(unittest.TestCase):
    def test_cli_dry_run(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "validate_tpch.py"), "--dry-run"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("ref_q1.json (4 linhas)", proc.stdout)
        self.assertIn("ref_q2.json (100 linhas)", proc.stdout)
        self.assertIn("ref_q3.json (10 linhas)", proc.stdout)
        self.assertIn("3.mongodb (bloco 2)", proc.stdout)
        self.assertIn("Execuções MongoDB planejadas (6)", proc.stdout)

    def test_cli_mongo_file_pass_and_fail(self) -> None:
        ref_q3 = json.loads((PROJECT_ROOT / "ref_q3.json").read_text(encoding="utf-8"))
        valid_ejson = [
            {
                "l_orderkey": r["orderkey"],
                "revenue": float(r["revenue"]),
                "o_orderdate": {"$date": f"{r['orderdate']}T00:00:00Z"},
                "o_shippriority": r["shippriority"],
            }
            for r in ref_q3
        ]

        with tempfile.TemporaryDirectory() as tmp:
            ok_path = Path(tmp) / "ok_q3.json"
            ok_path.write_text(json.dumps(valid_ejson), encoding="utf-8")

            proc_ok = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "validate_tpch.py"),
                    "-q",
                    "3",
                    "--mongo-file",
                    str(ok_path),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_ok.returncode, 0, proc_ok.stderr + proc_ok.stdout)
            self.assertIn("[PASS]", proc_ok.stdout)

            bad_ejson = list(valid_ejson)
            bad_ejson[0] = dict(bad_ejson[0], revenue=1.0)
            bad_path = Path(tmp) / "bad_q3.json"
            bad_path.write_text(json.dumps(bad_ejson), encoding="utf-8")

            proc_bad = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "validate_tpch.py"),
                    "-q",
                    "3",
                    "--mongo-file",
                    str(bad_path),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_bad.returncode, 1, proc_bad.stderr + proc_bad.stdout)
            self.assertIn("[FAIL]", proc_bad.stdout)

    def test_cli_q2_strip_strings_and_hint(self) -> None:
        ref_q2 = json.loads((PROJECT_ROOT / "ref_q2.json").read_text(encoding="utf-8"))
        mongo_q2 = [
            {
                "s_acctbal": float(r["acctbal"]),
                "s_name": r["supplier_name"],
                "n_name": r["nation_name"],
                "p_partkey": r["partkey"],
                "p_mfgr": r["mfgr"],
                "s_address": r["address"],
                "s_phone": r["phone"],
                "s_comment": r["supplier_comment"],
            }
            for r in ref_q2
        ]
        mongo_q2[0]["s_comment"] = " " + mongo_q2[0]["s_comment"] + " "
        mongo_q2[1]["s_address"] = mongo_q2[1]["s_address"] + " "

        with tempfile.TemporaryDirectory() as tmp:
            q2_path = Path(tmp) / "mongo_q2.json"
            q2_path.write_text(json.dumps(mongo_q2), encoding="utf-8")

            proc_fail = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "validate_tpch.py"),
                    "-q",
                    "2",
                    "--mongo-file",
                    str(q2_path),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_fail.returncode, 1, proc_fail.stderr + proc_fail.stdout)
            self.assertIn("[FAIL]", proc_fail.stdout)
            self.assertIn("[DICA]", proc_fail.stdout)

            proc_pass = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "validate_tpch.py"),
                    "-q",
                    "2",
                    "--mongo-file",
                    str(q2_path),
                    "--strip-strings",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_pass.returncode, 0, proc_pass.stderr + proc_pass.stdout)
            self.assertIn("[PASS]", proc_pass.stdout)
            self.assertIn("strip_strings=2", proc_pass.stdout)


if __name__ == "__main__":
    unittest.main()
