"""파이프라인 핵심 로직 단위 테스트 (네트워크·DB 없음)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class TestMapToSchema(unittest.TestCase):
    def setUp(self) -> None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from crawlers.common import map_to_schema
        self.mts = map_to_schema

    def test_required_fields_present(self) -> None:
        item = {
            "reg_no": "SIN11083P",
            "product_name": "Hydrine",
            "market_segment": "tender",
            "confidence": 0.92,
        }
        rec = self.mts(item, source_url="http://x", source_name="hsa", source_tier=1)
        for col in ("country", "currency", "product_id", "trade_name",
                    "market_segment", "confidence", "source_url",
                    "source_tier", "source_name"):
            self.assertIn(col, rec, f"필수 컬럼 누락: {col}")

    def test_country_currency_sg(self) -> None:
        rec = self.mts(
            {"product_name": "Test", "market_segment": "retail"},
            source_url="http://x", source_name="test", source_tier=1,
        )
        self.assertEqual(rec["country"], "SG")
        self.assertEqual(rec["currency"], "SGD")

    def test_confidence_100_clamped(self) -> None:
        rec = self.mts(
            {"product_name": "Test", "market_segment": "retail", "confidence": 1.0},
            source_url="http://x", source_name="test", source_tier=1,
        )
        self.assertLess(rec["confidence"], 1.0, "confidence 1.0은 절대 금지")

    def test_confidence_above_094_api_realtime_clamped(self) -> None:
        rec = self.mts(
            {
                "product_name": "Test",
                "market_segment": "retail",
                "confidence": 0.99,
                "sg_source_type": "api_realtime",
            },
            source_url="http://x", source_name="test", source_tier=1,
        )
        self.assertLessEqual(rec["confidence"], 0.94)

    def test_raw_payload_sg_fields(self) -> None:
        item = {"product_name": "X", "market_segment": "retail",
                "sg_ndf_listed": True, "sg_gebiz_award": "AWARD-001"}
        rec = self.mts(item, source_url="http://x", source_name="t", source_tier=1)
        rp = rec["raw_payload"]
        self.assertTrue(rp["sg_ndf_listed"])
        self.assertEqual(rp["sg_gebiz_award"], "AWARD-001")

    def test_product_id_auto_generated(self) -> None:
        rec = self.mts(
            {"reg_no": "SIN99999P", "product_name": "AutoId", "market_segment": "retail"},
            source_url="http://x", source_name="t", source_tier=1,
        )
        self.assertTrue(rec["product_id"].startswith("SG_"))

    def test_price_local_not_fob(self) -> None:
        """price_local만 저장 — FOB 컬럼 없음."""
        item = {"product_name": "X", "market_segment": "retail", "price": 42.5}
        rec = self.mts(item, source_url="http://x", source_name="t", source_tier=1)
        self.assertEqual(rec["price_local"], 42.5)
        self.assertNotIn("fob_estimated_usd", rec)


class TestInnNormalizer(unittest.TestCase):
    def setUp(self) -> None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from inn_normalizer import InnNormalizer
        self.inn = InnNormalizer()
        self.inn.register_brand("Hydrine", "hydroxyurea")
        self.inn.register_brand("Gastiin CR", "mosapride")

    def test_exact_brand_match(self) -> None:
        rec = self.inn.normalize_record({"trade_name": "Hydrine"})
        self.assertEqual(rec["inn_name"], "hydroxyurea")
        self.assertEqual(rec["inn_match_type"], "brand_map")

    def test_partial_brand_match(self) -> None:
        rec = self.inn.normalize_record({"trade_name": "Gastiin CR 15mg"})
        self.assertIn(rec.get("inn_match_type"), ("brand_map",))

    def test_scientific_fallback(self) -> None:
        rec = self.inn.normalize_record({
            "trade_name": "UnknownBrand",
            "scientific_name": "amlodipine",
        })
        self.assertEqual(rec["inn_match_type"], "scientific_fallback")

    def test_unresolved(self) -> None:
        rec = self.inn.normalize_record({"trade_name": "CompletelyUnknown"})
        self.assertEqual(rec["inn_match_type"], "unresolved")

    def test_no_trade_name(self) -> None:
        rec = self.inn.normalize_record({})
        self.assertEqual(rec["inn_match_type"], "none")

    def test_inn_id_slug(self) -> None:
        rec = self.inn.normalize_record({"trade_name": "Hydrine"})
        self.assertEqual(rec["inn_id"], "inn_hydroxyurea")


class TestCombo(unittest.TestCase):
    def setUp(self) -> None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from utils.combo import calc_combo_price_local
        self.calc = calc_combo_price_local

    def test_sum_of_components(self) -> None:
        demo = {"rosuvastatin_tab": 8.5, "omega3_ee90": 42.0}
        result = self.calc("SG_rosumeg", ["rosuvastatin_tab", "omega3_ee90"],
                           demo_prices_sgd=demo)
        self.assertAlmostEqual(result["price_local"], 50.5, places=3)

    def test_combo_multiplier_in_raw_payload(self) -> None:
        demo = {"a": 10.0, "b": 20.0}
        result = self.calc("SG_test", ["a", "b"], demo_prices_sgd=demo)
        self.assertEqual(result["raw_payload"]["combo_multiplier"], 0.85)

    def test_missing_component_returns_none(self) -> None:
        result = self.calc("SG_test", ["nonexistent_drug"])
        self.assertIsNone(result["price_local"])
        self.assertTrue(result["raw_payload"]["outlier_flagged"])

    def test_no_fob_calculation(self) -> None:
        """combo.py는 price_local 합산만. FOB는 2공정."""
        demo = {"a": 10.0, "b": 5.0}
        result = self.calc("SG_test", ["a", "b"], demo_prices_sgd=demo)
        self.assertNotIn("fob_estimated_usd", result)


class TestDbUpsert(unittest.TestCase):
    def setUp(self) -> None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from utils import db as dbutil
        self.dbutil = dbutil
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.conn = dbutil.get_connection(Path(self._tmp.name))

    def tearDown(self) -> None:
        self.conn.close()
        Path(self._tmp.name).unlink(missing_ok=True)

    def _base_row(self, pid: str = "SG_test_001") -> dict:
        return {
            "product_id": pid,
            "country": "SG",
            "currency": "SGD",
            "trade_name": "TestDrug",
            "market_segment": "retail",
            "confidence": 0.88,
            "source_url": "http://test",
            "source_tier": 1,
            "source_name": "test_source",
            "raw_payload": {"sg_source_type": "api_realtime"},
        }

    def test_insert_and_fetch(self) -> None:
        row = self._base_row()
        self.dbutil.upsert_product(self.conn, row)
        rows = self.dbutil.fetch_all_products(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["product_key"], "SG_test_001")

    def test_upsert_updates_existing(self) -> None:
        row = self._base_row()
        self.dbutil.upsert_product(self.conn, row)
        row2 = {**row, "price_local": 55.5}
        self.dbutil.upsert_product(self.conn, row2)
        rows = self.dbutil.fetch_all_products(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["price_local"], 55.5)

    def test_required_columns_persisted(self) -> None:
        row = self._base_row("SG_req_check")
        self.dbutil.upsert_product(self.conn, row)
        rows = self.dbutil.fetch_all_products(self.conn)
        r = rows[0]
        self.assertEqual(r["country"], "SG")
        self.assertEqual(r["currency"], "SGD")
        self.assertEqual(r["source_tier"], 1)

    def test_raw_payload_roundtrip(self) -> None:
        payload = {"sg_source_type": "api_realtime", "sg_ndf_listed": True}
        row = {**self._base_row(), "raw_payload": payload}
        self.dbutil.upsert_product(self.conn, row)
        rows = self.dbutil.fetch_all_products(self.conn)
        stored = json.loads(rows[0]["raw_payload"])
        self.assertEqual(stored["sg_source_type"], "api_realtime")
        self.assertTrue(stored["sg_ndf_listed"])

    def test_fob_column_exists_but_null(self) -> None:
        """products 테이블에 fob_estimated_usd 컬럼이 존재하되 1공정은 NULL (헌법 §2).
        2공정이 역산 후 채우는 필드이므로 컬럼은 있고 값은 NULL이어야 함."""
        row = self._base_row()
        self.dbutil.upsert_product(self.conn, row)
        rows = self.dbutil.fetch_all_products(self.conn)
        self.assertIn("fob_estimated_usd", rows[0])
        self.assertIsNone(rows[0]["fob_estimated_usd"])


class TestIqr(unittest.TestCase):
    def setUp(self) -> None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from utils.iqr import iqr_outlier
        self.iqr = iqr_outlier

    def test_normal_value_not_outlier(self) -> None:
        values = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0]
        lo, hi, flag = self.iqr(values)
        self.assertFalse(flag)

    def test_extreme_value_is_outlier(self) -> None:
        values = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 9999.0]
        lo, hi, flag = self.iqr(values)
        self.assertTrue(flag)

    def test_insufficient_data_returns_no_flag(self) -> None:
        lo, hi, flag = self.iqr([10.0, 20.0])
        self.assertIsNone(lo)
        self.assertFalse(flag)


class TestDomainValidator(unittest.TestCase):
    def setUp(self) -> None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from utils.domain_validator import is_trusted_domain
        self.check = is_trusted_domain

    def test_gov_sg_trusted(self) -> None:
        self.assertTrue(self.check("https://www.hsa.gov.sg/api/foo"))

    def test_who_trusted(self) -> None:
        self.assertTrue(self.check("https://www.who.int/medicines"))

    def test_commercial_not_trusted(self) -> None:
        self.assertFalse(self.check("https://www.guardian.com.sg/product/123"))

    def test_empty_url_not_trusted(self) -> None:
        self.assertFalse(self.check(""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
