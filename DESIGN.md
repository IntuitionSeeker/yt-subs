# DESIGN — YouTube 자막 수집 · 지식층 파이프라인

> **버전:** v5.5  
> **작성일:** 2026-08-09  
> **연계 문서:** REQUIREMENTS.md v5.5 (FR1~FR34)  
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
| `add(url, lang)` | channels.yaml 등록, 채널명 반환 |
| `remove(name)` / `list()` / `get(name)` / `names()` | 등록 해제·조회 |
| `resolve_name(url)` | URL → **등록명 역조회** (`extract_handle` 값 일치, 대소문자 무시). 실패 시 `extract_handle` 폴백 (FR32.2·DQ-19) |
| `rename(old,new)` / `set_group(name,group)` / `set_channel_id(name,id)` | 이름 변경(FR31.1)·폴더 지정(FR25.1)·RSS channel_id 캐시(FR29.1) |
| `set_auto_run(name, flag)` | `auto_run` 플래그 기록 (FR34.7). **`True`이면 필드를 제거**한다 — 기본값이 `True`이므로 참값을 쓰면 yaml에 의미 없는 잡음이 쌓인다(`set_group`의 빈 값 처리와 동일 패턴). 미등록 채널은 `KeyError` |
| `names(auto_only=False)` | 기본 동작(전체 반환)은 **바꾸지 않는다** — `names()`는 `cmd_*` 대상 산출 외에 `jobs.py`의 등록 여부 확인(`name not in reg.names()`)에도 쓰이므로, 기본값을 바꾸면 검색 유입 채널이 "미등록"으로 오판돼 매번 재등록·폴더 재지정된다. `auto_only=True`일 때만 `auto_run is False`인 채널을 제외하며, 이 인자를 쓰는 곳은 **`main.bulk_targets`(채널 인자 없음) 한 곳뿐**이고 `cmd_run`·`cmd_transcribe`가 그 헬퍼를 공유한다 (FR34.7·DQ-25) |

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
| `run(force_vid, limit, progress, entries, pl_map, date_range, rest_state)` | 채널 루프: 429 지수 백오프(FR14.3)·배치 휴식(FR14.2)·연속 429 중단(FR13.5)·카나리아 `--limit`(FR14.5)·멤버십 감지 스킵. **신규 4인자가 모두 None이면 기존 CLI 동작과 완전 동일**(FR18.1, 반환 dict에 `date_skip:0` 키만 추가). `entries`/`pl_map`이 주어지면 `scan_channel()`/`scan_playlists()`를 건너뛰고 대시보드 스캔 캐시를 그대로 사용(DQ-13). `date_range`는 해석 없이 `process_video`로 전달(DQ-12). `rest_state`(`BatchRest`)를 주면 배치 휴식 카운터를 **호출 경계를 넘어 공유**한다 — 그룹 추출은 채널마다 `run()`을 새로 부르므로 지역 카운터로는 휴식이 오지 않는다(FR14.2, 검색은 영상당 채널이 달라 치명적). None이면 이 호출 전용 상태를 새로 만든다(기존 CLI 동작). `progress`가 False를 반환하면 우아한 취소 — 루프 break → `finishing` 보고 → `state.save()` → (`pl_map` 있으면) `_backfill_meta()` → 최종 로그 → `stats["cancelled"]=True`로 반환 (FR18.2). 인덱싱은 이 함수 범위 밖(DQ-14) |

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
| `POST /channels/delete` | 구현됨 | 채널 삭제 (FR21.2) — `ChannelRegistry.remove`로 등록 해제(없으면 404). `purge=true`면 `channel_dir().resolve()`가 `OUTPUT_BASE` 하위인지 재확인 후 `shutil.rmtree` — 등록 해제만으로는 `output/` 폴더가 disk에 남지만 `/channels/stats`가 registry 기준이라 라이브러리 UI에서는 즉시 사라진다. `MANAGER.is_busy()`면 409 |

> 요청 모델(Pydantic): `ScanRequest{url}` · `Filters{latest, since, until, categories, include_members, keyword}` · `ExtractRequest{url, scan_id, filters, index=True}` ·
> `VideoDeleteRequest{channel, basename}` · `ChannelDeleteRequest{channel, purge=False}`.
> `JobBusyError`→409 `{detail, job}`, `ValueError`→400 `{detail}`로 매핑한다.

### 2.10 dashboard/jobs.py — FR17~18

| 항목 | 설계 |
|---|---|
| `classify_url(url)` | 영상(watch?v=·youtu.be·/shorts/·/live/에서 11자 ID) → 재생목록(`/playlist?list=`) → **검색(`/results?search_query=`)** → 채널(@핸들·/channel/UC), 판별 불가 시 `ValueError`→400 (FR17.1·24.1·34.1). 퍼센트 인코딩 핸들도 디코드 후 판별. **순수 텍스트는 검색으로 승격하지 않는다** — 검색 진입은 요청 본문의 `q` 필드 전용이다 (DQ-27) |
| `JobManager` | 모듈 싱글턴(단일 uvicorn 프로세스 전제). `threading.Thread(daemon=True)` 1개, `threading.Event` 취소, `RLock` 하 job dict 갱신. **점유 플래그(`_busy`) 1개를 추출·스캔이 공유** — 추출 중 `POST /extract/scan`도, 스캔 중 `POST /extract`도 409다(스캔은 1+N회 요청이라 결코 가볍지 않고, 동시 호출은 429 위험을 키운다). `start()`는 **요청 검증(400) → 점유 획득(409)** 순서라 잘못된 요청이 점유를 남기지 않는다 |
| 스캔 캐시 | `scan_id → {channel, url, videos_view, entries, pl_map, created_at}` TTL 10분 (FR17.3·DQ-13). API 응답용 `videos_view`뿐 아니라 **원본 flat `entries`와 `pl_map`을 함께 보관**하는 것이 핵심이다 — 추출 시 `Extractor.run(entries=, pl_map=)`으로 그대로 넘겨 재스캔(1+N회 요청 중복)을 없앤다. 만료·부재 시 `ValueError`→400 |
| `apply_filters(videos, f)` | ⓒ카테고리(OR·재생목록 제목 완전일치, 카테고리 선택 시 재생목록 없는 영상 제외) → ⓓ멤버십(`include_members=false`면 제외) → ⓔ키워드(소문자 부분일치) 를 AND로 적용한 뒤 **마지막에 `out[:latest]`**. ⓑ기간은 여기서 적용하지 않는다(DQ-12). 프론트 `applyFilters()`와 동일 순서가 계약이다 (DQ-15) |
| 멤버십 판정 | 스캔 엔트리 `availability`(`subscriber_only`·`needs_auth`·`premium_only` 부분일치) **OR** `state.sub_type=="members_only"` 합집합 (FR17.6). `include_members=false`면 여기서 제외되므로 `decide()`의 FR19.1 재시도에 도달하지 않는다 |
| 채널 워커 | `apply_filters` 결과 id 집합으로 원본 `entries`를 **같은 순서로** 재구성 → 미등록 채널이면 `registry.add(원본 URL)`(기존 항목이면 호출하지 않아 `added_at`·`note` 보존) → `Extractor.run(entries=, pl_map=, date_range=, progress=cb)`. cb는 락 하에 job을 갱신하고 `not cancel.is_set()`을 반환한다 |
| 단일영상 워커 | full info 1회 조회 → **채널 등록 URL 조립**: `uploader_id`가 `@`로 시작하면 `https://www.youtube.com/{uploader_id}`를 만들고, 아니면 `channel_url`→`uploader_url` 폴백. `uploader_id`(`@handle`)를 그대로 `registry.add()`에 넘기면 `normalize_url()`이 `@handle/videos`라는 깨진 URL을 저장해 이후 모든 `run`이 실패한다 → 조립 필수 (FR17.2). 이후 `process_video(info=선조회분)`. 재생목록 매핑은 생략하고 다음 전체 run의 백필(FR15.5)로 채운다 |
| 후처리 | `index` 옵션이 켜져 있고 **취소가 아니며** 신규+수정 > 0일 때만 같은 스레드에서 `KLIndexer.index_all()` (FR17.9·DQ-14) |
| 지연 임포트 가드 (F-6) | `extractor` 모듈은 `_app_extractor()` 헬퍼로만 로드한다. yt-dlp 실행이 legacy 플러그인 탐색으로 site-packages의 `ytdlp_plugins` 경로를 등록하면 이후의 맨 `import extractor`가 그 서브패키지로 **섀도잉**된다 — 실증: 첫 스캔은 200, 같은 프로세스의 두 번째 스캔이 ImportError (2026-08-04 발견). 헬퍼는 sys.modules 캐시에 올바른 모듈(`Extractor` 속성 보유)이 있으면 재사용하고, 오염 시 앱 루트를 sys.path 최우선으로 되돌려 재임포트한다. mock 테스트의 가짜 `extractor` 주입과도 호환 |
| `is_busy()` (FR21.4) | `_busy` 플래그를 락 하에 읽어 반환하는 공개 헬퍼. `server.py`의 삭제 엔드포인트가 진행 중인 추출·스캔과 파일 정리가 겹치지 않도록 이 값으로 409를 판단한다(사설 속성 직접 접근 대신) |

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
  ├ 8~12개(랜덤)마다 45~90초(랜덤) 휴식 (FR14.2, BatchRest — 그룹 추출은 채널 간 공유)
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
    group: "AI LLM Wiki"       # 선택 — 라이브러리 폴더 (FR25.1)
    channel_id: "UCxxxxxxxx"   # 선택 — RSS 1회 해석 캐시 (FR29.1)
    auto_run: false            # 선택 — ./yt.sh run·transcribe 전체 순회 제외 (FR34.7)
```

> 선택 필드는 **값이 기본값이면 기록하지 않는다**: `group`은 빈 값이면 제거(FR25.1), `auto_run`은 `true`이면 제거.
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
  "kind": "channel_run | single_video | playlist_run | search_run",
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
| `GET /channels/stats` | – | `{channels:[{name,url,lang,added_at,group,auto_run,extracted,members_only,no_sub,total_known,last_extracted}]}` | – |
| `POST /channels/auto_run` | `{channel, auto_run}` | `{channel, auto_run}` | 404 미등록 / 409 작업 중(FR21.4 `is_busy`) |
| `GET /subtitle` | `?channel=&basename=` | `{basename, text}` (txt 전문) | 400 경로탈출(`channel`·`basename` 양쪽 검사), 404 파일 없음 |
| `GET /videos` | `?channel=` | `{videos:[{video_id,title,upload_date,basename,tickers,playlists,content_type,sub_type,duration,duration_string,url}]}` (날짜 역순) | 400 채널 누락·경로탈출 |

**`POST /extract` 요청 판정 규칙 (전부 400 `{detail}`):**

1. `url`과 `scan_id`가 **둘 다 있음** → 400 "url과 scan_id는 함께 지정할 수 없습니다."
2. **둘 다 없음** → 400 "url 또는 scan_id 중 하나가 필요합니다."
3. `url`이 채널로 분류됨 → 400 "채널 URL은 `/extract/scan`을 먼저 호출하세요." (무조건 전체 추출 폭주 방지 — 채널은 반드시 스캔·조건 단계를 거친다)
4. `url` 판별 불가 → 400 (FR17.1)
5. `scan_id`가 **만료(TTL 10분 초과)되었거나 존재하지 않음** → 400 "scan_id가 만료되었습니다. 다시 스캔하세요." (410 신설 없이 §5.9 오류 집합 유지)

**`POST /extract/scan` 검색 요청 판정 규칙 (전부 400 `{detail}`):** `url`과 `q` 동시 지정 · 둘 다 없음 · `q`가 공백 ·
`limit`이 1~50 밖 · `min_duration`이 음수. `period`는 알 수 없는 값이면 400이 아니라 `all`로 폴백한다(조건 완화는 안전 방향).
`folder`는 트림 후 비면 `q`를 쓴다 (FR34.1~34.6).

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
├── dashboard/
│   ├── server.py          # FastAPI (FR11·17~20)
│   ├── jobs.py            # JobManager·classify_url·apply_filters·재생목록/검색 워커 (FR17~18·24·34)
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

### 9.1 단위 검증 (V-U — 네트워크 불필요)

> **정본은 코드다 (2026-09-20 번호 충돌·누락 전면 해소).** 아래 ID·대상은 `tests/test_unit.py`의
> 섹션 헤더 주석과 **1:1**로 맞춘 것이다. 목록에 없는 V-U 번호는 존재하지 않고, 테스트에 있는
> 검증은 번호가 없더라도 §9.1b에 전부 기록한다. 신규 번호는 **V-U22**부터 잇는다.

#### 9.1a 번호 부여 항목 (V-U1~V-U21)

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

기준선: 2026-09-20 기준 `./yt.sh test` = **60 passed / 1 skipped** (skip 1건은 컨테이너 이미지에 node가
없는 `fmtDuration` node 실행 테스트 — 호스트 node 22에서 통과 확인).

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
| `종목 추출 (FR12.2)` | `extract_tickers` 티커 추출 | |

mock 스크립트(`mock_scan_test.py` ⑥~⑰ · `mock_jobs_test.py` ①~⑪)에도 번호 없는 검증이 다수 있다 —
CLI 동작 불변(FR18.1)·우아한 취소(FR18.2)·스캔 캐시 재사용(DQ-13)·date_skip 등식(V-D11 전제)·
`--limit` 요청 예산(F-2)·쿠키 건강(FR19.2~19.3)·`mark_invalid`(F-3)·영상별 이벤트(FR26.1)·
JobManager 동시성/취소(FR17.7~17.8)·필터 차분 대조(V-D11 전제)·재생목록 워커(FR24)·
검색 워커(FR34.6~34.10)·배치 휴식 크로스 그룹(FR14.2). 번호가 붙은 것은 V-U8·V-U9뿐이다.

### 9.2 통합 테스트 (tests/test_integration.py — 네트워크 필요)

V-I1 등록+폴더 · V-I2 2회차 SKIP · V-I3 수정 감지 · V-I4 SUSPECT 기록 ·
V-I5 재추출 갱신 · V-I6 2컬렉션 생성 · V-I7 채널 격리 · V-I8 재시작 영속

### 9.3 대시보드·기능 검증 (V-D — FR15~20·24~26·34)

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
| DQ-20 | 제목 언어는 `extractor_args.youtube.lang`으로 **고정** | 다국어 제목 채널에서 flat 스캔(browse)과 영상별 full info(player)가 서로 다른 언어 트랙을 반환해 같은 영상 제목이 화면마다 달라졌다. 후처리 정규화가 아니라 **요청 단계에서 언어를 고정**한다 — 모든 경로가 같은 옵션 빌더(`_ydl_opts`)를 지나므로 한 곳에서 계약이 성립하고, 저장된 meta.title과 스캔 제목이 같아진다. 값은 채널 `lang`(NFR4), 번역이 없으면 yt-dlp가 원제로 폴백한다 (FR32.1) |
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
| 단위 | pipeline-verify ② | V-U1~V-U21 (§9.1a — pytest + mock 스크립트, 네트워크 없음). V-U3은 테스트 미구현, V-U8·V-U9는 mock 스크립트 |
| 빌드 | pipeline-verify ③ | docker build |
| 카나리아 | pipeline-verify ④⑤ | V-D2 + 회귀(스킵 수 유지·429 없음) |
| 인덱스/스모크 | pipeline-verify ⑥⑦ | V-D9 일부 (curl /videos·/search) |
| 문서 정합 | spec-sync | 트레이서빌리티 불일치 0건 |

실행 모드: **서브 에이전트 오케스트레이션** (파일 기반 산출물 전달, `_workspace/`).
