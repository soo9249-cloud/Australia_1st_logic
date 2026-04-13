import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { CatalogProduct, ProductSummary, SitesPayload } from "@/lib/types";

type CatalogJson = { products: CatalogProduct[] };

function formatAud(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) {
    return "—";
  }
  return new Intl.NumberFormat("en-AU", {
    style: "currency",
    currency: "AUD",
    minimumFractionDigits: 2,
  }).format(n);
}

function formatDt(iso: string | null | undefined): string {
  if (!iso) {
    return "—";
  }
  try {
    return new Date(iso).toLocaleString("ko-KR");
  } catch {
    return iso;
  }
}

function formatConfidence(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) {
    return "—";
  }
  return n.toFixed(2);
}

function badgeClassForExport(
  v: string | null | undefined,
): "green" | "orange" | "red" | "gray" {
  if (v === "viable") {
    return "green";
  }
  if (v === "conditional") {
    return "orange";
  }
  if (v === "not_viable") {
    return "red";
  }
  return "gray";
}

function asSites(raw: ProductSummary["sites"]): SitesPayload | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const o = raw as Record<string, unknown>;
  return {
    public_procurement: Array.isArray(o.public_procurement)
      ? (o.public_procurement as SitesPayload["public_procurement"])
      : undefined,
    private_price: Array.isArray(o.private_price)
      ? (o.private_price as SitesPayload["private_price"])
      : undefined,
    paper: Array.isArray(o.paper) ? (o.paper as SitesPayload["paper"]) : undefined,
  };
}

export default function AuMarketPage(): JSX.Element {
  const [catalog, setCatalog] = useState<CatalogProduct[]>([]);
  const [rows, setRows] = useState<ProductSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [runningId, setRunningId] = useState<string | null>(null);
  const [statusMsg, setStatusMsg] = useState<string>("");
  const [timeoutMsg, setTimeoutMsg] = useState<string>("");
  const baselineCrawledRef = useRef<string>("");

  const loadProducts = useCallback(async () => {
    const res = await fetch("/api/au/products");
    if (!res.ok) {
      return;
    }
    const json = (await res.json()) as { data: ProductSummary[] };
    setRows(json.data ?? []);
  }, []);

  useEffect(() => {
    void (async () => {
      const res = await fetch("/au_products.json");
      const data = (await res.json()) as CatalogJson;
      const list = data.products ?? [];
      setCatalog(list);
      if (list.length > 0) {
        setSelectedId(list[0].product_id);
      }
    })();
  }, []);

  useEffect(() => {
    void loadProducts();
  }, [loadProducts]);

  useEffect(() => {
    if (!runningId) {
      return;
    }
    let n = 0;
    const timer = window.setInterval(() => {
      void (async () => {
        n += 1;
        if (n > 40) {
          window.clearInterval(timer);
          setRunningId(null);
          setTimeoutMsg(
            "시간 초과: 폴링을 중지했습니다. GitHub Actions 로그를 확인하세요.",
          );
          return;
        }
        const res = await fetch("/api/au/products");
        if (!res.ok) {
          return;
        }
        const json = (await res.json()) as { data: ProductSummary[] };
        const list = json.data ?? [];
        setRows(list);
        const row = list.find((r) => r.product_id === runningId);
        const at = row?.crawled_at ?? "";
        if (at && at !== baselineCrawledRef.current) {
          window.clearInterval(timer);
          setRunningId(null);
          setStatusMsg("수집이 반영되었습니다.");
          baselineCrawledRef.current = "";
        }
      })();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [runningId]);

  const cat = useMemo(
    () => catalog.find((c) => c.product_id === selectedId),
    [catalog, selectedId],
  );

  const row = useMemo(
    () => rows.find((r) => r.product_id === selectedId),
    [rows, selectedId],
  );

  const sites = useMemo(() => asSites(row?.sites ?? null), [row?.sites]);

  const trigger = async (): Promise<void> => {
    if (!selectedId) {
      return;
    }
    setStatusMsg("");
    setTimeoutMsg("");
    const prev = rows.find((r) => r.product_id === selectedId);
    baselineCrawledRef.current = prev?.crawled_at ?? "";
    setRunningId(selectedId);
    try {
      const res = await fetch("/api/trigger", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ product_id: selectedId }),
      });
      if (!res.ok) {
        const err = (await res.json()) as { error?: string };
        setRunningId(null);
        setStatusMsg(err.error ?? `오류 ${res.status}`);
        return;
      }
      setStatusMsg("크롤링이 시작되었습니다.");
    } catch (e) {
      setRunningId(null);
      setStatusMsg(e instanceof Error ? e.message : "요청 실패");
    }
  };

  const execLabel =
    runningId === selectedId
      ? "실행 중"
      : row?.crawled_at
        ? "완료"
        : "대기";

  const ev = row?.export_viable;
  const bc = badgeClassForExport(ev ?? undefined);

  const pubSites = sites?.public_procurement ?? [];
  const privSites = sites?.private_price ?? [];

  const 핵심판단 =
    ev === "viable"
      ? `${cat?.product_name_ko ?? ""} 품목은 1공정 기준 수출 가능으로 판단됩니다.`
      : ev === "conditional"
        ? `${cat?.product_name_ko ?? ""} 품목은 조건부 수출 가능으로 판단됩니다.`
        : ev === "not_viable"
          ? `${cat?.product_name_ko ?? ""} 품목은 현재 근거 기준 수출이 어렵습니다.`
          : "아직 크롤링 결과가 없거나 판단을 표시할 수 없습니다.";

  const 근거1 = row?.pbs_listed
    ? `PBS에 성분·품목 매칭 근거가 있습니다. (코드: ${row.pbs_item_code ?? "—"})`
    : "PBS 등재·매칭 정보가 없거나 미확인입니다.";

  const 근거2 =
    row?.artg_number || row?.tga_sponsor
      ? `TGA ARTG ${row.artg_number ?? "—"} · 스폰서 ${row.tga_sponsor ?? "—"}`
      : "TGA ARTG·스폰서 정보가 없거나 미확인입니다.";

  return (
    <div className="step1-root">
      <div className="app">
        <header className="topbar">
          <div className="brand">
            <div className="logo">
              <span className="logo-txt">UK</span>
            </div>
            <div>
              <h1>한국유나이티드제약 해외 영업·마케팅 대시보드</h1>
              <p>1공정 · 시장조사 · Australia</p>
            </div>
          </div>

          <div className="top-actions">
            <div className="pill" aria-label="Australia">
              <span className="flag" aria-hidden />
              <span>Australia</span>
            </div>
            <input
              className="search"
              readOnly
              value="8개 품목 선택 (단일 실행)"
            />
          </div>
        </header>

        <main className="main">
          <nav className="nav">
            <Link className="tab" href="/">
              메인 프리뷰
            </Link>
            <span className="tab active">1공정 - 시장조사</span>
            <span className="tab tab-disabled">2공정 - 수출전략</span>
            <span className="tab tab-disabled">3공정 - 바이어 발굴</span>
            <span className="tab tab-disabled">보고서</span>
          </nav>

          <section className="hero">
            <article className="card soft">
              <div className="hero-badge">Step 1 · Run → Result → Report</div>
              <h2>
                {cat
                  ? `${cat.product_name_ko} (${cat.product_id})`
                  : "품목을 선택하세요"}
              </h2>
              <p>
                선택한 품목 1건에 대해 크롤링을 실행하고, Supabase에 저장된
                결과만 이 화면에 표시합니다. 전체 일괄 실행은 제공하지 않습니다.
              </p>
            </article>

            <article className="card">
              <div className="section-head">
                <div>
                  <h3>실행</h3>
                  <p>기본 8개 품목 선택 후 단건 크롤링</p>
                </div>
                <span className="tag">Runner</span>
              </div>

              <div className="control-grid">
                <select
                  className="select"
                  value={selectedId}
                  onChange={(e) => setSelectedId(e.target.value)}
                  aria-label="품목 선택"
                >
                  {catalog.map((p) => (
                    <option key={p.product_id} value={p.product_id}>
                      {p.product_id} · {p.product_name_ko}
                    </option>
                  ))}
                </select>

                <div className="field-grid">
                  <input
                    className="input"
                    readOnly
                    value={cat?.product_name_ko ?? ""}
                    placeholder="품목명"
                  />
                  <input
                    className="input"
                    readOnly
                    value={cat?.inn_normalized ?? ""}
                    placeholder="성분/INN"
                  />
                </div>

                <div className="field-grid">
                  <input
                    className="input"
                    readOnly
                    value={cat?.strength ?? ""}
                    placeholder="함량"
                  />
                  <input
                    className="input"
                    readOnly
                    value={
                      cat
                        ? `${cat.dosage_form ?? ""} / ${cat.hs_code_6 ?? ""}`
                        : ""
                    }
                    placeholder="제형 / HS"
                  />
                </div>

                <textarea
                  className="textarea"
                  readOnly
                  value={
                    row?.evidence_text_ko?.slice(0, 400) ??
                    "크롤링 후 근거 텍스트가 여기에 요약됩니다."
                  }
                />

                <div className="toolbar">
                  <button
                    type="button"
                    className="btn"
                    disabled={!selectedId || runningId !== null}
                    onClick={() => void trigger()}
                  >
                    크롤링 실행
                  </button>
                  <button type="button" className="btn-light" disabled>
                    입력값 반영
                  </button>
                </div>
                {runningId === selectedId ? (
                  <p className="hint">실행 중… GitHub Actions 완료를 기다립니다.</p>
                ) : null}
                {statusMsg ? <p className="hint ok">{statusMsg}</p> : null}
                {timeoutMsg ? <p className="hint err">{timeoutMsg}</p> : null}
              </div>
            </article>
          </section>

          <section className="summary-grid">
            <div className="mini">
              <small>실행 상태</small>
              <strong>{execLabel}</strong>
              <span>
                마지막 업데이트{" "}
                {row?.crawled_at ? formatDt(row.crawled_at) : "—"}
              </span>
            </div>
            <div className="mini">
              <small>수출 가능 여부</small>
              <strong>
                {ev === "viable"
                  ? "가능"
                  : ev === "conditional"
                    ? "조건부"
                    : ev === "not_viable"
                      ? "불가"
                      : "—"}
              </strong>
              <span>
                <span className={`badge ${bc}`}>{ev ?? "—"}</span>
              </span>
            </div>
            <div className="mini">
              <small>Pricing Case</small>
              <strong>{cat?.pricing_case ?? row?.pricing_case ?? "—"}</strong>
              <span>PBS 매칭 유형</span>
            </div>
            <div className="mini">
              <small>Confidence</small>
              <strong>{formatConfidence(row?.confidence ?? null)}</strong>
              <span>source coverage 기반</span>
            </div>
          </section>

          <section className="layout">
            <article className="card">
              <div className="section-head">
                <div>
                  <h3>최종 결과 요약</h3>
                  <p>사용자가 바로 봐야 하는 핵심 결과만 정리</p>
                </div>
                <span className="tag">Result</span>
              </div>

              <div className="list">
                <div className="item">
                  <div className="item-row">
                    <div>
                      <strong>핵심 판단</strong>
                      <p>{핵심판단}</p>
                    </div>
                    <span className={`badge ${bc}`}>
                      {ev === "viable"
                        ? "가능"
                        : ev === "conditional"
                          ? "조건부"
                          : ev === "not_viable"
                            ? "불가"
                            : "—"}
                    </span>
                  </div>
                </div>

                <div className="item">
                  <div className="item-row">
                    <div>
                      <strong>핵심 근거 1</strong>
                      <p>{근거1}</p>
                    </div>
                    <span className="badge gray">PBS</span>
                  </div>
                </div>

                <div className="item">
                  <div className="item-row">
                    <div>
                      <strong>핵심 근거 2</strong>
                      <p>{근거2}</p>
                    </div>
                    <span className="badge gray">TGA</span>
                  </div>
                </div>

                <div className="item">
                  <div className="item-row">
                    <div>
                      <strong>2공정 전달 포인트</strong>
                      <p>
                        {ev ?? "—"} / {cat?.pricing_case ?? "—"} / PBS·TGA 링크
                        · 조달 경로 추가 검토
                      </p>
                    </div>
                    <span className="badge orange">전달</span>
                  </div>
                </div>
              </div>
            </article>

            <article className="card">
              <div className="section-head">
                <div>
                  <h3>보고서 프리뷰</h3>
                  <p>1공정 보고서에 들어갈 내용만 보여주는 영역</p>
                </div>
                <span className="tag">Report</span>
              </div>

              <div className="item item-sp">
                <strong>관련 사이트</strong>
                <div className="site-list">
                  {pubSites.length === 0 && privSites.length === 0 ? (
                    <div className="site">
                      <strong>—</strong>
                      <span className="site-muted">sites 데이터 없음</span>
                    </div>
                  ) : (
                    <>
                      {pubSites.map((s) => (
                        <div key={`p-${s.name}-${s.url}`} className="site">
                          <strong>{s.name}</strong>
                          <a href={s.url} target="_blank" rel="noreferrer">
                            {s.url}
                          </a>
                        </div>
                      ))}
                      {privSites.map((s) => (
                        <div key={`v-${s.name}-${s.url}`} className="site">
                          <strong>{s.name}</strong>
                          <a href={s.url} target="_blank" rel="noreferrer">
                            {s.url}
                          </a>
                        </div>
                      ))}
                    </>
                  )}
                </div>
              </div>

              <div className="item item-sp">
                <strong>수출 가능 시 근거</strong>
                <div className="paragraph">
                  {ev === "viable" || ev === "conditional"
                    ? row?.evidence_text_ko?.trim() || "(근거 텍스트 없음)"
                    : "현재 viable/conditional 판정이 아니면 이 블록은 참고용입니다."}
                </div>
              </div>

              <div className="item item-sp">
                <strong>수출 불가능 시 근거</strong>
                <div className="paragraph">
                  {ev === "not_viable"
                    ? row?.evidence_text_ko?.trim() ||
                      row?.reason_code ||
                      "(사유 코드 확인)"
                    : "직접적인 수출 불가 판정이 아닌 경우, 규제·시장 변동에 따른 리스크를 2공정에서 재검토합니다."}
                </div>
              </div>

              <div className="toolbar">
                <button type="button" className="btn" onClick={() => {}}>
                  보고서 생성
                </button>
                <button type="button" className="btn-light" disabled>
                  초안 저장
                </button>
              </div>
            </article>
          </section>

          <section className="details-wrap">
            <details>
              <summary>
                상세 근거 보기{" "}
                <span className="badge gray">PBS / TGA / Chemist / AusTender</span>
              </summary>
              <div className="details-body">
                <div className="kv-grid">
                  <div className="kv">
                    <small>PBS Listed</small>
                    <span>{row?.pbs_listed === true ? "true" : "false"}</span>
                  </div>
                  <div className="kv">
                    <small>PBS Item Code</small>
                    <span>{row?.pbs_item_code ?? "—"}</span>
                  </div>
                  <div className="kv">
                    <small>PBS Price (AUD)</small>
                    <span>{formatAud(row?.pbs_price_aud ?? null)}</span>
                  </div>
                  <div className="kv">
                    <small>TGA ARTG Number</small>
                    <span>{row?.artg_number ?? "—"}</span>
                  </div>
                  <div className="kv">
                    <small>TGA Sponsor</small>
                    <span>{row?.tga_sponsor ?? "—"}</span>
                  </div>
                  <div className="kv">
                    <small>Retail (AUD)</small>
                    <span>{formatAud(row?.retail_price_aud ?? null)}</span>
                  </div>
                </div>
              </div>
            </details>

            <details>
              <summary>
                백엔드 실행 로그 보기 <span className="badge gray">참고</span>
              </summary>
              <div className="details-body">
                <div className="list">
                  <div className="item">
                    <strong>최근 수집 시각</strong>
                    <p>{formatDt(row?.crawled_at)}</p>
                  </div>
                  <div className="item">
                    <strong>product_id</strong>
                    <p>{selectedId || "—"}</p>
                  </div>
                  <div className="item">
                    <strong>reason_code</strong>
                    <p>{row?.reason_code ?? "—"}</p>
                  </div>
                </div>
              </div>
            </details>
          </section>
        </main>
      </div>

      <style jsx global>{`
        .step1-root {
          --bg: #f3efe8;
          --shell: #fbf8f3;
          --surface: #fffdf9;
          --surface-soft: #f8f3eb;
          --line: #e5ddd0;
          --text: #243247;
          --muted: #738197;
          --navy: #173f78;
          --navy2: #224f91;
          --orange: #f0a13a;
          --orange-deep: #e18e20;
          --green: #2d9870;
          --warn: #c98b28;
          --red: #c8564d;
          --shadow: 0 14px 34px rgba(23, 63, 120, 0.06);
          --shadow-soft: 0 4px 12px rgba(23, 63, 120, 0.04);
          margin: 0;
          min-height: 100vh;
          font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI",
            Roboto, "Noto Sans KR", sans-serif;
          color: var(--text);
          background: radial-gradient(
              circle at top left,
              rgba(240, 161, 58, 0.05),
              transparent 22%
            ),
            radial-gradient(
              circle at top right,
              rgba(23, 63, 120, 0.04),
              transparent 24%
            ),
            linear-gradient(180deg, #f2ede5 0%, #efe9e0 100%);
          padding: 24px;
          box-sizing: border-box;
        }
        .step1-root * {
          box-sizing: border-box;
        }
      `}</style>

      <style jsx>{`
        .app {
          max-width: 1720px;
          margin: 0 auto;
          background: var(--shell);
          border: 1px solid rgba(23, 63, 120, 0.06);
          border-radius: 36px;
          overflow: hidden;
          box-shadow: 0 24px 56px rgba(23, 63, 120, 0.07);
        }
        .topbar {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 18px;
          padding: 24px 28px;
          border-bottom: 1px solid var(--line);
          background: linear-gradient(
            180deg,
            rgba(255, 253, 249, 0.94),
            rgba(251, 248, 243, 0.98)
          );
        }
        .brand {
          display: flex;
          align-items: center;
          gap: 18px;
          min-width: 0;
        }
        .logo {
          width: 66px;
          height: 66px;
          border-radius: 18px;
          background: #fff;
          border: 1px solid rgba(23, 63, 120, 0.08);
          box-shadow: var(--shadow-soft);
          display: grid;
          place-items: center;
          overflow: hidden;
          padding: 8px;
          flex: 0 0 auto;
        }
        .logo-txt {
          font-weight: 900;
          color: var(--navy);
          font-size: 18px;
        }
        .brand h1 {
          margin: 0;
          font-size: 21px;
          line-height: 1.2;
          letter-spacing: -0.03em;
        }
        .brand p {
          margin: 8px 0 0;
          font-size: 13px;
          color: var(--muted);
        }
        .top-actions {
          display: flex;
          align-items: center;
          gap: 12px;
          flex-wrap: wrap;
          justify-content: flex-end;
        }
        .pill {
          height: 54px;
          padding: 0 18px 0 12px;
          border-radius: 999px;
          background: #fff;
          border: 1px solid rgba(23, 63, 120, 0.08);
          box-shadow: var(--shadow-soft);
          display: inline-flex;
          align-items: center;
          gap: 12px;
          color: var(--navy);
          font-weight: 800;
        }
        .flag {
          width: 30px;
          height: 30px;
          border-radius: 50%;
          background: linear-gradient(145deg, var(--navy), var(--navy2));
          box-shadow: 0 4px 10px rgba(23, 63, 120, 0.14);
        }
        .search {
          width: 300px;
          max-width: 100%;
          height: 52px;
          border-radius: 999px;
          border: 1px solid rgba(23, 63, 120, 0.08);
          background: #fff;
          padding: 0 18px;
          color: var(--muted);
          font-size: 14px;
          outline: none;
          box-shadow: var(--shadow-soft);
        }
        .main {
          padding: 22px 28px 28px;
        }
        .nav {
          display: flex;
          gap: 12px;
          flex-wrap: wrap;
          margin-bottom: 22px;
        }
        .tab {
          height: 56px;
          padding: 0 24px;
          border-radius: 999px;
          background: #fff;
          border: 1px solid rgba(23, 63, 120, 0.08);
          color: #6680a3;
          box-shadow: var(--shadow-soft);
          display: inline-flex;
          align-items: center;
          font-weight: 800;
          text-decoration: none;
          cursor: pointer;
        }
        .tab.active {
          background: var(--navy);
          color: #fff;
          box-shadow: 0 12px 24px rgba(23, 63, 120, 0.16);
        }
        .tab-disabled {
          opacity: 0.45;
          cursor: default;
        }
        .card {
          background: var(--surface);
          border: 1px solid rgba(23, 63, 120, 0.06);
          border-radius: 28px;
          box-shadow: var(--shadow);
          padding: 24px;
        }
        .card.soft {
          background: linear-gradient(180deg, #fffdf9 0%, #f8f3eb 100%);
        }
        .hero {
          display: grid;
          grid-template-columns: 1.15fr 0.85fr;
          gap: 20px;
          margin-bottom: 20px;
        }
        .hero-badge {
          display: inline-flex;
          align-items: center;
          padding: 11px 16px;
          border-radius: 999px;
          background: rgba(23, 63, 120, 0.05);
          color: var(--navy);
          font-size: 13px;
          font-weight: 800;
          border: 1px solid rgba(23, 63, 120, 0.06);
          margin-bottom: 18px;
        }
        .hero h2 {
          margin: 0;
          font-size: clamp(30px, 2.6vw, 46px);
          line-height: 1.08;
          letter-spacing: -0.05em;
          color: var(--navy);
        }
        .hero p {
          margin: 16px 0 0;
          color: var(--muted);
          font-size: 15px;
          line-height: 1.72;
          max-width: 95%;
        }
        .section-head {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 12px;
          margin-bottom: 18px;
        }
        .section-head h3 {
          margin: 0;
          font-size: 20px;
          letter-spacing: -0.03em;
        }
        .section-head p {
          margin: 8px 0 0;
          color: var(--muted);
          font-size: 14px;
          line-height: 1.55;
        }
        .tag {
          height: 42px;
          padding: 0 16px;
          border-radius: 999px;
          background: rgba(23, 63, 120, 0.05);
          color: var(--navy);
          display: inline-flex;
          align-items: center;
          font-weight: 800;
          font-size: 12px;
        }
        .control-grid {
          display: grid;
          gap: 12px;
        }
        .field-grid {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 12px;
        }
        .select,
        .input,
        .textarea {
          width: 100%;
          border-radius: 14px;
          border: 1px solid rgba(23, 63, 120, 0.08);
          background: #fff;
          padding: 0 14px;
          font-size: 14px;
          color: var(--text);
          outline: none;
        }
        .select,
        .input {
          height: 48px;
        }
        .textarea {
          min-height: 88px;
          padding: 14px;
          resize: vertical;
        }
        .input[readonly] {
          background: #fbfaf7;
          color: var(--muted);
        }
        .toolbar {
          display: flex;
          gap: 10px;
          flex-wrap: wrap;
          align-items: center;
        }
        .btn {
          height: 44px;
          padding: 0 16px;
          border: none;
          border-radius: 999px;
          background: var(--orange);
          color: #fff;
          font-weight: 800;
          cursor: pointer;
          box-shadow: 0 10px 22px rgba(240, 161, 58, 0.22);
        }
        .btn:hover:not(:disabled) {
          background: var(--orange-deep);
        }
        .btn:disabled {
          opacity: 0.55;
          cursor: not-allowed;
        }
        .btn-light {
          height: 44px;
          padding: 0 16px;
          border-radius: 999px;
          border: 1px solid rgba(23, 63, 120, 0.08);
          background: #fff;
          color: var(--navy);
          font-weight: 800;
          cursor: pointer;
        }
        .hint {
          margin: 0;
          font-size: 13px;
          color: var(--muted);
        }
        .hint.ok {
          color: var(--navy);
          font-weight: 700;
        }
        .hint.err {
          color: var(--red);
          font-weight: 700;
        }
        .summary-grid {
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 14px;
          margin-bottom: 20px;
        }
        .mini {
          background: #fff;
          border: 1px solid rgba(23, 63, 120, 0.06);
          border-radius: 20px;
          padding: 18px;
        }
        .mini small {
          display: block;
          margin-bottom: 10px;
          color: var(--muted);
          font-size: 12px;
          font-weight: 700;
        }
        .mini strong {
          display: block;
          color: var(--navy);
          font-size: 24px;
          letter-spacing: -0.03em;
        }
        .mini span {
          display: block;
          margin-top: 8px;
          color: var(--muted);
          font-size: 12px;
          line-height: 1.5;
        }
        .badge {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          padding: 8px 10px;
          border-radius: 999px;
          font-size: 12px;
          font-weight: 800;
          white-space: nowrap;
        }
        .badge.gray {
          background: rgba(23, 63, 120, 0.06);
          color: var(--navy);
        }
        .badge.green {
          background: rgba(45, 152, 112, 0.14);
          color: var(--green);
        }
        .badge.orange {
          background: rgba(240, 161, 58, 0.18);
          color: #96570a;
        }
        .badge.red {
          background: rgba(200, 86, 77, 0.12);
          color: var(--red);
        }
        .layout {
          display: grid;
          grid-template-columns: 1.05fr 0.95fr;
          gap: 20px;
          margin-bottom: 20px;
        }
        .list {
          display: grid;
          gap: 12px;
        }
        .item {
          background: linear-gradient(180deg, #fffdf9, #f8f3eb);
          border: 1px solid rgba(23, 63, 120, 0.06);
          border-radius: 18px;
          padding: 16px;
        }
        .item-sp {
          margin-bottom: 12px;
        }
        .item strong {
          display: block;
          color: var(--navy);
          font-size: 15px;
          margin-bottom: 8px;
        }
        .item p {
          margin: 0;
          color: var(--muted);
          font-size: 13px;
          line-height: 1.65;
        }
        .item-row {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          align-items: flex-start;
          flex-wrap: wrap;
        }
        .site-list {
          display: grid;
          gap: 10px;
          margin-top: 10px;
        }
        .site {
          padding: 12px;
          border-radius: 14px;
          background: #fff;
          border: 1px solid rgba(23, 63, 120, 0.05);
          display: grid;
          gap: 4px;
        }
        .site strong {
          color: var(--text);
          font-size: 13px;
        }
        .site a {
          color: var(--navy);
          font-size: 12px;
          text-decoration: none;
          word-break: break-all;
        }
        .site-muted {
          font-size: 12px;
          color: var(--muted);
        }
        .paragraph {
          margin-top: 10px;
          padding: 14px;
          border-radius: 16px;
          background: #fff;
          border: 1px solid rgba(23, 63, 120, 0.05);
          color: var(--muted);
          font-size: 13px;
          line-height: 1.72;
        }
        .details-wrap {
          display: grid;
          gap: 14px;
        }
        details {
          background: var(--surface);
          border: 1px solid rgba(23, 63, 120, 0.06);
          border-radius: 20px;
          box-shadow: var(--shadow-soft);
          overflow: hidden;
        }
        summary {
          list-style: none;
          cursor: pointer;
          padding: 18px 20px;
          font-weight: 800;
          color: var(--navy);
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
        }
        summary::-webkit-details-marker {
          display: none;
        }
        .details-body {
          padding: 0 20px 20px;
          display: grid;
          gap: 12px;
        }
        .kv-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 12px;
        }
        .kv {
          background: #fff;
          border: 1px solid rgba(23, 63, 120, 0.05);
          border-radius: 16px;
          padding: 14px;
        }
        .kv small {
          display: block;
          color: var(--muted);
          font-size: 11px;
          margin-bottom: 6px;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.03em;
        }
        .kv span {
          display: block;
          color: var(--text);
          font-size: 13px;
          line-height: 1.55;
          word-break: break-word;
        }
        @media (max-width: 1360px) {
          .hero,
          .layout {
            grid-template-columns: 1fr;
          }
        }
        @media (max-width: 980px) {
          .summary-grid,
          .kv-grid,
          .field-grid {
            grid-template-columns: repeat(2, 1fr);
          }
        }
        @media (max-width: 760px) {
          .summary-grid,
          .kv-grid,
          .field-grid {
            grid-template-columns: 1fr;
          }
        }
      `}</style>
    </div>
  );
}
