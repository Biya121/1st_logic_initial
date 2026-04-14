/**
 * UPharma Export AI — 대시보드 스크립트
 * ═══════════════════════════════════════════════════════════════
 *
 * 수정 이력 (원본 index.html 인라인 스크립트 대비):
 *   B1  /api/sites (미존재) 제거 → /api/datasource/status 로 교체
 *   B2  크롤링 step → DB 조회 step (prog-db_load)
 *   B3  refreshOutlier(): /api/products → /api/analyze/result 로 변경
 *   B4  논문 카드: refs 0건이면 숨김 처리
 *   U1  API 키 상태 배지 (Claude·Perplexity) — /api/keys/status
 *   U2  진입 경로(entry_pathway) 결과 카드에 표시
 *   U3  신뢰도 설명(confidence_note) 결과 카드에 표시
 *   U4  PDF 카드: 생성 중 스피너 / 성공 / 실패 3가지 상태
 *   U5  데이터 소스 패널 (Supabase 연결 + 품목 수 + HSA 컨텍스트)
 *   U6  재분석 버튼 (결과 카드 하단)
 *
 * 파일 구조:
 *   1. 상수 & 전역 상태
 *   2. 거시지표
 *   3. API 키 상태 (U1)
 *   4. 진행 단계 (B2)
 *   5. 파이프라인 실행 & 폴링
 *   6. 결과 렌더링 (U2·U3·U4·U6·B4)
 *   7. 이상치 검증 (B3)
 *   8. 초기화
 * ═══════════════════════════════════════════════════════════════
 */

'use strict';

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   1. 상수 & 전역 상태
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

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

/** product_id → 브랜드명 (이상치 카드에 사용) */
const TRADE_NAMES = {
  SG_hydrine_hydroxyurea_500:  'Hydrine',
  SG_gadvoa_gadobutrol_604:    'Gadvoa Inj.',
  SG_sereterol_activair:       'Sereterol',
  SG_omethyl_omega3_2g:        'Omethyl',
  SG_rosumeg_combigel:         'Rosumeg',
  SG_atmeg_combigel:           'Atmeg',
  SG_ciloduo_cilosta_rosuva:   'Ciloduo',
  SG_gastiin_cr_mosapride:     'Gastiin CR',
};

/**
 * B2: 서버 step 이름과 프론트 progress 단계 ID 매핑
 * 서버 step: init → db_load → analyze → refs → report → done
 */
const STEP_ORDER = ['db_load', 'analyze', 'refs', 'report'];

let _pollTimer   = null;     // 파이프라인 폴링 타이머
let _currentKey  = null;     // 현재 선택된 product_key

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   3. 거시지표 (GET /api/macro)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

async function loadMacro() {
  try {
    const res  = await fetch('/api/macro');
    const data = await res.json();
    const grid = document.getElementById('macro-grid');
    grid.innerHTML = '';
    for (const item of data) {
      const card = document.createElement('div');
      card.className = 'macro-card';
      card.innerHTML = `
        <div class="macro-label">${item.label}</div>
        <div class="macro-value">${item.value}</div>
        <div class="macro-sub">${item.sub}</div>`;
      grid.appendChild(card);
    }
  } catch (e) {
    console.warn('거시지표 로드 실패:', e);
  }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   4. API 키 상태 (U1) — GET /api/keys/status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/**
 * Claude·Perplexity 키 설정 여부를 헤더 배지로 표시.
 * 실제 키 값은 서버에서 노출하지 않음.
 */
async function loadKeyStatus() {
  try {
    const res  = await fetch('/api/keys/status');
    const data = await res.json();
    _applyKeyBadge('key-claude',     data.claude,     'Claude',     'API 키 설정됨', 'API 키 미설정 — 분석 불가');
    _applyKeyBadge('key-perplexity', data.perplexity, 'Perplexity', 'API 키 설정됨', '미설정 — 논문 검색 생략');
  } catch (_) { /* 조용히 실패 */ }
}

function _applyKeyBadge(id, active, label, okTitle, ngTitle) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = 'key-badge ' + (active ? 'active' : 'inactive');
  el.title     = active ? `${label} ${okTitle}` : `${label} ${ngTitle}`;
  const dot    = el.querySelector('.key-badge-dot');
  if (dot) dot.style.background = active ? 'var(--ok)' : 'var(--muted)';
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   7. 진행 단계 표시 (B2: 크롤링 → DB 조회)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/**
 * @param {string} currentStep  STEP_ORDER 내 현재 단계
 * @param {'running'|'done'|'error'} status
 */
function setProgress(currentStep, status) {
  document.getElementById('progress-row').classList.add('visible');
  const idx = STEP_ORDER.indexOf(currentStep);

  for (let i = 0; i < STEP_ORDER.length; i++) {
    const el  = document.getElementById('prog-' + STEP_ORDER[i]);
    if (!el) continue;
    const dot = el.querySelector('.prog-dot');

    if (status === 'error' && i === idx) {
      el.className     = 'prog-step error';
      dot.textContent  = '✕';
    } else if (i < idx || (i === idx && status === 'done')) {
      el.className     = 'prog-step done';
      dot.textContent  = '✓';
    } else if (i === idx) {
      el.className     = 'prog-step active';
      dot.textContent  = i + 1;
    } else {
      el.className     = 'prog-step';
      dot.textContent  = i + 1;
    }
  }
}

function resetProgress() {
  document.getElementById('progress-row').classList.remove('visible');
  for (let i = 0; i < STEP_ORDER.length; i++) {
    const el = document.getElementById('prog-' + STEP_ORDER[i]);
    if (!el) continue;
    el.className = 'prog-step';
    el.querySelector('.prog-dot').textContent = i + 1;
  }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   8. 파이프라인 실행 & 폴링
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/**
 * 선택 품목 파이프라인 실행.
 * U6: 재분석 버튼도 이 함수를 호출 (파이프라인이 항상 새 태스크 생성).
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

  // 재분석 버튼 숨김
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

/** 분석 버튼 원래 상태 복원 */
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

    // B2: 서버 step 이름을 프론트 STEP_ORDER에 맞게 매핑
    if (d.step === 'db_load') {
      setProgress('db_load', 'running');
    } else if (d.step === 'analyze') {
      setProgress('db_load', 'done');
      setProgress('analyze', 'running');
    } else if (d.step === 'refs') {
      setProgress('analyze', 'done');
      setProgress('refs', 'running');
    } else if (d.step === 'report') {
      setProgress('refs', 'done');
      setProgress('report', 'running');
      // U4: PDF 생성 중 카드 표시
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
      const errStep = STEP_ORDER.includes(d.step) ? d.step : 'analyze';
      setProgress(errStep, 'error');
      _resetBtn();
    }
  } catch (_) { /* 조용히 재시도 */ }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   9. 신약 분석 파이프라인
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

let _customPollTimer = null;

const CUSTOM_STEP_ORDER = ['analyze', 'refs', 'report'];

function _setCustomProgress(step, status) {
  document.getElementById('custom-progress-row').classList.add('visible');
  const idMap = { analyze: 'cprog-analyze', refs: 'cprog-refs', report: 'cprog-report' };
  const idx = CUSTOM_STEP_ORDER.indexOf(step);
  CUSTOM_STEP_ORDER.forEach((s, i) => {
    const el = document.getElementById(idMap[s]);
    if (!el) return;
    const dot = el.querySelector('.prog-dot');
    if (status === 'error' && i === idx) {
      el.className = 'prog-step error'; dot.textContent = '✕';
    } else if (i < idx || (i === idx && status === 'done')) {
      el.className = 'prog-step done'; dot.textContent = '✓';
    } else if (i === idx) {
      el.className = 'prog-step active'; dot.textContent = i + 1;
    } else {
      el.className = 'prog-step'; dot.textContent = i + 1;
    }
  });
}

function _resetCustomProgress() {
  document.getElementById('custom-progress-row').classList.remove('visible');
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
  if (!tradeName || !inn) {
    alert('약품명과 성분명을 입력하세요.');
    return;
  }

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
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trade_name: tradeName, inn, dosage_form: dosage }),
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

    if (d.step === 'analyze') {
      _setCustomProgress('analyze', 'running');
    } else if (d.step === 'refs') {
      _setCustomProgress('analyze', 'done');
      _setCustomProgress('refs', 'running');
    } else if (d.step === 'report') {
      _setCustomProgress('refs', 'done');
      _setCustomProgress('report', 'running');
      _showReportLoading();
    }

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

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   10. 결과 렌더링 (U2·U3·U4·U6·B4)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/**
 * 분석 완료 후 결과·논문·PDF 카드를 화면에 렌더링.
 * @param {object|null} result  분석 결과 (analyze_product 반환값)
 * @param {Array}       refs    Perplexity 논문 목록
 * @param {string|null} pdfName PDF 파일명
 */
function renderResult(result, refs, pdfName) {

  /* ─ 분석 결과 카드 ─ */
  if (result) {
    const verdict = result.verdict;
    const vc      = verdict === '적합'   ? 'v-ok'
                  : verdict === '부적합' ? 'v-err'
                  : verdict             ? 'v-warn'
                  :                       'v-none';
    const err    = result.analysis_error;
    const vLabel = verdict
      || (err === 'no_api_key'    ? 'API 키 미설정'
        : err === 'claude_failed' ? 'Claude 분석 실패'
        :                          '미분석');

    document.getElementById('verdict-badge').className   = `verdict-badge ${vc}`;
    document.getElementById('verdict-badge').textContent = vLabel;
    document.getElementById('verdict-name').textContent  = result.trade_name || result.product_id || '';
    document.getElementById('verdict-inn').textContent   = INN_MAP[result.product_id] || result.inn || '';

    // S2: 신호등 — 판정에 따라 해당 램프만 켜기
    ['tl-red', 'tl-yellow', 'tl-green'].forEach(id => {
      document.getElementById(id).classList.remove('on');
    });
    if (verdict === '적합')   document.getElementById('tl-green').classList.add('on');
    else if (verdict === '부적합') document.getElementById('tl-red').classList.add('on');
    else if (verdict)              document.getElementById('tl-yellow').classList.add('on');

    // S3: 판정 근거 (시장/의료 · 규제 · 무역)
    const basisFallback = _deriveBasisFromRationale(result.rationale);
    _setText(
      'basis-market-medical',
      _formatDetailed(result.basis_market_medical || basisFallback.marketMedical),
    );
    _setText(
      'basis-regulatory',
      _formatDetailed(result.basis_regulatory || basisFallback.regulatory),
    );
    _setText(
      'basis-trade',
      _formatDetailed(result.basis_trade || basisFallback.trade),
    );
    _setText('basis-pbs-line', _pbsLineFromApi(result));

    // S4: 진입 채널 권고
    const pathEl = document.getElementById('entry-pathway');
    if (pathEl) {
      if (result.entry_pathway) {
        pathEl.textContent   = result.entry_pathway;
        pathEl.style.display = 'inline-block';
      } else {
        pathEl.style.display = 'none';
      }
    }

    const pbsPos = String(result.price_positioning_pbs || '').trim();
    _setText(
      'price-positioning-pbs',
      _formatDetailed(pbsPos || _pbsLineFromApi(result)),
    );

    // S4: 리스크/조건 (단일 텍스트)
    const riskText = String(result.risks_conditions || '').trim()
      || (Array.isArray(result.key_factors) ? result.key_factors.join(' / ') : '');
    _setText('risks-conditions', _formatDetailed(riskText));

    // U6: 재분석 버튼 표시
    const reBtn = document.getElementById('btn-reanalyze');
    if (reBtn) reBtn.style.display = 'inline-flex';

    document.getElementById('result-card').classList.add('visible');
  }

  /* ─ B4: 논문 카드 — refs 0건이면 숨김 ─ */
  const papersCard = document.getElementById('papers-card');
  const papersList = document.getElementById('papers-list');
  papersList.innerHTML = '';

  if (refs && refs.length > 0) {
    for (const ref of refs) {
      const item       = document.createElement('div');
      item.className   = 'paper-item';
      // XSS 방지: href/textContent에 직접 사용자 데이터가 들어오므로
      // title·url은 textContent로, href는 URL 검증 후 설정
      const safeUrl = /^https?:\/\//.test(ref.url || '') ? ref.url : '#';
      item.innerHTML = `
        <span class="paper-arrow">▸</span>
        <div>
          <div>
            <a class="paper-link" href="${safeUrl}" target="_blank" rel="noopener noreferrer"></a>
            <span class="paper-src"></span>
          </div>
          <div class="paper-reason"></div>
        </div>`;
      item.querySelector('.paper-link').textContent = ref.title || ref.url || '';
      item.querySelector('.paper-src').textContent  = ref.source ? `[${ref.source}]` : '';
      item.querySelector('.paper-reason').textContent = ref.reason || '';
      papersList.appendChild(item);
    }
    papersCard.classList.add('visible');
  } else {
    // B4: refs 없으면 카드 자체를 숨김
    papersCard.classList.remove('visible');
  }

  /* ─ U4: PDF 보고서 카드 ─ */
  const reportCard = document.getElementById('report-card');
  if (pdfName) {
    _showReportOk();
  } else {
    // PDF 생성 실패 (pdfName이 null인 경우)
    _showReportError();
  }
}

/** U4: PDF 생성 중 상태 */
function _showReportLoading() {
  document.getElementById('report-state-loading').style.display = 'flex';
  document.getElementById('report-state-ok').style.display      = 'none';
  document.getElementById('report-state-error').style.display   = 'none';
  document.getElementById('report-card').classList.add('visible');
}

/** U4: PDF 생성 완료 상태 */
function _showReportOk() {
  document.getElementById('report-state-loading').style.display = 'none';
  document.getElementById('report-state-ok').style.display      = 'block';
  document.getElementById('report-state-error').style.display   = 'none';
  document.getElementById('report-card').classList.add('visible');
}

/** U4: PDF 생성 실패 상태 */
function _showReportError() {
  document.getElementById('report-state-loading').style.display = 'none';
  document.getElementById('report-state-ok').style.display      = 'none';
  document.getElementById('report-state-error').style.display   = 'block';
  document.getElementById('report-card').classList.add('visible');
}

function _setText(id, value, fallback = '—') {
  const el = document.getElementById(id);
  if (!el) return;
  const s = String(value || '').trim();
  el.textContent = s || fallback;
}

function _deriveBasisFromRationale(rationale) {
  const text = String(rationale || '');
  const lines = text.split('\n').map((x) => x.trim()).filter(Boolean);
  const out = { marketMedical: '', regulatory: '', trade: '' };
  for (const line of lines) {
    const low = line.toLowerCase();
    if (!out.marketMedical && (low.includes('시장') || low.includes('의료'))) {
      out.marketMedical = line.replace(/^[\-\d\.\)\s]+/, '');
      continue;
    }
    if (!out.regulatory && low.includes('규제')) {
      out.regulatory = line.replace(/^[\-\d\.\)\s]+/, '');
      continue;
    }
    if (!out.trade && low.includes('무역')) {
      out.trade = line.replace(/^[\-\d\.\)\s]+/, '');
      continue;
    }
  }
  if (!out.marketMedical && lines.length > 0) out.marketMedical = lines[0];
  if (!out.regulatory && lines.length > 1) out.regulatory = lines[1];
  if (!out.trade && lines.length > 2) out.trade = lines[2];
  return out;
}

function _formatDetailed(text) {
  const src = String(text || '').trim();
  if (!src) return '';
  const parts = src
    .split(/(?<=[\.\!\?])\s+/)
    .map((x) => x.trim())
    .filter(Boolean);
  if (parts.length <= 1) return src;
  return parts.map((x) => `- ${x}`).join('\n');
}

/** PBS DPMQ 한 줄 요약 */
function _pbsLineFromApi(result) {
  const aud = result.pbs_dpmq_aud;
  const sgd = result.pbs_dpmq_sgd_hint;
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

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   10. 초기화
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

loadMacro();
loadKeyStatus();   // U1
