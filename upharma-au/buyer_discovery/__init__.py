"""바이어발굴(Buyer Discovery) — 한국유나이티드제약 호주 수출 3번째 기능.

시장조사(1공정)·수출가격(2공정)과 독립된 기능. 기존 `crawler/` / `stage2/`
코드는 **수정하지 않고** 본 폴더에 격리해 구현.

구조:
  sources/          — 6개 소스 병렬 수집 (TGA·PBS·MA·GBMA·GPCE·성분매칭)
  stage1_filter.py  — 결정적 필터 (AI 없음, 4-pass)
  stage2_haiku.py   — Haiku 5기준 점수화 (`claude-haiku-4-5-20251001` 고정)
  pipeline_collect.py — 6소스 병렬 오케스트레이터
  pipeline.py       — 백그라운드 워커 (사용자 트리거)
  seeds/            — 블랙리스트·alias·INN→치료영역·하드코딩 시트
  db/               — au_buyers UPSERT
  cli.py            — Phase 3.5 dry-run 진입점
"""
