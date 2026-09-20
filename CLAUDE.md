# yt-subs — YouTube 자막 수집 · 지식층 파이프라인

## 하네스: yt-subs 개발 하네스

**목표:** Spec-First(PRD·설계 선행) 원칙 아래 파이프라인·대시보드 기능을 구현하고, 검증 게이트(V-U/V-I/V-D)를 통과시킨다.

**트리거:** yt-subs 기능 추가·수정·구현·버그 수정·검증·운영(429/쿠키) 요청 시 `yt-subs-orchestrator` 스킬을 사용하라. 단순 질문·조회는 직접 응답 가능.

**용어 구분:** `kl_harness.py`(FR10)는 제품 내 **질의 하네스**다. 이 문서가 가리키는 것은 **개발 하네스**(`.claude/agents`·`.claude/skills`)이며 둘은 별개다.

**변경 이력:**
| 날짜 | 변경 내용 | 대상 | 사유 |
|------|----------|------|------|
| 2026-08-02 | 초기 구성 (에이전트 3 · 스킬 5) | 전체 | DESIGN v4.0 정합화와 함께 하네스 신규 구축 |
| 2026-08-02 | FR17~20 백엔드 구현(대시보드 추출·진행율·쿠키상태·라이브러리) + 문서 v4.1 동기화 | REQUIREMENTS.md · DESIGN.md · qa-verifier.md | 구현 확정 사항(조건 적용 순서·취소 시 인덱싱 생략·limit 예산 = DQ-14~16)을 스펙에 반영하고, 회귀 기준선을 실측값으로 정정 |
| 2026-08-09 | v1 공개 릴리스 준비 — 죽은 코드 제거(kl_query·server·index.html 폴백), 민감 파일 git 추적 해제, mock 테스트·SKILL.md 경로 이식성 수정, README·LICENSE 정비 | 전체 | GitHub 공개(IntuitionSeeker/yt-subs)를 위한 보안·품질 정리, 히스토리 스쿼시로 v1 시작 |
| 2026-08-22 | 429 방어 강화(FR14.2~14.3, 문서 v4.6) — 배치 휴식 랜덤화(8~12개/45~90초), 429 백오프 후 같은 영상 1회 재시도 | REQUIREMENTS.md · DESIGN.md · config.py · extractor.py · mock_scan_test.py | 실측: 고정 60초 휴식 직후 첫 요청마다 429(2026 패턴 기반 탐지) + 일시 429로 영상 3개 영구 누락 → 서명 제거·재시도로 해소 |
| 2026-08-22 | 재생목록 URL 추출(FR24, 문서 v4.7) — 대시보드에서 `/playlist?list=…` 스캔·조건 추출, 원채널 폴더 저장 + 재생목록 제목 카테고리 병합(DQ-17), pytest 기준선 31→33 | REQUIREMENTS.md · DESIGN.md · dashboard/jobs.py · dashboard/index.html · tests · mock_jobs_test.py · README/USAGE | 재생목록 링크 입력 시 400 나던 것을 신규 기능으로 지원 (사용자 요청: 원채널 밑에 재생목록 별도 추가) |
| 2026-08-22 | 채널 폴더(FR25, 문서 v4.8) — channels.yaml `group` 필드·`POST /channels/group`, 라이브러리 폴더 섹션·병합 전체 보기, 재생목록 추출 시 신규 채널 자동 폴더 지정, pytest 기준선 33→34 | channel_registry.py · dashboard/server.py · dashboard/jobs.py · dashboard/index.html · tests · mock_jobs_test.py · 문서 | 다채널 재생목록 추출 시 라이브러리에 채널이 흩어지는 문제 → 폴더로 묶어 관리 (사용자 요청) |
| 2026-08-22 | FR25 보강(25.8~25.9) — 폴더 모드 내용 검색(채널별 /search 점수순 병합), 추출 탭 폴더 표시·접기, 처음 보는 폴더 기본 접힘 | dashboard/index.html · REQUIREMENTS.md · DESIGN.md · USAGE.md | 폴더 전체 보기에서 검색 불가 제약 해소 + 추출 탭 정리 (사용자 요청) |
| 2026-08-22 | 추출 결과 상세(FR26, 문서 v4.9) — 영상별 이벤트(제목·종류·이유)를 job에 축적(캡 1000), 통계 칩 클릭 → 분류별 영상·이유 패널. CLI 무영향(FR18.1 유지) | extractor.py · dashboard/jobs.py · dashboard/index.html · mock 2종 · 문서 | 오류·수정·스킵이 왜 발생했는지 대시보드에서 바로 확인 (사용자 요청) |
| 2026-08-22 | **v3 (문서 v5.0)** — 챕터 메타(FR27)·Markdown 내보내기(FR28)·RSS 새 영상 감지(FR29, channels.yaml `channel_id` 캐시)·Whisper 전사 폴백(FR30, `transcribe` 명령·`sub_type=whisper`·DQ-18), pytest 기준선 34→38 | meta_collector.py · rss_monitor.py(신규) · transcriber.py(신규) · main.py · config.py · channel_registry.py · dashboard/server.py · dashboard/index.html · requirements.txt · tests · 문서 | GitHub 유사 프로젝트 조사에서 선별한 기능 4종 (사용자 승인, v3 브랜치) |
| 2026-08-23 | 이름 변경(FR31, 문서 v5.1) — 채널(yaml+폴더 이동)·영상 제목(파일명 유지)·카테고리(채널 일괄)·폴더, ChromaDB 메타 동기화(재임베딩 없음), busy 가드, ✏️ UI 4곳, pytest 기준선 38→41 | renamer.py(신규) · channel_registry.py · kl_indexer.py · dashboard/server.py · dashboard/index.html · tests · 문서 | 저장된 채널·재생목록·영상 이름 수정 (사용자 요청) |
| 2026-09-09 | 스캔 정합성(FR32, 문서 v5.2) — 제목 언어 고정(`extractor_args.youtube.lang`, DQ-20)으로 스캔·라이브러리 제목 불일치 해소, `ChannelRegistry.resolve_name` 신설로 등록명≠URL핸들 채널의 `extracted` 오판·중복 폴더 생성 차단(DQ-19), pytest 기준선 41→43 | extractor.py · channel_registry.py · dashboard/jobs.py · tests · mock_jobs_test.py · qa-verifier.md · 문서 | 대시보드 추출 탭에서 제목이 한/영으로 뒤바뀌고 기추출 표시가 안 나오던 문제 (사용자 보고) |
| 2026-09-10 | 증분 인덱싱·인덱싱 진행율(FR33, 문서 v5.3) — `KLIndexer._unchanged`가 ChromaDB 기존 청크(id·문서 본문·메타)를 대조해 변경 없으면 임베딩 생략(DQ-21), `index_all(on_progress=)` → job `index_stage`/`index_done`/`index_total` → 프론트 배지·바 전환(DQ-22), pytest 기준선 43→44 | kl_indexer.py · dashboard/jobs.py · dashboard/index.html · tests · mock_jobs_test.py · 문서 | 신규 3개 추출에 채널 전체 100개를 재임베딩해 30분 소요 + 인덱싱 진행 표시가 없어 멈춘 것처럼 보임 (사용자 보고) |
| 2026-09-20 | Whisper 전사 진행률(FR30.6, 문서 v5.4) — `progress_percent`·`with_progress` 신설, 세그먼트 스트림 통과 방식(지연 생성자 1회 소비)으로 10% 단위 CLI 로그 + `on_progress` 콜백 훅, pytest 기준선 44→47 | transcriber.py · tests/test_unit.py · REQUIREMENTS.md · DESIGN.md | 전사가 영상당 수 분인데 진행 표시가 없어 멈춘 것처럼 보임 — 인덱싱(FR33)에서 이미 겪은 문제의 전사판 |
| 2026-09-20 | **검색 기반 일괄 추출(FR34, 문서 v5.5)** — 검색어→후보 스캔(조건 ⓐ개수 상한 ⓑN초 미만 제외 ⓒ`sp` 프리셋+`date_range` 2층), 원채널 저장·신규 채널만 폴더 지정, 검색 유입 채널 `auto_run:false`로 `run`·`transcribe` 무인자 순회 제외(공통 헬퍼 `main.bulk_targets`). 쇼츠 라벨 부재 실측(DQ-23)에 따른 영상 길이 노출(FR20.5~20.6, 백필 없음). QA가 찾은 **기존 결함** 수정 — 배치 휴식 카운터가 `run()` 지역 변수라 그룹 전환마다 리셋되던 것을 `extractor.BatchRest`+`run(rest_state=)`로 분리해 공통 워커에서 공유(FR14.2 경계 명문화, DQ-30). 신규 결정 DQ-23~30, 검증 V-U18~21·V-D14~16, pytest 기준선 47→**60 passed/1 skipped** | jobs.py · server.py · index.html · channel_registry.py · main.py · kl_query.py · extractor.py · tests · mock_jobs_test.py · qa-verifier.md · pipeline-verify/SKILL.md · 문서(REQUIREMENTS·DESIGN·USAGE·README) | 여러 채널에 흩어진 주제 영상을 한 번에 모아 폴더로 관리 (사용자 요청). 쇼츠는 판별 라벨이 없어(185초 실제 쇼츠 반례) "쇼츠 제외" 대신 길이 노출+임계 조정으로 설계, 검색 채널 폭증이 `run` 전체 순회를 429로 몰지 않도록 제외 플래그 도입 |
| 2026-09-20 | 문서 정합·이미지 위생 — V-U 번호 충돌(DESIGN §9.1 ↔ test_unit.py)과 누락(V-U11b·14·15·16) 해소: 실행되는 테스트를 정본으로 문서를 재작성, 재번호 0건(§9.1a 번호 부여 / §9.1b FR 헤더만 있는 검증 분리, V-U3은 "테스트 미구현" 표시). `.dockerignore` 신설 — 쿠키·`output/`(196MB)·`_workspace/`·`.git`이 이미지에 구워지던 것 차단 | DESIGN.md · REQUIREMENTS.md · pipeline-verify/SKILL.md · spec-sync/SKILL.md · .dockerignore(신규) | 문서가 만든 번호가 테스트에 없어 드리프트가 반복됨 → "문서는 번호를 임의 생성하지 않는다" 규칙화. 이미지에 박힌 2026-08-08자 낡은 쿠키가 마운트보다 먼저 쓰이는 것을 QA가 실측 |
