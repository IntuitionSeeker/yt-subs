# DESIGN — YouTube 자막 수집 · 지식층 파이프라인

> **버전:** v4.5  
> **작성일:** 2026-08-09  
> **연계 문서:** REQUIREMENTS.md v4.5 (FR1~FR23)  
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
> **v4.8:** 채널 폴더(FR25) — `ChannelRegistry.set_group`(channels.yaml `group` 필드), `POST /channels/group`, `/channels/stats.group`, 라이브러리 폴더 섹션·병합 전체 보기(프론트 병합·원채널 배지·자막/삭제는 영상별 원채널로 라우팅), `_run_playlist` 신규 채널 자동 폴더 지정

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
| `add(url, lang)` | channels.yaml 등록, 채널명 반환 |
| `remove(name)` / `list()` / `get(name)` / `names()` | 등록 해제·조회 |

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
| `process_video(vid, action, content_type, playlists_map, info, date_range)` | 단일 영상: (`info` 미전달 시) full info 조회 → **진행/예약 라이브 가드**(`live_status`∈{is_live,is_upcoming} → `"live_wait"` 반환, state 미기록 — FR16.5) → **기간 판정(`_out_of_range`)** → 자막 선택 → VTT→SRT→TXT → meta/desc 저장 → state 기록. `was_live` 보정(FR16.4). `info`가 주어지면 재조회 생략(FR17.2 단일영상 워커가 선조회분 재사용). 반환 `"ok"` \| `"no_sub"` \| `"date_skip"` \| `"live_wait"` — 멤버십은 반환값이 아니라 **예외 경로**(`_is_members_only`→`_mark_skip`)로만 분류된다 |
| `_out_of_range(upload_date, date_range)` | 기간 조건(`{"since","until"}`, YYYYMMDD, 경계 포함) 판정. 범위 밖이면 자막을 받지 않고 `extract_log.csv`에 `status="date_skip"` 1행만 기록하고 **state.json에는 기록하지 않는다**(조건을 바꾼 다음 실행에서 다시 대상이 되어야 하므로). `upload_date`가 없거나 `"00000000"`이면 판정 불가 → 통과 (FR2.6과 같은 보수 원칙) |
| `_fetch_vtt(url)` | 자막 직접 다운로드: 자체 딜레이 + 쿠키 + 브라우저 UA (FR13.4) |
| `_report(progress, phase, done, total, current_title, stats)` | 진행 콜백 호출 헬퍼(FR18.1~2). `progress=None`이면 즉시 `True` 반환(CLI 경로 무영향), 콜백 내부 예외는 삼키고 "계속"으로 간주. `stats`는 얕은 복사본으로 전달(폴링 스레드의 직렬화 레이스 방지) |
| `run(force_vid, limit, progress, entries, pl_map, date_range)` | 채널 루프: 429 지수 백오프(FR14.3)·배치 휴식(FR14.2)·연속 429 중단(FR13.5)·카나리아 `--limit`(FR14.5)·멤버십 감지 스킵. **신규 4인자가 모두 None이면 기존 CLI 동작과 완전 동일**(FR18.1, 반환 dict에 `date_skip:0` 키만 추가). `entries`/`pl_map`이 주어지면 `scan_channel()`/`scan_playlists()`를 건너뛰고 대시보드 스캔 캐시를 그대로 사용(DQ-13). `date_range`는 해석 없이 `process_video`로 전달(DQ-12). `progress`가 False를 반환하면 우아한 취소 — 루프 break → `finishing` 보고 → `state.save()` → (`pl_map` 있으면) `_backfill_meta()` → 최종 로그 → `stats["cancelled"]=True`로 반환 (FR18.2). 인덱싱은 이 함수 범위 밖(DQ-14) |

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
| `extract_tickers(text)` | 한국 종목코드(6자리)·미국 티커($XXX) 추출 (FR12.2) |
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
| `list_videos(since, until)` | meta/ 순회 → video_id·title·upload_date·basename·tickers·playlists·content_type·**sub_type**·url (날짜 역순). `sub_type`은 라이브러리 뷰의 📝수동/🤖자동 뱃지용 (FR20.2) |
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
| `POST /channels/delete` | 구현됨 | 채널 삭제 (FR21.2) — `ChannelRegistry.remove`로 등록 해제(없으면 404). `purge=true`면 `channel_dir().resolve()`가 `OUTPUT_BASE` 하위인지 재확인 후 `shutil.rmtree` — 등록 해제만으로는 `output/` 폴더가 disk에 남지만 `/channels/stats`가 registry 기준이라 라이브러리 UI에서는 즉시 사라진다. `MANAGER.is_busy()`면 409 |

> 요청 모델(Pydantic): `ScanRequest{url}` · `Filters{latest, since, until, categories, include_members, keyword}` · `ExtractRequest{url, scan_id, filters, index=True}` ·
> `VideoDeleteRequest{channel, basename}` · `ChannelDeleteRequest{channel, purge=False}`.
> `JobBusyError`→409 `{detail, job}`, `ValueError`→400 `{detail}`로 매핑한다.

### 2.10 dashboard/jobs.py — FR17~18

| 항목 | 설계 |
|---|---|
| `classify_url(url)` | 영상(watch?v=·youtu.be·/shorts/·/live/에서 11자 ID) vs 채널(@핸들·/channel/UC), 판별 불가 시 `ValueError`→400 (FR17.1). 퍼센트 인코딩 핸들도 디코드 후 판별 |
| `JobManager` | 모듈 싱글턴(단일 uvicorn 프로세스 전제). `threading.Thread(daemon=True)` 1개, `threading.Event` 취소, `RLock` 하 job dict 갱신. **점유 플래그(`_busy`) 1개를 추출·스캔이 공유** — 추출 중 `POST /extract/scan`도, 스캔 중 `POST /extract`도 409다(스캔은 1+N회 요청이라 결코 가볍지 않고, 동시 호출은 429 위험을 키운다). `start()`는 **요청 검증(400) → 점유 획득(409)** 순서라 잘못된 요청이 점유를 남기지 않는다 |
| 스캔 캐시 | `scan_id → {channel, url, videos_view, entries, pl_map, created_at}` TTL 10분 (FR17.3·DQ-13). API 응답용 `videos_view`뿐 아니라 **원본 flat `entries`와 `pl_map`을 함께 보관**하는 것이 핵심이다 — 추출 시 `Extractor.run(entries=, pl_map=)`으로 그대로 넘겨 재스캔(1+N회 요청 중복)을 없앤다. 만료·부재 시 `ValueError`→400 |
| `apply_filters(videos, f)` | ⓒ카테고리(OR·재생목록 제목 완전일치, 카테고리 선택 시 재생목록 없는 영상 제외) → ⓓ멤버십(`include_members=false`면 제외) → ⓔ키워드(소문자 부분일치) 를 AND로 적용한 뒤 **마지막에 `out[:latest]`**. ⓑ기간은 여기서 적용하지 않는다(DQ-12). 프론트 `applyFilters()`와 동일 순서가 계약이다 (DQ-15) |
| 멤버십 판정 | 스캔 엔트리 `availability`(`subscriber_only`·`needs_auth`·`premium_only` 부분일치) **OR** `state.sub_type=="members_only"` 합집합 (FR17.6). `include_members=false`면 여기서 제외되므로 `decide()`의 FR19.1 재시도에 도달하지 않는다 |
| 채널 워커 | `apply_filters` 결과 id 집합으로 원본 `entries`를 **같은 순서로** 재구성 → 미등록 채널이면 `registry.add(원본 URL)`(기존 항목이면 호출하지 않아 `added_at`·`note` 보존) → `Extractor.run(entries=, pl_map=, date_range=, progress=cb)`. cb는 락 하에 job을 갱신하고 `not cancel.is_set()`을 반환한다 |
| 단일영상 워커 | full info 1회 조회 → **채널 등록 URL 조립**: `uploader_id`가 `@`로 시작하면 `https://www.youtube.com/{uploader_id}`를 만들고, 아니면 `channel_url`→`uploader_url` 폴백. `uploader_id`(`@handle`)를 그대로 `registry.add()`에 넘기면 `normalize_url()`이 `@handle/videos`라는 깨진 URL을 저장해 이후 모든 `run`이 실패한다 → 조립 필수 (FR17.2). 이후 `process_video(info=선조회분)`. 재생목록 매핑은 생략하고 다음 전체 run의 백필(FR15.5)로 채운다 |
| 후처리 | `index` 옵션이 켜져 있고 **취소가 아니며** 신규+수정 > 0일 때만 같은 스레드에서 `KLIndexer.index_all()` (FR17.9·DQ-14) |
| 지연 임포트 가드 (F-6) | `extractor` 모듈은 `_app_extractor()` 헬퍼로만 로드한다. yt-dlp 실행이 legacy 플러그인 탐색으로 site-packages의 `ytdlp_plugins` 경로를 등록하면 이후의 맨 `import extractor`가 그 서브패키지로 **섀도잉**된다 — 실증: 첫 스캔은 200, 같은 프로세스의 두 번째 스캔이 ImportError (2026-08-04 발견). 헬퍼는 sys.modules 캐시에 올바른 모듈(`Extractor` 속성 보유)이 있으면 재사용하고, 오염 시 앱 루트를 sys.path 최우선으로 되돌려 재임포트한다. mock 테스트의 가짜 `extractor` 주입과도 호환 |
| `is_busy()` (FR21.4) | `_busy` 플래그를 락 하에 읽어 반환하는 공개 헬퍼. `server.py`의 삭제 엔드포인트가 진행 중인 추출·스캔과 파일 정리가 겹치지 않도록 이 값으로 409를 판단한다(사설 속성 직접 접근 대신) |

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

### 2.12 yt.sh — FR8

이미지 자동 빌드, channels.yaml 파일 보장, cookies.txt 존재 시 ro 마운트, serve 시 8800 포트, HF 캐시 공유, ANTHROPIC_API_KEY 전달.

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
  ├ 8~12개(랜덤)마다 45~90초(랜덤) 휴식 (FR14.2)
  └ process_video(vid, action, content_type, pl_map)
      ├ 성공   → stats, 429카운터 리셋
      ├ 멤버십 → _mark_skip(members_only)
      └ 429    → 지수 백오프 → 같은 영상 1회 재시도(예산 소비),
                 재실패 시 포기·다음 영상, 연속 5회 시 중단 (FR13.5·14.3)
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

---

## 4. 출력 폴더 구조

```
output/
├── .cookie_status.json          # 쿠키 경고 상태 (FR19.2, 컨테이너 공유)
├── 두두감자/
│   ├── srt/  txt/  desc/  meta/ # 자막·전문·설명·메타
│   ├── chroma/                  # 채널별 독립 KL
│   ├── state.json               # 증분 상태
│   ├── playlists.json           # video_id→재생목록 매핑 (FR15.1)
│   ├── extract_log.csv
│   └── review_report.csv
└── 다른채널/ (완전 격리, FR7.4·NFR8)
```

---

## 5. 스키마 정의

### 5.1 channels.yaml (FR7.1)

```yaml
channels:
  두두감자:
    url: https://youtube.com/@두두감자/videos
    lang: ko
    added_at: "2026-06-21"
    note: ""
```

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
  "kind": "channel_run | single_video",
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
- `status`는 `done|cancelled|error`로 끝나며 마지막 job은 메모리에 유지된다. `{"status":"idle"}`는 프로세스 기동 후 한 번도 작업이 없었을 때만.

### 5.8 output/.cookie_status.json (FR19.2)

```json
{ "invalid": true, "message": "…no longer valid…", "detected_at": "2026-08-02T10:00:00" }
```

`detected_at`은 **최초 감지 시각**이다(중복 기록 방지 — §2.11). `GET /cookies` 응답은 이 파일과 키가 다르며(`invalid`→`warning`), 쿠키 mtime과 비교해 자동 해제를 계산한다.

### 5.9 대시보드 API (FR17~20)

| 메서드/경로 | 요청 | 응답(성공) | 오류 |
|---|---|---|---|
| `POST /extract/scan` | `{url}` | `{scan_id, channel, videos:[{id,title,content_type,playlists,members_only,extracted}], playlists:[제목…]}` | 400 판별불가·영상 URL·핸들 추출 실패 / 409 작업(추출·스캔) 실행 중 |
| `POST /extract` | `{url, index}` 또는 `{scan_id, filters:{latest,since,until,categories,include_members,keyword}, index}` | 202 `{job}` | 409 실행중 `{detail, job}` / 400 (아래 판정 규칙) |
| `GET /extract/status` | – | `{job}` 또는 `{job:{status:"idle"}}` | – |
| `POST /extract/cancel` | – | `{cancelled: bool}` (실행 중 작업이 없으면 `false`) | – |
| `GET /cookies` | – | `{present, mtime, warning, warning_message, detected_at}` | – |
| `GET /channels/stats` | – | `{channels:[{name,url,lang,added_at,extracted,members_only,no_sub,total_known,last_extracted}]}` | – |
| `GET /subtitle` | `?channel=&basename=` | `{basename, text}` (txt 전문) | 400 경로탈출(`channel`·`basename` 양쪽 검사), 404 파일 없음 |

**`POST /extract` 요청 판정 규칙 (전부 400 `{detail}`):**

1. `url`과 `scan_id`가 **둘 다 있음** → 400 "url과 scan_id는 함께 지정할 수 없습니다."
2. **둘 다 없음** → 400 "url 또는 scan_id 중 하나가 필요합니다."
3. `url`이 채널로 분류됨 → 400 "채널 URL은 `/extract/scan`을 먼저 호출하세요." (무조건 전체 추출 폭주 방지 — 채널은 반드시 스캔·조건 단계를 거친다)
4. `url` 판별 불가 → 400 (FR17.1)
5. `scan_id`가 **만료(TTL 10분 초과)되었거나 존재하지 않음** → 400 "scan_id가 만료되었습니다. 다시 스캔하세요." (410 신설 없이 §5.9 오류 집합 유지)

**409 규칙:** 추출·스캔이 점유 플래그 하나를 공유하므로 `POST /extract`와 `POST /extract/scan`은 **서로에 대해서도** 409를 낸다.
409 본문은 `{detail, job}`이며 `job`은 직전 job 스냅샷 또는 `null`(스캔만 돌던 중이면 null일 수 있음)이다.
요청 검증(400)이 점유 검사(409)보다 먼저라 잘못된 요청은 점유를 남기지 않는다.

**`GET /channels/stats` 계산 정의:** 채널 목록·`url`·`lang`·`added_at`은 channels.yaml(registry) 기준,
통계는 state.json 집계 — `extracted = count(sub_type ∈ {manual, auto})`, `members_only`, `no_sub = count(sub_type=="none")`,
`total_known = len(state)`, `last_extracted = max(extracted_at)`(빈 문자열 제외, 없으면 `""`, ISO 문자열).

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
├── main.py                # CLI 진입점
├── config.py              # 상수·경로·yt-dlp 옵션·429/쿠키 설정
├── channel_registry.py    # FR7
├── extractor.py           # FR1·2·13~17
├── state_manager.py       # FR2·19.1
├── meta_collector.py      # FR3·12.2·15.2
├── subtitle_utils.py      # VTT→SRT→TXT·청킹·파일명
├── quality_checker.py     # FR4
├── kl_indexer.py          # FR6·15.3
├── kl_query.py            # FR9·12·15.4
├── kl_harness.py          # FR10 질의 하네스 (제품 내)
├── cookie_health.py       # FR19 (YDLLogger·상태 영속·get_status)
├── dashboard/
│   ├── server.py          # FastAPI (FR11·17~20)
│   ├── jobs.py            # JobManager·classify_url·apply_filters (FR17~18)
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
```

사전 준비: `export ANTHROPIC_API_KEY=…`, (선택) `cookies.txt` 배치 — COOKIES_GUIDE.md.

> **대시보드 개발 시**: `dashboard/`를 라이브 마운트(`-v $PWD/dashboard:/app/dashboard`)하면
> index.html 수정이 재빌드 없이 반영된다. 파이썬 코드 수정은 `docker build` 필수 (이미지에 구워짐).

---

## 9. 검증 설계 (Verification Design)

### 9.1 단위 테스트 (tests/test_unit.py — 네트워크 불필요)

```
V-U1 make_basename        파일명 형식·srt/txt 동일성
V-U2 vtt_to_srt           변환 정확성 · 자동자막 dedup 2패턴(누적형·슬라이딩형 F-7)
V-U3 subtitle_priority    수동 우선 폴백
V-U4 is_updated           수정 감지 + FR2.6 날짜 미제공 무변경
V-U5 quality_rules        SUSPECT 판정
V-U6 chunk_by_srt         120초 윈도우·start_seconds
V-U7 extract_handle       URL→채널명
V-U8 scan 병합·live 필터   videos+streams 병합, is_live 제외, 중복 시 video 우선 (FR16)
V-U9 playlists 매핑·백필   복수 소속 보존, 탭 없음 빈 매핑, 백필 갱신·무변경 스킵 (FR15)
V-U10 members 재시도       쿠키 유/무별 decide 판정 (FR19.1) — 구현됨
V-U11 classify_url        영상/채널/판별불가 8케이스 (FR17.1) — tests/test_unit.py 편입 완료
V-U12 reflow_sentences     문장 단위 개행 한·영/무공백 한글/소수점 보존 (FR23) — 3케이스
V-U13 live guard           is_live/is_upcoming → live_wait + state 미기록, was_live 정상 경로 (FR16.5)
                          (2026-08-04, 25 passed로 게이트 검증)
```

### 9.2 통합 테스트 (tests/test_integration.py — 네트워크 필요)

V-I1 등록+폴더 · V-I2 2회차 SKIP · V-I3 수정 감지 · V-I4 SUSPECT 기록 ·
V-I5 재추출 갱신 · V-I6 2컬렉션 생성 · V-I7 채널 격리 · V-I8 재시작 영속

### 9.3 대시보드·기능 검증 (V-D — FR15~20)

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
| DQ-17 | 재생목록 태깅은 병합 full-map으로만 | `_backfill_meta(mapping)`은 **맵에 없는 vid의 meta.playlists를 `[]`로 덮어쓴다**(전체 맵 전제 설계). 따라서 재생목록 추출(FR24.4)에서 {대상 vid: [재생목록]}만 담은 부분 맵을 `run(pl_map=)`에 넘기면 그 채널의 기존 카테고리가 전부 소실된다. 반드시 채널의 기존 playlists.json(없으면 기존 meta들에서 재구성)에 재생목록 제목을 **병합한 전체 맵**을 전달한다 (FR24.4) |
| DQ-16 | `--limit` 예산 기준 | limit은 "성공 추출 수"가 아니라 **요청 소비 수** 상한이다. `extract_info` 요청을 쓰는 모든 경로(정상·무자막·**멤버십 재시도(FR19.1)**·오류·`date_skip`)가 예산을 소비하고, 요청을 쓰지 않는 state `skip`은 소비하지 않는다. FR14.5의 목적이 429 방어(요청 총량 통제)이기 때문이며, FR19.1 도입으로 멤버십 재시도가 매 run 요청을 쓰게 되면서 성공-기준 카운트로는 카나리아가 실제 요청 수를 통제하지 못한다 (FR14.5) |

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
| 단위 | pipeline-verify ② | V-U1~11 (mock, 네트워크 없음) |
| 빌드 | pipeline-verify ③ | docker build |
| 카나리아 | pipeline-verify ④⑤ | V-D2 + 회귀(스킵 수 유지·429 없음) |
| 인덱스/스모크 | pipeline-verify ⑥⑦ | V-D9 일부 (curl /videos·/search) |
| 문서 정합 | spec-sync | 트레이서빌리티 불일치 0건 |

실행 모드: **서브 에이전트 오케스트레이션** (파일 기반 산출물 전달, `_workspace/`).
