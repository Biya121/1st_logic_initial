"""HTML → 가시 텍스트 추출 (trafilatura 우선, 미설치 시 bs4 폴백).

보고서 §9: trafilatura_fallback.py — HTML이면 어디든 작동
"""

from __future__ import annotations


def extract_text(html: str, *, url: str = "") -> str:
    """HTML에서 가시 텍스트를 추출한다.

    우선순위:
    1. trafilatura (설치된 경우) — 광고·메뉴 제거, 본문 집중 추출
    2. BeautifulSoup4 (설치된 경우) — script/style 제거 후 get_text
    3. 정규식 태그 제거 (최후 폴백)
    """
    if not html:
        return ""

    # 1) trafilatura
    try:
        import trafilatura  # type: ignore

        result = trafilatura.extract(
            html,
            url=url or None,
            include_tables=True,
            include_links=False,
            no_fallback=False,
        )
        if result:
            return result
    except ImportError:
        pass
    except Exception:
        pass

    # 2) BeautifulSoup4
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
        # 연속 빈줄 압축
        import re
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text
    except ImportError:
        pass

    # 3) 정규식 태그 제거
    import re
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text
