"""순수 함수 단위 테스트 (네트워크 없음)."""

from __future__ import annotations

import unittest

from crawlers.http_probe import extract_html_title
from crawlers.sg_moh_news_pdf import collect_pdf_hrefs


class TestHttpProbe(unittest.TestCase):
    def test_extract_title(self) -> None:
        html = "<html><head><title>  Hello \n World  </title></head></html>"
        self.assertEqual(extract_html_title(html), "Hello World")

    def test_collect_pdf_hrefs(self) -> None:
        html = (
            '<a href="/x/a.PDF">a</a> '
            '<a href=\'https://moh.gov.sg/y/b.pdf?q=1\'>b</a>'
        )
        links = collect_pdf_hrefs(html, "https://www.moh.gov.sg/news/", limit=10)
        self.assertEqual(
            links,
            [
                "https://www.moh.gov.sg/x/a.PDF",
                "https://moh.gov.sg/y/b.pdf?q=1",
            ],
        )


if __name__ == "__main__":
    unittest.main()
