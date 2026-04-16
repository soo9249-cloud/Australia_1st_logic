/* UPharma Export AI · Australia — 프론트 로직 (v3 기반)
 * upharma_demo_v3.html 의 <script> 블록에서 분리.
 * runCrawl / saveReport / 초기 reports 로드만 실제 백엔드 API 로 교체.
 * 나머지 UI 함수(goTab, setMode, setStep1, buildReportCards, renderReports,
 * generateReport, showToast, dlRpt, delRpt)는 v3 원본 로직 유지.
 */

/* ── au_products.json 의 product_id 매핑 (select option index → id) ── */
const PRODUCT_IDS = [
  "au-omethyl-001",   // 0
  "au-rosumeg-005",   // 1
  "au-atmeg-006",     // 2
  "au-ciloduo-007",   // 3
  "au-gastiin-008",   // 4
  "au-sereterol-003", // 5
  "au-gadvoa-002",    // 6
  "au-hydrine-004",   // 7
];

/* ── Mock 데이터 (API 실패 시 폴백) ─────────────────────────────
 * 원 PRODS 배열 — 서버 응답이 없을 때만 사용됨.
 */
const PRODS = [
  {name:"Omethyl Cutielet",inn:"omega-3-acid ethyl esters",str:"2g",form:"Pouch",type:"개량신약",hs:"3004.90",
   tga:{val:"등재",num:"ARTG 287451",sched:"—"},pbs:{listed:false,price:"미등재",dpmq:"—"},
   cw:{val:"A$29.99"},nsw:{val:"해당없음"},viable:"조건부",conf:0.55},
  {name:"Rosumeg Combigel",inn:"rosuvastatin + omega-3",str:"5/1000",form:"Cap.",type:"개량신약",hs:"3004.90",
   tga:{val:"등재",num:"ARTG 312044",sched:"S4"},pbs:{listed:true,price:"A$38.50",dpmq:"A$55.20"},
   cw:{val:"해당없음"},nsw:{val:"해당없음"},viable:"가능",conf:0.78},
  {name:"Atmeg Combigel",inn:"atorvastatin + omega-3",str:"10/1000",form:"Cap.",type:"개량신약",hs:"3004.90",
   tga:{val:"등재",num:"ARTG 308112",sched:"S4"},pbs:{listed:true,price:"A$41.10",dpmq:"A$59.80"},
   cw:{val:"해당없음"},nsw:{val:"해당없음"},viable:"가능",conf:0.80},
  {name:"Ciloduo",inn:"cilostazol + rosuvastatin",str:"200/10mg",form:"Tab.",type:"개량신약",hs:"3004.90",
   tga:{val:"미등재",num:"—",sched:"—"},pbs:{listed:false,price:"미등재",dpmq:"—"},
   cw:{val:"해당없음"},nsw:{val:"해당없음"},viable:"불가",conf:0,error:true},
  {name:"Gastiin CR",inn:"mosapride citrate",str:"15mg",form:"Tab.",type:"개량신약",hs:"3004.90",
   tga:{val:"미등재",num:"—",sched:"—"},pbs:{listed:false,price:"미등재",dpmq:"—"},
   cw:{val:"A$45.99"},nsw:{val:"해당없음"},viable:"조건부",conf:0.48},
  {name:"Sereterol Activair",inn:"fluticasone propionate + salmeterol",str:"250/50",form:"Inhaler",type:"일반제",hs:"3004.60",
   tga:{val:"등재",num:"ARTG 195448",sched:"S4"},pbs:{listed:true,price:"A$41.22",dpmq:"A$59.50"},
   cw:{val:"해당없음"},nsw:{val:"해당없음"},viable:"가능",conf:0.82},
  {name:"Gadvoa Inj.",inn:"gadobutrol 604.72mg",str:"5mL",form:"PFS",type:"일반제",hs:"3006.30",
   tga:{val:"등재",num:"ARTG 234109",sched:"S4"},pbs:{listed:false,price:"병원 전용",dpmq:"—"},
   cw:{val:"해당없음"},nsw:{val:"A$89.50/vial",contract:"NSW Health 계약"},viable:"가능(병원)",conf:0.71},
  {name:"Hydrine",inn:"hydroxycarbamide (hydroxyurea)",str:"500mg",form:"Cap.",type:"항암제",hs:"3004.90",
   tga:{val:"등재",num:"ARTG 47486",sched:"S4"},pbs:{listed:true,price:"A$31.92",dpmq:"A$48.11"},
   cw:{val:"해당없음"},nsw:{val:"해당없음"},viable:"가능",conf:0.85},
];

/* ── Supabase row → v3 카드 뷰모델 매퍼 ── */
function formatHs6(raw){
  const s = String(raw || "").replace(/\D/g,"");
  return s.length >= 6 ? `${s.slice(0,4)}.${s.slice(4,6)}` : (raw || "—");
}
function fmtAud(v){
  if (v == null || v === "") return null;
  const n = typeof v === "number" ? v : parseFloat(v);
  return isFinite(n) ? `A$${n.toFixed(2)}` : null;
}
function mapViable(row){
  const ev = String(row.export_viable || "").toLowerCase();
  if (ev === "viable")       return row.market_segment === "hospital" ? "가능(병원)" : "가능";
  if (ev === "conditional")  return "조건부";
  if (ev === "not_viable")   return "불가";
  return "분석 중";
}
function mapRowToProd(row){
  const pbsListed = !!row.pbs_listed;
  const pbsPriceStr = pbsListed ? (fmtAud(row.pbs_price_aud) || "등재") : "미등재";
  const pbsDpmqStr  = fmtAud(row.pbs_dpmq) || "—";

  // Chemist Warehouse 소매가는 price_source_name 이 CW 일 때만 표시
  const isCW = row.price_source_name === "Chemist Warehouse";
  const cwStr = (isCW && row.retail_price_aud != null)
    ? (fmtAud(row.retail_price_aud) || "—")
    : (pbsListed ? "해당없음" : "—");

  // NSW 조달 — 금액이 있으면 표기, 없으면 segment 기반
  let nswVal = row.market_segment === "hospital" ? "병원 조달" : "해당없음";
  let nswContract = null;
  if (row.nsw_contract_value_aud != null){
    nswVal = fmtAud(row.nsw_contract_value_aud) || nswVal;
  }
  if (row.nsw_supplier_name){
    nswContract = row.nsw_supplier_name;
  }
  // 매칭 없을 때 buynsw.py 가 생성한 일반 안내문
  const nswNote = row.nsw_note || null;

  return {
    name: row.product_name_ko || row.product_id || "—",
    inn:  row.inn_normalized || "—",
    str:  row.strength || "—",
    form: row.dosage_form || "—",
    type: row.market_segment === "hospital" ? "병원" : "일반",
    hs:   formatHs6(row.hs_code_6),
    tga:  {
      val:   row.artg_status === "registered" ? "등재"
           : row.artg_status === "not_registered" ? "미등재"
           : (row.artg_status || "—"),
      num:   row.artg_number || "—",
      sched: row.tga_schedule || "—",
    },
    pbs:  { listed: pbsListed, price: pbsPriceStr, dpmq: pbsDpmqStr },
    cw:   { val: cwStr },
    nsw:  { val: nswVal, contract: nswContract, note: nswNote },
    viable: mapViable(row),
    conf:   typeof row.confidence === "number" ? row.confidence : 0,
    error:  !!row.error_type || row.artg_status === "not_registered",
  };
}

/* 탭 전환 */
function goTab(id,el){
  document.querySelectorAll(".page").forEach(p=>p.classList.remove("on"));
  document.querySelectorAll(".tab").forEach(t=>t.classList.remove("on"));
  document.getElementById(id).classList.add("on");
  el.classList.add("on");
}

/* 입력 모드 */
function setMode(m){
  document.getElementById("modeSelect").style.display=m==="select"?"block":"none";
  document.getElementById("modeManual").style.display=m==="manual"?"block":"none";
  document.getElementById("togSel").classList.toggle("on",m==="select");
  document.getElementById("togMan").classList.toggle("on",m==="manual");
}

/* 스텝 업데이트 */
function setStep1(n){
  const fills=[0,25,50,75,100];
  for(let i=1;i<=5;i++){
    const ts=document.getElementById("ts1_"+i);
    const td=document.getElementById("td1_"+i);
    if(i<n){ts.className="todo-step done";td.className="todo-dot done";td.textContent="✓";}
    else if(i===n){ts.className="todo-step active";td.className="todo-dot active";td.textContent=String.fromCharCode(9311+i);}
    else{ts.className="todo-step idle";td.className="todo-dot idle";td.textContent=String.fromCharCode(9311+i);}
  }
  document.getElementById("trackFill1").style.width=fills[n-1]+"%";
}

/* 카드 렌더 (v3 원본 마크업 그대로) */
let crawlCount=0;
function renderCrawlCard(p){
  const stack=document.getElementById("crawlStack");
  const em=document.getElementById("crawlEmpty");
  if(em) em.remove();
  crawlCount++;
  document.getElementById("crawlCount").textContent=crawlCount;
  // genBtn1 은 레이아웃 개편으로 제거됨 — null 방어만 하고 스킵
  const _genBtn1 = document.getElementById("genBtn1");
  if(_genBtn1) _genBtn1.disabled=false;

  const now=new Date();
  const t=now.toLocaleTimeString("ko-KR",{hour:"2-digit",minute:"2-digit",second:"2-digit"});
  const hasErr=p.error||p.tga.val==="미등재";
  const vColor=p.viable==="가능"||p.viable.startsWith("가능")?"green":p.viable==="조건부"?"orange":p.viable==="불가"?"red":"gray";
  const card=document.createElement("div");
  card.className="crawl-card"+(hasErr?" has-error":"");
  card.innerHTML=`
    <div class="cc-head">
      <div>
        <div class="cc-title">${p.name} <span style="font-size:11px;color:var(--muted);">[${p.type||""}]</span></div>
        <div class="cc-inn">${p.inn} · ${p.str||""} · ${p.form||""} · HS ${p.hs||"—"}</div>
      </div>
      <div class="cc-time">${t}</div>
    </div>
    <div class="cc-src-grid">
      <div class="cc-src tga">
        <div class="cc-src-name">① TGA ARTG</div>
        <div class="cc-src-val">${p.tga.val}</div>
        <div class="cc-src-sub">ARTG: ${p.tga.num}<br>Schedule: ${p.tga.sched}</div>
      </div>
      <div class="cc-src pbs">
        <div class="cc-src-name">② PBS API v3</div>
        <div class="cc-src-val">${p.pbs.listed?p.pbs.price:"미등재"}</div>
        <div class="cc-src-sub">DPMQ: ${p.pbs.dpmq||"—"}</div>
      </div>
      <div class="cc-src cw">
        <div class="cc-src-name">③ Chemist Warehouse</div>
        <div class="cc-src-val">${p.cw.val}</div>
        <div class="cc-src-sub">${p.pbs.listed?"PBS 등재":"소매 채널"}</div>
      </div>
      <div class="cc-src nsw">
        <div class="cc-src-name">④ NSW Health Procurement</div>
        <div class="cc-src-val">${p.nsw.val}</div>
        <div class="cc-src-sub">${p.nsw.contract || p.nsw.note || ""}</div>
      </div>
    </div>
    <div class="cc-footer">
      <div style="display:flex;align-items:center;gap:10px;">
        <span class="bdg ${vColor}">${p.viable}</span>
        <span style="font-size:11.5px;color:var(--muted);">신뢰도 ${p.conf>0?Math.round(p.conf*100)+"%":"—"}</span>
      </div>
      ${hasErr?`<button class="cc-retry" onclick="this.textContent='재크롤링 중...';setTimeout(()=>this.textContent='↺ 재크롤링',1500)">↺ 재크롤링</button>`:""}
    </div>`;
  stack.insertBefore(card,stack.firstChild);
  return card;
}

/* 로딩 pending 카드 (서버 응답 대기 동안 표시) */
function renderPendingCard(label){
  const stack=document.getElementById("crawlStack");
  const em=document.getElementById("crawlEmpty");
  if(em) em.remove();
  const now=new Date();
  const t=now.toLocaleTimeString("ko-KR",{hour:"2-digit",minute:"2-digit",second:"2-digit"});
  const card=document.createElement("div");
  card.className="crawl-card";
  card.innerHTML=`
    <div class="cc-head">
      <div>
        <div class="cc-title">${label} <span style="font-size:11px;color:var(--orange2);">● 크롤링 중</span></div>
        <div class="cc-inn">TGA · PBS · Chemist · NSW 병렬 수집</div>
      </div>
      <div class="cc-time">${t}</div>
    </div>
    <div class="empty-state" style="padding:18px;color:var(--muted);">서버에서 au_crawler 실행 중 — 30초~2분 소요</div>`;
  stack.insertBefore(card,stack.firstChild);
  return card;
}

/* 크롤링 실행 — 실제 API 연동 (mock 폴백 포함) */
async function runCrawl(mode){
  let productId=null, fallbackProd=null, label="";

  if(mode==="select"){
    const idx=document.getElementById("prodSel").value;
    if(idx===""){alert("품목을 선택하세요.");return;}
    const i=parseInt(idx);
    productId = PRODUCT_IDS[i];
    fallbackProd = PRODS[i];
    label = fallbackProd ? fallbackProd.name : productId;
  } else {
    const name=document.getElementById("m_name").value.trim();
    const inn=document.getElementById("m_inn").value.trim();
    if(!name||!inn){alert("품목명과 INN 성분명을 입력하세요.");return;}
    // manual 모드는 현 단계 백엔드 미지원 — 즉시 mock 카드만 표시
    const p={name,inn,str:document.getElementById("m_str").value,
      form:document.getElementById("m_form").value,
      type:document.getElementById("m_type").value,hs:"3004.90",
      tga:{val:"조회 중",num:"크롤링 필요",sched:"—"},
      pbs:{listed:false,price:"조회 중",dpmq:"—"},
      cw:{val:"조회 중"},nsw:{val:"조회 중"},viable:"분석 중",conf:0.3};
    setStep1(2);
    renderCrawlCard(p);
    showToast("신약 직접 입력은 백엔드 미지원 — mock 데이터만 표시됩니다");
    return;
  }

  // 진출 적합 분석 시작 — 모든 결과 영역은 계속 숨김 유지.
  // 파이프라인(크롤링→분석→논문→PDF) 완료 후 한 번에 노출한다.
  setStep1(2);
  const pending = renderPendingCard(label);
  setTimeout(()=>setStep1(3),800);

  // 1) POST /api/crawl — 크롤러 실행
  let crawlOk=false;
  try{
    const res=await fetch("/api/crawl",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({product_id:productId}),
    });
    const body=await res.json().catch(()=>({}));
    crawlOk=!!body.ok;
  }catch(e){crawlOk=false;}

  setStep1(4);

  // 2) GET /api/data/{product_id} — 결과 조회
  let prod=null;
  try{
    const res=await fetch("/api/data/"+encodeURIComponent(productId));
    if(!res.ok) throw new Error("no data");
    const row=await res.json();
    prod=mapRowToProd(row);
  }catch(e){
    // 서버/DB 실패 → mock 폴백
    if(fallbackProd){
      prod=fallbackProd;
      showToast("⚠ API 실패 — mock 데이터로 표시");
    }
  }

  if(pending) pending.remove();
  if(prod){
    renderCrawlCard(prod);
    // 크롤링 성공 → 자동으로 1공정 시장분석 이어서 실행 (이때까지도 UI는 숨김 유지)
    await runAnalysis(1);
    // 파이프라인 전체 완료 → 크롤링 결과 + 보고서 + PDF 를 동시에 노출
    document.querySelectorAll(".crawl-head, #crawlStack").forEach(el => { el.style.display = ""; });
    const pvDone = document.getElementById("rptPreview1");
    if(pvDone){
      pvDone.style.display = "block";
      document.querySelector(".crawl-head").scrollIntoView({behavior:"smooth",block:"start"});
    }
  } else {
    // 폴백도 없고 API 도 실패한 경우 — 에러 카드 재삽입 + 크롤링 영역 노출
    document.querySelectorAll(".crawl-head, #crawlStack").forEach(el => { el.style.display = ""; });
    const stack=document.getElementById("crawlStack");
    const err=document.createElement("div");
    err.className="crawl-card has-error";
    err.innerHTML=`
      <div class="cc-head"><div><div class="cc-title">${label}</div>
        <div class="cc-inn">크롤링 ${crawlOk?"완료":"실패"} · Supabase 조회 실패</div></div></div>
      <div class="empty-state" style="padding:16px;color:var(--red);">
        데이터 조회 실패 — .env 의 SUPABASE 키 확인 필요
        <button class="cc-retry" style="margin-left:10px;" onclick="runCrawl('select')">↺ 재크롤링</button>
      </div>`;
    stack.insertBefore(err,stack.firstChild);
    crawlCount++;
    document.getElementById("crawlCount").textContent=crawlCount;
  }
}

/* ═══════════════════════════════════════════════════════════════
 *  3단계 워크플로우:
 *    Step 1. runAnalysis(1)         — Claude + Perplexity 호출, 블록/레퍼런스만 렌더
 *    Step 2. generateReportPreview(1) — A4 미리보기 렌더 (LLM 재호출 없음)
 *    Step 3. printReport() / saveReport(1) — 인쇄 / DB 저장 (이미 분리되어 있음)
 *
 *  Step 1 결과는 _currentAnalysisData 전역에 캐시해서 Step 2 에서 재사용.
 * ═══════════════════════════════════════════════════════════════ */
const reportStore = [];
let _currentAnalysisData = null;  // Step 1 결과 캐시 (Step 2 에서 사용)

async function runAnalysis(n){
  // Step 1 — 시장분석 실행: 크롤링 row → Claude + Perplexity 호출
  const names = {1:"1공정 시장분석",2:"2공정 수출전략",3:"3공정 유망 바이어"};
  const ICON_CHART = '<svg class="btn-icon" viewBox="0 0 24 24"><circle cx="10" cy="10" r="7"/><line x1="15.2" y1="15.2" x2="21" y2="21"/><line x1="7" y1="12" x2="7" y2="10"/><line x1="10" y1="12" x2="10" y2="7"/><line x1="13" y1="12" x2="13" y2="9"/></svg>';
  const ICON_FILE  = '<svg class="btn-icon" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="13" y2="17"/></svg>';
  const btn = document.getElementById("genBtn"+n);

  if(n !== 1){
    // 2/3 공정은 현재 데모 — 기존 동작 유지 (즉시 보고서 표시)
    if(btn){btn.textContent="생성 중...";btn.disabled=true;}
    setTimeout(()=>{
      if(btn){btn.innerHTML=ICON_FILE+names[n]+" 산출";btn.disabled=false;}
      const sb=document.getElementById("saveBtn"+n);
      if(sb) sb.style.display="inline-flex";
    },1400);
    return;
  }

  // 1공정 — 실제 Claude Haiku + Perplexity 호출
  const sel = document.getElementById("prodSel");
  const idx = sel ? sel.value : "";
  const productId = idx !== "" ? PRODUCT_IDS[parseInt(idx)] : null;

  if(!productId){
    alert("품목을 먼저 선택하고 크롤링한 뒤 분석을 실행하세요.");
    return;
  }

  if(btn){btn.textContent="⚙️ Claude + Perplexity 호출 중... (40~60초)";btn.disabled=true;}
  showToast('<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 22h14"/><path d="M5 2h14"/><path d="M17 22v-4.172a2 2 0 0 0-.586-1.414L12 12l-4.414 4.414A2 2 0 0 0 7 17.828V22"/><path d="M7 2v4.172a2 2 0 0 0 .586 1.414L12 12l4.414-4.414A2 2 0 0 0 17 6.172V2"/></svg>시장분석 진행중...');

  let apiData = null;
  try{
    const res = await fetch("/api/report/generate", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({product_id: productId}),
    });
    if(res.ok) {
      apiData = await res.json();
    } else {
      const err = await res.json().catch(()=>({}));
      showToast("⚠ 분석 실패: " + (err.detail || res.status));
    }
  }catch(e){
    showToast("⚠ 네트워크 오류");
  }

  if(btn){btn.innerHTML=ICON_CHART+names[n]+" 실행";btn.disabled=false;}
  setStep1(5);
  _currentAnalysisData = apiData;

  try {
    // 분석 결과 + A4 미리보기 렌더 — 단, rptPreview1 은 여기서 노출하지 않는다.
    // runCrawl 이 파이프라인 전체가 끝난 뒤 한 번에 보여줄 것.
    renderAnalysisBlocks(apiData);

    renderA4Preview(apiData);
    const a4View = document.getElementById("a4PreviewView");
    if(a4View) a4View.style.display = "block";

    _setPreviewButtonsEnabled(true);

    if(apiData && apiData.ok){
      showToast("✅ 시장분석 완료 — PDF 다운로드 준비됨");
    } else if (!apiData) {
      showToast("⚠ API 응답 없음 — Network 탭 확인 필요");
    }
  } catch (renderErr) {
    console.error("[render 에러]", renderErr);
    showToast("⚠ 렌더링 오류: " + (renderErr.message || renderErr));
    const pv = document.getElementById("rptPreview1");
    if (pv) pv.style.display = "block";
  }
}

function generateReportPreview(n){
  // 하위 호환용 — 이제 runAnalysis 에서 자동 호출됨
  if(n !== 1) return;
  if(!_currentAnalysisData){
    alert("먼저 [1공정 시장분석 실행] 버튼으로 분석을 돌려주세요.");
    return;
  }
  try {
    renderA4Preview(_currentAnalysisData);
    const a4View = document.getElementById("a4PreviewView");
    if(a4View){
      a4View.style.display = "block";
      a4View.scrollIntoView({behavior:"smooth",block:"start"});
    }
    _setPreviewButtonsEnabled(true);
  } catch(e){
    console.error("[renderA4Preview 에러]", e);
    showToast("⚠ 미리보기 렌더 오류: " + (e.message || e));
  }
}

async function reanalyze(){
  // 재분석 — LLM만 다시 호출 (크롤링·품목 재선택 없음)
  if(!_currentAnalysisData){
    showToast("먼저 1공정 시장분석을 실행하세요.");
    return;
  }
  await runAnalysis(1);
}

function _setPreviewButtonsEnabled(enabled){
  // 다크바의 A4 PDF / DB 저장 버튼 활성/비활성
  const buttons = document.querySelectorAll("#rptPreview1 [data-preview-btn]");
  buttons.forEach(b => {
    b.disabled = !enabled;
    b.style.opacity = enabled ? "1" : "0.4";
    b.style.cursor = enabled ? "pointer" : "not-allowed";
  });
}

// 기존 코드와의 호환을 위해 generateReport(n) 을 runAnalysis 로 위임
async function generateReport(n){
  return runAnalysis(n);
}

/* A4 PDF 출력 — #rptA4 를 body 직속으로 임시 이동 후 window.print(), 끝나면 원위치.
 * 기존 @media print의 `*:not(#rptA4){display:none}` 전체 선택자는 부모 체인까지
 * 숨겨서 하얀 화면을 만들었음. 이 방식은 부모 체인 영향 없이 형제만 숨긴다. */
function printReport(){
  const a4 = document.getElementById("rptA4");
  if(!a4){ alert("A4 미리보기 영역을 먼저 생성하세요 (보고서 산출 버튼)"); return; }
  const origParent = a4.parentNode;
  const origNext = a4.nextSibling;
  document.body.appendChild(a4);
  document.body.classList.add("printing-a4");

  const restore = () => {
    document.body.classList.remove("printing-a4");
    if (origNext) origParent.insertBefore(a4, origNext);
    else origParent.appendChild(a4);
    window.removeEventListener("afterprint", restore);
  };
  window.addEventListener("afterprint", restore);
  // afterprint 이벤트가 안 오는 브라우저 방어 — 1.5초 뒤 강제 복구
  setTimeout(() => { if (document.body.classList.contains("printing-a4")) restore(); }, 1500);

  window.print();
}

function saveAndDownload(){
  saveReport(1);
  setTimeout(()=>printReport(),300);
}

// block4_regulatory 는 "① TGA Act 1989: 내용\n② GMP PIC/S: 내용\n..." 형식
// 5개 법령으로 파싱 → 각 항목 [법령명 / 이 품목 영향] 2열 테이블
const REG_META = [
  {num:"①", title:"Therapeutic Goods Act 1989", badge:"핵심 장벽", color:"orange"},
  {num:"②", title:"GMP (PIC/S 상호인정)",        badge:"유리",       color:"green"},
  {num:"③", title:"PBS (National Health Act 1953)", badge:"공공조달", color:"blue"},
  {num:"④", title:"KAFTA",                       badge:"활성",       color:"green"},
  {num:"⑤", title:"Customs Regulations",         badge:"확인 필요",  color:"gray"},
];
// 안전한 파서: 번호 기호로 split → 각 조각에서 콜론 뒤 본문만 추출
// 이전 정규식 RegExp("u" flag + 유니코드 문자 클래스) 가 일부 환경에서
// 런타임 SyntaxError 를 내던 것을 예방하기 위해 split 기반으로 재작성.
function parseBlock4(txt){
  if (!txt || typeof txt !== "string") {
    return REG_META.map(m => ({...m, impact: "생성 데이터 없음"}));
  }
  const normalized = txt.replace(/\\n/g, "\n");
  const parts = normalized.split(/([①②③④⑤])/);
  const byNum = {};
  for (let i = 1; i < parts.length; i += 2) {
    const num = parts[i];
    const body = (parts[i+1] || "").trim();
    const colonMatch = body.match(/^[^:：]*[:：]\s*/);
    byNum[num] = colonMatch ? body.slice(colonMatch[0].length).trim() : body;
  }
  return REG_META.map(m => ({...m, impact: byNum[m.num] || "본문 없음"}));
}

function buildReportCards(apiData){
  // apiData: { ok, blocks, refs, llm_model, llm_generated_at, meta } | null
  const blocks  = (apiData && apiData.blocks)  || {};
  const apiRefs = (apiData && Array.isArray(apiData.refs)) ? apiData.refs : [];
  const meta    = (apiData && apiData.meta)    || {};

  // ── 메타 값은 서버 응답의 meta 에서만 읽는다 (DOM 스크래핑 폐기) ──
  const prodName = meta.product_name_ko || "—";
  const prodInn  = meta.inn_normalized  || "—";
  const prodStr  = meta.strength        || "—";
  const prodForm = meta.dosage_form     || "—";
  // hs_code_6 은 "300490" 식 6자리 → "3004.90" 으로 포맷
  const rawHs    = meta.hs_code_6 || "";
  const prodHs   = rawHs.length >= 6 ? `${rawHs.slice(0,4)}.${rawHs.slice(4,6)}` : (rawHs || "—");

  // 수출 적합 판정 (신호등) — 원본: 색 원형 div + 이모지 🟢/🟡/🔴 + 색 배지
  const ev = String(meta.export_viable || "").toLowerCase();
  const viable = ev === "viable" ? "가능"
               : ev === "conditional" ? "조건부"
               : ev === "not_viable" ? "불가" : "분석 중";
  const viableColor = ev === "viable" ? "green"
                    : ev === "conditional" ? "orange"
                    : ev === "not_viable" ? "red" : "gray";

  // 신뢰도 — 7개 크롤링 필드 기반 서버 재계산값
  const cb = meta.confidence_breakdown || {checklist:[], hits:0, total:0};
  const confPct = meta.confidence != null ? Math.round(Number(meta.confidence) * 100) + "%" : "—";
  // 체크리스트 예: "ARTG ✓ · PBS가격 ✓ · 스폰서 ✓ · 소매가 ✓ · TGA스케줄 ✗ · NSW조달 ✗ · DPMQ ✓"
  const checklistStr = (cb.checklist || []).map(c =>
    `<span style="color:${c.collected ? '#8fe3bc' : '#f8b4ae'};">${c.label} ${c.collected ? '✓' : '✗'}</span>`
  ).join(" · ");
  const checklistFooter = cb.total > 0
    ? ` <span style="color:rgba(255,255,255,.6);">(${cb.total}개 중 ${cb.hits}개)</span>`
    : "";

  const caseGrade = ev === "viable" ? "A" : ev === "conditional" ? "B" : "C";
  const now = new Date();

  // 메타바 — 2줄 구조 (1줄: 기본 정보, 2줄: 신뢰도 산정 근거)
  document.getElementById("rptMetabar").innerHTML = `
    <div>${_escapeHtml(prodName)}  ·  ${_escapeHtml(prodInn)}  ·  ${_escapeHtml(prodStr)} ${_escapeHtml(prodForm)}  ·  HS ${_escapeHtml(prodHs)}  ·  Case ${caseGrade}  ·  confidence ${confPct}</div>
    <div style="font-size:10.5px;color:rgba(255,255,255,.75);margin-top:5px;font-weight:500;line-height:1.5;">
      신뢰도 ${confPct} = ${checklistStr}${checklistFooter}
    </div>`;
  const footerText=`생성일: ${now.toLocaleString("ko-KR")} · UPharma Export AI · 이 보고서는 자동 생성 초안이며 전문가 검토가 필요합니다.`;
  const rptFooterEl=document.getElementById("rptFooter");
  if(rptFooterEl){
    rptFooterEl.textContent=footerText;
    rptFooterEl.style.display="block";
  }
  const a4FooterEl=document.getElementById("a4Footer");
  if(a4FooterEl) a4FooterEl.textContent=footerText;

  const block1=`
    <div style="background:var(--card);border:1px solid rgba(23,63,120,.08);border-radius:18px;padding:18px;">
      <div style="font-size:11.5px;font-weight:800;color:var(--muted);margin-bottom:12px;letter-spacing:.04em;">● 수출 적합성 분석 결과</div>
      <div style="font-size:15px;font-weight:800;color:var(--navy);margin-bottom:6px;">
        ${prodName} <span style="font-size:12px;color:var(--muted);font-weight:500;">${prodInn}</span>
      </div>
      <div style="margin-bottom:10px;">
        <div style="font-size:12px;font-weight:700;color:var(--muted);margin-bottom:6px;">핵심 판정</div>
        <div style="display:flex;align-items:center;gap:12px;">
          <div style="width:48px;height:48px;border-radius:50%;background:${viableColor==="green"?"#27ae60":viableColor==="orange"?"#f39c12":"#e74c3c"};
            display:flex;align-items:center;justify-content:center;font-size:20px;">
            ${viableColor==="green"?"🟢":viableColor==="orange"?"🟡":"🔴"}
          </div>
          <span class="bdg ${viableColor}" style="font-size:16px;padding:8px 18px;">${viable}</span>
        </div>
      </div>
    </div>`;

  const reasons=[
    {k:"시장/의료", v: blocks.block2_market      || "⚙️ Claude Haiku 생성 실패 — 재시도 필요"},
    {k:"규제",      v: blocks.block2_regulatory  || "⚙️ Claude Haiku 생성 실패 — 재시도 필요"},
    {k:"무역",      v: blocks.block2_trade       || "⚙️ Claude Haiku 생성 실패 — 재시도 필요"},
    {k:"조달",      v: blocks.block2_procurement || "⚙️ Claude Haiku 생성 실패 — 재시도 필요"},
    {k:"유통",      v: blocks.block2_channel     || "⚙️ Claude Haiku 생성 실패 — 재시도 필요"},
  ];
  const block2=`
    <div style="background:var(--card);border:1px solid rgba(23,63,120,.08);border-radius:18px;padding:18px;">
      <div style="font-size:11.5px;font-weight:800;color:var(--muted);margin-bottom:14px;letter-spacing:.04em;">● 두괄식 판정 근거</div>
      <div style="display:flex;flex-direction:column;gap:8px;">
        ${reasons.map((r,i)=>`
          <div style="background:var(--inner);border-radius:12px;padding:12px 14px;">
            <div style="font-size:11px;font-weight:800;color:var(--muted);margin-bottom:4px;">${i+1}. ${r.k}</div>
            <div style="font-size:13px;color:var(--text);line-height:1.6;">${r.v}</div>
          </div>`).join("")}
      </div>
    </div>`;

  const strats=[
    {k:"진입 채널 전략",  v: blocks.block3_channel  || "⚙️ 생성 실패"},
    {k:"가격 포지셔닝",   v: blocks.block3_pricing  || "⚙️ 생성 실패"},
    {k:"파트너 발굴",     v: blocks.block3_partners || "⚙️ 생성 실패"},
    {k:"리스크·조건",     v: blocks.block3_risks    || "⚙️ 생성 실패"},
  ];
  const block3=`
    <div style="background:var(--card);border:1px solid rgba(23,63,120,.08);border-radius:18px;padding:18px;">
      <div style="font-size:11.5px;font-weight:800;color:var(--muted);margin-bottom:14px;letter-spacing:.04em;">● 시장 진출 전략</div>
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
        ${strats.map(s=>`
          <div style="background:var(--inner);border-radius:12px;padding:14px;">
            <div style="font-size:11.5px;font-weight:800;color:var(--navy);margin-bottom:8px;">${s.k}</div>
            <div style="font-size:12.5px;color:var(--text);line-height:1.6;white-space:pre-line;">${_escapeHtml(s.v)}</div>
          </div>`).join("")}
      </div>
    </div>`;

  document.getElementById("rptBlocks").innerHTML=block1+block2+block3;

  // 하이브리드 레퍼런스 렌더: korean_summary 우선 + venue + citationCount + source 뱃지
  const categoryColor = (cat) => (cat||"").startsWith("거시") ? "blue"
                              : (cat||"").startsWith("규제") ? "orange"
                              : (cat||"").startsWith("가격") ? "green"
                              : "gray";
  const sourceBadge = (src) => {
    if(src === "semantic_scholar") return "Semantic Scholar";
    if(src === "pubmed")           return "PubMed";
    if(src === "perplexity")       return "Perplexity";
    return src || "출처";
  };
  const refsFallback = [
    {category:"거시·시장 분석", title:"Australia Pharmaceutical Industry Overview", venue:"IMARC Group",   source:"perplexity", url:"https://www.imarcgroup.com/"},
    {category:"규제 분석",      title:"TGA — Prescription Medicines Registration", venue:"TGA",           source:"perplexity", url:"https://www.tga.gov.au"},
    {category:"가격·조달 분석", title:"PBS — Fees and Patient Contributions",       venue:"Dept. of Health", source:"perplexity", url:"https://www.pbs.gov.au"},
  ];
  const refs = apiRefs.length ? apiRefs : refsFallback;

  const renderOneRef = (r) => {
    const title = r.title || (r.url || "").replace(/^https?:\/\//,"").replace(/\/$/,"").slice(0,90) || "(제목 없음)";
    const venue = r.venue || "";
    const year = r.year || "";
    const cites = (r.citation_count != null) ? ` · ${r.citation_count} citations` : "";
    const authors = (r.authors && r.authors.length) ? r.authors.join(", ") : "";
    const summary = r.korean_summary || r.tldr || r.abstract || r.snippet || "";
    const srcLabel = sourceBadge(r.source);
    const domain = (r.url || "").replace(/^https?:\/\//,"").split("/")[0];

    return `
      <div style="padding:12px 0;border-bottom:1px solid rgba(23,63,120,.06);">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap;">
          <span class="bdg ${categoryColor(r.category)}" style="font-size:10.5px;padding:2px 8px;">${_escapeHtml(r.category || "")}</span>
          <span style="font-size:10px;color:var(--muted);font-weight:700;">${_escapeHtml(srcLabel)}</span>
          ${venue ? `<span style="font-size:10.5px;color:var(--muted);">${_escapeHtml(venue)}${year ? ` (${year})` : ""}${cites}</span>` : ""}
        </div>
        <div style="font-size:13px;font-weight:700;color:var(--navy);margin-bottom:4px;line-height:1.4;">
          <a href="${r.url || "#"}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none;">${_escapeHtml(title)}</a>
        </div>
        ${authors ? `<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">${_escapeHtml(authors)}</div>` : ""}
        ${summary ? `<div style="font-size:12px;color:var(--text);line-height:1.6;">${_escapeHtml(summary)}</div>` : ""}
        ${domain ? `<div style="font-size:10px;color:var(--muted);margin-top:4px;">🔗 ${_escapeHtml(domain)}</div>` : ""}
      </div>`;
  };

  const sourceCounts = apiRefs.reduce((acc, r) => {
    const k = r.source || "unknown";
    acc[k] = (acc[k] || 0) + 1;
    return acc;
  }, {});
  const sourceSummary = Object.entries(sourceCounts)
    .map(([k, v]) => `${sourceBadge(k)} ${v}건`)
    .join(" · ");
  const refsFooter = apiRefs.length
    ? `<div style="font-size:11px;color:var(--muted);margin-top:10px;">✅ 하이브리드 검색 · ${sourceSummary}</div>`
    : `<div style="font-size:11px;color:var(--muted);margin-top:10px;">⚙️ 학술 API 호출 전 — 기본 레퍼런스</div>`;

  document.getElementById("rptRefs").innerHTML = refs.map(renderOneRef).join("") + refsFooter;

  // A4 미리보기는 Step 2 (generateReportPreview) 에서 따로 처리
  // — Step 1(분석 실행) 에서는 여기까지만 렌더됨
}

/* ───── Step 1 전용: 블록 + 레퍼런스 카드만 렌더 (A4 제외) ───── */
function renderAnalysisBlocks(apiData){
  // buildReportCards 는 이미 "블록 + 레퍼런스 카드" 까지만 렌더하도록 위에서 수정됨.
  // 여기는 얇은 알리아스.
  buildReportCards(apiData);
}

/* ───── Step 2: 실제 .pdf 파일을 iframe 에 임베드 + 다운로드 버튼 세팅 ───── */
function renderA4Preview(apiData){
  if(!apiData) return;
  const pdfName = apiData.pdf || null;
  const dlBtn = document.getElementById("pdfDownloadBtn");
  const frame = document.getElementById("pdfPreviewFrame");
  const fnEl  = document.getElementById("pdfPreviewFilename");

  if(!pdfName){
    if(dlBtn) dlBtn.style.display = "none";
    if(fnEl)  fnEl.textContent = "⚠ PDF 생성 실패 — 서버 로그 확인";
    if(frame) frame.src = "about:blank";
    return;
  }

  const q = `name=${encodeURIComponent(pdfName)}`;
  if(dlBtn){
    dlBtn.href = `/api/report/download?${q}`;
    dlBtn.style.display = "inline-flex";
  }
  if(fnEl)  fnEl.textContent = pdfName;
  if(frame) frame.src = `/api/report/download?${q}&inline=1`;
}

/* 보고서 저장 — POST /api/reports → GET 재조회 */
async function saveReport(n){
  const names={1:"1공정 시장조사 보고서",2:"2공정 수출전략 보고서",3:"3공정 유망 바이어 보고서"};
  const productId = document.getElementById("prodSel")?.value
    ? PRODUCT_IDS[parseInt(document.getElementById("prodSel").value)]
    : null;

  const sb=document.getElementById("saveBtn"+n);
  if(sb){sb.textContent="저장 중...";sb.disabled=true;}

  let ok=false;
  try{
    const res=await fetch("/api/reports",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({
        product_id: productId,
        gong: n,
        title: names[n],
        file_url: null,
        crawled_data: null,
      }),
    });
    const body=await res.json().catch(()=>({}));
    ok=!!body.ok;
  }catch(e){ok=false;}

  if(ok){
    await loadReports();
    showToast("✓ Supabase reports 테이블에 저장되었습니다");
    if(sb){sb.textContent="✓ 저장됨";}
  } else {
    // 저장 실패 → 로컬에만 추가 (화면 유지)
    const subs={1:"TGA·PBS·가격·수출가능성",2:"FOB 역산 3시나리오·채널전략",3:"PSI Top-10·GMP/MAH 필터"};
    const now=new Date();
    const ts=now.toLocaleDateString("ko-KR")+" · "+now.toLocaleTimeString("ko-KR",{hour:"2-digit",minute:"2-digit"});
    reportStore.unshift({id:Date.now(),title:names[n],sub:subs[n],time:ts,gong:n+"공정"});
    renderReports();
    showToast("⚠ API 실패 — 로컬에만 저장됨 (DB 미반영)");
    if(sb){sb.textContent="✓ 저장됨";}
  }
  // window.print() 는 호출하지 않는다 — PDF 출력은 별도 버튼(printReport)의 책임.
}

/* 페이지 로드 시 오늘의 보고서 조회 */
async function loadReports(){
  try{
    const res=await fetch("/api/reports");
    if(!res.ok) return;
    const body=await res.json();
    const items=Array.isArray(body.items)?body.items:[];
    const subs={1:"TGA·PBS·가격·수출가능성",2:"FOB 역산 3시나리오·채널전략",3:"PSI Top-10·GMP/MAH 필터"};
    reportStore.length=0;
    items.forEach(r=>{
      const dt=r.created_at?new Date(r.created_at):new Date();
      reportStore.push({
        id:r.id||Date.now()+Math.random(),
        title:r.title||"보고서",
        sub:subs[r.gong]||"",
        time:dt.toLocaleDateString("ko-KR")+" · "+dt.toLocaleTimeString("ko-KR",{hour:"2-digit",minute:"2-digit"}),
        gong:(r.gong||1)+"공정",
      });
    });
    renderReports();
  }catch(e){/* 무시 — 로컬 상태 유지 */}
}

function renderReports(){
  const cnt=reportStore.length;
  document.getElementById("repCount").textContent=cnt+"건";
  document.getElementById("repDate").textContent="오늘 저장된 보고서 · "+cnt+"건";
  const c=document.getElementById("allReports");
  if(!cnt){c.innerHTML=`<div class="empty-state">저장된 보고서가 없습니다.</div>`;return;}
  c.innerHTML=reportStore.map(r=>`
    <div class="rpt">
      <div class="rt">${r.title}</div>
      <div class="rd">${r.sub}</div>
      <div class="rpt-bot">
        <div><span class="st ok">Completed</span><div class="rpt-time" style="margin-top:3px;">${r.time} · ${r.gong}</div></div>
        <div class="rpt-btns">
          <button class="btn-sm" onclick="dlRpt('${r.id}')">⬇ 다운로드</button>
          <button class="btn-del" onclick="delRpt('${r.id}')">✕ 삭제</button>
        </div>
      </div>
    </div>`).join("");
}

function dlRpt(id){
  const r=reportStore.find(x=>String(x.id)===String(id));if(!r)return;
  const b=new Blob([`UPharma Export AI
${r.title}
생성: ${r.time}`],{type:"text/plain;charset=utf-8"});
  const a=document.createElement("a");a.href=URL.createObjectURL(b);
  a.download=r.title+".txt";a.click();
}
function delRpt(id){
  if(!confirm("삭제하시겠습니까?"))return;
  const i=reportStore.findIndex(x=>String(x.id)===String(id));
  if(i>-1)reportStore.splice(i,1);
  renderReports();
  showToast("✓ 삭제되었습니다");
}

let toastTimer;
function showToast(msg){
  // HTML 지원 — 호출부에서 SVG 아이콘 주입 가능 (모든 호출은 내부 하드코딩이라 XSS 無)
  const t=document.getElementById("toast");t.innerHTML=msg;t.classList.add("show");
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.classList.remove("show"),2400);
}

/* ── 외부 데이터 로더 (메인탭 뉴스/환율 카드 갱신) ── */
function _escapeHtml(s){
  return String(s||"").replace(/[&<>"']/g,c=>(
    {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]
  ));
}

function _findCardByH3(keyword){
  return [...document.querySelectorAll(".card")].find(c=>{
    const h=c.querySelector(".sec h3");
    return h && h.textContent.includes(keyword);
  }) || null;
}

async function loadNews(){
  const list=document.getElementById("news-list");
  if(!list) return;
  let items=[];
  try{
    const res=await fetch("/api/news");
    if(!res.ok) return;
    const body=await res.json();
    items=Array.isArray(body)?body:(body.items||[]);
  }catch(e){return;}
  if(!items.length){ list.innerHTML='<div class="irow" style="color:var(--muted);font-size:12px;text-align:center;padding:20px 0;">뉴스를 불러올 수 없습니다.</div>'; return; }

  list.innerHTML="";
  items.slice(0,6).forEach(n=>{
    const sub=[_escapeHtml(n.source||""),_escapeHtml(n.date||"")].filter(Boolean).join(" · ");
    if(n.link){
      const a=document.createElement("a");
      a.className="irow news-item";
      a.href=n.link;
      a.target="_blank";
      a.rel="noopener";
      a.innerHTML=`<div class="tit">${_escapeHtml(n.title||"—")}</div><div class="sub">${sub}</div>`;
      list.appendChild(a);
    }else{
      const div=document.createElement("div");
      div.className="irow";
      div.innerHTML=`<div class="tit">${_escapeHtml(n.title||"—")}</div><div class="sub">${sub}</div>`;
      list.appendChild(div);
    }
  });
}

function toggleTodo(el){
  el.classList.toggle("done");
}

function addTodoItem(){
  const inp=document.getElementById("todoInput");
  if(!inp) return;
  const text=inp.value.trim();
  if(!text) return;
  const list=document.getElementById("todoCustomList");
  if(!list) return;
  const div=document.createElement("div");
  div.className="todo-item";
  div.setAttribute("onclick","toggleTodo(this)");
  div.innerHTML=`<div class="todo-check"></div><span class="todo-label">${_escapeHtml(text)}</span>`;
  list.appendChild(div);
  inp.value="";
}

async function loadExchange(){
  let d=null;
  try{
    const res=await fetch("/api/exchange");
    if(!res.ok) return;
    d=await res.json();
  }catch(e){return;}
  if(!d || d.aud_krw==null || d.aud_usd==null) return;

  const audKrw=Number(d.aud_krw);
  const audUsd=Number(d.aud_usd);
  const usdKrw=audUsd>0?(audKrw/audUsd):null;
  const audJpy=d.aud_jpy!=null?Number(d.aud_jpy):null;
  const audCny=d.aud_cny!=null?Number(d.aud_cny):null;

  const card=_findCardByH3("환율");
  if(!card) return;

  const subP=card.querySelector(".sec p");
  if(subP) subP.textContent=`1 AUD = ${audKrw.toFixed(2)}원 · FOB 역산 기준`;

  const mainEl=document.getElementById("fx-main");
  if(mainEl) mainEl.innerHTML=audKrw.toFixed(2)+'<span style="font-size:14px;margin-left:4px;color:var(--muted);font-weight:700;">원</span>';

  // 전일 대비 % 변동 뱃지 (yfinance 응답에 pct_change 있을 때만)
  const chgEl=document.getElementById("fx-chg");
  if(chgEl){
    if(d.pct_change!=null && !Number.isNaN(Number(d.pct_change))){
      const pct=Number(d.pct_change);
      const sign=pct>=0?"▲ +":"▼ −";
      const color=pct>=0?"green":"orange";
      chgEl.className=`bdg ${color}`;
      chgEl.textContent=`${sign}${Math.abs(pct).toFixed(2)}% 전일 대비`;
      chgEl.style.display="inline-flex";
    } else {
      chgEl.style.display="none";
    }
  }

  const suffix=(v,u)=>v+'<span style="font-size:14px;margin-left:4px;color:var(--muted);font-weight:700;">'+u+'</span>';
  const setH=(id,html)=>{const el=document.getElementById(id);if(el)el.innerHTML=html;};
  setH("fx-usd-krw", usdKrw!=null?suffix(usdKrw.toFixed(2),"원"):"—");
  setH("fx-aud-usd", suffix(audUsd.toFixed(4),"US$"));
  setH("fx-aud-jpy", audJpy!=null?suffix(audJpy.toFixed(2),"¥"):"—");
  setH("fx-aud-cny", audCny!=null?suffix(audCny.toFixed(4),"元"):"—");

  const tsEl=document.getElementById("fxTimestamp");
  if(tsEl){
    const now=new Date();
    const h=now.getHours();
    const m=String(now.getMinutes()).padStart(2,"0");
    tsEl.textContent=`조회: ${h>=12?"오후":"오전"} ${h%12||12}:${m}`;
  }
}


// ═══════════════════════════════════════════════════════════════════════
// § 2공정 수출전략 — 팀원 싱가포르 프로젝트(Biya121/1st_logic_initial)에서 이식
//   - 이식일: 2026-04-16
//   - 원본 경로: frontend/static/app.js 라인 378-1043 (666줄)
//   - 팀원 원본 JS 로직은 한 글자도 수정 안 함
//   - Australia app.js와 팀원 코드의 헬퍼 함수명 차이만 아래 shim으로 맞춤
//       · _escHtml()   ← Australia의 _escapeHtml()로 위임
//       · _loadReports() ← Australia의 reportStore 전역 배열로 위임
//       · _setText()   ← Australia에 없어서 얇게 추가
//   - 팀원 p2 JS가 호출하는 백엔드 API: /api/p2/upload, /api/p2/pipeline,
//     /api/p2/pipeline/status, /api/p2/pipeline/result, /api/p2/report,
//     /api/exchange (기존), /api/report/download (기존)
// ═══════════════════════════════════════════════════════════════════════

/* ── 호환 shim (팀원 코드 ↔ Australia 기존 헬퍼 이름 매칭) ─────────────── */
if (typeof _escHtml !== 'function') {
  window._escHtml = function(s){
    if (typeof _escapeHtml === 'function') return _escapeHtml(s);
    return String(s == null ? '' : s)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  };
}
if (typeof _loadReports !== 'function') {
  window._loadReports = function(){
    if (typeof reportStore !== 'undefined' && Array.isArray(reportStore)) return reportStore;
    return [];
  };
}
if (typeof _setText !== 'function') {
  window._setText = function(id, value, fallback){
    if (fallback === undefined) fallback = '—';
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = String(value || '').trim() || fallback;
  };
}

/* ── 팀원 원본 p2 JS 블록 시작 (수정 없이 그대로) ─────────────────────── */
/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §6. 2공정 수출전략 (P2)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

let _p2Ready = false;
let _p2Tab = 'ai';
let _p2ManualSeg = 'public';
let _p2AiSeg = 'public';
let _p2SelectedReportId = '';
let _p2AiSelectedReportId = '';
let _p2UploadedReportFilename = '';
let _p2ManualUploadedFilename = ''; // 직접 입력 탭 전용 업로드 파일명 (AI 탭과 독립)
let _p2AiPollTimer = null;
let _p2Manual = _makeP2Defaults();
let _p2LastScenarios = null;

function _makeP2Defaults() {
  return {
    public: [
      { key: 'base_price', label: '기준 입찰가', value: 0, type: 'abs_input', unit: 'AUD', step: 0.5, min: 0, max: 99999, enabled: true, fixed: false, expanded: false, hint: '경쟁사 입찰가 또는 목표 기준가', rationale: '공공 채널은 입찰 경쟁이 강해 기준가 설정이 핵심입니다.' },
      { key: 'exchange', label: '환율 (USD→AUD)', value: 1.0, type: 'abs_input', unit: 'rate', step: 0.0001, min: 0.0001, max: 99, enabled: true, fixed: false, expanded: false, hint: 'USD 입력 시 적용, AUD면 1.0 유지', rationale: '실시간 환율을 반영해 환차 리스크를 줄입니다.' },
      { key: 'pub_ratio', label: '공공 수출가 산출 비율', value: 30, type: 'pct_mult', unit: '%', step: 1, min: 10, max: 60, enabled: true, fixed: false, expanded: false, hint: '기준가 대비 최종 반영 비율', rationale: '입찰·유통·파트너 마진을 반영한 목표 비율입니다.' },
    ],
    private: [
      { key: 'base_het', label: '민간 기준가 (HET/HNA)', value: 0, type: 'abs_input', unit: 'AUD', step: 0.5, min: 0, max: 99999, enabled: true, fixed: false, expanded: false, hint: '소매/입고 기준 가격', rationale: '민간 시장은 소매 가격 구조 역산이 중요합니다.' },
      { key: 'exchange', label: '환율 (USD→AUD)', value: 1.0, type: 'abs_input', unit: 'rate', step: 0.0001, min: 0.0001, max: 99, enabled: true, fixed: false, expanded: false, hint: 'USD 입력 시 적용', rationale: '실시간 환율 반영으로 가격 정합성을 유지합니다.' },
      { key: 'gst', label: 'GST 공제 (÷1.10)', value: 10, type: 'gst_fixed', unit: '%', step: 0, min: 10, max: 10, enabled: true, fixed: true, expanded: false, hint: '호주 GST 10% 고정', rationale: '민간 소비자 가격에서 세금을 분리합니다.' },
      { key: 'retail', label: '소매 마진율', value: 40, type: 'pct_deduct', unit: '%', step: 1, min: 10, max: 60, enabled: true, fixed: false, expanded: false, hint: '체인/약국 마진 차감', rationale: '채널별 마진 차이를 반영합니다.' },
      { key: 'partner', label: '파트너사 마진', value: 20, type: 'pct_deduct', unit: '%', step: 1, min: 0, max: 40, enabled: true, fixed: false, expanded: false, hint: '현지 파트너 수수료', rationale: '현지 영업·등록 비용을 포함합니다.' },
      { key: 'distribution', label: '유통 마진', value: 15, type: 'pct_deduct', unit: '%', step: 1, min: 0, max: 40, enabled: true, fixed: false, expanded: false, hint: '물류/도매 비용', rationale: '유통 구조별 고정비를 반영합니다.' },
    ],
  };
}

function initP2Strategy() {
  if (!document.getElementById('p2-wrap')) return;
  _p2Ready = true;

  const manualSelect = document.getElementById('p2-report-select');
  if (manualSelect) {
    manualSelect.addEventListener('change', (e) => {
      _p2SelectedReportId = e.target.value || '';
      if (_p2SelectedReportId) {
        // 보고서를 선택하면 업로드한 파일은 해제 (AI 탭과 동일한 패턴)
        _p2ManualUploadedFilename = '';
        const upEl = document.getElementById('p2-manual-upload-status');
        if (upEl) upEl.style.display = 'none';
        const upText = document.getElementById('p2-manual-upload-text');
        if (upText) upText.textContent = 'PDF 파일 선택';
        const fileInput = document.getElementById('p2-manual-pdf-file');
        if (fileInput) fileInput.value = '';
      }
      _renderP2ReportBrief();
      _p2FillBaseFromReport();
      _renderP2Manual();
    });
  }

  const aiSelect = document.getElementById('p2-ai-report-select');
  if (aiSelect) {
    aiSelect.addEventListener('change', (e) => {
      _p2AiSelectedReportId = e.target.value || '';
      if (_p2AiSelectedReportId) {
        _p2UploadedReportFilename = '';
        const upEl = document.getElementById('p2-upload-status');
        if (upEl) upEl.style.display = 'none';
      }
    });
  }

  document.querySelectorAll('[data-p2-manual-seg]').forEach((btn) => {
    btn.addEventListener('click', () => {
      _p2ManualSeg = btn.getAttribute('data-p2-manual-seg') || 'public';
      document.querySelectorAll('[data-p2-manual-seg]').forEach((x) => x.classList.remove('on'));
      btn.classList.add('on');
      _renderP2Manual();
    });
  });

  _syncP2ReportsOptions();
  _p2FillExchangeRate();
  switchP2Tab('ai');
  _renderP2Manual();
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
  const desc = document.getElementById('p2-tab-desc');
  if (desc) {
    desc.textContent = _p2Tab === 'ai'
      ? '1공정 보고서를 기반으로 AI가 자동으로 가격을 추출·산정합니다.'
      : '필요한 옵션만 남겨 직접 산정 공식을 구성할 수 있습니다.';
  }
  if (_p2Tab === 'ai') _showP2AiStatus(false, '');
}

function setP2AiSeg(seg) {
  _p2AiSeg = seg === 'private' ? 'private' : 'public';
  document.getElementById('p2-ai-seg-public')?.classList.toggle('on', _p2AiSeg === 'public');
  document.getElementById('p2-ai-seg-private')?.classList.toggle('on', _p2AiSeg === 'private');
  const desc = document.getElementById('p2-ai-seg-desc');
  if (desc) {
    desc.textContent = _p2AiSeg === 'public'
      ? '공공 시장: PBS 공공급여 채널 · 주별 병원조달(HealthShare NSW 등) 기준'
      : '민간 시장: Chemist Warehouse 등 약국 체인 · 소매 유통 구조 기준';
  }
}

async function handleP2FileSelect(inputEl) {
  const file = inputEl?.files?.[0];
  const statusEl = document.getElementById('p2-upload-status');
  const textEl = document.getElementById('p2-upload-text');
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    if (statusEl) {
      statusEl.style.display = 'block';
      statusEl.textContent = 'PDF 파일만 업로드 가능합니다.';
    }
    return;
  }

  if (statusEl) {
    statusEl.style.display = 'block';
    statusEl.textContent = '업로드 중…';
  }
  if (textEl) textEl.textContent = file.name;

  try {
    const arr = await file.arrayBuffer();
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
    _p2AiSelectedReportId = '';
    const aiSelect = document.getElementById('p2-ai-report-select');
    if (aiSelect) aiSelect.value = '';
    if (statusEl) statusEl.textContent = `업로드 완료: ${data.filename}`;
  } catch (err) {
    if (statusEl) statusEl.textContent = `업로드 실패: ${err.message}`;
  }
}

/* ── 직접 입력 탭 PDF 업로드 (AI 파이프라인 탭과 동일 UI·독립 상태) ──
 * AI 탭과 별개 상태: 사용자가 두 탭을 오가도 업로드 정보가 섞이지 않음.
 * 업로드 파일명에서 품목을 유추해 GST 자동 전환에도 반영.
 */
async function handleP2ManualFileSelect(inputEl) {
  const file = inputEl?.files?.[0];
  const statusEl = document.getElementById('p2-manual-upload-status');
  const textEl = document.getElementById('p2-manual-upload-text');
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    if (statusEl) {
      statusEl.style.display = 'block';
      statusEl.textContent = 'PDF 파일만 업로드 가능합니다.';
    }
    return;
  }

  if (statusEl) {
    statusEl.style.display = 'block';
    statusEl.textContent = '업로드 중…';
  }
  if (textEl) textEl.textContent = file.name;

  try {
    const arr = await file.arrayBuffer();
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

    _p2ManualUploadedFilename = data.filename;
    // 보고서 선택을 해제하고 업로드 파일을 주 기준으로 사용
    _p2SelectedReportId = '';
    const reportSelect = document.getElementById('p2-report-select');
    if (reportSelect) reportSelect.value = '';

    // 파일명에서 품목 유추 → GST 자동 전환
    const g = _p2ApplyGstForReport(file.name);
    _renderP2Manual();

    if (statusEl) {
      const gstNote = g.kind === 'otc' ? ' · OTC 10% 자동 적용' : ' · 처방약 GST 면제 자동 적용';
      statusEl.textContent = `업로드 완료: ${data.filename}${gstNote}`;
    }
  } catch (err) {
    if (statusEl) statusEl.textContent = `업로드 실패: ${err.message}`;
  }
}

function _showP2AiStatus(show, text) {
  const card = document.getElementById('p2-ai-progress-card');
  const label = document.getElementById('p2-ai-step-label-text');
  if (card) card.style.display = show ? '' : 'none';
  if (label && text) label.textContent = text;
}

function _resetP2AiResultView() {
  const resultSection = document.getElementById('p2-ai-result-section');
  if (resultSection) resultSection.style.display = 'none';
  const dlState = document.getElementById('p2-report-dl-state');
  if (dlState) {
    dlState.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;">
        <span class="report-loading-spinner"></span>
        <span style="font-size:13px;color:var(--muted);">분석 완료 후 보고서가 준비됩니다.</span>
      </div>`;
  }
}

async function runP2AiPipeline() {
  const runBtn = document.getElementById('btn-p2-ai-run');
  const runIcon = document.getElementById('p2-ai-run-icon');
  const selectedReport = _loadReports().find((r) => String(r.id) === String(_p2AiSelectedReportId));
  const reportFilename = _p2UploadedReportFilename || (selectedReport ? (selectedReport.pdf_name || '') : '');

  if (!reportFilename) {
    _showP2AiStatus(true, '실행 전 PDF가 있는 보고서를 선택하거나 PDF를 직접 업로드해 주세요.');
    return;
  }

  if (_p2AiPollTimer) clearInterval(_p2AiPollTimer);
  _resetP2AiResultView();
  _showP2AiStatus(true, 'AI 가격 파이프라인을 시작합니다…');

  if (runBtn) runBtn.disabled = true;
  if (runIcon) runIcon.textContent = '⏳';

  try {
    const res = await fetch('/api/p2/pipeline', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ report_filename: reportFilename, market: _p2AiSeg }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    _p2AiPollTimer = setInterval(_pollP2AiPipeline, 1800);
  } catch (err) {
    _showP2AiStatus(true, `실행 실패: ${err.message}`);
    if (runBtn) runBtn.disabled = false;
    if (runIcon) runIcon.textContent = '▶';
  }
}

async function _pollP2AiPipeline() {
  try {
    const res = await fetch('/api/p2/pipeline/status');
    const data = await res.json();
    if (data.status === 'idle') return;

    const label = data.step_label || '분석 중…';
    _showP2AiStatus(true, label);

    if (data.status === 'done') {
      clearInterval(_p2AiPollTimer);
      _p2AiPollTimer = null;
      const rr = await fetch('/api/p2/pipeline/result');
      const result = await rr.json();
      _renderP2AiResult(result);
      _showP2AiStatus(false, '');
      document.getElementById('btn-p2-ai-run')?.removeAttribute('disabled');
      const runIcon = document.getElementById('p2-ai-run-icon');
      if (runIcon) runIcon.textContent = '▶';
    } else if (data.status === 'error') {
      clearInterval(_p2AiPollTimer);
      _p2AiPollTimer = null;
      _showP2AiStatus(true, `오류: ${data.step_label || '파이프라인 실패'}`);
      document.getElementById('btn-p2-ai-run')?.removeAttribute('disabled');
      const runIcon = document.getElementById('p2-ai-run-icon');
      if (runIcon) runIcon.textContent = '▶';
    }
  } catch (_err) {
    // polling retry
  }
}

function _renderP2AiResult(data) {
  const extracted = data?.extracted || {};
  const analysis = data?.analysis || {};
  const rates = data?.exchange_rates || {};
  const scenarios = Array.isArray(analysis.scenarios) ? analysis.scenarios : [];
  const resultSection = document.getElementById('p2-ai-result-section');
  if (resultSection) resultSection.style.display = '';

  _setText('p2r-product-name', extracted.product_name || '미상');
  _setText('p2r-ref-price-text', extracted.ref_price_text || (extracted.ref_price_aud ? `AUD ${Number(extracted.ref_price_aud).toFixed(2)}` : '추출값 없음'));
  _setText('p2r-verdict', extracted.verdict || '미상');
  _setText('p2r-exchange', rates.aud_krw ? `1 AUD = ${Number(rates.aud_krw).toFixed(2)} KRW` : '환율 정보 없음');
  _setText('p2r-final-price', `AUD ${Number(analysis.final_price_aud || 0).toFixed(2)}`);
  _setText('p2r-formula', analysis.formula_str || '산정 공식 없음');
  _setText('p2r-rationale', analysis.rationale || '산정 이유 없음');

  const scenEl = document.getElementById('p2r-scenarios');
  if (scenEl) {
    if (scenarios.length) {
      scenEl.innerHTML = scenarios.map((s, idx) => {
        const cls = idx === 0 ? 'agg' : idx === 1 ? 'avg' : 'cons';
        return `
          <div class="p2-scenario p2-scenario--${cls}">
            <div class="p2-scenario-top">
              <span class="p2-scenario-name">${_escHtml(String(s.name || `시나리오 ${idx + 1}`))}</span>
              <span class="p2-scenario-price">AUD ${Number(s.price_aud || 0).toFixed(2)}</span>
            </div>
            <div class="p2-scenario-reason">${_escHtml(String(s.reason || ''))}</div>
          </div>`;
      }).join('');
    } else {
      scenEl.innerHTML = '<div class="p2-note">시나리오 데이터가 없습니다.</div>';
    }
  }

  const dlState = document.getElementById('p2-report-dl-state');
  if (dlState && data?.pdf) {
    dlState.innerHTML = `
      <a class="btn-download"
         href="/api/report/download?name=${encodeURIComponent(data.pdf)}"
         target="_blank">PDF 보고서 다운로드</a>`;
  }
}

function _p2FillExchangeRate() {
  const rates = window._exchangeRates;
  if (!rates) return;
  const audUsd = Number(rates.aud_usd);
  if (!audUsd || audUsd <= 0) return;
  const usdToAud = Number((1 / audUsd).toFixed(4));
  ['public', 'private'].forEach((seg) => {
    const opt = _p2Manual[seg].find((x) => x.key === 'exchange');
    if (opt) opt.value = usdToAud;
  });
}

/* ── 품목별 GST 분류 ──────────────────────────────────────────
 * 호주 GST 규정: 처방의약품(S4/S8)은 GST-free(0%), OTC·건강기능식품은 10% 과세
 * Rx (GST 0%): Hydrine, Sereterol, Gadvoa, Rosumeg, Atmeg, Ciloduo, Gastiin CR
 * OTC (GST 10%): Omethyl (Omega-3 건강기능식품)
 */
function _p2ClassifyGst(report) {
  // report.report_title, report.product 또는 문자열 받기
  const src = (report && typeof report === 'object')
    ? String(report.report_title || report.product || report.product_name || '')
    : String(report || '');
  const s = src.toLowerCase();
  // OTC/건강기능식품 판정 — Omethyl 또는 omega-3 키워드
  const isOtc = /omethyl|omega\s*-?\s*3|오메가|omacor/i.test(s);
  return isOtc
    ? { rate: 10, kind: 'otc', label: 'GST 공제 (÷1.10) · OTC 10%', hint: '호주 GST 10% (Omega-3 건강기능식품은 과세)' }
    : { rate: 0,  kind: 'rx',  label: 'GST 공제 (면제) · 처방약 0%', hint: '호주 처방약(S4/S8)은 GST-free — 공제 없음' };
}

function _p2ApplyGstForReport(report) {
  const g = _p2ClassifyGst(report);
  const opt = _p2Manual.private.find((x) => x.key === 'gst');
  if (!opt) return g;
  opt.value = g.rate;
  opt.min = g.rate;
  opt.max = g.rate;
  opt.label = g.label;
  opt.hint = g.hint;
  opt.enabled = g.rate > 0; // 처방약은 GST-free이므로 공제 비활성화
  return g;
}

function _p2FillBaseFromReport() {
  const report = _getP2SelectedReport();
  if (!report) return;
  const hint = _extractAudHint(report.price_hint || report.price_positioning_pbs || '');
  if (!Number.isNaN(hint) && hint > 0) {
    const pub = _p2Manual.public.find((x) => x.key === 'base_price');
    const pri = _p2Manual.private.find((x) => x.key === 'base_het');
    if (pub) pub.value = hint;
    if (pri) pri.value = hint;
  }
  // 품목별 GST 자동 전환 (처방약 0% / OTC 10%)
  _p2ApplyGstForReport(report);
}

function _syncP2ReportsOptions() {
  if (!_p2Ready) return;
  const reports = _loadReports();
  const optionHtml = ['<option value="">보고서를 선택하세요</option>']
    .concat(reports.map((r) => `<option value="${r.id}">${_escHtml(r.report_title || r.product || '보고서')}</option>`))
    .join('');

  const manualSelect = document.getElementById('p2-report-select');
  if (manualSelect) {
    const curr = _p2SelectedReportId;
    manualSelect.innerHTML = optionHtml;
    _p2SelectedReportId = reports.some((r) => String(r.id) === String(curr)) ? curr : '';
    manualSelect.value = _p2SelectedReportId;
  }

  const aiSelect = document.getElementById('p2-ai-report-select');
  if (aiSelect) {
    const curr = _p2AiSelectedReportId;
    aiSelect.innerHTML = optionHtml;
    _p2AiSelectedReportId = reports.some((r) => String(r.id) === String(curr)) ? curr : '';
    aiSelect.value = _p2AiSelectedReportId;
  }

  _renderP2ReportBrief();
}

function _getP2SelectedReport() {
  if (!_p2SelectedReportId) return null;
  return _loadReports().find((r) => String(r.id) === String(_p2SelectedReportId)) || null;
}

function _extractAudHint(text) {
  const src = String(text || '');
  // AUD 12.34  /  A$12.34  /  $12.34 (Chemist Warehouse 표기) 셋 다 인식
  const mRange = src.match(/(?:AUD|A\$|\$)\s*([0-9]+(?:\.[0-9]+)?)\s*[~\-–]\s*([0-9]+(?:\.[0-9]+)?)/i);
  if (mRange) return (Number(mRange[1]) + Number(mRange[2])) / 2;
  const mSingle = src.match(/(?:AUD|A\$|\$)\s*([0-9]+(?:\.[0-9]+)?)/i);
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
  const vc = report.verdict === '적합' ? 'green' : report.verdict === '부적합' ? 'red' : report.verdict !== '—' ? 'orange' : 'gray';
  const priceHint = String(report.price_hint || '').trim() || '가격 힌트 없음';
  const basisTrade = String(report.basis_trade || '').trim() || '무역 근거 없음';
  el.innerHTML = `
    <div class="p2-brief-badge-row">
      <span class="bdg ${vc}">${_escHtml(report.verdict || '—')}</span>
      <span class="p2-brief-product">${_escHtml(report.report_title || report.product || '')}</span>
    </div>
    <div class="p2-brief-grid">
      <div class="p2-brief-item">
        <div class="basis-label">참고 가격</div>
        <div class="basis-value">${_escHtml(priceHint)}</div>
      </div>
      <div class="p2-brief-item p2-brief-item--wide">
        <div class="basis-label">무역 조건</div>
        <div class="basis-value">${_escHtml(basisTrade)}</div>
      </div>
    </div>`;
}

function _calcP2Manual() {
  const seg = _p2ManualSeg;
  const options = _p2Manual[seg].filter((x) => x.enabled);
  if (seg === 'public') {
    const base = Number(options.find((x) => x.key === 'base_price')?.value || 0);
    const ex = Number(options.find((x) => x.key === 'exchange')?.value || 1);
    const ratio = Number(options.find((x) => x.key === 'pub_ratio')?.value || 30);
    let price = base * ex * (ratio / 100);
    const parts = [`AUD ${base.toFixed(2)}`, `× ${ex.toFixed(4)}`, `× ${ratio}%`];
    options.forEach((opt) => {
      if (opt.type === 'pct_add_custom') {
        price *= (1 + Number(opt.value) / 100);
        parts.push(`× (1+${Number(opt.value).toFixed(1)}%)`);
      } else if (opt.type === 'abs_add_custom') {
        price += Number(opt.value);
        parts.push(`+ AUD ${Number(opt.value).toFixed(2)}`);
      }
    });
    return { kup: Math.max(price, 0), formulaStr: `${parts.join('  ')}  =  KUP  AUD ${Math.max(price, 0).toFixed(2)}` };
  }

  let price = 0;
  const parts = [];
  options.forEach((opt) => {
    if (opt.key === 'base_het') {
      price = Number(opt.value);
      parts.push(`AUD ${price.toFixed(2)}`);
    } else if (opt.key === 'exchange' && Number(opt.value) !== 1) {
      price *= Number(opt.value);
      parts.push(`× ${Number(opt.value).toFixed(4)}`);
    } else if (opt.type === 'gst_fixed') {
      const rate = Number(opt.value) || 0;
      if (rate > 0) {
        const divisor = 1 + rate / 100;
        price /= divisor;
        parts.push(`÷ ${divisor.toFixed(2)} (GST ${rate}%)`);
      } else {
        parts.push('GST 면제 (처방약)');
      }
    } else if (opt.type === 'pct_deduct') {
      price *= (1 - Number(opt.value) / 100);
      parts.push(`× (1−${Number(opt.value).toFixed(1)}%)`);
    } else if (opt.type === 'pct_add_custom') {
      price *= (1 + Number(opt.value) / 100);
      parts.push(`× (1+${Number(opt.value).toFixed(1)}%)`);
    } else if (opt.type === 'abs_add_custom') {
      price += Number(opt.value);
      parts.push(`+ AUD ${Number(opt.value).toFixed(2)}`);
    }
  });
  return { kup: Math.max(price, 0), formulaStr: `${(parts.join('  ') || 'AUD 0.00')}  =  KUP  AUD ${Math.max(price, 0).toFixed(2)}` };
}

function _renderP2Manual() {
  const wrapEl = document.getElementById('p2-manual-options');
  const removedEl = document.getElementById('p2-manual-removed');
  const scenarioEl = document.getElementById('p2-manual-scenarios');
  const formulaEl = document.getElementById('p2-formula-preview');
  if (!wrapEl || !removedEl || !scenarioEl || !formulaEl) return;

  const options = _p2Manual[_p2ManualSeg];
  const active = options.filter((x) => x.enabled);
  const inactive = options.filter((x) => !x.enabled);
  wrapEl.innerHTML = active.map((opt) => _p2OptionCardHtml(opt)).join('');
  _bindP2OptionEvents(wrapEl, options);

  removedEl.innerHTML = inactive.length
    ? `<span class="p2-removed-label">복원:</span>${inactive.map((opt) => `<button class="p2-add-btn" data-p2-op="add" data-key="${_escHtml(opt.key)}" type="button">+ ${_escHtml(opt.label)}</button>`).join('')}`
    : '';
  removedEl.querySelectorAll('[data-p2-op="add"]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const item = options.find((x) => x.key === btn.getAttribute('data-key'));
      if (item) {
        item.enabled = true;
        _renderP2Manual();
      }
    });
  });

  _renderP2CustomAddSection();
  const calc = _calcP2Manual();
  formulaEl.textContent = calc.formulaStr;

  const agg = calc.kup * 0.9;
  const avg = calc.kup;
  const cons = calc.kup * 1.1;
  const aggReason = _p2ManualScenarioReason('aggressive', _p2ManualSeg);
  const avgReason = _p2ManualScenarioReason('average', _p2ManualSeg);
  const consReason = _p2ManualScenarioReason('conservative', _p2ManualSeg);
  scenarioEl.innerHTML = _p2ScenarioHtml(agg, avg, cons, aggReason, avgReason, consReason);

  _p2LastScenarios = { mode: 'manual', seg: _p2ManualSeg, base: calc.kup, agg, avg, cons, formulaStr: calc.formulaStr, aggReason, avgReason, consReason, rationaleLines: [] };
  _renderP2PdfSection();
}

function _p2OptionCardHtml(opt) {
  const isInput = opt.type === 'abs_input';
  const isFixed = opt.type === 'gst_fixed';
  const canStep = !isInput && !isFixed && opt.step > 0;

  let valDisplay = '';
  if (isFixed) {
    const rate = Number(opt.value) || 0;
    valDisplay = rate > 0 ? `÷ ${(1 + rate / 100).toFixed(2)} 고정 (GST ${rate}%)` : 'GST 면제 (처방약 0%)';
  }
  else if (opt.type === 'pct_mult') valDisplay = `× ${Number(opt.value).toFixed(0)}%`;
  else if (opt.type === 'pct_deduct') valDisplay = `× (1−${Number(opt.value).toFixed(0)}%)`;
  else if (opt.type === 'pct_add_custom') valDisplay = `× (1+${Number(opt.value).toFixed(1)}%)`;
  else if (opt.unit === 'AUD') valDisplay = `AUD ${Number(opt.value).toFixed(2)}`;
  else if (opt.unit === 'rate') valDisplay = `× ${Number(opt.value).toFixed(4)}`;
  else valDisplay = `+ AUD ${Number(opt.value).toFixed(2)}`;

  const inputVal = opt.unit === 'rate' ? Number(opt.value).toFixed(4) : Number(opt.value).toFixed(2);
  return `
    <div class="p2-step-card">
      <div class="p2-step-header">
        <button class="p2-step-toggle" data-p2-op="toggle" data-key="${_escHtml(opt.key)}" type="button">
          <span class="p2-step-label-text">${_escHtml(opt.label)}</span>
          <span class="p2-step-arrow">${opt.expanded ? '▾' : '▸'}</span>
        </button>
        <div class="p2-step-controls">
          ${isInput
            ? `<input class="p2-step-input" type="number" data-p2-op="input" data-key="${_escHtml(opt.key)}" value="${inputVal}" step="${opt.step}" min="${opt.min}" max="${opt.max}">`
            : canStep
              ? `<span class="p2-step-val-display">${_escHtml(valDisplay)}</span><button class="p2-step-btn" data-p2-op="dec" data-key="${_escHtml(opt.key)}" type="button">−</button><button class="p2-step-btn" data-p2-op="inc" data-key="${_escHtml(opt.key)}" type="button">+</button>`
              : `<span class="p2-step-val-display">${_escHtml(valDisplay)}</span>`
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
        if (!Number.isNaN(v)) item.value = Math.max(item.min, Math.min(item.max, v));
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
        <option value="abs_add_custom">AUD 가산</option>
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
      unit: type === 'abs_add_custom' ? 'AUD' : '%',
      step: type === 'abs_add_custom' ? 0.1 : 1,
      min: 0,
      max: type === 'abs_add_custom' ? 9999 : 100,
      enabled: true,
      fixed: false,
      expanded: false,
      hint: '사용자 추가 옵션',
      rationale: '',
    });
    _renderP2Manual();
  });
}

function _p2ManualScenarioReason(type, seg) {
  if (type === 'aggressive') {
    return seg === 'public'
      ? '입찰 경쟁 대응을 위해 점유율 확보 우선의 공격적 가격입니다.'
      : '민간 채널 진입 초기 확산을 위한 공격적 가격입니다.';
  }
  if (type === 'average') {
    return '현재 입력 옵션을 그대로 반영한 기준 시나리오입니다.';
  }
  return seg === 'public'
    ? '재입찰 및 환율 변동 리스크를 반영한 보수적 가격입니다.'
    : '유통/재고 리스크를 반영한 보수적 가격입니다.';
}

function _p2ScenarioHtml(agg, avg, cons, aggReason, avgReason, consReason) {
  const card = (name, cls, price, reason) => `
    <div class="p2-scenario p2-scenario--${cls}">
      <div class="p2-scenario-top">
        <span class="p2-scenario-name">${_escHtml(name)}</span>
        <span class="p2-scenario-price">AUD ${Number(price).toFixed(2)}</span>
      </div>
      <div class="p2-scenario-reason">${_escHtml(reason)}</div>
    </div>`;
  return card('공격적인 시나리오', 'agg', agg, aggReason)
    + card('평균 시나리오', 'avg', avg, avgReason)
    + card('보수 시나리오', 'cons', cons, consReason);
}

function _renderP2PdfSection() {
  const el = document.getElementById('p2-manual-pdf-section');
  if (!el) return;
  el.innerHTML = `
    <div class="p2-pdf-bar">
      <span class="p2-pdf-label">2공정 수출가 시나리오 보고서</span>
      <button class="btn-analyze" id="p2-pdf-btn-manual" type="button" style="font-size:13px;padding:8px 18px;">PDF 생성</button>
      <div id="p2-pdf-state-manual" class="p2-pdf-state"></div>
    </div>`;
  document.getElementById('p2-pdf-btn-manual')?.addEventListener('click', _generateP2Pdf);
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
        { name: '공격적인 시나리오', price: sc.agg, reason: sc.aggReason },
        { name: '평균 시나리오', price: sc.avg, reason: sc.avgReason },
        { name: '보수 시나리오', price: sc.cons, reason: sc.consReason },
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
/* ── 팀원 원본 p2 JS 블록 끝 ─────────────────────────────────────────── */

document.addEventListener("DOMContentLoaded",()=>{
  renderReports();
  loadReports();
  loadNews();
  loadExchange();
  if (typeof initP2Strategy === 'function') initP2Strategy();  // 2공정 수출전략 초기화
});
