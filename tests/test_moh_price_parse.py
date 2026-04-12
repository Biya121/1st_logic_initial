"""MOH HTML 가격 파서 단위 테스트."""

from __future__ import annotations

import unittest

from crawlers.moh_price_parse import (
    collect_sgd_candidates,
    html_to_visible_text,
    pick_price_near_keywords,
)


class TestMohPriceParse(unittest.TestCase):
    def test_pick_near_keyword(self) -> None:
        plain = (
            "Some intro text. Omethyl capsules are listed at S$ 86.40 per course. "
            "Other items cost $12."
        )
        p = pick_price_near_keywords(plain, ("Omethyl", "omega"))
        self.assertEqual(p, 86.4)

    def test_html_strip_and_pick(self) -> None:
        html = (
            "<html><body><p>Product Omethyl</p>"
            "<span class='x'>Price: S$125.00</span></body></html>"
        )
        plain = html_to_visible_text(html)
        p = pick_price_near_keywords(plain, ("Omethyl",))
        self.assertEqual(p, 125.0)

    def test_collect_candidates_dedup(self) -> None:
        plain = "A S$10 B $10.00 C SGD 20.5 D $20.50"
        c = collect_sgd_candidates(plain, limit=10)
        self.assertIn(10.0, c)
        self.assertIn(20.5, c)


if __name__ == "__main__":
    unittest.main()
