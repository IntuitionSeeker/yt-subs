# 사용 설명서 (USAGE)

yt-subs를 처음 설치하는 것부터 일상적인 사용까지 순서대로 설명합니다.

- 처음이라면: **1. 준비물 → 2. 설치 → 3. 첫 채널 등록**을 순서대로 따라오세요.
- 쿠키·멤버십·429 차단 문제는 [COOKIES_GUIDE.md](COOKIES_GUIDE.md)를 참고하세요.

---

## 1. 준비물

| 항목 | 필수 여부 | 설명 |
|---|---|---|
| **Docker Desktop** | 필수 | 모든 명령이 Docker 컨테이너에서 실행됩니다. 파이썬·yt-dlp를 직접 설치할 필요가 없습니다. |
| **Anthropic API 키** | LLM 기능만 | `ask`(질의)·`review --llm`(LLM 검토)에 필요. 추출·검색만 쓸 거면 없어도 됩니다. |
| **Firefox + YouTube 로그인** | 권장 | 멤버십 영상 추출과 429 차단 방지에 사용. 로그인만 해두면 쿠키를 자동으로 읽습니다. |

지원 환경: macOS (Apple Silicon 네이티브 지원) · Linux. 최초 실행 시 Docker 이미지 빌드와 임베딩 모델(bge-m3) 다운로드가 자동으로 진행됩니다.

## 2. 설치

```bash
git clone https://github.com/IntuitionSeeker/yt-subs.git
cd yt-subs
chmod +x yt.sh

# LLM 질의를 쓸 경우에만
export ANTHROPIC_API_KEY="sk-ant-..."   # ~/.zshrc에 넣어두면 편합니다
```

별도 설치 절차는 없습니다. 첫 `./yt.sh` 실행 시 이미지가 자동 빌드되고, `channels.yaml`(채널 목록)과 `output/`(결과물 폴더)이 자동 생성됩니다.

## 3. 첫 채널 등록 → 추출 → 질의

```bash
# ① 채널 등록 + 전체 자막 추출 (자동 시작)
./yt.sh add https://youtube.com/@채널핸들

# ② 품질 검토 (규칙 기반)
./yt.sh review

# ③ 지식층(ChromaDB) 인덱싱
./yt.sh index

# ④ 질문하기
./yt.sh ask 채널명 "최근 영상에서 다룬 핵심 주제 정리해줘"
```

채널명은 `output/` 아래 폴더 이름과 같습니다 (`./yt.sh list`로 확인).

## 4. 명령어 레퍼런스

### 채널 관리

```bash
./yt.sh add URL [--lang ko]     # 채널 등록 + 전체 추출 시작
./yt.sh list                    # 등록된 채널 목록
./yt.sh remove 채널명            # 채널 등록 해제 (추출물은 유지)
```

### 추출·갱신

```bash
./yt.sh run                     # 모든 채널: 신규/수정 영상만 업데이트
./yt.sh run 채널명               # 특정 채널만
./yt.sh run 채널명 --limit 3     # 최대 3개만 처리 (변경 후 카나리아 확인용)
```

- 이미 추출된 영상은 자동으로 스킵되므로 `run`은 매일 돌려도 안전합니다.
- 라이브 방송은 종료 후 자막이 준비되면 자동으로 재추출됩니다.

### 품질 검토

```bash
./yt.sh review [채널명]          # 규칙 기반 검토 → review_report.csv
./yt.sh review --llm            # LLM 검토 포함 (API 키 필요)
./yt.sh reextract [채널명]       # SUSPECT 판정 영상 재추출
```

### 인덱싱·질의

```bash
./yt.sh index [채널명]                          # ChromaDB 인덱싱 (추출 후 실행)
./yt.sh search 채널명 "키워드"                   # 벡터 검색만 (LLM 없음, 빠름)
./yt.sh ask 채널명 "질문"                        # RAG 질의: 검색 → LLM 답변
./yt.sh ask 채널명 "질문" --multistep            # 멀티스텝: 비교·표·검증이 필요한 질문
./yt.sh summarize 채널명 VIDEO_ID                # 영상 1개 전체 요약
```

날짜 필터 (`search`·`ask` 공통):

```bash
./yt.sh ask 채널명 "삼성전자 전망 시간순으로 비교해줘" --since 20260101 --until 20260630
```

| 질의 방식 | 언제 쓰나 |
|---|---|
| `search` | "그 얘기 어느 영상에서 했지?" — 구간 찾기 |
| `ask` | 단일 질문·요약 |
| `ask --multistep` | 여러 검색을 조합해야 하는 질문 (시간순 비교, 표 정리, 교차 검증) |

### 검증

```bash
./yt.sh test                    # 단위 테스트 (네트워크 불필요)
./yt.sh test --integration      # 통합 테스트 (실 채널 접근)
```

## 5. 웹 대시보드

```bash
./yt.sh serve                   # http://localhost:8800
```

터미널 없이 대부분의 작업을 할 수 있습니다:

| 탭 | 하는 일 |
|---|---|
| **질의** | 채널·카테고리를 고르고 자막 지식층에 질문 (CLI `ask`와 동일) |
| **라이브러리** | 채널별 추출 현황 카드 · 영상 목록 필터(제목/카테고리/내용 검색) · 자막 전문 열람·복사 · 영상/채널 삭제 |
| **추출** | 채널·재생목록 URL 스캔 → 조건 선택(최신 N개·기간·카테고리·멤버십 포함·제목 검색어) → 추출 실행 · 진행율 표시 · 쿠키 상태 확인 |

추출 탭 사용 흐름: URL 입력(또는 등록 채널 카드 클릭) → 스캔 완료 후 조건 화면에서 대상 미리보기 확인 → "선택 대상 추출 시작". 단일 영상 URL(`watch?v=...`)을 넣으면 스캔 없이 바로 추출됩니다.

재생목록 URL(`playlist?list=...`)도 지원합니다: 결과물은 각 영상의 **원채널 폴더**(`output/채널명/`)에 저장되고, 재생목록 제목이 그 채널의 카테고리로 추가되어 라이브러리 탭에서 재생목록 이름으로 필터링할 수 있습니다. 여러 채널이 섞인 재생목록이면 필요한 채널이 자동 등록됩니다. (`watch?v=...&list=...` 형태는 단일 영상으로 처리됩니다.)

## 6. 결과물은 어디에 있나

```
output/채널명/
├── txt/     순수 텍스트 자막  ←  가장 많이 쓰게 되는 폴더
├── srt/     타임스탬프 포함 자막
├── desc/    영상 설명
├── meta/    메타데이터 JSON (제목·날짜·재생목록·종목코드 등)
└── chroma/  ChromaDB 인덱스
```

파일명은 `업로드날짜_제목` 형식이라 정렬만으로 시간순이 됩니다.

## 7. 자주 겪는 문제

| 증상 | 원인·해결 |
|---|---|
| `HTTP 429 Too Many Requests` | YouTube의 요청 제한. **즉시 중단**하고 [COOKIES_GUIDE.md](COOKIES_GUIDE.md)대로 쿠키 인증을 설정한 뒤, `run --limit 3`으로 소량부터 재개 |
| 멤버십 영상이 `🔒 멤버십`으로 스킵됨 | 멤버십 가입 계정으로 Firefox에 로그인돼 있어야 함. 조건은 COOKIES_GUIDE.md 참고 |
| 대시보드 쿠키 상태가 "만료 의심" | Firefox를 열어 YouTube 재로그인 → serve 재시작 |
| `ask`가 "API 키 없음" 오류 | `export ANTHROPIC_API_KEY=...` 후 다시 실행 |
| 질의 결과에 최근 영상이 안 나옴 | 추출 후 `./yt.sh index`를 안 돌린 경우. 대시보드 추출은 "완료 후 자동 인덱싱"을 켜두면 자동 |
| 코드 수정 후 동작이 안 바뀜 | 이미지 재빌드 필요: `docker build -t youtube-subs .` 후 컨테이너 재시작 |

## 8. 데이터 관리

- **백업**: `channels.yaml`(채널 목록)과 `output/`(전체 결과물)만 복사하면 끝입니다.
- **삭제**: 영상·채널 단위 삭제는 대시보드 라이브러리 탭의 🗑 버튼이 가장 안전합니다 (자막·메타·인덱스를 함께 정리).
- `output/`·`channels.yaml`·쿠키 파일은 git에 커밋되지 않습니다 (.gitignore 처리됨).
