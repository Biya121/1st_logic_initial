"""분석 엔진 단위 테스트 — 네트워크 없이 정적 폴백 경로 검증."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path


class TestExportAnalyzerStatic(unittest.TestCase):
    """API 키 없이 정적 폴백으로 analyze_product 동작 확인."""

    def setUp(self) -> None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        # API 키 제거 (정적 폴백 강제)
        import os
        self._orig = {
            k: os.environ.pop(k, None)
            for k in ("CLAUDE_API_KEY", "ANTHROPIC_API_KEY", "PERPLEXITY_API_KEY")
        }

    def tearDown(self) -> None:
        import os
        for k, v in self._orig.items():
            if v is not None:
                os.environ[k] = v

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_analyze_all_returns_8_results(self) -> None:
        """analyze_all이 8품목 결과를 반환해야 함."""
        from analysis.sg_export_analyzer import analyze_all
        results = self._run(analyze_all(use_perplexity=False))
        self.assertEqual(len(results), 8)

    def test_result_has_required_fields(self) -> None:
        """모든 결과에 필수 필드 존재."""
        from analysis.sg_export_analyzer import analyze_all
        results = self._run(analyze_all(use_perplexity=False))
        required = ["product_id", "trade_name", "verdict", "verdict_en",
                    "rationale", "key_factors", "sources", "analyzed_at"]
        for r in results:
            for field in required:
                self.assertIn(field, r, f"{r.get('product_id')}: '{field}' 필드 없음")

    def test_verdict_values_valid(self) -> None:
        """verdict는 적합/부적합/조건부 또는 None(API 미설정) 이어야 함."""
        from analysis.sg_export_analyzer import analyze_all
        results = self._run(analyze_all(use_perplexity=False))
        valid = {"적합", "부적합", "조건부", None}
        for r in results:
            self.assertIn(r["verdict"], valid,
                          f"{r.get('product_id')}: verdict={r.get('verdict')!r}")

    def test_verdict_en_values_valid(self) -> None:
        """verdict_en은 SUITABLE/UNSUITABLE/CONDITIONAL 또는 None(API 미설정)."""
        from analysis.sg_export_analyzer import analyze_all
        results = self._run(analyze_all(use_perplexity=False))
        valid = {"SUITABLE", "UNSUITABLE", "CONDITIONAL", None}
        for r in results:
            self.assertIn(r["verdict_en"], valid,
                          f"{r.get('product_id')}: verdict_en={r.get('verdict_en')!r}")

    def test_rationale_not_empty(self) -> None:
        """rationale은 비어있지 않아야 함."""
        from analysis.sg_export_analyzer import analyze_all
        results = self._run(analyze_all(use_perplexity=False))
        for r in results:
            self.assertTrue(
                len(r.get("rationale", "")) > 10,
                f"{r.get('product_id')}: rationale 너무 짧음"
            )

    def test_fallback_model_label(self) -> None:
        """API 키 없으면 analysis_model이 static_fallback이어야 함."""
        from analysis.sg_export_analyzer import analyze_all
        results = self._run(analyze_all(use_perplexity=False))
        for r in results:
            self.assertEqual(
                r.get("analysis_model"), "static_fallback",
                f"{r.get('product_id')}: model={r.get('analysis_model')!r}"
            )

    def test_unknown_product_id_returns_error(self) -> None:
        """알 수 없는 product_id는 error 필드를 반환해야 함."""
        from analysis.sg_export_analyzer import analyze_product
        result = self._run(analyze_product("UNKNOWN_PID"))
        self.assertIn("error", result)

    def test_all_8_product_ids_covered(self) -> None:
        """8품목 product_id 전부 커버."""
        from analysis.sg_export_analyzer import analyze_all, PRODUCT_META
        results = self._run(analyze_all(use_perplexity=False))
        result_pids = {r["product_id"] for r in results}
        expected_pids = {m["product_id"] for m in PRODUCT_META}
        self.assertEqual(result_pids, expected_pids)

    def test_gastiin_returns_result(self) -> None:
        """Gastiin CR 분석 결과가 반환되어야 함 (API 미설정 시 verdict=None)."""
        from analysis.sg_export_analyzer import analyze_product
        r = self._run(analyze_product("SG_gastiin_cr_mosapride", use_perplexity=False))
        self.assertIn("product_id", r)
        self.assertIn("hsa_reg", r)
        self.assertIn("entry_pathway", r)

    def test_sereterol_returns_result(self) -> None:
        """Sereterol Activair 분석 결과가 반환되어야 함 (API 미설정 시 verdict=None)."""
        from analysis.sg_export_analyzer import analyze_product
        r = self._run(analyze_product("SG_sereterol_activair", use_perplexity=False))
        self.assertIn("product_id", r)
        self.assertIn("hsa_reg", r)
        self.assertIn("entry_pathway", r)

    def test_key_factors_is_list(self) -> None:
        """key_factors는 리스트여야 함."""
        from analysis.sg_export_analyzer import analyze_all
        results = self._run(analyze_all(use_perplexity=False))
        for r in results:
            self.assertIsInstance(r["key_factors"], list,
                                  f"{r.get('product_id')}: key_factors가 리스트가 아님")

    def test_sources_is_list(self) -> None:
        """sources는 리스트여야 함."""
        from analysis.sg_export_analyzer import analyze_all
        results = self._run(analyze_all(use_perplexity=False))
        for r in results:
            self.assertIsInstance(r["sources"], list,
                                  f"{r.get('product_id')}: sources가 리스트가 아님")

    def test_with_db_row_hsa_reg_present(self) -> None:
        """db_row 제공 시에도 hsa_reg 필드가 반환 결과에 포함."""
        from analysis.sg_export_analyzer import analyze_product
        fake_row = {"price_local": 52.3, "confidence": 0.72}
        r = self._run(
            analyze_product("SG_sereterol_activair", db_row=fake_row, use_perplexity=False)
        )
        self.assertIn("hsa_reg", r)
        self.assertIn("product_type", r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
