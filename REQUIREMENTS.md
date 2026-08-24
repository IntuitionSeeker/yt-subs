# REQUIREMENTS — YouTube 자막 수집 · 지식층 파이프라인

> **버전:** v4.5  
> **작성일:** 2026-08-09  
> **연계 문서:** DESIGN.md v4.5  
> **주요 변경:** 쿠키/429 방어(FR13~14), 재생목록 카테고리(FR15), 라이브 추출(FR16),
> 대시보드 추출 인터페이스·진행율·쿠키상태·라이브러리(FR17~20), 개발 거버넌스(NFR10)  
> **v4.1 (FR17~20 백엔드 구현 확정 반영):** 조건 적용 순서·ⓐ"최신" 정의·ⓓ↔FR19.1 우선순위 비고(FR17.4),
> 취소 시 인덱싱 생략(FR17.8, DQ-14), `--limit` 예산 정의(FR14.5, DQ-16), `sub_type` 노출(FR20.2),
> phase 목록 정합(FR18.3), 트레이서빌리티 매트릭스를 실제 구현 파일명으로 정정  
> **v4.2 (라이브러리 관리 신규):** 채널/영상 삭제(FR21.1~21.2)·자막 클립보드 복사(FR21.3) 추가  
> **v4.3:** 진행 중 라이브 단일 URL 가드(FR16.5), TXT 문장 단위 줄바꿈(FR23), 멤버십 추출 계정 조건 실증(FR19 비고)  
> **v4.4:** Firefox 쿠키 직접 읽기(FR13.6) — 수동 내보내기·회전 문제 해소, 멤버십 추출 실증 완료  
> **v4.5:** [추출] 탭에 등록 채널 현황 카드 추가, 클릭 시 조건 화면 자동 진입(FR22, 프론트 전용)  
> **v4.6:** 429 방어 강화(FR14.2~14.3) — 배치 휴식 랜덤화(고정 패턴 서명 제거), 429 백오프 후 같은 영상 1회 재시도(일시 차단으로 인한 영구 누락 방지)  
> **v4.7:** 재생목록 URL 추출(FR24) — 대시보드에서 `/playlist?list=…` 스캔·조건 추출, 결과물은 원채널 폴더 저장 + 재생목록 제목을 카테고리로 병합  
> **v4.8:** 채널 폴더(FR25) — channels.yaml `group` 필드, 라이브러리 폴더 섹션·전체 보기(영상 병합), 재생목록 추출 시 신규 채널 자동 폴더 지정  
> **v4.9:** 추출 결과 상세(FR26) — 영상별 이벤트(id·제목·종류·이유)를 job에 축적, 통계 칩 클릭 시 해당 분류 영상 목록·이유 표시  
> **범위:** 채널 관리 → 자막 추출 → 메타데이터 수집 → 품질 검토 → 지식층 인덱싱 → 질의·대시보드

---

## 1. 시스템 개요

여러 YouTube 채널의 모든 영상에서 자막·메타데이터·설명을 수집하고,  
품질 검토 후 채널별 로컬 지식층(ChromaDB)에 인덱싱하는 개인용 파이프라인.

### 실행 원칙
- **멀티 채널** — `channels.yaml`로 여러 채널 등록·관리
- **수동 실행 전용** — 자동 스케줄 없음, 사용자가 필요할 때 실행
- **간결한 CLI** — `yt.sh` 래퍼로 Docker 명령어 은닉, 한 줄 실행
- **Spec-First** — 코드 변경 전 본 문서 갱신 우선
- **로컬 우선** — 외부 API 최소화 (LLM 품질 검토 단계만 Claude API 사용)

---

## 2. 용어 정의

| 용어 | 정의 |
|---|---|
| **자막 (Subtitle)** | YouTube 영상의 자막 텍스트 (수동 또는 자동생성) |
| **메타데이터 (Metadata)** | 영상 제목·날짜·조회수 등 영상 속성 정보 |
| **설명 (Description)** | YouTube 영상 설명란 전문 텍스트 |
| **channels.yaml** | 등록된 채널 목록·설정을 담은 파일 |
| **state.json** | 영상별 처리 상태·수정일을 저장하는 채널별 영속 파일 |
| **KL (Knowledge Layer)** | ChromaDB 기반 로컬 벡터 지식층 |
| **modified_date** | yt-dlp가 YouTube에서 추출한 영상 최종 수정일 |
| **yt.sh** | Docker 명령어를 감싸는 래퍼 셸 스크립트 |
| **질의 하네스** | 제품 내 LLM tool_use 루프 (`kl_harness.py`, FR10) |
| **개발 하네스** | 프로젝트 개발·검증·운영용 Claude Code 에이전트/스킬 체계 (`.claude/`, NFR10, DESIGN §11) |

---

## 3. 기능 요구사항 (FR)

### FR1 — 자막 추출

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR1.1 | 지정 채널 전체 영상의 자막을 일괄 추출한다 | 필수 |
| FR1.2 | 수동 자막 우선 적용, 없으면 자동생성(auto) 폴백 | 필수 |
| FR1.3 | SRT(타임스탬프 포함)와 TXT(순수 텍스트) 두 형식으로 저장 | 필수 |
| FR1.4 | 파일명 형식: `YYYYMMDD_영상제목.{srt\|txt}` | 필수 |
| FR1.5 | SRT와 TXT는 동일한 베이스 파일명을 사용한다 | 필수 |
| FR1.6 | `srt/`와 `txt/` 폴더를 분리하여 저장한다 | 필수 |

---

### FR2 — 변경 감지

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR2.1 | `state.json`에 없는 영상 ID는 신규(NEW)로 판단하여 추출 | 필수 |
| FR2.2 | `modified_date`가 변경된 영상은 수정(UPDATED)으로 판단하여 재추출 | 필수 |
| FR2.3 | `modified_date`가 없는 경우 `upload_date`로 폴백 비교 | 필수 |
| FR2.4 | 처리 결과(신규·수정·스킵)를 `extract_log.csv`에 기록 | 필수 |
| FR2.5 | 특정 영상 강제 재처리 가능 | 선택 |
| FR2.6 | 채널 스캔이 수정일 정보를 제공하지 않으면(extract_flat) 무변경으로 간주하여 스킵 | 필수 |

---

### FR3 — 메타데이터 수집

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR3.1 | 영상별 메타데이터를 `meta/YYYYMMDD_제목.json`으로 저장 | 필수 |
| FR3.2 | 수집 필드: id, title, upload_date, modified_date, duration, view_count, like_count, comment_count, tags, categories, thumbnail, webpage_url, channel, sub_type, extracted_at | 필수 |
| FR3.3 | 영상 설명(description)을 `desc/YYYYMMDD_제목.txt`로 별도 저장 | 필수 |
| FR3.4 | 설명이 없는 영상은 빈 파일 생성 없이 스킵 | 필수 |

---

### FR4 — 자막 품질 검토

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR4.1 | `review` 명령 실행 시 전체 TXT 자막 품질을 검토한다 | 필수 |
| FR4.2 | **1차 규칙 기반 검토** — 아래 조건 중 하나라도 해당 시 의심(SUSPECT) 플래그 | 필수 |
|        | · 자막 단어 수 < 30 (비정상적으로 짧음) | |
|        | · 동일 문장 반복 비율 > 50% | |
|        | · 한국어 문자 비율 < 30% (언어 불일치) | |
|        | · 특수문자 비율 > 20% | |
| FR4.3 | **2차 LLM 검토** — 1차 SUSPECT 항목에 한해 Claude API로 영상당 1회 내용 검토 | 선택 |
| FR4.4 | 검토 결과를 `review_report.csv`에 저장 | 필수 |
| FR4.5 | SUSPECT 영상 목록을 터미널에 요약 출력 | 필수 |

---

### FR5 — 자막 재처리

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR5.1 | SUSPECT 전체 영상 재추출 | 필수 |
| FR5.2 | 특정 영상 단일 강제 재처리 | 필수 |
| FR5.3 | 재처리 시 기존 파일 덮어쓰기, `state.json` 업데이트 | 필수 |

---

### FR6 — 지식층 인덱싱 (ChromaDB)

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR6.1 | `index` 명령으로 ChromaDB에 청크 인덱싱 | 필수 |
| FR6.2 | 청킹 단위: SRT 타임스탬프 기준 120초 윈도우 | 필수 |
| FR6.3 | 임베딩 모델: `BAAI/bge-m3` (HuggingFace Hub 자동 다운로드) | 필수 |
| FR6.4 | 청크 메타데이터: video_id, title, upload_date, sub_type, chunk_index, start_seconds, source_url | 필수 |
| FR6.5 | 자막은 `subtitle_chunks`, 설명은 `desc_chunks` 2개 컬렉션으로 분리 인덱싱 | 필수 |
| FR6.6 | ChromaDB 인덱스는 채널별 `output/{채널}/chroma/`에 로컬 저장 | 필수 |
| FR6.7 | 이미 인덱싱된 청크는 upsert (중복 방지) | 필수 |

---

### FR7 — 멀티 채널 관리 (신규)

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR7.1 | `channels.yaml`로 채널 목록 등록·관리 | 필수 |
| FR7.2 | `add URL` 명령으로 채널 등록 + 전체 자막 추출 자동 시작 | 필수 |
| FR7.3 | `run` 명령으로 등록된 전체 채널 신규/수정 감지·업데이트 | 필수 |
| FR7.4 | 채널별 독립 출력 폴더·상태 파일·ChromaDB 유지 | 필수 |
| FR7.5 | 채널 목록 조회(`list`)·삭제(`remove`) 가능 | 필수 |
| FR7.6 | 채널명은 URL의 핸들(@뒤)에서 자동 추출, 폴더명으로 사용 | 필수 |

---

### FR8 — 래퍼 스크립트

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR8.1 | `yt.sh`가 Docker 빌드·실행 명령어를 은닉한다 | 필수 |
| FR8.2 | 사용자는 `./yt.sh {명령} {인자}` 형태로만 실행 | 필수 |
| FR8.3 | output 폴더 볼륨 마운트를 자동 처리 | 필수 |
| FR8.4 | Anthropic API 키를 환경변수로 컨테이너에 전달 | 필수 |
| FR8.5 | 이미지 미빌드 시 최초 1회 자동 빌드 | 선택 |

---

### FR9 — 질의 인터페이스 (신규)

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR9.1 | `ask "질문"` — 벡터 검색(RAG) 후 LLM 답변 + 출처 링크 | 필수 |
| FR9.2 | `summarize VIDEO_ID` — 전체 자막 직접 로드 후 요약 (RAG 미사용) | 필수 |
| FR9.3 | `search "키워드"` — 벡터 검색 결과만 출력 (LLM 미사용) | 필수 |
| FR9.4 | 검색 시 날짜 필터(`--since`, `--until`) 지원 | 필수 |
| FR9.5 | 답변에 영상 제목·날짜·타임스탬프 링크(`?t=N초`) 포함 | 필수 |
| FR9.6 | `KLQuery` 클래스를 Python import로 재사용 가능 | 필수 |

---

### FR10 — 멀티스텝 질의 하네스 (제품 내)

> **명칭 주의:** 본 FR의 "질의 하네스"(`kl_harness.py`)는 제품 기능이다.
> 프로젝트 개발·검증에 쓰는 **개발 하네스**(`.claude/`, NFR10·DESIGN §11)와 구분한다.

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR10.1 | tool_use 루프 기반 에이전트 (max_steps 제한) | 필수 |
| FR10.2 | 도구 노출: search, get_full, summarize, list_videos | 필수 |
| FR10.3 | 멀티스텝 작업 지원: 비교 → 표 생성 → 검증 | 필수 |
| FR10.4 | 검증 단계는 하이브리드(셀프 검증 + 핵심 수치 코드 대조) | 필수 |
| FR10.5 | 각 도구 호출·결과를 로그로 추적 | 선택 |
| FR10.6 | `KLHarness` 클래스를 Python import로 재사용 가능 | 필수 |

---

### FR11 — 웹 대시보드 (신규)

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR11.1 | FastAPI 백엔드 + 단일 HTML 프론트엔드 | 필수 |
| FR11.2 | 엔드포인트: `/ask`(멀티스텝), `/search`, `/videos`, `/summary` | 필수 |
| FR11.3 | 대화창에서 LLM 질의·답변 (멀티스텝 하네스 호출) | 필수 |
| FR11.4 | 영상 목록 패널 (날짜순·종목 태그·검색) | 필수 |
| FR11.5 | 결과 패널 (표·요약·검증 결과 렌더링) | 필수 |
| FR11.6 | 답변 내 타임스탬프 링크 클릭 시 YouTube 해당 구간 이동 | 필수 |
| FR11.7 | 채널 선택 드롭다운 (멀티 채널 전환) | 필수 |

---

### FR12 — 주식 채널 특화 (신규)

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR12.1 | 청크 메타데이터에 `upload_date` 기반 시계열 필터 강화 | 필수 |
| FR12.2 | 영상별 종목/티커 자동 추출 → meta.json `tickers` 필드 | 선택 |
| FR12.3 | "최근 N일 영상만" 같은 시점 기반 질의 지원 | 필수 |
| FR12.4 | 시간순 비교 질의 지원 ("종목 X 전망 시간순 변화") | 필수 |

> **면책:** 본 시스템은 영상 내용 검색·요약 도구이며, 투자 자문이나 매매 신호를 생성하지 않는다. 추출된 내용은 영상 제작자의 의견이며 투자 판단의 책임은 사용자에게 있다.

---

### FR13 — 쿠키 인증 · 429 가드 (신규 문서화)

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR13.1 | `cookies.txt` 존재 시 yt-dlp 인증에 사용 (429 완화) | 필수 |
| FR13.2 | 읽기전용 원본 쿠키를 쓰기 가능한 작업본(`/tmp`)으로 복사해 사용 | 필수 |
| FR13.3 | 쿠키 없거나 파싱 실패 시 비로그인으로 폴백 | 필수 |
| FR13.4 | 자막 VTT 직접 다운로드 요청에도 쿠키 + 브라우저 UA 적용 | 필수 |
| FR13.5 | 연속 429 발생 N회(기본 5) 초과 시 추출 자동 중단 (차단 악화 방지) | 필수 |
| FR13.6 | **Firefox 쿠키 직접 읽기** — Firefox 프로필 폴더가 `/app/firefox_profile`에 마운트되어 있으면(`yt.sh`가 cookies.sqlite 있는 최신 프로필 자동 감지·ro 마운트) yt-dlp `cookiesfrombrowser`로 매 실행 최신 쿠키를 직접 읽는다. cookies.txt보다 **우선**하며, 수동 내보내기와 쿠키 회전(수 시간 내 만료) 문제를 원천 해소. 자막 VTT 직접 다운로드(FR13.4)에도 동일 적용. `/cookies` 응답에 `source`(`"firefox"`\|`"file"`\|null) 노출 | 필수 |

### FR14 — 레이트리밋 방어 (신규 문서화)

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR14.1 | yt-dlp 다운로드 간 랜덤 딜레이 (8~20초) | 필수 |
| FR14.2 | 배치 휴식 **랜덤화** — 8~12개(배치마다 재추첨) 처리 시 45~90초 랜덤 휴식. 고정 주기(구 10개/60초)는 패턴 기반 차단 탐지에 기계 서명이 됨 (2026-08 실측: 고정 60초 휴식 직후 첫 요청마다 429) | 필수 |
| FR14.3 | 429 발생 시 지수 백오프 대기 후 **같은 영상을 1회 재시도**, 재시도도 429면 그 영상은 이번 run에서 포기하고 다음 영상으로 진행. 재시도도 요청 예산을 소비한다(FR14.5·DQ-16). `stats.error`는 최종 포기 영상 수 기준(일시 429 후 재시도 성공은 오류로 세지 않음), extract_log.csv에는 시도별 `error:429` 행이 남는다(감사 추적) | 필수 |
| FR14.4 | 메타데이터 요청(`extract_info`) 간 `sleep_requests` 딜레이 | 필수 |
| FR14.5 | `run --limit N` 카나리아 실행 — 최대 N개만 처리 후 안전 종료, 나머지는 다음 run에서 이어받기 | 필수 |

> **`--limit` 예산의 의미 (FR14.5):** limit은 "성공 추출 N건"이 아니라 **YouTube 요청 예산의 상한 가드**다(FR14의 취지 = 429 방어).
> 따라서 `extract_info` 요청 1회를 소비하는 **모든 경로**가 예산을 소비한다 — 정상 추출·무자막·**멤버십 재시도(FR19.1)**·오류·**기간 밖 스킵(`date_skip`, FR17.5)**.
> 반대로 요청을 쓰지 않는 경로(state 기준 `skip`)는 예산을 소비하지 않는다. 근거는 DESIGN DQ-16.
> FR19.1 도입 이후 멤버십 영상은 매 run 요청을 소비하므로, 멤버십 영상이 많은 채널에서 이 규칙이 없으면 카나리아가 실제 요청 수를 통제하지 못한다.

> **운영 절차 (429 재발 시):** 차단 확인은 요청 1회 프로브(watch 페이지 HTTP 상태)로 수행한다. 차단 상태면 24시간 대기 원칙. 복구 후에는 `run --limit 5` → `--limit 20` → 전체 순으로 점진 확대한다.

### FR15 — 재생목록 카테고리 (신규)

채널 주인이 만든 재생목록을 영상 분류(카테고리)로 사용한다. 물리적 폴더 분리 없이 meta.json + ChromaDB 메타데이터 태그로만 저장한다.

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR15.1 | run 시 `@채널/playlists` 탭과 각 재생목록을 flat 스캔해 `video_id → [재생목록 제목]` 매핑 생성, `output/채널/playlists.json` 저장. 탭 없는 채널은 빈 매핑으로 계속 | 필수 |
| FR15.2 | meta.json에 `playlists` 필드(제목 리스트, 복수 소속 전부 보존) 저장 | 필수 |
| FR15.3 | ChromaDB 청크 메타데이터에 `playlists`(쉼표 join 문자열 — 메타데이터 리스트 불가 제약) 태그 | 필수 |
| FR15.4 | 검색(`search`)·대시보드에 카테고리 부분일치 필터 (여유분 조회 후 클라이언트 측 필터) | 필수 |
| FR15.5 | run 종료 시 기존 meta/*.json에 `playlists` 백필 (로컬 쓰기만, 네트워크 없음). 매핑이 비면 기존 값 보존 | 필수 |

### FR16 — 라이브(스트림) 추출 (신규)

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR16.1 | 채널 스캔 시 videos + streams 두 탭을 스캔해 id 기준 병합 | 필수 |
| FR16.2 | streams/playlists 탭이 없는 채널은 에러가 아닌 info 로그 후 계속 (graceful) | 필수 |
| FR16.3 | 진행 중(`is_live`)·예약(`is_upcoming`) 라이브는 제외 (자막 미완성) | 필수 |
| FR16.4 | meta.json·청크 메타데이터에 `content_type`(`"video"` \| `"live"`) 저장. full info의 `live_status=='was_live'`로 보정 | 필수 |
| FR16.5 | **단일 영상 URL 경로에서도** 진행(`is_live`)·예약(`is_upcoming`) 라이브는 추출하지 않고 `live_wait`로 집계하며 **state.json에 기록하지 않는다** — 종료 후 같은 링크(또는 다음 run)에서 자동으로 새 영상으로 추출된다. (기록하면 no_sub로 남아 종료 후에도 영원히 skip되는 결함이 있었음, 2026-08-09) | 필수 |

> **비고:** Shorts는 수집 범위에서 제외한다. 재생목록에 포함된 Shorts는 videos 탭 스캔 결과에 없으므로 자연히 무시된다.

### FR17 — 대시보드 추출 인터페이스 (신규)

대시보드에서 URL 입력으로 자막 추출 작업을 시작한다. serve 컨테이너 내부에서 백그라운드 스레드로 실행한다.
채널 URL은 바로 추출하지 않고 **① 채널 스캔 → ② 추출 조건 선택 → ③ 조건 대상만 추출**의 2단계 흐름을 따른다.

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR17.1 | URL 자동 분류: 영상 URL(watch?v= · youtu.be · /shorts/ · /live/ 패턴) vs 채널 URL(@핸들 · /channel/UC). 판별 불가 시 400 | 필수 |
| FR17.2 | 단일 영상 URL: full info 1회 조회로 채널 핸들 획득 → 미등록 채널이면 channels.yaml 자동 등록 → 해당 영상만 추출 (조건 선택 단계 없음). 재생목록 매핑은 생략하고 다음 전체 run의 백필(FR15.5)로 채움 | 필수 |
| FR17.3 | 채널 URL 입력 시 사전 스캔(`POST /extract/scan`): videos+streams 탭 병합(FR16) + 재생목록 매핑(FR15) 스캔 → 후보 영상 목록(제목·content_type·재생목록·멤버십 여부·기추출 여부)과 재생목록 목록 반환. 결과는 `scan_id`로 서버에 캐시(TTL 10분) | 필수 |
| FR17.4 | 추출 조건 (AND 결합, `POST /extract`에 `scan_id` + 조건 전달): ⓐ 최신 업로드 N개(기본 10 — "최신"의 정의는 아래 비고) ⓑ 업로드 기간(시작~종료일) ⓒ 재생목록 카테고리(복수 선택) ⓓ 멤버십 전용 포함/제외(기본 제외, 포함 시 쿠키 필요 안내 — 제외 선택은 FR19.1 재시도보다 우선) ⓔ 제목 검색어 포함(부분일치). 조건 미지정 시 전체. **적용 순서는 아래 비고를 계약으로 한다** | 필수 |
| FR17.5 | 조건 선택 UI는 조건 변경 시 대상 영상 수·목록을 즉시 미리보기(클라이언트 필터). 단, 기간 조건(ⓑ)은 flat 스캔이 업로드 날짜를 제공하지 않으므로(FR2.6 동일 제약) 미리보기에서 "처리 시 확정"으로 표기하고, 추출 단계에서 영상별 full info의 upload_date로 판정해 범위 밖이면 자막 다운로드 없이 스킵(`date_skip` 집계) | 필수 |
| FR17.6 | 멤버십 전용 여부는 스캔 단계에서 flat 엔트리 availability(subscriber_only 등)로 우선 판별하고, 스캔에서 놓친 경우 처리 시 FR13 감지로 보완 | 필수 |
| FR17.7 | 동시 1작업 제한 — 실행 중 추가 요청은 409 + 현재 작업 상태 반환 | 필수 |
| FR17.8 | 영상 단위 우아한 취소 (`POST /extract/cancel`) — 현재 영상 완료 후 중단, state.json 저장·meta 백필(FR15.5) 보장. **취소 시 자동 인덱싱(FR17.9)은 생략한다** — 취소는 "지금 멈춤" 신호이고 `index_all()`은 임베딩 모델 로드 + 전체 재임베딩으로 수 분이 걸리기 때문. 추출된 파일은 다음 추출 또는 `./yt.sh index`에서 upsert된다 (근거: DESIGN DQ-14) | 필수 |
| FR17.9 | 완료 후 자동 인덱싱 (요청 옵션 `index`, 기본 on) — 라이브러리 벡터 검색에 즉시 반영. "완료"에 취소(FR17.8)는 포함하지 않으며, 신규·수정이 0건이면 생략한다 | 필수 |
| FR17.10 | serve 컨테이너 외부(CLI `yt.sh run`)와의 동시 실행 직렬화는 범위 외 — 동시 실행 금지를 운영 주의로 명기 | 비고 |

> **FR17.4 조건 적용 순서 (계약):** ⓒ카테고리(선택 항목 중 하나라도 포함 = OR, 재생목록 제목 완전일치) → ⓓ멤버십 → ⓔ검색어(대소문자 무시 부분일치)를
> AND로 걸러낸 뒤 **마지막에 ⓐ최신 N개를 slice**한다. ⓑ기간은 이 단계에서 적용하지 않고 처리 시 full info의 `upload_date`로 확정한다(FR17.5·DESIGN DQ-12).
> 클라이언트 미리보기와 서버가 이 순서를 동일하게 지켜야 "미리보기 대상 수 = 실제 처리 수"(V-D11)가 성립한다 — 근거는 DESIGN DQ-15.

> **FR17.4ⓐ "최신"의 정의:** flat 스캔은 업로드 날짜를 제공하지 않으므로(FR2.6) 진짜 날짜순 정렬은 불가능하다.
> ⓐ는 **스캔 응답 `videos` 배열의 앞에서 N개**를 뜻하며, 그 배열 순서는 videos 탭 전체 → streams 탭 전체 병합 순서(FR16.1)다.
> 따라서 라이브(streams)는 videos 탭 항목이 N개 미만일 때만 최신 N에 포함된다(사실상 거의 포함되지 않음).

> **FR17.4ⓓ ↔ FR19.1 우선순위:** 대시보드 조건 추출에서 `include_members=false`(기본)이면 멤버십 영상은 **대상 목록에서 사전 제외**되어
> FR19.1의 자동 재시도가 발동하지 않는다(사용자 조건 우선). CLI `run`과 `include_members=true`에서는 FR19.1이 그대로 매 run 재시도한다.
> 멤버십 여부는 스캔 단계 `availability`(FR17.6) **OR** state의 `sub_type=="members_only"` 합집합으로 판정한다.

### FR18 — 추출 진행율 보고 (신규)

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR18.1 | `Extractor.run(progress=콜백)` — 콜백 미전달(CLI 경로) 시 기존 동작과 완전 동일 | 필수 |
| FR18.2 | 콜백이 False 반환 시 취소로 간주, state 저장·백필은 정상 수행, stats에 `cancelled` 표시 | 필수 |
| FR18.3 | `GET /extract/status` 폴링 API — phase(`registering`/`extracting`/`indexing`/`finishing`, 스캔 캐시 없이 `Extractor.run`이 직접 스캔하는 경우 `scanning`/`playlists` 추가), 진행 M/N, 현재 영상 제목, 누적 stats | 필수 |
| FR18.4 | 작업 종료 시 최종 stats(신규/수정/스킵/무자막/멤버십/오류) 노출 | 필수 |

### FR19 — 쿠키 상태 · 멤버십 재시도 (신규)

멤버십 가입 계정의 유효한 쿠키가 있으면 멤버십 전용 영상도 자막 추출이 가능하다(yt-dlp 인증).

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR19.1 | state에 `sub_type=members_only`로 기록된 영상은 cookies.txt 존재 시 매 run 자동 재시도("updated" 액션). 여전히 접근 불가면 다시 members_only로 수렴 (run당 1회). 단 대시보드 조건 추출에서 `include_members=false`면 대상에서 사전 제외되어 재시도하지 않는다(FR17.4ⓓ 우선). 재시도는 요청 1회를 소비하므로 `--limit` 예산에 포함된다(FR14.5) | 필수 |
| FR19.2 | yt-dlp logger 주입으로 "cookies no longer valid" 경고 감지 → `output/.cookie_status.json`에 영속 (CLI·serve 컨테이너 간 공유) | 필수 |
| FR19.3 | `GET /cookies` — 쿠키 존재 여부 · mtime · 만료 경고 상태 반환. 쿠키 파일이 경고 감지 시각 이후 갱신됐으면 경고 자동 해제 | 필수 |
| FR19.4 | 라이브 영상(FR16)은 대시보드 추출 인터페이스 경유 시에도 동일 동작 (검증 항목) | 필수 |

> **멤버십 계정 조건 (2026-08-09 실증):** 멤버십 자막 추출은 쿠키가 유효한 것만으로는 부족하고,
> **그 쿠키의 계정이 해당 채널 멤버십에 실제 가입**되어 있어야 한다. 유효 쿠키로 두두감자 멤버십 7건
> 재시도 시 YouTube가 "'호두감자' 등급 가입 필요"로 전건 거부 — 파이프라인은 정상(가입 계정 쿠키면
> 별도 설정 없이 자동 추출됨). 멤버십 가입 계정으로 로그인한 브라우저에서 쿠키를 내보내야 한다.

> **운영 주의:** cookies.txt는 파일 단위 bind mount이므로 호스트에서 파일 교체 시 실행 중인 serve 컨테이너에 반영되지 않을 수 있다. 쿠키 갱신 후 serve 재시작 권장. 반영 여부는 `/cookies`의 mtime으로 확인.

### FR20 — 라이브러리 뷰 (신규)

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR20.1 | 채널 목록 + 통계: 추출 수 · 멤버십 수 · 무자막 수 · 최근 추출일 (state.json 집계, `GET /channels/stats`) | 필수 |
| FR20.2 | 영상 목록: 제목 즉시 필터(클라이언트) + 자막유형(📝수동/🤖자동)·재생목록·LIVE 뱃지 + 원본 링크. 자막유형 뱃지를 위해 `GET /videos`(=`KLQuery.list_videos`)가 meta.json의 `sub_type`(`manual`\|`auto`\|`none`)을 응답 필드로 노출한다 | 필수 |
| FR20.3 | 자막 전문 보기 (`GET /subtitle?channel=&basename=`) — basename 경로 탈출(`..`, `/`) 검증 필수 | 필수 |
| FR20.4 | 자막 내용 벡터 검색은 기존 `POST /search`(카테고리 필터 포함) 재사용 | 필수 |

### FR21 — 라이브러리 관리: 삭제·복사 (신규)

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR21.1 | 영상 삭제 (`POST /videos/delete`, `{channel, basename}`) — srt·txt·meta·desc 파일, state.json 항목, playlists.json 매핑, ChromaDB 두 컬렉션(청크)을 함께 제거. basename 경로 탈출 검증(FR20.3과 동일) | 필수 |
| FR21.2 | 채널 삭제 (`POST /channels/delete`, `{channel, purge}`) — channels.yaml 등록 해제. `purge=true`면 `output/{채널}/` 폴더까지 완전 삭제(되돌릴 수 없음), `false`(기본)면 등록만 해제하고 파일 보존 | 필수 |
| FR21.3 | 자막 전문 보기 패널에 클립보드 복사 버튼 — `navigator.clipboard` 사용, 실패 시 `textarea`+`execCommand` 폴백 | 필수 |
| FR21.4 | FR21.1·FR21.2는 추출/스캔 작업 진행 중(`JobManager` 점유) 요청 시 409로 거부 — 파일 정리와 진행 중인 작업의 경합 방지 | 필수 |

### FR22 — 추출 탭 채널 현황 (신규)

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR22.1 | [추출] 탭에 등록된 전체 채널을 카드로 표시 — 추출 수·멤버십 수·무자막 수·최근 추출일 (기존 `GET /channels/stats`, FR20.1 재사용) | 필수 |
| FR22.2 | 채널 카드 클릭 시 그 채널 URL로 사전 스캔(FR17.3)을 자동 실행하고 추출 조건 화면(FR17.4)을 연다 — URL을 직접 입력·붙여넣기하지 않아도 재추출 가능 | 필수 |
| FR22.3 | 채널 카드 목록은 [추출] 탭 진입 시마다, 그리고 추출 작업 완료 시 새로 조회한다 — 직전 작업 결과·새로 등록된 채널이 곧바로 반영됨 | 필수 |
| FR22.4 | 등록된 채널이 없으면 안내 문구를 표시한다 | 필수 |

### FR23 — TXT 문장 단위 줄바꿈 (신규)

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR23.1 | TXT 저장 시 자막의 화면 표시 줄(2줄 롤링 폭)이 아니라 **문장 경계에서만 개행**한다 — 문장부호(`.` `!` `?` `…` + 닫는 따옴표·괄호) 뒤 공백, 또는 부호 바로 뒤 한글(무공백 연결) 위치에서 개행. 한·영 공통 | 필수 |
| FR23.2 | 소수점·버전 표기("3.5", "v4.2")는 부호 뒤에 공백/한글이 없으므로 분리되지 않는다 | 필수 |
| FR23.3 | SRT는 타임스탬프 동기화를 위해 원래 줄 구조를 유지한다 (변경 없음). 인덱싱 청킹은 SRT 기반이라 영향 없음 | 필수 |

### FR24 — 재생목록 URL 추출 (신규, 대시보드 전용)

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR24.1 | URL 분류(FR17.1)에 `"playlist"` 종류 추가 — `/playlist?list=<id>` 형태를 인식한다. 판정 우선순위는 **영상 → 재생목록 → 채널** (즉 `watch?v=…&list=…`는 기존대로 단일 영상으로 처리) | 필수 |
| FR24.2 | `POST /extract/scan`이 재생목록 URL을 수용 — flat 스캔으로 후보 목록을 반환한다. 응답에 `kind:"playlist"`·`playlist`(재생목록 제목)를 추가하고 `channel`에는 표시용으로 재생목록 제목을 넣는다. 각 후보 항목에 `channel`(원채널명)을 포함한다. 진행 중/예정 라이브 제외(FR16.3 준용)·멤버십 판별(FR17.6 준용)·`extracted`는 원채널 state 조회로 판정 | 필수 |
| FR24.3 | 추출 결과물은 각 영상의 **원채널 폴더**(`output/채널명/`)에 저장한다. 대상을 채널별로 그룹핑해 순차 실행하며, 미등록 채널은 추출 시점에 자동 등록한다(FR17.2 단일영상 선례 준용). 채널명은 엔트리의 `uploader_id`(@핸들) 우선, 없으면 `channel_id`(UC…) 폴백, 둘 다 없으면 해당 영상 스킵(경고 로그) | 필수 |
| FR24.4 | **재생목록 제목을 카테고리로 병합** — 대상 영상의 `meta.playlists`와 채널 `playlists.json`에 재생목록 제목을 추가한다(기존 태그 보존). 라이브러리 탭 카테고리 필터에서 재생목록 이름으로 조회 가능해진다. 병합 맵은 채널의 기존 playlists.json(없으면 기존 meta에서 재구성) ∪ {대상 vid: +재생목록 제목}으로 구성한다 — `_backfill_meta`는 맵에 없는 vid를 `[]`로 덮어쓰므로 부분 맵 전달 금지 | 필수 |
| FR24.5 | 조건 필터(FR17.4)는 동일 적용(카테고리 칩은 재생목록 스캔에서 비어 있음). 진행율은 전체 대상 기준으로 채널 그룹 경계에서 연속 합산하고, 취소는 우아한 취소(FR18.2 준용). 완료 후 자동 인덱싱은 변경(new+updated>0)이 있는 채널만 각각 수행(FR17.9 준용) | 필수 |
| FR24.6 | CLI(`yt.sh add/run`)는 범위 외 — 재생목록 지원은 대시보드 전용 | 명시 |

### FR25 — 채널 폴더(그룹) (신규)

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR25.1 | `channels.yaml` 채널 항목에 선택 필드 `group`(폴더명) 추가. `ChannelRegistry.set_group(name, group)` — 빈 값/None이면 필드 제거(폴더 해제), 미등록 채널은 KeyError | 필수 |
| FR25.2 | `POST /channels/group` `{channel, group?}` — 폴더 지정/변경/해제. 미등록 채널 404, `group`은 트림 후 빈 문자열이면 해제 | 필수 |
| FR25.3 | `GET /channels/stats` 응답 항목에 `group` 필드 포함 (미분류는 `""`) | 필수 |
| FR25.4 | 라이브러리 탭 — `group` 있는 채널은 📁 폴더 섹션으로 묶어 표시(접기/펼치기, 헤더에 채널 수·추출 합계). 미분류 채널은 기존대로 최상위 카드. **처음 보는 폴더는 접힌 상태로 시작**하고, 접기 상태는 세션 내 유지 | 필수 |
| FR25.5 | 폴더 "전체 보기" — 폴더 내 모든 채널의 영상을 병합해 최신순 단일 목록으로 표시하고 각 행에 원채널 배지를 단다. 자막 열람·영상 삭제는 각 영상의 원채널 기준으로 동작 | 필수 |
| FR25.6 | 채널 카드의 📁 버튼으로 폴더 지정/변경/해제 (프롬프트 입력, 빈 값 = 해제) | 필수 |
| FR25.7 | 재생목록 추출(FR24.3)에서 **신규 등록**되는 채널은 재생목록 제목 폴더에 자동 지정된다. 이미 등록돼 있던 채널의 폴더는 변경하지 않는다 | 필수 |
| FR25.8 | 폴더 모드 내용 검색 — 폴더 "전체 보기" 상태에서 내용 검색 시, 인덱스가 있는(추출>0) 소속 채널 각각에 `POST /search`(채널당 top_k 5, 동시 3채널)를 실행해 점수순으로 병합, 상위 10건을 원채널 배지와 함께 표시한다. 검색 실패 채널은 건너뛴다 | 필수 |
| FR25.9 | 추출 탭 채널 현황(FR22.1)도 폴더 섹션으로 묶어 표시(접기/펼치기, 라이브러리와 동일 규칙·기본 접힘·별도 접기 상태). 카드 클릭 동작(FR22.2)은 기존과 동일 | 필수 |

### FR26 — 추출 결과 상세: 영상별 이벤트 (신규, 대시보드)

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR26.1 | `Extractor.run`은 영상 1개의 처리 결과가 확정될 때마다 진행 콜백 payload에 `event={id, title, kind, reason}`을 포함한다 (kind: new·updated·skip·no_sub·members_only·error·date_skip·live_wait). **영상 처리 전 보고에는 event 키가 없다**(기존 payload 스키마 유지). `progress=None`(CLI)이면 이벤트를 만들지 않는다(FR18.1 무영향). 스킵 영상도 이벤트 보고를 위해 처리 직후 1회 보고하며 취소 신호를 존중한다 | 필수 |
| FR26.2 | JobManager는 event를 `job["events"]`에 축적(최대 1,000건, 초과 시 오래된 것부터 삭제). 재생목록 작업 이벤트에는 `channel` 필드를 추가한다. 단일 영상 작업도 동일하게 축적. `/extract/status` 응답에 events가 포함된다 | 필수 |
| FR26.3 | 대시보드 진행/완료 통계의 각 항목은 개수>0이고 이벤트가 있으면 클릭 가능한 칩으로 표시되고, 클릭 시 해당 분류의 영상 목록(제목·이유·채널)을 진행 카드 아래 패널에 토글 표시한다. 새 작업이 시작되면(job_id 변경) 패널을 닫는다 | 필수 |
| FR26.4 | 이유 문구 — 오류: 실제 예외 메시지(429는 구분 문구), 수정: "수정 감지 — 재추출", 스킵: "이미 추출됨 · 변경 없음", 신규: "신규 추출", 무자막·멤버십·기간외·라이브대기: 고정 문구 | 필수 |
| FR26.5 | 이력 범위는 현행 status 정책과 동일 — **마지막 작업 1건**. 과거 작업 이력 저장은 범위 외(`extract_log.csv`가 보조 기록) | 명시 |

---

## 4. 비기능 요구사항 (NFR)

| ID | 요구사항 | 기준 |
|---|---|---|
| NFR1 | **실행 환경** | Docker(ARM64) 기본, Python venv 보조 지원 |
| NFR2 | **상태 영속성** | 컨테이너 재시작 후에도 state.json·출력 파일 유지 (-v 마운트) |
| NFR3 | **수동 실행** | 자동 스케줄 없음, 사용자 명령 시만 실행 |
| NFR4 | **언어 우선순위** | 채널별 `lang` 설정 우선, 기본 한국어(ko) |
| NFR5 | **의존성** | yt-dlp, chromadb, sentence-transformers, anthropic, PyYAML |
| NFR6 | **플랫폼** | Mac mini Apple M4 (ARM64) 네이티브 지원 |
| NFR7 | **처리 속도** | 영상 1개당 메타데이터+자막 수집 ≤ 10초 (네트워크 제외) |
| NFR8 | **채널 격리** | 채널 간 데이터·인덱스 완전 분리 |
| NFR9 | **확장성** | 채널 추가 시 코드 수정 없이 yaml 등록만으로 동작 |
| NFR10 | **개발 거버넌스** | 모든 기능 변경은 Spec-First(본 문서 FR·DESIGN.md 갱신 선행). 검증은 `.claude/` 개발 하네스의 pipeline-verify 게이트(V-U/V-I/V-D, DESIGN §9·§11.2) 통과를 기준으로 한다 |

---

## 5. 사용자 명령어 (CLI 사양)

모든 명령은 `yt.sh` 래퍼를 통해 실행한다.

| 명령 | 동작 | 비고 |
|---|---|---|
| `./yt.sh add URL` | 채널 등록 + 전체 자막 추출 자동 시작 | FR7.2 |
| `./yt.sh run` | 등록된 전체 채널 신규/수정 업데이트 | FR7.3 |
| `./yt.sh run 채널명` | 특정 채널만 업데이트 | FR7.3 |
| `./yt.sh run --limit N` | 카나리아: 최대 N개 영상만 처리 | FR14.5 |
| `./yt.sh review` | 전체 채널 품질 검토 (규칙 기반) | FR4 |
| `./yt.sh review --llm` | LLM 검토 포함 | FR4.3 |
| `./yt.sh reextract` | SUSPECT 영상 재추출 | FR5.1 |
| `./yt.sh index` | 전체 채널 KL 인덱싱 | FR6 |
| `./yt.sh list` | 등록 채널 목록 조회 | FR7.5 |
| `./yt.sh remove 채널명` | 채널 등록 해제 | FR7.5 |

---

## 6. 트레이서빌리티 매트릭스

| FR | 구현 컴포넌트 | 출력 아티팩트 |
|---|---|---|
| FR1.1~1.6 | `Extractor` | srt/, txt/ |
| FR2.1~2.5 | `StateManager` | state.json, extract_log.csv |
| FR3.1~3.4 | `MetaCollector` | meta/*.json, desc/*.txt |
| FR4.1~4.5 | `QualityChecker` | review_report.csv |
| FR5.1~5.3 | `Reprocessor` (Extractor 재사용) | srt/, txt/ 덮어쓰기 |
| FR6.1~6.7 | `KLIndexer` | chroma/ (2개 컬렉션) |
| FR7.1~7.6 | `ChannelRegistry` | channels.yaml |
| FR8.1~8.5 | `yt.sh` | (래퍼) |
| FR9.1~9.6 | `KLQuery` | (질의 API) |
| FR10.1~10.6 | `KLHarness` (질의 하네스) | (answer·trace) |
| FR11.1~11.4 | `dashboard/server.py` · `index.html` | (대시보드) |
| FR12.1~12.4 | `MetaCollector.extract_tickers` · `KLQuery` | meta tickers 필드 |
| FR13.1~13.5 | `config.resolve_cookiefile` · `Extractor._fetch_vtt` | /tmp 쿠키 작업본 |
| FR13.6 | `config.firefox_profile_dir`·`has_auth` · `Extractor._ydl_opts`(cookiesfrombrowser)·`_fetch_vtt` · `yt.sh`(프로필 자동 감지 마운트) · `cookie_health.get_status`(source) | (Firefox 쿠키 직접 읽기) |
| FR14.1~14.5 | `config.YTDLP_COMMON` · `Extractor.run` | (레이트리밋 방어) |
| FR15.1~15.5 | `Extractor.scan_playlists` · `MetaCollector` · `KLIndexer` | playlists.json, meta/*.json |
| FR16.1~16.4 | `Extractor.scan_channel` | meta/*.json (content_type) |
| FR17.1~17.9 | `dashboard/jobs.py` (`classify_url` · `apply_filters` · `JobManager` · 스캔 캐시 · `_run_channel`/`_run_single`) · `dashboard/server.py` (`POST /extract/scan`·`/extract`·`/extract/cancel`) · `extractor.py` (`run(entries=,pl_map=,date_range=)` · `process_video(info=,date_range=)`) · `kl_indexer.KLIndexer.index_all` | (백그라운드 작업) |
| FR17.10 | (구현 없음 — 운영 주의 문구만) | — |
| FR18.1~18.4 | `Extractor.run(progress=)` · `Extractor._report` · `dashboard/jobs.py` (`JobManager._make_cb`·`_merge_stats`) · `dashboard/server.py` (`GET /extract/status`) | (폴링 API) |
| FR19.1~19.4 | `StateManager.decide` (쿠키 인지) · `cookie_health.py` (`YDLLogger`·`mark_invalid`·`get_status`) · `Extractor._ydl_opts` (로거 주입) · `dashboard/server.py` (`GET /cookies`) | output/.cookie_status.json |
| FR20.1~20.4 | `dashboard/server.py` (`GET /channels/stats`·`GET /subtitle`) · `kl_query.list_videos` (`sub_type` 노출) · `KLQuery.search` · `dashboard/index.html` (라이브러리 탭) | (라이브러리 뷰) |
| FR16.5 | `Extractor.process_video` (live_status 가드, state 미기록) · stats `live_wait` 키 | extract_log.csv `live_wait` 행 |
| FR23.1~23.3 | `subtitle_utils.reflow_sentences` · `srt_to_txt` | txt/ (문장 단위 개행) |
| FR24.1~24.6 | `dashboard/jobs.py` (`classify_url` playlist 분기 · `_do_scan_playlist` · `_run_playlist` · `_merged_pl_map`) · `dashboard/index.html` (kind 표시·채널 배지) | 원채널 output/ + playlists.json 병합 |
| FR25.1~25.7 | `channel_registry.set_group` · `dashboard/server.py` (`POST /channels/group`·stats group) · `dashboard/jobs.py` (`_run_playlist` 자동 폴더) · `dashboard/index.html` (폴더 섹션·전체 보기·📁 버튼) | channels.yaml `group` 필드 |
| FR26.1~26.5 | `extractor.py` (`_event`·`_report(event=)`) · `dashboard/jobs.py` (`job["events"]` 축적·`_append_event`) · `dashboard/index.html` (통계 칩·이벤트 패널) | /extract/status.events |
| FR21.1~21.4 | `dashboard/server.py` (`POST /videos/delete`·`POST /channels/delete`·`_reject_path_traversal`) · `kl_indexer.KLIndexer.delete_video` · `dashboard/jobs.py` (`JobManager.is_busy`) · `dashboard/index.html` (`deleteVideo`·`deleteChannel`·`copySubtitle`) | output 파일·state·ChromaDB 정리 |
| FR22.1~22.4 | `dashboard/index.html` (`loadExtChannels`·`extSelectChannel`·`switchTab`·`pollJob` 완료 훅) — 기존 `GET /channels/stats`(FR20.1)·`extStart`(FR17.3~17.4) 재사용, 신규 백엔드 없음 | (프론트 전용) |

---

## 7. 검증 방법 (Verification) — 신규

각 요구사항이 충족됐는지 확인하는 방법.

### 7.1 단위 검증 (자동화 테스트)

| 검증 ID | 대상 FR | 검증 방법 | 합격 기준 |
|---|---|---|---|
| V-U1 | FR1.4, FR1.5 | `make_basename()` 호출 → 파일명 형식 확인 | `YYYYMMDD_제목` 형식, srt·txt 동일 |
| V-U2 | FR1.3 | 샘플 VTT → `vtt_to_srt()` → SRT 형식 검증 | 인덱스·타임스탬프·텍스트 3요소 존재 |
| V-U3 | FR1.2 | 수동/자동 자막 mock → 우선순위 선택 확인 | 수동 있으면 manual, 없으면 auto |
| V-U4 | FR2.2 | modified_date 변경 mock → is_updated() | 변경 시 True 반환 |
| V-U5 | FR4.2 | 정상/이상 자막 샘플 → 규칙 검토 | 이상 자막만 SUSPECT 판정 |
| V-U6 | FR6.2 | SRT → `chunk_by_srt()` → 청크 검증 | 120초 윈도우, start_seconds 정확 |
| V-U7 | FR7.6 | URL → 채널명 추출 | `@핸들`에서 핸들명 정확 추출 |

### 7.2 통합 검증 (시나리오 테스트)

| 검증 ID | 시나리오 | 절차 | 합격 기준 |
|---|---|---|---|
| V-I1 | 신규 채널 등록 | `add URL` 실행 | channels.yaml 등록 + srt/txt/meta/desc 생성 |
| V-I2 | 증분 업데이트 | 2회차 `run` 실행 | 변경 없으면 전부 SKIP 로그 |
| V-I3 | 수정 감지 | state.json의 modified_date 임의 변경 후 `run` | 해당 영상만 UPDATED 재처리 |
| V-I4 | 품질 검토 | 이상 자막 포함 상태로 `review` | review_report.csv에 SUSPECT 기록 |
| V-I5 | 재추출 | `reextract` 실행 | SUSPECT 영상 파일 갱신 |
| V-I6 | KL 인덱싱 | `index` 후 ChromaDB 조회 | subtitle_chunks·desc_chunks 2개 컬렉션 존재 |
| V-I7 | 채널 격리 | 2개 채널 등록 후 폴더 확인 | 각 채널 독립 폴더·state·chroma |
| V-I8 | 영속성 | 컨테이너 재시작 후 `list` | 등록 채널·상태 유지 |

### 7.3 검색 품질 검증 (수동)

| 검증 ID | 대상 | 절차 | 합격 기준 |
|---|---|---|---|
| V-Q1 | 자막 검색 | 영상 내용 관련 질의 → subtitle_chunks 검색 | 관련 청크 상위 반환 |
| V-Q2 | 타임스탬프 링크 | 검색 결과의 source_url 클릭 | 정확한 영상 구간으로 이동 |
| V-Q3 | 설명 검색 | 영상 주제 질의 → desc_chunks 검색 | 관련 설명 청크 반환 |

### 7.4 검증 실행 방법

```bash
# 단위 검증 (자동)
./yt.sh test            # pytest 전체 실행

# 통합 검증 (시나리오)
./yt.sh test --integration

# 검증 결과는 test_report.txt로 출력
```

### 7.5 합격 판정 기준

- **단위 검증:** V-U1~V-U7 전부 통과 (100%)
- **통합 검증:** V-I1~V-I8 전부 통과 (100%)
- **검색 품질:** V-Q1~V-Q3 수동 확인, 주관적 만족도 기준

---

## 8. 설계 결정 사항 (확정)

| ID | 질문 | 결정 |
|---|---|---|
| DQ-01 | bge-m3 로드 방법 | ✅ HuggingFace Hub 온라인 다운로드 |
| DQ-02 | Claude API 품질 검토 단위 | ✅ 영상당 1회 전송 (장편은 앞 500단어 샘플링) |
| DQ-03 | desc 인덱싱 컬렉션 구조 | ✅ `subtitle_chunks` + `desc_chunks` 2개 분리 |
| DQ-04 | 청킹 전략 | ✅ SRT 타임스탬프 기준 120초 윈도우 |
| DQ-05 | 멀티 채널 관리 | ✅ channels.yaml + 채널별 독립 폴더 |
| DQ-06 | CLI 간소화 | ✅ yt.sh 래퍼로 Docker 은닉, add 시 추출 자동 시작 |
