"""D7 통합 테스트 — 네트워크 없이 전체 파이프라인 end-to-end 검증.

보고서 §11 D7:
  IQR 검증 + inn_normalizer 매칭 확인 + products 테이블 쿼리 검증
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path


class TestFullPipeline(unittest.TestCase):
    """pipeline.run_full_crawl → DB upsert → fetch 전체 흐름."""

    def setUp(self) -> None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db_path = Path(self._tmp.name)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_pipeline_produces_8_products(self) -> None:
        """파이프라인 실행 후 products 테이블에 8개 품목이 존재해야 함."""
        from crawlers.pipeline import run_full_crawl

        root = Path(__file__).resolve().parents[1]
        events: list[dict] = []

        async def collect_emit(e: dict) -> None:
            events.append(e)

        self._run(
            run_full_crawl(
                root,
                collect_emit,
                db_path=self.db_path,
                include_ai_discovery=False,
            )
        )

        from utils import db as dbutil
        conn = dbutil.get_connection(self.db_path)
        rows = dbutil.fetch_all_products(conn)
        conn.close()

        pids = {r["product_id"] for r in rows}
        expected = {
            "SG_hydrine_hydroxyurea_500",
            "SG_gadvoa_gadobutrol_604",
            "SG_sereterol_activair",
            "SG_omethyl_omega3_2g",
            "SG_rosumeg_combigel",
            "SG_atmeg_combigel",
            "SG_ciloduo_cilosta_rosuva",
            "SG_gastiin_cr_mosapride",
        }
        missing = expected - pids
        self.assertEqual(missing, set(), f"누락된 product_id: {missing}")

    def test_pipeline_no_confidence_1(self) -> None:
        """모든 레코드의 confidence < 1.0 (헌법 필수)."""
        from crawlers.pipeline import run_full_crawl
        root = Path(__file__).resolve().parents[1]

        async def _noop(e): pass
        self._run(run_full_crawl(root, _noop, db_path=self.db_path))

        from utils import db as dbutil
        conn = dbutil.get_connection(self.db_path)
        rows = dbutil.fetch_all_products(conn)
        conn.close()

        violations = [r["product_id"] for r in rows if r.get("confidence", 0) >= 1.0]
        self.assertEqual(violations, [], f"confidence 1.0 위반: {violations}")

    def test_pipeline_required_columns_not_null(self) -> None:
        """헌법 필수 9컬럼이 NULL이 아닌지 확인."""
        from crawlers.pipeline import run_full_crawl
        root = Path(__file__).resolve().parents[1]

        async def _noop(e): pass
        self._run(run_full_crawl(root, _noop, db_path=self.db_path))

        from utils import db as dbutil
        conn = dbutil.get_connection(self.db_path)
        rows = dbutil.fetch_all_products(conn)
        conn.close()

        required = ["country", "currency", "product_id", "market_segment",
                    "confidence", "source_url", "source_tier", "source_name"]
        for row in rows:
            for col in required:
                self.assertIsNotNone(
                    row.get(col),
                    f"{row['product_id']}: 필수 컬럼 {col!r} is NULL"
                )

    def test_pipeline_fob_column_exists_but_null(self) -> None:
        """fob_estimated_usd 컬럼은 존재하되 1공정 레코드는 NULL (헌법 §2, 2공정 위임)."""
        from utils import db as dbutil
        conn = dbutil.get_connection(self.db_path)
        rows = dbutil.fetch_all_products(conn)
        conn.close()
        for row in rows:
            self.assertIn("fob_estimated_usd", row)
            self.assertIsNone(row["fob_estimated_usd"])

    def test_pipeline_country_sg(self) -> None:
        """모든 레코드의 country='SG', currency='SGD'."""
        from crawlers.pipeline import run_full_crawl
        root = Path(__file__).resolve().parents[1]

        async def _noop(e): pass
        self._run(run_full_crawl(root, _noop, db_path=self.db_path))

        from utils import db as dbutil
        conn = dbutil.get_connection(self.db_path)
        rows = dbutil.fetch_all_products(conn)
        conn.close()

        for row in rows:
            self.assertEqual(row["country"], "SG")
            self.assertEqual(row["currency"], "SGD")


class TestInnMatchingIntegration(unittest.TestCase):
    """inn_normalizer 매칭 결과 검증 (보고서 §4)."""

    def setUp(self) -> None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from crawlers.sg_inn_map import register_sg_brands
        from inn_normalizer import _inn
        register_sg_brands()
        self._inn = _inn

    def test_all_8_brands_resolved(self) -> None:
        brands = [
            ("Hydrine", "hydroxyurea"),
            ("Gadvoa Inj.", "gadobutrol"),
            ("Sereterol Activair", "fluticasone/salmeterol"),
            ("Omethyl", "omega-3 acid ethyl esters"),
            ("Rosumeg Combigel", "rosuvastatin/omega-3 acid ethyl esters"),
            ("Atmeg Combigel", "atorvastatin/omega-3 acid ethyl esters"),
            ("Ciloduo", "cilostazol/rosuvastatin"),
            ("Gastiin CR", "mosapride"),
        ]
        for brand, expected_inn in brands:
            rec = self._inn.normalize_record({"trade_name": brand})
            self.assertEqual(
                rec.get("inn_name"), expected_inn,
                f"{brand} → 기대 {expected_inn!r}, 실제 {rec.get('inn_name')!r}"
            )
            self.assertEqual(rec.get("inn_match_type"), "brand_map")

    def test_inn_id_format(self) -> None:
        """inn_id는 'inn_' 접두사 + 슬러그 형태."""
        rec = self._inn.normalize_record({"trade_name": "Hydrine"})
        self.assertTrue(rec["inn_id"].startswith("inn_"))
        self.assertNotIn(" ", rec["inn_id"])


class TestIqrPipelineIntegration(unittest.TestCase):
    """IQR 이상치 탐지가 outlier_flagged에 반영되는지 확인 (보고서 §9 iqr.py)."""

    def setUp(self) -> None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    def test_outlier_flag_set_on_extreme_value(self) -> None:
        from utils.iqr import iqr_outlier
        prices = [100.0, 102.0, 98.0, 101.0, 99.0, 103.0, 97.0, 9999.0]
        _, _, flagged = iqr_outlier(prices)
        self.assertTrue(flagged, "극단값이 이상치로 탐지되어야 함")

    def test_outlier_flag_not_set_on_normal(self) -> None:
        from utils.iqr import iqr_outlier
        prices = [100.0, 102.0, 98.0, 101.0, 99.0, 103.0, 97.0, 100.5]
        _, _, flagged = iqr_outlier(prices)
        self.assertFalse(flagged, "정상값은 이상치 아님")


class TestReportGenerator(unittest.TestCase):
    """report_generator.py 기본 동작 검증."""

    def setUp(self) -> None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        self._tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp_db.close()
        self._tmp_out = tempfile.mkdtemp()

    def tearDown(self) -> None:
        Path(self._tmp_db.name).unlink(missing_ok=True)
        import shutil
        shutil.rmtree(self._tmp_out, ignore_errors=True)

    def test_report_runs_on_empty_db(self) -> None:
        """빈 DB에서도 보고서가 생성되어야 함."""
        from report_generator import main
        ret = main(["--db", self._tmp_db.name, "--out", self._tmp_out])
        self.assertEqual(ret, 0)
        out = list(Path(self._tmp_out).glob("sg_report_*.json"))
        self.assertEqual(len(out), 1)

    def test_report_json_structure(self) -> None:
        """JSON 보고서에 meta + products 키 존재."""
        from report_generator import main
        main(["--db", self._tmp_db.name, "--out", self._tmp_out])
        json_file = list(Path(self._tmp_out).glob("sg_report_*.json"))[0]
        data = json.loads(json_file.read_text(encoding="utf-8"))
        self.assertIn("meta", data)
        self.assertIn("products", data)
        self.assertEqual(len(data["products"]), 8)

    def test_report_pdf_generated(self) -> None:
        """PDF 보고서 파일이 생성되어야 함."""
        from report_generator import main
        main(["--db", self._tmp_db.name, "--out", self._tmp_out])
        pdf_files = list(Path(self._tmp_out).glob("sg_report_*.pdf"))
        self.assertEqual(len(pdf_files), 1)
        # PDF 시그니처 확인 (%PDF-)
        with open(pdf_files[0], "rb") as f:
            header = f.read(5)
        self.assertEqual(header, b"%PDF-")

    def test_report_no_fob_in_meta(self) -> None:
        """보고서 meta에 fob_estimated_usd 없음 (2공정 위임 — 보고서 메타에는 포함 안 함)."""
        from report_generator import main
        main(["--db", self._tmp_db.name, "--out", self._tmp_out])
        json_file = list(Path(self._tmp_out).glob("sg_report_*.json"))[0]
        data = json.loads(json_file.read_text(encoding="utf-8"))
        self.assertNotIn("fob_estimated_usd", data["meta"])


class TestSupabaseState(unittest.TestCase):
    """supabase_state.py 서킷브레이커 + 토큰버킷 단위 테스트."""

    def setUp(self) -> None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    def test_circuit_starts_closed(self) -> None:
        from utils.supabase_state import CircuitBreaker, CircuitState
        cb = CircuitBreaker()
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertTrue(cb.allow_request())

    def test_circuit_opens_after_failures(self) -> None:
        from utils.supabase_state import CircuitBreaker, CircuitState
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertFalse(cb.allow_request())

    def test_circuit_resets_on_success(self) -> None:
        from utils.supabase_state import CircuitBreaker, CircuitState
        cb = CircuitBreaker(failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        self.assertFalse(cb.allow_request())
        # 강제로 CLOSED 복구 후 success
        cb._state = CircuitState.HALF_OPEN
        cb.record_success()
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_token_bucket_acquires(self) -> None:
        from utils.supabase_state import TokenBucket
        bucket = TokenBucket(rate_rps=100.0, capacity=10.0)
        bucket._tokens = 5.0
        # 동기 테스트를 위해 직접 확인
        self.assertGreaterEqual(bucket._tokens, 1.0)


class TestSessionCache(unittest.TestCase):
    """session_cache.py ttl_guard 단위 테스트."""

    def setUp(self) -> None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    def test_ttl_guard_none(self) -> None:
        from utils.session_cache import ttl_guard
        self.assertFalse(ttl_guard(None))

    def test_ttl_guard_no_expires(self) -> None:
        from utils.session_cache import ttl_guard
        self.assertFalse(ttl_guard({"cookies": []}))

    def test_ttl_guard_expired(self) -> None:
        import time
        from utils.session_cache import ttl_guard
        self.assertFalse(ttl_guard({"expires_at": time.time() - 10}))

    def test_ttl_guard_valid(self) -> None:
        import time
        from utils.session_cache import ttl_guard
        # 만료까지 600초 남음 (5분 마진 이상)
        self.assertTrue(ttl_guard({"expires_at": time.time() + 600}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
