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
    nsw:  { val: nswVal, contract: nswContract },
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
        <div class="cc-src-sub">${p.nsw.contract||"조영제 전용"}</div>
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

/* 보고서 생성 */
const reportStore=[];
function generateReport(n){
  const names={1:"1공정 시장조사 보고서",2:"2공정 수출전략 보고서",3:"3공정 유망 바이어 보고서"};
  const btn=document.getElementById("genBtn"+n);
  if(btn){btn.textContent="생성 중...";btn.disabled=true;}
  setTimeout(()=>{
    if(btn){btn.textContent="📄 "+names[n]+" 산출";btn.disabled=false;}
    if(n===1){
      setStep1(5);
      buildReportCards();
      const pv=document.getElementById("rptPreview1");
      pv.style.display="block";
      pv.scrollIntoView({behavior:"smooth",block:"start"});
    } else {
      const sb=document.getElementById("saveBtn"+n);
      if(sb) sb.style.display="inline-flex";
    }
  },1400);
}

function saveAndDownload(){
  saveReport(1);
  setTimeout(()=>window.print(),300);
}

function buildReportCards(){
  // 크롤링 카드에서 메타 추출
  const stack=document.getElementById("crawlStack");
  const firstCard=stack?stack.querySelector(".crawl-card"):null;
  let prodName="—",prodInn="—",prodStr="—",prodForm="—",prodHs="—";
  let viable="분석 중",conf="—",viableColor="gray";
  if(firstCard){
    const t=firstCard.querySelector(".cc-title");
    const inn=firstCard.querySelector(".cc-inn");
    const v=firstCard.querySelector(".bdg");
    if(t) prodName=t.textContent.replace(/\[.*?\]/g,"").trim();
    if(inn){
      const parts=inn.textContent.split("·");
      prodInn=(parts[0]||"").trim();
      prodStr=(parts[1]||"").trim();
      prodForm=(parts[2]||"").trim();
      const hm=inn.textContent.match(/HS ([\d.]+)/);
      if(hm) prodHs=hm[1];
    }
    if(v){viable=v.textContent.trim();viableColor=v.className.replace("bdg ","");}
    const confEl=firstCard.querySelector(".cc-footer span");
    if(confEl) conf=confEl.textContent.replace("신뢰도 ","");
  }
  const caseGrade=viable==="가능"?"A":viable.includes("병원")?"C":viable==="조건부"?"B":"C";
  const now=new Date();
  const dateStr=now.toISOString().slice(0,10);

  document.getElementById("rptMetabar").textContent=
    `${prodName}  ·  ${prodInn}  ·  ${prodStr} ${prodForm}  ·  HS ${prodHs}  ·  Case ${caseGrade}  ·  confidence ${conf}`;
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
    {k:"시장·의료",v:"⚙️ Claude Haiku 생성 예정 (ANTHROPIC_API_KEY 연동 후 자동 생성)"},
    {k:"규제",v:"⚙️ TGA ARTG 등재 현황 및 스케줄 등급 기반 분석 예정"},
    {k:"무역",v:"⚙️ KAFTA FTA 활용 가능성 및 관세 현황 분석 예정"},
    {k:"조달",v:"⚙️ PBS/NSW Health 조달 경로 분석 예정"},
    {k:"유통",v:"⚙️ 채널 구조 및 스폰서 파트너 분석 예정"},
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
    {k:"진입 채널 전략",v:"⚙️ Claude Haiku 생성 예정",em:true},
    {k:"가격 포지셔닝",v:"⚙️ PBS DPMQ 기준 FOB 역산 연동 후 자동 생성"},
    {k:"리스크·조건",v:"⚙️ TGA 등재 일정 / GMP 요건 / 시장 경쟁 분석 예정"},
  ];
  const block3=`
    <div style="background:var(--card);border:1px solid rgba(23,63,120,.08);border-radius:18px;padding:18px;">
      <div style="font-size:11.5px;font-weight:800;color:var(--muted);margin-bottom:14px;letter-spacing:.04em;">● 시장 진출 전략</div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;">
        ${strats.map(s=>`
          <div style="background:var(--inner);border-radius:12px;padding:14px;${s.em?"border-left:3px solid var(--orange);":""}">
            <div style="font-size:11.5px;font-weight:800;color:var(--navy);margin-bottom:8px;">${s.k}</div>
            <div style="font-size:12.5px;color:var(--text);line-height:1.6;">${s.v}</div>
          </div>`).join("")}
      </div>
    </div>`;

  const regs=[
    {t:"Therapeutic Goods Act 1989",d:"ARTG 등재 의무 · TGA 심사 12–18개월",b:"핵심 장벽",c:"orange"},
    {t:"GMP (PIC/S 상호인정)",d:"한국 PIC/S 정회원 → 제조소 실사 면제 가능",b:"유리",c:"green"},
    {t:"PBS (National Health Act 1953)",d:"공공조달 등재 시 가격 통제 수반",b:"공공조달",c:"blue"},
    {t:"KAFTA",d:"2014년 발효 · 의약품 관세 철폐 완료",b:"활성",c:"green"},
  ];
  const block4=`
    <div style="background:var(--card);border:1px solid rgba(23,63,120,.08);border-radius:18px;padding:18px;">
      <div style="font-size:11.5px;font-weight:800;color:var(--muted);margin-bottom:14px;letter-spacing:.04em;">● 규제 체크포인트</div>
      <div style="display:flex;flex-direction:column;gap:8px;">
        ${regs.map(r=>`
          <div style="background:var(--inner);border-radius:12px;padding:12px 14px;
            display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">
            <div>
              <div style="font-size:13px;font-weight:800;color:var(--navy);margin-bottom:3px;">${r.t}</div>
              <div style="font-size:12px;color:var(--muted);">${r.d}</div>
            </div>
            <span class="bdg ${r.c}" style="flex-shrink:0;">${r.b}</span>
          </div>`).join("")}
      </div>
    </div>`;

  document.getElementById("rptBlocks").innerHTML=block1+block2+block3+block4;

  const refs=[
    {t:"Prescription medicines registration process",src:"TGA 2024",url:"https://www.tga.gov.au"},
    {t:"PBS Fees and patient contributions",src:"Dept. of Health 2025",url:"https://www.pbs.gov.au"},
    {t:"KAFTA FTA Portal — Tariff Rates",src:"DFAT",url:"https://ftaportal.dfat.gov.au"},
  ];
  document.getElementById("rptRefs").innerHTML=refs.map(r=>`
    <div style="display:flex;align-items:flex-start;gap:10px;padding:8px 0;
      border-bottom:1px solid rgba(23,63,120,.06);">
      <span style="color:var(--navy);font-size:14px;margin-top:1px;">▶</span>
      <div>
        <div style="font-size:13px;font-weight:700;color:var(--navy);">
          <a href="${r.url}" target="_blank" style="color:inherit;text-decoration:none;">${r.t}</a>
        </div>
        <div style="font-size:11.5px;color:var(--muted);">[${r.src}]</div>
      </div>
    </div>`).join("")
  +"<div style='font-size:11.5px;color:var(--muted);margin-top:8px;'>⚙️ PERPLEXITY_API_KEY 연동 후 실제 논문 자동 첨부</div>";

  document.getElementById("a4Date").textContent=dateStr;
  document.getElementById("a4MetabarInner").textContent=document.getElementById("rptMetabar").textContent;
  document.getElementById("a4Footer").textContent=document.getElementById("rptFooter").textContent;

  const a4rows=[
    ["1. 수출 적합 판정", `판정: ${viable} · HS ${prodHs} · 신뢰도: ${conf}`],
    ["2. 판정 근거", "① 시장·의료: Claude Haiku 생성 예정\n② 규제: TGA 등재 현황 분석 예정\n③ 무역: KAFTA 활용 분석 예정\n④ 조달: PBS/NSW 경로 분석 예정\n⑤ 유통: 채널·스폰서 분석 예정"],
    ["3. 시장 진출 전략", "진입채널 / 가격포지셔닝 / 리스크·조건 — Claude Haiku 생성 예정"],
    ["4. 규제 체크포인트", "① Therapeutic Goods Act 1989 — ARTG 등재 의무\n② GMP PIC/S 상호인정 — 실사 면제 가능\n③ PBS National Health Act — 가격 통제 수반\n④ KAFTA — 관세 0% 활성\n⑤ Customs Regulations — 항암제 수입 확인"],
    ["5. 근거 및 출처", "TGA ARTG · PBS API v3 · Chemist Warehouse · NSW Health Procurement · Perplexity 논문"],
  ];
  document.getElementById("a4Blocks").innerHTML=a4rows.map(([h,v])=>`
    <div style="border:1px solid #e2e8f0;">
      <div style="background:#e2e8f0;padding:5px 8px;font-size:9.5px;font-weight:700;color:#1e293b;">${h}</div>
      <table style="width:100%;border-collapse:collapse;">
        <tr><td style="padding:7px 8px;font-size:9.5px;white-space:pre-line;color:#334155;">${v}</td></tr>
      </table>
    </div>`).join("");
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
    showToast("✓ 보고서가 저장되었습니다");
    if(sb){sb.textContent="✓ 저장됨";}
  } else {
    // 저장 실패 → 로컬에만 추가 (화면 유지)
    const subs={1:"TGA·PBS·가격·수출가능성",2:"FOB 역산 3시나리오·채널전략",3:"PSI Top-10·GMP/MAH 필터"};
    const now=new Date();
    const ts=now.toLocaleDateString("ko-KR")+" · "+now.toLocaleTimeString("ko-KR",{hour:"2-digit",minute:"2-digit"});
    reportStore.unshift({id:Date.now(),title:names[n],sub:subs[n],time:ts,gong:n+"공정"});
    renderReports();
    showToast("⚠ API 실패 — 로컬에만 저장됨");
    if(sb){sb.textContent="✓ 저장됨";}
  }
  if(n===1) window.print();
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
  const t=document.getElementById("toast");t.textContent=msg;t.classList.add("show");
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
  let items=[];
  try{
    const res=await fetch("/api/news");
    if(!res.ok) return;
    const body=await res.json();
    items=Array.isArray(body)?body:(body.items||[]);
  }catch(e){return;}
  if(!items.length) return;

  const card=_findCardByH3("시장 신호");
  if(!card) return;

  // 기존 .irow 4개 제거 후 새로 4개 삽입
  card.querySelectorAll(".irow").forEach(el=>el.remove());
  items.slice(0,4).forEach(n=>{
    const div=document.createElement("div");
    div.className="irow";
    const link=n.link?` href="${_escapeHtml(n.link)}" target="_blank" rel="noopener"`:"";
    const sub=[_escapeHtml(n.source||""),_escapeHtml(n.date||"")].filter(Boolean).join(" · ");
    div.innerHTML=`
      <div class="tit"><a${link} style="color:inherit;text-decoration:none;">${_escapeHtml(n.title||"—")}</a></div>
      <div class="sub">${sub}</div>`;
    card.appendChild(div);
  });
}

async function loadExchange(){
  let d=null;
  try{
    const res=await fetch("/api/exchange");
    if(!res.ok) return;
    d=await res.json();
  }catch(e){return;}
  if(!d || d.aud_krw==null || d.aud_usd==null) return;

  const audKrw=Math.round(Number(d.aud_krw));
  const audUsd=Number(d.aud_usd);
  const krwUsd=audUsd>0?Math.round(audKrw/audUsd):null;

  const card=_findCardByH3("환율");
  if(!card) return;

  // 부제 (1 AUD = XXX원)
  const subP=card.querySelector(".sec p");
  if(subP) subP.textContent=`1 AUD = ${audKrw}원 · FOB 역산 기준`;

  // 큰 KRW/AUD 숫자 — font-size:30px 인 div
  const bigDiv=[...card.children].find(el=>{
    const st=el.getAttribute("style")||"";
    return st.includes("font-size:30px");
  });
  if(bigDiv){
    bigDiv.innerHTML=audKrw+
      '<span style="font-size:14px;margin-left:3px;color:var(--muted);font-weight:700;">원</span>';
  }

  // 하단 USD/AUD · KRW/USD 두 .irow
  const innerIrows=card.querySelectorAll(".irow");
  if(innerIrows.length>=2){
    const usdEl=innerIrows[0].lastElementChild;
    if(usdEl) usdEl.textContent=audUsd.toFixed(4);
    if(krwUsd!=null){
      const krwEl=innerIrows[1].lastElementChild;
      if(krwEl) krwEl.textContent=krwUsd.toLocaleString("ko-KR")+"원";
    }
  }
}

document.addEventListener("DOMContentLoaded",()=>{
  renderReports();
  loadReports();
  loadNews();
  loadExchange();
});
