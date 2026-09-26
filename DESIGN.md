# DESIGN — YouTube 자막 수집 · 지식층 파이프라인

> **버전:** v5.11  
> **작성일:** 2026-08-09  
> **연계 문서:** REQUIREMENTS.md v5.11 (FR1~FR38)  
> **주요 변경:** 질의 인터페이스(FR9)·질의 하네스(FR10)·웹 대시보드(FR11~12) 설계 편입,
> 쿠키/429 방어(FR13~14), 재생목록 카테고리(FR15), 라이브 추출(FR16),
> 대시보드 추출 인터페이스·진행율·쿠키상태·라이브러리(FR17~20) 설계 추가,
> 개발 하네스(§11) 신설  
> **v4.1 (FR17~20 백엔드 구현 확정):** "구현 예정" 표기 제거(§1·§2.9~2.11·§3.7~3.8·§5.7~5.8·§7),
> `Extractor.run`/`process_video` 확장 시그니처와 progress 콜백 계약 확정(§2.2),
> `decide()` 쿠키 인지(§2.3), `list_videos.sub_type`(§2.7), `jobs.py` 상세(§2.10),
> `cookie_health` 중복 기록 방지(§2.11), job phase·API 오류 규칙(§5.7·§5.9),
> 검증 현황 반영(§9.3), 신규 결정 DQ-14~DQ-16(§10)  
> **v4.2 (라이브러리 관리 신규, FR21):** `POST /videos/delete`·`POST /channels/delete`(§2.9),
> `KLIndexer.delete_video`(§2.6), `JobManager.is_busy`(§2.10), 프론트 삭제·클립보드 복사
> UI(F-1 이벤트 위임 패턴 재사용, `stopImmediatePropagation`으로 행 선택과 분리), 검증 V-D12~13(§9.3)
> **v4.3:** 진행 중 라이브 가드 `live_wait`(FR16.5, §2.2), `reflow_sentences` 문장 단위 TXT(FR23), stats 8키
> **v4.4:** Firefox 쿠키 직접 읽기(FR13.6) — `_ydl_opts` cookiesfrombrowser 우선, `has_auth()` 재시도 판정, `/cookies.source`
> **v4.5:** [추출] 탭 채널 현황 카드(FR22, §2.9) — 신규 백엔드 없이 기존 `GET /channels/stats`·`extStart` 재사용
> **v4.6:** 429 방어 강화(FR14.2~14.3, §2.2·§3.2) — 배치 크기·휴식 랜덤화(`BATCH_SIZE_RANGE`·`BATCH_REST_RANGE`), 429 백오프 후 같은 영상 1회 재시도(재시도도 요청 예산 소비, `stats.error`는 최종 포기 기준)
> **v4.7:** 재생목록 URL 추출(FR24, §2.10) — `classify_url` playlist 분기, `_do_scan_playlist`(flat 스캔·채널별 state 조회), `_run_playlist`(채널 그룹 순차 실행·진행율 합산·채널별 인덱싱), `_merged_pl_map`(재생목록 제목 카테고리 병합), 신규 결정 DQ-17
> **v4.8:** 채널 폴더(FR25) — `ChannelRegistry.set_group`(channels.yaml `group` 필드), `POST /channels/group`, `/channels/stats.group`, 라이브러리 폴더 섹션·병합 전체 보기(프론트 병합·원채널 배지·자막/삭제는 영상별 원채널로 라우팅), `_run_playlist` 신규 채널 자동 폴더 지정. 보강: 폴더 모드 내용 검색(채널별 /search 병합, FR25.8), 추출 탭 폴더 표시(FR25.9), 처음 보는 폴더 기본 접힘(FR25.4)
> **v4.9:** 추출 결과 상세(FR26) — `Extractor._event`+`_report(event=)`로 영상별 결과 이벤트를 진행 콜백에 실어 보내고(처리 전 보고에는 event 없음 — 기존 payload 스키마 유지), JobManager가 `job["events"]`에 축적(캡 1,000·재생목록은 channel 부가), 프론트 통계 칩 클릭 → 분류별 영상·이유 패널
> **v5.1:** 이름 변경(FR31) — 신규 `renamer.py`(채널: yaml 키 이동+폴더 rename / 영상: meta.title+chroma metadata / 카테고리: playlists.json+meta 배열+chroma / 폴더: set_group 일괄), `KLIndexer.update_video_metadata`(get(where=video_id)→update, 재임베딩 없음), 전 작업 busy 가드(FR31.5)
> **v5.3:** 증분 인덱싱(FR33.1~33.2) — `KLIndexer._unchanged`가 ChromaDB 기존 청크(id·문서·메타)를 대조해 동일하면 임베딩 생략. 인덱싱 진행율(FR33.3) — `index_all(on_progress=)` → job `index_stage`/`index_done`/`index_total` → 프론트 배지·바 전환. 신규 결정 DQ-21·DQ-22
> **v5.2 (버그 수정):** 스캔 정합성(FR32) — `_ydl_opts`에 `extractor_args.youtube.lang` 주입(DQ-20), `ChannelRegistry.resolve_name` 신설 후 `_do_scan`·`_entry_channel`이 레지스트리 역조회 사용(DQ-19). 신규 결정 DQ-19·DQ-20
> **v5.4:** Whisper 전사 진행률(FR30.6) — `transcriber.progress_percent`(t/duration → 0~100, 비정상 입력은 0)와 `with_progress(segments, duration, on_progress)` 제너레이터 신설. faster-whisper의 segments는 지연 생성자라 **통과시키며** 보고한다(따로 순회하면 재전사·빈 결과). 진행률은 자막 시각 기준이라 단조 증가하지만 VAD가 건너뛴 무음 때문에 경과 시간과 정비례하지는 않는다. CLI는 10% 단위 로그, `on_progress`는 대시보드 접점으로만 열어두고 배선은 하지 않는다(FR30.5 유지)
> **v5.0 (v3):** 챕터(FR27) — `meta_collector.save`가 info.chapters를 `[{start,end,title}]`로 정규화 저장, `/subtitle` 응답 확장, 상세 패널 챕터 링크. Markdown(FR28) — `/export/markdown` 서버 조립 + 프론트 Blob 다운로드. RSS(FR29) — 신규 `rss_monitor.py`(channel_id 해석 1회 캐시 → channels.yaml, 피드 파싱은 표준 xml.etree), `/channels/new`, 추출 탭 🔔 버튼(수동 트리거 — NFR3 유지). Whisper(FR30) — 신규 `transcriber.py`(faster-whisper CPU int8, 오디오 bestaudio 임시 다운로드, 세그먼트→SRT→기존 txt 경로), `sub_type="whisper"` 도입(stats.extracted 포함·decide 스킵), CLI `transcribe` 명령. 신규 결정 DQ-18(whisper sub_type 취급)
> **v5.5:** 검색 기반 일괄 추출(FR34, §2.1·§2.10·§3.9·§5.1·§5.7·§5.9) — `classify_url` search 분기, `_build_search_url`(검색어+`sp` 프리셋+`playlist_items` 상한), `_do_scan_search`(flat 스캔·duration 필터·원채널 state 조회), `_run_search`(`_run_playlist` 재사용, `pl_map={}`), `ChannelRegistry.set_auto_run`·`names(auto_only=)`와 공통 헬퍼 `main.bulk_targets`로 검색 유입 채널을 `cmd_run`·`cmd_transcribe` 전체 순회에서 제외. 신규 결정 DQ-23~DQ-29, 검증 V-U18~20·V-D14~16. 검증 중 드러난 **기존 결함**(배치 휴식 카운터가 `run()` 지역 변수라 그룹 전환마다 리셋 → 검색 경로에서 FR14.2가 무력화, 다채널 재생목록도 동일)을 `extractor.BatchRest` + `run(rest_state=)`로 수정하고 공통 워커 `_run_grouped`가 그룹 루프 바깥에서 공유한다 — 신규 결정 DQ-30, 검증 V-U21, FR14.2에 "휴식은 작업 단위 누적·채널 경계에서 리셋하지 않는다" 명시. 쇼츠 전용 라벨 부재 실측(DQ-23)에 따라 **영상 길이 노출**(FR20.5~20.6) 동반 — `list_videos`가 meta.json의 `duration`·`duration_string`을 그대로 내려보내고(백필 없음, DQ-29) 라이브러리·검색 미리보기가 공유 포맷터로 표시  
> **v5.6:** 폴더(그룹)의 **실제 디렉터리 승격**(FR35, §2.1b·§2.11c·§3.10·§4·§5.1·§5.9) — `config.channel_dir()`이 `channels.yaml`의 `group`을 해석해 `output/<폴더>/<채널>/`를 돌려준다. `config`가 `channel_registry`를 import할 수 없으므로(순환) **config 내부에 yaml 직접 파싱 읽기 전용 해석기 + `(st_mtime_ns, st_size)` 캐시**를 두고, `ChannelRegistry._save()`의 명시 무효화를 2차 안전망으로 건다(DQ-32). 그룹명은 **변환 없이 거부**하는 세그먼트 검증(DQ-33), `output/` 최상위 이름공간 유일성(DQ-34), 이동은 **`os.rename` 단일 호출 + 보상 롤백**(복사 폴백 금지, DQ-35), 마이그레이션은 **명시적 CLI·dry-run 기본·저널 자동 롤백·yaml 무변경**(DQ-36). 신규 모듈 `folder_ops.py`, 신규 CLI `migrate-groups`. 동반 결함 수정: `ChannelRegistry.add()` upsert화 + `resolve_name` 사용(FR7.7~7.9, DQ-37) — FR35 하에서 `group` 소실은 **채널 디스크 경로 변경**으로 격상된다. 신규 결정 DQ-31~DQ-37, 검증 V-U22~27·V-D17~19
> **v5.7 (버그 수정):** 멤버십 감지 언어 비의존화(FR13.7, §2.2·§2.10) — **DQ-20(`extractor_args.youtube.lang=ko`)이 YouTube가 주는 오류 `reason` 문구까지 한국어로 번역해** 영어 키워드만 보던 `Extractor._is_members_only`가 2026-09-09 이후 모든 멤버십 영상을 조용히 `error`로 분류했다(실측 `_workspace/30`). 판정을 신규 잎 모듈 `video_access.py`로 모아 **1차 `availability`(언어 비의존)·2차 메시지(영/한)** 로 바꾸고 추출 경로와 대시보드 스캔(FR17.6)이 같은 규칙을 공유한다. 429는 멤버십 판정보다 **먼저** 확정한다(오분류 시 `_mark_skip`으로 영구 스킵). DQ-20에 **언어 결합 관계**를 명문화. 신규 결정 DQ-38, 검증 V-U29
> **v5.8:** 채널 메모 · 추출 탭 이름 변경 · 탭 간 자동 갱신(FR36, §2.1·§2.9·§2.9b·§2.10·§5.1·§5.9) — 메모는 **신규 스키마가 아니다**: `channels.yaml`의 `note`는 `add()`가 이미 쓰고 FR7.7이 보존까지 약속하지만 **읽는 곳이 0이라 죽어 있던 필드**이며(77채널 전부 `""`), `ChannelRegistry.set_note` + `POST /channels/note` + `/channels/stats.note` 세 접점만으로 살린다(DQ-39). 표시·입력은 기존 채널 카드와 `prompt`를 재사용한다(한 줄·200자, DQ-39). 추출 탭 이름 변경은 **기존 `POST /channels/rename`을 그대로 호출**하고, 그 과정에서 드러난 기존 결함 — 옛 이름으로 캐시된 `scan_id`가 `output/<옛이름>/` **유령 폴더**를 만드는 경로 — 를 `JobManager.invalidate_scans()`로 끊는다(DQ-41, 기존 400 계약 재사용). 갱신은 **`/channels/stats` 단일 출처 + 공통 헬퍼 `refreshChannelViews({names})`**로 통일하되 비활성 탭은 무효화 플래그로 지연 로드하고, 이관은 `renameChannel` 한 곳만 한다(DQ-40). 메모 저장은 `ChannelRegistry`의 read-modify-write 특성 때문에 작업 중 409다(DQ-42). 신규 결정 DQ-39~DQ-42, 검증 V-U30·V-U31·V-D20
> **v5.9 (버그 수정):** 종목코드 추출 문맥화(FR12.2·12.5~12.7, §2.4·§5.3) — 구 규칙 `_TICKER_KR = re.compile(r'\b(\d{6})\b')`은 **6자리 숫자면 무엇이든** 채택했고, 실측 441개 meta에서 `tickers`가 **100% 오탐**이었다(값 있는 26개 = 제목 날짜 `260819` 22종 + 계좌번호 조각 `241686`, `_workspace/34_ticker_bug.md`). 날짜·계좌·전화·사업자번호·URL 조각이 전부 같은 모양이므로 **숫자만 보는 방식 자체가 성립하지 않는다** → `extract_tickers`를 후보 스캔 + 근거/배제 판정으로 재작성한다(강한 근거 = 종목 전용 라벨·거래소 표기 / 약한 근거 = 일반 `코드:`·괄호 단독·나열 / 공통 배제 = URL·숫자 나열 / 약한 근거에만 YYMMDD 배제, DQ-43). 기존 데이터는 신규 CLI `backfill-tickers`(기본 dry-run, meta+desc 재계산이라 네트워크 불필요)로 정리한다. **정상 결과가 빈 값**임을 FR12.6에 못박았다. 검증 V-U32
> **v5.10:** 주기 자동 추출(FR37, §2.2·§2.9·§2.10·§2.13·§3.11·§5.7·§5.9·§5.10·§7) + **NFR3 개정** — 스케줄러는 **serve 프로세스 안의 단일 데몬 스레드**다(신규 잎 모듈 `scheduler.py`). 별도 컨테이너·호스트 cron을 쓰지 않는 이유는 `JobManager._busy`가 **프로세스 지역 싱글턴**이라 외부 프로세스의 추출은 사용자의 대시보드 작업과 동시에 돌기 때문이다(DQ-44). 주기 판정은 맥북 절전 때문에 **정시가 아니라 경과 시간**이고 밀린 주기는 1회만 따라잡는다(DQ-45). 기본 주기는 **3일**이다(선택지 3·7·14·28일, 대시보드에서 변경 — 근거는 FR37.3: RSS 15개 상한 구멍 축소·주기당 버스트 감소·예산 30 적정화·백오프 상한 12일). 상태·설정은 `output/.scheduler.json` 한 파일(원자 교체, DQ-46 — `channels.yaml`의 read-modify-write lost update를 피한다). 실행은 **RSS 선행 → 새 영상 있는 채널만 `_run_grouped` 재사용**(`pl_map={}`·`group_title=None`)이고 RSS 15개 상한은 **무시하되 감지·노출**한다(DQ-47). 무인 전용 안전장치로 **429 2단 회로차단**을 신설했다 — ⓐ `Extractor.run`이 429 중단을 `stats["aborted_429"]`로 **알리게 하고**(현재는 호출자가 구별할 수 없어 `_run_grouped`가 차단 상태로 다음 채널을 계속 두드린다 — **재생목록·검색 경로에도 이미 있던 결함**) ⓑ 주기 간 지수 백오프 `skip_cycles` 1→2→4(DQ-48). 쿠키 경고·백오프는 **상태로만 표현**하고 사용자 설정(`enabled`)을 기계가 되돌려 쓰지 않는다(DQ-49). `run-now`는 우회 경로가 아니라 "지금 도래시키기"다(DQ-50). 신규 결정 DQ-44~DQ-50, 검증 V-U33~V-U34·V-D21
> **v5.11:** 정합 감사 · 데이터 건전성 점검(FR38, §2.14·§3.12·§5.11·§7·§8·§9.1a·§9.3·§11.2) — 신규 잎 모듈 `selfcheck.py` 하나에 `audit`(문서·코드 정합 8검사)와 `doctor`(실데이터 건전성 10검사)를 담고 **CLI만 둘로 분리**한다(DQ-51 — 모듈을 쪼개면 발견 표현·심각도·예외·종료코드 계약이 흩어져 그것이 다음 drift가 된다. 명령을 분리하는 이유는 성격이 아니라 **실행 환경**이다: audit은 `output/` 없이 완결되고 doctor는 `output/`이 전부다). 검사는 `{ID: 함수}` 레지스트리 + `Finding(check, target, message, severity, evidence)` 계약으로 표준화하고 **고치지 않는다** — 문서가 낡았는지 코드가 틀렸는지는 기계가 정할 수 없고(spec-sync 원칙), 잘못된 방향의 자동 수정은 정본을 오염시킨다(DQ-52). 종료코드 `0`/`1`(경고)/`2`(오류)/`3`(**점검 실패** — '이상 없음'과 '확인 못 함'을 같은 코드로 내지 않는다)이고 기본 차단선은 2다. **정밀도 우선**(판정 불가는 침묵)과 **2층 예외 모델**(1차 = 문서 안의 기존 표기 `(구현 없음 — …)`·`**테스트 미구현**`·`(구현 예정)`·§9.1a 위치 열, 2차 = `audit_waivers.yaml` 정확 일치·와일드카드 금지·**stale waiver 자체가 경고**)이 오탐으로 도구가 죽는 것을 막는다(DQ-53 — 시제품 실측 허위 보고: FR 중복 15건·DQ 중복 6건·토큰 잡음 140건을 스코프·토큰 분류로 0으로 내렸다). 손으로 복제된 값(pytest 기준선 5곳·V-U/DQ/V-D '다음 번호' 포인터)은 **정본에서 계산해 대조**한다(DQ-54 — 한 세션에 4번 어긋난 실측). `doctor`는 **전수**로 돌고(실측 0.02s + chroma 0.32s — 샘플링은 놓침만 만든다) ChromaDB는 **`chroma.sqlite3` 읽기 전용 sqlite 직조회**다 — `KLIndexer._get_client()`가 `mkdir(parents=True)`를 하고 `PersistentClient`가 스키마를 쓰므로 평소 경로로 점검하면 **읽기 전용이 코드 수준에서 깨진다**(DQ-55). 스냅샷이 필요한 '분포 급변 감지'는 기각하고(로그에 timestamp 열이 없고 `members_only` 레코드의 `extracted_at`이 비어 있다 — 실측) **같은 데이터 안의 모순 감지**로 대체했다 — 로그의 `error:` 사유를 `video_access.is_members_message()`로 재판정해 **2026-09-09 멤버십 감지 고장의 화석 1건을 실제로 적발**했고, `tickers` 오탐은 '값이 있는데 distinct 1' 신호로 **필드 하드코딩 없이** 일반화했다(DQ-56). 자동 실행은 없다 — NFR3 예외는 FR37에 한정되고, 결과를 남기려면 리포트 저장소·알림이 필요해지는데 그것이 이번에 배제한 것들이다(FR38.16). 자막 교정(다음 단계)용 검사는 **레지스트리 자리만 열고 이름·스키마를 정하지 않는다**(DQ-57 — `note`·`tickers`가 '미리 만들었는데 죽어 있던' 전례다). 신규 결정 DQ-51~DQ-57, 검증 V-U35~V-U36·V-D22

> **v5.5 정합 정정 (2026-09-20, 문서 전용):** §9.1을 전면 재정렬해 V-U 번호 충돌·누락을 해소했다 — **정본은 `tests/test_unit.py`**(실행되는 것이 진실). 구 목록의 `V-U12 reflow_sentences`·`V-U13 live guard`는 테스트 파일이 쓰는 `V-U12 채널 폴더(FR25.1)`·`V-U13 챕터 정규화(FR27.1)`에 자리를 내주고 번호를 폐기(검증 자체는 §9.1b에 존치), 누락돼 있던 V-U14(Whisper SRT 조립)·V-U15(RSS 파싱)·V-U16(이름 변경)·V-U11b(재생목록 URL 분류)를 편입했다. 코드·FR 변경 없음

---

## 1. 시스템 아키텍처

```
사용자 (Mac 터미널)                      사용자 (브라우저)
      │                                       │
      ▼                                       ▼
┌──────────────────────────────┐   ┌─────────────────────────────┐
│  yt.sh  (래퍼 셸 스크립트)     │   │  대시보드 http://:8800       │
│  docker 빌드/실행·볼륨마운트   │   │  (yt.sh serve 로 기동)       │
│  cookies.txt(ro)·HF캐시 공유  │   └──────────────┬──────────────┘
└──────────────┬───────────────┘                  │
               │  docker run                      │  FastAPI
               ▼                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                    main.py (CLI 진입점)                           │
│  add · run · review · reextract · index · list · remove ·        │
│  ask · summarize · search · serve · test                         │
└──┬─────────┬─────────┬─────────┬─────────┬─────────┬────────────┘
   │         │         │         │         │         │
   ▼         ▼         ▼         ▼         ▼         ▼
ChannelRegistry Extractor QualityChecker KLIndexer KLQuery KLHarness(질의)
   │         │  │                  │         │         │
   │         │  └ MetaCollector    │         └────┬────┘
   │         │    StateManager     │              │
   │         │    cookie_health*   ▼              ▼
   ▼         │              ChromaDB+bge-m3   Claude API
channels.yaml│
             ▼
        output/
        ├── 채널A/ (srt txt desc meta chroma state.json playlists.json ...)
        ├── 채널B/ (완전 격리)
        └── .cookie_status.json   (쿠키 경고 상태 — 컨테이너 간 공유)

  dashboard/ = server.py(FastAPI) + jobs.py(JobManager) + index.html
  (cookie_health.py·jobs.py = FR17~19 구현 완료 컴포넌트)
```

---

## 2. 컴포넌트 상세

### 2.1 ChannelRegistry (`channel_registry.py`) — FR7

| 메서드 | 기능 |
|---|---|
| `extract_handle(url)` | `@핸들`/`/channel/UC…`에서 채널명 추출 (URL 디코드 포함, FR7.6) |
| `normalize_url(url)` | 디코드 + `/videos` 부착 정규화 |
| `add(url, lang, note)` | channels.yaml **upsert**. 이름은 `resolve_name(url)`로 해석(FR7.8)하고, **이미 등록된 채널이면 `url`·`lang`만 갱신하고 `group`·`auto_run`·`channel_id`·`added_at`은 보존**한다(FR7.7). 이름은 `config.validate_path_segment`로 검증(FR7.9). 채널명 반환 |
| `remove(name)` / `list()` / `get(name)` / `names()` | 등록 해제·조회 |
| `resolve_name(url)` | URL → **등록명 역조회** (`extract_handle` 값 일치, 대소문자 무시). 실패 시 `extract_handle` 폴백 (FR32.2·DQ-19) |
| `rename(old,new)` / `set_group(name,group)` / `set_channel_id(name,id)` | 이름 변경(FR31.1)·폴더 지정(FR25.1)·RSS channel_id 캐시(FR29.1) |
| `set_note(name, note) -> str` | **채널 메모 (FR36.1, 신규).** 제어문자(U+0000~U+001F·U+007F) → 공백 치환 → 트림 → 200자 초과면 `ValueError`(**잘라내지 않는다** — 조용한 절삭은 사용자 텍스트 소실). 빈 값이면 필드를 제거하지 않고 **`note: ""`로 되돌린다**: `add()`가 신규 등록 시 항상 `note: ""`를 쓰고 기존 77채널도 그 형태라, `set_group`식 "빈 값이면 pop"을 쓰면 yaml이 불균일해지고 77줄짜리 무의미한 diff를 부른다. 미등록 채널 `KeyError`. 정규화된 최종 값을 반환한다(API가 그대로 응답). **`add()`는 손대지 않는다** — FR7.7의 "note는 인자가 비어 있지 않을 때만 갱신"이 그대로 유효하며, 메모 쓰기 통로는 `set_note` 하나다 |
| `_save()` | yaml 저장 **직후 `config.invalidate_group_cache()` 호출** — 같은 프로세스의 `channel_dir()`가 즉시 새 그룹을 반영한다(FR35.3 2차 안전망. 1차는 mtime 자동 감지라 이 호출을 빠뜨려도 조용히 깨지지 않는다) |
| `set_auto_run(name, flag)` | `auto_run` 플래그 기록 (FR34.7). **`True`이면 필드를 제거**한다 — 기본값이 `True`이므로 참값을 쓰면 yaml에 의미 없는 잡음이 쌓인다(`set_group`의 빈 값 처리와 동일 패턴). 미등록 채널은 `KeyError` |
| `names(auto_only=False)` | 기본 동작(전체 반환)은 **바꾸지 않는다** — `names()`는 `cmd_*` 대상 산출 외에 `jobs.py`의 등록 여부 확인(`name not in reg.names()`)에도 쓰이므로, 기본값을 바꾸면 검색 유입 채널이 "미등록"으로 오판돼 매번 재등록·폴더 재지정된다. `auto_only=True`일 때만 `auto_run is False`인 채널을 제외하며, 이 인자를 쓰는 곳은 **`main.bulk_targets`(채널 인자 없음) 한 곳뿐**이고 `cmd_run`·`cmd_transcribe`가 그 헬퍼를 공유한다 (FR34.7·DQ-25) |

### 2.1b config.py — 채널 경로 해석 (FR35.1~35.5)

| 함수 | 설계 |
|---|---|
| `validate_path_segment(name) -> str` | **순수 문자열 연산**(파일시스템 접근 없음). NFC 정규화 → 트림 → FR35.4의 ⓐ~ⓖ 검사 → 통과하면 정규화된 값 반환, 위반이면 `ValueError(사유)`. 그룹명·채널명이 **같은 함수**를 쓴다 |
| `_group_map() -> dict` | `channels.yaml`을 **직접 얕게 파싱**해 `{채널명: group}`만 만든다. `channel_registry`를 import하지 않는다(순환 회피, DQ-32). 캐시 키는 `(st_mtime_ns, st_size)`이며 히트 시 I/O는 `stat()` 1회. yaml 부재·파싱 실패는 **빈 맵**(예외 없음) |
| `invalidate_group_cache()` | 캐시 강제 무효화. `ChannelRegistry._save()`가 호출 |
| `channel_dir(channel) -> Path` | **시그니처 무변경.** `validate_path_segment(channel)` 실패 → `ValueError`(폴백 불가, FR7.9가 선행 차단). 그룹은 `_group_map()`에서 읽고 `validate_path_segment` 실패 시 **무시하고 평면 경로로 폴백**(프로세스당 1회 경고). 반환: `OUTPUT_BASE/<group>/<channel>` 또는 `OUTPUT_BASE/<channel>` |
| `channel_subdirs(channel)` | **무변경** — `channel_dir()` 결과에 하위 이름만 붙인다 |

> **호출부 12곳은 전혀 바뀌지 않는다**(`state_manager:13` · `renamer:24·25·63` · `extractor:86·198` · `quality_checker:106` ·
> `server:391·416` · `jobs:256·314` · `config:122`). `mkdir(parents=True)` 8곳도 그대로다 — `channel_dir()`가 반환값의
> `OUTPUT_BASE` 하위성을 이미 보장하기 때문이다(FR35.5).

### 2.2 Extractor (`extractor.py`) — FR1·2·13~17

| 메서드 | 기능 |
|---|---|
| `_ydl_opts(**extra)` | 공통 yt-dlp 옵션 + **Firefox 프로필 우선(`cookiesfrombrowser`, FR13.6) → 쿠키 작업본 폴백**(FR13.1~2) + 쿠키경고 로거 주입(FR19.2). `logger`는 항상 `cookie_health.YDLLogger(log)`로 덮어써 감지 누락을 막는다. `self`를 쓰지 않아 `jobs._probe_opts()`가 언바운드로 재사용한다 |
| `_channel_base()` | 등록 URL에서 탭 접미사 제거한 채널 루트 |
| `_scan_tab(url)` | 단일 탭/재생목록 flat 스캔 |
| `scan_channel()` | **videos+streams 두 탭 병합**(id 기준), `is_live`/`is_upcoming` 제외, `content_type` 표시 (FR16.1~16.3). 탭 없으면 info 로그 후 계속 |
| `scan_playlists()` | `@채널/playlists` → 각 재생목록 flat 스캔 → `video_id→[재생목록 제목]` 매핑, `playlists.json` 저장 (FR15.1) |
| `_backfill_meta(mapping)` | 기존 meta/*.json에 playlists·content_type 백필, 빈 매핑이면 생략 (FR15.5) |
| `_pick_subtitle(info)` | 수동 우선 → 자동 폴백, lang → en (FR1.2) |
| `process_video(vid, action, content_type, playlists_map, info, date_range)` | 단일 영상: (`info` 미전달 시) full info 조회 → **진행/예약 라이브 가드**(`live_status`∈{is_live,is_upcoming} → `"live_wait"` 반환, state 미기록 — FR16.5) → **기간 판정(`_out_of_range`)** → 자막 선택 → VTT→SRT→TXT → meta/desc 저장 → state 기록. `was_live` 보정(FR16.4). `info`가 주어지면 재조회 생략(FR17.2 단일영상 워커가 선조회분 재사용). 반환 `"ok"` \| `"no_sub"` \| `"date_skip"` \| `"live_wait"` — 멤버십은 반환값이 아니라 **예외 경로**(`_is_members_only`→`_mark_skip`)로만 분류된다 — 그 판정은 스캔 엔트리의 `availability`가 1차 신호이므로 `run()` 루프가 수행한다(DQ-38) |
| `_out_of_range(upload_date, date_range)` | 기간 조건(`{"since","until"}`, YYYYMMDD, 경계 포함) 판정. 범위 밖이면 자막을 받지 않고 `extract_log.csv`에 `status="date_skip"` 1행만 기록하고 **state.json에는 기록하지 않는다**(조건을 바꾼 다음 실행에서 다시 대상이 되어야 하므로). `upload_date`가 없거나 `"00000000"`이면 판정 불가 → 통과 (FR2.6과 같은 보수 원칙) |
| `_is_members_only(msg, availability=None)` | 멤버십 전용 판별 — **1차 `availability`**(스캔 flat 엔트리/full info의 구조화 필드, 언어 비의존) **2차 오류 메시지**(영어·한국어 키워드). 규칙 본체는 `video_access`에 있고 대시보드 스캔(`jobs._is_members_availability`, FR17.6)과 **같은 상수·같은 함수**를 쓴다. 메시지 단독 판정은 로케일 의존이다 — DQ-20이 YouTube `reason`을 번역한다(FR13.7·DQ-38) |
| `_is_429(msg)` / `_is_no_tab(msg)` | 429·탭 부재 판별. 둘 다 **yt-dlp/urllib가 생성한 문구**(+숫자 `429`)라 로케일 영향을 받지 않음이 실측 확인됐다 — 번역되는 것은 **YouTube가 준 `reason`뿐**이다(DQ-38의 경계표) |
| `_fetch_vtt(url)` | 자막 직접 다운로드: 자체 딜레이 + 쿠키 + 브라우저 UA (FR13.4) |
| `_report(progress, phase, done, total, current_title, stats)` | 진행 콜백 호출 헬퍼(FR18.1~2). `progress=None`이면 즉시 `True` 반환(CLI 경로 무영향), 콜백 내부 예외는 삼키고 "계속"으로 간주. `stats`는 얕은 복사본으로 전달(폴링 스레드의 직렬화 레이스 방지) |
| `run(force_vid, limit, progress, entries, pl_map, date_range, rest_state)` | 채널 루프: 429 지수 백오프(FR14.3)·배치 휴식(FR14.2)·연속 429 중단(FR13.5)·카나리아 `--limit`(FR14.5)·멤버십 감지 스킵(**429를 먼저 판정한 뒤** 비-429 실패에만 멤버십 판정을 적용한다 — 멤버십 영상의 일시 429를 `members_only`로 오분류하면 `_mark_skip`으로 영구 스킵된다, DQ-38). **신규 4인자가 모두 None이면 기존 CLI 동작과 완전 동일**(FR18.1, 반환 dict에 `date_skip:0` 키만 추가). `entries`/`pl_map`이 주어지면 `scan_channel()`/`scan_playlists()`를 건너뛰고 대시보드 스캔 캐시를 그대로 사용(DQ-13). `date_range`는 해석 없이 `process_video`로 전달(DQ-12). `rest_state`(`BatchRest`)를 주면 배치 휴식 카운터를 **호출 경계를 넘어 공유**한다 — 그룹 추출은 채널마다 `run()`을 새로 부르므로 지역 카운터로는 휴식이 오지 않는다(FR14.2, 검색은 영상당 채널이 달라 치명적). None이면 이 호출 전용 상태를 새로 만든다(기존 CLI 동작). `progress`가 False를 반환하면 우아한 취소 — 루프 break → `finishing` 보고 → `state.save()` → (`pl_map` 있으면) `_backfill_meta()` → 최종 로그 → `stats["cancelled"]=True`로 반환 (FR18.2). 인덱싱은 이 함수 범위 밖(DQ-14). **v5.10 —** 연속 429 중단(FR13.5)으로 루프를 빠져나온 경우 반환 dict에 `stats["aborted_429"]=True`를 싣는다. `stats["cancelled"]`와 같은 계열의 **불리언 표식**이며 `_STAT_KEYS` 카운터가 아니므로 `_merge_stats`의 화이트리스트를 통과하지 않는다(job `stats` 등식 불변 — V-D11). 이 표식이 없으면 호출자는 "429로 끊겼다"와 "오류 1건 나고 끝났다"를 구별할 수 없고, 그래서 `_run_grouped`가 차단 상태로 다음 채널을 계속 두드린다 (FR37.9·DQ-48) |

#### progress 콜백 계약 (FR18.1~18.2 — extractor ↔ jobs 경계)

```python
progress(payload: dict) -> bool          # False 반환 = 취소 요청
payload = {"phase": str,                 # scanning | playlists | extracting | finishing
           "done": int, "total": int,    # done은 skip된 영상도 포함(진행률이 total에 도달)
           "current_title": str | None,  # 스캔 엔트리의 title
           "stats": dict}                # 스냅샷(얕은 복사) — new·updated·skip·no_sub·members_only·error·date_skip
```

호출 시점(이 순서 고정):

1. `scanning` — `entries=None`일 때만, `done=0,total=0`
2. `playlists` — `pl_map=None`일 때만, `done=0,total=len(entries)`
3. `extracting` — 영상 처리 **직전**(`current_title` = 해당 엔트리 title)
4. `extracting` — 영상 처리 **직후**(`done`+1, `stats` 갱신)
5. `finishing` — 루프 종료 직후·`state.save()` 직전 (반환값 무시)

`scanning`/`playlists` 단계에서 False를 받아도 취소로 처리한다(스캔은 수십 초 걸려 취소 신호를 무시하면 UX가 나쁘다).
대시보드 채널 워커는 `entries`·`pl_map`을 항상 캐시에서 넘기므로 실제 폴링에서는 1·2가 나타나지 않는다.
dict 단일 인자 규약이라 필드를 추가해도 시그니처가 깨지지 않는다.

### 2.3 StateManager (`state_manager.py`) — FR2·19.1

| 메서드 | 기능 |
|---|---|
| `decide(vid, mod_date, up_date)` | `new`/`updated`/`skip`. 스캔이 날짜 미제공 시 **무변경 간주**(FR2.6). **`sub_type=="members_only"`이고 쿠키 파일이 존재하면 `updated`를 반환해 매 run 재시도**(FR19.1·DQ-10). 판정에는 `config.COOKIE_FILE.exists()`만 쓴다 — `config.resolve_cookiefile()`은 호출할 때마다 원본을 `/tmp` 작업본으로 복사하는 부작용(FR13.2)이 있어 판정용으로 부적합하기 때문. 억제 플래그는 두지 않는다: `include_members=false` 제외는 `jobs.apply_filters`가 대상 목록 단계에서 책임진다(FR17.4ⓓ 우선) |
| `mark_done(vid, meta)` / `remove(vid)` / `save()` | 상태 기록·강제 재처리·영속화 |

### 2.4 MetaCollector (`meta_collector.py`) — FR3·12.2·15.2·16.4

| 항목 | 기능 |
|---|---|
| `extract_tickers(text)` | 종목코드·티커 추출 (FR12.2·12.5, DQ-43). `(?<!\d)\d{6}(?!\d)` 후보마다 앞뒤 24자 문맥을 보고 **근거가 있을 때만** 채택한다 — 강한 근거(`_LABEL_STRONG`·`_EX_PREFIX`·`_EX_SUFFIX`) / 약한 근거(`_LABEL_WEAK`·괄호 단독·직전 채택 코드와의 나열) / 공통 배제(`_URL_HINT` 토큰·`_numeric_neighbor`) / 약한 근거에만 `_is_date_like`(YYMMDD) 배제. 미국 티커 `$[A-Z]{1,5}`는 불변 |
| `tickers_from_meta(meta, description)` | meta(제목·태그) + 설명으로 `tickers` 재계산 — `save()`와 백필이 **같은 입력 구성**을 쓰도록 한 단일 통로 (FR12.2) |
| `backfill_tickers(channel, apply=False)` | 기존 `meta/*.json`의 `tickers` 재계산 (FR12.7). 설명은 `desc/*.txt`에서 읽으므로 **네트워크 없음**. `apply=False`(기본)면 차분만 반환하고 **쓰지 않는다**. 반환 `{scanned, changed, removed, added, samples}` |
| `save(info, basename, sub_type, playlists, content_type)` | meta/*.json(+`tickers`·`playlists`·`content_type`) + desc/*.txt 저장 |

### 2.5 QualityChecker (`quality_checker.py`) — FR4

1차 규칙 기반(단어수·반복·한국어비율·특수문자) → 2차 Claude API(SUSPECT만, 앞 500단어 샘플) → review_report.csv.

### 2.6 KLIndexer (`kl_indexer.py`) — FR6·15.3·21.1

| 메서드 | 기능 |
|---|---|
| `index_subtitles()` | srt/ 전체 → 120초 윈도우 청킹 → `subtitle_chunks` upsert. 청크 메타데이터에 `playlists`(쉼표 join 문자열)·`content_type` 포함 |
| `index_descriptions()` | desc/ → 300토큰 청킹 → `desc_chunks` upsert (동일 메타데이터) |
| `index_all()` | 채널 전체 인덱싱 (upsert라 재실행 시 메타데이터 갱신) |
| `delete_video(video_id)` | 두 컬렉션에서 `where={"video_id": video_id}`로 청크 삭제 (FR21.1). `get_or_create_collection`이 항상 컬렉션을 만들어두므로 인덱스가 비어 있어도 안전한 no-op |

### 2.7 KLQuery (`kl_query.py`) — FR9·12·15.4

| 메서드 | 기능 |
|---|---|
| `search(query, top_k, since, until, collection, category)` | 벡터 검색 + 날짜 where 필터. **category는 여유분(4×) 조회 후 클라이언트 측 부분일치 필터** (ChromaDB 문자열 contains 미지원) |
| `get_full(video_id|basename)` | txt/ 전문 로드 (RAG 미사용 경로) |
| `list_videos(since, until)` | meta/ 순회 → video_id·title·upload_date·basename·tickers·playlists·content_type·**sub_type**·**duration**·**duration_string**·url (날짜 역순). `sub_type`은 📝수동/🤖자동 뱃지용(FR20.2), `duration`(초 int)·`duration_string`(표기용 str)은 길이 표시용(FR20.5). **meta.json에 이미 저장된 값을 그대로 읽는다 — 백필 없음.** 키가 없는 과거 meta는 `duration=None`·`duration_string=""`로 내린다(0으로 채우지 않음, DQ-23b) |
| `ask(query, ...)` | search → 컨텍스트 주입 → LLM 답변 + 출처 (FR9.1·9.5) |
| `summarize(video_id)` | 전문 로드 → LLM 구조 요약 (FR9.2) |

### 2.8 KLHarness (`kl_harness.py`) — FR10 **질의 하네스 (제품 내)**

> 개발 하네스(§11)와 별개. 대시보드 `/ask`와 CLI `ask --multistep`이 사용하는 LLM tool_use 루프.

| 항목 | 내용 |
|---|---|
| 도구 4종 | `search` · `get_full` · `summarize` · `list_videos` (FR10.2) |
| 루프 | max `HARNESS_MAX_STEPS`(10)회 tool_use 반복, 각 호출 trace 기록 (FR10.1·10.5) |
| 반환 | `{answer, steps, trace}` |

### 2.9 dashboard/server.py — FR11·17~22

| 엔드포인트 | 상태 | 기능 |
|---|---|---|
| `GET /` `GET /channels` `GET /videos` | 구현됨 | 페이지·채널 목록·영상 목록(playlists·content_type 포함) |
| `POST /search` | 구현됨 | 벡터 검색 (category 필터 포함) |
| `POST /ask` `POST /summary` | 구현됨 | 질의 하네스 호출·영상 요약 |
| `POST /extract/scan` | 구현됨 | 채널 사전 스캔 → scan_id 캐시 (FR17.3). 스캔은 요청 스레드에서 동기 수행하며 job dict를 만들지 않는다 |
| `POST /extract` | 구현됨 | 추출 시작 (단일영상 `{url}` or `{scan_id, filters}`), 409 동시제한 (FR17.4·17.7). 202 + `{job}` |
| `GET /extract/status` | 구현됨 | 진행 폴링 (FR18.3) — 마지막 job 유지, `idle`은 기동 후 무작업일 때만 |
| `POST /extract/cancel` | 구현됨 | 우아한 취소 (FR17.8) |
| `GET /cookies` | 구현됨 | 쿠키 존재·mtime·경고 상태 (FR19.3). `cookie_health`를 엔드포인트 내부에서 지연 임포트해 모듈 부재가 서버 기동을 막지 않게 한다 |
| `GET /channels/stats` | 구현됨 | 채널 목록(registry 기준) + state.json 집계 통계 (FR20.1). 요청마다 state.json을 새로 읽어 최신값 보장. [추출] 탭 채널 현황 카드(FR22.1)도 이 엔드포인트를 그대로 재사용 — FR22 전용 백엔드는 없다 |
| `GET /subtitle` | 구현됨 | 자막 전문 txt (FR20.3). `channel`·`basename` 양쪽에 경로 문자 검사 후 `resolve()` 결과가 채널 txt 디렉터리 하위인지 재확인(2중 방어) |
| `POST /videos/delete` | 구현됨 | 영상 삭제 (FR21.1) — `_reject_path_traversal`(FR20.3과 동일 검사)로 `channel`·`basename` 검증 → meta.json에서 `video_id` 조회(404 없으면) → srt·txt·meta·desc 4파일 `unlink(missing_ok=True)` → `StateManager.remove`+`save()` → `playlists.json`에서 해당 `video_id` 항목 제거(있으면) → `KLIndexer.delete_video`로 ChromaDB 양쪽 컬렉션 정리. `MANAGER.is_busy()`면 409 |
| `POST /channels/group` · `POST /folders/rename` | 구현됨 (v5.6 확장) | 폴더 지정/해제·폴더 이름 변경 (FR25.2·FR31.4). **v5.6부터 yaml 기록에 더해 `folder_ops`가 충돌 검사(409)·디렉터리 이동·보상 롤백을 수행**하고 그룹명 검증 실패는 400이다 (FR35.4·35.6·35.8~35.9) |
| `POST /channels/note` | **신규 (v5.8)** | 채널 메모 저장 (FR36.4) — `_reject_if_busy()`(FR36.11) → `_reject_path_traversal(channel)` → `ChannelRegistry.set_note`. `KeyError`→404 · `ValueError`(200자 초과)→400 · 정규화된 `note`를 응답에 실어 프론트가 **서버 값을 그대로** 렌더한다(클라이언트에서 다시 트림·치환하지 않는다 = 표시 불일치 차단) |
| `GET /channels/stats` (v5.8 확장) | 확장 | 응답 항목에 `note`(`ch.get("note", "")`) 추가 (FR36.3). **라이브러리·추출 두 탭이 이미 이 엔드포인트 하나만 읽으므로 조회용 신규 API는 만들지 않는다** |
| `POST /channels/rename` (v5.8 확장) | 확장 | 기존 동작(FR31.1) 뒤에 **`MANAGER.invalidate_scans(channel=옛이름)`** 한 줄을 더한다 (FR36.8·DQ-41). 실패해도 이름 변경은 이미 성공했으므로 예외를 삼키지 않고 그대로 두되, 호출 순서는 **rename 성공 후**다(rename이 409/400이면 캐시는 건드리지 않는다) |
| `POST /channels/delete` | 구현됨 | 채널 삭제 (FR21.2) — `ChannelRegistry.remove`로 등록 해제(없으면 404). `purge=true`면 `channel_dir().resolve()`가 `OUTPUT_BASE` 하위인지 재확인 후 `shutil.rmtree` — 등록 해제만으로는 `output/` 폴더가 disk에 남지만 `/channels/stats`가 registry 기준이라 라이브러리 UI에서는 즉시 사라진다. `MANAGER.is_busy()`면 409  **v5.8 —** 삭제 성공 후 `MANAGER.invalidate_scans(channel=)`를 부른다 (FR36.8): 남겨 두면 옛 `scan_id`로 온 추출이 `_run_channel`의 `reg.add()`로 채널을 **되살린다** |

| `GET /schedule` · `POST /schedule` · `POST /schedule/run-now` | **신규 (v5.10)** | 주기 자동 추출 설정·상태 (FR37.14). 전부 `scheduler` 모듈에 위임하는 얇은 어댑터다 — `GET`은 상태 파일 + 파생값(`next_due_at`·`running`), `POST`는 부분 갱신(검증 위반 `ValueError`→400), `run-now`는 `scheduler.request_now()` 호출 후 202. **`_reject_if_busy()`를 부르지 않는다** — 쓰는 대상이 전용 상태 파일 하나뿐이라 `channels.yaml` lost update(FR36.11·DQ-42)나 디렉터리 이동 위험이 없다. 기동 배선은 `@app.on_event("startup")`에서 `scheduler.start(MANAGER)` 한 줄이며 `SCHEDULER_DISABLED=1`이면 건너뛴다(CLI·테스트) |

> 요청 모델(Pydantic): `ScanRequest{url}` · `Filters{latest, since, until, categories, include_members, keyword}` · `ExtractRequest{url, scan_id, filters, index=True}` ·
> `VideoDeleteRequest{channel, basename}` · `ChannelDeleteRequest{channel, purge=False}` · **`ChannelNoteRequest{channel, note}`(v5.8)** · **`ScheduleRequest{enabled=None, interval_days=None, max_videos_per_cycle=None}`(v5.10 — 셋 다 Optional, 준 것만 갱신)**.
> `JobBusyError`→409 `{detail, job}`, `ValueError`→400 `{detail}`로 매핑한다.

### 2.9b dashboard/index.html — 채널 카드 공통 갱신 (FR36.5~36.10)

| 항목 | 설계 |
|---|---|
| `chanNoteHtml(c)` | 메모 한 줄 조각. **빈 메모면 빈 문자열을 반환**해 줄 자체가 생기지 않는다(FR36.5 — 현행 77채널이 전부 빈 값이라 배포 직후 화면 변화 0). 전문은 `title` 속성, 표시는 1행 `text-overflow: ellipsis`. `chanCardHtml`(라이브러리)과 `extCardHtml`(추출)이 **같은 조각을 호출**한다 — 두 카드 렌더가 따로 있는 현 구조에서 표시 규칙만은 한 곳에 둔다 |
| 버튼 배치 | 라이브러리 카드는 ✏️·📁·🗑에 **📝가 더해져 4개**가 되므로 `.chan-name`의 `padding-right`(현 64px)와 `.chan-*` 절대 위치(`right: 8/28/48px`)를 한 칸씩 넓힌다. 추출 카드는 ✏️·📝 **2개**만 둔다(폴더·삭제는 라이브러리 전용 유지 — 추출 탭에서 파괴적 조작을 노출하지 않는다). 추출 카드의 두 버튼도 **라이브러리와 같은 `right: 48/68px`를 쓴다** — ⓐ `.rss-badge`가 이미 `right: 8px`를 점유하고 ⓑ 두 탭에서 같은 아이콘이 같은 자리에 있어야 오조작이 준다 |
| 이벤트 위임 순서 | `extChanCards`의 `.chan-rename`·`.chan-note` 위임은 **`.chan-card` 위임보다 먼저 등록**하고 `ev.stopImmediatePropagation()`을 부른다. 카드 클릭이 곧 스캔 시작(FR22.2)이라 순서를 어기면 **✏️를 누르는 순간 스캔이 뜬다.** 기존 `.norun`(FR34.8)이 같은 이유로 이미 이 패턴을 쓴다 |
| `refreshChannelViews({names, rename})` | **이름·메모 변경 후 유일한 갱신 통로 (FR36.9).** `rename`은 `{from, to}`로 ⓒ의 `loadChannels`에 그대로 전달된다. ⓐ `libLoaded = false` + 라이브러리 탭이 활성일 때만 `loadLibrary()` — 비활성 탭은 `switchTab`의 기존 `!libLoaded` 가드가 다음 진입에 로드한다(**가드를 제거하지 않는다**: 탭 전환마다 전체 재조회로 돌아가면 FR25.4 접기 상태·선택이 매번 흔들린다). ⓑ `loadExtChannels()`는 항상 호출(비용 `/channels/stats` 1회). ⓒ `names: true`일 때만 `loadChannels()`. 호출부: `renameChannel`(라이브러리·추출 공용, `names: true`)·`setChannelNote`(`names: false`) |
| `loadChannels()` 선택 보존 | 재조회 전 `channelSel.value`를 기억했다가 재구성 후 복원한다. 이름이 바뀐 채널이면 **새 이름**으로 복원하고(호출부가 `{from, to}`를 넘긴다), 목록에 없으면 첫 채널로 폴백한다. **선택이 실제로 달라졌을 때만 `loadVideos()`** 를 부른다 (FR36.10). 현행 구현은 무조건 `channels[0]` + `loadVideos()`라 갱신이 잦아지면 질의 중 선택이 튕긴다 |
| `setChannelNote(name)` | `prompt(현재 메모)` → 취소(`null`)면 아무 것도 하지 않는다(**빈 문자열 제출 = 메모 삭제**와 구분). `_renamePost("/channels/note", …)`로 전송(기존 공통 POST 헬퍼 재사용 — 409/400 detail을 그대로 alert) → 성공 시 `refreshChannelViews({names:false})` |
| 이름 변경 후 스캔 화면 | `renameChannel` 성공 후 `scanData && scanData.channel === 옛이름`이면 조건 화면을 닫고(`scanData = null`) "채널 이름이 바뀌어 스캔 결과를 버렸습니다 — 다시 스캔하세요" 안내를 띄운다. 서버측 캐시 무효화(FR36.8)와 **같은 사실을 화면에도 반영**하는 것이며, 이렇게 하지 않으면 사용자가 400을 만난 뒤에야 알게 된다 |

### 2.10 dashboard/jobs.py — FR17~18

| 항목 | 설계 |
|---|---|
| `classify_url(url)` | 영상(watch?v=·youtu.be·/shorts/·/live/에서 11자 ID) → 재생목록(`/playlist?list=`) → **검색(`/results?search_query=`)** → 채널(@핸들·/channel/UC), 판별 불가 시 `ValueError`→400 (FR17.1·24.1·34.1). 퍼센트 인코딩 핸들도 디코드 후 판별. **순수 텍스트는 검색으로 승격하지 않는다** — 검색 진입은 요청 본문의 `q` 필드 전용이다 (DQ-27) |
| `JobManager` | 모듈 싱글턴(단일 uvicorn 프로세스 전제). `threading.Thread(daemon=True)` 1개, `threading.Event` 취소, `RLock` 하 job dict 갱신. **점유 플래그(`_busy`) 1개를 추출·스캔이 공유** — 추출 중 `POST /extract/scan`도, 스캔 중 `POST /extract`도 409다(스캔은 1+N회 요청이라 결코 가볍지 않고, 동시 호출은 429 위험을 키운다). `start()`는 **요청 검증(400) → 점유 획득(409)** 순서라 잘못된 요청이 점유를 남기지 않는다 |
| 스캔 캐시 | `scan_id → {channel, url, videos_view, entries, pl_map, created_at}` TTL 10분 (FR17.3·DQ-13). API 응답용 `videos_view`뿐 아니라 **원본 flat `entries`와 `pl_map`을 함께 보관**하는 것이 핵심이다 — 추출 시 `Extractor.run(entries=, pl_map=)`으로 그대로 넘겨 재스캔(1+N회 요청 중복)을 없앤다. 만료·부재 시 `ValueError`→400 |
| `apply_filters(videos, f)` | ⓒ카테고리(OR·재생목록 제목 완전일치, 카테고리 선택 시 재생목록 없는 영상 제외) → ⓓ멤버십(`include_members=false`면 제외) → ⓔ키워드(소문자 부분일치) 를 AND로 적용한 뒤 **마지막에 `out[:latest]`**. ⓑ기간은 여기서 적용하지 않는다(DQ-12). 프론트 `applyFilters()`와 동일 순서가 계약이다 (DQ-15) |
| 멤버십 판정 | 스캔 엔트리 `availability`(`subscriber_only`·`needs_auth`·`premium_only` 부분일치) **OR** `state.sub_type=="members_only"` 합집합 (FR17.6). 판정 함수·상수는 `video_access`에 있고 **추출 경로(`Extractor._is_members_only`)와 같은 것을 쓴다** — 따로 두면 드리프트가 생긴다(DQ-38). `include_members=false`면 여기서 제외되므로 `decide()`의 FR19.1 재시도에 도달하지 않는다 |
| 채널 워커 | `apply_filters` 결과 id 집합으로 원본 `entries`를 **같은 순서로** 재구성 → 미등록 채널이면 `registry.add(원본 URL)`(기존 항목이면 호출하지 않아 `added_at`·`note` 보존) → `Extractor.run(entries=, pl_map=, date_range=, progress=cb)`. cb는 락 하에 job을 갱신하고 `not cancel.is_set()`을 반환한다 |
| 단일영상 워커 | full info 1회 조회 → **채널 등록 URL 조립**: `uploader_id`가 `@`로 시작하면 `https://www.youtube.com/{uploader_id}`를 만들고, 아니면 `channel_url`→`uploader_url` 폴백. `uploader_id`(`@handle`)를 그대로 `registry.add()`에 넘기면 `normalize_url()`이 `@handle/videos`라는 깨진 URL을 저장해 이후 모든 `run`이 실패한다 → 조립 필수 (FR17.2). 이후 `process_video(info=선조회분)`. 재생목록 매핑은 생략하고 다음 전체 run의 백필(FR15.5)로 채운다 |
| 후처리 | `index` 옵션이 켜져 있고 **취소가 아니며** 신규+수정 > 0일 때만 같은 스레드에서 `KLIndexer.index_all()` (FR17.9·DQ-14) |
| 지연 임포트 가드 (F-6) | `extractor` 모듈은 `_app_extractor()` 헬퍼로만 로드한다. yt-dlp 실행이 legacy 플러그인 탐색으로 site-packages의 `ytdlp_plugins` 경로를 등록하면 이후의 맨 `import extractor`가 그 서브패키지로 **섀도잉**된다 — 실증: 첫 스캔은 200, 같은 프로세스의 두 번째 스캔이 ImportError (2026-08-04 발견). 헬퍼는 sys.modules 캐시에 올바른 모듈(`Extractor` 속성 보유)이 있으면 재사용하고, 오염 시 앱 루트를 sys.path 최우선으로 되돌려 재임포트한다. mock 테스트의 가짜 `extractor` 주입과도 호환 |
| `is_busy()` (FR21.4) | `_busy` 플래그를 락 하에 읽어 반환하는 공개 헬퍼. `server.py`의 삭제 엔드포인트가 진행 중인 추출·스캔과 파일 정리가 겹치지 않도록 이 값으로 409를 판단한다(사설 속성 직접 접근 대신) |
| `is_busy()` v5.6 확장 (FR35.10) | 반환값에 `folder_ops.is_locked()`를 **OR로 합산**한다 — CLI 마이그레이션 컨테이너와 serve 컨테이너는 job 상태를 공유할 수 없으므로 `output/.migration.lock` 파일을 통해서만 서로를 인지한다. 이 덕분에 마이그레이션 중 `/extract`·삭제·이름 변경·폴더 이동이 모두 409로 막힌다 |
| `invalidate_scans(channel=None)` (FR36.8, 신규) | 락 하에 `self._scans`를 훑어 **해당 채널을 참조하는 항목을 전부 삭제**한다 — 채널 스캔은 `entry["channel"]`, 재생목록·검색 스캔은 `entry["by_channel"]`의 키가 판정 대상이다(대소문자·NFC는 `resolve_name`과 달리 **정확 일치**로 충분하다: 캐시에 들어간 이름은 이미 레지스트리 표기 그대로다). `channel=None`이면 전체 비운다. 삭제된 `scan_id`로 오는 `POST /extract`는 **기존 400 "scan_id가 만료되었습니다"** 경로를 그대로 탄다(§5.9 판정 규칙 5 — 신규 오류 코드 없음). 호출부는 `server.py`의 **`/channels/rename`·`/channels/delete` 성공 직후** 두 곳이다 (삭제도 같은 구멍이다 — 옛 `scan_id`로 추출하면 `_run_channel`의 `reg.add()`가 채널을 **되살린다**) |
| `start_schedule(plan)` (FR37.7, 신규) | 스케줄러 전용 진입점. `plan`(`{by_channel:{채널:{url, entries}}, videos_view}`)을 받아 `_new_job("schedule_run", "자동 추출", "")` → `_acquire()` → `_run_schedule` 스레드 시작. **`start()`와 같은 점유·취소 규약**을 쓰므로 사용자 작업과 상호 409/취소가 그대로 성립한다. 점유 실패(`JobBusyError`)는 예외가 아니라 **스케줄러가 삼켜 "이번 틱 무동작"으로 처리**한다(FR37.12 — 사용자에게 보일 오류가 아니다) |
| `_run_schedule(job, entry)` (FR37.7, 신규) | `_run_grouped(job, entry, filters={"include_members": True}, index=True, group_title=None, merge_categories=False, auto_run=True)` 한 줄. 인자 조합의 의미: `group_title=None` → 폴더 자동 지정 없음(대상이 전부 기등록 채널) · `merge_categories=False` + `pl_title=None` → **`pl_map={}`** 이라 재생목록 스캔·백필 0회(FR34와 같은 이유, DQ-28) · `include_members=True` → RSS 엔트리에 `availability`가 없어 `apply_filters`의 기본 멤버십 제외가 **조용히 대상을 지우는 것**을 막는다(판정은 추출 시 FR13.7이 한다) |
| `_run_grouped` 429 중단 (FR37.9) | 그룹 루프에서 `stats.get("aborted_429")`를 `cancelled`와 **같은 위치에서** 검사해 루프를 빠져나온다. 단 `status`는 `cancelled`가 아니라 **`done`** 이고(사용자가 취소한 게 아니다), `_append_warning`에 "429 연속 차단 — 남은 채널 N개를 건너뜀"을 남긴다. **이 변경은 스케줄 경로 전용이 아니다** — 재생목록(FR24)·검색(FR34) 추출도 지금까지 차단 상태로 다음 채널을 계속 두드리고 있었다(잠재 결함 동반 수정) |
| 자동 폴더 지정 충돌 (FR35.13) | `_run_grouped`가 신규 채널에 폴더를 지정할 때 `folder_ops.set_channel_group(..., on_conflict="skip")`을 쓴다 — 충돌 시 예외 대신 건너뛰고 로그·job 경고만 남겨 **배치 추출 전체가 죽지 않게** 한다 |

**검색 추출 (FR34) — 재생목록 경로의 소스 치환**

| 항목 | 설계 |
|---|---|
| `SP_PRESETS` | `period` → `sp` 문자열 상수 맵. `type=video`를 반드시 포함해야 재생목록·채널 엔트리 혼입이 사라져 `uploader_id`·`duration` 커버리지가 100%가 된다. **6종 전부 실측 확정(2026-09-20, `_workspace/17b`):** `all → "EgIQAQ"`(동영상 필터만) · `hour → "EgQIARAB"` · `today → "EgQIAhAB"` · `week → "EgQIAxAB"` · `month → "EgQIBBAB"` · `year → "EgQIBRAB"`. 알 수 없는 값은 `all`로 폴백한다 (FR34.4·DQ-24) |
| `_build_search_url(q, period)` | `https://www.youtube.com/results?search_query={quote_plus(q)}&sp={SP_PRESETS[period]}` — yt-dlp `youtube:search_url` 추출기가 받는다. `ytsearchN:` 구문도 동작하지만 **`sp` 필터를 실을 수 없어** 쓰지 않는다 (FR34.1·34.4) |
| `_search_opts(limit)` | `{**_flat_opts(), "playlist_items": f"1-{limit}"}` — ⓐ개수 상한을 스캔 단계에서 절단한다. `_flat_opts()`를 거치므로 `extractor_args.youtube.lang`(DQ-20)이 검색 경로에도 그대로 적용된다 (FR34.2) |
| `scan_search(q, limit, min_duration, period, folder)` | 검색 스캔 공개 진입점. `normalize_search_params()`로 조건을 정규화·검증(위반 → `ValueError`→400, 점유 획득 **전**)한 뒤 점유를 잡고 `_do_scan_search`를 돈다. `/results?search_query=` URL을 `scan(url)`로 넣으면 `search_query_from_url()`로 검색어만 복원해 **기본 조건**으로 이 경로를 탄다 — URL에 박힌 `sp`는 쓰지 않는다(조건의 단일 출처는 대시보드 UI여야 미리보기와 실제가 어긋나지 않는다) |
| `_do_scan_search(q, limit, min_duration, period, folder)` | `_do_scan_playlist`와 **같은 골격**(엔트리 그룹핑은 공통 헬퍼 `_group_flat_entries(entries, min_duration=None)`로 추출해 두 경로가 **같은 코드**를 쓴다): flat 스캔 → 엔트리별 `_entry_channel(e, reg)`로 원채널 해석(FR32.3) → `by_channel` 그룹핑 → 채널별 `StateManager`로 `extracted` 판정 → `videos_view` 조립 → 캐시. 차이는 세 가지다. ① **ⓑduration 필터**: `e["duration"]`이 있고 `min_duration` 미만이면 제외, **`duration` 결측은 포함**(DQ-23). ② `live_status` 선제외 분기가 **동작하지 않는다**(검색 flat에 키 자체가 없음 — 실측 0/15) → 진행 중 라이브는 처리 시 FR16.5 가드가 잡는다(DQ-26). ③ 캐시 항목이 `kind:"search"`·`query`·`folder`를 갖고 `playlist_title`은 없다. `videos_view` 항목에는 **`duration`(초, int \| null)을 실어 보낸다** — 쇼츠 라벨이 없어 사용자가 길이를 직접 보고 판단해야 하기 때문이다(FR34.5·DQ-23). flat 엔트리에는 `duration_string`이 없으므로 표기는 프론트 공유 포맷터가 초에서 만든다(FR20.6) |
| 스캔 캐시(검색) | `{scan_id, kind:"search", query, folder, channel(=표시용 검색어), url(조립된 results URL), videos_view, by_channel, created_at}` — TTL·점유 규칙은 기존과 동일(DQ-13) |
| `_run_search(job, entry, filters, index)` | `_run_playlist`와 **동일 로직**이므로 공통 워커로 추출하고 두 인자만 달리 넘긴다: `group_title = entry["folder"]`(신규 채널 폴더 지정용, FR34.6), `merge_categories=False` → **`Extractor.run(pl_map={})`**(검색어는 카테고리로 병합하지 않음, FR34.10·DQ-28). **`None`이 아니라 빈 dict를 넘긴다** — `run(pl_map=None)`은 `scan_playlists()`를 호출해 채널마다 추가 요청이 나가고 `_backfill_meta`까지 돌아 DQ-28이 말하는 "병합 없음"이 성립하지 않는다. 빈 dict는 스캔도 백필도 건너뛴다. 신규 등록 시 `reg.add(...)` → `reg.set_group(name, group_title)` → **`reg.set_auto_run(name, False)`**(FR34.7). 기존 등록 채널은 `group`·`auto_run` 모두 건드리지 않는다 |
| 공통 워커 `_run_grouped(job, entry, filters, index, group_title, merge_categories, auto_run)` | `_run_playlist`(재생목록: `group_title=playlist_title`·`merge_categories=True`·`auto_run=True`)와 `_run_search`(검색: `group_title=folder`·`merge_categories=False`·`auto_run=False`)가 **같은 본문**을 쓴다. 재생목록 경로의 기존 동작은 인자 기본값으로 완전히 보존된다 |
| 빈 `pl_map`의 파급 | `Extractor.run(pl_map={})`이면 `if pl_map:`이 거짓이라 `_backfill_meta()`가 호출되지 않는다 — DQ-17이 경고한 "부분 맵으로 기존 카테고리 전멸" 사고가 **구조적으로 불가능**해진다. 검색 대상 영상의 `playlists`는 다음 전체 run의 백필(FR15.5)로 채워진다 (FR17.2 단일영상 선례와 동일) |
| `_group_flat_entries`의 부수 효과 | 재생목록 스캔(`_do_scan_playlist`)의 `videos_view`에도 `duration`이 함께 실린다 — 공통화의 결과이며 순수 추가 필드다. 프론트는 같은 `fmtDuration`으로 재생목록 미리보기에도 길이를 표시한다(비용 0: flat 엔트리가 이미 갖고 있는 값) |
| `apply_filters` | 변경 없음. ⓑ는 스캔 단계에서 이미 적용됐고 ⓒ카테고리 칩은 비어 있다. ⓐ최신N의 "최신"은 **검색 결과 순서**(YouTube 관련도 정렬)를 뜻한다 — 날짜순이 아니다(FR34.11·FR17.4ⓐ 비고와 같은 제약) |

### 2.11 cookie_health.py — FR19

| 항목 | 설계 |
|---|---|
| `YDLLogger` | yt-dlp `logger` 옵션 어댑터 — `warning()`/`error()`에서 "cookies no longer valid"·"cookies are invalid" 패턴 감지 시 `mark_invalid()` (yt-dlp 경고는 stderr 직행이라 logging 핸들러로는 캡처 불가). `debug()`/`info()`는 base 로거의 debug로만 흘려 `quiet: True` 정책을 유지한다 |
| `STATUS_FILE` | `output/.cookie_status.json` — CLI·serve 컨테이너가 공유하는 유일한 쓰기 마운트 (DQ-11) |
| `mark_invalid(message)` | 상태 파일 기록. **프로세스 내 중복 기록 방지** — 같은 경고가 요청마다 반복 발생하므로(1회 run에서 수십 회) 첫 감지에만 파일을 쓰고 이후 호출은 무시한다. 이미 `invalid` 상태인 파일이 있으면 **최초 `detected_at`을 보존**한다: `detected_at`이 "마지막 감지"로 갱신되면 쿠키 갱신 자동 해제(FR19.3) 판정이 흐려진다. 모든 예외를 삼켜 상태 기록 실패가 추출을 죽이지 않는다 |
| `clear()` | 상태 파일 제거 (수동 해제용) |
| `get_status()` | `{present, mtime, warning, warning_message, detected_at}`. `warning = invalid AND (쿠키 없음 OR detected_at ≥ 쿠키 mtime)` — 쿠키를 경고 이후 갱신했으면 자동 해제 (FR19.3). `warning=false`면 `warning_message`·`detected_at`은 `None`으로 마스킹해 해제된 옛 문구가 UI에 남지 않게 한다. 상태 파일이 없거나 깨졌으면 `warning=false` 폴백 |

> `mtime`·`detected_at`은 컨테이너 로컬 타임존으로 렌더된다. 자동 해제 판정은 두 값이 같은 기준이라 타임존과 무관하다.
> **F-4 해소 (2026-08-08)**: Dockerfile에 `ENV TZ=Asia/Seoul`을 주입해 CLI·serve 모두 KST로 렌더한다 (base 이미지에 zoneinfo 포함 확인).
> 전환 직전에 기록된 `.cookie_status.json`의 `detected_at`(UTC 시각)은 새 기록이 덮을 때까지 9시간 이르게 보일 수 있으나,
> 자동 해제 비교는 주 단위 마진에서 동작하므로 실무 영향 없음.

### 2.11b 이미지 버전 가시성 (F-8)

| 항목 | 설계 |
|---|---|
| `.build_time` | Dockerfile이 `COPY . .` 직후 `date -u`로 빌드 시각을 파일에 기록 |
| 기동 로그 | `dashboard/server.py` 모듈 로드 시 `.build_time`을 읽어 `docker logs`에 출력 — 웹 대시보드용 장수 컨테이너가 코드 재빌드 후에도 재기동되지 않아 구코드를 계속 서빙하는 사고(F-8, F-7 재발 원인)를 로그 한 줄로 즉시 드러낸다 |
| `GET /version` | `{build_time}` 반환 — 셸 접근 없이도 브라우저·curl로 확인 가능 |

### 2.11c folder_ops.py (신규, v5.6) — FR35.6~35.13

폴더(그룹) 디렉터리의 **충돌 검사·이동·마이그레이션** 전담. `config`·`channel_registry`에만 의존한다(순환 없음).
`ChannelRegistry.set_group`은 yaml 전용 순수 함수로 남겨 단위 검증(V-U12)을 보존하고, 부작용은 전부 여기로 모은다.

| 함수 | 설계 |
|---|---|
| `is_channel_like_dir(path) -> bool` | `path/state.json` · `path/srt` · `path/meta` 중 하나라도 있으면 채널 폴더로 간주 (FR35.6ⓐ — 등록 해제 후 남은 잔존 폴더 식별) |
| `check_namespace(new_group=None, new_channel=None) -> None` | FR35.6 ⓐ~ⓓ 전수 판정. 위반 시 `ConflictError`(→ HTTP 409). 판정 재료는 레지스트리(`group` 집합·미지정 채널명 집합) + `output/` 최상위 디렉터리 실재 여부 |
| `move_channel_dir(src, dst)` | **`os.rename` 단일 호출.** `dst.exists()` → 거부 · `st_dev` 불일치(EXDEV) → 거부(복사 폴백 금지) · `dst.parent.mkdir(parents=True)` 선행 · 성공 후 빈 `src.parent`만 `rmdir` (FR35.7) |
| `set_channel_group(name, group, on_conflict="reject")` | 검증(FR35.4) → `check_namespace` → `move_channel_dir` → `ChannelRegistry.set_group` 순서. yaml 기록 실패 시 **역방향 `os.rename` 보상 롤백**, 롤백 실패 시 복구용 `mv` 경로를 예외 메시지에 담는다. `on_conflict="skip"`이면 충돌 시 예외 대신 `{"skipped": 사유}` 반환 (FR35.13 — 자동 폴더 지정 경로 전용) |
| `rename_group(old, new) -> int` | `output/<old>/` → `output/<new>/` **단일 rename** 후 `group==old`인 채널 전부 yaml 갱신. 실패 시 보상 롤백 (FR35.9) |
| `plan_migration() -> list` | `[{channel, src, dst, reason}]` + 제외 목록(미등록 잔존 폴더·이미 이동됨·출력 폴더 없음). **읽기 전용** (FR35.11 dry-run) |
| `apply_migration(plan)` | 사전 검증 6종 전부 통과 시에만 시작 → 채널마다 `move_channel_dir` → 저널 append+fsync. 실패 시 저널 역순 자동 롤백 (FR35.11~35.12) |
| `rollback_migration()` | 저널 역순 되돌리기 → `.done.json` 보관 또는 저널 삭제 |
| `lock()` / `unlock()` / `is_locked()` | `output/.migration.lock`(pid·started_at). mtime 6시간 초과 = stale(무시 + 경고). `JobManager.is_busy()`가 `is_locked()`를 OR로 합산 (FR35.10) |

> **`renamer.py`와의 분담:** `renamer`는 "이름"(채널명·영상 제목·카테고리)을 다루고, `folder_ops`는 "폴더 위치"를 다룬다.
> `renamer.rename_folder`(FR31.4)는 `folder_ops.rename_group`에 위임하고, `renamer.rename_channel`(FR31.1)은
> 목적지를 **`old_dir.parent / new`** 로 계산한다 — `config.channel_dir(new)`는 새 이름이 아직 yaml에 없어 평면 경로를 돌려준다(FR35.9).

### 2.12 yt.sh — FR8

이미지 자동 빌드, channels.yaml 파일 보장, cookies.txt 존재 시 ro 마운트, serve 시 8800 포트, HF 캐시 공유, ANTHROPIC_API_KEY 전달.

### 2.13 scheduler.py (신규, v5.10) — FR37

주기 자동 추출의 **판정·계획·상태**를 전담한다. 의존은 `config`·`channel_registry`·`rss_monitor`·`cookie_health`뿐이고,
`dashboard/jobs`는 **주입받는다**(`start(manager)`) — 잎 모듈로 두어야 `jobs → scheduler → jobs` 순환이 생기지 않고
판정 로직을 네트워크 없이 단위 검증할 수 있다(V-U33).

| 함수·클래스 | 설계 |
|---|---|
| `load_state()` / `save_state(st)` / `save_scheduler_state(st)` | `output/.scheduler.json` 읽기·쓰기. 쓰기는 **`.tmp` 기록 → `os.replace`** 원자 교체. **필드 소유가 코드 상수다** — `USER_FIELDS`(`enabled`·`interval_days`·`max_videos_per_cycle`)는 `update()`만, `SCHEDULER_FIELDS`(`last_run_at`·`skip_cycles`·`cursor`·`paused_reason`·`last_result`·`last_skip_at`)는 기계만 쓴다. 기계 쪽 쓰기는 전부 `save_scheduler_state()`를 거쳐 **저장 직전 재적재 후 자기 필드만 병합**한다(주기 스냅샷이 주기 중의 설정 변경을 덮는 lost update 차단 — NFR3 ⓓ·DQ-49·DQ-46). `update()`도 대칭으로 사용자 필드만 얹는다. 파일 부재·JSON 손상·키 누락은 전부 **기본값 병합으로 폴백**하고 다음 저장에서 정상 파일로 재생성한다 — 상태 파일 하나 때문에 대시보드가 죽지 않는다 (FR37.13) |
| `get_view()` | `GET /schedule` 응답 조립 — 저장값 + 파생값(`next_due_at = last_run_at + interval`, `running = 현재 job이 schedule_run이고 running`). 파생값은 **저장하지 않는다**(간격을 바꾸면 즉시 새 값이 나와야 한다) |
| `update(**fields)` | `POST /schedule` 부분 갱신. `interval_days ∈ {3,7,14,28}`(기본 **3**)·`max_videos_per_cycle ∈ [1,200]`(기본 30) 위반은 `ValueError`(→400). **사용자 소유 필드를 쓰는 유일한 경로**다. `enabled`를 **켤 때마다 `last_run_at = now`** 로 갱신한다 — "비어 있을 때만"이면 껐다가 한참 뒤 다시 켤 때 "간격 경과"가 이미 참이라 토글하자마자 36채널 추출이 시작된다(옵트인의 취지를 배반한다). 첫 실행을 당기고 싶으면 `run-now`를 쓴다 |
| `request_now()` | `POST /schedule/run-now`. `last_run_at`을 **간격만큼 과거로 당겨** 도래 상태로 만들고 틱 이벤트를 `set()`한다. 실행 경로는 평상시와 동일하다(DQ-50). `skip_cycles`는 **소모하지 않는다**(백오프는 그대로 두고 이번 실행만 허용) |
| `decide_cycle(st, now, *, busy, cookie_warning, started_at)` | **순수 함수** — 부수효과 없이 `("run" \| "skip" \| "idle", 사유, 갱신된 st)`를 돌려준다. 판정 순서 고정: ① `enabled`? ② `now - started_at >= 300초`(기동 유예, FR37.2) ③ 도래(`now >= last_run_at + interval`)? ④ `skip_cycles > 0` → **1 감소 + `last_run_at = now`** 후 `skip`(요청 0, FR37.10) ⑤ `cookie_warning` → `paused_reason="cookie"`로 `idle`(**`last_run_at` 미갱신** — 쿠키를 고치면 다음 틱에 곧바로 돈다, FR37.11) ⑥ `busy` → `idle`(미갱신, 60초 뒤 재시도, FR37.12) ⑦ `run`. **순서가 계약이다** — ④를 ⑤·⑥보다 뒤에 두면 쿠키가 만료된 동안 백오프가 소모되지 않아 차단 회복 후에도 계속 쉰다 |
| `build_plan(new_by_channel, st, reg)` | RSS 결과 → 실행 계획. ⓐ 채널 순서는 레지스트리 순서에 **`cursor`부터 회전**(FR37.8 기아 방지) ⓑ `max_videos_per_cycle`까지만 담고 다음 채널을 새 `cursor`로 기록 ⓒ 채널별 새 영상 수가 **15(`rss_monitor` 피드 상한)에 도달하면 `truncated`** 표시(DQ-47) ⓓ `by_channel[name] = {"url": 등록 URL, "entries": [{"id","title"}]}` + `videos_view`(같은 영상들을 `members_only:False`·`playlists:[]`로) 조립 — `_run_grouped`가 기대하는 스캔 캐시와 **같은 모양**이다. **`published`를 `upload_date`로 넘기지 않는다**(형식 불일치 + FR2.6과 같은 보수 원칙) |
| `run_cycle(manager)` | 한 주기 실행: 대상 채널(`names(auto_only=True)`) → `rss_monitor.check_new_videos(names=)` → `build_plan` → 계획이 비면 **job 없이 종료** → 아니면 `manager.start_schedule(plan)`. 작업 종료를 폴링으로 기다렸다가 job 스냅샷에서 `last_result`를 채우고 `aborted_429`면 `skip_cycles`를 1→2→4로 승급, 아니면 0으로 리셋한다. `last_run_at = now`는 **취소·429 중단으로 끝난 주기에도 갱신**한다(FR37.12 — 갱신을 빠뜨리면 60초 뒤 같은 작업이 되살아나 사용자와 싸운다). 마감(`_finish_cycle`)은 **주기 시작 시점 st를 받지 않고 상태를 다시 읽어** 스케줄러 소유 필드만 병합하며, 작업 대기 중 예외가 나도 `try/finally`로 **반드시 마감한다**(마감 누락 = 같은 주기 재실행) |
| `SchedulerThread` / `start(manager)` / `stop()` | `threading.Thread(daemon=True)` 1개. 대기는 `threading.Event.wait(60)`이라 `request_now()`·종료 신호에 **즉시 깨어난다**(sleep 폴링 아님). 루프 본문은 `try/except`로 전부 감싸 **어떤 예외도 스레드를 죽이지 못하게** 한다(죽으면 조용히 영원히 멈춘다 — 무인 기능의 최악 실패 모드). 기동은 `server.py`의 `startup` 훅, `SCHEDULER_DISABLED=1`이면 기동하지 않는다 |

> **왜 `jobs.py`가 아니라 별도 모듈인가:** `JobManager`는 "요청 하나를 실행하는 것"이고 스케줄러는 "언제 요청할지 정하는 것"이다.
> 후자는 시계·상태 파일·백오프라는 **완전히 다른 상태 기계**이며, `decide_cycle`을 순수 함수로 분리해야
> 일 단위 동작을 초 단위 단위 테스트로 검증할 수 있다(V-U33 — 실제로 3일을 기다려 검증할 수는 없다).

### 2.14 selfcheck.py (신규, v5.11) — FR38

`audit`(문서·코드 정합)와 `doctor`(데이터 건전성)의 **검사 구현과 공통 계약**을 한 모듈에 둔다.
CLI 명령은 둘로 분리되지만(FR38.2·DQ-51) 모듈을 쪼개지 않는 이유는 **공통 계약이 흩어지면 그것이 다음 drift**이기 때문이다 —
발견 표현·심각도·예외 적용·출력 형식·종료코드는 두 명령이 **한 글자도 다르지 않아야** 한다.
의존은 표준 라이브러리 + `config`·`channel_registry`·`folder_ops`·`video_access`·`scheduler`·`cookie_health`뿐이고,
**`chromadb`·`sentence_transformers`·`yt_dlp`를 임포트하지 않는다**(FR38.15).

| 함수·클래스 | 설계 |
|---|---|
| `Severity` | `ERROR` / `WARN` / `INFO` 3단. 정렬·집계·종료코드 계산의 기준이며 검사 함수가 새 등급을 만들지 않는다 (FR38.7) |
| `Finding(check, target, message, severity, evidence=None)` | 발견 1건. `check` = 고정 ID(`audit.*`/`doctor.*`), `target` = waiver 매칭 키가 되는 **안정 문자열**(파일 경로·채널명·번호 등 — 가변 메시지를 키로 쓰면 waiver가 조용히 풀린다), `evidence` = 수치 dict(`--json`에 실린다) |
| `AUDIT_CHECKS` / `DOCTOR_CHECKS` | `{검사ID: 함수}` **레지스트리**. 함수는 `ctx`를 받아 `list[Finding]`을 돌려주는 순수 함수에 가깝게 쓴다(입력 = 읽은 텍스트·파싱 결과). 새 검사 추가 = 함수 1개 + 등록 1행이며 이것이 FR38.18의 "열어 둔 자리"다 |
| `AuditContext` / `DoctorContext` | 입력을 **한 번만 읽어** 검사들이 공유한다. `AuditContext` = 문서 텍스트 5종 + `tests/test_unit.py` + 소스 인덱스(단어 집합) + `main.py` 서브파서 목록. `DoctorContext` = 레지스트리 + 채널별 `{dir, state, meta 목록, chroma video_id 집합, extract_log 행}`. 파일을 두 번 읽는 검사가 생기면 전수 스캔 예산(FR38.14)이 무너진다 |
| `load_waivers(path)` / `apply_waivers(findings, waivers)` | `audit_waivers.yaml` 적재 → `(check, target)` 정확 일치로 면제. **와일드카드·정규식을 지원하지 않는다**(FR38.10ⓐ). 반환은 `(남은 발견, 면제된 발견, stale waiver 목록)`이며 stale은 `WARN` Finding으로 승격된다 — 이 승격이 예외 파일이 썩지 않게 하는 유일한 장치다. **stale 판정은 '전부 돈 검사'에만 적용한다**: `WHOLE_CHECK`(`(전체)`) 건너뜀 표식이 있는 검사와 **부분 범위 실행(`doctor <채널>`)** 은 제외한다 — 보지 못한 발견을 근거로 '예외가 썩었다'고 하면 그것이 오탐이고(FR38.8), 전수에서 0인 기준선이 채널 실행에서만 깨진다 |
| `render_text(findings, summary)` / `render_json(...)` | FR38.4·38.5. 같은 발견 집합에서 형식만 다르다. 텍스트는 `[E]`/`[W]`/`[I]` 접두 + 심각도·검사ID 순 정렬, 0건이면 요약 1줄만 |
| `exit_code(findings)` | `ERROR>0 → 2` · `WARN>0 → 1`(`--strict`면 1도 실패로 취급하는 것은 호출자 몫이 아니라 이 함수의 인자 `strict`로 표현) · 그 외 `0`. **점검 자체 실패는 예외를 올려** `main`이 `3`으로 변환한다(FR38.6 — "이상 없음"과 "확인 못 함"을 절대 같은 코드로 내지 않는다) |
| `_read_chroma_ids(chroma_dir)` | `sqlite3.connect("file:<chroma.sqlite3>?mode=ro", uri=True)` → `embedding_metadata`에서 `key='video_id'`의 distinct `string_value` 집합 + 청크 수. 파일 부재·테이블 부재·조회 실패는 **예외를 삼키고 `None`** 을 돌려주며 호출한 검사가 "건너뜀(INFO)"으로 보고한다. `chromadb.PersistentClient`를 쓰지 않는 이유는 **디렉터리·스키마를 쓰기 때문**이다(FR38.3·DQ-55) |

> **왜 `main.py`에 직접 쓰지 않는가:** 검사 본문은 단위 테스트가 **합성 픽스처**로 참·거짓 양쪽을 돌려야 한다(FR38.19).
> `main.py`의 `cmd_*`는 인자 파싱과 종료코드 변환만 맡고, 검사는 전부 import 가능한 순수 함수로 둔다.

---

## 3. 데이터 플로우

### 3.1 채널 등록 + 추출 (`add URL`)

```
ChannelRegistry.add(url) → channels.yaml 등록 → Extractor(config).run()
```

### 3.2 증분 업데이트 (`run [채널] [--limit N]`)

```
scan_channel()  ── videos+streams 병합, live 진행중 제외 (FR16)
scan_playlists() ─ video_id→재생목록 매핑 (FR15, 요청 1+N회)
각 entry:
  decide(vid, mod, up)     ─ 날짜 미제공 → skip (FR2.6)
  ├ skip                   → 통과
  ├ limit 도달             → 카나리아 종료 (FR14.5)
  ├ 8~12개(랜덤)마다 45~90초(랜덤) 휴식 (FR14.2, BatchRest — 그룹 추출은 채널 간 공유)
  └ process_video(vid, action, content_type, pl_map)
      ├ 성공   → stats, 429카운터 리셋
      ├ 429    → 지수 백오프 → 같은 영상 1회 재시도(예산 소비),
      │          재실패 시 포기·다음 영상, 연속 5회 시 중단 (FR13.5·14.3)
      └ 멤버십 → availability 1차 / 오류 메시지(영·한) 2차 → _mark_skip(members_only)
                 ※ 429를 **먼저** 확정한 뒤 비-429 실패에만 적용 (DQ-38)
state.save() → _backfill_meta(매핑 비면 생략, FR15.5) → 통계 로그
```

### 3.3 품질 검토 (`review [--llm]`) — 규칙 → SUSPECT만 LLM → review_report.csv

### 3.4 재처리 (`reextract`) — SUSPECT → state.remove → process_video 덮어쓰기

### 3.5 KL 인덱싱 (`index`) — srt/desc 청킹 → bge-m3 → 2개 컬렉션 upsert (playlists·content_type 태그 포함)

### 3.6 질의 (`ask` / 대시보드)

```
단순 RAG:   KLQuery.ask → search(top_k) → 컨텍스트 주입 → LLM → 답변+출처
멀티스텝:   KLHarness.run → tool_use 루프(search/get_full/summarize/list_videos)
            → max 10스텝 → {answer, steps, trace}
```

### 3.7 대시보드 추출 (FR17~18)

```
URL 입력 → classify_url
├ 영상 URL  → POST /extract {url} ─ full info 1회 → 채널 URL 조립 → 미등록이면 자동등록
│              → process_video(info=선조회분) → 해당 영상만
└ 채널 URL → POST /extract/scan ─ 병합 스캔+재생목록 매핑
              → scan_id 캐시(10분): videos_view + 원본 entries + pl_map
              → 조건 UI (ⓒ카테고리→ⓓ멤버십→ⓔ검색어 AND → 마지막 ⓐ최신N slice, 미리보기)
              → POST /extract {scan_id, filters, index} → JobManager 스레드
                  → 대상 id로 원본 entries 재구성(순서 보존)
                  → Extractor.run(entries=, pl_map=, date_range=, progress=cb)  ← 재스캔 없음
                      └ 영상별: _out_of_range → 범위 밖이면 date_skip(자막 미다운로드, state 미기록)
클라이언트: GET /extract/status 2초 폴링 (phase·M/N·현재 제목·stats)
취소:      POST /extract/cancel → Event set → 현재 영상 완료 후 break
              → finishing 보고 → state.save() → _backfill_meta() → stats.cancelled=True
              → **인덱싱 생략** → status=cancelled   (DQ-14)
완료:      index 옵션 & 신규+수정>0 이면 KLIndexer.index_all() → status=done
```

### 3.8 쿠키 경고 감지·해제 (FR19)

```
yt-dlp 실행(모든 경로) → YDLLogger.warning("...no longer valid")
  → mark_invalid() ─ 프로세스 내 최초 1회만 기록, 최초 detected_at 보존
  → output/.cookie_status.json {invalid, message, detected_at}
GET /cookies → present·mtime + (detected_at ≥ 쿠키 mtime ? 경고 : 해제)
쿠키 갱신(파일 mtime 갱신) → 경고 자동 해제. bind mount 특성상 serve 재시작 권장
멤버십 재시도: state의 members_only 항목 → 쿠키 존재 시 매 run "updated"로 재시도 (FR19.1)
              단 대시보드에서 include_members=false면 대상 단계에서 제외 (FR17.4ⓓ 우선)
```

### 3.9 검색 기반 일괄 추출 (FR34)

```
검색어 q + ⓐlimit + ⓑmin_duration + ⓒperiod + folder
  → POST /extract/scan {q, limit, min_duration, period, folder}
      → _build_search_url(q, period)   …/results?search_query=q&sp=<프리셋>   ← ⓒ 1층: 서버측 프리필터
      → flat 스캔  _search_opts(limit) = {extract_flat, playlist_items:"1-N"}  ← ⓐ 상한
      → 엔트리별: duration < min_duration ? 제외 : 통과                        ← ⓑ (결측은 통과)
                  _entry_channel(e, reg) → 원채널 해석(DQ-19) → by_channel
                  state 조회 → extracted / members_only(state만)
      → scan_id 캐시 {kind:"search", query, folder, videos_view, by_channel}
  → 조건 UI (ⓐ최신N·ⓓ멤버십·ⓔ키워드 + ⓒ임의 날짜 since/until)
  → POST /extract {scan_id, filters, index}  → job kind="search_run"
      → 채널 그룹 순차:
           미등록이면 registry.add → set_group(folder) → set_auto_run(False)   ← FR34.6~34.7
           Extractor.run(entries=g, pl_map={}, date_range=…, progress=cb)
              └ 영상별 full info → upload_date로 ⓒ 2층 확정 → 범위 밖 date_skip (DQ-12)
                              → is_live/is_upcoming → live_wait (FR16.5, 검색 경로의 유일한 라이브 방어)
      → 변경 있는 채널만 index_all(on_progress=) → done
결과: 각 영상은 원채널 폴더에 저장되고, 신규 채널들은 folder 하나로 묶여 라이브러리에 보인다.
      그 채널들은 ./yt.sh run(인자 없음)의 순회 대상이 아니다.                   ← DQ-25
```

### 3.10 폴더 디렉터리화 (FR35)

```
[그룹 지정/변경]  POST /channels/group
   is_busy()? ──yes──> 409
   validate_path_segment(group) ──실패──> 400 (디스크 무변경)
   check_namespace(new_group) ──충돌──> 409
   mkdir(output/<G>) → os.rename(output/<C> → output/<G>/<C>)
        └─실패(EXDEV·목적지 존재)──> 예외, 이동 0
   set_group(C, G)  ──실패──> 역방향 os.rename 보상 롤백
   빈 원본 그룹 폴더 rmdir

[마이그레이션]  ./yt.sh migrate-groups [--apply]
   [호스트 yt.sh] cp -a output/ output_backup_<ts>/   # 기본 on, --no-backup으로만 해제 (244MB)
   plan_migration()            # 읽기 전용 계획 (dry-run 기본)
   ├ 사전검증 ①락·저널 ②세그먼트 ③이름공간 ④목적지 부재 ⑤st_dev ⑥확인
   │    └─하나라도 실패──> 아무것도 옮기지 않고 중단
   lock() → for ch in plan: os.rename → journal.append+fsync
   │    └─실패──> 저널 역순 자동 롤백 → unlock → 비정상 종료
   journal → .done.json → unlock
   (channels.yaml 은 한 글자도 바뀌지 않는다)
```

### 3.11 주기 자동 추출 (FR37)

```
[틱 — 60초마다, serve 프로세스 내 데몬 스레드]        네트워크 0
  decide_cycle(state, now, busy=MANAGER.is_busy(), cookie_warning=get_status().warning)
   ├ enabled=false ─────────────────────────────> idle (기본값 — 배포만으로는 아무 일도 없음)
   ├ 기동 후 5분 미만 ──────────────────────────> idle (절전 복귀·자동 시작 직후 DNS 미비)
   ├ now < last_run_at + interval ──────────────> idle   (interval 기본 3일, 선택 3·7·14·28)
   ├ skip_cycles > 0 ─> skip_cycles-- · last_run_at=now · last_skip_at=now ─> skip  (백오프, FR37.10)
   │                    ※ last_result는 건드리지 않는다 — 직전 429 기록이 증거다
   ├ 쿠키 warning ────> paused_reason="cookie"  ─> idle  (last_run_at 미갱신 = 자가 치유)
   ├ is_busy() ───────────────────────────────> idle  (미갱신 → 60초 뒤 재시도, FR37.12)
   └ run ↓

[주기 실행]
  대상 = ChannelRegistry.names(auto_only=True)         ← 검색 유입 채널 제외 (FR37.5, 실측 77→36)
  rss_monitor.check_new_videos(names=대상)             ← 429 예산과 무관 (FR29.4)
   ├ errors[채널] ─────> 이번 주기 제외 + 결과에 기록 (전체 스캔으로 승격하지 않음)
   └ channels[채널] = [{id,title,published}]  (피드 상한 15 — 도달 시 truncated 표시, DQ-47)
  build_plan  → cursor부터 회전 · max_videos_per_cycle까지 절단 (FR37.8)
   └ 계획이 비면 ──────> job 없이 주기 종료 (요청 0, "새 영상 없음")
  MANAGER.start_schedule(plan)      job kind="schedule_run"
   └ _run_grouped(filters={include_members:True}, group_title=None, merge_categories=False)
        채널 순차: Extractor.run(entries=[{id,title}], pl_map={}, rest_state=공유 BatchRest)
          ├ stats.aborted_429 ──> 남은 채널 중단 + job 경고           ← 주기 내 회로차단 (FR37.9)
          └ 변경 있는 채널만 index_all(on_progress=)                  ← FR33 증분
  결과 기록(마감은 try/finally로 보장 · 상태를 다시 읽어 스케줄러 소유 필드만 병합):
            last_run_at=now(취소·중단이어도 갱신) · last_result ·
            aborted_429면 skip_cycles 1→2→4, 아니면 0으로 리셋        ← 주기 간 백오프 (FR37.10)
            ※ 주기 중 바뀐 enabled·interval_days·예산은 **덮지 않는다** (NFR3 ⓓ)
```

- 카테고리(재생목록)는 이 경로에서 매핑하지 않는다 — `pl_map={}`이라 요청 0이고, 다음 전체 run의 백필(FR15.5)이 채운다.
- 자막 **수정 감지**(FR2.2)는 이 경로의 대상이 아니다 — RSS "새 영상"은 state에 없는 영상이라는 뜻이다(FR37.17).

### 3.12 정합 감사 · 건전성 점검 (FR38)

```
./yt.sh audit                                   ./yt.sh doctor [채널]
  │                                               │
  ├─ AuditContext 1회 적재                        ├─ DoctorContext 1회 적재
  │   REQUIREMENTS.md · DESIGN.md                 │   channels.yaml (98채널)
  │   qa-verifier.md · pipeline-verify/SKILL.md   │   output/ 워크 → 채널별
  │   CLAUDE.md · spec-sync/SKILL.md              │     state.json · meta/*.json 목록
  │   tests/test_unit.py (V-U 마커 = 정본)        │     srt·txt·meta·desc stat
  │   소스 단어 색인(*.py·index.html·yt.sh)       │     extract_log.csv 행
  │   main.py 서브파서 목록                       │     chroma.sqlite3 (mode=ro) → video_id 집합
  │                                               │   .scheduler.json · .cookie_status.json
  │   실측 < 0.3s · output/ 불필요                │   실측 0.02s + chroma 0.32s · 네트워크 0
  ▼                                               ▼
AUDIT_CHECKS 8종 순차 실행                      DOCTOR_CHECKS 10종 순차 실행
  (판정 불가 토큰·대상은 침묵 — FR38.8)           (판정 불가·스키마 상이는 INFO 건너뜀)
  │                                               │
  └──────────────┬────────────────────────────────┘
                 ▼
       문서 내 표준 표기로 1차 면제 (FR38.9)
         §6 `(구현 없음 — …)` · §9.1a `**테스트 미구현**` · `(구현 예정)` · §9.1a 위치 열
                 ▼
       audit_waivers.yaml 2차 면제 (FR38.10)
         (check, target) 정확 일치 · 면제분은 세어서 노출
         대응 발견 없는 waiver → stale WARN 승격
                 ▼
       render_text / render_json  →  exit_code
         0 이상 없음 · 1 경고만 · 2 오류 · 3 점검 실패
```

- **파일을 쓰지 않는다.** 두 경로 어디에도 열기·생성·기록이 없다(FR38.3). `chroma/`는 읽기 전용 sqlite로만 열고,
  `KLIndexer`·`chromadb`를 경유하지 않는 이유가 여기 있다 — `_get_client()`는 `mkdir(parents=True, exist_ok=True)`를 한다(DQ-55).
- **자동 실행 경로가 없다.** `scheduler.py`·`server.py`의 어디에서도 호출되지 않는다(FR38.16·DQ-56).
  호출 주체는 사람 또는 개발 하네스 게이트(§11.2)뿐이다.
- **스냅샷을 남기지 않는다.** 그래서 "직전 대비 변화" 계열 검사는 채택하지 않았고, 대신
  `doctor.detector-fossils`처럼 **같은 데이터 안의 모순**을 본다(DQ-56).

---

## 4. 출력 폴더 구조

```
output/                              # 최상위 = "그룹 폴더" + "그룹 미지정 채널 폴더" 공용 이름공간 (FR35.6)
├── .cookie_status.json              # 쿠키 경고 상태 (FR19.2, 컨테이너 공유 — 위치 불변)
├── .migration.lock                  # 마이그레이션 중에만 존재 (FR35.10)
├── .migration_journal.json          # 이동 저널 (실패 시 역순 롤백, FR35.12)
├── AI LLM Wiki/                     # ← group (FR35.1) — 그 자체는 채널이 아니다
│   ├── 두두감자/
│   │   ├── srt/  txt/  desc/  meta/ # 자막·전문·설명·메타
│   │   ├── chroma/                  # 채널별 독립 KL (폴더 이동 시 함께 따라간다)
│   │   ├── state.json               # 증분 상태
│   │   ├── playlists.json           # video_id→재생목록 매핑 (FR15.1)
│   │   ├── extract_log.csv
│   │   └── review_report.csv
│   └── 다른채널/ (완전 격리, FR7.4·NFR8)
├── 역배열1/
│   └── …
└── 그룹없는채널/                    # group 미지정 → 최상위 유지 (FR35.1)
    └── srt/  txt/  …
```

> 중첩은 **1단계뿐**이다(FR35.1). 하위 구조는 승격 전후가 완전히 동일하므로 `channel_subdirs()` 이하 모든 코드가 무변경이다.

---

## 5. 스키마 정의

### 5.1 channels.yaml (FR7.1)

```yaml
channels:
  두두감자:
    url: https://youtube.com/@두두감자/videos
    lang: ko
    added_at: "2026-06-21"
    note: "장투 관점 요약 위주"   # 채널 메모 — 한 줄·200자 (FR36.1). 빈 값도 ""로 상시 보존
    group: "AI LLM Wiki"       # 선택 — 실제 출력 디렉터리 (FR25.1·FR35.1) → output/AI LLM Wiki/두두감자/
    channel_id: "UCxxxxxxxx"   # 선택 — RSS 1회 해석 캐시 (FR29.1)
    auto_run: false            # 선택 — ./yt.sh run·transcribe 전체 순회 제외 (FR34.7)
```

> 선택 필드는 **값이 기본값이면 기록하지 않는다**: `group`은 빈 값이면 제거(FR25.1), `auto_run`은 `true`이면 제거.
> **`note`는 이 규칙의 예외**다 — `add()`가 신규 등록 시 항상 쓰고 기존 77채널이 전부 `note: ""`를 갖고 있어, 빈 값일 때 제거하면
> 오히려 파일이 불균일해진다. 따라서 메모 삭제는 **필드 제거가 아니라 `""` 기록**이다(FR36.1·DQ-39).
> **v5.8 —** `note`는 v5.8 전까지 **쓰기만 하고 읽는 곳이 없던 필드**였다(등록 77채널 전부 빈 문자열). 기능 추가에 **마이그레이션·백필이 없는 이유**가 이것이다.
> **v5.6 —** `group` 값은 `config.validate_path_segment`를 통과한 **NFC 정규화 문자열**이며 디렉터리명과 **1:1**이다(FR35.4). `add()`는 이 파일의 기존 항목을 **덮어쓰지 않는다**(upsert — FR7.7).
> 따라서 **`auto_run` 부재 = `true`**(기존 channels.yaml은 무변경으로 종전과 동일하게 동작한다).

### 5.2 state.json (채널별)

```json
{
  "VIDEO_ID": {
    "upload_date": "20260315", "modified_date": "20260820",
    "sub_type": "manual", "extracted_at": "2026-08-01T10:00:00",
    "basename": "20260315_영상제목"
  },
  "MEMBERS_VIDEO": {
    "upload_date": "00000000", "modified_date": "members_only",
    "sub_type": "members_only", "extracted_at": "", "basename": ""
  }
}
```

`sub_type` 값: `manual` | `auto` | `none`(무자막) | `members_only`(접근 불가 → 쿠키 존재 시 재시도 대상, FR19.1)

### 5.3 meta/*.json (FR3.2 + FR12.2·15.2·16.4)

```json
{
  "id": "VIDEO_ID", "title": "…", "upload_date": "20260315",
  "modified_date": null, "duration": 600, "duration_string": "10:00",
  "view_count": 100000, "like_count": 5000, "comment_count": 200,
  "tags": [], "categories": ["Science & Technology"],
  "thumbnail": "…", "webpage_url": "…", "channel": "두두감자",
  "sub_type": "auto",
  "playlists": ["국내주식", "바이브코딩 (주식 자동매매 시스템 만들기)"],
  "content_type": "video",
  "tickers": ["005930", "TSLA"],
  "extracted_at": "2026-08-01T14:00:00"
}
```

> `tickers`는 **문맥 근거가 있는 값만** 담는다(FR12.5·DQ-43). 위 예시는 "종목코드 005930"·"$TSLA"처럼
> 근거가 붙은 경우이고, **실측 441개에서는 근거가 없어 전부 `[]`** 다 — 빈 배열이 정상이다(FR12.6).
> 기존 파일 정리는 `./yt.sh backfill-tickers [--apply]`(기본 dry-run, FR12.7). ChromaDB 청크 메타에는
> `tickers`가 없으므로 백필 후 **재인덱싱이 필요 없다**(소비처는 `kl_query.list_videos`·`/subtitle` 뿐).

### 5.4 ChromaDB 컬렉션 (FR6.5·15.3) — 2개 분리

`subtitle_chunks` 메타데이터: `video_id · title · upload_date · sub_type · playlists(쉼표 join 문자열) · content_type · chunk_index · start_seconds · source_url(?t=Ns)`  
`desc_chunks` 메타데이터: `video_id · title · upload_date · playlists · content_type · chunk_index · source_url`

> ChromaDB 메타데이터는 str/int/float/bool만 허용 → 재생목록 리스트는 쉼표 join, 빈 값은 `""`.

### 5.5 review_report.csv — video_id·title·basename·verdict(OK/SUSPECT/FAIL)·reason·word_count·ko_ratio·repeat_ratio·llm_comment

### 5.6 playlists.json (FR15.1)

```json
{ "VIDEO_ID": ["국내주식"], "VIDEO_ID2": ["국내주식", "강의"] }
```

### 5.7 작업(job) 상태 dict (FR18)

```json
{
  "job_id": "20260802-153012",
  "kind": "channel_run | single_video | playlist_run | search_run | schedule_run",
  "channel": "두두감자", "url": "입력 URL",
  "status": "running | done | cancelled | error",
  "phase": "registering | extracting | indexing | finishing",
  "total": 51, "done": 12, "current_title": "…",
  "stats": {"new": 3, "updated": 0, "skip": 9, "no_sub": 0,
            "members_only": 0, "error": 0, "date_skip": 0},
  "error": null, "started_at": "…", "finished_at": null
}
```

- **phase 전이:** `registering`(job 생성 시 초기값) → `extracting` → (조건 충족 시) `indexing` → `finishing`(종료 시 항상).
  `scanning`·`playlists`는 `Extractor.run`이 직접 스캔할 때만 progress로 올라오며, 대시보드 채널 워커는 스캔 캐시를 넘기므로 나타나지 않는다.
  채널 사전 스캔(`POST /extract/scan`)은 job을 만들지 않으므로 스캔 중에도 `/extract/status`는 직전 작업의 최종 상태를 유지한다.
- **stats 8키(`live_wait` 포함)는 job 생성 시 0으로 선점**한다 — extractor가 늦게 채워도 프론트가 `undefined`를 보지 않는다.
- `stats["cancelled"]`(bool)은 **취소된 실행에서만** 존재하는 추가 키다. 카운터가 아니므로 집계 시 화이트리스트(위 8키)로만 합산한다.
- `done`은 skip된 영상도 포함해 증가한다(진행률이 `total`에 도달). 따라서 **`total == new+updated+skip+no_sub+members_only+error+date_skip`**(V-D11의 등식).
  **검색 작업(`search_run`)에서는 `live_wait`를 등식에 포함한다** — 채널·재생목록 스캔은 `live_status`로 진행 중 라이브를 사전 제외하지만
  검색 flat에는 그 필드가 없어(실측 0/15) 진행 중 라이브가 대상에 남고 처리 시 FR16.5 가드가 `live_wait`로 집계하기 때문이다 (DQ-26).
- **`schedule_run`(v5.10, FR37.7)** 은 스케줄러가 만든 job이다. 구조·phase·stats 규약은 `playlist_run`/`search_run`과 완전히 같고(같은 `_run_grouped`), `channel`에는 표시용 문자열 `"자동 추출"`이 들어간다. 429 연속 차단으로 남은 채널을 건너뛴 경우 `status`는 `done`이고 사유는 `warnings`에 남는다(FR37.9).
- `status`는 `done|cancelled|error`로 끝나며 마지막 job은 메모리에 유지된다. `{"status":"idle"}`는 프로세스 기동 후 한 번도 작업이 없었을 때만.

### 5.8 output/.cookie_status.json (FR19.2)

```json
{ "invalid": true, "message": "…no longer valid…", "detected_at": "2026-08-02T10:00:00" }
```

`detected_at`은 **최초 감지 시각**이다(중복 기록 방지 — §2.11). `GET /cookies` 응답은 이 파일과 키가 다르며(`invalid`→`warning`), 쿠키 mtime과 비교해 자동 해제를 계산한다.

### 5.9 대시보드 API (FR17~20)

| 메서드/경로 | 요청 | 응답(성공) | 오류 |
|---|---|---|---|
| `POST /extract/scan` | `{url}` 또는 `{q, limit, min_duration, period, folder}` | `{scan_id, kind, channel, videos:[{id,title,channel?,content_type,playlists,members_only,extracted,duration?}], playlists:[제목…]}` | 400 판별불가·영상 URL·핸들 추출 실패·`url`과 `q` 동시 지정·`q` 공백·`limit` 범위 밖 / 409 작업(추출·스캔) 실행 중 |
| `POST /extract` | `{url, index}` 또는 `{scan_id, filters:{latest,since,until,categories,include_members,keyword}, index}` | 202 `{job}` | 409 실행중 `{detail, job}` / 400 (아래 판정 규칙) |
| `GET /extract/status` | – | `{job}` 또는 `{job:{status:"idle"}}` | – |
| `POST /extract/cancel` | – | `{cancelled: bool}` (실행 중 작업이 없으면 `false`) | – |
| `GET /cookies` | – | `{present, mtime, warning, warning_message, detected_at}` | – |
| `GET /channels/stats` | – | `{channels:[{name,url,lang,added_at,group,auto_run,note,extracted,members_only,no_sub,total_known,last_extracted}]}` (`note`는 v5.8 추가, 부재 시 `""`) | – |
| `POST /channels/auto_run` | `{channel, auto_run}` | `{channel, auto_run}` | 404 미등록 / 409 작업 중(FR21.4 `is_busy`) |
| `POST /channels/note` (v5.8) | `{channel, note}` | `{ok, channel, note}` — `note`는 **서버가 정규화한 최종 값**(제어문자→공백·트림) | 400 경로 문자·**200자 초과**(FR36.1) / 404 미등록 / 409 작업 중·마이그레이션 락(FR36.11) |
| `POST /channels/rename` (v5.8 확장) | `{channel, new_name}` | `{ok, channel}` — 성공 시 **그 채널을 참조하는 스캔 캐시를 삭제**(FR36.8) | 400 이름 검증(FR35.4·FR7.9) / 404 미등록 / 409 중복·이름공간 충돌(FR35.6ⓒ)·작업 중(FR31.5) |
| `POST /channels/group` | `{channel, group?}` | `{channel, group, moved: bool}` | 404 미등록 / **400 그룹명 검증 실패(FR35.4 — 디스크 무변경)** / **409 이름공간 충돌(FR35.6)·작업 중·마이그레이션 락(FR35.10)** / 500 이동 실패(보상 롤백 완료 여부를 `detail`에 명시) |
| `POST /folders/rename` | `{old, new}` | `{old, new, channels: n, moved: bool}` | **400 이름 검증 실패** / **409 충돌·작업 중·락** / 500 이동 실패 |
| `GET /subtitle` | `?channel=&basename=` | `{basename, text}` (txt 전문) | 400 경로탈출(`channel`·`basename` 양쪽 검사), 404 파일 없음 |
| `GET /schedule` (v5.10) | – | `{enabled, interval_days, max_videos_per_cycle, last_run_at, next_due_at, skip_cycles, paused_reason, running, last_result, last_skip_at}` | – |
| `POST /schedule` (v5.10) | `{enabled?, interval_days?, max_videos_per_cycle?}` (준 것만 갱신) | `GET /schedule`과 **같은 전체 상태** | 400 `interval_days ∉ {3,7,14,28}` · `max_videos_per_cycle ∉ [1,200]` — **409 없음**(FR37.14) |
| `POST /schedule/run-now` (v5.10) | – | 202 `{queued:true, …상태}` | – (작업 중이어도 202 — 틱이 busy를 보고 알아서 미룬다, FR37.12·37.15) |
| `GET /videos` | `?channel=` | `{videos:[{video_id,title,upload_date,basename,tickers,playlists,content_type,sub_type,duration,duration_string,url}]}` (날짜 역순) | 400 채널 누락·경로탈출 |

**`POST /extract` 요청 판정 규칙 (전부 400 `{detail}`):**

1. `url`과 `scan_id`가 **둘 다 있음** → 400 "url과 scan_id는 함께 지정할 수 없습니다."
2. **둘 다 없음** → 400 "url 또는 scan_id 중 하나가 필요합니다."
3. `url`이 채널로 분류됨 → 400 "채널 URL은 `/extract/scan`을 먼저 호출하세요." (무조건 전체 추출 폭주 방지 — 채널은 반드시 스캔·조건 단계를 거친다)
4. `url` 판별 불가 → 400 (FR17.1)
5. `scan_id`가 **만료(TTL 10분 초과)되었거나 존재하지 않음** → 400 "scan_id가 만료되었습니다. 다시 스캔하세요." (410 신설 없이 §5.9 오류 집합 유지).
   **v5.8 —** 채널 이름 변경·**삭제**로 캐시가 무효화된 경우(FR36.8)도 "존재하지 않음"에 해당해 **같은 400**이 된다. 프론트는 이름 변경 시점에 조건 화면을 먼저 닫으므로 정상 흐름에서는 이 400을 만나지 않는다

**`POST /extract/scan` 검색 요청 판정 규칙 (전부 400 `{detail}`):** `url`과 `q` 동시 지정 · 둘 다 없음 · `q`가 공백 ·
`limit`이 1~50 밖 · `min_duration`이 음수. `period`는 알 수 없는 값이면 400이 아니라 `all`로 폴백한다(조건 완화는 안전 방향).
`folder`는 트림 후 비면 `q`를 쓴다 (FR34.1~34.6).

**409 규칙:** 추출·스캔이 점유 플래그 하나를 공유하므로 `POST /extract`와 `POST /extract/scan`은 **서로에 대해서도** 409를 낸다.
409 본문은 `{detail, job}`이며 `job`은 직전 job 스냅샷 또는 `null`(스캔만 돌던 중이면 null일 수 있음)이다.
요청 검증(400)이 점유 검사(409)보다 먼저라 잘못된 요청은 점유를 남기지 않는다.

**`GET /channels/stats` 계산 정의:** 채널 목록·`url`·`lang`·`added_at`은 channels.yaml(registry) 기준,
통계는 state.json 집계 — `extracted = count(sub_type ∈ {manual, auto})`, `members_only`, `no_sub = count(sub_type=="none")`,
`total_known = len(state)`, `last_extracted = max(extracted_at)`(빈 문자열 제외, 없으면 `""`, ISO 문자열).

### 5.10 output/.scheduler.json (FR37.13, v5.10)

```json
{
  "enabled": false,
  "interval_days": 3,
  "max_videos_per_cycle": 30,
  "last_run_at": "2026-09-24T03:10:00",
  "skip_cycles": 0,
  "cursor": "두두감자",
  "paused_reason": null,
  "last_skip_at": null,
  "last_result": {
    "started_at": "2026-09-24T03:10:00", "finished_at": "2026-09-24T03:41:12",
    "channels_checked": 36, "channels_with_new": 3,
    "videos_planned": 7, "videos_done": 7,
    "stats": {"new": 7, "updated": 0, "skip": 0, "no_sub": 0,
              "members_only": 0, "error": 0, "date_skip": 0, "live_wait": 0},
    "rss_errors": {"채널명": "RSS 조회 실패: …"},
    "truncated_channels": ["채널명"],
    "aborted_429": false,
    "outcome": "done | cancelled | aborted_429 | no_new | error"
  }
}
```

- **쓰는 주체는 serve 프로세스 하나뿐**이다(CLI 컨테이너는 읽지도 쓰지도 않는다). 그래도 쓰기는 `.tmp` + `os.replace`
  원자 교체다 — 절전·강제 종료로 부분 기록된 JSON이 남으면 무인 기능이 조용히 죽는다.
- `interval_days` **기본값은 3**이고 허용 집합은 `{3, 7, 14, 28}`이다(FR37.3 — 값 선택 근거). `max_videos_per_cycle` 기본 30.
- **필드 소유가 나뉜다** — `enabled`·`interval_days`·`max_videos_per_cycle`은 **사용자 소유**(`POST /schedule`만 쓴다),
  나머지(`last_run_at`·`skip_cycles`·`cursor`·`paused_reason`·`last_result`·`last_skip_at`)는 **스케줄러 소유**다.
  기계는 저장 직전 파일을 다시 읽어 **자기 소유 필드만 병합**한다 — 주기가 수십 분 걸릴 수 있으므로 주기 시작 시점
  스냅샷을 통째로 저장하면 그 사이의 설정 변경(특히 "끄기")이 조용히 원복된다(DQ-46이 `channels.yaml`을 기각한
  lost update를 전용 파일 안에서 재현하는 꼴). 필드를 추가하면 `scheduler.USER_FIELDS`/`SCHEDULER_FIELDS` 중
  하나에 반드시 등록한다(코드 `assert`가 강제한다).
- **`last_result`는 실제로 실행한 주기만 기록한다** — 백오프로 건너뛴 주기는 `last_skip_at`만 남긴다.
  `outcome`에 `skipped_backoff`가 없는 이유다: 덮일 값은 십중팔구 그 백오프를 유발한 `aborted_429` 기록인데,
  사용자가 "왜 멈췄나"를 확인해야 하는 3~12일 동안 그 증거가 0으로 채워진 레코드로 교체된다.
  "건너뛰는 중"은 `skip_cycles` 배너가 더 정확히 말한다. `outcome="error"`는 job이 오류로 끝났거나
  작업 대기 자체가 실패한 주기다(오류를 `done`으로 보고하지 않는다).
- **손상 값 교정(`_sanitize`)** — `enabled`는 JSON 불리언만 인정한다(`bool("no")`가 True인 함정을 피한다:
  모호하면 꺼짐). `last_run_at`이 파싱되지 않으면 `now`로 교정한다 — 비워 두면 "기록 없음 = 즉시 도래"라
  손상 파일 하나가 무인 전체 추출을 촉발한다.
- `next_due_at`·`running`은 **저장하지 않는 파생값**이다(§2.13 `get_view`).
- `cursor`는 "다음 주기에 이 채널부터 계획한다"는 **회전 시작점**이며, 그 채널이 삭제·개명되면 목록에 없으므로 첫 채널로 폴백한다.
- 위치가 `output/`인 근거는 DQ-11(`.cookie_status.json`)·FR35.10(`.migration.lock`)의 선례이며,
  `channels.yaml`을 쓰지 않는 근거는 **lost update**다(DQ-46·DQ-42).

### 5.11 audit_waivers.yaml · 발견(finding) 출력 (FR38.5·38.10, v5.11)

```yaml
# audit_waivers.yaml — 감사 예외 선언 (저장소 루트, 사람이 쓴다)
# 1차 예외는 문서 안의 표준 표기로 표현한다(FR38.9). 이 파일은 그것으로 표현할 수 없는 잔여만 담는다.
waivers:
  - check: audit.traceability          # 검사ID — 정확 일치 (와일드카드 금지)
    target: "Reprocessor"              # Finding.target 과 정확 일치
    reason: "FR5 행의 '(Extractor 재사용)' 개념명 — 같은 이름의 클래스는 존재하지 않는 것이 정상"
    added: "2026-09-26"
    # expires: "2027-03-31"            # (선택) 이 날짜 이후에는 면제하지 않고 원래 심각도로 보고
  - check: doctor.meta-fields
    target: "tickers/all-empty"
    reason: "DQ-43 — 이 코퍼스의 정답이 빈 값이다(실측 495/495). 느슨하게 되돌리지 말 것(FR12.6)"
    added: "2026-09-26"
  - check: doctor.detector-fossils      # 해소된 과거 사건 — 심각도는 내리지 않는다
    target: "변곡점주식/aetOCkgzurM"     # 채널/video_id 정확 일치 (새 화석은 걸러지지 않는다)
    reason: "2026-09-24에 FR13.7·DQ-38으로 감지 규칙을 고쳤고 이 행은 그 이전의 화석이다. append-only 감사 기록이라 고칠 수 없다"
    added: "2026-09-26"
```

- **와일드카드·정규식 없음**(`check: "audit.*"`·`target: "*"` 금지) — 검사 한 종류를 통째로 끌 수 있으면 그것이 첫 은폐 수단이 된다.
- **대응 발견이 없는 항목은 `stale waiver` 경고**로 올라온다 — 예외가 자기 수명을 스스로 신고하게 만드는 장치다.
  (`note`·`tickers`가 죽어 있던 이유는 "아무도 읽지 않아서"였다.)
- 파일이 없으면 예외 0건으로 동작한다(부재는 오류가 아니다).
- **부분 범위 실행(`doctor <채널>`)에서는 stale 판정을 하지 않는다.** 다른 채널의 발견을 보지 못한 채
  "대응 발견이 없다"고 하면 그것이 오탐이고(FR38.8), 전수에서 0인 기준선이 채널 실행에서만 빨개진다.
  (기존의 `WHOLE_CHECK` 건너뜀 표식은 그대로 유지된다 — 이쪽은 범위가 아니라 **검사 자체**를 못 돈 경우다.)

> **waiver를 쓸 자격 (2026-09-26 — `doctor` 기준선 0 작업에서 확정)**
> waiver는 "귀찮은 발견을 치우는 수단"이 아니다. 두 경우에만 쓴다:
> ⓐ **설계상 정상이 아니고**(정상이라면 심각도 자체가 틀린 것이다 → 정보로 내리고 근거를 표에 쓴다)
> ⓑ **해소된 과거 사건의 잔해**여서 지금 고칠 대상이 남아 있지 않은 것.
> 화석 waiver가 ⓑ의 표준례다 — 규칙은 이미 고쳐졌고(2026-09-24, FR13.7·DQ-38) 로그는 append-only라 못 고친다.
> **그래서 심각도를 내리지 않았다**: 같은 검사에서 **새 발견이 나오면 그것은 규칙이 또 뚫렸다는 진짜 신호**이고
> 오류로 잡혀야 한다. 심각도를 내리면 그 신호까지 함께 죽는다. 이 구분(정보 강등 vs waiver 등재)이
> FR38.7의 "조치 가능성" 기준이며, 되돌리기 전에 이 문단을 먼저 읽어야 한다.

`--json` 출력 (사람용 출력과 **같은 발견 집합**, 형식만 다르다):

```json
{
  "command": "doctor",
  "started_at": "2026-09-26T15:40:02",
  "elapsed_sec": 0.41,
  "checks_run": ["doctor.state-files", "doctor.index-coverage", "..."],
  "checks_skipped": [{"check": "doctor.index-coverage", "target": "output/한균수", "why": "chroma.sqlite3 스키마 상이"}],
  "findings": [
    {"check": "doctor.index-coverage", "target": "output/역배열1/변곡점주식",
     "severity": "warn", "message": "자막 4편이 ChromaDB에 없다 (chroma/ 미생성)",
     "evidence": {"subtitled": 4, "indexed": 0}}
  ],
  "waived": [
    {"check": "doctor.meta-fields", "target": "tickers/all-empty", "severity": "warn",
     "reason": "DQ-43 — 이 코퍼스의 정답이 빈 값이다"}
  ],
  "stale_waivers": [],
  "summary": {"error": 0, "warn": 9, "info": 3, "waived": 1, "exit_code": 1}
}
```

- `check`·`target`·`severity` 3필드는 **계약**이다(훅·CI·이슈 참조의 키). 메시지 문구는 개선해도 되지만 이 셋은 고정이다(FR38.4).
- `exit_code`를 payload에도 싣는 이유: 파이프로 받는 쪽이 프로세스 코드를 잃어도 판정이 가능해야 한다.

---

## 6. 지식층 시스템: ChromaDB

(v3.0과 동일 — 선택 근거·bge-m3·120초 청킹)

- **ChromaDB**: 서버 불필요, pip 설치, 파일 기반 영속, ARM64 네이티브
- **bge-m3**: HuggingFace Hub 자동 다운로드(캐시 공유 마운트), 1024차원, 한·영·다국어
- **청킹**: SRT 120초 윈도우 → `start_seconds`로 `?t=N초` 링크 생성 / 설명 300토큰

---

## 7. 파일 구성

```
yt-subs/
├── yt.sh                  # 래퍼 (FR8)
├── channels.yaml          # 채널 등록부 (FR7)
├── main.py                # CLI 진입점 (bulk_targets = run·transcribe 대상 산출, FR34.7)
├── config.py              # 상수·경로·yt-dlp 옵션·429/쿠키 설정
├── channel_registry.py    # FR7·25.1·29.1·31.1·34.7
├── extractor.py           # FR1·2·13~17
├── state_manager.py       # FR2·19.1
├── meta_collector.py      # FR3·12.2·15.2
├── subtitle_utils.py      # VTT→SRT→TXT·청킹·파일명
├── quality_checker.py     # FR4
├── kl_indexer.py          # FR6·15.3
├── kl_query.py            # FR9·12·15.4
├── kl_harness.py          # FR10 질의 하네스 (제품 내)
├── cookie_health.py       # FR19 (YDLLogger·상태 영속·get_status)
├── scheduler.py           # FR37 주기 자동 추출 — 판정(decide_cycle)·계획(build_plan)·상태 파일·틱 스레드
├── video_access.py        # 멤버십 판정 공유 규칙 (FR13.7·17.6, DQ-38) — 의존성 없는 잎 모듈
├── selfcheck.py           # FR38 정합 감사·건전성 점검 — audit 8검사 · doctor 10검사 (읽기 전용, chromadb 미임포트)
├── audit_waivers.yaml     # FR38.10 감사 예외 선언 (없으면 예외 0건)
├── dashboard/
│   ├── server.py          # FastAPI (FR11·17~20)
│   ├── jobs.py            # JobManager·classify_url·apply_filters·재생목록/검색/스케줄 워커 (FR17~18·24·34·37)
│   └── index.html         # 단일 파일 UI (질의·라이브러리·추출 3탭)
├── tests/                 # test_unit.py · test_integration.py · conftest.py
├── COOKIES_GUIDE.md       # 쿠키 추출 절차 (FR13 연계)
├── Dockerfile · requirements.txt
├── CLAUDE.md              # 개발 하네스 포인터 (§11)
├── .claude/agents|skills/ # 개발 하네스 (§11)
└── output/
```

---

## 8. 실행 방법

```bash
./yt.sh add https://youtube.com/@채널     # 등록 + 전체 추출
./yt.sh run [채널] [--limit N]            # 증분 업데이트 (카나리아)
./yt.sh review [--llm]                    # 품질 검토
./yt.sh reextract                         # SUSPECT 재추출
./yt.sh index                             # KL 인덱싱
./yt.sh ask 채널 "질문" [--multistep]      # RAG / 질의 하네스
./yt.sh search 채널 "검색어"               # 벡터 검색
./yt.sh summarize 채널 VIDEO_ID           # 전문 요약
./yt.sh serve                             # 대시보드 :8800
./yt.sh test [--integration]              # 검증
./yt.sh audit [--json] [--strict]         # 문서·코드 정합 감사 (FR38, output/ 불필요)
./yt.sh doctor [채널] [--json] [--strict] # 데이터 건전성 점검 (FR38, 읽기 전용 전수)
./yt.sh backfill-tickers [채널] [--apply] # 종목코드 재계산 (기본 dry-run, FR12.5)
```

사전 준비: `export ANTHROPIC_API_KEY=…`, (선택) `cookies.txt` 배치 — COOKIES_GUIDE.md.

> **대시보드 개발 시**: `dashboard/`를 라이브 마운트(`-v $PWD/dashboard:/app/dashboard`)하면
> index.html 수정이 재빌드 없이 반영된다. 파이썬 코드 수정은 `docker build` 필수 (이미지에 구워짐).

---

## 9. 검증 설계 (Verification Design)

### 9.1 단위 검증 (V-U — 네트워크 불필요)

> **정본은 코드다 (2026-09-20 번호 충돌·누락 전면 해소).** 아래 ID·대상은 `tests/test_unit.py`의
> 섹션 헤더 주석과 **1:1**로 맞춘 것이다. 목록에 없는 V-U 번호는 존재하지 않고, 테스트에 있는
> 검증은 번호가 없더라도 §9.1b에 전부 기록한다. **v5.6에서 V-U22~V-U27을 FR35·FR7.7~7.9용으로 선점**했다 —
> 구현 시 `tests/test_unit.py`에 같은 번호의 섹션 헤더 주석을 **같은 커밋에서** 넣어야 정본이 성립한다.
> **v5.8에서 V-U30~V-U31을 FR36용으로 선점**했다(문서 선행 — 구현 커밋이 같은 번호의 섹션 헤더를 넣는다). **v5.10에서 V-U33~V-U34를 FR37용으로 선점**했다(문서 선행 — 구현 커밋이 같은 번호의 섹션 헤더를 넣는다).
> **v5.11에서 V-U35~V-U36을 FR38용으로 선점**했다(같은 규약). 다음 신규 번호는 **V-U37**이다.
> **v5.11 이후 이 포인터는 손으로만 관리되지 않는다** — `audit.next-pointers`(FR38.11)가 이 문장의 값과 실제 최대값+1을 대조한다(DQ-54).

#### 9.1a 번호 부여 항목 (V-U1~V-U36)

| ID | 대상 | FR·DQ | 위치 |
|---|---|---|---|
| V-U1 | `make_basename`·특수문자 정규화 — 파일명 형식·srt/txt 동일성 | FR1.4~1.5 | `tests/test_unit.py` §V-U1 |
| V-U2 | `vtt_to_srt` 변환 정확성 · 자동자막 dedup 2패턴(누적형·슬라이딩형 F-7) · `srt_to_txt` | FR1.3 | `tests/test_unit.py` §V-U2 |
| V-U3 | `subtitle_priority` 수동 우선 폴백 | FR1.2 | **테스트 미구현** (v1 설계 항목 — 검증 의도만 보존, 구현 시 이 번호 사용) |
| V-U4 | `StateManager.decide` 수정 감지(skip/updated/new) + FR2.6 날짜 미제공 무변경 | FR2.2·FR2.6 | `tests/test_unit.py` §V-U4 |
| V-U5 | `quality_rules` SUSPECT 판정(정상·과소·반복)·한글 비율 | FR4.2 | `tests/test_unit.py` §V-U5 |
| V-U6 | `chunk_by_srt` 120초 윈도우·`start_seconds` · `chunk_text` | FR6.2 | `tests/test_unit.py` §V-U6 |
| V-U7 | `extract_handle`(URL→채널명·URL 인코딩)·`normalize_url` | FR7.6 | `tests/test_unit.py` §V-U7 |
| V-U8 | scan 병합·live 필터 — videos+streams 병합, is_live/is_upcoming 제외, 중복 시 video 우선, streams 탭 없는 채널 graceful | FR16 | `.claude/skills/pipeline-verify/scripts/mock_scan_test.py` ①② (pytest 아님) |
| V-U9 | playlists 매핑·백필 — 복수 소속 보존, 탭 없음 → 빈 매핑, 백필 신규 필드 추가·갱신·무변경 스킵 | FR15 | `mock_scan_test.py` ③④⑤ (pytest 아님) |
| V-U10 | members_only 재시도 — 쿠키 유/무별 `decide` 판정 | FR19.1·DQ-10 | `tests/test_unit.py` §V-U10 |
| V-U11 | `classify_url` 영상/채널/판별불가 8케이스 | FR17.1 | `tests/test_unit.py` §V-U11 |
| V-U11b | `classify_url` 재생목록 — `/playlist?list=` 인식 · `watch?v=…&list=` 는 영상 우선 | FR24.1 | `tests/test_unit.py` §V-U11b |
| V-U12 | 채널 폴더 — `set_group` 지정·yaml 영속, 빈 값 → 필드 제거(해제), 미등록 채널 KeyError | FR25.1 | `tests/test_unit.py` §V-U12 |
| V-U13 | 챕터 정규화 — `_normalize_chapters` 초 단위 내림·제목 trim·`None` 보정·비-dict 항목 제거·`None` 입력 `[]` | FR27.1 | `tests/test_unit.py` §V-U13 |
| V-U14 | Whisper SRT 조립 — `_srt_ts` 밀리초 포맷 · `segments_to_srt` 빈 텍스트 세그먼트 제외 | FR30.2 | `tests/test_unit.py` §V-U14 |
| V-U15 | RSS 피드 파싱 — `fetch_feed` videoId/title/published 추출 · `resolve_channel_id`(`/channel/UC…`는 무요청, `@핸들`은 HTML 해석) | FR29.2 | `tests/test_unit.py` §V-U15 |
| V-U16 | 이름 변경 — registry rename(설정 보존·미존재 KeyError·중복 ValueError), 채널 폴더 이동, 영상 제목·카테고리 일괄(메타·playlists.json·ChromaDB 메타 동기화) | FR31 | `tests/test_unit.py` §V-U16 |
| V-U17 | transcribe progress — 퍼센트 계산(클램프·duration 0·비정상 입력), 진행률+SRT 동시 산출, 지연 생성자 1회 소비 | FR30.6 | `tests/test_unit.py` §V-U17 |
| V-U18 | 검색 스캔 조립·필터 — `classify_url` search 분기(results URL 인식 / 순수 텍스트는 ValueError), `_build_search_url`(quote_plus·sp 프리셋·미지 period→all), `_search_opts`(`playlist_items="1-N"`), duration 필터(임계 미만 제외 / 이상 통과 / 결측 통과) | FR34.1~34.4 | `tests/test_unit.py` §V-U18 |
| V-U19 | auto_run 플래그 — `set_auto_run(False)` 기록·(True) 필드 제거·필드 부재는 True 간주, `names()` 기본 전체 반환(회귀)·`names(auto_only=True)`만 제외, `main.bulk_targets`(cmd_run·cmd_transcribe 공용) 인자 없으면 제외/채널 명시하면 포함, rename이 auto_run 보존 | FR34.7 | `tests/test_unit.py` §V-U19 |
| V-U20 | 길이 노출·포맷 — `list_videos`가 meta의 `duration`/`duration_string` 통과, 키 없는 meta는 `None`/`""`(0 아님, 백필 없음 DQ-29), `fmtDuration` 초→m:ss·h:mm:ss·null→빈칸 | FR20.5~20.6·DQ-29 | `tests/test_unit.py` §V-U20 (node 미설치 시 `fmtDuration` 실행 테스트 1건 skip) |
| V-U21 | 배치 휴식 크로스 그룹 — `BatchRest` 휴식마다 시간·배치 크기 재추첨(범위 내·고정 아님)·휴식 후 카운터 리셋 / `cancel_check` 없으면 단일 sleep(CLI 동일), 있으면 1초 틱으로 쪼개져 취소 시 중단 / **rest_state 공유 시 채널 9개×1영상 `run()` 9회에서 휴식 2회 발생, 미공유 대조군은 0회(결함 재현)** / `rest_state=None`이면 CLI 동작 불변 | FR14.2·DQ-30 | `tests/test_unit.py` §V-U21 |
| V-U22 | `config.channel_dir` 그룹 해석 — 그룹 있음(`output/<G>/<C>`)·없음(`output/<C>`)·미등록 채널·yaml 부재(빈 맵 폴백)·**yaml 변경 후 즉시 반영**(mtime 캐시 무효화)·캐시 히트 시 yaml 재파싱 없음 | FR35.1~35.3·DQ-32 | `tests/test_unit.py` §V-U22 (구현됨 — `test_channel_dir_resolves_group`·`test_group_cache_follows_yaml_mtime`·`test_group_cache_hit_does_not_reparse`·`test_channel_dir_bad_group_falls_back_flat`) |
| V-U23 | `validate_path_segment` 거부표 — `..`·`.`·`a/b`·역슬래시·절대경로·제어문자·선행 `.`·후행 `.`/공백·65자·`CON`/`com1`·빈 값 → `ValueError`, 정상값은 NFC 정규화 후 반환 | FR35.4·FR7.9·DQ-33 | `tests/test_unit.py` §V-U23 (구현됨 — `test_validate_path_segment_rejects`(24케이스)·`test_validate_path_segment_accepts_and_normalizes`·`test_rejected_group_creates_nothing_on_disk`) |
| V-U24 | `folder_ops.check_namespace` — 그룹명 ∩ 미지정 채널명 충돌·`is_channel_like_dir`(state.json/srt/meta) 판정·그룹 해제 시 최상위 충돌·`output/G/G`(동명 채널) 허용 | FR35.6·DQ-34 | `tests/test_unit.py` §V-U24 (구현됨 — `test_check_namespace_four_directions`·`test_check_namespace_casefold_and_nfc`·`test_group_same_name_as_member_channel_allowed`) |
| V-U25 | `folder_ops.move_channel_dir`·`set_channel_group` — 목적지 존재 시 거부(원본 무변경)·`st_dev` 불일치 시 거부·yaml 기록 실패 주입 시 **역방향 rename 보상 롤백으로 원상 복구**·빈 원본 부모만 `rmdir`·미추출 채널 no-op | FR35.7~35.8·DQ-35 | `tests/test_unit.py` §V-U25 (구현됨 — `test_move_channel_dir_*` 3건·`test_set_channel_group_moves_and_releases`·`test_set_channel_group_compensating_rollback`·`test_set_channel_group_conflict_and_skip`·`test_rename_group_single_rename`·`test_rename_channel_stays_inside_group`·`test_rename_channel_rejects_group_name`) |
| V-U26 | `ChannelRegistry.add` upsert — 기존 채널 재등록 시 `group`·`auto_run`·`channel_id`·`added_at` 보존 + `url`·`lang` 갱신, 개명 채널(등록명≠핸들) URL로 add해도 **두 번째 항목이 생기지 않음**(`resolve_name`), 신규는 전체 필드 생성, 부적합 이름 `ValueError` | FR7.7~7.9·DQ-37 | `tests/test_unit.py` §V-U26 (구현됨 — `test_add_upsert_preserves_settings`·`test_add_uses_resolve_name_for_renamed_channel`·`test_add_and_rename_reject_bad_names`·`test_add_new_channel_creates_all_fields`) |
| V-U27 | 마이그레이션 — `plan_migration`이 dry-run 계획과 실제 이동 목록이 일치, 미등록 잔존 폴더·`.cookie_status.json` 미포함, 재실행 멱등, 중간 실패 주입 시 **저널 역순 전량 원복**, 락 stale 판정(6h) | FR35.11~35.12·DQ-36 | `tests/test_unit.py` §V-U27 (구현됨 — `test_plan_migration_is_read_only`·`test_apply_migration_and_idempotent`·`test_apply_migration_rolls_back_on_failure`·`test_rollback_migration_restores_flat`·`test_migration_blocked_by_lock_and_journal`·`test_migration_lock_stale_after_6h`·`test_migration_precheck_rejects_namespace_conflict`·`test_job_is_busy_ors_migration_lock`(mock_jobs_test §12 병행)) |
| V-U28 | **FR35 QA 결함 회귀**(`_workspace/28_fr35_qa.md`) — 완료된 마이그레이션(`.done.json`)의 `--rollback` 복원(F-1)·되돌릴 저널 없음 보고·pending 우선 순위·**락 상태에서도 takeover 롤백**(F-2)·그룹 지정 채널 `purge` 실삭제(F-3)·`/channels/rename` 검증 400(F-4)·마이그레이션 락이 `_acquire()`(스캔·추출)를 차단(F-5)·예약어 채널명 1건이 스캔 전체를 죽이지 않고 해당 채널만 제외(F-6) | FR35.4·35.8·35.10·35.12·DQ-36 | `tests/test_unit.py` §V-U28 (구현됨 — `test_rollback_after_completed_migration`·`test_rollback_reports_nothing_to_restore`·`test_rollback_prefers_pending_journal_over_done`·`test_rollback_under_live_lock_requires_takeover`·`test_acquire_blocked_by_migration_lock`·`test_group_flat_entries_skips_unusable_channel_name`·`test_delete_channel_purges_grouped_dir`·`test_rename_channel_validation_is_400`·`test_rollback_aborts_when_restore_target_occupied`, mock_jobs_test §12 병행) |
| V-U29 | **멤버십 감지 언어 비의존**(`_workspace/30`) — `lang=ko` 실측 한국어 문구·영어 문구 **둘 다** 판정 / `회원` 단독·403·429·비공개 등 오탐 0 / `availability`(`subscriber_only`·`needs_auth`·`premium_only`) 1차 신호가 메시지와 무관하게 동작 / 추출 루프에서 ⓐ availability 있음 ⓑ 메시지 폴백 모두 `members_only` + `state.sub_type=members_only` 기록(FR19.1 재시도 대상) / **멤버십 영상의 429는 `error`로 남고 state 미기록**(영구 스킵 방지) / `jobs`와 `extractor`가 같은 상수·같은 함수 사용 | FR13.7·FR17.6·FR19.1·DQ-38 | `tests/test_unit.py` §V-U29 (`test_members_message_ko_and_en`·`test_members_availability_is_language_independent`·`test_members_rule_is_shared_by_jobs_and_extractor`·`test_run_classifies_members_by_availability_and_ko_message`·`test_429_takes_precedence_over_members_availability`), mock_scan_test §21 병행 |
| V-U30 | **채널 메모 계약**(FR36) — `set_note` 트림·제어문자(개행·탭 포함)→공백 치환 / **200자 통과·201자 `ValueError`**(절삭 아님) / 빈 값 제출 시 **필드가 제거되지 않고 `note: ""`** 로 남음 / 미등록 채널 `KeyError` / 반환값 = 저장된 정규화 값 / **회귀:** `add()` 재등록이 기존 `note`를 보존(FR7.7)하고 `rename()`이 `note`를 옮긴다 / **API 계약:** `POST /channels/note` 응답 shape(`{ok, channel, note}` 정규화 값)·404 미등록·400 201자·400 경로 문자·**작업 중 409**(DQ-42)와 `/channels/stats.note` 노출 | FR36.1~36.4·36.11·DQ-39·DQ-42 | `tests/test_unit.py` §V-U30 (구현됨 — `test_set_note_normalizes_control_chars_and_trims`·`test_set_note_length_limit_rejects_not_truncates`·`test_set_note_empty_keeps_field_as_empty_string`·`test_set_note_unknown_channel`·`test_note_survives_add_upsert_and_rename`·`test_channels_note_api_contract`) |
| V-U31 | **스캔 캐시 무효화**(FR36.8) — 채널 스캔(`entry["channel"]`)·재생목록/검색 스캔(`entry["by_channel"]` 키) 양쪽에서 대상 채널 항목만 삭제되고 **다른 채널 캐시는 남는다**(정확 일치 — 부분 문자열로 남의 캐시를 지우지 않는다) / 삭제된 `scan_id`로 `POST /extract` 시 **기존 400**("만료") / rename이 **400·409로 실패하면 캐시 무변경**(성공 후에만 무효화) / **채널 삭제도 무효화**(옛 `scan_id`로 되살아나지 않는다) / `channel=None`이면 전체 비움 / **결함 재현 대조군:** 무효화 없이 옛 이름 캐시로 `_run_channel`을 돌리면 `config.channel_dir(옛이름)`(=레지스트리에 없는 평면 경로)에 쓰려 한다 | FR36.8·DQ-41 | `tests/test_unit.py` §V-U31 (구현됨 — `test_invalidate_scans_targets_only_referencing_entries`·`test_rename_invalidates_scan_and_extract_is_400`·`test_rename_failure_keeps_scan_cache`·`test_delete_channel_invalidates_scan_cache`·`test_stale_scan_cache_targets_ghost_dir_without_invalidation`), mock_jobs_test §13 병행 |

| V-U32 | **종목코드 문맥 판정**(FR12.2·12.5~12.7) — 실측 오탐 고정: 제목 날짜 `[주식] 260819 …` 3종·계좌번호 `우리은행 /1002 763 241686 /`·사업자/전화번호·URL 숫자 조각·근거 없는 맨 6자리·`쿠폰코드 123456`·약한 근거+날짜(`인증코드: 260819`·`(260819)`) **전부 `[]`** / 채택: 강한 라벨(`종목코드:`·`단축코드`·`티커`)·거래소 표기(`KRX:`·`.KS`)·괄호 단독·일반 `코드:`·나열(`005930, 000660`)·`$AAPL` / **강한 라벨이면 날짜형 코드(`010130`)도 채택** / 백필은 dry-run에서 **파일 무변경**, `--apply`에서만 기록하고 재실행 시 변경 0(멱등) | FR12.2·12.5~12.7·DQ-43 | `tests/test_unit.py` §V-U32 (`test_extract_tickers_rejects_noise`(13케이스)·`test_extract_tickers_accepts_with_context`(10케이스)·`test_backfill_tickers_dry_run_then_apply`) |
| V-U33 | **스케줄 판정 상태 기계**(FR37) — `decide_cycle`을 **가짜 시계**로 구동: ⓐ `enabled=false`면 항상 `idle`(기본 설치 상태에서 네트워크 0) ⓑ 기동 후 5분 미만이면 `idle` ⓒ 도래 전/도래 후 경계(`last_run_at + interval_days`) ⓓ **8일·30일·90일 잠든 뒤 깨어나도 실행은 1회**(밀린 만큼 반복 없음)이고 `last_run_at`은 `now`로 갱신(cron식 `+=` 누적 아님) ⓔ `skip_cycles>0`이면 실행 대신 1 감소 + `last_run_at` 갱신 ⓕ 쿠키 `warning`이면 `idle` + `paused_reason="cookie"` + **`last_run_at` 미갱신**이고 warning이 내려가면 다음 틱에 `run`(자가 치유) ⓖ `busy`면 `idle` + 미갱신 → 다음 틱 `run` ⓗ **판정 순서**(백오프 감소가 쿠키·busy보다 먼저) ⓘ `update()` 검증 — `interval_days` **3(기본)**/7/14/28 통과·1·5·30 `ValueError`, **기본 설정 파일의 `interval_days`가 3**, 예산 1~200 경계, **켤 때마다 `last_run_at`이 `now`로 채워짐**(토글 즉시 대량 추출 방지 — 껐다가 한참 뒤 다시 켜는 경우 포함) ⓙ `request_now()`가 도래시키되 `skip_cycles`를 소모하지 않음 ⓚ 상태 파일 — 원자 교체·손상 JSON·부재·미지 키에서 기본값 폴백 ⓛ **주기 도중 `POST /schedule {enabled:false}` → 주기 마감 후에도 `false`**(주기 길이만큼의 창 동안 비상 정지가 무효화되던 결함의 회귀 시험 — 마감은 `USER_FIELDS`를 쓰지 않는다, NFR3 ⓓ) ⓜ 백오프 skip이 **`last_result`를 덮지 않고**(직전 `aborted_429` 증거 보존) `last_skip_at`만 남김 ⓝ `_sanitize` — `enabled`는 **JSON 불리언만**(`"yes"`는 꺼짐), 깨진 `last_run_at`은 `now`로 교정("즉시 도래" 금지) ⓞ 작업 대기 중 예외가 나도 **주기를 마감**한다(`last_run_at` 갱신 → 같은 주기 재실행 없음, `outcome="error"`) | FR37.2~37.3·37.10~37.15·DQ-45·DQ-49·DQ-50 | `tests/test_unit.py` §V-U33 (구현됨 — `test_schedule_*` 17케이스) |
| V-U34 | **RSS 선행 계획·429 중단 전파**(FR37) — ⓐ `check_new_videos(names=)`가 **준 채널만** 조회(`auto_run:false` 채널에 요청 0)하고 인자 없이 부르면 **기존 전 채널 동작 그대로**(FR29.2 회귀) ⓑ 새 영상 0이면 `build_plan`이 빈 계획 → **job 생성 0** ⓒ RSS 실패 채널은 계획에서 빠지고 `rss_errors`에 기록 ⓓ 채널 새 영상이 15건이면 `truncated_channels`에 오르고 **전체 스캔으로 승격하지 않음**(DQ-47) ⓔ 예산 절단 — 합계가 `max_videos_per_cycle`을 넘지 않고, **커서 회전으로 다음 주기에 뒷 채널이 먼저 잡힘**(기아 방지) ⓕ 계획 → `by_channel`/`videos_view` 모양이 `_run_grouped` 기대와 일치하고 `published`가 `upload_date`로 새지 않음 ⓖ **`stats["aborted_429"]`**: `run()`이 연속 429 중단 시 표식을 싣고 `_STAT_KEYS` 합산·job `stats` 등식(V-D11)을 오염시키지 않음 ⓗ `_run_grouped`가 표식을 보면 **남은 채널의 `run()`을 호출하지 않고**(대조군: 표식 없으면 전 채널 호출 = 현행 결함 재현) `status="done"` + 경고 1건 ⓘ 주기 결과가 `aborted_429`면 `skip_cycles` 1→2→4(상한 4), 정상 종료면 0으로 리셋 | FR37.4~37.9·DQ-47·DQ-48 | `tests/test_unit.py` §V-U34 + `mock_jobs_test.py` ⑭ (구현됨) |
| V-U35 | **`audit` 검사 계약** — 합성 문서 픽스처(tmp 디렉터리에 REQUIREMENTS/DESIGN/테스트/SKILL 축소판을 쓴다)로: ⓐ pytest 기준선이 5곳 중 1곳만 낡으면 **오류 1건·종료코드 2**, 전부 같으면 0건·종료코드 0, `--baseline` 실측값과 다르면 오류 ⓑ `CLAUDE.md` 이력의 **과거 값(31→33 등)은 오탐이 되지 않는다**(마지막 행 오른쪽 값만 본다) ⓒ 유령 V-U(문서에만)·누락 V-U(테스트에만) 각각 오류이고 `**테스트 미구현**` 표기는 면제, **위치 열이 `mock_scan_test.py`인 항목은 마커를 요구하지 않는다**(V-U8·V-U9 현행 상태가 오탐이 되지 않는다) ⓓ "다음 신규 번호" 포인터가 최대값+1과 다르면 오류(V-U·V-I·V-D·DQ 4계열) ⓔ FR 중복 = 오류·결번 = 경고이고 **§6 트레이서빌리티 행이 FR 정의로 세어지지 않는다**(실측 허위 15건 회귀) ⓕ DQ 중복 판정에서 **REQUIREMENTS §8 역사 표가 제외**된다(실측 허위 6건 회귀) ⓖ §6 토큰 분류 — 없는 파일·없는 식별자·없는 라우트는 오류, **CSS 선택자·필드명·`""`·런타임 산출물 경로는 침묵**(실측 140건 잡음 회귀) ⓗ `(구현 예정)` 표기 행은 CLI·트레이서빌리티 부재를 면제 ⓘ waiver 정확 일치로 면제되면 종료코드 0 + `waived` 집계에 남고, **와일드카드 waiver는 무효**(면제되지 않는다) ⓙ 대응 발견 없는 waiver = `stale waiver` 경고 ⓚ 문서 파일이 없거나 파싱 불가면 **종료코드 3**(0이 아니다) ⓛ `--json` 스키마(`check`·`target`·`severity`·`summary.exit_code`) 고정 ⓜ **실행 전후 픽스처 전체 바이트·mtime 불변**(FR38.3) | FR38.1~38.12·DQ-52~54 | `tests/test_unit.py` §V-U35 (구현됨 — `test_audit_clean_fixture_is_silent`·`test_audit_is_read_only`·`test_audit_pytest_baseline_catches_one_stale_place`·`test_audit_pytest_baseline_vs_measured`·`test_audit_pytest_baseline_ignores_claude_history`·`test_audit_vu_numbers_ghost_and_missing`·`test_audit_vu_numbers_exemptions`·`test_audit_next_pointers_four_families`·`test_audit_fr_numbers_scope_and_duplicates`·`test_audit_dq_numbers_excludes_requirements_history`·`test_audit_traceability_token_classification`·`test_audit_traceability_fr_coverage_warns`·`test_audit_planned_marker_exempts_absence`·`test_audit_cli_commands_both_directions`·`test_audit_doc_version_header_sync`·`test_audit_waivers_exact_match_and_wildcard`·`test_audit_waiver_stale_and_expired`·`test_audit_waiver_file_parses_without_pyyaml`·`test_audit_missing_document_is_exit_3`·`test_audit_json_schema_and_strict`) |
| V-U36 | **`doctor` 검사 계약** — 합성 `output/` 픽스처(채널 3~4개·그룹 1개·state·txt·meta·extract_log·가짜 chroma.sqlite3)로: ⓐ 레코드 있고 txt 없음 / txt 있고 레코드 없음 **양방향 오류** ⓑ 같은 basename을 두 video_id가 공유하면 오류 ⓒ 미등록 채널형 디렉터리 경고(`folder_ops.is_channel_like_dir` 판정 재사용)·등록됐지만 미추출은 정보 ⓓ 그룹 지정 채널이 평면 위치에 남아 있으면 오류 ⓔ 자막 보유 영상이 chroma에 없으면 경고·인덱스에만 있으면 경고 ⓕ **`chroma.sqlite3`가 없거나 `embedding_metadata`가 없으면 "건너뜀(정보)"이고 디렉터리·파일을 만들지 않는다**(FR38.3·DQ-55 회귀 — 클라이언트 경로의 `mkdir` 부작용 금지) ⓖ `extract_log.csv` 완전 동일 행은 **정보**(append-only 시도 기록의 정상 귀결 — 경고로 두면 해소 수단이 없어 영원히 남는다, DQ-53)·BOM 헤더를 정상 파싱(선두 BOM 침묵)·**열 수 불일치·헤더 계약 위반은 오류**·중간 BOM은 경고(강등이 구조 손상까지 묻지 않는지 양성·음성 쌍으로 고정) ⓗ `error:` 사유 재판정으로 **한국어 멤버십 문구(실측 문구 고정)·영어 문구 양쪽**이 오류로 잡히고 `error:429`는 정보, 멤버십과 무관한 오류 사유는 **침묵** ⓘ `meta-fields` — "값이 있는데 distinct 1"이 경고, 열거형 필드(`content_type`·`sub_type`)는 면제, `upload_date == "00000000"`은 정보(FR2.6)·다른 비8자리 값은 오류, `tickers` 전량 빈 값은 **waiver로 침묵** ⓙ 스케줄러 — 가짜 시계로 `enabled=true`·오래된 `last_run_at` → 적체 경고, `skip_cycles>0` → 경고, `interval_days=5` → 오류, 기본 상태(`enabled:false`)에서는 0건 ⓚ 쿠키 무효 경고 N일 방치 경고(가짜 시계) ⓛ `output/` 부재 시 **종료코드 3** ⓜ 채널 인자로 범위 한정 ⓝ **실행 전후 픽스처 전체 바이트·mtime 불변** ⓞ **화석 waiver는 등재된 `채널/video_id` 1행만 면제한다** — 같은 채널의 새 화석·같은 video_id의 다른 채널 화석·영어 문구 화석은 **여전히 오류**(와일드카드 waiver는 무효로 보고되고 아무것도 면제하지 않는다) ⓟ 채널 한정 실행은 다른 채널의 waiver를 `waiver.stale`로 올리지 않고(부분 범위는 예외의 수명을 판정할 근거가 없다), 전수 실행에서 화석이 사라지면 **stale 경고가 올라온다**(예외의 자기 신고 = FR38.10ⓒ 유지) | FR38.7·38.10·38.13~38.15·DQ-55~56 | `tests/test_unit.py` §V-U36 (구현됨 — `test_doctor_clean_fixture_is_silent`·`test_doctor_is_read_only`·`test_doctor_state_files_both_directions`·`test_doctor_basename_collision`·`test_doctor_orphan_dirs_and_unextracted`·`test_doctor_registry_paths_flat_leftover_is_error`·`test_doctor_index_coverage_missing_and_orphan`·`test_doctor_index_coverage_schema_mismatch_is_skip`·`test_doctor_extract_log_hygiene`·`test_doctor_extract_log_mid_file_bom_is_warn`·`test_doctor_detector_fossils_reappraises_reasons`·`test_doctor_meta_fields_generalized_signals`·`test_doctor_meta_fields_format_contract`·`test_doctor_scheduler_states`·`test_doctor_cookie_status_uses_get_status`·`test_doctor_channel_scope_and_missing_output`·`test_doctor_registry_and_state_parse_failure_is_exit_3`·`test_doctor_channel_scope_skips_corpus_signal`·`test_doctor_extract_log_duplicate_downgrade_keeps_real_anomalies`·`test_doctor_fossil_waiver_exempts_only_the_declared_row`·`test_real_waiver_file_pins_the_known_fossil_narrowly`·`test_doctor_channel_scope_does_not_stale_other_channel_waiver`) |

기준선: 2026-09-26 기준 `./yt.sh test` = **227 passed / 1 skipped** (FR38 정합 감사·건전성 점검 V-U35·V-U36 + FR37 주기 자동 추출 V-U33·V-U34 +
FR37 QA 결함 수정분 포함. skip 1건은 컨테이너 이미지에 node가 없는 `fmtDuration` node 실행 테스트 —
호스트 node 22에서 통과 확인).


#### 9.1b 번호 미부여 검증 (테스트는 있으나 V-U 번호 없음 — FR 헤더로 식별)

테스트 파일이 V-U 번호 대신 FR 번호로만 표시한 항목들이다. 코드가 정본이므로 **문서가 번호를 새로
만들지 않는다** — 필요해지면 그때 코드 주석과 함께 V-U22 이후를 부여한다.

| 테스트 헤더 (`tests/test_unit.py`) | 대상 | 비고 |
|---|---|---|
| `FR23` | `reflow_sentences` 문장 단위 개행 — 한·영/무공백 한글/소수점 보존 3케이스 | **구 문서의 "V-U12 reflow_sentences"** (번호 폐기, 검증은 실재) |
| `FR16.5` | 진행 중 라이브 가드 — is_live/is_upcoming → `live_wait` + state 미기록, was_live 정상 경로 | **구 문서의 "V-U13 live guard"** (번호 폐기, 검증은 실재) |
| `FR13.6` | Firefox 프로필 있으면 `cookiesfrombrowser` 우선, 없으면 `cookiefile` 폴백 | |
| `FR32.1` | 제목 언어 고정 — 채널 `lang`·기본 언어·`self` 없는 호출 경로·공유 상수 비오염 | DQ-20 |
| `FR32.2` | URL → 등록 채널명 역조회 `resolve_name` | DQ-19 |
| `FR33.1~33.2` | 증분 인덱싱 판정 — `_unchanged`가 기존 청크 id·본문·메타 대조 | DQ-21 |

mock 스크립트(`mock_scan_test.py` ⑥~⑰ · `mock_jobs_test.py` ①~⑪)에도 번호 없는 검증이 다수 있다 —
CLI 동작 불변(FR18.1)·우아한 취소(FR18.2)·스캔 캐시 재사용(DQ-13)·date_skip 등식(V-D11 전제)·
`--limit` 요청 예산(F-2)·쿠키 건강(FR19.2~19.3)·`mark_invalid`(F-3)·영상별 이벤트(FR26.1)·
JobManager 동시성/취소(FR17.7~17.8)·필터 차분 대조(V-D11 전제)·재생목록 워커(FR24)·
검색 워커(FR34.6~34.10)·배치 휴식 크로스 그룹(FR14.2). 번호가 붙은 것은 V-U8·V-U9뿐이다.

### 9.2 통합 테스트 (tests/test_integration.py — 네트워크 필요)

V-I1 등록+폴더 · V-I2 2회차 SKIP · V-I3 수정 감지 · V-I4 SUSPECT 기록 ·
V-I5 재추출 갱신 · V-I6 2컬렉션 생성 · V-I7 채널 격리 · V-I8 재시작 영속

### 9.3 대시보드·기능 검증 (V-D — FR15~20·24~26·34~38)

| ID | 절차 | 합격 기준 | 현황 (2026-08-04) |
|---|---|---|---|
| V-D1 | `./yt.sh test` | 단위 전부 통과 | ✅ 통과 (mock 14 + 로직 17) |
| V-D2 | CLI 회귀 `run --limit 1` | 기존 로그·동작 동일 (progress=None 경로) | ✅ 통과 (`--limit 3` 카나리아, 429 0회·오류 0) |
| V-D3 | serve 후 `/cookies` | present·mtime·경고 pill 표시 | ✅ 통과 (경고 감지 → `warning:true` 확인) |
| V-D4 | 미등록 채널의 단일 영상 URL 추출 | 채널 자동 등록 + txt 생성 + job done | ⏳ 미검증 (네트워크 예산 — mock 로직 검증으로 대체) |
| V-D5 | 채널 전체 run → 중간 취소 | cancelled, state 저장, 재시작 시 이어받기 | ✅ 통과 — 멤버십 재시도 실작업(14/56 시점) 취소: 현재 영상 완료 후 중단, status=cancelled, state 바이트 불변, 인덱싱 미실행 (2026-08-05) |
| V-D6 | run 중 두 번째 POST /extract | 409 + 현재 job | ✅ 통과 — 실작업 중 두 번째 요청 실HTTP 409 확인 (2026-08-05) |
| V-D7 | 만료 쿠키로 run → pill | 경고 표시, 갱신 후 자동 해제 | ✅ 통과 — 쿠키 갱신(2026-08-08) 후 `/cookies` warning 자동 해제·마스킹 확인. 유효 쿠키 run에서 🍪 경고 0건, FR19.1 재시도 5건이 멤버십 미가입 계정이라 members_only 재수렴(정상), `--limit 5` 정확히 5요청에서 종료(F-2 실증) |
| V-D8 | 유효 쿠키 run | members_only 재시도, 비멤버면 재수렴 | ✅ 통과 (7건 `updated` 재시도 → members_only 재수렴, state 바이트 동일 = 멱등) |
| V-D9 | 라이브러리 탭 | 통계 일치·제목 필터·뱃지·전문 보기·벡터 검색 | ✅ 통과 — 실브라우저에서 통계·즉시 필터·뱃지·자막 전문(작은따옴표 제목 포함) 확인 (2026-08-04) |
| V-D10 | 라이브 영상 재추출 | content_type="live" 유지 | ⏳ 미검증 (대상 채널에 streams 탭 없음) |
| V-D11 | 조건 추출(ⓐ~ⓔ 조합 3종) | 미리보기 대상 수 = 실제 처리 수(기간 조건은 date_skip 합산 일치) | ✅ 통과 (프론트/백엔드 필터 랜덤 3000회 차분 불일치 0, 카나리아 등식 56 = 1+48+7) |
| V-D12 | 영상 삭제 (`POST /videos/delete`) | srt·txt·meta·desc 제거, state.json 항목 제거, ChromaDB 두 컬렉션에서 청크 제거, 라이브러리 목록에서 즉시 사라짐, 진행 중 작업 있으면 409 | ✅ 통과 — 합성 테스트 채널(`_zztest_delfeature`, 실채널과 완전 분리)로 5항목(파일·state·playlists.json·`/videos`·ChromaDB) 전부 제거 확인 + 경로탈출 400 + 미존재 404 (2026-08-08) |
| V-D13 | 채널 삭제 (`POST /channels/delete`) | `purge=false`: 등록 해제만, `output/` 보존, 라이브러리에서 즉시 사라짐. `purge=true`: `output/{채널}/` 완전 삭제 | ✅ 통과 — 합성 테스트 채널로 `purge=false`(등록 해제+파일 보존+목록에서 사라짐) → `purge=true`(폴더 완전 삭제) 순차 검증. 실채널(두두감자·toyoungin·한균수의주식사용설명) 데이터는 검증 내내 무변경 확인 (2026-08-08) |
| V-D14 | 검색 추출 e2e (FR34) | 검색어 + ⓐlimit 10 + ⓑ180초 미만 제외 + ⓒ이번달 프리셋으로 스캔 → 미리보기 대상 수 = 실제 처리 수(`live_wait`·`date_skip` 포함 등식) → 각 영상이 **원채널 폴더**에 저장 → 신규 등록 채널이 전부 지정 폴더 하나로 묶임 → 신규 채널만 `auto_run: false`(기존 등록 채널의 `group`·`auto_run` 불변) → `meta.playlists`에 검색어가 들어가지 않음(FR34.10) | ✅ 통과 — 격리 컨테이너 e2e 실추출: 검색어 "클로드 코드 사용법" · limit 3(기준의 10 대신 비용 절감 값) · 180초 미만 제외 · month 프리셋 · 폴더 "QA검색묶음". 미리보기 2 = 처리 2(`total 2·done 2·new 2`) · 원채널 폴더 2개 생성·둘 다 동일 `group` · 신규만 `auto_run:false`(기존 35채널 0변경) · `meta.playlists == []` · 재생목록 스캔 0회(`pl_map={}` 실증, DQ-28) · 429·오류 0 (2026-09-20) |
| V-D15 | `auto_run` run·transcribe 제외 (FR34.7) | 검색 유입 채널이 있는 상태에서 `./yt.sh run`·`./yt.sh transcribe`(둘 다 인자 없음) 로그에 해당 채널이 나타나지 않고 yt-dlp 요청도 발생하지 않음. `./yt.sh run <검색유입채널>`·`./yt.sh transcribe <검색유입채널>`은 정상 실행. `/channels/stats.auto_run` 노출·토글 후 즉시 반영 | ✅ 통과 — 실제 `cmd_run`·`cmd_transcribe`를 스텁으로 호출해 대상 집합 비교: 양쪽 동일하게 검색 유입 채널 제외(35→34), yt-dlp 요청 미발생. 명시 지정 시는 정상 실행. `/channels/stats.auto_run` 노출·토글 즉시 반영 확인 (2026-09-20) |
| V-D16 | 영상 길이 노출·경계면 정합 (FR20.5~20.6) | `GET /videos` 응답에 `duration`·`duration_string`이 실제로 실리고 **프론트가 파싱하는 필드명과 정확히 일치** · 라이브러리 목록·폴더 전체 보기·내용 검색 결과 3곳 모두 길이 표시 · `duration` 키가 없는 과거 meta는 **빈칸**(0:00이 아님) · 검색 조건 미리보기 행에도 길이 표시(같은 포맷터) · **백필이 일어나지 않았음**(meta 파일 mtime 불변) | ✅ 통과 — 7개 경계면 전건 실호출 대조(`GET /videos` → `duration: 3131` / `duration_string: "52:11"`), index.html의 `fmtDuration`을 node로 그대로 추출 실행해 12케이스 검증. 결측 meta 사본 실측 → `null`/`""` → 프론트 빈칸(0:00 아님). `list_videos` 읽기 전용·meta mtime 전후 동일로 백필 부재 확인(DQ-29) (2026-09-20) |
| V-D17 | 마이그레이션 e2e (FR35.11~35.12) — `output/` **사본**(실데이터 아님)에서 `./yt.sh migrate-groups`(dry-run) → `--apply` → `--rollback` | dry-run 계획 건수 = 실제 이동 건수(그룹 지정 59) · 이동 후 `output/<G>/<C>/` 아래 srt·txt·meta·desc·chroma·state.json **파일 수와 바이트 합이 이동 전과 동일** · 미지정 11채널과 `.cookie_status.json`은 최상위 그대로 · `channels.yaml` **바이트 불변** · 재실행 시 "이동 0(멱등)" · `--rollback` 후 **원래 평면 구조와 완전 일치** · 중간 실패 주입 시 이동 0 또는 전량 원복 | ⏳ 미검증 (FR35 구현 시) |
| V-D18 | 폴더 이동 e2e (FR35.6~35.9) — 합성 테스트 채널로 그룹 지정 → 변경 → 해제 → 폴더 이름 변경 | 각 단계마다 디스크 경로가 실제로 이동하고 자막·ChromaDB가 따라감(`/videos`·`POST /search` 정상) · 기존 채널명과 같은 그룹명 시도 **409** · `../`·절대경로·제어문자 그룹명 **400이며 `output/` 밖·안 모두에 디렉터리 생성 0** · 추출 작업 중 시도 **409** · 마이그레이션 락 존재 시 **409** | ⏳ 미검증 (FR35 구현 시) |
| V-D19 | `./yt.sh add` 재등록 회귀 (FR7.7~7.9) — 이미 `group`·`auto_run`·`channel_id`가 있는 채널의 URL로 다시 `add` | 세 필드 **전부 보존**되고 `url`·`lang`만 갱신 · 채널 출력 경로 **불변**(라이브러리에서 채널이 사라지지 않음) · 개명 채널(등록명≠핸들) URL로 add해도 `channels.yaml` 항목 수 불변 · 신규 URL은 종전대로 등록+추출 시작 | ⏳ 미검증 (FR35 구현 시) |
| V-D20 | **메모·추출 탭 이름 변경·탭 간 갱신 (FR36)** — 합성 테스트 채널로: ① 추출 탭 카드의 📝로 메모 저장 → **라이브러리 탭으로 전환하면 같은 메모가 보인다**(새로고침 없이) ② 라이브러리에서 메모를 지우면 두 탭 모두 메모 줄이 사라지고 `channels.yaml`에 `note: ""`가 남는다(필드 제거 아님) ③ 추출 탭 ✏️로 이름 변경 → 라이브러리·추출·질의 탭 채널 목록이 **전부 새 이름**이고 질의 탭의 **선택 채널이 첫 채널로 튕기지 않는다**(FR36.10) ④ 스캔 조건 화면을 띄운 채 그 채널의 이름을 바꾸면 화면이 닫히고 안내가 뜨며, 옛 `scan_id`로 `POST /extract` 시 **400** ⑤ 추출 작업 중 메모 저장·이름 변경 **둘 다 409** ⑥ 201자 메모 **400**(절삭되지 않음) ⑦ ✏️·📝 클릭이 **스캔을 시작시키지 않는다**(FR36.7 전파 차단) ⑧ 메모가 빈 기존 채널들의 카드 외형이 **v5.7과 동일**(FR36.5) | 위 8항목 전부 관찰 일치 | ⏳ 미검증 (FR36 구현 완료 — 재빌드 후 브라우저 확인 대기) |
| V-D21 | **주기 자동 추출 (FR37)** — 합성/실채널 혼합으로: ① 배포 직후 기본 상태에서 **`GET /schedule.enabled == false`이고 서버를 30분 띄워도 yt-dlp 요청·RSS 요청이 0**(옵트인 실증) ② 켠 직후에도 즉시 실행되지 않고 `next_due_at`이 **기본 주기(3일) 뒤**(FR37.3·`update` 규약) ③ `POST /schedule/run-now` → 몇 초 내 `kind="schedule_run"` job 생성, **RSS 조회는 `auto_run:false` 채널을 제외한 수만큼**(실측 36) 발생 ④ 새 영상이 없는 채널에는 영상 페이지 요청 0이고, 전부 없으면 **job 자체가 만들어지지 않음** ⑤ 새 영상이 있는 채널만 추출되고 결과물은 **원채널(그룹) 폴더**에 저장, `meta.playlists`가 **덮어써지지 않음**(`pl_map={}`) ⑥ 실행 중 `POST /extract` → **409**, 취소 버튼 → `cancelled`이고 **`last_run_at`이 갱신돼 60초 뒤 재시작하지 않음** ⑦ 사용자 작업 중에 주기를 도래시키면 **그 틱에는 아무 일도 없고** 작업 종료 후 자동 시작 ⑧ 쿠키 경고를 주입하면 실행되지 않고 배너 표시, 쿠키 갱신 후 **자동 재개**(설정 토글 불필요) ⑨ 429 중단을 주입하면 **남은 채널이 돌지 않고**(`warnings` 1건) 다음 주기가 `skip_cycles`로 건너뛰며 UI에 재개 예정이 뜬다 ⑩ `interval_days=5` 요청 **400**(3·7·14·28만 허용) · `interval_days=7`로 바꾸면 `next_due_at`이 그에 맞게 이동(주기는 **고정값이 아니다**) ⑪ 컨테이너 재시작 후에도 설정·`last_run_at`이 유지(`output/.scheduler.json`) ⑫ `SCHEDULER_DISABLED=1`로 띄우면 스레드가 뜨지 않음 | 위 12항목 전부 관찰 일치 | ⏳ 미검증 (FR37 구현 시) |
| V-D22 | **정합 감사·건전성 점검 실데이터 1회 (FR38)** — 실제 저장소·실제 `output/`에서 `./yt.sh audit`와 `./yt.sh doctor`를 각 1회 실행하고, 스펙 작성 시점의 읽기 전용 시제품 실측과 대조한다: ① `audit` **1초 이내**·`doctor` **10초 이내** 종료 ② `git status` **clean 유지**·`output/` 전체 파일 바이트·mtime **불변**(읽기 전용 실증, FR38.3) ③ `doctor`가 **미인덱싱 12편**(`역배열1/firststockclass`·`변곡점주식`·`버럭쌤TV`·`1yearporsh`·`DevilishChart`·`GlobalDefens-e`의 `chroma/` 부재 9편 + `역배열1/차트분석남`·`역배열1/돌파감독`·`한균수` 각 1편) · **extract_log 동일 행 24개** · **멤버십 화석 1건**(`변곡점주식`/`aetOCkgzurM`) · **쿠키 방치 1건**(`detected_at: 2026-08-08`) 을 보고 ④ `doctor`가 `state-files`·`basename-collision`·`orphan-dirs`·`registry-paths`에서 **0건**(현행 기준선) ⑤ `audit`가 §6의 **미발견 식별자 4건**(`loadSchedule`·`saveSchedule`·`runScheduleNow` = 진짜 drift · `Reprocessor` = waiver 대상)을 보고하고, waiver 등록 후 **1건이 `waived`로 이동** ⑥ FR·DQ·V-U 번호 검사가 **허위 보고 0건**(허위 15·6·140건 회귀 — 시제품이 스코프 없이 냈던 수치) ⑦ 종료코드가 FR38.6 규약과 일치하고 `--strict`가 경고를 실패로 승격 ⑧ `--json` 출력이 `python3 -m json.tool`로 파싱 | 위 8항목 전부 관찰 일치 | ✅ 검증 (2026-09-26 구현 후 실측) — ① `audit` **0.0s**(컨테이너 포함 0.22s, `output/` 마운트 없이 성립) · `doctor` **0.3s**(상한 10s) ② `output/` 전체 3,099 파일·772 디렉터리의 크기·mtime **digest 불변**, `chroma/` 부재 채널에 디렉터리 생성 없음, `git status`에 `output/`·`channels.yaml` 변경 없음 ③ **미인덱싱 12편**(`chroma/` 부재 6채널 9편 + 부분 누락 3채널 3편) · **extract_log 동일 행 24개**(4채널) · **멤버십 화석 1건**(`변곡점주식`/`aetOCkgzurM`) 전부 스펙 실측과 일치 ④ `state-files`·`basename-collision`·`orphan-dirs`·`registry-paths` **전부 0건** ⑤ §6 미발견 식별자 **4건**(진짜 drift 3 = `loadSchedule`·`saveSchedule`·`runScheduleNow` → §6을 `schLoad`·`schSave`·`schRunNow`로 정정해 해소 · `Reprocessor` 1건은 waiver로 이동) ⑥ FR·DQ·V-U 번호 검사 **허위 보고 0건**(FR 256행 중복 0·결번 0 · DQ 57 중복 0·결번 0 · V-U 유령·누락 0 — 시제품의 허위 15·6·140건 전부 재현 안 됨) ⑦ 종료코드 audit **1**(경고 9) · doctor **2**(오류 1) · `--strict`가 1→2로 승격 · 문서 부재·미등록 채널·손상 JSON은 **3** ⑧ `--json`이 `python3 -m json.tool` 통과. **수정 사항 — ⓐ 쿠키 "48일 방치"는 오탐이었다**: `cookie_health.get_status()`가 FR19.3대로 Firefox `cookies.sqlite` mtime(2026-08-27) > `detected_at`(2026-08-08)로 이미 자동 해제한다 → 검사를 그 함수 경유로 고정하고 실측을 **0건**으로 정정(상태 파일을 직접 읽으면 오탐, `./yt.sh doctor`처럼 프로필이 마운트된 환경에서 판정해야 한다) ⓑ `upload_date == "00000000"`은 meta가 아니라 **state 레코드 9건**(스펙의 11건은 그 사이 변동) ⓒ 선두 BOM(98/98 파일)은 `extractor.py`가 `utf-8-sig`로 의도적으로 쓰는 것이라 **경고에서 제외**(중간 BOM만 경고) **ⓓ 기준선 0 조정(2026-09-26 후속, 사용자 승인)** — ③의 두 항목은 **고칠 수 없는 과거 기록**이라 그대로 두면 `doctor`가 영원히 빨간 상태로 고정된다(DQ-53이 경고한 그것). `extract_log` 중복은 **정보로 강등**(append-only 시도 기록의 정상 귀결 — 같은 날 24행/4채널 → 30행/10채널로 늘었다. 구조 손상은 오류·경고 유지), 멤버십 화석 1건은 **심각도를 유지한 채 waiver 등재**(2026-09-24 FR13.7·DQ-38으로 해소된 사건의 잔해 — 새 화석은 계속 오류여야 하므로 강등하지 않았다). 재측정: **오류 0 · 경고 0 · 정보 13 · 예외적용 3 · 종료코드 0** (`index-coverage`는 측정 중 실행되던 검색 추출 job의 미인덱싱분 8채널이라 별건 — 인덱싱 후 0으로 돌아온다). `audit`도 **오류 0 · 경고 0 · 종료코드 0** 유지(새 waiver가 `waiver.stale`·`waiver.invalid`를 만들지 않음) |

> **2026-08-08 FR21 실검증**: `POST /videos/delete`·`POST /channels/delete`를 합성 테스트 채널(등록·인덱싱까지 완료한
> 가짜 영상 1건)로 검증 — 실채널 데이터는 전혀 건드리지 않았다. 스캔 진행 중 삭제 시도 시 409 확인(FR21.4).
> FR21.3(클립보드 복사 버튼)은 코드 리뷰·DOM/이벤트 배선까지 확인했으나, Clipboard API 권한 프롬프트가
> CDP `Runtime.evaluate`/합성 클릭 모두에서 자동화 도구를 45초 타임아웃으로 멈추게 해(사용자 제스처 판정 문제로 추정)
> 자동 클릭 검증은 보류했다 — 실 브라우저에서 사용자 클릭 1회 확인 권장(F-1과 동일한 유형의 잔여 검증).

> **2026-08-04 실환경 보강**: FR17.3 실채널 스캔 200 (후보 56 = 추출 49 + 멤버십 7, 재생목록 16 — 기준선 일치)
> → `scan_id` + `latest:1` 조건 `POST /extract` 202 → job done, stats `skip:1`, total=done=1, 신규 0이라 인덱싱 생략(FR17.9).
> 이 경로에서 F-6(extractor 임포트 섀도잉)을 발견·수정했다(§2.10).
> **2026-08-05 재보강**: `include_members=true` 전체 대상 작업(멤버십 재시도 = 실네트워크, 56건 중 멤버 7건)으로
> 장시간 작업을 만들어 V-D5(우아한 취소)·V-D6(실HTTP 409)를 실검증. 부수 확인 — 이미 추출된 영상은
> state 판정이 기간 판정보다 먼저라 요청 없이 `skip`된다(ⓑ기간 조건은 state-미스킵 영상에만 실효,
> FR17.5의 `date_skip`은 신규·수정 영상에서 발생). 만료 scan_id → 400 계약도 실확인.

### 9.4 합격 기준 — 단위·통합 100% 통과, V-D는 해당 FR 구현 시점에 통과, 검색 품질(V-Q)은 수동 확인

> 미검증(⏳·◐) 항목은 네트워크·실쿠키·UI 조작이 필요해 의도적으로 보류한 것이며,
> 각각 mock 경로 검증으로 대체 커버되어 있다. 실환경 재검증 시점은 운영 판단에 따른다.

---

## 10. 설계 결정 사항

| ID | 항목 | 결정 |
|---|---|---|
| DQ-01 | bge-m3 로드 | HuggingFace Hub 다운로드 + 호스트 캐시 마운트 |
| DQ-02 | LLM 품질 검토 단위 | 영상당 1회, 앞 500단어 샘플 |
| DQ-03 | 컬렉션 구조 | subtitle_chunks + desc_chunks 분리 |
| DQ-04 | 청킹 | SRT 120초 윈도우 |
| DQ-05 | 멀티 채널 | channels.yaml + 채널별 독립 폴더 |
| DQ-06 | CLI | yt.sh 래퍼, add 시 추출 자동 시작 |
| DQ-07 | 채널명 | URL @핸들 자동 추출 |
| DQ-08 | 카테고리 | **재생목록 = 카테고리.** 물리 폴더 분리 없이 meta+ChromaDB 태그만 (탐색 로직·마이그레이션 영향 배제) |
| DQ-09 | 진행 보고 | **폴링(2초) > SSE.** 이벤트가 영상당 1회(수십 초 간격)라 폴링으로 충분, 재연결 복잡도 배제 |
| DQ-10 | members 재시도 | 쿠키 존재 시 **매 run 재시도** (대상 소수·비용 무시 가능, mtime 비교는 `extracted_at=""` 기준 부재로 배제) |
| DQ-11 | 쿠키 상태 영속 | `output/.cookie_status.json` — CLI·serve 컨테이너가 공유하는 유일한 쓰기 마운트 |
| DQ-12 | 기간 필터 | flat 스캔이 날짜 미제공 → **처리 시 full info로 확정**, 범위 밖은 자막 다운로드 없이 date_skip |
| DQ-13 | 스캔 캐시 | scan_id 서버 메모리 캐시 TTL 10분 (재스캔 요청 절약). 응답용 view뿐 아니라 **원본 entries·pl_map도 함께 보관**해 `run(entries=, pl_map=)`으로 재사용 |
| DQ-14 | 취소 시 인덱싱 | **생략한다.** 취소는 "지금 멈춤" 신호인데 `index_all()`은 임베딩 모델 로드 + 전체 재임베딩으로 수 분이 걸려 취소 의미를 훼손한다. FR18.2가 보장하는 state 저장·`_backfill_meta`는 정상 수행하고 `status="cancelled"`로 즉시 종료하며, 추출된 파일은 다음 추출 또는 `./yt.sh index`에서 upsert된다(유실 없음). FR17.9의 "완료 후"에 취소는 포함되지 않는다 (FR17.8) |
| DQ-15 | 조건 적용 순서 | **ⓒ카테고리 → ⓓ멤버십 → ⓔ검색어를 AND로 거른 뒤 마지막에 ⓐ최신N을 slice**하고, ⓑ기간은 처리 시 확정(DQ-12). 세 술어는 부작용이 없어 상호 순서와 무관하며 **slice 위치만이 결과를 가른다**(먼저 slice하면 대상이 줄어든다). 프론트 미리보기 `applyFilters()`가 이 순서이므로 **프론트 로직을 계약으로 삼고 백엔드 `jobs.apply_filters()`가 맞춘다** — 어긋나면 V-D11("미리보기 대상 수 = 실제 처리 수")이 즉시 깨진다. ⓐ의 "최신"은 flat 스캔에 날짜가 없어(FR2.6) **스캔 배열 순서(videos 탭 → streams 탭)** 기준이다 (FR17.4) |
| DQ-18 | `sub_type="whisper"`는 추출 완료와 동급 | Whisper 전사 결과는 manual/auto 자막과 동일하게 취급한다 — `StateManager.decide()`의 스킵 판정, `/channels/stats.extracted` 집계, 대시보드 추출됨 표시, 인덱싱 대상 모두 포함. 품질은 auto 자막보다 낮을 수 있으나 "없는 것보다 낫다"가 FR30의 취지이고, 재추출을 원하면 기존 reextract 경로(state 삭제)로 가능하기 때문 (FR30.3) |
| DQ-17 | 재생목록 태깅은 병합 full-map으로만 | `_backfill_meta(mapping)`은 **맵에 없는 vid의 meta.playlists를 `[]`로 덮어쓴다**(전체 맵 전제 설계). 따라서 재생목록 추출(FR24.4)에서 {대상 vid: [재생목록]}만 담은 부분 맵을 `run(pl_map=)`에 넘기면 그 채널의 기존 카테고리가 전부 소실된다. 반드시 채널의 기존 playlists.json(없으면 기존 meta들에서 재구성)에 재생목록 제목을 **병합한 전체 맵**을 전달한다 (FR24.4) |
| DQ-16 | `--limit` 예산 기준 | limit은 "성공 추출 수"가 아니라 **요청 소비 수** 상한이다. `extract_info` 요청을 쓰는 모든 경로(정상·무자막·**멤버십 재시도(FR19.1)**·오류·`date_skip`)가 예산을 소비하고, 요청을 쓰지 않는 state `skip`은 소비하지 않는다. FR14.5의 목적이 429 방어(요청 총량 통제)이기 때문이며, FR19.1 도입으로 멤버십 재시도가 매 run 요청을 쓰게 되면서 성공-기준 카운트로는 카나리아가 실제 요청 수를 통제하지 못한다 (FR14.5) |
| DQ-19 | 스캔 채널명은 **레지스트리 역조회**가 1순위 | `_do_scan`이 `extract_handle(url)`로 이름을 재추출하면 등록명≠핸들인 채널(개명·핸들 변경)에서 `output/<핸들>/state.json`(없는 경로)을 읽어 `extracted`가 전부 false가 되고, 이어지는 추출이 핸들 이름의 새 폴더에 중복 저장된다. DQ-07(핸들 자동 추출)은 **신규 등록** 규칙이지 **기존 채널 조회** 규칙이 아니다 — 조회 경로는 레지스트리를 진실로 삼고 미등록일 때만 핸들로 폴백한다 (FR32.2~32.3) |
| DQ-20 | 제목 언어는 `extractor_args.youtube.lang`으로 **고정** | 다국어 제목 채널에서 flat 스캔(browse)과 영상별 full info(player)가 서로 다른 언어 트랙을 반환해 같은 영상 제목이 화면마다 달라졌다. 후처리 정규화가 아니라 **요청 단계에서 언어를 고정**한다 — 모든 경로가 같은 옵션 빌더(`_ydl_opts`)를 지나므로 한 곳에서 계약이 성립하고, 저장된 meta.title과 스캔 제목이 같아진다. 값은 채널 `lang`(NFR4), 번역이 없으면 yt-dlp가 원제로 폴백한다. **⚠ 결합 관계(2026-09-24 추가, 실측 `_workspace/30`): 이 옵션은 제목만 바꾸지 않는다 — YouTube가 돌려주는 오류 `reason` 문구까지 같은 로케일로 번역한다.** 따라서 `lang`을 바꾸거나 추가할 때는 **오류 메시지 문자열로 판정하는 모든 곳을 함께 점검**해야 한다. 실제로 `lang=ko` 도입(2026-09-09) 이후 영어 키워드만 보던 멤버십 판정이 전량 실패했다(DQ-38). 경계는 **"누가 만든 문구인가"**다 — yt-dlp/urllib가 만든 문구(`_is_no_tab`·`_is_429`)는 영어로 유지되고, YouTube API가 `reason`으로 준 문구만 번역된다 (FR32.1·FR13.7) |
| DQ-21 | 증분 인덱싱 판정은 **저장된 문서 본문 비교** (해시 필드 아님) | 메타에 `srt_sig` 같은 해시를 넣으면 스키마가 바뀌어 **이미 인덱싱된 전량이 한 번은 재임베딩**돼야 혜택이 시작된다. ChromaDB는 문서 본문을 그대로 보관하므로 `col.get(where={"video_id": vid}, include=["documents","metadatas"])`로 id 집합·본문·메타를 그대로 대조하면 마이그레이션 없이 즉시 동작하고, 판정이 근사가 아니라 **정확**하다(같은 청크 수의 다른 자막도 잡아낸다). 조회 비용은 임베딩 대비 무시할 수준이다 (FR33.1~33.2) |
| DQ-22 | 인덱싱 진행율은 **별도 필드**(`index_done`/`index_total`)로 싣는다 | 기존 `done`/`total`은 추출 대상 영상 수의 의미를 갖고 완료 후에도 결과 표시(`10/10`)로 남는다. 인덱싱 진행을 같은 필드에 덮어쓰면 추출 결과가 사라지고, 인덱싱 대상 수(채널 전체)와 추출 대상 수(조건 필터 결과)가 달라 의미도 어긋난다. 프론트는 `phase=="indexing"`일 때만 표시를 전환한다 (FR33.3) |
| DQ-23 | 쇼츠 전용 라벨은 **존재하지 않는다** → duration 휴리스틱 + 조건명 "N초 미만 제외" + 길이 노출 | **실측(2026-09-20, `wEbizb3kF0Q` = `output/toyoungin`의 실제 `#Shorts`, 1080x1920):** 이름에 `short`가 들어간 필드 없음 · `media_type`은 full info에서 `"video"`로 일반 영상과 동일하고 flat 검색 엔트리에는 0/10으로 아예 없음 · `/shorts/{id}`로 접근해도 `webpage_url`이 `watch?v=`로 정규화 · flat url도 전부 `watch?v=`로 정규화(경로 판별 불가) · `availability`·`live_status`도 0/15. 즉 **스캔 단계에서 쇼츠를 정확히 판별할 방법이 없고** 유일한 신호가 `duration`(15/15)이다. `aspect_ratio`(쇼츠 0.56 vs 85초 일반영상 `aIUgM4daefg` 1.78)는 확실한 신호지만 **full info에만 있어** 영상당 `extract_info` 1회(429 예산)를 요구한다 → 스캔 미리보기에 쓸 수 없다(추출 시점 부가 판정으로는 가능하나 이번 범위 밖). 게다가 **그 실제 쇼츠의 duration은 185초로 쇼츠 상한 3분을 넘는 반례**다 — 180초 임계로는 걸러지지 않는다(2024년 상한이 60초→3분으로 확대된 여파). 따라서 조건을 "쇼츠 제외"라고 부르면 **사용자에게 거짓 정확도를 약속**하게 된다 → UI·API·로그 모두 **"N초 미만 제외"**(기본 180초, **임계값 사용자 조정·해제 가능**)로 명명하고, 판별 대신 **`duration`을 그대로 보여줘 사용자가 판단하게 한다**(FR20.5·FR34.5). `duration` 결측 엔트리는 **통과**시킨다 — 판정 불가를 제외 근거로 쓰면 신호 없는 영상이 조용히 사라진다 (FR34.3) |
| DQ-24 | 기간은 **2층**(서버측 `sp` 프리필터 + 처리 시 `date_range` 확정)이고 최종 판정권은 2층에 있다 | flat 검색은 `upload_date`·`timestamp`를 주지 않는다(실측 0/15) — FR2.6·DQ-12와 **똑같은 제약**이다. 그러나 채널 스캔과 달리 검색에는 YouTube가 직접 거르는 `sp=` 프로토버프 필터가 있어 **후보 자체를 줄여 429 예산을 아낄 수 있다**(실측: `EgQIBBAB` 이번달+동영상, `EgQIBRAB` 올해+동영상). 두 층은 상충하지 않는다: 1층은 프리셋(시간/오늘/이번주/이번달/올해)만 표현 가능한 **비용 절감 장치**이고, 임의 날짜 범위는 표현할 수 없다. 정확한 경계 판정은 기존 DQ-12 경로(처리 시 full info의 `upload_date` → 범위 밖 `date_skip`)가 그대로 책임진다. 둘 다 지정되면 둘 다 적용되며, **1층만으로 date_skip이 0이 되리라 가정하지 않는다**(`sp`의 "이번 달" 경계와 사용자의 since/until은 서로 다른 기준이다) (FR34.4) |
| DQ-25 | 검색 유입 신규 채널은 **`auto_run: false`** 로 `run`·`transcribe` 전체 순회에서 제외 | 검색 50건이면 최대 50개 채널이 `channels.yaml`에 등록되는데 `main.cmd_run`은 인자가 없으면 **등록된 전 채널을 순회**한다. 현재 이미 35채널 중 24개가 재생목록 1회 추출로 유입된 상태여서, 검색 추출을 반복하면 `./yt.sh run`이 감당 불가능한 요청 규모가 된다(429 직결). 대안 비교 — (b)그대로 등록: 운영 부담을 사용자에게 전가, (c)채널 미등록·영상만 저장: 출력 구조가 채널 폴더 기준이라 FR24.3·FR25·라이브러리·인덱싱 전반을 다시 설계해야 한다. **(a)플래그**만이 기존 자산을 보존하면서 문제를 정확히 해결한다. 플래그는 `run`·`transcribe`의 **무인자 순회에만** 작용하고(공통 헬퍼 `main.bulk_targets`, 사용자 결정 2026-09-20 — Whisper 전사는 run보다 네트워크·CPU 비용이 크다) 라이브러리·질의·인덱싱·대시보드 추출·명시적 `run 채널명`에는 영향을 주지 않는다 — "이 채널을 배제한다"가 아니라 "일괄 갱신 기본 대상에서 뺀다"는 뜻이기 때문이다. 부재를 `true`로 해석해 기존 yaml은 무변경으로 종전과 동일하게 동작한다 (FR34.7~34.8) |
| DQ-26 | 검색 미리보기는 **재생목록보다 정확도가 낮다** — 숨기지 않고 명시하며, 등식은 `live_wait`를 포함하도록 확장 | flat 검색 엔트리에 `availability`·`live_status`가 없다(실측 0/15). 그 결과 ① 진행 중·예약 라이브가 스캔에서 걸러지지 않고(재생목록 경로는 `live_status`로 사전 제외한다), ② 멤버십 판정이 state(`sub_type=="members_only"`)에만 의존해 **처음 보는 멤버십 영상은 미리보기에서 일반 영상으로 보인다**. 최종 방어선은 처리 시점의 FR16.5 라이브 가드와 FR13·FR19.1 멤버십 감지이므로 **기능은 정상 동작하고 잘못된 파일이 생기지도 않는다** — 훼손되는 것은 "미리보기 = 실제"라는 V-D11의 전제뿐이다. 따라서 (i) 조건 UI에 "멤버십·라이브 여부는 처리 시 확정" 안내를 띄우고, (ii) `search_run`의 검증 등식을 `total == new+updated+skip+no_sub+members_only+error+date_skip+live_wait`로 확장한다. **full info로 사전 보강하지 않는다** — 후보 N개에 extract_info N회를 미리 쓰면 스캔이 flat인 이유(429 예산)가 사라지고 비용이 두 배가 된다 (FR34.5) |
| DQ-27 | 검색 진입은 **전용 `q` 필드**로만 — 순수 텍스트를 검색으로 승격하지 않는다 | `classify_url`이 "URL로 판별 불가 → 검색어로 간주"하면, 오타 난 채널 URL·깨진 재생목록 링크가 **조용히 검색으로 둔갑해** 엉뚱한 채널 수십 개를 등록한다(DQ-25가 막으려는 바로 그 사고를 다른 문으로 들여보낸다). 판별 불가는 계속 400이고, 검색은 `POST /extract/scan {q}`라는 **명시적 의사표시**를 요구한다. 편의를 위해 `youtube.com/results?search_query=…` URL은 인식하되(사용자가 검색 결과 페이지를 복사해 오는 자연스러운 경로), 이때도 분류 우선순위는 영상 → 재생목록 → 검색 → 채널이다 (FR34.1) |
| DQ-28 | 검색어는 **카테고리로 병합하지 않는다**(`pl_map={}`) | FR24.4는 재생목록 제목을 `meta.playlists`에 병합한다 — 재생목록은 **채널 주인이 만든 분류**라 카테고리 의미론과 일치하기 때문이다. 검색어는 사용자의 일회성 질의어이고, 같은 영상이 여러 검색으로 유입되면 태그가 무한히 늘어나 FR15.4 카테고리 필터가 무의미해진다. 검색 묶음의 정체성은 **폴더(FR25·FR34.6)** 가 책임진다. 부수 효과로 `pl_map={}`이면 `if pl_map:`이 거짓이라 `_backfill_meta()`가 호출되지 않아 DQ-17이 경고한 "부분 맵으로 기존 카테고리 전멸" 사고가 구조적으로 불가능해진다. **`None`은 반대로 `scan_playlists()`(채널마다 추가 요청 = 429 예산 소모) + 백필을 유발하므로 쓰지 않는다** — `run()` 독스트링이 명시하듯 "주어지면(빈 dict 포함) `scan_playlists()` 생략"이므로 **`{}`가 '생략'의 정식 값**이다(`extractor.py`: `if pl_map is None:` → `scan_playlists()`, `if pl_map:` → `_backfill_meta()`). 실측(2026-09-20 QA): `{}` → scan 0회·backfill 0회 / `None` → 둘 다 1회. 대상 영상의 `playlists`는 다음 전체 run의 백필(FR15.5)로 채워진다 (FR34.10) |
| DQ-29 | 길이 노출은 **기존 meta 재사용** — 백필·재추출 없음 | `meta_collector`의 수집 필드에 `duration`·`duration_string`이 이미 들어 있어(§5.3) `output/*/meta/*.json`에 값이 **이미 저장돼 있다**. 따라서 `list_videos`의 응답 필드와 프론트 표시만 추가하면 되고, 마이그레이션·재추출을 절대 수행하지 않는다(DQ-21과 같은 취지 — 기존 데이터가 즉시 혜택을 받아야 한다). 과거 파일에 필드가 없을 수 있으므로 `null`/`""`로 내리고 프론트는 빈칸 처리한다 — **0으로 채우지 않는다**(0초 영상과 구분 불가) (FR20.5~20.6) |
| DQ-30 | 배치 휴식 카운터는 **작업(job) 단위 상태**(`extractor.BatchRest`)로 분리해 `run()` 호출 경계를 넘어 공유한다 | **기존 결함(2026-09-20 QA 발견, FR34가 드러냈을 뿐 FR24부터 있었다).** 휴식 카운터가 `Extractor.run()`의 **지역 변수**였는데, 그룹 추출 워커 `_run_grouped`는 채널 그룹마다 `Extractor(ch_cfg).run(...)`을 **새로 호출**한다 → 그룹이 바뀔 때마다 카운터가 0으로 리셋된다. 재생목록은 보통 한 채널이라 눈에 띄지 않았지만(다채널 재생목록에는 **똑같은 구멍이 이미 있었다**), **검색은 영상당 채널이 다르다** — 실측 후보 13개 = 서로 다른 채널 13개 → 그룹당 1영상이라 `batch_size`(8~12)에 **영원히 도달하지 못한다.** 기본 `limit 20`이면 휴식 0회로 `extract_info` 20연속, 즉 FR14.2 방어가 검색 경로에서 통째로 빠진다. 대안 비교 — (a)`_run_grouped`가 그룹 **사이에서만** 세어 쉰다: 그룹 내부는 여전히 리셋된 카운터로 세므로 큰 재생목록에서 8~12개 주기가 어긋난다. (b)**상태 객체 주입**: `BatchRest`(`count()`/`due()`/`take()`)를 `run(rest_state=)`로 받아 그룹 루프 **바깥**에서 한 개 만들어 모든 `ext.run()`에 넘긴다 → 그룹 내부·그룹 경계 모두 하나의 카운터로 세어지고, **재생목록(FR24)·검색(FR34) 두 경로가 공통 워커 한 곳에서 동시에 막힌다.** (b)를 택했다. 휴식 시간·다음 배치 크기는 `take()`마다 `BATCH_SIZE_RANGE`·`BATCH_REST_RANGE`에서 **재추첨**해 FR14.2의 랜덤화 취지를 유지하고, 429 재시도의 카운터 소비(DQ-16)도 그대로다. `rest_state=None`이면 그 호출 전용 객체를 만들므로 **CLI 경로는 휴식 시점·randint 사용 횟수·`time.sleep` 호출 형태까지 완전 무영향**이다(V-U21이 고정). 부수적으로 `cancel_check`를 받은 경우에만 휴식을 1초 단위로 쪼개 자 취소 응답성이 최대 90초 → 1초가 된다(FR18.2). **실사용 영향**: 다채널 재생목록·검색 추출은 이제 실제로 쉬므로 **추출 시간이 휴식만큼 늘어난다 — 의도된 비용**이다(429 차단 1회가 24시간 대기를 부르는 것보다 싸다) (FR14.2·FR24·FR34.9) |
| DQ-31 | 폴더(그룹)를 **실제 디렉터리로 승격**한다 (A안) — DQ-08의 "물리 폴더 분리 없음"은 **카테고리**에만 유효하다 | 채널 70개(그룹 지정 59 · 미지정 11)에서 `output/` 평면 탐색이 실질적으로 불가능해졌다. 승격 비용이 낮은 이유는 구조가 이미 준비돼 있기 때문이다 — 경로 생성 12곳이 **전부 `config.channel_dir()` 단일 통로**를 지나고, 채널 열거는 `channels.yaml`만 보며(`/channels`·`/channels/stats` 어디에도 파일시스템 스캔이 없다), ChromaDB가 채널 폴더 **안**에 있어 함께 따라가고, **저장된 절대경로가 0건**(chroma 메타 = `video_id`·`title`·`source_url`, meta.json = `webpage_url`)이라 이동 후 깨질 참조가 없다. 따라서 변경 표면은 `channel_dir()` 내부 + 이동/마이그레이션 신규 코드로 **국한**된다. **DQ-08과 모순되지 않는다**: DQ-08이 거부한 것은 "재생목록(카테고리)마다 물리 폴더를 만드는 것"(영상 1개가 복수 재생목록에 속해 중복·동기화 문제 발생)이고, `group`은 **채널당 정확히 0~1개**라 그런 모호성이 없다 (FR35.1) |
| DQ-32 | 경로 해석기는 **`config` 내부의 yaml 직접 파싱 + `(st_mtime_ns, st_size)` 캐시** — 지연 import도 훅 등록도 아니다 | `channel_registry → config` 의존이 이미 있어 `config`가 `ChannelRegistry`를 모듈 레벨에서 import하면 순환이다. 후보 3안 비교 — **(a) 함수 내 지연 import**: 순환은 피하지만 `ChannelRegistry()` 생성이 **매 호출 yaml 파싱**이라 핫패스(`channel_dir`)에 부적합하고, 캐시를 붙이려면 어차피 config 쪽 상태가 필요하다. **(b) 의존성 역전(훅 등록)**: `config`에 `_group_resolver` 훅을 두고 `channel_registry` import 시 등록 → **`channel_registry`를 import하지 않은 프로세스에서는 훅이 비어 평면 경로로 떨어진다.** 같은 채널이 프로세스마다 다른 경로를 갖는 최악의 버그라 **기각**. **(c) config 내장 경량 해석기(채택)**: `channels.yaml`에서 `{채널: group}`만 읽는 **읽기 전용** 파서를 config에 둔다 — `channel_registry`를 전혀 참조하지 않아 순환이 없고, 어떤 프로세스든 동일하게 동작한다. 파싱 로직이 두 곳이 되지만 config 쪽은 **읽기 전용 최소 파싱**이고 **쓰기는 `ChannelRegistry` 독점**이라 드리프트 위험이 낮다. 캐시 무효화는 **mtime+size 자동 감지가 1차**다 — 명시 무효화만 두면 호출을 빠뜨렸을 때 조용히 깨지고, 무엇보다 **CLI 컨테이너와 serve 컨테이너가 별개 프로세스**라 한쪽의 무효화가 다른 쪽에 전달되지 않는다. `_save()`의 `invalidate_group_cache()`는 같은 프로세스 즉시 반영을 위한 **2차 안전망**일 뿐이라 빠뜨려도 mtime이 잡는다. 캐시 히트 비용은 `stat()` 1회 (FR35.2~35.3) |
| DQ-33 | 그룹명은 **정규화·치환하지 않고 거부**한다 (쟁점 1) | 대시보드 `schFolder`·📁 프롬프트는 **자유 텍스트**이고 이 값이 이제 디렉터리 이름이 된다. sanitize(치환) 방식을 택하면 `A/B`와 `A_B`, `역배열1 `과 `역배열1`이 **같은 디렉터리로 붕괴**해 표시명↔디렉터리가 N:1이 되고, 이름 변경·조회·마이그레이션이 전부 "어느 표시명의 폴더인가"를 되묻게 된다. 거부하면 **yaml의 `group` 값 = 디렉터리명**이라는 1:1 불변식이 성립해 모든 하위 로직이 단순해진다. 검증은 **순수 문자열 연산**으로 하고(`resolve()`·`exists()` 금지 — `channel_dir()`는 핫패스다) 경로 탈출·절대경로·제어문자·선행 점(숨김)·후행 점/공백·Windows 예약어를 모두 막는다. `server.py:417`의 `is_relative_to(OUTPUT_BASE)` 가드는 **중첩 경로에서도 그대로 유효하므로 유지**하되, 그것만으로는 부족하다 — 그 가드는 삭제(`purge`) 한 곳에만 있고 `mkdir`을 호출하는 8곳에는 없기 때문이다. 그래서 방어선을 **`channel_dir()` 반환값 자체**로 끌어올렸다: 반환값이 항상 `OUTPUT_BASE` 하위임이 보장되면 mkdir 호출부를 하나도 고치지 않아도 된다. 읽기 경로에서 yaml의 잘못된 `group`을 만나면 **예외 대신 평면 폴백 + 1회 경고**다 — 수동으로 yaml을 편집한 사용자 때문에 라이브러리 전체가 죽으면 안 된다 (FR35.4~35.5) |
| DQ-34 | `output/` 최상위는 **그룹명과 "그룹 미지정 채널명"이 공유하는 하나의 이름공간**이며 유일해야 한다 (쟁점 2) | 그룹 없는 채널 11개는 `output/<채널>/`에 있고 그룹 폴더도 `output/<그룹>/`다. 이름이 같으면 `output/X/`가 **채널 폴더이면서 그룹 폴더**가 되어 `srt/`·`state.json`과 하위 채널 폴더가 한 디렉터리에 섞이고, 그 뒤 채널 삭제(`purge=true`)가 `rmtree`로 **그룹 전체를 지운다.** 그래서 지정·해제·채널 개명·신규 등록 **네 방향 모두**에서 충돌을 막는다. 디스크 실재 여부도 함께 본다 — 등록 해제 후 남은 잔존 폴더(FR21.2 `purge=false`·FR32.4)가 레지스트리에 없는 채로 최상위를 점유하고 있기 때문이며, `state.json`·`srt/`·`meta/` 중 하나라도 있으면 채널 폴더로 간주한다. 반대로 **그룹 `G` 안의 동명 채널(`output/G/G/`)은 허용**한다 — 중첩 레벨이 달라 실제 경로 충돌이 아니고, 금지하면 "AI LLM Wiki 폴더에 AI LLM Wiki 채널을 넣을 수 없다"는 납득 불가한 제약이 된다. 자동 폴더 지정(FR25.7·FR34.6)만은 **거부가 아니라 건너뛰기**다(FR35.13) — 배치 추출 한복판에서 이름 충돌 하나로 수십 개 영상 작업이 무산되는 쪽이 더 큰 손해다 (FR35.6·35.13) |
| DQ-35 | 이동은 **`os.rename` 단일 호출만** — 복사 폴백 금지, yaml 기록 실패 시 **역방향 rename 보상 롤백** (쟁점 3) | 같은 파일시스템 안에서 `os.rename`은 **원자적**이다(POSIX). 반면 `shutil.move`는 경계를 넘으면 조용히 **복사+삭제**로 떨어지고, 그 도중 실패는 곧 **자막 유실 또는 반쯤 옮겨진 상태**다. 따라서 `st_dev` 불일치(EXDEV)면 복사로 폴백하지 않고 **실패로 끝낸다** — 이동을 못 하는 것은 복구 가능하지만 반만 옮기는 것은 아니다. 목적지가 존재하면 rename을 아예 시도하지 않는다(POSIX rename은 **빈 디렉터리를 무음으로 교체**할 수 있다). 순서는 **디스크 먼저, yaml 나중**이다: yaml을 먼저 쓰고 rename이 실패하면 `channel_dir()`가 없는 경로를 가리켜 **라이브러리에서 채널이 통째로 빈 것처럼 보인다**(사용자가 데이터 유실로 오인). 반대 순서에서 yaml 기록이 실패하면 파일은 새 경로, 해석은 옛 경로라 같은 증상이므로 **보상 롤백(역방향 rename, 이것도 원자적)** 으로 원상 복구하고, 롤백까지 실패한 극단에서는 **수동 복구용 `mv` 원문 경로 한 줄**을 예외·로그에 남긴다(침묵 금지). 정리는 **빈 디렉터리 `rmdir`만** 허용하고 `rmtree`는 이 경로에 절대 두지 않는다. 경합은 FR31.5·FR21.4의 `is_busy()` 409 가드를 그대로 재사용하고, 반대 방향(CLI 마이그레이션 ↔ serve)은 job 상태를 공유할 수 없으므로 `output/.migration.lock` 파일로 막는다(DQ-11이 `output/`을 유일한 공유 쓰기 마운트로 쓰는 것과 같은 수법). **열린 ChromaDB 핸들은 문제가 되지 않는다** — POSIX rename은 inode를 유지하므로 이동 전 열린 sqlite 핸들은 이동한 실제 데이터를 계속 가리키고, 이동 후 생성되는 `KLIndexer`/`KLQuery`는 생성자에서 `channel_subdirs()`를 새로 읽어 새 경로로 연다(두 클래스 모두 모듈 레벨 캐시가 없다) (FR35.7~35.10) |
| DQ-36 | 마이그레이션은 **명시적 CLI · dry-run 기본 · 저널 자동 롤백 · `channels.yaml` 무변경** (쟁점 4) | 대상은 사용자의 **되돌릴 수 없는 실데이터**(70 디렉터리)다. **서버 기동 시 자동 실행을 기각**한 이유 — NFR3(수동 실행 전용)에 정면으로 어긋나고, 대시보드를 켜는 순간 수십 GB가 말없이 이동하며, 실패 시 사용자가 어느 시점의 무엇이 깨졌는지 알 길이 없다. 그래서 `./yt.sh migrate-groups`의 **기본값이 dry-run**이고 `--apply`가 있어야 움직인다. 사전 검증 6종(락·저널 / 세그먼트 / 이름공간 / 목적지 부재 / `st_dev` / 대화형 확인)은 **전부 통과해야 시작**하고 하나라도 실패하면 **한 건도 옮기지 않는다** — 절반 옮기고 멈추는 것이 최악이기 때문이다. 실행 중에는 이동 1건마다 저널을 **append + fsync**해 프로세스가 죽어도 어디까지 옮겼는지가 디스크에 남고, 실패 시 **역순 자동 롤백**, 저널이 남아 있으면 다음 실행이 **즉시 중단하고 `--rollback`을 요구**한다. 결정적으로 **마이그레이션은 `channels.yaml`을 한 글자도 바꾸지 않는다** — `group` 값은 그대로이고 해석 규칙만 달라지므로 순수 디렉터리 이동이고, 따라서 **롤백도 디렉터리 되돌리기만으로 완결**되며 "yaml은 새 형식인데 디스크는 옛 형식" 같은 반쪽 상태가 **구조적으로 불가능**하다. 여유 공간 검사는 하지 않는다(rename은 복사가 없다). 백업은 강제하지 않되 `--apply` 전에 권고 문구를 띄운다 (FR35.11~35.12) |
| DQ-37 | `ChannelRegistry.add()`는 **upsert**이고 이름 해석에 **`resolve_name`** 을 쓴다 (쟁점 5, 기존 결함) | 현행 `add()`는 `self.data["channels"][name] = {url, lang, added_at, note}`로 항목을 **통째로 덮어써** `group`·`auto_run`·`channel_id`를 전부 날리고, 이름을 `resolve_name`이 아닌 `extract_handle`로 뽑아 **개명 채널(등록명≠핸들)에 두 번째 항목**을 만든다(DQ-19가 스캔 경로에서 막은 사고가 등록 경로에 그대로 남아 있었다). 호출부 4곳 중 `jobs.py` 3곳은 `not in reg.names()` 가드로 **우연히** 보호되지만 **`main.cmd_add`(`./yt.sh add`)에는 가드가 없다** — 이것이 실제 구멍이다. 호출부마다 가드를 붙이는 대신 **`add()` 자체를 고친다**: 가드는 새 호출부가 생기면 또 빠지지만 함수 계약은 한 번 고치면 모든 호출부가 혜택을 받고, `rename()`이 이미 독스트링으로 "설정 보존"을 약속하고 있어 **두 함수의 계약을 일치**시키는 것이 옳다. FR35와 묶는 이유는 심각도 격상이다 — 승격 전에는 `group` 소실이 "라이브러리 폴더 분류가 풀림"이었지만, **승격 후에는 `channel_dir()`가 즉시 다른 경로를 돌려줘 그 채널의 자막이 사라진 것처럼 보이고 이후 추출이 최상위에 새 폴더를 판다.** 표시 문제가 데이터 경로 문제로 바뀐다 (FR7.7~7.9) |
| DQ-38 | 멤버십 판정의 **1차 신호는 `availability`**(언어 비의존)이고 메시지 키워드는 2차 폴백이며, 규칙은 `video_access.py` **한 곳**에만 둔다 | **기존 결함(2026-09-24 발견, DQ-20이 유발한 회귀).** `Extractor._is_members_only`가 영어 키워드 5종만 봤는데 DQ-20이 `extractor_args.youtube.lang=ko`를 넣자 YouTube가 주는 `reason`이 한국어로 바뀌어(`이 동영상은 …VIP 회원 등급 이상의 채널 회원에게 제공됩니다…`) **2026-09-09 이후 모든 멤버십 영상이 조용히 `error`로 분류**됐다. 같은 영상 `aetOCkgzurM` 실측: `lang=ko` → 판정 False / `lang` 미지정 → `This video is available to this channel's members on level: …` → True. 예외 객체에는 언어 비의존 신호가 없다(둘 다 `DownloadError`, 구조화된 사유 속성 없음). 파급은 세 겹이다 — ⓐ 대시보드에 멤버십으로 표시되지 않고 ⓑ `_mark_skip`이 불리지 않아 state에 기록이 없어 **FR19.1의 쿠키 재시도 대상에서 빠지며** ⓒ 매 run 실패를 반복해 429 예산만 태운다. **해결**: 이미 `jobs._is_members_availability`(FR17.6)가 쓰던 **구조화 필드 `availability`(`subscriber_only`·`needs_auth`·`premium_only`)를 추출 경로에도 1차 신호로 도입**한다 — 스캔 flat 엔트리·full info 모두 이 필드를 주고 **로케일과 무관**하다. 메시지 키워드는 `availability`가 없는 경로(단일영상 추출 등)의 **안전망**으로 남기되 한국어 패턴(`채널 회원`·`회원 전용`·`회원 등급`·`멤버십`)을 더한다 — `회원` 단독처럼 넓은 패턴은 오탐을 만들어 정상 영상을 `_mark_skip`으로 영구 스킵시키므로 쓰지 않는다. **규칙을 `video_access.py`(의존성 없는 잎 모듈)에 모은 이유**: 대시보드와 추출에 각각 두면 이번 같은 드리프트가 다시 난다. `extractor`에 두면 `jobs.py`가 모듈 로드 시점에 yt-dlp까지 끌어오게 되어 섀도잉 방어용 지연 import(`_app_extractor`)가 무의미해진다 → 양쪽이 최상단에서 안전하게 import할 수 있는 독립 모듈이 유일한 해다. **429 우선순위**: `availability`는 실패 *원인*이 아니라 영상의 *속성*이라, 멤버십 영상에 일시 429가 나면 멤버십으로 오분류돼 `_mark_skip`으로 **영구 스킵**된다 → 루프는 `_is_429`를 **먼저** 확정하고 비-429 실패에만 멤버십 판정을 적용한다(메시지 경로에서는 두 집합이 겹치지 않아 동작 불변). **메시지 기반 판정 경계표**(전수 점검): `_is_no_tab`·`_is_429`는 **yt-dlp/urllib 자체 생성** 문구(+숫자 `429`)라 안전함이 실측 확인됐고, 번역되는 것은 **YouTube가 `reason`으로 준 문구뿐**이다 — 앞으로 메시지 판정을 추가할 때 이 경계를 먼저 물어야 한다(DQ-20) (FR13.7·FR17.6·FR19.1) |
| DQ-39 | 채널 메모는 **죽어 있던 `note` 필드를 살리는 것**이고, "간단한"의 경계는 **한 줄·200자·`prompt` 입력·검색 비대상**으로 고정한다 | `channels.yaml`의 `note`는 `add(url, lang, note)`가 이미 쓰고 FR7.7(upsert)이 "비어 있지 않을 때만 갱신"으로 **보존 계약까지** 갖고 있는데, 읽는 코드도 노출하는 API·UI도 **0**이고 등록 77채널의 값이 **전부 빈 문자열**이다(2026-09-24 조사). 따라서 신규 필드·마이그레이션·백필이 전부 불필요하며 추가 비용은 **`set_note` 1개 + 엔드포인트 1개 + 응답 필드 1개**다. 경계를 명시적으로 고정하는 이유: "메모"는 요구가 늘어나기 가장 쉬운 기능(태그→검색→마크다운→이력→첨부)이고, 그 확장은 전부 **스키마 변경 + 인덱싱 관여 + UI 위젯**을 끌고 들어와 지금의 0비용 구조를 깬다. 빈 값을 `""`로 **남기는** 것도 같은 맥락이다 — `group`·`auto_run`식 "기본값이면 제거"를 흉내 내면 77채널의 기존 `note: ""` 줄과 불균일해지고 무의미한 대량 diff가 난다. **검색 제외**의 근거: 라이브러리 즉시 필터는 *영상 제목* 축이고 내용 검색은 *ChromaDB 벡터* 축이라 메모가 들어갈 자리가 없다 — 넣으려면 채널 단위 의사 문서를 만들어 컬렉션 스키마(DQ-03)를 오염시켜야 한다. 채널 77개는 눈으로 훑을 수 있는 규모이고, 정말 필요해지면 `/channels/stats`를 이미 들고 있는 **클라이언트 측 카드 필터**로 백엔드 무변경 확장이 가능하다 (FR36.1~36.2·36.6) |
| DQ-40 | 뷰 갱신은 **`/channels/stats` 단일 출처 + 공통 헬퍼 `refreshChannelViews({names})`**, 비활성 탭은 **무효화 플래그로 지연 로드**하고 기존 rename 4종 중 **`renameChannel`만 이관**한다 | 현행은 호출부마다 `loadLibrary(); loadExtChannels(); loadChannels();`를 **손으로 나열**한다(`renameChannel`). 새 기능이 늘 때마다 하나를 빠뜨리면 "다른 탭에 옛 이름이 남는" 증상이 조용히 생기는 구조라, 이름·메모 변경만큼은 통로를 하나로 묶는다. **전량 이관을 기각한 이유:** 폴더·영상·카테고리 변경은 갱신해야 할 대상이 서로 다르고(영상 목록만 / 라이브러리만), 이관하면 **호출 집합이 바뀌어** 회귀 위험만 생기고 얻는 게 없다. 반면 `renameChannel`은 **이미 헬퍼와 정확히 같은 3종을 호출**하므로 이관해도 동작이 같다(유일한 차이는 라이브러리 탭이 비활성일 때 즉시 재조회 대신 무효화로 미루는 것 — fetch가 줄고 결과는 동일). **`libLoaded` 가드를 없애지 않은 이유:** 가드를 제거하면 탭 전환마다 전체 재조회가 되어 FR25.4 접기 상태·채널 선택이 매번 흔들린다. 문제는 캐시가 아니라 **무효화 신호가 없던 것**이고, 이미 `pollJob` 완료 훅이 `libLoaded = false`로 같은 수법을 쓰고 있다. **질의 탭을 `names`로 가른 이유:** `loadChannels()`는 현재 선택을 첫 채널로 되돌리고 `loadVideos()`까지 부르는 **부작용 있는 함수**라, 메모 변경처럼 이름과 무관한 갱신에서 호출하면 질의 중 화면이 튄다(FR36.9~36.10) |
| DQ-41 | 채널 이름 변경은 **그 채널을 참조하는 스캔 캐시를 버린다** — 캐시를 새 이름으로 고쳐 쓰지 않는다 (기존 결함) | `_run_channel`은 스캔 캐시에 박힌 `entry["channel"]`(스캔 시점의 이름)을 끝까지 쓴다. 이름이 바뀌면 `channel not in reg.names()`가 참이 되어 `reg.add(url)`을 부르지만, **`add()`의 반환값을 받지 않으므로** 지역 변수 `channel`은 옛 이름 그대로이고 뒤이은 `reg.get(옛이름)`은 `KeyError` → 폴백 cfg로 진행한다. 결과적으로 `config.channel_dir(옛이름)`이 **레지스트리에 없는 평면 경로**를 만들고 자막·state·ChromaDB가 **유령 폴더**에 쌓인다(라이브러리에는 나타나지 않는다). **캐시 재작성(rewrite)을 기각한 이유:** 캐시에는 `channel` 말고도 `by_channel` 키·`url`·`entries`가 얽혀 있고, 이름 변경 중 부분 갱신은 새로운 불일치를 만든다. 반면 **폐기는 단 한 줄이고 기존 400 계약**("scan_id가 만료되었습니다. 다시 스캔하세요")에 그대로 착지한다 — 스캔 재실행 비용은 1+N회 요청이지만 이름을 바꾸는 빈도는 매우 낮다. 이 결함은 FR31(v5.1)부터 존재했으나 이름 변경이 라이브러리 탭에만 있어 드러나지 않았고, 추출 탭에 ✏️를 다는 FR36.7이 **스캔 결과 화면 바로 옆에** 방아쇠를 놓는다. **채널 삭제도 같은 계열**이다 — 삭제 뒤 옛 `scan_id`로 추출하면 `reg.add()`가 채널을 되살려 "삭제했는데 다시 생긴다"가 되므로, 한 줄짜리 같은 해법을 `/channels/delete`에도 건다 (FR36.8) |
| DQ-42 | 메모 저장도 작업 중 **409**다 — 이유는 파일 경합이 아니라 **`channels.yaml` lost update** | "메모는 디렉터리를 건드리지 않으니 409가 과하다"는 반론이 자연스러우므로 근거를 남긴다. `ChannelRegistry`는 **생성 시 yaml 전체를 읽고 `_save()`가 전체를 덮어쓰는** read-modify-write이고, 그룹 추출 워커 `_run_grouped`는 `reg = ChannelRegistry()`를 **작업 시작 시 한 번 만들어 수십 분짜리 루프 내내 재사용**하면서 `add`·`set_auto_run`·`set_group`으로 `_save()`를 반복한다. 작업 도중 다른 요청이 메모를 쓰면 워커의 다음 `_save()`가 **그 메모를 조용히 되돌린다** — 실패도 로그도 없는 소실이다. 대안(저장 시 yaml 재읽기 후 필드만 갱신, 또는 파일 락)은 registry 전반의 동시성 모델을 바꾸는 일이라 "간단한 메모"의 범위를 넘는다. 사용자 비용은 **추출 중 몇 분간 메모를 못 적는 것**이고 이득은 소실 0이며, 이름 변경(FR31.5)·`auto_run` 토글이 이미 같은 이유로 409다 (FR36.11) |
| DQ-43 | 종목코드는 **문맥 근거가 있을 때만** 채택한다 — 정규식을 다듬는 방식을 기각 | 구 규칙 `\b(\d{6})\b`의 실측 결과는 **오탐률 100%**(441개 meta 중 값이 있는 26개 전부 오탐: 제목 앞머리 날짜 `[주식] 260819 …` 22종 + 설명 고정문구의 계좌번호 조각 `우리은행 /1002 763 241686 /`). **정규식 보정을 기각한 이유:** 6자리 숫자라는 모양은 종목코드·YYMMDD 날짜·계좌/전화/사업자번호 조각·URL 경로가 전부 공유한다 — 문맥 없이 숫자만 보면 어떤 패턴을 써도 이 넷을 가를 수 없다. 그래서 판정을 **후보(6자리) → 근거(라벨·거래소 표기·괄호·나열) → 배제(URL·숫자 나열·날짜)** 로 재구성했다. **날짜 배제를 약한 근거에만 적용한 이유:** KRX 코드의 약 3.7%(전체 6자리 공간 기준 37,200/1,000,000)가 YYMMDD로도 읽히므로(`010130` 고려아연, `000120` CJ대한통운) 일괄 배제하면 진짜 코드를 놓친다 — `종목코드 010130`처럼 **종목 전용 라벨**이 있으면 날짜 해석보다 라벨이 강하다고 본다. **채택 규칙을 더 넓히지 않은 이유:** 이 코퍼스에는 근거 있는 코드가 한 건도 없어(실측 새 규칙 채택 0건) 넓히는 근거 자체가 없다 — 한국 주식 유튜버는 종목을 **이름**으로 부른다. 따라서 **빈 값이 정확한 결과**이며, "티커가 안 잡힌다"는 관찰은 규칙을 느슨하게 되돌릴 근거가 **아니다**(FR12.6). 종목명 기반 추출은 사전이 필요한 별개 기능이라 범위 밖이다. **백필을 dry-run 기본으로 둔 이유:** 대상이 사용자 실데이터(441개 meta)이고 `migrate-groups`(DQ-36)가 세운 선례와 같다 — 재계산은 네트워크 없이 결정적이므로 언제든 다시 돌릴 수 있고, 잘못 쓰는 쪽만 되돌리기 어렵다 (FR12.2·12.5~12.7) |
| DQ-44 | 스케줄러는 **serve 프로세스 안의 스레드**다 — 별도 컨테이너·호스트 cron·launchd를 전부 기각 | 결정적 근거는 **동시 실행 제어**다. `JobManager`의 점유 플래그(`_busy`)는 **모듈 싱글턴**이고 스캔 캐시·취소 이벤트·진행 상태도 전부 프로세스 메모리에 있다. 스케줄러가 다른 프로세스(cron이 띄우는 `./yt.sh run`, 별도 컨테이너)에 있으면 사용자가 대시보드에서 추출하는 **바로 그 순간에 두 번째 추출이 시작**돼 429 위험이 배가되고, 이를 막으려면 파일 락 기반 프로세스 간 배타 제어를 새로 만들어야 한다(`.migration.lock`이 그 비용을 보여준다 — FR35.10). 같은 프로세스에 두면 `is_busy()` 한 줄로 끝나고 진행율(FR18)·이벤트(FR26)·취소(FR17.8)·인덱싱(FR33)이 **전부 공짜로 재사용**된다. 대가는 "대시보드 컨테이너가 떠 있어야 한다"는 운영 전제이며, 이는 사용자의 요구("맥북이 켜져 있으면 계속 떠 있어야 함")와 정확히 일치한다 (FR37.1·37.18) |
| DQ-45 | 주기 판정은 **정시(cron)가 아니라 "마지막 실행 + 간격"**, 밀린 주기는 **1회만** 따라잡는다 | 운용 호스트가 **맥북**이라 덮개를 닫으면 컨테이너가 통째로 멈춘다(NFR6 개정). "매주 일요일 3시" 같은 정시 모델은 그 시각에 잠들어 있으면 **영원히 실행되지 않거나**, 깨어난 뒤 밀린 실행을 몰아서 터뜨린다. 경과 시간 모델은 깨어난 다음 틱에 자연히 따라잡고 시계·타임존 변경에도 둔감하다. 밀린 주기를 **누적 실행하지 않는 이유**는 작업이 state.json 기반 멱등이기 때문이다 — 3주를 건너뛰었어도 한 번 돌면 그동안의 신규 영상을 (RSS 상한 내에서) 전부 잡고, 두 번째 실행은 "새 영상 없음"으로 요청만 낭비한다. `last_run_at`은 cron식 `+= interval`이 아니라 **`= now`** 로 갱신한다(누적식은 깨어난 직후 연속 실행을 유발한다). 기본 간격은 **3일**이며 선택지는 3·7·14·28일이다(값 선택 근거는 FR37.3) (FR37.2~37.3) |
| DQ-46 | 스케줄 설정·상태는 **`output/.scheduler.json`** — `channels.yaml` 기각 | ⓐ **lost update:** `ChannelRegistry`는 인스턴스 생성 시 yaml 전체를 읽고 `_save()`로 전체를 덮어쓰는 read-modify-write이며, 그룹 워커는 **작업 내내 같은 인스턴스**를 들고 있다(FR36.11·DQ-42가 메모 저장을 409로 만든 바로 그 이유). 스케줄러는 주기마다 `last_run_at`을 쓰므로 이 경합을 **정면으로** 맞는다 — 게다가 그 쓰기는 무인이라 아무도 알아채지 못한다. ⓑ 스케줄 설정은 **전역**인데 `channels.yaml`에는 전역 섹션이 없다(최상위가 `channels:` 하나) — 없는 층을 새로 만들면 `add`·`rename`·`set_*` 전부가 그 층을 보존해야 한다. ⓒ `output/`은 CLI·serve가 공유하는 **유일한 쓰기 마운트**라는 선례가 이미 둘 있다(DQ-11 `.cookie_status.json`, FR35.10 `.migration.lock`). ⓓ 설정과 런타임 상태를 **한 파일**에 둔 것은 쓰는 주체가 serve 프로세스 하나뿐이라 분리 이익이 없고, 파일이 늘수록 부분 기록 조합이 늘기 때문이다(원자 교체 1회로 끝낸다) (FR37.13) |
| DQ-47 | RSS **15개 상한**은 무시하되 **감지·노출**한다 — 주기적 전체 스캔 폴백을 기각 | 한 채널이 한 주기에 15개를 넘게 올리면 피드가 오래된 쪽을 잘라낸다. 그래도 ⓐ **유실이 아니라 지연**이다 — 잘린 영상은 state.json에 없으므로 다음 수동 `./yt.sh run`이 정상 처리한다(무엇도 영구히 사라지지 않는다). ⓑ 폴백으로 "가끔 전체 채널 스캔"을 넣으면 **무인 요청량이 채널 수만큼 곱해지고**(36채널 × 2탭 + 재생목록) 그것은 "무작정 전체 run 하지 않는다"는 이 기능의 전제 자체를 무너뜨린다 — RSS 선행을 채택한 이유가 사라진다. ⓒ 그렇다고 조용히 넘기지는 않는다: **새 영상 수가 15에 도달하면 `truncated`** 로 표시해 "전체 run 권장" 안내를 띄운다(감지 비용 = 배열 길이 비교 = 0). 판단은 사람에게 남기고, 기계는 요청을 늘리지 않는다. **ⓓ 주기 길이가 이 타협의 발생 빈도를 직접 좌우한다** — 상한에 걸리려면 *한 주기 안에* 15개를 넘겨야 하므로, 기본 주기를 7일이 아니라 **3일**로 잡은 것 자체가 이 구멍을 줄이는 1차 수단이다(FR37.3ⓐ). 즉 이 DQ의 "무시"는 주기가 짧다는 전제 위에서 성립하며, 사용자가 주기를 28일로 늘리면 `truncated` 경고가 그만큼 자주 뜨는 것이 정상 동작이다 (FR37.6) |
| DQ-48 | 429 회로차단을 **2단**으로 올린다 — ⓐ 주기 내 즉시 중단(신호 `aborted_429` 신설) ⓑ 주기 간 지수 백오프(1→2→4) | 현행 방어는 **사람이 지켜보는 실행** 전제다: 연속 429 5회면 `run()`이 중단하고 로그로 "30분~1시간 후 다시"라고 말한다 — 읽는 사람이 있을 때만 작동하는 방어다. 무인에서는 두 구멍이 난다. **ⓐ 같은 주기 안:** `run()`이 중단 사실을 호출자에게 **구별 가능하게 알리지 않아**(`stats["error"] += 1`이 전부) `_run_grouped`가 차단 상태에서 다음 채널로 넘어가 계속 두드린다 — 이것은 스케줄러 이전에 **재생목록(FR24)·검색(FR34) 추출에 이미 있던 결함**이며, 사람이 보고 있으면 취소할 수 있었을 뿐이다. `stats["cancelled"]`와 같은 계열의 불리언 표식을 추가해 그룹 루프가 즉시 멈추게 한다. **ⓑ 다음 주기:** 아무 조치가 없으면 일주일 뒤 같은 조건으로 다시 들어간다. 차단은 "회복될 때까지 누적되는 압력"이므로 **성공한 주기만** 백오프를 0으로 되돌리고, 실패는 1→2→4주기로 물러선다(상한 4 = **기본 주기 3일 기준 최대 12일**. 주 단위 기본이었다면 약 한 달이라 과했다 — 기본 주기 3일이 이 상한을 합리적 범위에 두는 전제다, FR37.3ⓓ). 백오프 중에도 **사용자의 수동 추출은 막지 않는다** — 사람은 상황을 보고 판단할 수 있고, 막으면 복구 수단까지 빼앗는 것이다 (FR37.9~37.10) |
| DQ-49 | 쿠키 만료·백오프는 **상태**로 표현한다 — 기계가 사용자 설정(`enabled`)을 끄지 않는다 | "쿠키 만료 시 스케줄 정지"를 `enabled = false` 기록으로 구현하면 ⓐ 사용자가 **켜 둔 적 없는 상태**로 되돌아가 나중에 "왜 안 돌지"를 겪고 ⓑ 쿠키를 고쳐도 **누군가 다시 켜야** 하며 ⓒ 설정 파일만 봐서는 사용자 의도와 기계 개입을 구별할 수 없다. 대신 매 틱마다 `cookie_health.get_status().warning`을 **조건으로 평가**하고 `paused_reason`으로 노출한다 — FR19.3이 쿠키 갱신 시 warning을 자동 해제하므로 스케줄은 **스스로 재개**한다. 같은 원칙이 `skip_cycles`(429)와 busy 경합에도 적용된다: 전부 판정 입력이지 설정 변경이 아니다. NFR3 ⓓ로 이 원칙을 못박았다 (FR37.11·37.12). **v5.10 보강(QA F1):** 원칙을 말로만 두면 저장 경로에서 깨진다 — 주기 마감이 *주기 시작 시점 스냅샷*을 통째로 저장해 그 사이의 `enabled`·`interval_days`·`max_videos_per_cycle` 변경을 되돌려 썼다(끄기가 듣지 않고, 원복 방향이 하필 더 자주·더 많이 도는 쪽). 그래서 소유를 **코드 상수**(`USER_FIELDS`/`SCHEDULER_FIELDS`)로 못박고 기계 쪽 쓰기를 전부 `save_scheduler_state()` **재적재 후 병합**으로 돌렸다. 같은 이유로 백오프 skip은 `last_result`(직전 429 증거)를 덮지 않고 `last_skip_at`만 남긴다 |
| DQ-50 | `run-now`는 **별도 실행 경로가 아니다** — "지금 도래시키기"로 구현한다 | 수동 트리거를 "바로 추출 시작"으로 구현하면 busy·쿠키·백오프·예산 검사를 **두 벌** 갖게 되고, 경험상 안전장치는 반드시 둘 중 한쪽에서 빠진다(무인 기능에서 그 누락은 429 차단으로 돌아온다). `request_now()`는 `last_run_at`을 간격만큼 과거로 당기고 틱 이벤트를 깨우기만 하며, 실제 실행은 평상시와 **완전히 같은 `decide_cycle` → `run_cycle`** 경로다. 부수 효과로 검증이 가능해진다 — 최소 주기가 7일이라 `run-now` 없이는 V-D21을 실행할 방법이 없다. `skip_cycles`를 소모하지 않는 것은 사용자가 상황을 보고 누른 예외 실행이기 때문이다 (FR37.15) |
| DQ-51 | `audit`와 `doctor`를 **두 명령으로 분리**한다 — 하나로 합치기를 기각 | 성격 차이(문서 vs 데이터)가 아니라 **실행 환경이 갈린다**는 것이 결정 근거다. `audit`의 입력은 저장소 텍스트뿐이라 `output/`·Docker 볼륨·네트워크가 **없어도 완결**되고(CI 컨테이너·커밋 훅) 실측 0.3초다. `doctor`의 입력은 사용자 실데이터(`output/` 293MB·98채널)라 **마운트가 없으면 아무것도 못 한다**. 합치면 ⓐ CI에서 반드시 절반이 실패하거나 조용히 스킵되고(스킵은 "이상 없음"으로 읽힌다 — FR35.12 F-1과 같은 실패 유형) ⓑ 종료코드 하나가 "문서가 어긋났다"와 "데이터가 깨졌다"를 뒤섞어 **훅이 무엇을 막아야 하는지** 정할 수 없게 되고 ⓒ 실행 빈도가 다르다(audit = 변경마다 · doctor = 릴리스·운영 점검 시). 반대로 **모듈은 하나(`selfcheck.py`)로 둔다** — 발견 표현·심각도·예외 적용·출력·종료코드는 두 명령이 한 글자도 달라서는 안 되고, 공통 계약이 두 파일로 흩어지는 것이 곧 다음 drift다 (FR38.2) |
| DQ-52 | 감사는 **고치지 않는다** — `--fix` 류를 만들지 않고, 종료코드로만 말한다 | 문서가 낡은 것인지 코드가 틀린 것인지는 **기계가 정할 수 없다.** spec-sync가 이미 "문서와 코드 중 어느 쪽이 진실인지 임의로 정하지 않는다"를 원칙으로 세웠고, 이번 세션에서 드러난 §6의 `loadSchedule`→`schLoad` 같은 사례도 "문서를 코드에 맞추는" 것이 정답처럼 보이지만 **반대 방향일 수도 있다**(이름을 문서가 요구한 대로 바꾸는 것이 옳을 수 있다). 잘못된 방향의 자동 수정은 정본을 오염시켜 다음 세션이 그 오염을 근거로 또 틀린다 — `tickers`가 2주간 "값이 있으니 맞겠지"로 넘어간 것과 같은 구조다. **종료코드 체계를 4단으로 나눈 것**도 같은 이유다: `0`(이상 없음)과 `3`(확인 못 함)을 같은 코드로 내는 순간 도구는 거짓말을 시작한다. 경고(`1`)를 기본 차단선에서 뺀 이유는 경고가 상시 몇 건 존재하는 것이 정상이고(실측: 미인덱싱 12편·로그 중복 24행) **상시 빨간 게이트는 꺼진 게이트**이기 때문이다 (FR38.1·38.6) |
| DQ-53 | **정밀도 우선 + 2층 예외 모델** — 판정 불가는 침묵, 예외는 먼저 문서 안의 기존 표기로 표현하고 남는 것만 `audit_waivers.yaml` | 오탐을 내는 감사 도구는 **아무도 보지 않는다** — 그 상태는 도구가 없는 것보다 나쁘다(있다고 믿기 때문이다). `tickers`가 2주간 100% 오탐으로 방치된 메커니즘이 정확히 이것이다. 실측이 이 위험을 수치로 보여줬다: 소박한 정규식 시제품은 §6 트레이서빌리티 행을 FR 정의 행으로 착각해 **FR 중복 15건을 허위 보고**했고, §6 백틱 토큰 224개 중 **140개가 판정 대상이 아닌 잡음**(CSS 선택자·필드명·`""`)이었으며, REQUIREMENTS §8의 역사 표 때문에 **DQ 중복 6건**이 허위로 잡혔다. 그래서 ⓐ 스코프를 절 단위로 못박고 ⓑ 토큰을 분류해 판정 가능한 것만 세고 ⓒ 나머지는 "의심"으로도 올리지 않는다(FR34.3의 "판정 불가를 제외 근거로 쓰지 않는다"와 같은 원칙). **예외를 별 파일부터 만들지 않은 이유:** 의도적 예외는 이미 문서에 한국어로 적혀 있다(`(구현 없음 — 범위 명시)`·`**테스트 미구현**`·`(구현 예정)`·§9.1a 위치 열). 예외는 그것을 설명하는 문장 **옆에** 있어야 함께 갱신된다 — 별 파일로 떼면 그 파일이 또 drift한다. 2차 파일은 개념명(`Reprocessor`)처럼 1차로 표현할 수 없는 잔여만 담고, **대응 발견이 없는 waiver 자체가 경고**다(예외에 수명을 신고하게 만드는 장치) (FR38.8~38.10) |
| DQ-54 | 번호 **"다음 값" 포인터를 손으로 들지 않는다** — 정본에서 계산해 포인터와 대조 | V-U 번호는 사람이 문서에 적어 둔 "다음 신규 번호는 V-U35" 같은 포인터로 관리됐고, 그것이 **구 V-U12/13 중복과 V-U14~16 누락의 직접 원인**이었다(2026-09-20 전면 재정렬). 같은 패턴이 pytest 기준선에서 반복됐다 — **5곳 중복 · 한 세션에 4번 어긋남**(124 낡음 → 140 vs 163 두 문서 불일치 → 180 → 185). 공통 구조는 "계산 가능한 값을 사람이 복제해 들고 있다"이며, 해법은 값을 없애는 것이 아니라(포인터는 다음 작업자에게 유용하다) **기계가 계산값과 대조**하는 것이다. V-U의 정본은 `tests/test_unit.py`의 섹션 헤더 주석이고(2026-09-20 확정), DQ의 정본은 DESIGN §10, FR의 정본은 REQUIREMENTS §3이다. 포인터가 사는 곳(DESIGN §9.1·spec-sync SKILL.md)은 **전부 검사 대상**이다 (FR38.11 `audit.vu-numbers`·`audit.next-pointers`·`audit.pytest-baseline`) |
| DQ-55 | `doctor`는 **전수**로 돌고, ChromaDB는 **`chroma.sqlite3` 읽기 전용 sqlite 직조회**다 — 샘플링·증분과 chromadb 클라이언트를 모두 기각 | **전수:** 실측 98채널 디렉터리 워크 + state 98개 + 파일 1,980개 stat = **0.02초**, chroma 91개 조회 = **0.32초**. 293MB의 대부분은 자막 본문과 sqlite이고 점검은 본문을 **열지 않는다**(경로·크기·JSON 메타·인덱스 id만 본다). 이 비용에서 샘플링은 **놓침만 만들고 얻는 것이 없다** — 그리고 이 도구의 목적은 "드문 이상의 발견"이라 샘플링과 목적이 정면으로 충돌한다. 증분은 "직전 상태"라는 스냅샷을 요구하므로 리포트 저장소를 불러들인다(DQ-56에서 기각). **클라이언트 기각:** `KLIndexer._get_client()`는 `self.dirs["chroma"].mkdir(parents=True, exist_ok=True)`를 하고 `chromadb.PersistentClient`는 스키마를 쓴다 — 읽기 전용 점검을 평소 경로로 구현하면 **`chroma/`가 없는 6채널에 빈 디렉터리와 sqlite가 생긴다**(실측). "읽기 전용"이 선언이 아니라 코드 수준에서 깨지는 것이다. 대신 `sqlite3.connect("file:…?mode=ro", uri=True)` + `embedding_metadata`로 필요한 것(`video_id` 집합·청크 수)을 전부 얻는다(실측 91/91 성공·임베딩 6,688·0.32초). 대가는 chromadb 내부 스키마 의존이며, 그래서 **테이블·컬럼이 다르면 오류가 아니라 "검사 건너뜀(INFO)"** 이다 — 라이브러리 업그레이드가 감사 실패로 나타나면 도구가 신뢰를 잃는다 (FR38.14~38.15) |
| DQ-56 | **자동 실행하지 않는다**(스케줄러가 부르지 않는다) + "`sub_type` 분포 급변 감지"를 기각하고 **내적 모순 감지**로 대체 | **자동 실행:** 읽기 전용이라 NFR3ⓐ(멱등·가산)보다 약한 작업이지만, 자동 실행이 의미를 가지려면 결과를 **남기고 비교하고 노출해야** 한다 — 리포트 파일·이력·알림·대시보드 UI. 그것들은 이번 범위에서 명시적으로 배제한 것들이고, 없이 자동 실행하면 아무도 읽지 않는 stdout만 쌓인다(= `note`·`tickers`가 죽어 있던 패턴의 재현). 그래서 호출 주체는 사람과 개발 하네스 게이트(§11.2)로 한정한다. NFR3의 예외는 **FR37 하나로 유지**되고 이 FR은 예외를 넓히지 않는다. **급변 감지 기각:** "분포가 갑자기 변했다"를 판정하려면 기준 스냅샷이 필요하고, 그 저장이 바로 위에서 배제한 것이다. 게다가 데이터가 시계열을 주지 않는다 — 실측 ⓐ `extract_log.csv`에 **timestamp 열이 없다**(열은 `video_id,upload_date,title,action,sub_type,status,basename`) ⓑ `members_only` 레코드는 `extracted_at`이 **빈 문자열**이고 `upload_date`가 `"00000000"`이다(8/8). 즉 멤버십 감지 고장을 시계열로 잡을 방법이 애초에 없다. **대체:** 같은 데이터 안의 **모순**을 본다 — `extract_log.csv`의 `error:` 사유를 **현행 판정 규칙(`video_access.is_members_message`)으로 재판정**해 참이면 "감지 규칙이 그 사유를 놓쳤다"는 확정적 모순이다. 기준선·스냅샷이 필요 없고 오탐이 없으며, **실제로 2026-09-09 결함의 화석 1건을 적발했다**(`error:ERROR: [youtube] aetOCkgzurM: … VIP 회원 등급 이상의 채널 회…`). 같은 발상이 `doctor.meta-fields`의 "값이 있는데 distinct 1"이다 — `tickers` 오탐(26개 전부 같은 모양)을 **필드 하드코딩 없이** 잡는다 (FR38.13·38.16) |
| DQ-57 | 자막 교정(다음 단계)용 검사는 **자리만 열고 지금 만들지 않는다** | 교정 기능이 필요해질 검사는 짐작이 간다(죽은 규칙·오적용·stale 교정본·hold 적체). 그래도 지금 이름·스키마를 정하지 않는 이유는 이 프로젝트가 **미리 만든 것이 죽어 있던 전례를 두 번** 겪었기 때문이다: `note`는 쓰기만 하고 읽는 곳이 0이었고(FR36까지), `tickers`는 필드와 추출기가 있었는데 값이 전부 오탐이었다(FR12.2까지, 2주 방치). 대상 기능의 데이터 모양이 확정되기 전에 검사를 박으면 ⓐ 아무도 읽지 않는 죽은 검사가 되고 ⓑ 그 검사 ID가 **다시 drift 원천**이 된다(FR38.4는 공개한 ID를 바꾸지 말라고 요구한다 — 그래서 잘못 정한 이름의 비용이 크다). 열어 두는 것은 **구조뿐**이다: `DOCTOR_CHECKS` 레지스트리 + `Finding` 계약 + waiver 규약이 있으므로 교정 FR이 확정될 때 `doctor.correction-*` 계열을 **함수 1개 + 표 1행**으로 추가할 수 있고, FR38.19가 그때 요구할 것(합성 픽스처 테스트 + 실데이터 발견 건수 실측)을 미리 못박아 뒀다 (FR38.18~38.19) |

---

## 11. 개발 하네스 (Development Harness)

> **질의 하네스(FR10, kl_harness.py)와 별개.** 개발 하네스는 이 프로젝트를 개발·검증·운영하는
> Claude Code 에이전트/스킬 체계다. 트리거 규칙은 프로젝트 루트 `CLAUDE.md` 참조.

### 11.1 구성

| 자산 | 경로 | 역할 |
|---|---|---|
| 오케스트레이터 | `.claude/skills/yt-subs-orchestrator/` | 기능 추가/수정 워크플로우: 스펙→설계→구현→검증→문서 동기화 |
| spec-guardian | `.claude/agents/spec-guardian.md` | PRD·DESIGN 정합성, Spec-First 강제, 트레이서빌리티 관리 |
| pipeline-engineer | `.claude/agents/pipeline-engineer.md` | 파이프라인·대시보드 구현 (재빌드·429 보호 규칙 준수) |
| qa-verifier | `.claude/agents/qa-verifier.md` | 경계면 교차 비교·mock 검증·카나리아·회귀 |
| spec-sync 스킬 | `.claude/skills/spec-sync/` | FR↔설계↔코드 정합 감사 절차 |
| pipeline-verify 스킬 | `.claude/skills/pipeline-verify/` | 검증 런북 (V-U/V-D 게이트 실행) |
| extraction-ops 스킬 | `.claude/skills/extraction-ops/` | 429·쿠키·멤버십 운영 런북 |
| dashboard-dev 스킬 | `.claude/skills/dashboard-dev/` | 대시보드 작업 규칙 |

### 11.2 검증 게이트 매핑

| 게이트 | 실행 주체 | 대응 검증 |
|---|---|---|
| 정적 | pipeline-verify ① | py_compile 전체 |
| 단위 | pipeline-verify ② | V-U1~V-U36 (§9.1a — pytest + mock 스크립트, 네트워크 없음). V-U3은 테스트 미구현, V-U8·V-U9는 mock 스크립트, V-U35~36은 FR38 정합 감사·건전성 점검(구현됨) |
| 빌드 | pipeline-verify ③ | docker build |
| 카나리아 | pipeline-verify ④⑤ | V-D2 + 회귀(스킵 수 유지·429 없음) |
| 인덱스/스모크 | pipeline-verify ⑥⑦ | V-D9 일부 (curl /videos·/search) |
| 문서 정합 | **`./yt.sh audit`**(FR38) + spec-sync | **종료코드 2 없음**(오류 0건) — 기준선 5곳 일치·V-U 번호 정본 대조·FR/DQ 번호·트레이서빌리티 실재·버전 헤더·CLI 명령. 경고(종료코드 1)는 사람이 판단한다. 기계 판정으로 옮기기 전에는 사람이 기억해야 했고 한 세션에 4번 어긋났다(DQ-54) |
| 데이터 건전성 | **`./yt.sh doctor`**(FR38, 선택) | 오류 0건 + 경고 건수가 회귀 기준선(`.claude/agents/qa-verifier.md`)과 일치. 무인 운영(FR37)을 켠 뒤에는 릴리스·주기 점검 시 실행한다 |

실행 모드: **서브 에이전트 오케스트레이션** (파일 기반 산출물 전달, `_workspace/`).
