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
  document.getElementById("genBtn1").disabled=false;

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
  } else {
    // 폴백도 없고 API 도 실패한 경우 — 에러 카드 재삽입
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
    // 분석 결과 + A4 미리보기를 한 번에 렌더 (단일 플로우)
    renderAnalysisBlocks(apiData);
    const pv = document.getElementById("rptPreview1");
    pv.style.display = "block";

    // A4 미리보기 자동 생성 (과거 Step 2 버튼 제거)
    renderA4Preview(apiData);
    const a4View = document.getElementById("a4PreviewView");
    if(a4View) a4View.style.display = "block";

    _setPreviewButtonsEnabled(true);
    pv.scrollIntoView({behavior:"smooth",block:"start"});

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
  if(rptFooterEl) rptFooterEl.textContent=footerText;
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
    {k:"진입 채널 전략",  v: blocks.block3_channel  || "⚙️ 생성 실패", em:true},
    {k:"가격 포지셔닝",   v: blocks.block3_pricing  || "⚙️ 생성 실패"},
    {k:"파트너 발굴",     v: blocks.block3_partners || "⚙️ 생성 실패"},
    {k:"리스크·조건",     v: blocks.block3_risks    || "⚙️ 생성 실패"},
  ];
  const block3=`
    <div style="background:var(--card);border:1px solid rgba(23,63,120,.08);border-radius:18px;padding:18px;">
      <div style="font-size:11.5px;font-weight:800;color:var(--muted);margin-bottom:14px;letter-spacing:.04em;">● 시장 진출 전략</div>
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
        ${strats.map(s=>`
          <div style="background:var(--inner);border-radius:12px;padding:14px;${s.em?"border-left:3px solid var(--orange);":""}">
            <div style="font-size:11.5px;font-weight:800;color:var(--navy);margin-bottom:8px;">${s.k}</div>
            <div style="font-size:12.5px;color:var(--text);line-height:1.6;">${s.v}</div>
          </div>`).join("")}
      </div>
    </div>`;

  const regsParsed = parseBlock4(blocks.block4_regulatory);
  const block4=`
    <div style="background:var(--card);border:1px solid rgba(23,63,120,.08);border-radius:18px;padding:18px;">
      <div style="font-size:11.5px;font-weight:800;color:var(--muted);margin-bottom:14px;letter-spacing:.04em;">● 규제 체크포인트 — 이 품목 수출 시 실무 영향</div>
      <div style="display:flex;flex-direction:column;gap:8px;">
        ${regsParsed.map(r=>`
          <div style="background:var(--inner);border-radius:12px;padding:12px 14px;
            display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">
            <div style="flex:1;">
              <div style="font-size:13px;font-weight:800;color:var(--navy);margin-bottom:3px;">${r.num} ${_escapeHtml(r.title)}</div>
              <div style="font-size:12px;color:var(--text);line-height:1.6;">${_escapeHtml(r.impact)}</div>
            </div>
            <span class="bdg ${r.color}" style="flex-shrink:0;">${_escapeHtml(r.badge)}</span>
          </div>`).join("")}
      </div>
    </div>`;

  document.getElementById("rptBlocks").innerHTML=block1+block2+block3+block4;

  // 하이브리드 레퍼런스 렌더: korean_summary 우선 + venue + citationCount + source 뱃지
  const categoryColor = (cat) => (cat||"").startsWith("거시") ? "blue"
                              : (cat||"").startsWith("규제") ? "orange"
                              : (cat||"").startsWith("가격") ? "green"
                              : "gray";
  const sourceBadge = (src) => {
    if(src === "semantic_scholar") return "🎓 Semantic Scholar";
    if(src === "pubmed")           return "🔬 PubMed";
    if(src === "perplexity")       return "🔎 Perplexity";
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

/* ───── Step 2 전용: A4 미리보기 영역 렌더 (SG 팀 레이아웃 준수) ───── */
function renderA4Preview(apiData){
  if(!apiData) return;
  const blocks = apiData.blocks || {};
  const apiRefs = Array.isArray(apiData.refs) ? apiData.refs : [];
  const meta = apiData.meta || {};

  const esc = _escapeHtml;
  const prodName = meta.product_name_ko || "—";
  const prodInn  = meta.inn_normalized  || "—";
  const prodStr  = meta.strength        || "";
  const prodForm = meta.dosage_form     || "";
  const rawHs    = meta.hs_code_6 || "";
  const prodHs   = rawHs.length >= 6 ? `${rawHs.slice(0,4)}.${rawHs.slice(4,6)}` : (rawHs || "—");

  const ev = String(meta.export_viable || "").toLowerCase();
  const viable = ev === "viable" ? "가능" : ev === "conditional" ? "조건부"
              : ev === "not_viable" ? "불가" : "분석 중";
  const confPct = meta.confidence != null ? Math.round(Number(meta.confidence)*100) + "%" : "—";
  const caseGrade = ev === "viable" ? "A" : ev === "conditional" ? "B" : "C";
  const now = new Date();
  const dateStr = now.toISOString().slice(0,10);

  const a4Date = document.getElementById("a4Date");
  const a4MetabarInner = document.getElementById("a4MetabarInner");
  const a4Footer = document.getElementById("a4Footer");
  const rptFooter = document.getElementById("rptFooter");
  if(a4Date) a4Date.textContent = dateStr;
  if(a4MetabarInner){
    const strForm = [prodStr, prodForm].filter(Boolean).join(" ");
    a4MetabarInner.textContent =
      `${prodName} — ${prodInn}${strForm ? " · " + strForm : ""} | HS CODE: ${prodHs}`;
  }
  if(a4Footer && rptFooter) a4Footer.textContent = rptFooter.textContent;

  // ── 2열 테이블 (카테고리 | 내용) 헬퍼
  const kvTable = (rows) => `
    <table class="a4-tbl">
      <tbody>
        ${rows.map(([k,v]) => `
          <tr>
            <th>${esc(k)}</th>
            <td>${esc(v || "—")}</td>
          </tr>`).join("")}
      </tbody>
    </table>`;

  // ── 섹션 1: 수출 적합 판정
  const sec1 = `
    <div class="a4-section">
      <div class="a4-section-title">1. 수출 적합 판정</div>
      ${kvTable([
        ["판정",    `${viable} · HS ${prodHs} · Case ${caseGrade} · 신뢰도 ${confPct}`],
      ])}
    </div>`;

  // ── 섹션 2: 판정 근거 (5축 유지)
  const sec2 = `
    <div class="a4-section">
      <div class="a4-section-title">2. 판정 근거</div>
      ${kvTable([
        ["시장 / 의료", blocks.block2_market],
        ["규제",        blocks.block2_regulatory],
        ["무역",        blocks.block2_trade],
        ["조달",        blocks.block2_procurement],
        ["유통",        blocks.block2_channel],
      ])}
    </div>`;

  // ── 섹션 3: 시장 진출 전략 (4축 유지)
  const sec3 = `
    <div class="a4-section">
      <div class="a4-section-title">3. 시장 진출 전략</div>
      ${kvTable([
        ["진입 채널 권고", blocks.block3_channel],
        ["가격 포지셔닝",  blocks.block3_pricing],
        ["파트너 발굴",    blocks.block3_partners],
        ["리스크 + 조건",  blocks.block3_risks],
      ])}
    </div>`;

  // ── 섹션 4: 규제 체크포인트 (block4_regulatory 파싱 → 5법령 2열 테이블)
  const regsParsed = parseBlock4(blocks.block4_regulatory);
  const sec4 = `
    <div class="a4-section">
      <div class="a4-section-title">4. 규제 체크포인트</div>
      ${kvTable(regsParsed.map(r => [`${r.num} ${r.title}`, r.impact]))}
    </div>`;

  // ── 섹션 5-1: 학술 논문 테이블 (No | 논문 제목 / 출처 | 한국어 요약)
  const sourceLabel = (src) => {
    if(src === "semantic_scholar") return "Semantic Scholar";
    if(src === "pubmed")           return "PubMed";
    if(src === "perplexity")       return "Perplexity";
    return src || "출처";
  };
  const refsForTable = apiRefs.length > 0 ? apiRefs : [];
  const papersRows = refsForTable.map((r, i) => {
    const title = r.title || (r.url || "").replace(/^https?:\/\//,"").slice(0,90) || "(제목 없음)";
    const urlLine = r.url ? `<div class="a4-refs-url">${esc(r.url)}</div>` : "";
    const srcLine = r.source ? `<div class="a4-refs-src">[${esc(sourceLabel(r.source))}]</div>` : "";
    const summary = r.korean_summary || r.tldr || r.abstract || r.snippet || "—";
    return `
      <tr>
        <td class="col-no">${i+1}</td>
        <td>
          <div class="a4-refs-title">${esc(title)}</div>
          ${srcLine}
          ${urlLine}
        </td>
        <td class="col-summary">${esc(summary)}</td>
      </tr>`;
  }).join("");
  const sec5_1 = `
    <div class="a4-subsection-title">5-1. 추천 논문 · 하이브리드 학술 검색</div>
    ${refsForTable.length > 0 ? `
      <table class="a4-refs-tbl">
        <thead>
          <tr>
            <th class="col-no">No.</th>
            <th>논문 제목 / 출처</th>
            <th class="col-summary">한국어 요약</th>
          </tr>
        </thead>
        <tbody>${papersRows}</tbody>
      </table>`
      : `<div class="a4-refs-empty">학술 API 호출 전 또는 결과 없음</div>`}`;

  // ── 섹션 5-2: 사용된 DB/기관 테이블 (호주 데이터 소스)
  const sourcesStatic = [
    {name:"TGA ARTG",           desc:"호주 치료제 등록부(ARTG) — 등록번호·스폰서·스케줄 조회", link:"https://www.tga.gov.au/products/australian-register-therapeutic-goods-artg"},
    {name:"PBS Schedule",       desc:"호주 의약품 급여제도 공개 스케줄 — item code·DPMQ·innovator 지위", link:"https://www.pbs.gov.au"},
    {name:"Chemist Warehouse",  desc:"호주 최대 약국 체인 소매가 참조", link:"https://www.chemistwarehouse.com.au"},
    {name:"NSW Health Procurement", desc:"뉴사우스웨일스주 공공조달 계약 공시", link:"https://buy.nsw.gov.au"},
    {name:"KUP_PIPELINE",       desc:"한국유나이티드제약 내부 파이프라인 DB — 품목 식별자·HS·메타", link:"내부 데이터"},
    {name:"하이브리드 학술 API", desc:"Semantic Scholar → PubMed → Perplexity 순 폴백 학술 검색", link:"내부 데이터"},
  ];
  const sec5_2 = `
    <div class="a4-subsection-title">5-2. 사용된 DB/기관</div>
    <table class="a4-refs-tbl">
      <thead>
        <tr>
          <th style="width:26%;">DB/기관명</th>
          <th>설명</th>
          <th style="width:28%;">링크</th>
        </tr>
      </thead>
      <tbody>
        ${sourcesStatic.map(s => `
          <tr>
            <td><strong>${esc(s.name)}</strong></td>
            <td>${esc(s.desc)}</td>
            <td class="a4-refs-url">${esc(s.link)}</td>
          </tr>`).join("")}
      </tbody>
    </table>`;

  const sec5 = `
    <div class="a4-section">
      <div class="a4-section-title">5. 근거 및 출처</div>
      ${sec5_1}
      ${sec5_2}
    </div>`;

  const a4Blocks = document.getElementById("a4Blocks");
  if(a4Blocks){
    a4Blocks.innerHTML = sec1 + sec2 + sec3 + sec4 + sec5;
  }
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

document.addEventListener("DOMContentLoaded",()=>{
  renderReports();
  loadReports();
  loadNews();
  loadExchange();
});
