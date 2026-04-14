/* UPharma Export AI · Australia — 프론트 로직
 * upharma_demo_v2.html 의 인라인 스크립트에서 분리.
 * mock 데이터(PRODS) 제거 → /api/crawl + /api/data/{product_id} 로 교체.
 * 수동 입력(manual) 은 au_products.json 에 정의되지 않은 품목이라 현재 단계에서는 미지원.
 */

/* ── 탭 전환 ── */
function goTab(id, el) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('on'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('on'));
  document.getElementById(id).classList.add('on');
  el.classList.add('on');
}

/* ── 입력 모드 전환 ── */
function setMode(m) {
  document.getElementById('modeSelect').style.display = m === 'select' ? 'block' : 'none';
  document.getElementById('modeManual').style.display = m === 'manual' ? 'block' : 'none';
  document.getElementById('togSelect').classList.toggle('on', m === 'select');
  document.getElementById('togManual').classList.toggle('on', m === 'manual');
}

/* ── 수출 적합성 판정 뱃지 ── */
function renderViableBadge(val) {
  const v = String(val || '').trim();
  const cls = (v === '가능' || v.startsWith('가능')) ? 'green'
            : v === '조건부' ? 'orange'
            : v === '불가'   ? 'red'
            : 'gray';
  const label = v || '분석 중';
  return `<span class="bdg ${cls}" style="font-size:13px;padding:6px 14px;">${label}</span>`;
}

/* ── Supabase row → 카드 뷰모델 매핑 ── */
function mapRowToCard(row) {
  const pbsListed = !!row.pbs_listed;
  const pbsPriceAud = row.pbs_price_aud;
  const retailAud = row.retail_price_aud;
  const priceSrcName = row.price_source_name || '';
  const isCW = priceSrcName === 'Chemist Warehouse';

  // TGA 블록
  const tgaStatus = row.artg_status || '—';
  const tgaVal = tgaStatus.toLowerCase() === 'registered' || tgaStatus === '등재'
    ? '등재' : (tgaStatus || '미등재');

  // PBS 블록
  const pbsVal = pbsListed
    ? (typeof pbsPriceAud === 'number' ? `A$${Number(pbsPriceAud).toFixed(2)}` : '등재')
    : '미등재';
  const pbsDpmq = (row.pbs_dpmq || row.pbs_dpmq === 0)
    ? `A$${Number(row.pbs_dpmq).toFixed(2)}`
    : '—';

  // Chemist WH 블록
  const cwVal = (isCW && typeof retailAud === 'number')
    ? `A$${Number(retailAud).toFixed(2)}`
    : (pbsListed ? '해당없음' : '—');

  // buy.nsw.gov.au 블록 — 1공정 summary 에서는 market_segment 기반으로 간략 표시
  const nswVal = row.market_segment === 'hospital' ? '병원 조달' : '해당없음';

  // 수출 가능성
  const exportViable = row.export_viable || '';
  let viableLabel = '분석 중';
  if (exportViable === 'viable') viableLabel = '가능';
  else if (exportViable === 'conditional') viableLabel = '조건부';
  else if (exportViable === 'not_viable') viableLabel = '불가';

  return {
    name: row.product_name_ko || row.product_id,
    inn: row.inn_normalized || '',
    strength: row.strength || '',
    form: row.dosage_form || '',
    type: row.market_segment === 'hospital' ? '병원' : '일반',
    tga: { val: tgaVal, num: row.artg_number || '—', sched: row.tga_schedule || '—' },
    pbs: { listed: pbsListed, price: pbsVal, dpmq: pbsDpmq },
    cw: { val: cwVal },
    nsw: { val: nswVal },
    viable: viableLabel,
    conf: typeof row.confidence === 'number' ? row.confidence : 0,
    error: row.error_type ? true : false,
  };
}

/* ── 크롤링 결과 카드 스택 ── */
let crawlResultCount = 0;

function renderCard(p) {
  const stack = document.getElementById('crawlStack');
  const empty = document.getElementById('crawlEmpty');
  if (empty) empty.remove();

  crawlResultCount++;
  document.getElementById('crawlCount').textContent = crawlResultCount;

  const now = new Date();
  const timeStr = now.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const hasError = p.error || p.tga.val === '미등재';

  const card = document.createElement('div');
  card.className = 'crawl-card' + (hasError ? ' has-error' : '');
  card.innerHTML = `
    <div class="cc-head">
      <div>
        <div class="cc-title">${p.name} <span style="font-size:11px;color:var(--muted);">[${p.type || ''}]</span></div>
        <div class="cc-inn">${p.inn}${p.strength ? ' · ' + p.strength : ''} · ${p.form}</div>
      </div>
      <div class="cc-time">${timeStr}</div>
    </div>
    <div class="cc-src-grid">
      <div class="cc-src tga">
        <div class="cc-src-name">① TGA ARTG</div>
        <div class="cc-src-val">${p.tga.val}</div>
        <div class="cc-src-sub">ARTG: ${p.tga.num}<br>Schedule: ${p.tga.sched}</div>
      </div>
      <div class="cc-src pbs">
        <div class="cc-src-name">② PBS API v3</div>
        <div class="cc-src-val">${p.pbs.listed ? p.pbs.price : '미등재'}</div>
        <div class="cc-src-sub">DPMQ: ${p.pbs.dpmq || '—'}</div>
      </div>
      <div class="cc-src cw">
        <div class="cc-src-name">③ Chemist Warehouse</div>
        <div class="cc-src-val">${p.cw.val}</div>
        <div class="cc-src-sub">${p.pbs.listed ? 'PBS 등재 → 약국 비해당' : '소매 채널'}</div>
      </div>
      <div class="cc-src nsw">
        <div class="cc-src-name">④ buy.nsw.gov.au</div>
        <div class="cc-src-val">${p.nsw.val}</div>
        <div class="cc-src-sub">${p.nsw.contract || '조영제/병원 전용'}</div>
      </div>
    </div>
    <div style="display:flex;align-items:center;justify-content:center;gap:10px;padding:12px 0;margin-top:6px;border-top:1px solid rgba(23,63,120,.06);border-bottom:1px solid rgba(23,63,120,.06);">
      <span style="font-size:11.5px;font-weight:800;color:var(--muted);letter-spacing:.02em;">수출 적합성 판정</span>
      ${renderViableBadge(p.viable)}
    </div>
    <div class="cc-footer" style="margin-top:10px;">
      <span style="font-size:11.5px;color:var(--muted);">신뢰도 ${p.conf > 0 ? Math.round(p.conf * 100) + '%' : '—'}</span>
    </div>`;

  stack.insertBefore(card, stack.firstChild);
  document.getElementById('genBtn1').disabled = false;
}

function renderPendingCard(productId) {
  const stack = document.getElementById('crawlStack');
  const empty = document.getElementById('crawlEmpty');
  if (empty) empty.remove();

  const card = document.createElement('div');
  card.className = 'crawl-card';
  card.id = 'pending_' + productId;
  card.innerHTML = `
    <div class="cc-head">
      <div>
        <div class="cc-title">${productId} <span style="font-size:11px;color:var(--muted);">크롤링 중...</span></div>
        <div class="cc-inn">TGA · PBS · Chemist · buy.nsw 병렬 수집</div>
      </div>
      <div class="cc-time">진행 중</div>
    </div>
    <div class="empty-state" style="padding:16px;">서버에서 au_crawler.py 실행 중 — 30초~2분 소요</div>`;
  stack.insertBefore(card, stack.firstChild);
  return card;
}

async function runCrawl(mode) {
  if (mode === 'manual') {
    alert('수동 입력(신약 직접 입력)은 현재 단계에서 미지원 — au_products.json 에 정의된 8개 품목만 크롤링 가능합니다.');
    return;
  }

  const productId = document.getElementById('prodSel').value;
  if (!productId) { alert('품목을 선택하세요.'); return; }

  const pending = renderPendingCard(productId);

  let ok = false;
  try {
    const res = await fetch('/api/crawl', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_id: productId }),
    });
    const body = await res.json().catch(() => ({}));
    ok = !!body.ok;
  } catch (e) {
    ok = false;
  }

  try {
    const dataRes = await fetch('/api/data/' + encodeURIComponent(productId));
    if (!dataRes.ok) throw new Error('no data');
    const row = await dataRes.json();
    pending.remove();
    renderCard(mapRowToCard(row));
  } catch (e) {
    pending.innerHTML = `
      <div class="cc-head">
        <div>
          <div class="cc-title">${productId}</div>
          <div class="cc-inn">크롤링 ${ok ? '완료' : '실패'} · Supabase 조회 실패</div>
        </div>
      </div>
      <div class="empty-state" style="padding:16px;color:var(--red);">데이터 조회 실패 — .env 의 SUPABASE 키 확인 필요</div>`;
    pending.classList.add('has-error');
  }
}

/* ── 보고서 생성 ── */
const reportStore = [];

function generateReport(n) {
  const names = { 1: '1공정 시장조사 보고서', 2: '2공정 수출전략 보고서', 3: '3공정 유망 바이어 보고서' };
  const btn = document.getElementById('genBtn' + n);
  if (btn) { btn.textContent = '생성 중...'; btn.disabled = true; }

  setTimeout(() => {
    if (btn) { btn.textContent = '📄 ' + names[n] + ' 산출'; btn.disabled = false; }

    if (n === 1) {
      buildReportPreview();
      const preview = document.getElementById('rptPreview1');
      if (preview) {
        preview.style.display = 'block';
        preview.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    } else {
      const saveBtn = document.getElementById('saveBtn' + n);
      if (saveBtn) saveBtn.style.display = 'inline-flex';
      alert(names[n] + ' 생성 완료! 저장 버튼으로 보고서 탭에 저장하세요.');
    }
  }, 1400);
}

function buildReportPreview() {
  const now = new Date();
  const dateStr = now.toLocaleDateString('ko-KR');

  const stack = document.getElementById('crawlStack');
  const cards = stack ? stack.querySelectorAll('.crawl-card') : [];
  let prodRows = '';
  cards.forEach(c => {
    const title = c.querySelector('.cc-title');
    const inn   = c.querySelector('.cc-inn');
    const srcs  = c.querySelectorAll('.cc-src');
    const viable = c.querySelector('.bdg');
    if (!title || !srcs.length) return;
    const tgaVal = srcs[0] ? srcs[0].querySelector('.cc-src-val').textContent : '—';
    const pbsVal = srcs[1] ? srcs[1].querySelector('.cc-src-val').textContent : '—';
    const cwVal  = srcs[2] ? srcs[2].querySelector('.cc-src-val').textContent : '—';
    const nswVal = srcs[3] ? srcs[3].querySelector('.cc-src-val').textContent : '—';
    const vBadge = viable ? viable.outerHTML : '';
    prodRows += `<div class="rpt-prod">
      <div class="pname">${title.textContent.trim()}</div>
      <div class="pinn">${inn ? inn.textContent.trim() : ''}</div>
      <div class="rpt-prod-row"><span class="k">TGA ARTG</span><span class="v">${tgaVal}</span></div>
      <div class="rpt-prod-row"><span class="k">PBS 가격</span><span class="v">${pbsVal}</span></div>
      <div class="rpt-prod-row"><span class="k">Chemist WH</span><span class="v">${cwVal}</span></div>
      <div class="rpt-prod-row"><span class="k">NSW 조달</span><span class="v">${nswVal}</span></div>
      <div class="rpt-prod-row"><span class="k">수출 가능성</span><span class="v">${vBadge}</span></div>
    </div>`;
  });
  if (!prodRows) prodRows = '<div style="color:var(--muted);font-size:13px;padding:12px;">크롤링 데이터 없음 — 1공정에서 품목을 먼저 크롤링하세요.</div>';

  document.getElementById('rptContent1').innerHTML = `
    <div class="rpt-sec">
      <div class="rpt-sec-title">Section 1 · 보고서 개요</div>
      <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;font-size:13px;">
        <div><b>보고서명:</b> 호주 수출 시장조사 보고서 (1공정)</div>
        <div><b>생성일:</b> ${dateStr}</div>
        <div><b>대상 국가:</b> Australia (AU)</div>
        <div><b>데이터 소스:</b> TGA · PBS API v3 · Chemist Warehouse · buy.nsw.gov.au</div>
      </div>
    </div>

    <div class="rpt-sec">
      <div class="rpt-sec-title">Section 2 · 호주 거시 지표</div>
      <div class="rpt-row">
        <div class="rpt-kpi"><div class="lbl">GDP per capita</div><div class="val">$64,604</div><div class="src">World Bank 2024</div></div>
        <div class="rpt-kpi"><div class="lbl">인구</div><div class="val">26,974,026</div><div class="src">Worldometer 2025</div></div>
        <div class="rpt-kpi"><div class="lbl">의약품 시장규모</div><div class="val">USD $25.3B</div><div class="src">IMARC 2025</div></div>
        <div class="rpt-kpi"><div class="lbl">총 보건지출</div><div class="val">A$270.5B</div><div class="src">AIHW 2023–24</div></div>
      </div>
    </div>

    <div class="rpt-sec">
      <div class="rpt-sec-title">Section 3 · 관세 및 FTA 현황</div>
      <div class="rpt-row" style="grid-template-columns:repeat(3,1fr);">
        <div class="rpt-kpi"><div class="lbl">HS 3004.90 KAFTA 관세율</div><div class="val" style="color:var(--green);">0%</div><div class="src">DFAT FTA Portal · KAFTA</div></div>
        <div class="rpt-kpi"><div class="lbl">HS 3006.30 KAFTA 관세율</div><div class="val" style="color:var(--green);">0%</div><div class="src">DFAT FTA Portal · KAFTA</div></div>
        <div class="rpt-kpi"><div class="lbl">GST (부가가치세)</div><div class="val" style="color:var(--warn);">10%</div><div class="src">처방의약품 면제·OTC 과세</div></div>
      </div>
      <div style="font-size:12px;color:var(--muted);padding:8px 12px;background:var(--inner);border-radius:10px;">
        <b>KAFTA (한-호주 FTA):</b> 2014년 발효. Chapter 30 의약품 전반 관세 철폐 완료. 실질 진입 장벽은 TGA 인증 비관세 장벽.
      </div>
    </div>

    <div class="rpt-sec">
      <div class="rpt-sec-title">Section 4 · 품목별 크롤링 결과</div>
      <div class="rpt-prod-grid">${prodRows}</div>
    </div>

    <div class="rpt-sec">
      <div class="rpt-sec-title">Section 5 · 규제 체크포인트</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
        <div class="rpt-kpi"><div class="lbl">Therapeutic Goods Act 1989</div><div style="font-size:12px;color:var(--text);margin-top:4px;">ARTG 등재 의무 · TGA 심사 12–18개월</div></div>
        <div class="rpt-kpi"><div class="lbl">GMP 기준 (PIC/S 상호인정)</div><div style="font-size:12px;color:var(--text);margin-top:4px;">한국 PIC/S 회원 → 제조소 실사 면제 가능</div></div>
        <div class="rpt-kpi"><div class="lbl">PBS (National Health Act 1953)</div><div style="font-size:12px;color:var(--text);margin-top:4px;">공공조달 등재 시 가격 통제 수반</div></div>
        <div class="rpt-kpi"><div class="lbl">buy.nsw.gov.au 조달 경로</div><div style="font-size:12px;color:var(--text);margin-top:4px;">Gadvoa 등 병원 전용 품목 — NSW 입찰 참여</div></div>
      </div>
    </div>

    <div class="rpt-sec">
      <div class="rpt-sec-title">Section 6 · 결론 및 권장사항</div>
      <div style="font-size:13px;color:var(--text);line-height:1.8;">
        <p style="margin-bottom:8px;">• <b>즉시 추진 가능 품목:</b> TGA 등재 완료·PBS 수재 품목 (Hydrine, Sereterol Activair, Rosumeg, Atmeg) — FOB 역산 후 2공정 진행 권장</p>
        <p style="margin-bottom:8px;">• <b>병원 채널 집중 품목:</b> Gadvoa Inj. — buy.nsw.gov.au 입찰 접근, NSW Health 계약 목표</p>
        <p style="margin-bottom:8px;">• <b>TGA 등재 선행 필요:</b> Ciloduo, Gastiin CR — 등재 후 재분석 필요. 예상 소요 12–18개월</p>
        <p>• <b>다음 단계:</b> 2공정 FOB 역산 실행 → 공격·중도·보수 3개 시나리오 수출전략 도출</p>
      </div>
    </div>`;
}

function printReport() { window.print(); }

async function saveReport(n) {
  const names = { 1: '1공정 시장조사 보고서', 2: '2공정 수출전략 보고서', 3: '3공정 유망 바이어 보고서' };
  const subs  = { 1: 'TGA·PBS·가격·수출가능성 분석 완료', 2: 'FOB 역산 3시나리오 · 채널 전략', 3: 'PSI 점수 Top-10 · GMP/MAH 필터' };
  const productId = document.getElementById('prodSel')?.value || null;

  // 화면 즉시 반영(낙관적 업데이트) 후 백엔드 저장 시도
  const now = new Date();
  const ts = now.toLocaleDateString('ko-KR') + ' · ' + now.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
  const localId = Date.now();
  reportStore.unshift({ id: localId, title: names[n], sub: subs[n], time: ts, gong: n + '공정' });
  renderReports();
  const saveBtn = document.getElementById('saveBtn' + n);
  if (saveBtn) { saveBtn.textContent = '저장 중...'; saveBtn.disabled = true; }

  try {
    const res = await fetch('/api/reports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        product_id: productId,
        gong: n,
        title: names[n],
        file_url: null,
        crawled_data: null,
      }),
    });
    const body = await res.json().catch(() => ({}));
    if (saveBtn) saveBtn.textContent = body.ok ? '✓ 저장됨' : '저장 실패';
  } catch (e) {
    if (saveBtn) saveBtn.textContent = '저장 실패(네트워크)';
  }
}

/* 서버에 저장된 오늘의 보고서 목록을 초기 로드 */
async function loadReportsToday() {
  try {
    const res = await fetch('/api/reports');
    if (!res.ok) return;
    const body = await res.json();
    const items = Array.isArray(body.items) ? body.items : [];
    const names = { 1: '1공정 시장조사 보고서', 2: '2공정 수출전략 보고서', 3: '3공정 유망 바이어 보고서' };
    const subs  = { 1: 'TGA·PBS·가격·수출가능성 분석 완료', 2: 'FOB 역산 3시나리오 · 채널 전략', 3: 'PSI 점수 Top-10 · GMP/MAH 필터' };
    reportStore.length = 0;
    items.forEach(r => {
      const dt = r.created_at ? new Date(r.created_at) : new Date();
      reportStore.push({
        id: r.id || Date.now() + Math.random(),
        title: r.title || names[r.gong] || '보고서',
        sub: subs[r.gong] || '',
        time: dt.toLocaleDateString('ko-KR') + ' · ' + dt.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' }),
        gong: (r.gong || 1) + '공정',
      });
    });
    renderReports();
  } catch (e) { /* 무시 */ }
}

function renderReports() {
  const cnt = reportStore.length;
  const repCount = document.getElementById('repCount');
  const repDate = document.getElementById('repDate');
  if (repCount) repCount.textContent = cnt + '건';
  if (repDate) repDate.textContent = '오늘 저장된 보고서 누적 목록 · ' + cnt + '건';

  const container = document.getElementById('allReports');
  if (container) {
    if (cnt === 0) {
      container.innerHTML = '<div class="empty-state">저장된 보고서가 없습니다.</div>';
    } else {
      container.innerHTML = reportStore.map(r => `
        <div class="rpt">
          <div class="rt">${r.title}</div>
          <div class="rd">${r.sub}</div>
          <div class="rpt-bot">
            <div>
              <span class="st ok">Completed</span>
              <div class="rpt-time" style="margin-top:3px;">${r.time} · ${r.gong}</div>
            </div>
            <div class="rpt-btns">
              <button class="btn-sm" onclick="downloadRpt(${r.id})">⬇ 다운로드</button>
              <button class="btn-del" onclick="deleteRpt(${r.id})">✕ 삭제</button>
            </div>
          </div>
        </div>`).join('');
    }
  }

  const main = document.getElementById('mainReports');
  if (main) {
    const top4 = reportStore.slice(0, 4);
    if (top4.length === 0) {
      main.innerHTML = '<div style="text-align:center;padding:20px;color:var(--muted);font-size:13px;">아직 저장된 보고서가 없습니다.</div>';
    } else {
      main.innerHTML = top4.map(r => `
        <div class="rpt">
          <div class="rt">${r.title}</div>
          <div class="rd">${r.sub}</div>
          <div class="rpt-bot">
            <div><span class="st ok">Completed</span><div class="rpt-time" style="margin-top:3px;">${r.time}</div></div>
            <div class="rpt-btns"><button class="btn-sm" onclick="downloadRpt(${r.id})">⬇</button></div>
          </div>
        </div>`).join('');
    }
  }
}

function downloadRpt(id) {
  const r = reportStore.find(x => x.id === id);
  if (!r) return;
  const blob = new Blob(
    [`UPharma Export AI\n${r.title}\n생성: ${r.time}\n\n[실제 구현 시 Supabase storage에서 PDF 다운로드]`],
    { type: 'text/plain;charset=utf-8' }
  );
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = r.title + '.txt';
  a.click();
}

function deleteRpt(id) {
  if (!confirm('삭제하시겠습니까?')) return;
  const idx = reportStore.findIndex(x => x.id === id);
  if (idx > -1) reportStore.splice(idx, 1);
  renderReports();
}

document.addEventListener('DOMContentLoaded', () => {
  renderReports();
  loadReportsToday();
});
