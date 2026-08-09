---
name: extraction-ops
description: yt-subs 추출 운영 런북 — 429 차단, 쿠키 만료/갱신, 멤버십 영상 재시도 대응. "429", "차단됐어", "추출이 안 돼", "쿠키 갱신", "쿠키 만료", "멤버십 영상", "Too Many Requests" 등 추출 실패·인증 이슈가 언급되면 반드시 이 스킬을 사용하라.
---

# extraction-ops — 429·쿠키·멤버십 운영 런북

## 429 차단 대응 (FR13.5·FR14 운영 절차)

1. **프로브는 1회만**: 차단 여부 확인은 watch 페이지 HTTP 상태 요청 1회로 한다. 반복 프로브는 차단을 연장시킨다
   ```bash
   curl -s -o /dev/null -w "%{http_code}" "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
   ```
2. 429/403이면 **24시간 대기 원칙**. 대기 단축 시도(IP 변경·UA 변조 등)를 제안하지 않는다
3. 복구 후 점진 확대: `./yt.sh run 채널 --limit 5` → 정상이면 `--limit 20` → 전체. 각 단계에서 429 재발 시 처음부터
4. 코드의 보호장치(`sleep_requests`·배치 휴식·백오프·연속 429 중단)는 조정 대상이 아니다 — 완화하려면 FR14 스펙 변경이 선행돼야 한다

## 쿠키 갱신 (FR13·FR19)

증상: 로그에 `cookies are no longer valid` 경고, 또는 대시보드 쿠키 pill "만료 의심".

1. 사용자에게 브라우저에서 쿠키 재추출을 안내한다 — 절차는 `COOKIES_GUIDE.md` (시크릿 창 + 확장프로그램 방식). **쿠키 추출은 사용자가 직접 수행한다**
2. 갱신된 `cookies.txt`를 프로젝트 루트에 배치
3. **serve 컨테이너 재시작 필수**: cookies.txt는 파일 단위 bind mount라 호스트에서 파일을 교체하면 실행 중인 컨테이너에 반영되지 않을 수 있다
   ```bash
   docker rm -f yt-dash 2>/dev/null; ./yt.sh serve
   ```
4. 확인: `GET /cookies`의 mtime이 갱신 시각인지, 경고가 해제됐는지 (경고는 쿠키 mtime > 감지 시각이면 자동 해제 — FR19.3)

## 멤버십 영상 (FR19.1)

- 멤버십 전용 영상은 **가입 계정의 유효한 쿠키**가 있어야 추출 가능하다. 쿠키가 비멤버 계정이면 몇 번을 재시도해도 실패한다
- state.json에 `sub_type: members_only`로 기록된 영상은 cookies.txt 존재 시 다음 run에서 자동 재시도된다 (구현 전이면: 해당 vid를 `state.remove` 후 run)
- 재시도 후에도 members_only로 재수렴하면 정상 동작이다 — 계정 멤버십 등급을 사용자에게 확인

## 동시 실행 금지 (FR17.10)

CLI `./yt.sh run`과 대시보드 추출을 동시에 돌리지 않는다 — 같은 state.json을 두 프로세스가 쓰면 마지막 저장이 이긴다(유실). 대시보드 작업 상태는 `GET /extract/status`로 확인 후 CLI를 실행하라.

## 회귀 기준선 (2026-08-02 기준)

두두감자: 추출 48 · 멤버십 3 · 무자막 0 · 재생목록 16개(매핑 67영상). 운영 작업 후 이 수치가 의도 없이 변했으면 보고한다.
