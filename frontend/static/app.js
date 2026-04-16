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

let _p2Ready = false;
let _p2Mode = 'manual';
let _p2ManualSeg = 'public';
let _p2AiSeg = 'public';
let _p2SelectedReportId = '';
let _p2Manual = _makeP2Defaults();

function _makeP2Defaults() {
  return {
    public: [
      { key: 'public_adj',   label: '공공 시장 조정률', value: 0,  step: 1, type: 'pct', min: -40, max: 80, enabled: true },
      { key: 'logistics',    label: '물류/유통 가산률', value: 12, step: 1, type: 'pct', min: 0,   max: 80, enabled: true },
      { key: 'partner',      label: '파트너 마진률',   value: 18, step: 1, type: 'pct', min: 0,   max: 80, enabled: true },
      { key: 'risk_premium', label: '리스크 프리미엄', value: 0.1, step: 0.1, type: 'abs', min: 0, max: 50, enabled: true },
    ],
    private: [
      { key: 'gst',         label: 'GST 공제율',       value: 9,  step: 1, type: 'pct', min: 0, max: 20, enabled: true },
      { key: 'retail',      label: '소매 마진율',      value: 30, step: 1, type: 'pct', min: 0, max: 70, enabled: true },
      { key: 'partner',     label: '파트너 마진율',    value: 20, step: 1, type: 'pct', min: 0, max: 70, enabled: true },
      { key: 'distribution',label: '도매/유통 마진율', value: 15, step: 1, type: 'pct', min: 0, max: 70, enabled: true },
    ],
  };
}

function initP2Strategy() {
  const select = document.getElementById('p2-report-select');
  if (!select) return;
  _p2Ready = true;

  document.getElementById('p2-mode-manual')?.addEventListener('click', () => _setP2Mode('manual'));
  document.getElementById('p2-mode-ai')?.addEventListener('click', () => _setP2Mode('ai'));
  document.getElementById('p2-ai-run')?.addEventListener('click', _runP2AiAnalysis);

  select.addEventListener('change', (e) => {
    _p2SelectedReportId = e.target.value || '';
    _renderP2ReportBrief();
    _renderP2Manual();
  });

  document.querySelectorAll('[data-p2-manual-seg]').forEach((btn) => {
    btn.addEventListener('click', () => {
      _p2ManualSeg = btn.getAttribute('data-p2-manual-seg') || 'public';
      document.querySelectorAll('[data-p2-manual-seg]').forEach((x) => x.classList.remove('on'));
      btn.classList.add('on');
      _renderP2Manual();
    });
  });

  document.querySelectorAll('[data-p2-ai-seg]').forEach((btn) => {
    btn.addEventListener('click', () => {
      _p2AiSeg = btn.getAttribute('data-p2-ai-seg') || 'public';
      document.querySelectorAll('[data-p2-ai-seg]').forEach((x) => x.classList.remove('on'));
      btn.classList.add('on');
    });
  });

  _syncP2ReportsOptions();
  _setP2Mode('manual');
  _renderP2Manual();
}

function _syncP2ReportsOptions() {
  if (!_p2Ready) return;
  const select = document.getElementById('p2-report-select');
  if (!select) return;
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

function _setP2Mode(mode) {
  _p2Mode = mode === 'ai' ? 'ai' : 'manual';
  document.getElementById('p2-manual-box')?.classList.toggle('on', _p2Mode === 'manual');
  document.getElementById('p2-ai-box')?.classList.toggle('on', _p2Mode === 'ai');

  const manualBtn = document.getElementById('p2-mode-manual');
  const aiBtn = document.getElementById('p2-mode-ai');
  if (manualBtn && aiBtn) {
    manualBtn.classList.toggle('p2-btn-alt', _p2Mode !== 'manual');
    aiBtn.classList.toggle('p2-btn-alt', _p2Mode !== 'ai');
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
  const mAny = src.match(/([0-9]+(?:\.[0-9]+)?)/);
  if (mAny) return Number(mAny[1]);
  return NaN;
}

function _p2BasePrice() {
  const report = _getP2SelectedReport();
  if (!report) return 1.0;
  const fromHint = _extractSgdHint(report.price_hint || '');
  if (!Number.isNaN(fromHint) && fromHint > 0) return fromHint;
  return 1.0;
}

function _renderP2ReportBrief() {
  const el = document.getElementById('p2-report-brief');
  if (!el) return;
  const report = _getP2SelectedReport();
  if (!report) {
    el.textContent = '보고서를 선택하면 가격/근거 요약이 표시됩니다.';
    return;
  }
  const lines = [
    `${report.report_title || report.product || '보고서'}`,
    `판정: ${report.verdict || '—'}`,
    `가격 힌트: ${report.price_hint || '보고서에 명시된 가격 힌트 없음'}`,
    `무역 근거: ${report.basis_trade || '근거 없음'}`,
  ];
  el.textContent = lines.join('\n');
}

function _renderP2Manual() {
  const wrap = document.getElementById('p2-manual-options');
  const removed = document.getElementById('p2-manual-removed');
  const scenario = document.getElementById('p2-manual-scenarios');
  if (!wrap || !removed || !scenario) return;

  const options = _p2Manual[_p2ManualSeg];
  const active = options.filter(x => x.enabled);
  const inactive = options.filter(x => !x.enabled);

  wrap.innerHTML = active.map((opt) => `
    <div class="p2-opt-item">
      <div class="p2-opt-label">${_escHtml(opt.label)}</div>
      <div class="p2-opt-val">${opt.type === 'pct' ? `${Number(opt.value).toFixed(1)}%` : `SGD ${Number(opt.value).toFixed(2)}`}</div>
      <button class="p2-step-btn" data-p2-op="dec" data-key="${opt.key}" type="button">-</button>
      <button class="p2-step-btn" data-p2-op="inc" data-key="${opt.key}" type="button">+</button>
      <button class="p2-del-btn" data-p2-op="del" data-key="${opt.key}" type="button">×</button>
    </div>
  `).join('');

  removed.innerHTML = inactive.map((opt) => (
    `<button class="p2-add-btn" data-p2-op="add" data-key="${opt.key}" type="button">+ ${_escHtml(opt.label)}</button>`
  )).join('');

  const bindEls = wrap.querySelectorAll('button[data-p2-op], .p2-add-btn');
  bindEls.forEach((btn) => {
    btn.addEventListener('click', () => {
      const op = btn.getAttribute('data-p2-op');
      const key = btn.getAttribute('data-key');
      if (!key) return;
      const item = options.find(x => x.key === key);
      if (!item) return;
      if (op === 'del') item.enabled = false;
      if (op === 'add') item.enabled = true;
      if (op === 'inc') item.value = Math.min(item.max, Number((item.value + item.step).toFixed(2)));
      if (op === 'dec') item.value = Math.max(item.min, Number((item.value - item.step).toFixed(2)));
      _renderP2Manual();
    });
  });

  const base = _p2BasePrice();
  let avg = base;
  for (const opt of active) {
    if (opt.type === 'pct') avg *= (1 + opt.value / 100);
    else avg += opt.value;
  }
  avg = Math.max(0, avg);
  const aggressive = avg * 0.92;
  const conservative = avg * 1.10;
  scenario.innerHTML = _p2ScenarioHtml(aggressive, avg, conservative);
}

function _p2ScenarioHtml(aggressive, avg, conservative) {
  return `
    <div class="p2-scenario">
      <span class="p2-scenario-name">공격적인 시나리오</span>
      <span class="p2-scenario-price">SGD ${Number(aggressive).toFixed(2)}</span>
    </div>
    <div class="p2-scenario">
      <span class="p2-scenario-name">평균 시나리오</span>
      <span class="p2-scenario-price">SGD ${Number(avg).toFixed(2)}</span>
    </div>
    <div class="p2-scenario">
      <span class="p2-scenario-name">보수 시나리오</span>
      <span class="p2-scenario-price">SGD ${Number(conservative).toFixed(2)}</span>
    </div>`;
}

function _runP2AiAnalysis() {
  const report = _getP2SelectedReport();
  const note = document.getElementById('p2-ai-note');
  const out = document.getElementById('p2-ai-scenarios');
  if (!note || !out) return;
  if (!report) {
    note.textContent = '먼저 1공정 보고서를 선택해 주세요.';
    out.innerHTML = '';
    return;
  }

  const base = _p2BasePrice();
  const verdict = String(report.verdict || '—');
  const verdictFactor = verdict === '적합' ? 1.04 : verdict === '조건부' ? 0.98 : 0.93;
  const segFactor = _p2AiSeg === 'public' ? 0.95 : 1.06;
  const riskFactor = (String(report.risks_conditions || '').trim()) ? 0.98 : 1.0;
  const avg = Math.max(0, base * verdictFactor * segFactor * riskFactor);
  const aggressive = avg * 0.90;
  const conservative = avg * 1.12;

  note.textContent = `AI 추정 기준: ${verdict} 판정, ${_p2AiSeg === 'public' ? '공공' : '민간'} 시장 가중치, 보고서 리스크 문구 반영`;
  out.innerHTML = _p2ScenarioHtml(aggressive, avg, conservative);
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

loadKeyStatus();   // §6: API 키 배지
loadExchange();    // §3: 환율 즉시 로드
initTodo();        // §4: Todo 상태 복원
renderReportTab(); // §5: 보고서 탭 초기 렌더
initP2Strategy();  // §6: 2공정 수출전략 초기화
loadNews();        // §11: 시장 뉴스 즉시 로드
