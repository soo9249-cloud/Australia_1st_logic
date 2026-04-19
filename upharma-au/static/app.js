/**
 * UPharma Export AI — 호주 대시보드 스크립트 (싱가포르 원본 베이스 이식)
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
 *   B2  1단계 라벨: 실시간 크롤링 (내부 id: prog-db_load / db_load)
 *   B3  refreshOutlier → /api/analyze/result
 *   B4  논문 카드: refs 0건이면 숨김
 *   U1  API 키 상태 배지
 *   U2  진입 경로(entry_pathway) 표시
 *   U3  신뢰도(confidence_note) 표시
 *   U4  PDF 카드 3가지 상태
 *   U6  재분석 버튼
 *   N1  탭 전환 (AU 프론트 기반)
 *   N2  환율 카드 (호주 /api/exchange AUD 기준 응답 → USD/KRW 메인 파생 표시)
 *   N3  To-Do 리스트 (localStorage)
 *   N4  보고서 탭 자동 등록
 * ═══════════════════════════════════════════════════════════════
 */

'use strict';

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §1. 상수 & 전역 상태
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/** product_id → INN 표시명 (호주 8 품목, au_products.json 기준) */
const INN_MAP = {
  'au-hydrine-004':   'Hydroxycarbamide 500mg',
  'au-gadvoa-002':    'Gadobutrol 604.72mg',
  'au-sereterol-003': 'Fluticasone / Salmeterol (250/50·500/50 DPI)',
  'au-omethyl-001':   'Omega-3 EE 2g',
  'au-rosumeg-005':   'Rosuvastatin + Omega-3',
  'au-atmeg-006':     'Atorvastatin + Omega-3',
  'au-ciloduo-007':   'Cilostazol + Rosuvastatin',
  'au-gastiin-008':   'Mosapride CR (서방형) 15mg',
};

/**
 * B2: 서버 step 이름 → 프론트 progress 단계 ID 매핑
 * 진행 단계: 크롤링(db_load) → analyze → refs → report → done
 */
const STEP_ORDER = ['db_load', 'analyze', 'refs', 'report'];

let _pollTimer  = null;   // 파이프라인 폴링 타이머
let _currentKey = null;   // 현재 선택된 product_key

// P2 3열 시나리오용 원본 데이터
let _p2ScenarioRaw = { agg: 0, avg: 0, cons: 0, sgd_usd: 0, sgd_krw: 0 };

// P2 컬럼별 커스텀 옵션 데이터
let _p2ColData = {
  agg:  { opts: [] },
  avg:  { opts: [] },
  cons: { opts: [] },
};
/** 미확정 옵션 입력 행(프론트만 — 새로고침 시 초기화) */
let _p2ColPendingDrafts = { agg: [], avg: [], cons: [] };

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
   §2-b. 단계 섹션 토글
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

const _processOpen = { p1: true, p2: true, p3: true };

function toggleProcess(id) {
  _processOpen[id] = !_processOpen[id];
  const body  = document.getElementById('pb-' + id);
  const arrow = document.getElementById('pa-' + id);
  if (body)  body.classList.toggle('hidden', !_processOpen[id]);
  if (arrow) arrow.classList.toggle('closed', !_processOpen[id]);
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §2-c. 거시 지표 로드 — GET /api/macro
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

async function loadMacro() {
  // 호주: /api/macro 엔드포인트 없음 (Stage 0 Q1 → HTML 하드코딩).
  // templates/index.html 의 .macro-card 4 개에 IMF/ABS/BMI/IMF 값 직접 삽입.
  // USD → KRW 환산 보조 표시는 Stage 3 에서 loadExchange() 콜백에 연결 예정.
  return;
}

function _setMacro(valId, val, srcId, src) {
  const ve = document.getElementById(valId);
  const se = document.getElementById(srcId);
  if (ve) ve.textContent = val;
  if (se) se.textContent = src;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §3. 환율 로드 (N2) — GET /api/exchange
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

async function loadExchange() {
  // 호주 /api/exchange 응답: { aud_krw, aud_usd, aud_jpy, aud_cny, updated, pct_change?, ok? }
  //   · yfinance 성공 : ok 키 없음, pct_change 포함 (AUD/KRW 전일대비 %)
  //   · 폴백(exchangerate-api.com) : ok:false, pct_change 없음
  //
  // 호주 UI 는 USD 기준 표시이므로 프론트에서 파생 환율 계산:
  //   USD/KRW = aud_krw / aud_usd   → 메인 fx-main (28px)
  //   USD/AUD = 1 / aud_usd          → 서브 fx-usd-aud
  //   AUD/KRW = aud_krw              → 서브 fx-aud-krw (FOB 역산 참고용)
  //   pct_change 는 AUD/KRW 기준 → fx-chg 에 표시하되 라벨로 "AUD/KRW 전일" 명시해 오해 방지
  // JPY/CNY 는 응답에 포함되지만 호주 UI 에서 미사용 (Stage 1 Q2-c 결정)
  const btn = document.querySelector('button.btn-refresh[onclick*="loadExchange"]');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 조회 중…'; }

  try {
    const res  = await fetch('/api/exchange');
    const data = await res.json();

    // 파생 환율 계산
    const audUsd = Number(data.aud_usd) || 0;
    const audKrw = Number(data.aud_krw) || 0;
    const usdKrw = audUsd > 0 ? audKrw / audUsd : 0;
    const usdAud = audUsd > 0 ? 1 / audUsd : 0;

    // 전역 저장 (수출가격 전략 P2 에서 USD 환산·FOB 역산 재사용) + 호주 원본 키 보존
    window._exchangeRates = {
      ...data,
      usd_krw: usdKrw,   // 파생 (USD→KRW 메인 표시·수출전략 최종가 환산)
      usd_aud: usdAud,   // 파생 (USD→AUD, 역환산용)
    };
    if (typeof _p2FillExchangeRate === 'function') {
      _p2FillExchangeRate();
      if (typeof _renderP2Manual === 'function') _renderP2Manual();
    }

    // 메인: USD / KRW (28px)
    const mainEl = document.getElementById('fx-main');
    if (mainEl && usdKrw > 0) {
      const fmt = usdKrw.toLocaleString('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      mainEl.innerHTML = `${fmt}<span style="font-size:14px;margin-left:4px;color:var(--muted);font-weight:700;">원</span>`;
    }

    // 서브 1: USD / AUD
    const usdAudEl = document.getElementById('fx-usd-aud');
    if (usdAudEl && usdAud > 0) {
      usdAudEl.textContent = usdAud.toFixed(4);
    }

    // 서브 2: AUD / KRW (FOB 역산 참고값)
    const audKrwEl = document.getElementById('fx-aud-krw');
    if (audKrwEl && audKrw > 0) {
      audKrwEl.textContent = audKrw.toLocaleString('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + '원';
    }

    // 전일 대비 변동 (pct_change 는 AUD/KRW 기준 — 라벨로 명시해 오해 방지)
    const chgEl = document.getElementById('fx-chg');
    if (chgEl) {
      if (typeof data.pct_change === 'number') {
        const pct = data.pct_change;
        const sign = pct > 0 ? '▲' : pct < 0 ? '▼' : '·';
        const color = pct > 0 ? 'var(--green)' : pct < 0 ? 'var(--red)' : 'var(--muted)';
        chgEl.style.display = 'inline-flex';
        chgEl.style.color = color;
        chgEl.textContent = `${sign} ${Math.abs(pct).toFixed(2)}% · AUD/KRW 전일`;
      } else {
        chgEl.style.display = 'none';
      }
    }

    // 출처 + 조회 시각
    const tsEl = document.getElementById('fxTimestamp');
    if (tsEl) {
      const now = new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
      const fallbackNote = data.ok === false ? ' · 폴백값' : '';
      tsEl.textContent = `조회: ${now}${fallbackNote}`;
    }
  } catch (e) {
    const tsEl = document.getElementById('fxTimestamp');
    if (tsEl) tsEl.textContent = '환율 조회 실패 — 잠시 후 다시 시도해 주세요';
    console.warn('환율 로드 실패:', e);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '↻ 환율 새로고침'; }
  }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §4. To-Do 리스트 (N3)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

const TODO_FIXED_IDS = ['p1', 'rep', 'p2', 'p3'];
const TODO_LS_KEY    = 'au_upharma_todos_v1';
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

const REPORTS_LS_KEY = 'au_upharma_reports_v1';

/**
 * 수출가격 전략(AI 파이프라인) 3시나리오 — 호주 수입 스폰서(수입상) 마진(%) UI 표기.
 * (수출사 마진이 아님.) 마진이 클수록 동일 소매가 기준 FOB는 낮아짐 → 저가 진입 열이 가장 낮은 FOB.
 * 백엔드 `stage2/fob_calculator.py` 의 `DEFAULT_PRESETS_PCT` 와 일치해야 함.
 */
const P2_IMPORTER_MARGIN_DISPLAY_PCT = { agg: 30, avg: 20, cons: 10 };

/**
 * @param {object|undefined} s 시나리오 객체 (백엔드가 importer_margin_pct 를 주면 우선)
 * @param {number} idx 0=agg 1=avg 2=cons
 */
function _p2ImporterMarginPctFromAiScenario(s, idx) {
  if (s && s.importer_margin_pct != null && !Number.isNaN(Number(s.importer_margin_pct))) {
    return Number(s.importer_margin_pct);
  }
  const keys = ['agg', 'avg', 'cons'];
  const k = keys[idx];
  return k ? P2_IMPORTER_MARGIN_DISPLAY_PCT[k] : null;
}

/** 3열 카드 상단 '수입상 마진 n%' 문구 갱신 */
function _p2UpdateImporterMarginHints(scenarios) {
  const cols = ['agg', 'avg', 'cons'];
  cols.forEach((col, idx) => {
    const el = document.getElementById('p2c-importer-hint-' + col);
    if (!el) return;
    const s = Array.isArray(scenarios) ? scenarios[idx] : null;
    const pct = _p2ImporterMarginPctFromAiScenario(s, idx);
    el.textContent = pct != null ? `수입상 마진 ${pct}%` : '수입상 마진 —';
  });
}

function _loadReports() {
  try   { return JSON.parse(localStorage.getItem(REPORTS_LS_KEY) || '[]'); }
  catch { return []; }
}

/**
 * 시장조사 완료 후 renderResult()가 호출 → 보고서 탭에 항목 추가.
 * @param {object|null} result  분석 결과
 * @param {string|null} pdfName PDF 파일명
 */
function _addReportEntry(result, pdfName) {
  const reports = _loadReports();
  const productName = result ? (result.trade_name || result.product_id || '알 수 없음') : '알 수 없음';
  const entry   = {
    id:        Date.now(),
    product_id: result ? (result.product_id || '') : '',
    product:   productName,
    stage_label: '시장조사',
    report_title: `시장조사 보고서 - ${productName}`,
    inn:       result ? (INN_MAP[result.product_id] || result.inn || '') : '',
    verdict:   result ? (result.verdict || '—') : '—',
    price_hint: result ? String(result.price_positioning_pbs || '').trim() : '',
    pbs_sgd_hint: result ? (result.pbs_dpmq_sgd_hint ?? null) : null,
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
        메인에서 분석을 실행하면 여기에 자동으로 등록됩니다.
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
    /* 판정 없음(—)은 뱃지로 두면 대시가 버튼처럼 보여 혼동되므로 표시하지 않음 */
    const verdictBlock =
      r.verdict && r.verdict !== '—'
        ? `<div class="rep-item-verdict">
          <span class="bdg ${vc}">${_escHtml(r.verdict)}</span>
        </div>`
        : '';

    return `
      <div class="rep-item">
        <div class="rep-item-info">
          <div class="rep-item-product">${_escHtml(r.report_title || r.product)}${innSpan}</div>
          <div class="rep-item-meta">${_escHtml(r.timestamp)}</div>
        </div>
        ${verdictBlock}
        ${dlBtn}
        ${delBtn}
      </div>`;
  }).join('');
  _syncP2ReportsOptions();
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §6. 수출가격 전략 (P2)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

let _p2Ready = false;
let _p2Tab = 'ai';
let _p2ManualSeg = 'public';
let _p2AiSeg = 'public';
let _p2SelectedReportId = '';
let _p2AiSelectedReportId = '';
let _p2UploadedReportFilename = '';
let _p2AiPollTimer = null;
let _p2Manual = _makeP2Defaults();
let _p2LastScenarios = null;
let _p2ManualCalculated = false;

function _makeP2Defaults() {
  // 호주 기본값 (stage2/fob_calculator.py DEFAULT_* 상수 맞춤):
  //   GST_PCT               = 10%  (처방약은 _p2ClassifyGst 로 자동 0% 전환)
  //   PHARMACY_MARGIN_PCT   = 30%
  //   WHOLESALE_MARGIN_B    = 10%
  //   IMPORTER_MARGIN       = 20%  (average 시나리오 기준)
  // 화폐: USD 메인 (AUD 원본은 loadExchange.window._exchangeRates.aud_usd 로 환산)
  //
  // UI 범위(위임 정합): 민간(private) = Logic B와 동일하게 gst·pharmacy·wholesale·importer 4슬라이더(+기준가·환율).
  // 공공(public) = 입찰가·환율·비율 3항목으로 별도 단순 모델 — stage2 Logic A(공시 AEMP·α·수입상만)와
  // 항목 수가 1:1이 아님. Logic A 역산은 AI 탭 3열 또는 /api/stage2/calculate.
  return {
    public: [
      { key: 'base_price', label: '기준 입찰가 (USD)', value: 0, type: 'abs_input', unit: 'USD', step: 0.5, min: 0, max: 99999, enabled: true, fixed: false, expanded: false, hint: '호주 PBS AEMP 또는 주 정부 병원조달 입찰가 참고', rationale: '호주 공공채널은 PBS 급여가격·HealthShare NSW 입찰가가 기준.' },
      { key: 'exchange', label: '환율 (AUD→USD)', value: 0.65, type: 'abs_input', unit: 'rate', step: 0.0001, min: 0.0001, max: 99, enabled: true, fixed: false, expanded: false, hint: 'AUD 입력 값을 USD 로 환산 (로드 시 실시간 aud_usd 반영)', rationale: '호주 AUD 기준가를 USD 로 맞춰 환차 위험을 줄입니다.' },
      { key: 'pub_ratio', label: '공공 수출가 산출 비율', value: 30, type: 'pct_mult', unit: '%', step: 1, min: 0, max: 99999, enabled: true, fixed: false, expanded: false, hint: '기준가 대비 최종 반영 비율 (수입상 30% 기본)', rationale: 'PBS 급여·병원조달·파트너 마진을 반영한 목표 비율.' },
    ],
    private: [
      { key: 'base_het', label: '민간 기준가 (AUD 소매가 USD 환산)', value: 0, type: 'abs_input', unit: 'USD', step: 0.5, min: 0, max: 99999, enabled: true, fixed: false, expanded: false, hint: 'Chemist Warehouse × 1.20 (CHOICE 조사 기준) 또는 PBS DPMQ 를 USD 환산한 값', rationale: '호주 민간 시장은 Chemist/약국 체인 소매가를 USD 로 환산해 역산.' },
      { key: 'exchange', label: '환율 (AUD→USD)', value: 0.65, type: 'abs_input', unit: 'rate', step: 0.0001, min: 0.0001, max: 99, enabled: true, fixed: false, expanded: false, hint: 'AUD 입력 값을 USD 로 환산 (로드 시 실시간 aud_usd 반영)', rationale: '실시간 환율 반영으로 가격 정합성을 유지합니다.' },
      { key: 'gst', label: 'GST 공제', value: 0, type: 'gst_fixed', unit: '%', step: 0, min: 0, max: 10, enabled: true, fixed: true, expanded: false, hint: '호주 GST — 처방약(S4/S8) 0% · OTC/건강기능식품 10%. 품목 선택 시 _p2ClassifyGst 로 자동 전환.', rationale: '호주는 S4/S8 처방의약품 GST-free, Omethyl(Omega-3) 등 OTC 만 10% 과세.' },
      { key: 'pharmacy', label: '약국 마진율', value: 30, type: 'pct_deduct', unit: '%', step: 1, min: 0, max: 99999, enabled: true, fixed: false, expanded: false, hint: '호주 약국 체인 마진 (fob_calculator DEFAULT_PHARMACY_MARGIN_PCT = 30%)', rationale: 'Chemist Warehouse·Priceline 등 체인 마진 차감.' },
      { key: 'wholesale', label: '도매 마진', value: 10, type: 'pct_deduct', unit: '%', step: 1, min: 0, max: 99999, enabled: true, fixed: false, expanded: false, hint: '호주 도매 유통 마진 (fob_calculator DEFAULT_WHOLESALE_MARGIN_B_PCT = 10%)', rationale: 'Sigma/API 등 호주 의약품 도매 마진.' },
      { key: 'importer', label: '수입상 마진', value: 20, type: 'pct_deduct', unit: '%', step: 1, min: 0, max: 99999, enabled: true, fixed: false, expanded: false, hint: '호주 수입상 마진 (stage2 average 시나리오 = 20%)', rationale: '한국 제조사 ↔ 호주 수입상 사이 중간 마진.' },
    ],
  };
}

/**
 * 호주 GST 품목별 자동 전환 (Stage 4 복원).
 *
 * 호주 규칙:
 *   · S4/S8 처방의약품 → GST-free (0%)
 *   · OTC · 건강기능식품 → 10%
 *
 * 호주 8 품목 중 Omethyl(Omega-3, OTC) 만 10%, 나머지 7개 처방약은 0%.
 * product_id 문자열로 분류.
 *
 * @param {string} productId  호주 product_id (예: 'au-omethyl-001')
 * @returns {number}          GST 세율 (0 또는 10)
 */
function _p2ClassifyGst(productId) {
  const id = String(productId || '');
  // 신약 임시 ID — 시드 없음·초기 PBS 미등재가 기본 → 민간 소매 GST 10% (Logic B 기본과 정합)
  if (id.startsWith('au-newdrug-')) return 10;
  const OTC_PRODUCT_IDS = new Set([
    'au-omethyl-001',   // Omega-3 EE 2g — 건강기능식품 카테고리
  ]);
  return OTC_PRODUCT_IDS.has(id) ? 10 : 0;
}

/**
 * 현재 선택된 보고서의 product_id 를 기반으로 _p2Manual.private GST 옵션 자동 업데이트.
 * 직접입력 탭 보고서 드롭다운 onchange / AI 파이프라인 실행 직전에 호출.
 *
 * @param {object|null} report  _loadReports() 가 반환한 엔트리 ({product_id, ...})
 */
function _p2ApplyGstForReport(report) {
  if (!report) return;
  const productId = report.product_id || report.product || '';
  const gstRate = _p2ClassifyGst(productId);
  const gstOpt = _p2Manual.private.find((x) => x.key === 'gst');
  if (gstOpt) {
    gstOpt.value = gstRate;
    gstOpt.hint = gstRate === 10
      ? `호주 GST 10% — OTC/건강기능식품 (${productId})`
      : `호주 GST 0% — 처방의약품 GST-free (${productId})`;
  }
}

function initP2Strategy() {
  if (!document.getElementById('p2-wrap')) return;
  _p2Ready = true;

  const aiSelect = document.getElementById('p2-ai-report-select');
  if (aiSelect) {
    aiSelect.addEventListener('change', (e) => {
      _p2AiSelectedReportId = e.target.value || '';
      // 선택 보고서의 product_id 로 GST 자동 전환 (AI 탭도 _p2Manual.private.gst 공유)
      const report = _loadReports().find((r) => String(r.id) === String(_p2AiSelectedReportId));
      _p2ApplyGstForReport(report);
    });
  }

  const manualSelect = document.getElementById('p2-report-select');
  if (manualSelect) {
    manualSelect.addEventListener('change', (e) => {
      _p2SelectedReportId = e.target.value || '';
      const report = _getP2SelectedReport();
      _p2ApplyGstForReport(report);
      if (typeof _p2FillBaseFromReport === 'function') _p2FillBaseFromReport();
      if (typeof _renderP2Manual === 'function') _renderP2Manual();
    });
  }

  document.querySelectorAll('.p2-seg-btn[data-p2-manual-seg]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const seg = btn.getAttribute('data-p2-manual-seg') || 'public';
      _p2ManualSeg = seg === 'private' ? 'private' : 'public';
      document.querySelectorAll('.p2-seg-btn[data-p2-manual-seg]').forEach((b) => {
        b.classList.toggle('on', b.getAttribute('data-p2-manual-seg') === seg);
      });
      const desc = document.getElementById('p2-manual-seg-desc');
      if (desc) {
        desc.textContent = _p2ManualSeg === 'public'
          ? '공공 시장: PBS 공공급여 채널 · 주 정부 병원조달(HealthShare NSW 등)'
          : '민간 시장: Chemist Warehouse 등 약국 체인 · 소매 유통 (Logic B 역산)';
      }
      if (typeof _renderP2Manual === 'function') _renderP2Manual();
    });
  });

  _syncP2ReportsOptions();
  _p2FillExchangeRate();
}

function switchP2Tab(tab) {
  _p2Tab = tab === 'manual' ? 'manual' : 'ai';
  const aiBtn = document.getElementById('p2-tab-ai');
  const manualBtn = document.getElementById('p2-tab-manual');
  const aiTab = document.getElementById('p2-ai-tab');
  const manualTab = document.getElementById('p2-manual-tab');
  if (aiBtn && manualBtn) {
    aiBtn.classList.toggle('on', _p2Tab === 'ai');
    manualBtn.classList.toggle('on', _p2Tab === 'manual');
  }
  if (aiTab && manualTab) {
    aiTab.style.display = _p2Tab === 'ai' ? '' : 'none';
    manualTab.style.display = _p2Tab === 'manual' ? '' : 'none';
  }
  if (_p2Tab === 'ai') _showP2AiError('');
}

function setP2AiSeg(seg) {
  _p2AiSeg = seg === 'private' ? 'private' : 'public';
}

/** 서버 `_extract_product_id_from_filename` 과 동일 — `(2)` 복사본 → `_2_` 접미사 허용 */
function _p2ExtractProductIdFromFilename(name) {
  const base = String(name || '').split(/[/\\]/).pop() || '';
  const m = base.match(/(?:\d+_)?au_report_(.+?)_\d{8}_\d{6}/i);
  return m ? m[1] : '';
}

async function handleP2FileSelect(inputEl) {
  // TODO (CC 병행 Task) — 이 업로드가 신약(needs_price_upload) 케이스이면
  // /api/crawl/price-pdf-upload 로, 아니면 기존 /api/p2/pipeline 로 라우팅.
  // 현 프런트 변경 범위에서는 라벨·UX 만 통합하고 백엔드 분기는 유지.
  const file = inputEl?.files?.[0];
  const statusEl = document.getElementById('p2-upload-status');
  const textEl = document.getElementById('p2-upload-text');
  if (!file) return;

  if (statusEl) {
    statusEl.style.display = 'block';
    statusEl.textContent = '검사 중…';
  }
  if (textEl) textEl.textContent = file.name;

  try {
    const arr = await file.arrayBuffer();
    const head = new Uint8Array(arr.slice(0, 4));
    const isPdfMagic = head[0] === 0x25 && head[1] === 0x50 && head[2] === 0x44 && head[3] === 0x46;
    if (!isPdfMagic) {
      if (statusEl) statusEl.textContent = 'PDF 바이너리(%PDF)가 아닙니다. 파일명·확장자와 무관하게 실제 PDF 여부로 판단합니다.';
      return;
    }
    if (statusEl) statusEl.textContent = '업로드 중…';
    const bytes = new Uint8Array(arr);
    let binary = '';
    for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
    const contentB64 = btoa(binary);

    const res = await fetch('/api/p2/upload', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: file.name, content_b64: contentB64 }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.filename) throw new Error(data.detail || `HTTP ${res.status}`);

    _p2UploadedReportFilename = data.filename;
    /* 임의 이름 PDF를 올려도, 목록에서 선택한 보고서의 product_id 로 파이프라인 품목을 유지 */
    if (statusEl) {
      statusEl.textContent = data.original_name
        ? `업로드 완료: ${data.original_name} (저장: ${data.filename})`
        : `업로드 완료: ${data.filename}`;
    }
  } catch (err) {
    if (statusEl) statusEl.textContent = `업로드 실패: ${err.message}`;
  }
}

/* 수출가격 전략 진행 단계 — PDF 추출 → 가격 추출 → AI 분석 → 보고서 생성 */
const P2_STEP_ORDER = ['extract', 'ai_extract', 'ai_analysis', 'report'];

/** 서버 step 값으로 스테퍼 동기화 (이전 단계 완료·현재 단계 진행 중) */
function _applyP2PipelineStep(step) {
  const idx = P2_STEP_ORDER.indexOf(step);
  if (idx < 0) return;
  const row = document.getElementById('p2-progress-row');
  if (row) row.classList.add('visible');
  for (let i = 0; i < P2_STEP_ORDER.length; i++) {
    const el = document.getElementById('p2prog-' + P2_STEP_ORDER[i]);
    if (!el) continue;
    const dot = el.querySelector('.prog-dot');
    if (i < idx) {
      el.className = 'prog-step done';
      dot.textContent = '✓';
    } else if (i === idx) {
      el.className = 'prog-step active';
      dot.textContent = String(i + 1);
    } else {
      el.className = 'prog-step';
      dot.textContent = String(i + 1);
    }
  }
}

function _markP2ProgressAllDone() {
  const row = document.getElementById('p2-progress-row');
  if (row) row.classList.add('visible');
  for (let i = 0; i < P2_STEP_ORDER.length; i++) {
    const el = document.getElementById('p2prog-' + P2_STEP_ORDER[i]);
    if (!el) continue;
    el.className = 'prog-step done';
    el.querySelector('.prog-dot').textContent = '✓';
  }
}

function _setP2Progress(currentStep, status) {
  const row = document.getElementById('p2-progress-row');
  if (row) row.classList.add('visible');
  const idx = P2_STEP_ORDER.indexOf(currentStep);

  for (let i = 0; i < P2_STEP_ORDER.length; i++) {
    const el = document.getElementById('p2prog-' + P2_STEP_ORDER[i]);
    if (!el) continue;
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
  }
}

function _resetP2Progress() {
  const row = document.getElementById('p2-progress-row');
  if (row) row.classList.remove('visible');
  for (let i = 0; i < P2_STEP_ORDER.length; i++) {
    const el = document.getElementById('p2prog-' + P2_STEP_ORDER[i]);
    if (!el) continue;
    el.className = 'prog-step';
    el.querySelector('.prog-dot').textContent = i + 1;
  }
}

function _showP2AiError(msg) {
  const el = document.getElementById('p2-ai-error-msg');
  if (!el) return;
  if (msg) { el.style.display = ''; el.textContent = msg; }
  else { el.style.display = 'none'; el.textContent = ''; }
}

function _resetP2AiResultView() {
  const resultSection = document.getElementById('p2-ai-result-section');
  if (resultSection) resultSection.style.display = 'none';
  const dlState = document.getElementById('p2-report-dl-state');
  if (dlState) dlState.innerHTML = '';
  _showP2AiError('');
}

function _resetP2ManualResultView() {
  _p2ManualCalculated = false;
  _p2LastScenarios = null;
  const card = document.getElementById('p2-manual-result-card');
  if (card) card.style.display = 'none';
}

function runP2ManualCalculation() {
  const icon = document.getElementById('p2-manual-calc-icon');
  if (icon) icon.textContent = '⏳';
  _p2ManualCalculated = true;
  _renderP2Manual();
  if (icon) icon.textContent = '▶';
}

async function runP2AiPipeline() {
  const runBtn = document.getElementById('btn-p2-ai-run');
  const runIcon = document.getElementById('p2-ai-run-icon');
  const selectedReport = _loadReports().find((r) => String(r.id) === String(_p2AiSelectedReportId));
  const reportFilename = _p2UploadedReportFilename || (selectedReport ? (selectedReport.pdf_name || '') : '');

  if (!reportFilename) {
    _showP2AiError('실행 전 PDF가 있는 보고서를 선택하거나 PDF를 직접 업로드해 주세요.');
    return;
  }

  /* 품목 ID는 파일명과 무관 — 상단 품목 선택이 최우선, 그다음 저장된 보고서, 레거시 폴백만 파일명 */
  const fromTop = (document.getElementById('product-select')?.value || '').trim();
  let productCode = fromTop;
  if (!productCode && selectedReport && selectedReport.product_id) {
    productCode = String(selectedReport.product_id).trim();
  }
  if (!productCode && typeof _currentKey === 'string' && _currentKey) {
    productCode = _currentKey;
  }
  if (!productCode) {
    productCode = _p2ExtractProductIdFromFilename(reportFilename);
  }
  if (!productCode) {
    _showP2AiError(
      '분석할 품목을 화면 상단에서 먼저 선택해 주세요. PDF 파일명은 어떤 형식이어도 됩니다.',
    );
    return;
  }

  if (_p2AiPollTimer) clearInterval(_p2AiPollTimer);
  _resetP2AiResultView();
  _resetP2Progress();
  _applyP2PipelineStep('extract');

  if (runBtn) runBtn.disabled = true;
  if (runIcon) runIcon.textContent = '⏳';

  try {
    const res = await fetch('/api/p2/pipeline', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        report_filename: reportFilename,
        market: _p2AiSeg,
        product_code: productCode,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    _p2AiPollTimer = setInterval(_pollP2AiPipeline, 1800);
  } catch (err) {
    _setP2Progress('extract', 'error');
    _showP2AiError(`실행 실패: ${err.message}`);
    if (runBtn) runBtn.disabled = false;
    if (runIcon) runIcon.textContent = '▶';
  }
}

async function _pollP2AiPipeline() {
  try {
    const res = await fetch('/api/p2/pipeline/status');
    const data = await res.json();
    if (data.status === 'idle') return;

    if (data.status === 'running' && data.step && P2_STEP_ORDER.includes(data.step)) {
      _applyP2PipelineStep(data.step);
    }

    if (data.status === 'done') {
      clearInterval(_p2AiPollTimer);
      _p2AiPollTimer = null;
      _markP2ProgressAllDone();
      const rr = await fetch('/api/p2/pipeline/result');
      const result = await rr.json();
      _renderP2AiResult(result);
      document.getElementById('btn-p2-ai-run')?.removeAttribute('disabled');
      const runIcon = document.getElementById('p2-ai-run-icon');
      if (runIcon) runIcon.textContent = '▶';
    } else if (data.status === 'error') {
      clearInterval(_p2AiPollTimer);
      _p2AiPollTimer = null;
      const errStep = P2_STEP_ORDER.includes(data.step) ? data.step : 'extract';
      _setP2Progress(errStep, 'error');
      _showP2AiError(`오류: ${data.step_label || '파이프라인 실패'}`);
      document.getElementById('btn-p2-ai-run')?.removeAttribute('disabled');
      const runIcon = document.getElementById('p2-ai-run-icon');
      if (runIcon) runIcon.textContent = '▶';
    }
  } catch (_err) {
    // polling retry
  }
}

/* P2 3열 카드: 역산 섹션 토글 */
function toggleP2ColDetail(col) {
  const detail = document.getElementById('p2cd-' + col);
  const btn    = detail?.previousElementSibling?.querySelector('.p2-col-expand-btn');
  if (!detail) return;
  const willOpen = detail.style.display === 'none';
  /* 기준가·수수료·운임·추가 옵션은 모두 접었다 펼쳐도 유지(값만 사용자가 자유롭게 조정) */
  detail.style.display = willOpen ? '' : 'none';
  if (btn) btn.textContent = (willOpen ? '▾ ' : '▸ ') + '역산 · 옵션 편집';
}

/** 커스텀 옵션 한 건을 가격에 반영 (% 가산·% 차감·USD 가산) */
function _p2ApplyOptToPrice(price, type, rawVal) {
  const v = parseFloat(rawVal);
  if (Number.isNaN(v) || v < 0) return price;
  if (type === 'pct_add') return price * (1 + v / 100);
  if (type === 'pct_deduct') return price * (1 - v / 100);
  if (type === 'abs_add') return price + v;
  return price;
}

/* P2 3열 카드: 기준가/수수료/운임/커스텀옵션 변경 시 가격 재계산 — 확정 옵션 + 미확정 초안 행 모두 상단 메인 금액에 반영 */
function recalcP2Col(col) {
  const base    = parseFloat(document.getElementById('p2ci-base-' + col)?.value || 0);
  const fee     = parseFloat(document.getElementById('p2ci-fee-' + col)?.value || 0);
  const freight = parseFloat(document.getElementById('p2ci-freight-' + col)?.value || 1);

  let price = base * (1 - fee / 100) * freight;

  const opts = _p2ColData[col]?.opts || [];
  for (const opt of opts) {
    price = _p2ApplyOptToPrice(price, opt.type, opt.value);
  }
  const drafts = _p2ColPendingDrafts[col] || [];
  for (const d of drafts) {
    const rid = String(d.id).replace(/[^a-zA-Z0-9_-]/g, '') || 'd0';
    const type = document.getElementById('p2c-newtype-' + col + '-' + rid)?.value || 'pct_deduct';
    const raw = document.getElementById('p2c-newval-' + col + '-' + rid)?.value;
    if (raw === undefined || raw === '') continue;
    price = _p2ApplyOptToPrice(price, type, raw);
  }
  price = Math.max(0, price);

  // 기준가·수수료·운임은 모두 USD 기준 가격에 곱하기/나누기(비율)로 적용. 보조줄은 1 USD = usd_krw KRW.
  const usdKrw = Number(_p2ScenarioRaw.usd_krw) || 0;
  const krwTxt = usdKrw > 0 ? `${Math.round(price * usdKrw).toLocaleString('ko-KR')} KRW` : '—';

  const priceEl = document.getElementById('p2c-price-' + col);
  const subEl   = document.getElementById('p2c-sub-' + col);
  if (priceEl) priceEl.textContent = price.toFixed(2);
  if (subEl)   subEl.textContent   = `${price.toFixed(2)} USD · ${krwTxt}`;
}

/* P2 컬럼 커스텀 옵션 렌더링 — 확정 옵션 + 미확정 초안 행(여러 줄 가능) */
function renderP2ColOptions(col) {
  const container = document.getElementById('p2co-' + col);
  if (!container) return;
  const opts = (_p2ColData[col] || { opts: [] }).opts;
  const drafts = _p2ColPendingDrafts[col] || [];

  const typeLabel = { pct_add: '% 가산', pct_deduct: '% 차감', abs_add: 'USD 가산' };

  /* 표시만 역순: 나중에 추가한 옵션이 운임 입력에 가깝게(목록 위쪽) 쌓임. 적용 순서(recalc)는 배열 순서 유지 */
  const displayOpts = opts.slice().reverse();

  let html = displayOpts.map(opt => `
    <div class="p2c-opt-row">
      <span class="p2c-opt-name">${_escHtml(opt.name)}</span>
      <span class="p2c-opt-type-label">${typeLabel[opt.type] || opt.type}</span>
      <input class="p2c-opt-val" type="number" value="${opt.value}" step="0.1" min="0"
        onchange="updateP2ColOption('${col}','${_escHtml(opt.id)}',this.value)">
      <button type="button" class="p2c-opt-del" onclick="removeP2ColOption('${col}','${_escHtml(opt.id)}')">×</button>
    </div>`).join('');

  drafts.forEach((d) => {
    const rid = String(d.id).replace(/[^a-zA-Z0-9_-]/g, '') || 'd0';
    html += `
      <div class="p2c-opt-row p2c-add-row">
        <input class="p2c-opt-name-input" type="text" placeholder="옵션명" id="p2c-newname-${col}-${rid}" maxlength="20">
        <select class="p2c-opt-type-select" id="p2c-newtype-${col}-${rid}" onchange="recalcP2Col('${col}')">
          <option value="pct_deduct">% 차감</option>
          <option value="pct_add">% 가산</option>
          <option value="abs_add">USD 가산</option>
        </select>
        <input class="p2c-opt-val" type="number" placeholder="값" id="p2c-newval-${col}-${rid}" step="0.1" min="0"
          oninput="recalcP2Col('${col}')">
        <button type="button" class="p2c-confirm-btn" onclick="confirmP2ColOption('${col}','${rid}')">✓</button>
        <button type="button" class="p2c-draft-cancel" onclick="cancelP2ColDraft('${col}','${rid}')" title="이 줄 취소">×</button>
      </div>`;
  });

  container.innerHTML = html;
  recalcP2Col(col);
}

/** '+ 옵션 추가' — 미확정 입력 행을 하나 더 쌓음(서버/저장 없음) */
function addP2ColOption(col) {
  _p2ColPendingDrafts[col] = _p2ColPendingDrafts[col] || [];
  _p2ColPendingDrafts[col].push({ id: 'd' + Date.now() + Math.random().toString(36).slice(2, 10) });
  renderP2ColOptions(col);
}

/** 미확정 행 제거 */
function cancelP2ColDraft(col, draftId) {
  const arr = _p2ColPendingDrafts[col] || [];
  _p2ColPendingDrafts[col] = arr.filter((d) => String(d.id) !== String(draftId));
  renderP2ColOptions(col);
}

/** 입력 확정 — 해당 초안 행만 반영 후 제거 */
function confirmP2ColOption(col, draftId) {
  const did = String(draftId);
  const name = (document.getElementById('p2c-newname-' + col + '-' + did)?.value || '').trim();
  const type = document.getElementById('p2c-newtype-' + col + '-' + did)?.value || 'pct_deduct';
  const val  = parseFloat(document.getElementById('p2c-newval-' + col + '-' + did)?.value || '0');
  if (!name || Number.isNaN(val) || val < 0) return;
  _p2ColData[col] = _p2ColData[col] || { opts: [] };
  _p2ColData[col].opts.push({ id: 'o' + Date.now(), name, type, value: val });
  _p2ColPendingDrafts[col] = (_p2ColPendingDrafts[col] || []).filter((d) => String(d.id) !== did);
  renderP2ColOptions(col);
}

/* 옵션 삭제 */
function removeP2ColOption(col, optId) {
  if (!_p2ColData[col]) return;
  _p2ColData[col].opts = _p2ColData[col].opts.filter(o => o.id !== optId);
  renderP2ColOptions(col);
}

/* 옵션 값 수정 */
function updateP2ColOption(col, optId, newVal) {
  if (!_p2ColData[col]) return;
  const opt = _p2ColData[col].opts.find(o => o.id === optId);
  if (opt) { opt.value = parseFloat(newVal) || 0; recalcP2Col(col); }
}

function _renderP2AiResult(data) {
  // 호주 /api/p2/pipeline/result 응답 (render_api.py 1892~1923 줄):
  //   extracted.{product_name, ref_price_text, ref_price_aud, verdict}
  //   analysis.{final_price_aud, formula_str, rationale, scenarios[{name, price_aud, reason}]}
  //   exchange_rates.{aud_krw, aud_usd}
  //   pdf
  //
  // UI 표시 정책 (Stage 0 Q2 결정):
  //   · 모든 가격은 USD 메인 + ≈ KRW 보조
  //   · 원본 AUD 는 _p2ScenarioRaw + window._exchangeRates 에 보존 (디버깅·Stage 4 재계산용)
  const extracted = data?.extracted || {};
  const analysis = data?.analysis || {};
  // 응답의 exchange_rates 우선, 없으면 전역 fallback (loadExchange 저장본)
  const rates = data?.exchange_rates || window._exchangeRates || {};
  const scenarios = Array.isArray(analysis.scenarios) ? analysis.scenarios : [];
  const resultSection = document.getElementById('p2-ai-result-section');
  if (resultSection) resultSection.style.display = '';

  // AUD → USD/KRW 파생 환산 유틸
  const audUsd = Number(rates.aud_usd) || 0;
  const audKrw = Number(rates.aud_krw) || 0;
  const usdKrw = audUsd > 0 ? audKrw / audUsd : 0;
  const audToUsd = (aud) => (audUsd > 0 ? Number(aud || 0) * audUsd : 0);
  const audToKrw = (aud) => Number(aud || 0) * audKrw;
  const fmtUSD = (v) => `USD ${Number(v || 0).toFixed(2)}`;
  const fmtKRW = (v) => {
    const n = Number(v || 0);
    if (n >= 1e8) return `${(n / 1e8).toFixed(2)}억원`;
    if (n >= 1e4) return `${(n / 1e4).toFixed(1)}만원`;
    return `${Math.round(n).toLocaleString('ko-KR')}원`;
  };

  // 제품명
  _setText('p2r-product-name', extracted.product_name || '미상');

  // 판정 배지 — 호주 export_viable('viable'/'conditional'/'not_viable') → 한국어
  const verdictEl = document.getElementById('p2r-verdict-badge');
  if (verdictEl) {
    const evMap = { 'viable': '적합', 'conditional': '조건부', 'not_viable': '부적합' };
    const v = evMap[extracted.verdict] || extracted.verdict || '미상';
    const vc = v === '적합' ? 'v-ok' : v === '부적합' ? 'v-err' : v !== '미상' ? 'v-warn' : 'v-none';
    verdictEl.className = `verdict-badge ${vc}`;
    verdictEl.textContent = v;
  }

  // 참조가 — 호주는 AUD 기준. ref_price_text 우선, 없으면 AUD 원본 + USD 환산 표기
  const refAud = Number(extracted.ref_price_aud) || 0;
  _setText('p2r-ref-price-text',
    extracted.ref_price_text
      || (refAud > 0 ? `AUD ${refAud.toFixed(2)} (≈ ${fmtUSD(audToUsd(refAud))})` : '추출값 없음'));

  // 환율 표시 — USD/KRW 메인 + AUD/KRW 보조 (FOB 역산 참고값)
  let rateText = '환율 정보 없음';
  if (usdKrw > 0) {
    rateText = `1 USD = ${usdKrw.toFixed(2)} KRW`;
    if (audKrw > 0) rateText += ` · 1 AUD = ${audKrw.toFixed(2)} KRW`;
  }
  _setText('p2r-exchange', rateText);

  // 최종 권고가 — USD 메인 + KRW 보조 (Q2-a 스타일)
  const finalAud = Number(analysis.final_price_aud) || 0;
  const finalUsd = audToUsd(finalAud);
  const finalKrw = audToKrw(finalAud);
  const finalEl = document.getElementById('p2r-final-price');
  if (finalEl) {
    finalEl.innerHTML =
      `<span>${fmtUSD(finalUsd)}</span>` +
      `<span style="font-size:12px;color:var(--muted);margin-left:8px;">≈ ${fmtKRW(finalKrw)}</span>`;
  }

  // 시나리오 리스트 (p2r-scenarios) — USD + KRW
  const scenEl = document.getElementById('p2r-scenarios');
  if (scenEl) {
    if (scenarios.length) {
      scenEl.innerHTML = scenarios.map((s, idx) => {
        const cls = idx === 0 ? 'agg' : idx === 1 ? 'avg' : 'cons';
        const scAud = Number(s.price_aud || 0);
        const scUsd = audToUsd(scAud);
        const scKrw = audToKrw(scAud);
        const impPct = _p2ImporterMarginPctFromAiScenario(s, idx);
        const impLine = impPct != null
          ? `<div class="p2-scenario-margin" style="font-size:11px;color:var(--muted);margin-top:2px;">수입상 마진 ${impPct}%</div>`
          : '';
        return `
          <div class="p2-scenario p2-scenario--${cls}">
            <div class="p2-scenario-top">
              <span class="p2-scenario-name">${_escHtml(String(s.name || `시나리오 ${idx + 1}`))}</span>
              <span class="p2-scenario-price">${fmtUSD(scUsd)}
                <span style="font-size:11px;color:var(--muted);margin-left:4px;">≈ ${fmtKRW(scKrw)}</span>
              </span>
            </div>
            ${impLine}
          </div>`;
      }).join('');
    } else {
      scenEl.innerHTML = '<div class="p2-note">시나리오 데이터가 없습니다.</div>';
    }
  }

  // 산정 이유
  _setText('p2r-rationale', analysis.rationale || '산정 이유 없음');

  // 다운로드 링크 (호주 /api/report/download 그대로 호환)
  const dlState = document.getElementById('p2-report-dl-state');
  if (dlState) {
    if (data?.pdf) {
      dlState.innerHTML = `
        <a class="btn-download"
           href="/api/report/download?name=${encodeURIComponent(data.pdf)}"
           target="_blank">📄 수출전략 보고서 다운로드</a>`;
    } else {
      dlState.innerHTML = `<span style="font-size:13px;color:var(--red);">PDF 생성에 실패했습니다.</span>`;
    }
  }

  // ── 3열 시나리오 카드 (Stage 1 HTML `p2-three-col`) — USD 메인 + KRW 보조 ──
  const cols = ['agg', 'avg', 'cons'];
  scenarios.forEach((s, i) => {
    const col = cols[i];
    if (!col) return;
    const priceAud = Number(s.price_aud || 0);
    const priceUsd = audToUsd(priceAud);
    const priceKrw = audToKrw(priceAud);

    // 전역 저장소 — Stage 4 직접입력 탭 recalcP2Col() 재계산 기준값 (USD)
    _p2ScenarioRaw[col]    = priceUsd;
    _p2ScenarioRaw.aud_usd = audUsd;
    _p2ScenarioRaw.aud_krw = audKrw;
    _p2ScenarioRaw.usd_krw = usdKrw;
    // 원본 AUD 도 함께 보존 (디버깅·보고서 재검증용)
    _p2ScenarioRaw[col + '_aud'] = priceAud;

    const priceEl   = document.getElementById('p2c-price-' + col);
    const subEl     = document.getElementById('p2c-sub-' + col);
    const baseInput = document.getElementById('p2ci-base-' + col);

    if (priceEl)   priceEl.textContent = priceUsd.toFixed(2);
    if (baseInput) baseInput.value     = priceUsd.toFixed(2);
    if (subEl)     subEl.textContent   = `≈ ${fmtKRW(priceKrw)}`;

    // 새 AI 결과 올 때마다 각 컬럼의 커스텀 옵션·미확정 초안 행 초기화
    _p2ColData[col] = { opts: [] };
    _p2ColPendingDrafts[col] = [];
    renderP2ColOptions(col);
  });

  _p2ScenarioRaw.usd_krw = usdKrw;
  ['agg', 'avg', 'cons'].forEach((c) => recalcP2Col(c));

  _p2UpdateImporterMarginHints(scenarios);

  // 경쟁가 분포 (DOM 있으면 USD 기준 표기 — Stage 1 HTML 에는 없음, 향후 추가 대비)
  if (scenarios.length >= 3) {
    const prices = scenarios.map(s => audToUsd(Number(s.price_aud || 0))).sort((a, b) => a - b);
    _setText('p2-dist-p25', `${prices[0].toFixed(2)} USD`);
    _setText('p2-dist-med', `${prices[1].toFixed(2)} USD`);
    _setText('p2-dist-p75', `${prices[2].toFixed(2)} USD`);
  }

  // 제품 목록 (추출된 product_name 기준)
  const prodList = document.getElementById('p2-product-list');
  if (prodList && extracted.product_name) {
    prodList.innerHTML = `
      <table class="p2-prod-table">
        <thead><tr><th>제품</th><th>참조가 (원문)</th><th>출처</th></tr></thead>
        <tbody>
          <tr>
            <td>${_escHtml(extracted.product_name || '—')}</td>
            <td>${_escHtml(extracted.ref_price_text || '—')}</td>
            <td>report</td>
          </tr>
        </tbody>
      </table>`;
  }
}

function _p2FillExchangeRate() {
  const rates = window._exchangeRates;
  if (!rates) return;
  const sgdUsd = Number(rates.sgd_usd);
  if (!sgdUsd || sgdUsd <= 0) return;
  const usdToSgd = Number((1 / sgdUsd).toFixed(4));
  ['public', 'private'].forEach((seg) => {
    const opt = _p2Manual[seg].find((x) => x.key === 'exchange');
    if (opt) opt.value = usdToSgd;
  });
}

function _p2FillBaseFromReport() {
  const report = _getP2SelectedReport();
  if (!report) return;
  // 1순위: 저장된 숫자형 가격 힌트 (pbs_sgd_hint 키 — 보고서 생성 당시 저장된 AUD/USD 숫자, 단위는 보고서 구조에 의존)
  const numHint = report.pbs_sgd_hint;
  const hint = (numHint != null && !Number.isNaN(Number(numHint)) && Number(numHint) > 0)
    ? Number(numHint)
    : _extractPriceHint(report.price_hint || '');
  if (!Number.isNaN(hint) && hint > 0) {
    const pub = _p2Manual.public.find((x) => x.key === 'base_price');
    const pri = _p2Manual.private.find((x) => x.key === 'base_het');
    if (pub) pub.value = hint;
    if (pri) pri.value = hint;
  }
}

function _syncP2ReportsOptions() {
  if (!_p2Ready) return;
  const reports = _loadReports();
  const reportOpts = reports
    .map((r) => `<option value="${r.id}">${_escHtml(r.report_title || r.product || '보고서')}</option>`)
    .join('');
  const manualOptionHtml = `<option value="">보고서를 선택하세요</option>${reportOpts}`;
  const aiOptionHtml =
    '<option value="">01 시장조사에서 생성된 보고서가 여기에 자동 반영됩니다 (최근 24시간).</option>' + reportOpts;

  const manualSelect = document.getElementById('p2-report-select');
  if (manualSelect) {
    const curr = _p2SelectedReportId;
    manualSelect.innerHTML = manualOptionHtml;
    _p2SelectedReportId = reports.some((r) => String(r.id) === String(curr)) ? curr : '';
    manualSelect.value = _p2SelectedReportId;
  }

  const aiSelect = document.getElementById('p2-ai-report-select');
  if (aiSelect) {
    const curr = _p2AiSelectedReportId;
    aiSelect.innerHTML = aiOptionHtml;
    _p2AiSelectedReportId = reports.some((r) => String(r.id) === String(curr)) ? curr : '';
    aiSelect.value = _p2AiSelectedReportId;
  }

}

function _getP2SelectedReport() {
  if (!_p2SelectedReportId) return null;
  return _loadReports().find((r) => String(r.id) === String(_p2SelectedReportId)) || null;
}

function _extractPriceHint(text) {
  // 호주 시장조사 보고서 텍스트에서 가격 힌트 추출.
  // 우선순위: AUD(호주 원본) > USD > $ 표기.
  // 반환값은 입력 단위 그대로의 숫자 — 직접입력 탭은 USD 기준이라 AUD 검출 시 호출자가 환산해야 함.
  const src = String(text || '');
  // ① AUD 범위 표기 (예: "AUD 30.00 ~ 45.00")
  const mAudRange = src.match(/AUD\s*([0-9]+(?:\.[0-9]+)?)\s*[~\-–]\s*([0-9]+(?:\.[0-9]+)?)/i);
  if (mAudRange) return (Number(mAudRange[1]) + Number(mAudRange[2])) / 2;
  // ② AUD 단일 (예: "AUD 48.11", "1 AUD = ...")
  const mAudSingle = src.match(/AUD\s*([0-9]+(?:\.[0-9]+)?)/i);
  if (mAudSingle) return Number(mAudSingle[1]);
  // ③ USD 표기 (직접입력 탭과 동일 단위)
  const mUsd = src.match(/(?:\$|USD\s+)([0-9]+(?:\.[0-9]+)?)/i);
  if (mUsd) return Number(mUsd[1]);
  return NaN;
}

function _calcP2Manual() {
  // 호주 직접입력 탭 KUP(FOB) 산정 — USD 기준.
  // 공공: base × exchange(AUD→USD) × ratio% + custom opts
  // 민간: base ÷ (1+GST%) × (1−pharmacy%) × (1−wholesale%) × (1−importer%) + custom opts
  //   GST 는 _p2ClassifyGst 로 품목별 자동 전환 (처방약 0%, OTC 10%).
  const seg = _p2ManualSeg;
  const options = _p2Manual[seg].filter((x) => x.enabled);
  if (seg === 'public') {
    const base = Number(options.find((x) => x.key === 'base_price')?.value || 0);
    const ex = Number(options.find((x) => x.key === 'exchange')?.value || 1);
    const ratio = Number(options.find((x) => x.key === 'pub_ratio')?.value || 30);
    let price = base * ex * (ratio / 100);
    const parts = [`USD ${base.toFixed(2)}`, `× ${ex.toFixed(4)}`, `× ${ratio}%`];
    options.forEach((opt) => {
      if (opt.type === 'pct_add_custom') {
        price *= (1 + Number(opt.value) / 100);
        parts.push(`× (1+${Number(opt.value).toFixed(1)}%)`);
      } else if (opt.type === 'abs_add_custom') {
        price += Number(opt.value);
        parts.push(`+ USD ${Number(opt.value).toFixed(2)}`);
      }
    });
    return { kup: Math.max(price, 0), formulaStr: `${parts.join('  ')}  =  KUP  USD ${Math.max(price, 0).toFixed(2)}` };
  }

  // 민간 (Logic B) — 호주 fob_calculator.py 공식 맞춤:
  //   FOB = Retail ÷ (1+GST) ÷ (1+pharmacy) ÷ (1+wholesale) ÷ (1+importer)
  // 원본 싱가포르 로직은 (1-margin%) 차감 방식이었으나, 호주는 (1+margin) 나눗셈.
  // 호환성 유지를 위해 pct_deduct 는 (1+margin) ÷ 로 해석.
  let price = 0;
  const parts = [];
  options.forEach((opt) => {
    if (opt.key === 'base_het') {
      price = Number(opt.value);
      parts.push(`USD ${price.toFixed(2)}`);
    } else if (opt.key === 'exchange' && Number(opt.value) !== 1) {
      price *= Number(opt.value);
      parts.push(`× ${Number(opt.value).toFixed(4)}`);
    } else if (opt.type === 'gst_fixed') {
      // 호주 GST: 처방약 0% → ÷1.00, OTC 10% → ÷1.10. 동적 반영.
      const gstRate = Number(opt.value) || 0;
      const divisor = 1 + gstRate / 100;
      price /= divisor;
      parts.push(`÷ ${divisor.toFixed(2)} (GST ${gstRate}%)`);
    } else if (opt.type === 'pct_deduct') {
      // 호주 공식 — 나눗셈 방식 (stage2 fob_calculator.calculate_fob_logic_b 와 일치)
      const marginRate = Number(opt.value) || 0;
      const divisor = 1 + marginRate / 100;
      price /= divisor;
      parts.push(`÷ ${divisor.toFixed(3)} (${opt.label || ''} ${marginRate}%)`);
    } else if (opt.type === 'pct_add_custom') {
      price *= (1 + Number(opt.value) / 100);
      parts.push(`× (1+${Number(opt.value).toFixed(1)}%)`);
    } else if (opt.type === 'abs_add_custom') {
      price += Number(opt.value);
      parts.push(`+ USD ${Number(opt.value).toFixed(2)}`);
    }
  });
  return { kup: Math.max(price, 0), formulaStr: `${(parts.join('  ') || 'USD 0.00')}  =  FOB  USD ${Math.max(price, 0).toFixed(2)}` };
}

function _renderP2Manual() {
  const wrapEl    = document.getElementById('p2-manual-options');
  const removedEl = document.getElementById('p2-manual-removed');
  if (!wrapEl || !removedEl) return;

  const options = _p2Manual[_p2ManualSeg];
  const active  = options.filter((x) => x.enabled);
  const inactive = options.filter((x) => !x.enabled);
  wrapEl.innerHTML = active.map((opt) => _p2OptionCardHtml(opt)).join('');
  _bindP2OptionEvents(wrapEl, options);

  removedEl.innerHTML = inactive.length
    ? `<span class="p2-removed-label">복원:</span>${inactive.map((opt) => `<button class="p2-add-btn" data-p2-op="add" data-key="${_escHtml(opt.key)}" type="button">+ ${_escHtml(opt.label)}</button>`).join('')}`
    : '';
  removedEl.querySelectorAll('[data-p2-op="add"]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const item = options.find((x) => x.key === btn.getAttribute('data-key'));
      if (item) { item.enabled = true; _renderP2Manual(); }
    });
  });

  _renderP2CustomAddSection();

  const calc = _calcP2Manual();
  const agg  = calc.kup * 0.9;
  const avg  = calc.kup;
  const cons = calc.kup * 1.1;
  const aggReason  = _p2ManualScenarioReason('aggressive',   _p2ManualSeg);
  const avgReason  = _p2ManualScenarioReason('average',      _p2ManualSeg);
  const consReason = _p2ManualScenarioReason('conservative', _p2ManualSeg);
  const aggFormula  = `FOB USD ${calc.kup.toFixed(2)} × 0.90 = USD ${agg.toFixed(2)}`;
  const avgFormula  = `FOB USD ${avg.toFixed(2)} (기준가 그대로)`;
  const consFormula = `FOB USD ${calc.kup.toFixed(2)} × 1.10 = USD ${cons.toFixed(2)}`;
  _p2LastScenarios = { mode: 'manual', seg: _p2ManualSeg, base: calc.kup, agg, avg, cons, formulaStr: calc.formulaStr, aggReason, avgReason, consReason, aggFormula, avgFormula, consFormula, rationaleLines: [] };
}

function _p2OptionCardHtml(opt) {
  const isFixed = opt.type === 'gst_fixed';

  // 입력 필드 값 포맷
  const inputVal = opt.unit === 'rate' ? Number(opt.value).toFixed(4)
                 : opt.unit === '%'    ? Number(opt.value).toFixed(0)
                 :                       Number(opt.value).toFixed(2);
  // 단위 표시
  const unitLabel = opt.unit === '%' ? '%' : opt.unit === 'rate' ? '' : 'USD';

  return `
    <div class="p2-step-card">
      <div class="p2-step-header">
        <button class="p2-step-toggle" data-p2-op="toggle" data-key="${_escHtml(opt.key)}" type="button">
          <span class="p2-step-label-text">${_escHtml(opt.label)}</span>
          <span class="p2-step-arrow">${opt.expanded ? '▾' : '▸'}</span>
        </button>
        <div class="p2-step-controls">
          ${isFixed
            ? `<span class="p2-step-val-display">÷ 1.09 고정</span>`
            : `${unitLabel ? `<span class="p2-step-unit-label" style="font-size:12px;color:var(--muted);margin-right:2px;">${_escHtml(unitLabel)}</span>` : ''}
               <input class="p2-step-input" type="number" data-p2-op="input" data-key="${_escHtml(opt.key)}" value="${inputVal}" step="${opt.step}" min="${opt.min}">`
          }
          ${opt.fixed ? '' : `<button class="p2-del-btn" data-p2-op="del" data-key="${_escHtml(opt.key)}" type="button" title="옵션 제거">×</button>`}
        </div>
      </div>
      ${opt.expanded ? `<div class="p2-step-body"><div class="p2-step-hint">${_escHtml(opt.hint || '')}</div><div class="p2-step-rationale">${_escHtml(opt.rationale || '')}</div></div>` : ''}
    </div>`;
}

function _bindP2OptionEvents(wrap, options) {
  wrap.querySelectorAll('[data-p2-op]').forEach((el) => {
    const op = el.getAttribute('data-p2-op');
    const key = el.getAttribute('data-key');
    const item = options.find((x) => x.key === key);
    if (!item) return;

    if (op === 'toggle') {
      el.addEventListener('click', () => {
        item.expanded = !item.expanded;
        _renderP2Manual();
      });
    } else if (op === 'del') {
      el.addEventListener('click', () => {
        item.enabled = false;
        item.expanded = false;
        _renderP2Manual();
      });
    } else if (op === 'input') {
      el.addEventListener('input', () => {
        const v = parseFloat(el.value);
        if (!Number.isNaN(v)) item.value = Math.max(item.min, v);
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
      <input class="p2-custom-input" id="p2c-label" type="text" placeholder="옵션명" maxlength="30" style="flex:2">
      <select class="p2-custom-type-select" id="p2c-type">
        <option value="pct_deduct">% 차감</option>
        <option value="pct_add_custom">% 가산</option>
        <option value="abs_add_custom">USD 가산</option>
      </select>
      <input class="p2-custom-input" id="p2c-val" type="number" placeholder="값" step="0.1" min="0" max="999" style="width:80px;flex:0 0 80px">
      <button class="p2-add-custom-btn" id="p2c-add" type="button">+ 추가</button>
    </div>`;
  document.getElementById('p2c-add')?.addEventListener('click', () => {
    const label = (document.getElementById('p2c-label')?.value || '').trim();
    const type = document.getElementById('p2c-type')?.value || 'pct_deduct';
    const val = parseFloat(document.getElementById('p2c-val')?.value || '0');
    if (!label || Number.isNaN(val) || val < 0) return;
    _p2Manual[_p2ManualSeg].push({
      key: `custom_${Date.now()}`,
      label,
      value: val,
      type,
      unit: type === 'abs_add_custom' ? 'USD' : '%',
      step: type === 'abs_add_custom' ? 0.1 : 1,
      min: 0,
      max: type === 'abs_add_custom' ? 9999 : 100,
      enabled: true,
      fixed: false,
      expanded: false,
      hint: '사용자 추가 옵션',
      rationale: '',
    });
    _resetP2ManualResultView();
    _renderP2Manual();
  });
}

function _p2ManualScenarioReason(type, seg) {
  if (type === 'aggressive') {
    return seg === 'public'
      ? '저마진 포지셔닝 — 시장 진입 초기, 자사가 손해를 감수하며 가격경쟁력을 앞세워 점유율을 선점합니다.'
      : '저마진 포지셔닝 — 민간 채널 초기 진입 시 자사 손해를 감수해 가격 경쟁력을 확보하고 처방·입고 채널을 빠르게 확대합니다.';
  }
  if (type === 'average') {
    return '중간 포지셔닝 — 현재 입력 옵션을 그대로 반영한 기본 산정가입니다. 리스크와 마진의 균형을 유지하는 표준 전략입니다.';
  }
  return seg === 'public'
    ? '고마진 포지셔닝 — 자사 제품이 시장 내 자리를 잡은 이후, 마진율을 높여 이익 확대를 노리는 전략입니다.'
    : '고마진 포지셔닝 — 제품이 민간 시장에 자리잡은 후 마진율을 높여 이익 확대를 노립니다. 브랜드 포지셔닝이 확립된 단계에 적합합니다.';
}

async function _generateP2Pdf() {
  const btn = document.getElementById('p2-pdf-btn-manual');
  const stateEl = document.getElementById('p2-pdf-state-manual');
  const sc = _p2LastScenarios;
  if (!sc) {
    if (stateEl) stateEl.textContent = '먼저 시나리오를 산정해 주세요.';
    return;
  }

  if (btn) {
    btn.disabled = true;
    btn.textContent = '생성 중…';
  }
  if (stateEl) stateEl.textContent = '';

  try {
    const report = _getP2SelectedReport();
    const body = {
      product_name: report ? (report.report_title || report.product || '제품명 미상') : '제품명 미상',
      verdict: report ? (report.verdict || '—') : '—',
      seg_label: sc.seg === 'public' ? '공공 시장' : '민간 시장',
      base_price: sc.base,
      formula_str: sc.formulaStr,
      mode_label: '직접 입력',
      scenarios: [
        { label: '공격', price: sc.agg,  reason: sc.aggReason  || '', formula: sc.aggFormula  || '' },
        { label: '평균', price: sc.avg,  reason: sc.avgReason  || '', formula: sc.avgFormula  || '' },
        { label: '보수', price: sc.cons, reason: sc.consReason || '', formula: sc.consFormula || '' },
      ],
      ai_rationale: [],
    };
    const res = await fetch('/api/p2/report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.pdf) throw new Error(data.detail || `HTTP ${res.status}`);
    if (stateEl) {
      stateEl.innerHTML = `<a class="btn-download" href="/api/report/download?name=${encodeURIComponent(data.pdf)}" target="_blank" style="font-size:12px;padding:6px 14px;">다운로드</a>`;
    }
  } catch (err) {
    if (stateEl) stateEl.textContent = `생성 실패: ${err.message}`;
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'PDF 생성';
    }
  }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §7. API 키 상태 (U1) — GET /api/keys/status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

async function loadKeyStatus() {
  // 호주: /api/keys/status 엔드포인트 없음 (Stage 0 Q2 → 섹션 삭제).
  // API 키 배지 UI 자체가 Stage 1 HTML 에서 제거됨. 호출 시도조차 불필요.
  return;
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
    const dot = el.querySelector('.prog-dot');
    if (dot) dot.textContent = String(i + 1);
  }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §8. 파이프라인 실행 & 폴링
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/**
 * 선택 품목 파이프라인 실행.
 * U6: 재분석 버튼도 이 함수를 호출.
 */
/**
 * 호주 시장조사 파이프라인 — 2 단 동기 플로우
 *
 * 싱가포르는 POST /api/pipeline/{key} 가 비동기로 모든 단계를 서버에서 처리하고
 * GET /status 폴링 + GET /result 로 결과를 받았음.
 *
 * 호주 render_api.py 는 동기 엔드포인트 2 개로 분리됨:
 *   ① POST /api/crawl {product_id}
 *        → au_crawler.main() 실행 (TGA·PBS·Chemist·NSW·Healthylife 크롤링)
 *        → Supabase `australia` 테이블 upsert
 *        → 완료까지 수 초 ~ 수십 초 블로킹 (SystemExit catch 구조)
 *   ② POST /api/report/generate {product_id}
 *        → Claude Haiku 로 block2/block3 생성 + 하이브리드 논문 검색 + PDF 생성
 *        → 응답 JSON: { ok, product_id, row, blocks, refs_count, refs, meta, pdf }
 *        → 동기, 완료까지 5~30 초 블로킹
 *
 * 프론트는 await 2 회 + 진행률 업데이트만 수행. 폴링 불필요.
 */
async function runPipeline() {
  const productId = document.getElementById('product-select').value;
  _currentKey     = productId;

  // UI 초기화
  resetProgress();
  _hideP1Note();
  document.getElementById('result-card')?.classList.remove('visible');
  document.getElementById('papers-card')?.classList.remove('visible');
  _showReportIdle();
  const analyzeBtn = document.getElementById('btn-analyze');
  if (analyzeBtn) analyzeBtn.disabled = true;
  const iconEl = document.getElementById('btn-icon');
  if (iconEl) iconEl.textContent = '⏳';

  const reBtn = document.getElementById('btn-reanalyze');
  if (reBtn) reBtn.style.display = 'none';

  try {
    // ① 크롤링 실행 (동기 · 블로킹) — PDF 카드는 완료·오류 시에만 표시
    setProgress('db_load', 'running');
    const r1 = await fetch('/api/crawl', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ product_id: productId }),
    });
    if (!r1.ok) {
      const d1 = await r1.json().catch(() => ({}));
      console.error('크롤링 실패:', d1.detail || r1.status);
      setProgress('db_load', 'error');
      _showReportError();
      _resetBtn();
      return;
    }
    setProgress('db_load', 'done');

    // ② AI 분석 + 논문 검색 + PDF 생성 (동기)
    setProgress('analyze', 'running');
    const r2 = await fetch('/api/report/generate', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ product_id: productId }),
    });
    if (!r2.ok) {
      const d2 = await r2.json().catch(() => ({}));
      const detailStr =
        typeof d2.detail === 'string'
          ? d2.detail
          : (d2.detail != null ? JSON.stringify(d2.detail) : '');
      console.error('AI 분석/PDF 실패:', d2.detail || r2.status);
      setProgress('analyze', 'error');
      _showReportError();
      const msg = (detailStr || `HTTP ${r2.status}`).slice(0, 800);
      _showP1Note(`⚠️ 보고서 생성 실패 — ${msg}`, true);
      if (typeof showToast === 'function') {
        showToast(`보고서 API ${r2.status}: ${msg.slice(0, 200)}`, 'warn');
      }
      _resetBtn();
      return;
    }
    const reportData = await r2.json();
    // reportData: { ok, product_id, blocks, refs_count, refs, meta, pdf }  (row 없음)
    // · blocks : Claude Haiku 생성 block2_* / block3_* / block4_regulatory
    // · refs   : 하이브리드 논문 검색 결과
    // · meta   : { export_viable, reason_code, confidence, confidence_breakdown }
    // · pdf    : 파일명 (GET /api/report/download?name=... 로 다운로드)
    setProgress('analyze', 'done');
    setProgress('refs',    'done');   // 호주는 논문 검색이 report/generate 내부에 포함
    setProgress('report',  'done');

    // ③ 호주 Supabase row 별도 조회 (report/generate 응답에 row 없음)
    const r3 = await fetch(`/api/data/${encodeURIComponent(productId)}`);
    let auRow = null;
    if (r3.ok) {
      auRow = await r3.json();
    } else {
      console.warn('auRow 조회 실패:', r3.status, '— blocks/meta 만으로 렌더링 시도');
    }

    // ④ 호주 3응답 (auRow + blocks + meta) → 싱가포르 result shape 어댑터 변환 후 렌더
    const renderShape = _auToRenderResult(auRow, reportData.blocks, reportData.meta);
    renderResult(renderShape, reportData.refs, reportData.pdf);
    _resetBtn();
  } catch (e) {
    console.error('파이프라인 요청 실패:', e);
    setProgress('db_load', 'error');
    _showReportError();
    _resetBtn();
  }
}

function _resetBtn() {
  const analyzeBtn = document.getElementById('btn-analyze');
  if (analyzeBtn) analyzeBtn.disabled = false;
  const iconEl = document.getElementById('btn-icon');
  if (iconEl) iconEl.textContent = '▶';
}

// pollPipeline() 제거됨 — 호주는 동기 엔드포인트라 폴링 불필요.
// 기존 _pollTimer / STEP_ORDER 는 §1 상수 섹션에 남겨두되 미사용 (Stage 4 정리 대상).

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §9. 신약 분석 파이프라인
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

// 호주: 임의 신약용 /api/pipeline/custom 은 없음. UI는 팀원과 동일하게 두고 안내만 표시.
let _customPollTimer = null;
const CUSTOM_STEP_ORDER = ['analyze', 'refs', 'report'];

function _setCustomProgress() { /* 예약 */ }
function _resetCustomProgress() { /* 예약 */ }
function _resetCustomBtn() { /* 예약 */ }

function _showCustomDrugMsg(msg, isErr) {
  const el = document.getElementById('p1-custom-msg');
  if (!el) return;
  el.textContent = msg;
  el.style.display = 'block';
  el.className = 'p1-custom-msg' + (isErr ? ' err' : '');
}

/** Task 10 (2026-04-19) — 신약 직접 입력: /api/crawl/new-drug 백엔드 연동 */
async function runCustomPipeline() {
  const trade = (document.getElementById('custom-trade-name')?.value || '').trim();
  const inn = (document.getElementById('custom-inn')?.value || '').trim();
  const dosage = (document.getElementById('custom-dosage')?.value || '').trim();

  if (!trade || !inn || !dosage) {
    _showCustomDrugMsg('약품명·성분명·제형 3개 필드를 모두 입력해 주세요.', true);
    return;
  }

  _showCustomDrugMsg('신약 크롤링을 시작합니다. 잠시만 기다려 주세요… (약 1-2분 소요)', false);

  let data;
  try {
    const res = await fetch('/api/crawl/new-drug', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        product_name_ko: trade,
        inn: inn,
        strength_dosage_form: dosage,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      _showCustomDrugMsg(`신약 분석 요청 실패 (${res.status}): ${err.detail || res.statusText}`, true);
      return;
    }
    data = await res.json();
  } catch (exc) {
    _showCustomDrugMsg(`네트워크 오류: ${exc.message}`, true);
    return;
  }

  if (!data.job_id) {
    _showCustomDrugMsg('서버 응답 형식 오류 (job_id 없음).', true);
    return;
  }

  _pollCustomPipeline(data.job_id);
}

/** Task 10 — 신약 크롤 job 상태 폴링 (5초 간격, 최대 5분) */
async function _pollCustomPipeline(jobId) {
  const MAX_TRIES = 60;  // 5s × 60 = 300s
  for (let i = 0; i < MAX_TRIES; i += 1) {
    try {
      const res = await fetch(`/api/crawl/status/${jobId}`);
      if (res.ok) {
        const job = await res.json();
        if (job.status === 'done') {
          void _handleCustomCrawlResult(job);
          return;
        }
        if (job.status === 'failed') {
          _showCustomDrugMsg(`신약 분석 실패: ${job.error || '알 수 없는 오류'}`, true);
          return;
        }
        // queued / running — 진행 상태 표시
        _showCustomDrugMsg(
          `신약 크롤링 중… (${i + 1}/${MAX_TRIES}) 상태: ${job.status}`,
          false,
        );
      }
    } catch (exc) {
      // 네트워크 오류 시 재시도
    }
    await new Promise((r) => setTimeout(r, 5000));
  }
  _showCustomDrugMsg('신약 분석 시간 초과 — 백엔드 로그를 확인하세요.', true);
}

/** 신약 가격 유도 등 짧은 알림 (수출전략 업로드 안내용) */
function showToast(message, level) {
  const wrap = document.createElement('div');
  wrap.className = 'app-toast app-toast--' + (level || 'info');
  wrap.setAttribute('role', 'status');
  wrap.textContent = message;
  document.body.appendChild(wrap);
  requestAnimationFrame(() => wrap.classList.add('app-toast--visible'));
  const ms = level === 'warn' ? 5200 : 4000;
  setTimeout(() => {
    wrap.classList.remove('app-toast--visible');
    setTimeout(() => wrap.remove(), 320);
  }, ms);
}

/** Task 10 — 크롤 결과 분기: 가격 있음 → /api/stage2/calculate 로 FOB 연쇄 / 없음 → PDF 업로드 유도 */
async function _handleCustomCrawlResult(job) {
  if (job.needs_price_upload) {
    showToast(
      '호주 공개 DB에서 가격을 찾지 못했습니다. 수출가격 전략의 "가격 자료 PDF 업로드"에 PDF를 올려주세요.',
      'warn',
    );
    const p2body = document.getElementById('pb-p2');
    if (p2body && p2body.classList.contains('hidden')) toggleProcess('p2');
    if (typeof switchP2Tab === 'function') switchP2Tab('ai');
    const p2up = document.getElementById('p2-upload-area');
    if (p2up) {
      p2up.scrollIntoView({ behavior: 'smooth', block: 'center' });
      p2up.classList.add('p2-upload-highlight');
      setTimeout(() => p2up.classList.remove('p2-upload-highlight'), 2400);
    }
    return;
  }

  const code = job.product_code || '';
  const parsePos = (v) => {
    if (v == null || v === '') return 0;
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? n : 0;
  };
  const aempAud = parsePos(job.aemp_aud);
  const retailAud = parsePos(job.retail_price_aud);

  window._newDrugProductCode = code;
  window._newDrugLastJob = job;

  if (aempAud <= 0 && retailAud <= 0) {
    showToast('가격 필드(AEMP·소매)가 비어 있습니다. 가격 자료 PDF 업로드가 필요합니다.', 'warn');
    const p2body = document.getElementById('pb-p2');
    if (p2body && p2body.classList.contains('hidden') && typeof toggleProcess === 'function') {
      toggleProcess('p2');
    }
    if (typeof switchP2Tab === 'function') switchP2Tab('ai');
    return;
  }

  let payload = { product_id: code, overrides: {} };
  if (aempAud > 0) {
    payload.logic = 'A';
    payload.overrides = { base_aemp: aempAud, importer_margin: 20 };
  } else {
    payload.logic = 'B';
    payload.overrides = {
      base_retail: retailAud,
      importer_margin: 20,
      gst: 10,
      pharmacy_margin: 30,
      wholesale_margin: 10,
      is_pbs_listed_rx: false,
    };
  }

  try {
    const res = await fetch('/api/stage2/calculate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      showToast(`FOB 계산 실패 (${res.status}): ${err.detail || res.statusText}`, 'warn');
      _showCustomDrugMsg('FOB 역산 실패 — 수출가격 전략 탭에서 값을 확인하세요.', true);
      return;
    }
    const fob = await res.json();
    window._newDrugFobResult = fob;

    const rates = window._exchangeRates || {};
    const audUsd = Number(rates.aud_usd) > 0 ? Number(rates.aud_usd) : 0.64;
    const scenarios = fob.scenarios || [];
    const avgSc = scenarios.find((s) => s.name === 'average') || scenarios[1] || scenarios[0];
    const fobAud = avgSc && avgSc.fob_aud != null ? Number(avgSc.fob_aud) : NaN;
    const fobUsd = Number.isFinite(fobAud) ? (fobAud * audUsd).toFixed(2) : '?';

    const synReport = { product_id: code };
    if (typeof _p2ApplyGstForReport === 'function') _p2ApplyGstForReport(synReport);

    if (typeof _p2Manual !== 'undefined') {
      if (fob.logic === 'A' && Number.isFinite(fobAud)) {
        const pub = _p2Manual.public.find((x) => x.key === 'base_price');
        if (pub) pub.value = fobAud * audUsd;
      } else if (fob.logic === 'B' && retailAud > 0) {
        const pri = _p2Manual.private.find((x) => x.key === 'base_het');
        if (pri) pri.value = retailAud * audUsd;
      }
      if (typeof switchP2Tab === 'function') switchP2Tab('manual');
      const p2body = document.getElementById('pb-p2');
      if (p2body && p2body.classList.contains('hidden') && typeof toggleProcess === 'function') {
        toggleProcess('p2');
      }
      if (typeof _renderP2Manual === 'function') _renderP2Manual();
    }

    const w = (fob.warnings || []).filter(Boolean).join(' · ');
    showToast(
      `FOB 역산 완료 (Logic ${fob.logic}, α·GST 반영): 평균 시나리오 약 USD ${fobUsd}${w ? ' — ' + w.slice(0, 120) : ''}`,
      'info',
    );
    _showCustomDrugMsg(
      `신약 분석 완료 — ${code}. FOB 평균 USD ${fobUsd} (수출가격 전략·직접입력 탭에 반영).`,
      false,
    );
  } catch (exc) {
    console.warn('FOB 연쇄 계산 오류:', exc);
    showToast(`FOB 요청 오류: ${exc.message || exc}`, 'warn');
    _showCustomDrugMsg(
      `신약 크롤 완료(${code}) — FOB 자동 호출 실패. AEMP=${aempAud || 'N/A'} AUD.`,
      true,
    );
  }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §10. 결과 렌더링 (U2·U3·U4·U6·B4·N3·N4)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/**
 * 호주 백엔드 응답 → 싱가포르 renderResult() 가 기대하는 shape 로 어댑터 변환.
 *
 * 호주 응답 3종 조합:
 *   ① /api/data/{product_id}            → auRow (australia 테이블 73~75 컬럼 원본)
 *   ② /api/report/generate → blocks     → Claude Haiku block2_x · block3_x · block4_regulatory
 *   ③ /api/report/generate → meta       → { export_viable, reason_code, confidence, confidence_breakdown }
 *
 * 싱가포르 renderResult 가 접근하는 필드:
 *   result.trade_name, result.product_id, result.inn, result.verdict,
 *   result.basis_market_medical, result.basis_regulatory, result.basis_trade,
 *   result.basis_pbs_line, result.entry_pathway, result.price_positioning_pbs,
 *   result.risks_conditions, result.rationale, result.error, result.analysis_error
 *
 * 호주 원본은 _au_raw / _au_blocks / _au_meta 로 보존 — 데이터 전혀 버리지 않음.
 */
function _auToRenderResult(auRow, blocks, meta) {
  // row 조회 실패해도 /api/report/generate 가 성공하면 blocks·meta 로 화면·PDF 는 살려야 함(이전에는 error 로만 반환해 PDF UI 가 막혔음)
  if (!auRow) {
    if (blocks || meta) {
      const evMap = { viable: '적합', conditional: '조건부', not_viable: '부적합' };
      const fromV8Ko = { 가능: '적합', 조건부: '조건부', 불가: '부적합' };
      const v8Cat = blocks && blocks.verdict && blocks.verdict.category;
      const metaEv = meta && meta.export_viable;
      const verdict =
        (v8Cat && fromV8Ko[v8Cat]) ||
        (metaEv && evMap[metaEv]) ||
        (metaEv && fromV8Ko[metaEv]) ||
        null;
      const pid = (typeof _currentKey === 'string' && _currentKey) ? _currentKey : '';
      return {
        product_id:            pid,
        trade_name:            (meta && meta.product_name_ko) || '—',
        inn:                   (meta && meta.inn_normalized) || '',
        verdict:               verdict,
        reason_code:           (meta && meta.reason_code) || null,
        rationale:             null,
        basis_market_medical:  blocks && blocks.block2_market,
        basis_regulatory:      blocks && blocks.block2_regulatory,
        basis_trade:           blocks && blocks.block2_trade,
        basis_pbs_line:        '품목 DB 행을 불러오지 못했습니다. 보고서·PDF 본문을 참고하세요.',
        entry_pathway:         blocks && blocks.block3_channel,
        price_positioning_pbs: blocks && blocks.block3_pricing,
        risks_conditions:      blocks && blocks.block3_risks,
        regulatory_checks:     blocks && blocks.block4_regulatory,
        confidence:            meta && meta.confidence,
        confidence_breakdown:  meta && meta.confidence_breakdown,
        pbs_listed:            undefined,
        _au_raw:               null,
        _au_blocks:            blocks,
        _au_meta:              meta,
      };
    }
    return { error: '호주 백엔드에서 품목 row 를 조회하지 못했습니다.' };
  }

  // 판정: ① Claude v8 verdict.category(가능/조건부/불가) → ② meta.export_viable(보고서 응답) → ③ 크롤 row.export_viable
  // (크롤만 보면 Haiku 판정과 어긋나 '미분석'·오표시가 날 수 있음)
  const evMap = { viable: '적합', conditional: '조건부', not_viable: '부적합' };
  const fromV8Ko = { 가능: '적합', 조건부: '조건부', 불가: '부적합' };
  const v8Cat = blocks && blocks.verdict && blocks.verdict.category;
  const metaEv = meta && meta.export_viable;
  const rowEv = auRow.export_viable;
  const verdict =
    (v8Cat && fromV8Ko[v8Cat]) ||
    (metaEv && evMap[metaEv]) ||
    (metaEv && fromV8Ko[metaEv]) ||
    evMap[rowEv] ||
    (rowEv && fromV8Ko[rowEv]) ||
    rowEv ||
    null;

  // PBS 한 줄 요약 (basis-pbs-line)
  let pbsLine;
  if (auRow.pbs_listed) {
    pbsLine = `PBS 등재 · ${auRow.pbs_item_code || ''}` +
              (auRow.pbs_dpmq ? ` · DPMQ AUD ${auRow.pbs_dpmq}` : '');
  } else if (auRow.retail_price_aud) {
    const method = auRow.retail_estimation_method === 'pbs_dpmq'
      ? 'PBS DPMQ 기준'
      : auRow.retail_estimation_method === 'chemist_markup'
      ? 'Chemist × 1.20 (CHOICE 기준)'
      : '';
    pbsLine = `PBS 미등재 · 시장 추정가 AUD ${auRow.retail_price_aud}` + (method ? ` (${method})` : '');
  } else {
    pbsLine = 'PBS 미등재 · 참고가 미확보';
  }

  return {
    product_id:            auRow.product_id,
    trade_name:            auRow.product_name_ko,
    inn:                   auRow.inn_normalized,
    verdict:               verdict,
    reason_code:           auRow.reason_code,
    rationale:             null,
    basis_market_medical:  blocks?.block2_market       || null,
    basis_regulatory:      blocks?.block2_regulatory   || null,
    basis_trade:           blocks?.block2_trade        || null,
    basis_pbs_line:        pbsLine,
    entry_pathway:         blocks?.block3_channel      || null,
    price_positioning_pbs: blocks?.block3_pricing      || null,
    risks_conditions:      blocks?.block3_risks        || null,
    regulatory_checks:     blocks?.block4_regulatory   || null,
    confidence:            meta?.confidence,
    confidence_breakdown:  meta?.confidence_breakdown,
    // 호주 원본 보존 (_pbsLineFromApi 같은 싱가포르 헬퍼가 PBS 정보 재가공 시 접근)
    pbs_listed:            auRow.pbs_listed,
    pbs_item_code:         auRow.pbs_item_code,
    pbs_price_aud:         auRow.pbs_price_aud,
    pbs_dpmq:              auRow.pbs_dpmq,
    retail_price_aud:      auRow.retail_price_aud,
    chemist_price_aud:     auRow.chemist_price_aud,
    retail_estimation_method: auRow.retail_estimation_method,
    // 전체 원본 (디버깅·Stage 4 복원용)
    _au_raw:    auRow,
    _au_blocks: blocks,
    _au_meta:   meta,
  };
}

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
      const noteErr =
        pdfName
          ? '⚠️ 일부 표시용 DB 행을 불러오지 못했습니다. 아래 PDF 는 생성된 파일입니다.'
          : '⚠️ 분석 데이터 오류 — 재시도하거나 서버 로그를 확인하세요.';
      _showP1Note(noteErr, true);
      if (!pdfName) {
        _showReportError();
        return;
      }
      // PDF 가 있으면 아래 U4 에서 다운로드 표시(이전에는 여기서 return 해 PDF 가 영구히 숨겨짐)
    } else {
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

    // 완료 노트 표시 (result-card는 숨김 DOM이므로 visible 처리 안 함)
    _showP1Note(
      `✅ ${result.trade_name || '제품'} 분석 완료 — 판정: ${vLabel}. 상세 결과는 보고서 탭에서 확인하세요.`,
      false
    );
    }
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

/** U4: PDF 영역 초기 — 파이프라인 재실행 직전·첫 진입: 카드 숨김(목업: 완료 후에만 PDF 영역 표시) */
function _showReportIdle() {
  const idle = document.getElementById('report-state-idle');
  const loading = document.getElementById('report-state-loading');
  const ok = document.getElementById('report-state-ok');
  const err = document.getElementById('report-state-error');
  const card = document.getElementById('report-card');
  if (idle) idle.style.display = '';
  if (loading) loading.style.display = 'none';
  if (ok) ok.style.display = 'none';
  if (err) err.style.display = 'none';
  if (card) card.classList.remove('visible');
  const preview = document.getElementById('pdf-preview-frame');
  if (preview) preview.setAttribute('src', 'about:blank');
}

/** U4: PDF 생성 완료 */
function _showReportOk(pdfName) {
  const dl = document.querySelector('#report-state-ok .btn-download');
  const baseQ = pdfName ? `name=${encodeURIComponent(pdfName)}` : '';
  const downloadUrl = `/api/report/download${baseQ ? `?${baseQ}` : ''}`;
  if (dl) dl.setAttribute('href', downloadUrl);
  // iframe 제거됨 — null-safe 처리
  const preview = document.getElementById('pdf-preview-frame');
  if (preview) {
    const previewUrl = `/api/report/download?${baseQ ? `${baseQ}&` : ''}inline=1`;
    preview.setAttribute('src', previewUrl);
  }
  document.getElementById('report-state-loading').style.display = 'none';
  document.getElementById('report-state-ok').style.display      = 'block';
  document.getElementById('report-state-error').style.display   = 'none';
  const idleOk = document.getElementById('report-state-idle');
  if (idleOk) idleOk.style.display = 'none';
  document.getElementById('report-card').classList.add('visible');
}

/** U4: PDF 생성 실패 */
function _showReportError() {
  const preview = document.getElementById('pdf-preview-frame');
  if (preview) preview.setAttribute('src', 'about:blank');
  document.getElementById('report-state-loading').style.display = 'none';
  document.getElementById('report-state-ok').style.display      = 'none';
  document.getElementById('report-state-error').style.display   = 'block';
  const idleErr = document.getElementById('report-state-idle');
  if (idleErr) idleErr.style.display = 'none';
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
    if (!Number.isNaN(sNum)) t += `, 참고 AUD ${sNum.toFixed(2)}`;
    return t;
  }
  const haiku = String(result.pbs_haiku_estimate || '').trim();
  if (haiku) return haiku;
  return '참고 가격 정보 없음';
}

/** 시장조사 완료/오류 노트 표시 */
function _showP1Note(msg, isErr) {
  const el = document.getElementById('p1-result-note');
  if (!el) return;
  el.textContent = msg;
  el.className   = 'p1-result-note' + (isErr ? ' err' : '');
  el.style.display = '';
}

function _hideP1Note() {
  const el = document.getElementById('p1-result-note');
  if (el) el.style.display = 'none';
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
  const bannerEl = document.getElementById('news-mock-banner');
  if (bannerEl) {
    bannerEl.hidden = true;
    bannerEl.innerHTML = '';
  }
  listEl.className = 'news-list--busy';
  listEl.innerHTML = '<div class="irow" style="color:var(--muted);font-size:12px;text-align:center;padding:20px 0;">뉴스 로드 중…</div>';

  try {
    const res  = await fetch('/api/news');
    const newsBackend = (res.headers.get('X-News-Source') || '').trim();
    let raw;
    try {
      raw = await res.json();
    } catch (parseErr) {
      listEl.className = 'news-list--busy';
      listEl.innerHTML = '<div class="irow" style="color:var(--muted);font-size:12px;text-align:center;padding:16px 0;">뉴스 응답 형식 오류</div>';
      console.warn('뉴스 JSON 파싱 실패:', parseErr);
      return;
    }
    /* 레거시: 배열만 오던 응답 호환 */
    const data = Array.isArray(raw) ? { ok: true, items: raw, error: null } : raw;
    /* HTTP 오류 시 FastAPI는 { detail: "..." } 만 줄 수 있음 — ok/items 없음 */
    if (!res.ok) {
      const detail = (data && (data.detail || data.message)) || res.statusText || 'HTTP 오류';
      listEl.className = 'news-list--busy';
      listEl.innerHTML = `<div class="irow" style="color:var(--muted);font-size:12px;text-align:center;padding:16px 0;">${_escHtml(String(detail))}</div>`;
      return;
    }
    const items = data.items || [];
    const serverOk = data.ok !== false;
    if (!serverOk || !items.length) {
      listEl.className = 'news-list--busy';
      listEl.innerHTML = `<div class="irow" style="color:var(--muted);font-size:12px;text-align:center;padding:16px 0;">${_escHtml(data.error || '뉴스를 불러올 수 없습니다.')}</div>`;
      return;
    }

    if (bannerEl) {
      if (newsBackend === 'mock') {
        bannerEl.hidden = false;
        bannerEl.innerHTML =
          '<div class="news-source-hint" role="status">Perplexity 실데이터가 아닌 <strong>샘플</strong>입니다. Render에 <code>PERPLEXITY_API_KEY</code>(또는 <code>PERPLEXITY_KEY</code>)가 설정돼 있고 API가 성공하면 최신 기사로 바뀝니다.</div>';
      } else {
        bannerEl.hidden = true;
        bannerEl.innerHTML = '';
      }
    }

    /* 메인 카드는 항상 5건 레이아웃(백엔드와 동일) */
    const newsItems = items.slice(0, 5);

    listEl.className = 'news-list--ready';
    listEl.innerHTML = newsItems.map(item => {
      const href   = item.link ? `href="${_escHtml(item.link)}" target="_blank" rel="noopener"` : '';
      const tag    = item.link ? 'a' : 'div';
      const source = [item.source, item.date].filter(Boolean).join(' · ');
      const head   = item.title_ko || item.title || '';
      const sum    = item.summary_ko
        ? `<div class="news-sum">${_escHtml(item.summary_ko)}</div>`
        : '';
      return `
        <${tag} class="irow news-item" ${href}>
          <div class="tit">${_escHtml(head)}</div>
          ${sum}
          ${source ? `<div class="sub">${_escHtml(source)}</div>` : ''}
        </${tag}>`;
    }).join('');
  } catch (e) {
    listEl.className = 'news-list--busy';
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
// 메인에 환율 UI 없음 — 수출전략 FOB·직접입력이 window._exchangeRates 를 쓰므로 초기 1회만 조회
loadExchange();
loadMacro();            // 거시 지표 로드
renderReportTab();      // 보고서 탭 초기 렌더
initP2Strategy();       // 수출가격 전략 초기화
loadNews();             // 시장 뉴스 즉시 로드
_showReportIdle();      // 01 시장조사: PDF 카드·진행 스테퍼 초기 상태(첫 진입 = 품목/신약만)
resetProgress();
