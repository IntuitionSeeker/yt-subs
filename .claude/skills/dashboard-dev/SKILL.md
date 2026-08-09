---
name: dashboard-dev
description: yt-subs 웹 대시보드(FastAPI + 단일 index.html) 작업 규칙. "대시보드", "화면", "UI", "페이지", "index.html", "탭", "버튼", "프론트" 수정·추가 요청이나 대시보드 API를 만들 때 반드시 이 스킬을 사용하라.
---

# dashboard-dev — 대시보드 작업 규칙

## 구조 원칙

- **단일 파일 유지**: UI는 `dashboard/index.html` 하나 (vanilla JS + 인라인 CSS). 빌드 도구·프레임워크·npm을 도입하지 않는다 — 이 프로젝트의 UI 규모에서 빌드 체인은 유지비만 늘린다
- 백엔드는 `dashboard/server.py`(FastAPI) + `dashboard/jobs.py`(작업 관리). 무거운 로직은 서버가 아닌 파이프라인 모듈(kl_query 등)에 두고 서버는 얇게 유지
- 3탭 구조: 질의(채팅) · 라이브러리(목록/검색/전문) · 추출(URL→조건→진행). 새 기능은 기존 탭에 배치하고, 4번째 탭은 사용자 합의 후에만

## 개발 흐름 (재빌드 최소화)

`server.py`는 요청마다 index.html을 읽으므로, **dashboard/를 라이브 마운트하면 HTML 수정이 새로고침만으로 반영**된다:

```bash
docker rm -f yt-dash 2>/dev/null
docker run --rm -d --name yt-dash -p 8800:8800 \
  -v "$PWD/output:/app/output" -v "$PWD/channels.yaml:/app/channels.yaml" \
  -v "$PWD/dashboard:/app/dashboard" \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
  -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" youtube-subs serve
```

- `dashboard/*.py` 수정도 마운트로 반영되지만 uvicorn 재시작(컨테이너 재시작)이 필요하다
- 파이프라인 모듈(`*.py` 루트) 수정은 이미지에 구워지므로 `docker build` 후 재시작

## API 추가 시 경계면 규칙

서버와 프론트는 항상 **한 커밋 안에서 같이** 수정한다:

1. `server.py`에 엔드포인트 + Pydantic 요청 모델
2. `index.html`의 해당 fetch 코드 — 응답 필드명을 서버 반환 dict와 문자 그대로 대조
3. DESIGN §5.9 API 표에 요청/응답 형태 기록
4. 검증: `pipeline-verify` ⑦ 스모크에서 curl 응답과 프론트 파싱 코드를 교차 비교

## 미구현 백엔드 폴백 패턴

프론트를 백엔드보다 먼저 만들 때(UI 미리보기), 새 API 호출은 반드시 우아하게 실패시킨다:

- `try/fetch` 실패 시 `.notice` 배너로 "OO API가 아직 준비되지 않았습니다 (FR{N} 백엔드 구현 후 동작)" 안내
- 가능하면 기존 API로 **로컬 미리보기 폴백** (예: 스캔 미구현 시 `/videos` 데이터로 조건 UI 시연 + "로컬 미리보기 모드" 배너)
- 백엔드 구현 완료 시 폴백 배너 문구가 더는 뜨지 않는지 확인하는 것까지가 구현 완료다

## UI 컨벤션

- 다크 테마 CSS 변수 재사용: `--bg --panel --border --text --muted --accent --accent-dim --green --warn --red`
- 뱃지: 재생목록 `.playlist`(파랑), LIVE `.live`(주황), 티커 `.ticker`(초록), 멤버십 `.mem`(빨강), 자막유형 📝수동/🤖자동
- 사용자 노출 텍스트는 한국어, 날짜는 `fmtDate`(YYYY.MM.DD), XSS 방지로 동적 문자열은 반드시 `esc()` 경유
- 진행 표시는 2초 폴링 (`/extract/status`) — SSE 도입 금지 (DQ-09)

## 상태 확인 명령

```bash
docker ps --filter name=yt-dash          # serve 실행 여부
curl -s localhost:8800/channels          # 백엔드 응답 확인
```
