---
name: pipeline-verify
description: yt-subs 변경 사항의 검증 런북. "검증해줘", "테스트", "확인해줘", "동작하는지 봐줘", "회귀 확인", "카나리아" 요청이나 코드 수정 완료 후 반드시 이 스킬을 사용하라. 정적 검사부터 Docker 카나리아·대시보드 스모크까지 7단계 게이트를 순서대로 실행한다.
---

# pipeline-verify — 검증 런북

DESIGN §9의 V-U/V-D 게이트를 실행 가능한 순서로 배열한 런북. **앞 게이트가 실패하면 뒤로 가지 않는다** — 네트워크·빌드 비용이 큰 게이트일수록 뒤에 있다.

## ① 정적 + 문서 정합 (V-D1 일부 · FR38)

```bash
python3 -m py_compile *.py dashboard/*.py 2>/dev/null || python3 -m py_compile *.py dashboard/server.py
python3 main.py audit          # 문서·코드 정합 8검사 (output/·Docker·PyYAML 불필요, 1초 이내)
```

- `audit`의 **오류(종료코드 2)는 보고하고 계속 진행**한다 — 문서 어긋남이 코드 검증을 막을 이유는 없다.
  단 커밋 전에는 반드시 해소한다(DESIGN §11.2 "문서 정합" 행의 합격 기준 = 종료코드 2 없음)
- 경고(종료코드 1)는 상시 몇 건이 정상이다. 현행 기준선 = **경고 9건**(§6 미수록 FR) · **오류 0**
- pytest 기준선을 바꿨으면 `python3 main.py audit --baseline "<실측값>"`으로 5곳 동기화까지 확인한다
- `audit`은 **고치지 않는다**. 발견은 사람이 방향을 정한다(FR38.1·DQ-52)

## ② mock 단위 (V-U8·V-U9 + 번호 미부여 mock 케이스 — 네트워크·Docker 불필요)

```bash
python3 .claude/skills/pipeline-verify/scripts/mock_scan_test.py   # 파이프라인 코어 (17케이스)
python3 .claude/skills/pipeline-verify/scripts/mock_jobs_test.py   # 대시보드 jobs (18항목)
```

- 호스트에 yt_dlp가 없으므로 스크립트가 `sys.modules` 스텁을 사용한다. 새 로직을 추가했으면 **해당 스크립트에 케이스를 추가**한 뒤 실행하라 — 케이스 추가 없이 통과를 선언하지 않는다
- `mock_jobs_test.py`의 필터 순서 차분 대조(프론트 `applyFilters` 참조 구현과 랜덤 입력 비교)는 V-D11의 전제다. 필터 로직을 건드렸으면 반드시 이 케이스가 통과해야 한다
- Docker가 가능하면 `./yt.sh test`(pytest **267 passed / 2 skipped**, V-U1~V-U40 중 pytest 소관 전부 — 2026-09-27 기준선)도 병행
- V-U 번호↔테스트 대응은 **DESIGN §9.1a(번호 부여)·§9.1b(번호 미부여)**가 정본이며, 그 정본은 `tests/test_unit.py`의 섹션 헤더 주석이다. 새 테스트에 번호를 붙일 때는 §9.1과 테스트 주석을 **같은 커밋에서** 함께 갱신하고, 다음 번호는 **V-U35**부터 잇는다 (V-U3은 테스트 미구현 자리, V-U8·V-U9는 이 ② mock 스크립트 소관)

## ③ 빌드

파이썬 코드를 수정했으면 필수 (코드가 이미지에 구워짐):

```bash
docker build -t youtube-subs "$(git rev-parse --show-toplevel)"
```

## ④ 카나리아 (V-D2)

```bash
./yt.sh run 두두감자 --limit 3
```

- 전체 run 금지 — 카나리아부터. 429가 1회라도 보이면 즉시 중단하고 `extraction-ops` 절차로 전환

## ⑤ 회귀 판정

카나리아 로그에서 확인:

- 기존 추출분이 여전히 `스킵`인지 (기준선: qa-verifier.md의 회귀 기준선 수치)
- `오류 0`, 연속 429 중단 없음
- 새 기능 산출물 존재 (예: playlists.json 갱신, 신규 meta 필드)

**데이터 건전성 점검(선택, FR38):**

```bash
./yt.sh doctor                 # 읽기 전용 전수 (10검사 · 실측 0.3초)
```

- **기준선은 오류 0 · 경고 0 · 종료코드 0**이다(2026-09-26). 1건이라도 나오면 이번 변경이 만든 것인지 먼저 따진다 —
  `[I]` 정보와 `예외적용`은 남아 있어도 정상이고, 상세 해석은 `.claude/agents/qa-verifier.md`의 **doctor 기준선** 항목을 본다
  (단 `index-coverage` 경고는 추출 job이 인덱싱을 끝내기 전이면 정상이다)
- 읽기 전용이므로 실데이터에 그대로 돌려도 된다(파일·디렉터리를 만들지 않는다 — V-U36이 고정)
- 무인 운영(FR37)을 켠 뒤에는 릴리스·주기 점검 시 실행한다

## ⑥ 인덱스 확인

인덱싱 로직을 건드렸을 때만:

```bash
./yt.sh index
# 청크 메타데이터 확인 (컨테이너 내 python)
docker run --rm -v "$PWD/output:/app/output" youtube-subs -c \
  "import chromadb; c=chromadb.PersistentClient('output/두두감자/chroma'); \
   col=c.get_collection('subtitle_chunks'); print(col.peek(1)['metadatas'])"
```

## ⑦ 대시보드 스모크 (V-D9 일부)

serve가 떠 있을 때 (`docker ps`로 확인, 없으면 `./yt.sh serve` 백그라운드):

```bash
curl -s localhost:8800/channels
curl -s "localhost:8800/videos?channel=두두감자" | head -c 300
curl -s -X POST localhost:8800/search -H 'Content-Type: application/json' \
  -d '{"channel":"두두감자","query":"테스트","top_k":1}' | head -c 300
```

- **경계면 교차 비교**: 위 응답의 실제 필드명과 `dashboard/index.html`에서 해당 엔드포인트를 fetch하는 코드의 파싱 필드를 대조한다. 필드 추가/변경 시 양쪽을 모두 확인해야 한다 — 서버만 고치고 프론트를 안 고친 버그는 존재 확인으로는 안 잡힌다

## 보고 형식

`_workspace/03_qa_report.md`:

```markdown
| 게이트 | 결과 | 비고 |
|---|---|---|
| ① 정적 | ✅ | |
| ② mock 단위 | ✅ | 케이스 2개 추가 |
| ③ 빌드 | ✅ | |
| ④ 카나리아 | ✅ | 신규 3 스킵 48 |
| ⑤ 회귀 | ✅ | 기준선 유지 |
| ⑥ 인덱스 | ⏭ 해당 없음 | |
| ⑦ 스모크 | ❌ | /videos에 sub_type 누락 ↔ index.html은 v.sub_type 파싱 |
```

실패 항목은 재현 절차·기대값·실제값을 반드시 포함한다.
