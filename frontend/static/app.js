/**
 * UPharma Export AI — 싱가포르 대시보드 스크립트
 * ═══════════════════════════════════════════════════════════════
 *
 * 기능 목록:
 *   §1  상수 & 전역 상태
 *   §2  탭 전환          goTab(id, el)
 *   §3  환율 로드        loadExchange()  → GET /api/exchange
 *   §4  To-Do 리스트     initTodo / toggleTodo / markTodoDone / addTodoItem
 *   §5  보고서 탭        renderReportTab / _addReportEntry
 *   §6  API 키 배지      loadKeyStatus() → GET /api/keys/status
 *   §7  진행 단계        setProgress / resetProgress
 *   §8  파이프라인       runPipeline / pollPipeline
 *   §9  신약 분석        runCustomPipeline / _pollCustomPipeline
 *   §10 결과 렌더링      renderResult
 *   §11 초기화
 *
 * 수정 이력 (원본 대비):
 *   B1  /api/sites 제거 → /api/datasource/status
 *   B2  크롤링 step → DB 조회 step (prog-db_load)
 *   B3  refreshOutlier → /api/analyze/result
 *   B4  논문 카드: refs 0건이면 숨김
 *   U1  API 키 상태 배지
 *   U2  진입 경로(entry_pathway) 표시
 *   U3  신뢰도(confidence_note) 표시
 *   U4  PDF 카드 3가지 상태
 *   U6  재분석 버튼
 *   N1  탭 전환 (AU 프론트 기반)
 *   N2  환율 카드 (yfinance SGD/KRW)
 *   N3  To-Do 리스트 (localStorage)
 *   N4  보고서 탭 자동 등록
 * ═══════════════════════════════════════════════════════════════
 */

'use strict';

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §1. 상수 & 전역 상태
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/** product_id → INN 표시명 */
const INN_MAP = {
  SG_hydrine_hydroxyurea_500:  'Hydroxyurea 500mg',
  SG_gadvoa_gadobutrol_604:    'Gadobutrol 604mg',
  SG_sereterol_activair:       'Fluticasone / Salmeterol',
  SG_omethyl_omega3_2g:        'Omega-3 EE 2g',
  SG_rosumeg_combigel:         'Rosuvastatin + Omega-3',
  SG_atmeg_combigel:           'Atorvastatin + Omega-3',
  SG_ciloduo_cilosta_rosuva:   'Cilostazol + Rosuvastatin',
  SG_gastiin_cr_mosapride:     'Mosapride CR',
};

/**
 * B2: 서버 step 이름 → 프론트 progress 단계 ID 매핑
 * 서버 step: init → db_load → analyze → refs → report → done
 */
const STEP_ORDER = ['db_load', 'analyze', 'refs', 'report'];

let _pollTimer  = null;   // 파이프라인 폴링 타이머
let _currentKey = null;   // 현재 선택된 product_key

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §2. 탭 전환 (N1)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/**
 * 탭 전환: 모든 .page / .tab 비활성 후 대상만 활성화.
 * @param {string} id  — 대상 페이지 element ID
 * @param {Element} el — 클릭된 탭 element
 */
function goTab(id, el) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('on'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('on'));
  const page = document.getElementById(id);
  if (page) page.classList.add('on');
  if (el)   el.classList.add('on');
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §3. 환율 로드 (N2) — GET /api/exchange
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

async function loadExchange() {
  const btn = document.getElementById('btn-exchange-refresh');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 조회 중…'; }

  try {
    const res  = await fetch('/api/exchange');
    const data = await res.json();

    // P2 환율 자동 채움용 전역 저장
    window._exchangeRates = data;
    if (typeof _p2FillExchangeRate === 'function') {
      _p2FillExchangeRate();
      if (typeof _renderP2Manual === 'function') _renderP2Manual();
    }

    // 메인 숫자 (KRW/SGD)
    const rateEl = document.getElementById('exchange-main-rate');
    if (rateEl) {
      const fmt = data.sgd_krw.toLocaleString('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      rateEl.innerHTML = `${fmt}<span style="font-size:14px;margin-left:4px;color:var(--muted);font-weight:700;">원</span>`;
    }

    // 서브 그리드 (USD/KRW + SGD 연관 환율)
    const subEl = document.getElementById('exchange-sub');
    if (subEl) {
      const fmtUsd = data.usd_krw.toLocaleString('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      const fmtSgdUsd = Number(data.sgd_usd).toFixed(4);
      const fmtSgdJpy = Number(data.sgd_jpy).toFixed(4);
      const fmtSgdCny = Number(data.sgd_cny).toFixed(4);
      subEl.innerHTML = `
        <div class="irow" style="margin:0">
          <div style="font-size:10.5px;color:var(--muted);margin-bottom:3px;">USD / KRW</div>
          <div style="font-size:15px;font-weight:900;color:var(--navy);">${fmtUsd}원</div>
        </div>
        <div class="irow" style="margin:0">
          <div style="font-size:10.5px;color:var(--muted);margin-bottom:3px;">SGD / USD</div>
          <div style="font-size:15px;font-weight:900;color:var(--navy);">${fmtSgdUsd}</div>
        </div>
        <div class="irow" style="margin:0">
          <div style="font-size:10.5px;color:var(--muted);margin-bottom:3px;">SGD / JPY</div>
          <div style="font-size:15px;font-weight:900;color:var(--navy);">${fmtSgdJpy}</div>
        </div>
        <div class="irow" style="margin:0">
          <div style="font-size:10.5px;color:var(--muted);margin-bottom:3px;">SGD / CNY</div>
          <div style="font-size:15px;font-weight:900;color:var(--navy);">${fmtSgdCny}</div>
        </div>
      `;
    }

    // 출처 + 조회 시각
    const srcEl = document.getElementById('exchange-source');
    if (srcEl) {
      const now = new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
      const fallbackNote = data.ok ? '' : ' · 폴백값';
      srcEl.textContent = `조회: ${now}${fallbackNote}`;
    }
  } catch (e) {
    const srcEl = document.getElementById('exchange-source');
    if (srcEl) srcEl.textContent = '환율 조회 실패 — 잠시 후 다시 시도해 주세요';
    console.warn('환율 로드 실패:', e);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '↺ 환율 새로고침'; }
  }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §4. To-Do 리스트 (N3)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

const TODO_FIXED_IDS = ['p1', 'rep', 'p2', 'p3'];
const TODO_LS_KEY    = 'sg_upharma_todos_v1';
let _lastTodoAddAt   = 0;

/** localStorage에서 todo 상태 읽기 */
function _loadTodoState() {
  try   { return JSON.parse(localStorage.getItem(TODO_LS_KEY) || '{}'); }
  catch { return {}; }
}

/** localStorage에 todo 상태 쓰기 */
function _saveTodoState(state) {
  localStorage.setItem(TODO_LS_KEY, JSON.stringify(state));
}

/** 페이지 로드 시 localStorage 상태 복원 */
function initTodo() {
  const state = _loadTodoState();

  // 고정 항목 상태 복원
  for (const id of TODO_FIXED_IDS) {
    const item = document.getElementById('todo-' + id);
    if (!item) continue;
    item.classList.toggle('done', !!state['fixed_' + id]);
  }

  // 커스텀 항목 렌더
  _renderCustomTodos(state);
}

/**
 * 고정 항목 수동 토글 (클릭 시 호출).
 * @param {string} id  'p1' | 'rep' | 'p2' | 'p3'
 */
function toggleTodo(id) {
  const state       = _loadTodoState();
  const key         = 'fixed_' + id;
  state[key]        = !state[key];
  _saveTodoState(state);

  const item = document.getElementById('todo-' + id);
  if (item) item.classList.toggle('done', state[key]);
}

/**
 * 자동 체크: 파이프라인·보고서 완료 시 호출 (N3).
 * @param {'p1'|'rep'} id
 */
function markTodoDone(id) {
  const state       = _loadTodoState();
  state['fixed_' + id] = true;
  _saveTodoState(state);

  const item = document.getElementById('todo-' + id);
  if (item) item.classList.add('done');
}

/** 사용자가 직접 항목 추가 */
function addTodoItem(evt) {
  if (evt) {
    if (evt.isComposing || evt.repeat) return;
    evt.preventDefault();
  }

  const now = Date.now();
  if (now - _lastTodoAddAt < 250) return;
  _lastTodoAddAt = now;

  const input = document.getElementById('todo-input');
  const text  = input ? input.value.trim() : '';
  if (!text) return;

  const state   = _loadTodoState();
  const customs = state.customs || [];
  customs.push({ id: now + Math.floor(Math.random() * 1000), text, done: false });
  state.customs = customs;
  _saveTodoState(state);
  _renderCustomTodos(state);
  if (input) input.value = '';
}

/** 커스텀 항목 토글 */
function toggleCustomTodo(id) {
  const state   = _loadTodoState();
  const customs = state.customs || [];
  const item    = customs.find(c => c.id === id);
  if (item) item.done = !item.done;
  state.customs = customs;
  _saveTodoState(state);
  _renderCustomTodos(state);
}

/** 커스텀 항목 삭제 */
function deleteCustomTodo(id) {
  const state   = _loadTodoState();
  state.customs = (state.customs || []).filter(c => c.id !== id);
  _saveTodoState(state);
  _renderCustomTodos(state);
}

/** 커스텀 항목 목록 DOM 갱신 */
function _renderCustomTodos(state) {
  const container = document.getElementById('todo-custom-list');
  if (!container) return;
  container.classList.add('todo-list');

  const customs = state.customs || [];
  if (!customs.length) { container.innerHTML = ''; return; }

  container.innerHTML = customs.map(c => `
    <div class="todo-item${c.done ? ' done' : ''}" onclick="toggleCustomTodo(${c.id})">
      <div class="todo-check"></div>
      <span class="todo-label">${_escHtml(c.text)}</span>
      <button
        onclick="event.stopPropagation();deleteCustomTodo(${c.id})"
        style="background:none;color:var(--muted);font-size:16px;cursor:pointer;
               border:none;outline:none;padding:0 4px;line-height:1;flex-shrink:0;"
        title="삭제"
      >×</button>
    </div>
  `).join('');
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §5. 보고서 탭 관리 (N4)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

const REPORTS_LS_KEY = 'sg_upharma_reports_v1';

function _loadReports() {
  try   { return JSON.parse(localStorage.getItem(REPORTS_LS_KEY) || '[]'); }
  catch { return []; }
}

/**
 * 1공정 완료 후 renderResult()가 호출 → 보고서 탭에 항목 추가.
 * @param {object|null} result  분석 결과
 * @param {string|null} pdfName PDF 파일명
 */
function _addReportEntry(result, pdfName) {
  const reports = _loadReports();
  const productName = result ? (result.trade_name || result.product_id || '알 수 없음') : '알 수 없음';
  const entry   = {
    id:        Date.now(),
    product:   productName,
    stage_label: '1공정',
    report_title: `1공정 보고서 - ${productName}`,
    inn:       result ? (INN_MAP[result.product_id] || result.inn || '') : '',
    verdict:   result ? (result.verdict || '—') : '—',
    price_hint: result ? String(result.price_positioning_pbs || '').trim() : '',
    basis_trade: result ? String(result.basis_trade || '').trim() : '',
    risks_conditions: result ? String(result.risks_conditions || '').trim() : '',
    timestamp: new Date().toLocaleString('ko-KR', {
      month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    }),
    hasPdf: !!pdfName,
    pdf_name: pdfName || '',
  };

  reports.unshift(entry);
  localStorage.setItem(REPORTS_LS_KEY, JSON.stringify(reports.slice(0, 30)));
  renderReportTab();
  _syncP2ReportsOptions();
}

function clearAllReports() {
  localStorage.setItem(REPORTS_LS_KEY, JSON.stringify([]));
  renderReportTab();
  _syncP2ReportsOptions();
}

function deleteReportEntry(id) {
  const reports = _loadReports().filter(r => r.id !== id);
  localStorage.setItem(REPORTS_LS_KEY, JSON.stringify(reports));
  renderReportTab();
  _syncP2ReportsOptions();
}

/** 보고서 탭 DOM 갱신 */
function renderReportTab() {
  const container = document.getElementById('report-tab-list');
  if (!container) return;

  const reports = _loadReports();
  if (!reports.length) {
    container.innerHTML = `
      <div class="rep-empty">
        아직 생성된 보고서가 없습니다.<br>
        1공정 분석을 실행하면 여기에 자동으로 등록됩니다.
      </div>`;
    return;
  }

  container.innerHTML = reports.map(r => {
    const vc = r.verdict === '적합'   ? 'green'
             : r.verdict === '부적합' ? 'red'
             : r.verdict !== '—'      ? 'orange'
             :                          'gray';
    const innSpan = r.inn
      ? ` <span style="font-weight:400;color:var(--muted);font-size:12px;">· ${_escHtml(r.inn)}</span>`
      : '';
    const dlBtn = r.hasPdf
      ? `<a class="btn-download"
            href="/api/report/download${r.pdf_name ? `?name=${encodeURIComponent(r.pdf_name)}` : ''}"
            target="_blank"
            style="padding:7px 14px;font-size:12px;flex-shrink:0;">📄 PDF</a>`
      : '';
    const delBtn = `<button class="btn-report-del" onclick="deleteReportEntry(${r.id})" title="보고서 삭제">×</button>`;

    return `
      <div class="rep-item">
        <div class="rep-item-info">
          <div class="rep-item-product">${_escHtml(r.report_title || r.product)}${innSpan}</div>
          <div class="rep-item-meta">${_escHtml(r.timestamp)}</div>
        </div>
        <div class="rep-item-verdict">
          <span class="bdg ${vc}">${_escHtml(r.verdict)}</span>
        </div>
        ${dlBtn}
        ${delBtn}
      </div>`;
  }).join('');
  _syncP2ReportsOptions();
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §6. 2공정 수출전략 (P2)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

let _p2Ready           = false;
let _p2Mode            = 'manual';
let _p2ManualSeg       = 'public';
let _p2AiSeg           = 'public';
let _p2SelectedReportId = '';
let _p2Manual          = _makeP2Defaults();
let _p2LastScenarios   = null; // PDF 생성용 마지막 산정 결과

/* ── 기본값 데이터 구조 ─────────────────────────────────────────────────── */

function _makeP2Defaults() {
  return {
    public: [
      {
        key: 'base_price', label: '기준 입찰가', value: 0,
        type: 'abs_input', unit: 'SGD', step: 0.5, min: 0, max: 99999,
        enabled: true, fixed: false, expanded: false,
        hint: '경쟁사 E-catalogue 입찰가 (USD 또는 SGD 직접 입력)',
        rationale: 'ALPS 공개 입찰가 기준. 27개 공공기관 통합 구매력이 강력한 하방압력을 형성합니다.',
      },
      {
        key: 'exchange', label: '환율 (USD→SGD)', value: 1.0,
        type: 'abs_input', unit: 'rate', step: 0.0001, min: 0.0001, max: 99,
        enabled: true, fixed: false, expanded: false,
        hint: '입찰가가 USD인 경우 적용. SGD 직접 입력 시 1.0 유지',
        rationale: 'USD 수출가와 SGD 현지가 혼용 방지. /api/exchange에서 실시간 로드됩니다.',
      },
      {
        key: 'pub_ratio', label: '공공 수출가 산출 비율', value: 30,
        type: 'pct_mult', unit: '%', step: 1, min: 20, max: 40,
        enabled: true, fixed: false, expanded: false,
        hint: '기본값 30% | 범위 20~40%',
        rationale: '수입비용(40~50%)+유통비용(15~20%)+파트너마진(20~30%)을 일괄 내포. 제네릭 경쟁 심화 시 20% 권장.',
      },
    ],
    private: [
      {
        key: 'base_het', label: '경쟁사 HET / HNA', value: 0,
        type: 'abs_input', unit: 'SGD', step: 0.5, min: 0, max: 99999,
        enabled: true, fixed: false, expanded: false,
        hint: 'HET(소비자 최종가) 또는 HNA(병원·약국 입고가) 중 선택 입력',
        rationale: 'Guardian·Watsons·Unity 체인 소매가 기준. HNA 직접 입력 시 GST·소매마진 역산 불필요.',
      },
      {
        key: 'exchange', label: '환율 (USD→SGD)', value: 1.0,
        type: 'abs_input', unit: 'rate', step: 0.0001, min: 0.0001, max: 99,
        enabled: true, fixed: false, expanded: false,
        hint: '입력가가 USD인 경우 적용. SGD 직접 입력 시 1.0 유지',
        rationale: '공공 시장과 동일. USD/SGD 혼용 방지 필수.',
      },
      {
        key: 'gst', label: 'GST 공제 (÷1.09)', value: 9,
        type: 'gst_fixed', unit: '%', step: 0, min: 9, max: 9,
        enabled: true, fixed: true, expanded: false,
        hint: '싱가포르 GST 9% 고정 (2024년 1월 확정)',
        rationale: '소비자가에 이미 포함된 세금 역산 분리. ×0.91이 아닌 ÷1.09 처리 필수.',
      },
      {
        key: 'retail', label: '소매 마진율', value: 40,
        type: 'pct_deduct', unit: '%', step: 1, min: 20, max: 50,
        enabled: true, fixed: false, expanded: false,
        hint: 'OTC(일반의약품) 권장 50% | Rx(전문의약품) 권장 30% | 기본값 40%',
        rationale: '싱가포르 아시아권 소매마진 상한 50%. Guardian·Watsons OTC 수익 의존도, 피부과·웰니스 50% 적용.',
      },
      {
        key: 'partner', label: '파트너사 마진', value: 20,
        type: 'pct_deduct', unit: '%', step: 1, min: 15, max: 30,
        enabled: true, fixed: false, expanded: false,
        hint: '기본값 20% | 서구권 제조 시 25% 상향 | 범위 15~30%',
        rationale: 'HSA 라이선스 비용($1,000~$17,500) 전가분 + 현지 에이전트 수수료. 서구권 원가 프리미엄 반영.',
      },
      {
        key: 'distribution', label: '도매/물류 유통 마진', value: 15,
        type: 'pct_deduct', unit: '%', step: 1, min: 0, max: 25,
        enabled: true, fixed: false, expanded: false,
        hint: '상온 보관: 15% | 콜드체인(바이오·백신·항암): 25%',
        rationale: '도매 순마진 6.5% + 물류 오버헤드. HSA GDP 기준 냉장물류 프리미엄. 바이오허브 특성상 고비용.',
      },
    ],
  };
}

/* ── 초기화 ─────────────────────────────────────────────────────────────── */

function initP2Strategy() {
  const select = document.getElementById('p2-report-select');
  if (!select) return;
  _p2Ready = true;

  document.getElementById('p2-mode-manual')?.addEventListener('click', () => _setP2Mode('manual'));
  document.getElementById('p2-mode-ai')?.addEventListener('click',    () => _setP2Mode('ai'));
  document.getElementById('p2-ai-run')?.addEventListener('click',     _runP2AiAnalysis);

  select.addEventListener('change', (e) => {
    _p2SelectedReportId = e.target.value || '';
    _renderP2ReportBrief();
    _p2FillBaseFromReport();
    _renderP2Manual();
  });

  document.querySelectorAll('[data-p2-manual-seg]').forEach((btn) => {
    btn.addEventListener('click', () => {
      _p2ManualSeg = btn.getAttribute('data-p2-manual-seg') || 'public';
      document.querySelectorAll('[data-p2-manual-seg]').forEach(x => x.classList.remove('on'));
      btn.classList.add('on');
      _renderP2Manual();
    });
  });

  document.querySelectorAll('[data-p2-ai-seg]').forEach((btn) => {
    btn.addEventListener('click', () => {
      _p2AiSeg = btn.getAttribute('data-p2-ai-seg') || 'public';
      document.querySelectorAll('[data-p2-ai-seg]').forEach(x => x.classList.remove('on'));
      btn.classList.add('on');
    });
  });

  _p2FillExchangeRate();
  _syncP2ReportsOptions();
  _setP2Mode('manual');
  _renderP2Manual();
}

/* ── 환율 자동 채움 ──────────────────────────────────────────────────────── */

function _p2FillExchangeRate() {
  const rates = window._exchangeRates;
  if (!rates) return;
  // sgd_usd = 1 SGD → ? USD  →  1 USD = 1/sgd_usd SGD
  const sgdUsd = Number(rates.sgd_usd);
  if (!sgdUsd || sgdUsd <= 0) return;
  const usdToSgd = Number((1 / sgdUsd).toFixed(4));
  for (const seg of ['public', 'private']) {
    const opt = _p2Manual[seg].find(x => x.key === 'exchange');
    if (opt) opt.value = usdToSgd;
  }
}

function _p2FillBaseFromReport() {
  const report = _getP2SelectedReport();
  if (!report) return;
  const hint = _extractSgdHint(report.price_hint || report.price_positioning_pbs || '');
  if (!Number.isNaN(hint) && hint > 0) {
    const pub  = _p2Manual.public.find(x => x.key === 'base_price');
    const priv = _p2Manual.private.find(x => x.key === 'base_het');
    if (pub)  pub.value  = hint;
    if (priv) priv.value = hint;
  }
}

/* ── 보고서 관련 ─────────────────────────────────────────────────────────── */

function _syncP2ReportsOptions() {
  if (!_p2Ready) return;
  const select = document.getElementById('p2-report-select');
  if (select) {
    const reports = _loadReports();
    const current = _p2SelectedReportId;

    const options = ['<option value="">보고서를 선택하세요</option>']
      .concat(reports.map(r => (
        `<option value="${r.id}">${_escHtml(r.report_title || r.product || '보고서')}</option>`
      )));
    select.innerHTML = options.join('');

    const hasCurrent = reports.some(r => String(r.id) === String(current));
    _p2SelectedReportId = hasCurrent ? current : '';
    select.value = _p2SelectedReportId;
    _renderP2ReportBrief();
  }
}

function _getP2SelectedReport() {
  if (!_p2SelectedReportId) return null;
  const reports = _loadReports();
  return reports.find(r => String(r.id) === String(_p2SelectedReportId)) || null;
}

function _extractSgdHint(text) {
  const src = String(text || '');
  const mRange = src.match(/SGD\s*([0-9]+(?:\.[0-9]+)?)\s*[~\-–]\s*([0-9]+(?:\.[0-9]+)?)/i);
  if (mRange) return (Number(mRange[1]) + Number(mRange[2])) / 2;
  const mSingle = src.match(/SGD\s*([0-9]+(?:\.[0-9]+)?)/i);
  if (mSingle) return Number(mSingle[1]);
  return NaN;
}

function _renderP2ReportBrief() {
  const el = document.getElementById('p2-report-brief');
  if (!el) return;
  const report = _getP2SelectedReport();
  if (!report) {
    el.innerHTML = '<p class="p2-brief-empty">보고서를 선택하면 가격 정보가 표시됩니다.</p>';
    return;
  }
  const vc = report.verdict === '적합'   ? 'green'
           : report.verdict === '부적합' ? 'red'
           : report.verdict !== '—'      ? 'orange'
           :                               'gray';
  const priceHint  = String(report.price_hint || '').trim() || '파악된 가격 없음';
  const pricePbs   = String(report.price_positioning_pbs || '').trim();
  const basisTrade = String(report.basis_trade || '').trim() || '무역 근거 없음';

  el.innerHTML = `
    <div class="p2-brief-badge-row">
      <span class="bdg ${vc}">${_escHtml(report.verdict || '—')}</span>
      <span class="p2-brief-product">${_escHtml(report.report_title || report.product || '')}</span>
    </div>
    <div class="p2-brief-grid">
      <div class="p2-brief-item">
        <div class="basis-label">참고 가격 (PBS/SGD)</div>
        <div class="basis-value">${_escHtml(priceHint)}</div>
      </div>
      ${pricePbs ? `
      <div class="p2-brief-item">
        <div class="basis-label">가격 포지셔닝 전략</div>
        <div class="basis-value">${_escHtml(pricePbs)}</div>
      </div>` : ''}
      <div class="p2-brief-item p2-brief-item--wide">
        <div class="basis-label">무역 조건 (관세·FTA)</div>
        <div class="basis-value">${_escHtml(basisTrade)}</div>
      </div>
    </div>`;
}

/* ── 모드 전환 ───────────────────────────────────────────────────────────── */

function _setP2Mode(mode) {
  _p2Mode = mode === 'ai' ? 'ai' : 'manual';
  document.getElementById('p2-manual-box')?.classList.toggle('on', _p2Mode === 'manual');
  document.getElementById('p2-ai-box')?.classList.toggle('on',    _p2Mode === 'ai');
  const manualBtn = document.getElementById('p2-mode-manual');
  const aiBtn     = document.getElementById('p2-mode-ai');
  if (manualBtn && aiBtn) {
    manualBtn.classList.toggle('p2-btn-alt', _p2Mode !== 'manual');
    aiBtn.classList.toggle('p2-btn-alt',    _p2Mode !== 'ai');
  }
}

/* ── 직접 입력 계산 엔진 ──────────────────────────────────────────────────── */

function _calcP2Manual() {
  const seg     = _p2ManualSeg;
  const options = _p2Manual[seg];
  const active  = options.filter(x => x.enabled);

  if (seg === 'public') {
    const baseOpt  = active.find(x => x.key === 'base_price');
    const exchOpt  = active.find(x => x.key === 'exchange');
    const ratioOpt = active.find(x => x.key === 'pub_ratio');

    let price = baseOpt ? Number(baseOpt.value) : 0;
    const parts = [`SGD ${price.toFixed(2)}`];

    if (exchOpt && Number(exchOpt.value) !== 1.0) {
      const r = Number(exchOpt.value);
      price *= r;
      parts.push(`× ${r.toFixed(4)} (환율)`);
    }
    const ratio = ratioOpt ? Number(ratioOpt.value) : 30;
    price *= ratio / 100;
    parts.push(`× ${ratio}%`);

    for (const opt of active) {
      if (opt.type === 'pct_add_custom') {
        price *= (1 + Number(opt.value) / 100);
        parts.push(`× (1+${Number(opt.value).toFixed(1)}%)`);
      } else if (opt.type === 'abs_add_custom') {
        price += Number(opt.value);
        parts.push(`+ SGD ${Number(opt.value).toFixed(2)}`);
      }
    }

    const kup = Math.max(0, price);
    return { kup, formulaStr: parts.join('  ') + `  =  KUP  SGD ${kup.toFixed(2)}` };

  } else {
    let price = 0;
    const parts = [];
    for (const opt of active) {
      if (opt.key === 'base_het' && opt.type === 'abs_input') {
        price = Number(opt.value);
        parts.push(`SGD ${price.toFixed(2)}`);
      } else if (opt.key === 'exchange' && opt.type === 'abs_input' && Number(opt.value) !== 1.0) {
        price *= Number(opt.value);
        parts.push(`× ${Number(opt.value).toFixed(4)} (환율)`);
      } else if (opt.type === 'gst_fixed') {
        price /= 1.09;
        parts.push(`÷ 1.09 (GST)`);
      } else if (opt.type === 'pct_deduct') {
        const m = Number(opt.value);
        price *= (1 - m / 100);
        parts.push(`× (1−${m}%)`);
      } else if (opt.type === 'pct_add_custom') {
        price *= (1 + Number(opt.value) / 100);
        parts.push(`× (1+${Number(opt.value).toFixed(1)}%)`);
      } else if (opt.type === 'abs_add_custom') {
        price += Number(opt.value);
        parts.push(`+ SGD ${Number(opt.value).toFixed(2)}`);
      }
    }
    const kup = Math.max(0, price);
    return { kup, formulaStr: (parts.join('  ') || 'SGD 0.00') + `  =  KUP  SGD ${kup.toFixed(2)}` };
  }
}

/* ── 직접 입력 렌더링 ────────────────────────────────────────────────────── */

function _renderP2Manual() {
  const wrapEl     = document.getElementById('p2-manual-options');
  const removedEl  = document.getElementById('p2-manual-removed');
  const scenarioEl = document.getElementById('p2-manual-scenarios');
  const formulaEl  = document.getElementById('p2-formula-preview');
  if (!wrapEl || !removedEl || !scenarioEl) return;

  const options = _p2Manual[_p2ManualSeg];
  const active  = options.filter(x => x.enabled);
  const inactive = options.filter(x => !x.enabled);

  wrapEl.innerHTML = active.map((opt, i) => _p2StepCardHtml(opt, i + 1)).join('');
  _bindP2StepEvents(wrapEl, options);

  removedEl.innerHTML = inactive.length ? `
    <span class="p2-removed-label">복원:</span>
    ${inactive.map(opt => `<button class="p2-add-btn" data-p2-op="add" data-key="${_escHtml(opt.key)}" type="button">+ ${_escHtml(opt.label)}</button>`).join('')}
  ` : '';
  removedEl.querySelectorAll('[data-p2-op="add"]').forEach(btn => {
    btn.addEventListener('click', () => {
      const item = options.find(x => x.key === btn.getAttribute('data-key'));
      if (item) { item.enabled = true; _renderP2Manual(); }
    });
  });

  _renderP2CustomAddSection();

  const { kup, formulaStr } = _calcP2Manual();
  if (formulaEl) formulaEl.textContent = formulaStr;

  const report     = _getP2SelectedReport();
  const agg        = kup * 0.88;
  const cons       = kup * 1.10;
  const aggReason  = _p2ManualScenarioReason('aggressive',   _p2ManualSeg, report);
  const avgReason  = _p2ManualScenarioReason('average',      _p2ManualSeg, report);
  const consReason = _p2ManualScenarioReason('conservative', _p2ManualSeg, report);

  scenarioEl.innerHTML = _p2ScenarioHtml(agg, kup, cons, aggReason, avgReason, consReason);

  _p2LastScenarios = {
    mode: 'manual', seg: _p2ManualSeg, base: kup,
    agg, avg: kup, cons, formulaStr,
    aggReason, avgReason, consReason, rationaleLines: [],
  };
  _renderP2PdfSection('manual');
}

/* ── Step 카드 HTML ──────────────────────────────────────────────────────── */

function _p2StepCardHtml(opt, stepNum) {
  const exp      = opt.expanded;
  const isInput  = opt.type === 'abs_input';
  const isFixed  = opt.type === 'gst_fixed';
  const canStep  = !isInput && !isFixed && opt.step > 0;
  const canDelete = !opt.fixed;

  let valDisplay = '';
  if (isFixed)                         valDisplay = '÷ 1.09 고정';
  else if (opt.type === 'pct_mult')    valDisplay = `× ${Number(opt.value).toFixed(0)}%`;
  else if (opt.type === 'pct_deduct')  valDisplay = `× (1−${Number(opt.value).toFixed(0)}%)`;
  else if (opt.type === 'pct_add_custom') valDisplay = `× (1+${Number(opt.value).toFixed(1)}%)`;
  else if (opt.unit === 'SGD')         valDisplay = `SGD ${Number(opt.value).toFixed(2)}`;
  else if (opt.unit === 'rate')        valDisplay = `× ${Number(opt.value).toFixed(4)}`;
  else                                 valDisplay = `+ SGD ${Number(opt.value).toFixed(2)}`;

  const inputVal = opt.unit === 'rate'
    ? Number(opt.value).toFixed(4)
    : Number(opt.value).toFixed(2);

  return `
    <div class="p2-step-card${exp ? ' open' : ''}">
      <div class="p2-step-header">
        <button class="p2-step-toggle" data-p2-op="toggle" data-key="${_escHtml(opt.key)}" type="button">
          <span class="p2-step-num">Step ${stepNum}</span>
          <span class="p2-step-label-text">${_escHtml(opt.label)}</span>
          <span class="p2-step-arrow">${exp ? '▾' : '▸'}</span>
        </button>
        <div class="p2-step-controls">
          ${isInput ? `
            <input class="p2-step-input" type="number"
              data-p2-op="input" data-key="${_escHtml(opt.key)}"
              value="${inputVal}" step="${opt.step}" min="${opt.min}" max="${opt.max}">
          ` : canStep ? `
            <span class="p2-step-val-display">${_escHtml(valDisplay)}</span>
            <button class="p2-step-btn" data-p2-op="dec" data-key="${_escHtml(opt.key)}" type="button">−</button>
            <button class="p2-step-btn" data-p2-op="inc" data-key="${_escHtml(opt.key)}" type="button">+</button>
          ` : `<span class="p2-step-val-display">${_escHtml(valDisplay)}</span>`}
          ${canDelete ? `<button class="p2-del-btn" data-p2-op="del" data-key="${_escHtml(opt.key)}" type="button" title="옵션 제거">×</button>` : ''}
        </div>
      </div>
      ${exp ? `
        <div class="p2-step-body">
          <div class="p2-step-hint">${_escHtml(opt.hint)}</div>
          <div class="p2-step-rationale">${_escHtml(opt.rationale)}</div>
          ${!isFixed ? `<div class="p2-step-range">범위: ${opt.min}${opt.unit === '%' ? '%' : opt.unit === 'SGD' ? ' SGD' : ''} ~ ${opt.max}${opt.unit === '%' ? '%' : opt.unit === 'SGD' ? ' SGD' : ''}</div>` : ''}
        </div>` : ''}
    </div>`;
}

function _bindP2StepEvents(wrap, options) {
  wrap.querySelectorAll('[data-p2-op]').forEach(el => {
    const op   = el.getAttribute('data-p2-op');
    const key  = el.getAttribute('data-key');
    const item = options.find(x => x.key === key);
    if (!item) return;

    if (op === 'toggle') {
      el.addEventListener('click', () => { item.expanded = !item.expanded; _renderP2Manual(); });
    } else if (op === 'del') {
      el.addEventListener('click', () => { item.enabled = false; item.expanded = false; _renderP2Manual(); });
    } else if (op === 'inc') {
      el.addEventListener('click', () => {
        item.value = Math.min(item.max, Number((Number(item.value) + item.step).toFixed(4)));
        _renderP2Manual();
      });
    } else if (op === 'dec') {
      el.addEventListener('click', () => {
        item.value = Math.max(item.min, Number((Number(item.value) - item.step).toFixed(4)));
        _renderP2Manual();
      });
    } else if (op === 'input') {
      el.addEventListener('change', () => {
        const v = parseFloat(el.value);
        if (!Number.isNaN(v)) item.value = Math.min(item.max, Math.max(item.min, v));
        _renderP2Manual();
      });
    }
  });
}

function _renderP2CustomAddSection() {
  const section = document.getElementById('p2-custom-add-section');
  if (!section) return;
  section.innerHTML = `
    <div class="p2-custom-add-row">
      <input class="p2-custom-input" id="p2c-label" type="text" placeholder="옵션명 (예: 관세 가산)" maxlength="30" style="flex:2">
      <select class="p2-custom-type-select" id="p2c-type">
        <option value="pct_deduct">% 차감</option>
        <option value="pct_add_custom">% 가산</option>
        <option value="abs_add_custom">SGD 가산</option>
      </select>
      <input class="p2-custom-input" id="p2c-val" type="number" placeholder="값" step="0.1" min="0" max="999" style="width:80px;flex:0 0 80px">
      <button class="p2-add-custom-btn" id="p2c-add" type="button">+ 추가</button>
    </div>`;
  document.getElementById('p2c-add')?.addEventListener('click', () => {
    const label = (document.getElementById('p2c-label')?.value || '').trim();
    const type  = document.getElementById('p2c-type')?.value || 'pct_deduct';
    const val   = parseFloat(document.getElementById('p2c-val')?.value || '0');
    if (!label || Number.isNaN(val) || val < 0) return;
    _p2Manual[_p2ManualSeg].push({
      key: `custom_${Date.now()}`, label, value: val, type,
      unit: type === 'abs_add_custom' ? 'SGD' : '%',
      step: type === 'abs_add_custom' ? 0.1 : 1, min: 0,
      max: type === 'abs_add_custom' ? 9999 : 100,
      enabled: true, fixed: false, expanded: false,
      hint: '사용자 추가 옵션', rationale: '',
    });
    _renderP2Manual();
  });
}

/* ── 시나리오 이유 서술 ──────────────────────────────────────────────────── */

function _p2ManualScenarioReason(type, seg, report) {
  const verdict  = report ? String(report.verdict || '—') : '—';
  const isPublic = seg === 'public';

  if (type === 'aggressive') {
    const parts = [];
    if (isPublic) {
      parts.push('ALPS E-catalogue 경쟁 구조에서 추가 입찰가 인하 여지를 최대한 반영한 시나리오입니다.');
      parts.push('제네릭 경쟁이 활성화된 품목일수록 낙찰을 위한 공격적 가격이 시장 진입에 유리합니다.');
    } else {
      parts.push('소매 체인 입점 초기 협상에서 낮은 진입가로 채널을 확보하는 전략입니다.');
      parts.push('Guardian·Watsons 등 주요 체인과의 초기 계약 시 경쟁 우위 선점이 가능합니다.');
    }
    if (verdict === '적합') parts.push(`1공정 '${verdict}' 판정으로 가격 경쟁력 확보 여력이 충분합니다.`);
    return parts.join(' ');
  }
  if (type === 'average') {
    const parts = ['현재 입력한 마진 구조를 그대로 반영한 기준 수출가입니다.'];
    if (isPublic) parts.push('ALPS 입찰 통과 가능 단가로, 수입비용·유통비용·파트너마진을 균형 있게 배분합니다.');
    else          parts.push('GST·소매·파트너·유통 마진을 모두 역산한 수출 기준가입니다. 시장 평균 진입 가격으로 권장됩니다.');
    return parts.join(' ');
  }
  if (type === 'conservative') {
    const parts = ['초기 진입 시 예상치 못한 비용에 대비한 안전 버퍼를 포함한 시나리오입니다.'];
    if (isPublic) parts.push('공공 입찰 재심사 또는 가격 재협상 시 하방 여유를 확보합니다. HSA 등록비·물류 지연·환율 변동 리스크를 반영합니다.');
    else          parts.push('민간 유통 채널 초기 정착 비용 및 반품·재고 리스크(HSA 등록 $1,000~$17,500 포함)를 가격에 흡수합니다.');
    if (verdict === '조건부') parts.push(`1공정 '${verdict}' 판정에 따른 조건 이행 비용(임상 데이터 보완 등)을 추가 반영합니다.`);
    return parts.join(' ');
  }
  return '';
}

/* ── 시나리오 HTML ────────────────────────────────────────────────────────── */

function _p2ScenarioHtml(agg, avg, cons, aggReason, avgReason, consReason) {
  const _card = (name, cls, price, reason) => `
    <div class="p2-scenario p2-scenario--${cls}">
      <div class="p2-scenario-top">
        <span class="p2-scenario-name">${_escHtml(name)}</span>
        <span class="p2-scenario-price">SGD ${Number(price).toFixed(2)}</span>
      </div>
      ${reason ? `<div class="p2-scenario-reason">${_escHtml(reason)}</div>` : ''}
    </div>`;
  return _card('공격적인 시나리오', 'agg',  agg,  aggReason)
       + _card('평균 시나리오',     'avg',  avg,  avgReason)
       + _card('보수 시나리오',     'cons', cons, consReason);
}

/* ── AI 분석 ─────────────────────────────────────────────────────────────── */

function _runP2AiAnalysis() {
  const report    = _getP2SelectedReport();
  const noteEl    = document.getElementById('p2-ai-note');
  const outEl     = document.getElementById('p2-ai-scenarios');
  const ratEl     = document.getElementById('p2-ai-rationale');
  const lblEl     = document.getElementById('p2-ai-scenario-label');
  if (!noteEl || !outEl) return;

  if (!report) {
    noteEl.textContent = '먼저 1공정 보고서를 선택해 주세요.';
    outEl.innerHTML = '';
    if (ratEl) ratEl.innerHTML = '';
    return;
  }

  const base          = _extractSgdHint(report.price_hint || report.price_positioning_pbs || '') || 0;
  const verdict       = String(report.verdict || '—');
  const verdictFactor = verdict === '적합' ? 1.04 : verdict === '조건부' ? 0.98 : 0.93;
  const segFactor     = _p2AiSeg === 'public' ? 0.95 : 1.06;
  const hasRisk       = String(report.risks_conditions || '').trim().length > 0;
  const riskFactor    = hasRisk ? 0.98 : 1.0;

  const avg  = Math.max(0, base * verdictFactor * segFactor * riskFactor);
  const agg  = avg * 0.90;
  const cons = avg * 1.12;

  const verdictCapacity = verdict === '적합' ? '높음' : verdict === '조건부' ? '보통' : '낮음';

  const rationaleLines = [
    `판정 계수 ×${verdictFactor.toFixed(2)} — 1공정 '${verdict}' 판정 기준으로 가격 경쟁력 여력이 '${verdictCapacity}'으로 평가됩니다.`,
    `시장 계수 ×${segFactor.toFixed(2)} — ${_p2AiSeg === 'public' ? 'ALPS 집중 구매 압력을 반영해 민간 대비 약 5% 낮게 산정합니다.' : '민간 채널의 높은 마진 허용 구조를 반영해 공공 대비 약 6% 높게 산정합니다.'}`,
    hasRisk
      ? `리스크 계수 ×${riskFactor.toFixed(2)} — 보고서 리스크 항목이 존재해 2% 안전마진을 추가했습니다.`
      : `리스크 계수 ×${riskFactor.toFixed(2)} — 보고서에 리스크 항목 없음, 추가 할인 불필요.`,
  ];

  if (ratEl) {
    ratEl.innerHTML = `
      <div class="p2-ai-rationale-block">
        <div class="selector-label" style="margin-bottom:6px;">AI 추론 근거</div>
        ${rationaleLines.map(l => `<div class="p2-ai-rationale-line">${_escHtml(l)}</div>`).join('')}
      </div>`;
  }

  const aggReason  = _p2AiScenarioReason('aggressive',   verdict, _p2AiSeg, hasRisk, verdictFactor, segFactor, riskFactor);
  const avgReason  = _p2AiScenarioReason('average',      verdict, _p2AiSeg, hasRisk, verdictFactor, segFactor, riskFactor);
  const consReason = _p2AiScenarioReason('conservative', verdict, _p2AiSeg, hasRisk, verdictFactor, segFactor, riskFactor);

  noteEl.textContent = `기준가 SGD ${Number(base).toFixed(2)} 기반 산정 완료.`;
  if (lblEl) lblEl.style.display = '';
  outEl.innerHTML = _p2ScenarioHtml(agg, avg, cons, aggReason, avgReason, consReason);

  _p2LastScenarios = {
    mode: 'ai', seg: _p2AiSeg, base, agg, avg, cons,
    formulaStr: `SGD ${Number(base).toFixed(2)} × ${verdictFactor.toFixed(2)} × ${segFactor.toFixed(2)} × ${riskFactor.toFixed(2)}  =  KUP  SGD ${Number(avg).toFixed(2)}`,
    aggReason, avgReason, consReason, rationaleLines,
  };
  _renderP2PdfSection('ai');
}

function _p2AiScenarioReason(type, verdict, seg, hasRisk, verdictFactor, segFactor, riskFactor) {
  const isPublic        = seg === 'public';
  const verdictCapacity = verdict === '적합' ? '높음' : verdict === '조건부' ? '보통' : '낮음';

  if (type === 'aggressive') {
    const parts = [`1공정 '${verdict}' 판정으로 가격 경쟁력 여력이 '${verdictCapacity}'입니다.`];
    if (isPublic) parts.push('공공 시장 ALPS 집중 구매 압력에 대응해 공격적 입찰가를 산정합니다. 초기 점유율 확보 우선 전략입니다.');
    else          parts.push('민간 채널 초기 점유율 확보를 위해 마진 일부를 희생한 저가 진입 전략입니다.');
    return parts.join(' ');
  }
  if (type === 'average') {
    const parts = [`판정 계수(×${verdictFactor.toFixed(2)}), 시장 계수(×${segFactor.toFixed(2)})를 순차 적용한 AI 기준가입니다.`];
    if (hasRisk) parts.push(`보고서 리스크 항목 반영으로 2% 안전마진(×${riskFactor.toFixed(2)})을 추가했습니다.`);
    else         parts.push('보고서에 리스크 항목이 없어 추가 할인 없이 기준가를 그대로 유지합니다.');
    return parts.join(' ');
  }
  if (type === 'conservative') {
    const parts = ['HSA 등록비($1,000~$17,500), 물류 지연, 환율 변동에 대비한 보수적 시나리오입니다.'];
    if (verdict === '조건부') parts.push(`'조건부' 판정에 따른 추가 조건 이행 비용(임상 데이터 보완 등)을 반영합니다.`);
    if (isPublic)             parts.push('공공 입찰 재심사 시 하방 여유를 확보합니다.');
    else                      parts.push('민간 채널 초기 정착 비용 및 반품·재고 리스크를 가격에 흡수합니다.');
    return parts.join(' ');
  }
  return '';
}

/* ── PDF 생성 ────────────────────────────────────────────────────────────── */

function _renderP2PdfSection(mode) {
  const el = document.getElementById(`p2-${mode}-pdf-section`);
  if (!el) return;
  el.innerHTML = `
    <div class="p2-pdf-bar">
      <span class="p2-pdf-label">2공정 수출가 시나리오 보고서</span>
      <button class="btn-analyze" id="p2-pdf-btn-${mode}" type="button" style="font-size:13px;padding:8px 18px;">📄 PDF 생성</button>
      <div id="p2-pdf-state-${mode}" class="p2-pdf-state"></div>
    </div>`;
  document.getElementById(`p2-pdf-btn-${mode}`)?.addEventListener('click', () => _generateP2Pdf(mode));
}

async function _generateP2Pdf(mode) {
  const btn     = document.getElementById(`p2-pdf-btn-${mode}`);
  const stateEl = document.getElementById(`p2-pdf-state-${mode}`);
  const sc      = _p2LastScenarios;
  if (!sc) {
    if (stateEl) stateEl.textContent = '먼저 시나리오를 산정해 주세요.';
    return;
  }
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 생성 중…'; }
  if (stateEl) stateEl.textContent = '';

  const report = _getP2SelectedReport();
  const body = {
    product_name: report ? (report.report_title || report.product || '제품명 미상') : '제품명 미상',
    verdict:      report ? (report.verdict || '—') : '—',
    seg_label:    sc.seg === 'public' ? '공공 시장' : '민간 시장',
    base_price:   sc.base,
    formula_str:  sc.formulaStr,
    mode_label:   mode === 'manual' ? '직접 입력' : 'AI 분석',
    scenarios: [
      { name: '공격적인 시나리오', price: sc.agg,  reason: sc.aggReason  },
      { name: '평균 시나리오',     price: sc.avg,  reason: sc.avgReason  },
      { name: '보수 시나리오',     price: sc.cons, reason: sc.consReason },
    ],
    ai_rationale: sc.rationaleLines || [],
  };

  try {
    const res = await fetch('/api/p2/report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data.pdf) throw new Error('PDF 파일명 없음');
    if (stateEl) stateEl.innerHTML = `
      <a class="btn-download"
         href="/api/report/download?name=${encodeURIComponent(data.pdf)}"
         target="_blank"
         style="font-size:12px;padding:6px 14px;">📄 다운로드</a>`;
  } catch (err) {
    if (stateEl) stateEl.textContent = `생성 실패: ${err.message}`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '📄 PDF 생성'; }
  }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §7. API 키 상태 (U1) — GET /api/keys/status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

async function loadKeyStatus() {
  try {
    const res  = await fetch('/api/keys/status');
    const data = await res.json();
    _applyKeyBadge('key-claude',     data.claude,     'Claude',     'API 키 설정됨',  'API 키 미설정 — 분석 불가');
    _applyKeyBadge('key-perplexity', data.perplexity, 'Perplexity', 'API 키 설정됨',  '미설정 — 논문 검색 생략');
  } catch (_) { /* 조용히 실패 */ }
}

function _applyKeyBadge(id, active, label, okTitle, ngTitle) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = 'key-badge ' + (active ? 'active' : 'inactive');
  el.title     = active ? `${label} ${okTitle}` : `${label} ${ngTitle}`;
  const dot    = el.querySelector('.key-badge-dot');
  if (dot) dot.style.background = active ? 'var(--green)' : 'var(--muted)';
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §7. 진행 단계 표시 (B2)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/**
 * @param {string} currentStep  STEP_ORDER 내 현재 단계
 * @param {'running'|'done'|'error'} status
 */
function setProgress(currentStep, status) {
  const row = document.getElementById('progress-row');
  if (row) row.classList.add('visible');
  const idx = STEP_ORDER.indexOf(currentStep);

  for (let i = 0; i < STEP_ORDER.length; i++) {
    const el  = document.getElementById('prog-' + STEP_ORDER[i]);
    if (!el) continue;
    const dot = el.querySelector('.prog-dot');

    if (status === 'error' && i === idx) {
      el.className    = 'prog-step error';
      dot.textContent = '✕';
    } else if (i < idx || (i === idx && status === 'done')) {
      el.className    = 'prog-step done';
      dot.textContent = '✓';
    } else if (i === idx) {
      el.className    = 'prog-step active';
      dot.textContent = i + 1;
    } else {
      el.className    = 'prog-step';
      dot.textContent = i + 1;
    }
  }
}

function resetProgress() {
  const row = document.getElementById('progress-row');
  if (row) row.classList.remove('visible');
  for (let i = 0; i < STEP_ORDER.length; i++) {
    const el = document.getElementById('prog-' + STEP_ORDER[i]);
    if (!el) continue;
    el.className = 'prog-step';
    el.querySelector('.prog-dot').textContent = i + 1;
  }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §8. 파이프라인 실행 & 폴링
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/**
 * 선택 품목 파이프라인 실행.
 * U6: 재분석 버튼도 이 함수를 호출.
 */
async function runPipeline() {
  const productKey = document.getElementById('product-select').value;
  _currentKey      = productKey;

  // UI 초기화
  resetProgress();
  document.getElementById('result-card').classList.remove('visible');
  document.getElementById('papers-card').classList.remove('visible');
  document.getElementById('report-card').classList.remove('visible');
  document.getElementById('btn-analyze').disabled = true;
  document.getElementById('btn-icon').textContent  = '⏳';

  const reBtn = document.getElementById('btn-reanalyze');
  if (reBtn) reBtn.style.display = 'none';

  // B2: db_load 단계 먼저 활성화
  setProgress('db_load', 'running');

  try {
    const res = await fetch(`/api/pipeline/${encodeURIComponent(productKey)}`, { method: 'POST' });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      console.error('파이프라인 오류:', d.detail || res.status);
      setProgress('db_load', 'error');
      _resetBtn();
      return;
    }
    _pollTimer = setInterval(() => pollPipeline(productKey), 2500);
  } catch (e) {
    console.error('요청 실패:', e);
    setProgress('db_load', 'error');
    _resetBtn();
  }
}

function _resetBtn() {
  document.getElementById('btn-analyze').disabled = false;
  document.getElementById('btn-icon').textContent  = '▶';
}

/**
 * GET /api/pipeline/{product_key}/status 를 주기적으로 폴링.
 * 서버 step: init → db_load → analyze → refs → report → done
 */
async function pollPipeline(productKey) {
  try {
    const res = await fetch(`/api/pipeline/${encodeURIComponent(productKey)}/status`);
    const d   = await res.json();

    if (d.status === 'idle') return;

    // B2: 서버 step → 프론트 STEP_ORDER 매핑
    if      (d.step === 'db_load')  { setProgress('db_load',  'running'); }
    else if (d.step === 'analyze')  { setProgress('db_load',  'done'); setProgress('analyze', 'running'); }
    else if (d.step === 'refs')     { setProgress('analyze',  'done'); setProgress('refs',    'running'); }
    else if (d.step === 'report')   {
      setProgress('refs', 'done'); setProgress('report', 'running');
      _showReportLoading();
    }

    if (d.status === 'done') {
      clearInterval(_pollTimer);
      for (const s of STEP_ORDER) setProgress(s, 'done');
      const r2   = await fetch(`/api/pipeline/${encodeURIComponent(productKey)}/result`);
      const data = await r2.json();
      renderResult(data.result, data.refs, data.pdf);
      _resetBtn();
    }

    if (d.status === 'error') {
      clearInterval(_pollTimer);
      setProgress(STEP_ORDER.includes(d.step) ? d.step : 'analyze', 'error');
      _resetBtn();
    }
  } catch (_) { /* 조용히 재시도 */ }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §9. 신약 분석 파이프라인
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

let _customPollTimer = null;
const CUSTOM_STEP_ORDER = ['analyze', 'refs', 'report'];

function _setCustomProgress(step, status) {
  const row = document.getElementById('custom-progress-row');
  if (row) row.classList.add('visible');
  const idMap = { analyze: 'cprog-analyze', refs: 'cprog-refs', report: 'cprog-report' };
  const idx   = CUSTOM_STEP_ORDER.indexOf(step);

  CUSTOM_STEP_ORDER.forEach((s, i) => {
    const el  = document.getElementById(idMap[s]);
    if (!el) return;
    const dot = el.querySelector('.prog-dot');
    if (status === 'error' && i === idx) {
      el.className = 'prog-step error'; dot.textContent = '✕';
    } else if (i < idx || (i === idx && status === 'done')) {
      el.className = 'prog-step done';  dot.textContent = '✓';
    } else if (i === idx) {
      el.className = 'prog-step active'; dot.textContent = i + 1;
    } else {
      el.className = 'prog-step'; dot.textContent = i + 1;
    }
  });
}

function _resetCustomProgress() {
  const row = document.getElementById('custom-progress-row');
  if (row) row.classList.remove('visible');
  CUSTOM_STEP_ORDER.forEach((s, i) => {
    const el = document.getElementById('cprog-' + s);
    if (!el) return;
    el.className = 'prog-step';
    el.querySelector('.prog-dot').textContent = i + 1;
  });
}

function _resetCustomBtn() {
  document.getElementById('btn-custom').disabled = false;
  document.getElementById('custom-icon').textContent = '▶';
}

async function runCustomPipeline() {
  const tradeName = document.getElementById('custom-trade-name').value.trim();
  const inn       = document.getElementById('custom-inn').value.trim();
  const dosage    = document.getElementById('custom-dosage').value.trim();
  if (!tradeName || !inn) { alert('약품명과 성분명을 입력하세요.'); return; }

  _resetCustomProgress();
  document.getElementById('result-card').classList.remove('visible');
  document.getElementById('papers-card').classList.remove('visible');
  document.getElementById('report-card').classList.remove('visible');
  document.getElementById('btn-custom').disabled = true;
  document.getElementById('custom-icon').textContent = '⏳';

  if (_customPollTimer) clearInterval(_customPollTimer);
  _setCustomProgress('analyze', 'running');

  try {
    const res = await fetch('/api/pipeline/custom', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ trade_name: tradeName, inn, dosage_form: dosage }),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      console.error('신약 분석 오류:', d.detail || res.status);
      _setCustomProgress('analyze', 'error');
      _resetCustomBtn();
      return;
    }
    _customPollTimer = setInterval(_pollCustomPipeline, 2500);
  } catch (e) {
    console.error('요청 실패:', e);
    _setCustomProgress('analyze', 'error');
    _resetCustomBtn();
  }
}

async function _pollCustomPipeline() {
  try {
    const res = await fetch('/api/pipeline/custom/status');
    const d   = await res.json();
    if (d.status === 'idle') return;

    if      (d.step === 'analyze') { _setCustomProgress('analyze', 'running'); }
    else if (d.step === 'refs')    { _setCustomProgress('analyze', 'done'); _setCustomProgress('refs', 'running'); }
    else if (d.step === 'report')  { _setCustomProgress('refs', 'done'); _setCustomProgress('report', 'running'); _showReportLoading(); }

    if (d.status === 'done') {
      clearInterval(_customPollTimer);
      for (const s of CUSTOM_STEP_ORDER) _setCustomProgress(s, 'done');
      const r2   = await fetch('/api/pipeline/custom/result');
      const data = await r2.json();
      renderResult(data.result, data.refs, data.pdf);
      _resetCustomBtn();
    }
    if (d.status === 'error') {
      clearInterval(_customPollTimer);
      _setCustomProgress(d.step || 'analyze', 'error');
      _resetCustomBtn();
    }
  } catch (_) { /* 조용히 재시도 */ }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §10. 결과 렌더링 (U2·U3·U4·U6·B4·N3·N4)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/**
 * 분석 완료 후 결과·논문·PDF 카드를 화면에 렌더링.
 * @param {object|null} result  분석 결과
 * @param {Array}       refs    Perplexity 논문 목록
 * @param {string|null} pdfName PDF 파일명
 */
function renderResult(result, refs, pdfName) {

  /* ─ 분석 결과 카드 ─ */
  if (result) {
    if (result.error) {
      document.getElementById('verdict-badge').className   = 'verdict-badge v-err';
      document.getElementById('verdict-badge').textContent = '분석 데이터 오류';
      document.getElementById('verdict-name').textContent  = result.trade_name || result.product_id || '';
      document.getElementById('verdict-inn').textContent   = INN_MAP[result.product_id] || result.inn || '';
      _setText('basis-market-medical', String(result.error || '데이터 오류'));
      _setText('basis-regulatory',     '품목 메타/DB 매핑 확인 필요');
      _setText('basis-trade',          '재실행 후 동일하면 서버 로그 점검');
      _setText('basis-pbs-line',       '참고 가격 정보 없음');
      const pathEl = document.getElementById('entry-pathway');
      if (pathEl) {
        pathEl.textContent = '진입 채널 권고 데이터 확인 필요';
        pathEl.style.display = 'block';
        pathEl.classList.add('empty');
      }
      _setText('price-positioning-pbs', '가격 포지셔닝 데이터를 불러오지 못했습니다.');
      _setText('risks-conditions', '분석 데이터 소스 확인 후 재시도해 주세요.');
      document.getElementById('result-card').classList.add('visible');
      _showReportError();
      return;
    }

    const verdict = result.verdict;
    const vc      = verdict === '적합'   ? 'v-ok'
                  : verdict === '부적합' ? 'v-err'
                  : verdict             ? 'v-warn'
                  :                       'v-none';
    const err    = result.analysis_error;
    const vLabel = verdict
      || (err === 'no_api_key'    ? 'API 키 미설정'
        : err === 'claude_failed' ? 'Claude 분석 실패'
        :                           '미분석');

    document.getElementById('verdict-badge').className   = `verdict-badge ${vc}`;
    document.getElementById('verdict-badge').textContent = vLabel;
    document.getElementById('verdict-name').textContent  = result.trade_name || result.product_id || '';
    document.getElementById('verdict-inn').textContent   = INN_MAP[result.product_id] || result.inn || '';

    // S2: 신호등
    ['tl-red', 'tl-yellow', 'tl-green'].forEach(id => {
      document.getElementById(id).classList.remove('on');
    });
    if (verdict === '적합')        document.getElementById('tl-green').classList.add('on');
    else if (verdict === '부적합') document.getElementById('tl-red').classList.add('on');
    else if (verdict)              document.getElementById('tl-yellow').classList.add('on');

    // S3: 판정 근거
    const basisFallback = _deriveBasisFromRationale(result.rationale);
    _setText('basis-market-medical', _formatDetailed(result.basis_market_medical || basisFallback.marketMedical));
    _setText('basis-regulatory',     _formatDetailed(result.basis_regulatory     || basisFallback.regulatory));
    _setText('basis-trade',          _formatDetailed(result.basis_trade          || basisFallback.trade));
    _setText('basis-pbs-line',       _pbsLineFromApi(result));

    // S4: 진입 채널
    const pathEl = document.getElementById('entry-pathway');
    if (pathEl) {
      const pathText = String(result.entry_pathway || '').trim();
      pathEl.textContent = pathText || '진입 채널 권고 데이터 확인 필요';
      pathEl.style.display = 'block';
      pathEl.classList.toggle('empty', !pathText);
    }

    const pbsPos = String(result.price_positioning_pbs || '').trim();
    _setText('price-positioning-pbs', _formatDetailed(pbsPos || _pbsLineFromApi(result)));

    const riskText = String(result.risks_conditions || '').trim()
      || (Array.isArray(result.key_factors) ? result.key_factors.join(' / ') : '');
    _setText('risks-conditions', _formatDetailed(riskText));

    // U6: 재분석 버튼 표시
    const reBtn = document.getElementById('btn-reanalyze');
    if (reBtn) reBtn.style.display = 'inline-flex';

    document.getElementById('result-card').classList.add('visible');

    // N3: 1공정 완료 → Todo 자동 체크
    markTodoDone('p1');
  }

  /* ─ B4: 논문 카드 ─ */
  const papersCard = document.getElementById('papers-card');
  const papersList = document.getElementById('papers-list');
  papersList.innerHTML = '';

  if (refs && refs.length > 0) {
    for (const ref of refs) {
      const item     = document.createElement('div');
      item.className = 'paper-item';
      const safeUrl  = /^https?:\/\//.test(ref.url || '') ? ref.url : '#';
      item.innerHTML = `
        <span class="paper-arrow">▸</span>
        <div>
          <div>
            <a class="paper-link" href="${safeUrl}" target="_blank" rel="noopener noreferrer"></a>
            <span class="paper-src"></span>
          </div>
          <div class="paper-reason"></div>
        </div>`;
      item.querySelector('.paper-link').textContent   = ref.title || ref.url || '';
      item.querySelector('.paper-src').textContent    = ref.source ? `[${ref.source}]` : '';
      item.querySelector('.paper-reason').textContent = ref.reason || '';
      papersList.appendChild(item);
    }
    papersCard.classList.add('visible');
  } else {
    papersCard.classList.remove('visible');
  }

  /* ─ U4: PDF 보고서 카드 ─ */
  if (pdfName) {
    _showReportOk(pdfName);
    // N3: 보고서 완료 → Todo 자동 체크
    markTodoDone('rep');
    // N4: 보고서 탭에 자동 등록
    _addReportEntry(result, pdfName);
  } else {
    _showReportError();
  }
}

/** U4: PDF 생성 중 */
function _showReportLoading() {
  const preview = document.getElementById('pdf-preview-frame');
  if (preview) preview.setAttribute('src', 'about:blank');
  document.getElementById('report-state-loading').style.display = 'flex';
  document.getElementById('report-state-ok').style.display      = 'none';
  document.getElementById('report-state-error').style.display   = 'none';
  document.getElementById('report-card').classList.add('visible');
}

/** U4: PDF 생성 완료 */
function _showReportOk(pdfName) {
  const dl = document.querySelector('#report-state-ok .btn-download');
  const baseQ = pdfName ? `name=${encodeURIComponent(pdfName)}` : '';
  const downloadUrl = `/api/report/download${baseQ ? `?${baseQ}` : ''}`;
  if (dl) dl.setAttribute('href', downloadUrl);
  const preview = document.getElementById('pdf-preview-frame');
  if (preview) {
    const previewUrl = `/api/report/download?${baseQ ? `${baseQ}&` : ''}inline=1`;
    preview.setAttribute('src', previewUrl);
  }
  document.getElementById('report-state-loading').style.display = 'none';
  document.getElementById('report-state-ok').style.display      = 'block';
  document.getElementById('report-state-error').style.display   = 'none';
  document.getElementById('report-card').classList.add('visible');
}

/** U4: PDF 생성 실패 */
function _showReportError() {
  const preview = document.getElementById('pdf-preview-frame');
  if (preview) preview.setAttribute('src', 'about:blank');
  document.getElementById('report-state-loading').style.display = 'none';
  document.getElementById('report-state-ok').style.display      = 'none';
  document.getElementById('report-state-error').style.display   = 'block';
  document.getElementById('report-card').classList.add('visible');
}

/* ─ 유틸 함수 ─ */

function _setText(id, value, fallback = '—') {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = String(value || '').trim() || fallback;
}

function _deriveBasisFromRationale(rationale) {
  const text  = String(rationale || '');
  const lines = text.split('\n').map(x => x.trim()).filter(Boolean);
  const out   = { marketMedical: '', regulatory: '', trade: '' };
  for (const line of lines) {
    const low = line.toLowerCase();
    if (!out.marketMedical && (low.includes('시장') || low.includes('의료'))) {
      out.marketMedical = line.replace(/^[\-\d\.\)\s]+/, ''); continue;
    }
    if (!out.regulatory && low.includes('규제')) {
      out.regulatory = line.replace(/^[\-\d\.\)\s]+/, ''); continue;
    }
    if (!out.trade && low.includes('무역')) {
      out.trade = line.replace(/^[\-\d\.\)\s]+/, ''); continue;
    }
  }
  if (!out.marketMedical && lines.length > 0) out.marketMedical = lines[0];
  if (!out.regulatory    && lines.length > 1) out.regulatory    = lines[1];
  if (!out.trade         && lines.length > 2) out.trade         = lines[2];
  return out;
}

function _formatDetailed(text) {
  const src = String(text || '').trim();
  if (!src) return '';
  const lines   = src.split('\n').map(x => x.trim()).filter(Boolean);
  const cleaned = lines.map(l =>
    l.replace(/^[\-\•\*\·]\s+/, '').replace(/^\d+[\.\)]\s+/, '')
  );
  let joined = '';
  for (const part of cleaned) {
    if (!joined) { joined = part; continue; }
    const prev = joined.trimEnd();
    const ends = prev.endsWith('.') || prev.endsWith('!') || prev.endsWith('?')
              || prev.endsWith('다') || prev.endsWith('음') || prev.endsWith('임');
    joined += ends ? ' ' + part : ', ' + part;
  }
  return joined;
}

function _pbsLineFromApi(result) {
  const aud    = result.pbs_dpmq_aud;
  const sgd    = result.pbs_dpmq_sgd_hint;
  const audNum = aud != null && aud !== '' ? Number(aud) : NaN;
  if (!Number.isNaN(audNum)) {
    const sNum = sgd != null && sgd !== '' ? Number(sgd) : NaN;
    let t = `DPMQ AUD ${audNum.toFixed(2)}`;
    if (!Number.isNaN(sNum)) t += `, 참고 SGD ${sNum.toFixed(2)}`;
    return t;
  }
  const haiku = String(result.pbs_haiku_estimate || '').trim();
  if (haiku) return haiku;
  return '참고 가격 정보 없음';
}

/** XSS 방지 HTML 이스케이프 */
function _escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §11. 시장 신호 · 뉴스 (Perplexity)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

async function loadNews() {
  const listEl = document.getElementById('news-list');
  const btn    = document.getElementById('btn-news-refresh');
  if (!listEl) return;

  if (btn) btn.disabled = true;
  listEl.innerHTML = '<div class="irow" style="color:var(--muted);font-size:12px;text-align:center;padding:20px 0;">뉴스 로드 중…</div>';

  try {
    const res  = await fetch('/api/news');
    const data = await res.json();

    if (!data.ok || !data.items?.length) {
      listEl.innerHTML = `<div class="irow" style="color:var(--muted);font-size:12px;text-align:center;padding:16px 0;">${data.error || '뉴스를 불러올 수 없습니다.'}</div>`;
      return;
    }

    listEl.innerHTML = data.items.map(item => {
      const href   = item.link ? `href="${_escHtml(item.link)}" target="_blank" rel="noopener"` : '';
      const tag    = item.link ? 'a' : 'div';
      const source = [item.source, item.date].filter(Boolean).join(' · ');
      return `
        <${tag} class="irow news-item" ${href} style="${item.link ? 'text-decoration:none;display:block;' : ''}">
          <div class="tit">${_escHtml(item.title)}</div>
          ${source ? `<div class="sub">${_escHtml(source)}</div>` : ''}
        </${tag}>`;
    }).join('');
  } catch (e) {
    listEl.innerHTML = '<div class="irow" style="color:var(--muted);font-size:12px;text-align:center;padding:16px 0;">뉴스 조회 실패 — 잠시 후 다시 시도해 주세요</div>';
    console.warn('뉴스 로드 실패:', e);
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §12. 초기화
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

loadKeyStatus();        // API 키 배지
loadExchange();         // 환율 즉시 로드
initTodo();             // Todo 상태 복원
renderReportTab();      // 보고서 탭 초기 렌더
initP2Strategy();       // 2공정 수출전략 수동 입력 초기화
loadNews();             // 시장 뉴스 즉시 로드
