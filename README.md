# YouTube 자막 수집 · 지식층 파이프라인

여러 YouTube 채널의 자막·메타데이터·설명을 수집하고, 품질 검토 후 로컬 지식층(ChromaDB)에 인덱싱하여 LLM으로 질의·분석하는 개인용 파이프라인.

저장소: https://github.com/IntuitionSeeker/yt-subs · 라이선스: MIT

---

## 문서

| 문서 | 내용 |
|---|---|
| **[USAGE.md](USAGE.md)** | 사용 설명서 — 설치부터 채널 등록·추출·질의·대시보드·문제 해결까지 단계별 안내 |
| [COOKIES_GUIDE.md](COOKIES_GUIDE.md) | 쿠키 인증 설정 — 429 차단 해결, 멤버십 영상 추출 조건 |
| [REQUIREMENTS.md](REQUIREMENTS.md) | PRD — 기능 요구사항(FR) 정의 |
| [DESIGN.md](DESIGN.md) | 설계서 — 아키텍처·모듈 설계·검증 게이트 |

처음 사용한다면 **USAGE.md**부터 읽으세요. 아래 빠른 시작은 요약본입니다.

---

## 빠른 시작

```bash
# 0. API 키 등록 (LLM 질의·검토용)
export ANTHROPIC_API_KEY="sk-ant-..."

# 1. 실행 권한
chmod +x yt.sh

# 2. 채널 등록 + 자막 추출 (자동 시작)
./yt.sh add https://youtube.com/@채널핸들

# 3. 품질 검토
./yt.sh review

# 4. KL 인덱싱
./yt.sh index

# 5. 대시보드 실행 → http://localhost:8800
./yt.sh serve
```

---

## 인증 (멤버십·연령 제한 영상)

두 가지 방식을 지원하며, 있으면 자동으로 사용된다:

1. **Firefox 프로필 직접 읽기 (권장)** — Firefox에 YouTube 로그인만 해두면 `yt.sh`가
   매 실행 시 최신 쿠키를 프로필에서 직접 읽는다. 쿠키 내보내기가 필요 없다.
2. **`cookies.txt` 폴백** — 프로젝트 루트에 Netscape 형식 쿠키 파일을 두면 사용된다.

멤버십 영상 추출 조건과 쿠키 만료 대응은 `COOKIES_GUIDE.md` 참고.

---

## 명령어

| 명령 | 동작 |
|---|---|
| `./yt.sh add URL` | 채널 등록 + 전체 자막 추출 자동 시작 |
| `./yt.sh run [채널]` | 신규/수정 영상만 업데이트 (전체 또는 특정 채널) |
| `./yt.sh review [--llm]` | 자막 품질 검토 (규칙 기반 / LLM 포함) |
| `./yt.sh reextract` | SUSPECT 영상 재추출 |
| `./yt.sh transcribe [채널] [--limit N]` | 무자막 영상 Whisper 음성 전사 (폴백) |
| `./yt.sh index` | ChromaDB 인덱싱 |
| `./yt.sh list` | 등록 채널 목록 |
| `./yt.sh remove 채널` | 채널 등록 해제 |
| `./yt.sh ask 채널 "질문"` | RAG 질의 (단일 검색) |
| `./yt.sh ask 채널 "질문" --multistep` | 멀티스텝 (비교·표·검증) |
| `./yt.sh search 채널 "키워드"` | 벡터 검색만 |
| `./yt.sh summarize 채널 VIDEO_ID` | 영상 전체 요약 |
| `./yt.sh serve` | 웹 대시보드 (포그라운드 — 터미널을 닫으면 종료) |
| `./yt.sh serve --detach` | 대시보드 상시 운용 (백그라운드 · 자동 재시작) — **주기 자동 추출**을 쓸 때 |
| `./yt.sh migrate-groups [--apply]` | 폴더(그룹)를 `output/<폴더>/<채널>/` 실제 디렉터리로 이전 (기본 dry-run · **대시보드를 먼저 종료**할 것 → [순서](USAGE.md#폴더그룹-디렉터리화--1회성-마이그레이션-fr35)) |
| `./yt.sh audit` | **문서·코드 정합 감사** (읽기 전용 · 1초 이내 · `output/` 불필요) |
| `./yt.sh doctor [채널]` | **데이터 건전성 점검** (읽기 전용 전수 — 미인덱싱·파일 불일치·쿠키·스케줄) |
| `./yt.sh test` | 단위 검증 |

---

## 웹 대시보드 (http://localhost:8800)

`./yt.sh serve`로 실행. 탭 3개:

| 탭 | 기능 |
|---|---|
| **질의** | 채널·카테고리 선택 후 자막 지식층에 RAG 질의 |
| **라이브러리** | 채널별 추출 현황 카드, 폴더(그룹)로 채널 묶기·폴더 전체 영상 병합 보기, 영상 목록 필터(제목·카테고리·내용 검색), 영상 길이 표시, 자막 전문 열람·복사, 영상/채널 삭제 |
| **추출** | 채널·재생목록 URL 또는 **유튜브 검색어** 스캔 → 조건(최신 N개·기간·카테고리·멤버십·검색어) 선택 후 추출, 진행율 표시, 쿠키 상태 확인. 재생목록은 영상별 원채널 폴더에 저장되고 재생목록 제목이 카테고리로 추가됨 |

**⏰ 자동 추출(선택)**: 추출 탭 상단 카드에서 켜면 등록 채널을 주기(3일 기본 · 1·2·4주 선택)마다
자동 추출한다. **기본은 꺼짐**이며, RSS로 새 영상이 있는 채널만 돌고(새 영상이 없으면 요청 0),
주기당 영상 상한(기본 30)·429 백오프(1→2→4주기 건너뜀)·쿠키 경고 시 자동 정지가 걸려 있다.
끄기는 같은 토글이고 **주기가 도는 중에 꺼도 되돌아가지 않는다**(진행 중인 작업 자체를 멈추려면
진행 카드의 취소 버튼). 스케줄러는 대시보드 프로세스 안에서만 살아 있으므로 상시 운용하려면
`./yt.sh serve --detach`로 띄우고 Docker Desktop의 "로그인 시 시작"을 켠다
(중지: `docker stop yt-subs-dashboard` · 로그: `docker logs -f yt-subs-dashboard`).
자세한 절차는 [USAGE.md](USAGE.md#-자동-추출-주기-실행) 참고.

**검색어로 모으기**: 추출 탭에 검색어를 넣으면 유튜브 검색 결과를 후보로 스캔한다. 조건은 ⓐ개수 상한(기본 20, 1~50) ⓑ**N초 미만 제외**(기본 180초, 조정·해제 가능) ⓒ기간 프리셋(시간/오늘/이번주/이번달/올해/전체). 영상은 각 영상의 **원채널 폴더**에 저장되고, 이번 검색으로 **새로 등록된 채널만** 지정한 폴더 하나로 묶인다(검색어는 카테고리로 병합하지 않는다).

- 조건 이름이 "쇼츠 제외"가 아닌 이유: 쇼츠를 식별할 라벨이 **존재하지 않는다**(실측 — 필드·URL 모두 일반 영상과 동일). 유일한 신호가 길이인데 쇼츠 상한이 3분으로 늘어 **185초짜리 실제 쇼츠**가 있어 180초로도 안 걸러진다. 그래서 길이를 그대로 노출하고 임계값 판단을 사용자에게 맡긴다. 길이 정보가 없는 영상은 빈칸이며 필터를 통과한다.
- 검색으로 새로 등록된 채널은 `channels.yaml`에 `auto_run: false`가 기록되어 **`./yt.sh run`·`./yt.sh transcribe`의 무인자 전체 순회에서 제외**된다(채널 폭증 → 429 방지). 채널을 명시하면 정상 실행되고, 대시보드 카드의 `⏭ run 제외` 배지를 눌러 해제할 수 있다.
- `youtube.com/results?search_query=…` 주소를 붙여넣어도 되지만 **주소 안의 `sp=` 필터는 무시**되고 화면에서 고른 조건으로 스캔한다.

---

## 출력 구조

```
output/
└── 채널명/
    ├── srt/          타임스탬프 포함 자막
    ├── txt/          순수 텍스트 자막
    ├── desc/         영상 설명
    ├── meta/         메타데이터 JSON (종목 추출 포함)
    ├── chroma/       ChromaDB 인덱스 (subtitle_chunks + desc_chunks)
    ├── state.json    신규/수정 감지 상태
    ├── extract_log.csv
    └── review_report.csv
```

---

## 아키텍처

```
yt.sh (Docker 래퍼)
   │
   ▼
main.py (CLI)
   │
   ├── ChannelRegistry ── channels.yaml
   ├── Extractor ──┬── StateManager (수정 감지)
   │               └── MetaCollector (메타·설명·종목)
   ├── QualityChecker (규칙 + LLM)
   ├── KLIndexer ── ChromaDB + bge-m3
   ├── KLQuery (검색·요약·RAG)
   └── KLHarness (멀티스텝 에이전트)
        │
        ▼
   dashboard/ (FastAPI + HTML)
```

---

## 질의 방식 3가지

| 방식 | 용도 | 예시 |
|---|---|---|
| `search` | 키워드 검색만 (LLM 없음) | 빠른 구간 찾기 |
| `ask` | RAG 단일 검색 → 답변 | "감자요리 레시피 알려줘" |
| `ask --multistep` | 멀티스텝 비교·표·검증 | "종목 전망 시간순 비교 후 표로" |

---

## 주식 채널 특화

- 영상별 종목코드(6자리)·미국 티커($AAPL) 자동 추출 → `meta.json`
- 날짜 필터: `--since 20240101 --until 20240630`
- 시간순 비교 질의 지원

> ⚠ 본 시스템은 영상 내용 검색·요약 도구이며 투자 자문이 아닙니다.

---

## 검증

```bash
./yt.sh test                # 단위 (223 passed / 1 skipped, 네트워크 불필요)
./yt.sh test --integration  # 통합 (V-I, 실 채널 필요)
./yt.sh audit               # 문서·코드 정합 감사 (읽기 전용, 1초 이내)
./yt.sh doctor              # 데이터 건전성 점검 (읽기 전용 전수)
```

`audit`·`doctor`는 **아무것도 고치지 않는다.** 종료코드로만 말한다 —
`0` 이상 없음 · `1` 경고만 · `2` 오류 · `3` **점검 자체 실패**("이상 없음"과 구분한다).
자세히는 [USAGE.md](USAGE.md#점검--조용히-망가진-것-찾기-fr38).

---

## 의존성

yt-dlp · chromadb · sentence-transformers · anthropic · fastapi · PyYAML

ARM64(Apple M4) 네이티브 지원. 최초 실행 시 bge-m3 모델 자동 다운로드(HuggingFace Hub).
