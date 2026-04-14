# Frontend 로직 상세 설명서

이 문서는 현재 프로젝트의 `frontend` 영역이 **어떤 데이터 흐름으로 화면을 구성하고**, **사용자 액션에 따라 어떤 백엔드 API를 호출하며**, **실시간 로그/상태를 어떻게 반영하는지**를 코드 기준으로 상세하게 설명합니다.

---

## 1) 구성 파일과 역할

- `frontend/server.py`
  - FastAPI 앱 본체
  - 대시보드 정적 파일 서빙 (`/`, `/static`)
  - 실시간 이벤트 스트림(SSE) 제공 (`/api/stream`)
  - 크롤링/분석/리포트 실행 API 제공
  - 단일 품목 파이프라인 실행 상태 관리
- `frontend/static/index.html`
  - 실제 대시보드 UI (HTML/CSS + 순수 JS)
  - 사용자 액션(분석 버튼) 처리
  - SSE 수신 및 로그/사이트상태/진행상태 렌더링
  - 결과 카드/논문 카드/PDF 다운로드/이상치 카드 렌더링
- `frontend/dashboard_sites.py`
  - “크롤링 사이트 상태” 패널에 표시되는 사이트 메타 정의
  - 사이트별 초기 상태(`pending`) 생성 유틸

---

## 2) 서버 부트스트랩과 런타임 상태

## 2-1. 초기화 순서 (`frontend/server.py`)

1. 프로젝트 루트(`ROOT`) 계산
2. `.env` 로드 시도 (`python-dotenv`가 있으면)
3. FastAPI 관련 의존성 import
4. `sys.path`에 루트 추가(프로젝트 내부 모듈 import 목적)
5. Playwright 환경 준비 (`prepare_playwright_browser_env()`)
6. 파이프라인/DB/대시보드 사이트 정의 import
7. 전역 상태 초기화

## 2-2. 전역 상태 객체

- `_state`
  - `events`: SSE로 흘려보낼 이벤트 버퍼(list)
  - `lock`: 이벤트 버퍼 보호용 `asyncio.Lock`
  - `running`: 크롤 작업 실행 여부
- `_site_states`
  - `dashboard_sites.py` 정의 기반 사이트별 상태
  - `{status, message, ts}` 구조
- `_analysis_cache`
  - 전체 분석 캐시 (`result`, `running`)
- `_pipeline_tasks`
  - 단일 품목별 파이프라인 실행 상태 캐시
- `_report_cache`
  - 보고서 생성 상태/최근 PDF 경로 캐시

## 2-3. Lifespan

- 앱 시작 시 `_state["lock"] = asyncio.Lock()` 생성
- 사이트 상태 `_reset_site_states()`로 초기화
- 주석대로 lock을 모듈 로드 시점이 아니라 lifespan에서 생성해 event-loop 결합 이슈를 피함

---

## 3) 이벤트 수집/전파 구조 (SSE 중심)

## 3-1. 이벤트 적재 함수 `_emit(event)`

- 파이프라인에서 전달되는 이벤트에 `ts`(timestamp) 추가
- lock으로 보호된 임계영역에서 `_state["events"]`에 append
- 버퍼가 500개 초과 시 최근 400개만 유지(메모리 완충)
- 이벤트가 `phase == "site_progress"`이고 `site_key`가 있으면 `_site_states` 즉시 갱신

즉, **모든 실시간 UI 갱신의 원천은 `_emit`**입니다.

## 3-2. SSE 스트림 엔드포인트 `/api/stream`

- 클라이언트별 `last` 인덱스를 유지하는 generator
- 약 120ms마다 새 이벤트를 확인
- 미전송 이벤트를 `data: <json>\n\n` 형식으로 전송
- 헤더:
  - `Cache-Control: no-cache`
  - `Connection: keep-alive`
  - `X-Accel-Buffering: no` (프록시 버퍼링 방지)

결과적으로 프런트는 폴링 대신 **서버 푸시형 로그 스트림**을 받습니다.

---

## 4) 핵심 API와 화면 연계

## 4-1. 크롤/상태 관련

- `POST /api/run`
  - 전체 크롤 백그라운드 실행
  - 이미 실행 중이면 409
- `GET /api/status`
  - `{running, event_count}` 반환
- `GET /api/sites`
  - 사이트 상태 패널 렌더링용 데이터 반환
  - `dashboard_sites.py`의 정의(`name`, `hint`, `domain`) + 실시간 상태 병합
- `GET /api/products`
  - DB 제품 목록 반환
  - `raw_payload`가 문자열이면 JSON 파싱 시도

## 4-2. 분석 관련 (전체)

- `POST /api/analyze`
  - `analysis.sg_export_analyzer.analyze_all` 실행
  - 옵션:
    - `use_perplexity` (기본 true)
    - `force_refresh`
  - Perplexity reference를 품목별 결과에 주입
- `GET /api/analyze/status`
  - 분석 실행 여부와 결과 존재 여부 반환
- `GET /api/analyze/result`
  - 결과가 없으면 404
  - 실행 중이면 202

## 4-3. 단일 품목 파이프라인

- `POST /api/pipeline/{product_key}`
  - 해당 품목 단위 파이프라인 시작
  - task 상태를 `_pipeline_tasks[product_key]`에 등록
- `GET /api/pipeline/{product_key}/status`
  - 진행 step과 결과 존재 여부/참고문헌 개수/PDF 여부 반환
- `GET /api/pipeline/{product_key}/result`
  - 최종 result/refs/pdf 반환

## 4-4. 리포트

- `POST /api/report`
  - `report_generator.py`를 subprocess로 실행
  - 옵션: `run_analysis`
- `GET /api/report/status`
  - 생성 상태 + 최신 PDF + PDF 개수
- `GET /api/report/download`
  - 최신 PDF 파일 반환

## 4-5. 기타

- `GET /api/macro`
  - 거시지표 카드 데이터 반환 (`utils.sg_macro`)
- `GET /`
  - `frontend/static/index.html` 반환
- `app.mount("/static", ...)`
  - 정적 자원 경로 마운트

---

## 5) 단일 품목 파이프라인 내부 단계 (`_run_pipeline_for_product`)

단일 품목 파이프라인은 아래 4단계를 순차 실행합니다.

1. `crawl`
   - 전체 크롤 `run_full_crawl(ROOT, _emit, db_path=DB_PATH)` 실행
   - `_state["running"] = True`로 표시
2. `analyze`
   - DB에서 해당 품목 row 조회
   - `analyze_product(product_key, db_row)` 실행
   - 로그에 분석 결과 suffix 표기(키 미설정/실패/판정)
3. `refs`
   - `fetch_references(product_key)`로 논문/참고자료 수집
4. `report`
   - 임시 JSON 파일에 분석 결과 저장
   - `report_generator.py --analysis-json <temp>` subprocess 실행
   - 최신 PDF 파일명 저장

오류 시:

- `status=error`, `step=error`, `step_label=<예외문자열>` 설정
- SSE 로그로 `"오류: ..."` 전송

성공 시:

- `status=done`, `step=done`, `step_label=완료`
- SSE 로그 `"파이프라인 완료 ✓"`

---

## 6) 프런트 UI 구조 (`index.html`)

## 6-1. 상단/요약

- 헤더: 고정(sticky), 프로젝트 타이틀 표시
- 거시지표 4칸 카드: `/api/macro` 결과 렌더링

## 6-2. 분석 실행 카드

- 품목 select (`8개 product_key`)
- “진출 적합 분석” 버튼
- 진행 단계 바(크롤링 → Claude 분석 → 논문 검색 → PDF 생성)

## 6-3. 결과 카드 3종

- 분석 결과 카드 (`result-card`)
  - verdict badge, 품목명/INN, 모델 정보, rationale, key factors
- 논문 카드 (`papers-card`)
  - 참고 링크 목록 렌더
  - 없으면 API 키 안내문 출력
- PDF 카드 (`report-card`)
  - `/api/report/download` 링크

## 6-4. 운영성 패널

- 크롤링 사이트 상태(접기/펼치기)
  - `/api/sites` 기반 상태 점(dot)/요약 메시지
- 실시간 로그(details 접이식)
  - SSE 이벤트 표시
  - 최대 300줄 유지
- 이상치 검증 카드
  - 적합/이상치/미분석 요약
  - 신뢰도 분포 바
  - 도넛 차트
  - 품목별 상세 테이블

---

## 7) 프런트 JS 로직 흐름

## 7-1. 초기화 루틴

페이지 로드 후 즉시:

1. `loadMacro()`
2. `connectSSE()`
3. `refreshSites()`
4. `setInterval(refreshSites, 6000)`
5. `"대시보드 초기화 완료"` 로그 출력

## 7-2. `runPipeline()` 버튼 클릭 시

1. 선택된 `productKey` 저장
2. 진행바/카드/UI 상태 초기화
3. 버튼 비활성화 + 아이콘 `⏳`
4. 크롤 패널 자동 펼침
5. `POST /api/pipeline/{product_key}` 호출
6. 성공 시 SSE 재연결 + 2.5초 주기 `pollPipeline()`

## 7-3. `pollPipeline()`

- `/status` 조회로 step 상태 갱신
- `done`이면:
  - interval 중지
  - `/result` 조회
  - `renderResult()` 호출
  - 버튼 복구
  - sites refresh
- `error`이면:
  - interval 중지
  - 진행바 오류 상태
  - 버튼 복구

## 7-4. `renderResult()`

- verdict 및 오류 상태에 따라 badge 텍스트 결정
  - 예: `analysis_error === "no_api_key"` → “API 키 미설정”
- 모델 정보(`analysis_model`, `claude_model_id`, `claude_error_detail`) 표시
- rationale 텍스트와 factor chip 렌더
- refs 목록을 링크로 렌더
- PDF 존재 시 다운로드 카드 표시

## 7-5. `refreshOutlier()`

- `/api/products` 기반으로 verdict/confidence 집계
- 정상/이상치/미분석 수 계산
- 평균 신뢰도 계산
- 도넛 차트 stroke-dasharray로 비율 반영
- 신뢰도 버킷(0.3~1.0, 7구간) 바 차트 렌더
- 품목 테이블 생성(진입경로/신뢰도/판정)

---

## 8) 상태/동시성 제어 포인트

- 서버 측 이벤트 버퍼는 lock으로 보호됨
- 크롤 중복 실행은 `_state["running"]`으로 방지
- 분석 중복 실행은 `_analysis_cache["running"]`으로 방지
- 품목별 파이프라인 중복은 `_pipeline_tasks[product_key]["status"]`로 방지
- 보고서 생성 중복은 `_report_cache["running"]`으로 방지

주의할 점:

- 단일 품목 파이프라인에서 `_state["running"]`을 공유하므로, 다른 실행 경로와 동시에 돌릴 때 UX 충돌 가능성 있음
- 클라이언트가 탭 여러 개면 SSE 연결이 여러 개 생기며 각 탭이 독립적으로 로그를 누적

---

## 9) 데이터 형태(프런트가 기대하는 주요 필드)

## 9-1. 파이프라인 결과 (`/api/pipeline/{key}/result`)

- `result`
  - `product_id`, `trade_name`, `inn`
  - `verdict` (적합/부적합/조건부 등)
  - `analysis_error` (`no_api_key`, `claude_failed` 등)
  - `analysis_model`, `claude_model_id`, `claude_error_detail`
  - `rationale`, `key_factors[]`
- `refs[]`
  - `title`, `url`, `source`, `reason`
- `pdf`
  - 최신 PDF 파일명

## 9-2. 제품 목록 (`/api/products`)

- `trade_name`, `product_key`, `confidence`, `verdict` 등
- `raw_payload` 내부에서 추가 신호를 읽어 이상치 카드에서 사용

---

## 10) UI/백엔드 메시지 결합 방식

- 프런트는 `ev.phase`와 `ev.message`를 그대로 로그에 출력
- 사이트 카드는 `site_progress` 이벤트만으로 갱신
- 진행바는 `/api/pipeline/.../status`의 `step`을 기준으로 제어
- 즉:
  - **로그/사이트상태는 SSE 중심**
  - **단계 완료판정은 status polling 중심**

이 이중 구조 덕분에 사용자 체감상 실시간성이 높고, SSE 누락 시에도 폴링으로 최종 상태 복구가 가능합니다.

---

## 11) 현재 코드 기준 확인된 특징/갭

- `dashboard_sites.py`의 `moh_pdf` 힌트 문구는 “PDF 링크 수집”으로 적혀 있으나, 실제 최근 로직은 MOH 뉴스 텍스트/링크 수집으로 확장됨
- 프런트는 API 응답 필드에 매우 직접적으로 의존하므로 백엔드 필드명 변경 시 UI가 즉시 깨질 수 있음
- 오류 대부분을 JS에서 `catch(e){}`로 무시하는 구간이 있어(특히 refresh/poll 쪽), 장애 원인 추적은 서버 로그 의존도가 높음

---

## 12) 실행 관점 요약

- 사용자는 `index.html`에서 품목 선택 후 `runPipeline()` 실행
- 서버는 백그라운드 태스크로 크롤→분석→논문→PDF 순서 처리
- 처리 중 이벤트는 `_emit`으로 중앙 적재, `/api/stream`으로 지속 송출
- 프런트는 로그/Site상태/Step/결과카드/이상치카드를 조합해 “한 화면에서 운영 + 결과 확인” UX를 제공

이상으로 현재 `frontend` 로직 전체 흐름 설명을 마칩니다.
