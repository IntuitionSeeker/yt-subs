# 자막 추출 후 용어 교정(Post-Correction) 기능 가이드

대상: 영상별 자막 생성 프로젝트(oxalpha). STT(Whisper 등) 추출 결과에 **주제별 용어 교정 단계**를 추가하기 위한 설계 가이드.
이 문서는 AKM/LLM Wiki 스터디 자막 4편의 교정 사례를 바탕으로 작성했으나, 영상 주제는 매번 다르므로 **주제 프로파일 + 용어사전을 교체 가능**하게 설계한다.

---

## 1. 목표와 범위

| 항목 | 내용 |
|---|---|
| 목표 | STT 오인식(음차·약어·유사 한국어 대체)을 문맥 기반으로 교정해 LLM Wiki ingest 품질을 높인다 |
| 입력 | `*.srt`(원본), 선택적으로 이미 생성된 `*.txt` |
| 출력 | `*.corrected.srt`, `*.corrected.txt`(SRT에서 파생), `*.changes.jsonl`(변경 로그), `*.hold.md`(미확정 후보) |
| 비범위 | 잡담/음악 제거(별도 단계), 화자 분리, 요약 |

원칙
1. **원본 불변**: 원본 SRT/TXT는 절대 덮어쓰지 않는다. 교정본은 별도 파일. (AKM의 source 불변 원칙과 동일)
2. **타임스탬프 무손실**: 큐 번호·시각은 그대로, 텍스트만 변경.
3. **단일 원천**: TXT는 교정된 SRT에서 재생성한다. SRT와 TXT를 각각 교정하지 않는다.
4. **신뢰도 등급**: 확실(○)만 자동 적용, 추정(△)은 hold 목록으로 사람 확인.
5. **Learnback**: 사람이 확정한 hold 항목은 용어사전에 반영해 다음 영상에서 자동 적용.

---

## 2. 파이프라인 위치

```
[영상] → STT 추출 → (A) 정규화 → (B) 주제 감지 → (C) 사전 기반 결정적 치환
      → (D) LLM 문맥 교정(후보 생성) → (E) 검증 → (F) 산출(SRT→TXT 파생) → (G) hold 리뷰 → 사전 갱신
```

| 단계 | 역할 | 비고 |
|---|---|---|
| A 정규화 | `[음악]`, `[웃음]`, 외국어 노이즈(예: 태국어 한 글자, `hej`), 반복 `&gt;&gt;` 마커 정리. 큐 경계에서 끊긴 단어 합치기 위한 "문장 창(window)" 생성 | 원문 큐 매핑 유지 |
| B 주제 감지 | 앞 10~15분 텍스트로 도메인 태그 추출(예: `ai-agent`, `db`, `ontology`, `finance`) → 로딩할 용어사전 선택 | LLM 1회 호출, 또는 파일명/메타로 지정 |
| C 결정적 치환 | 용어사전의 ○ 항목을 정규식으로 치환. 한글 조사 결합 처리 | 빠름, 결정적, 로그 남김 |
| D LLM 문맥 교정 | 문장 창 단위로 "주제와 무관해 보이는 단어"를 찾아 교정 후보+근거+확신도 제안 | 확신 high만 적용, 나머지 hold |
| E 검증 | 큐 수·시각 동일성, 길이 변화율 임계(예: ±30%), 금지 변경(숫자·URL·고유명 확정 목록) | 실패 시 해당 큐 원복 |
| F 산출 | corrected.srt → corrected.txt(타임코드 제거, 문장 병합) | |
| G 리뷰 | hold.md를 사람이 확인 → 사전에 추가/기각 | AKM에서는 hold→pass 승격과 동일 |

---

## 3. 용어사전(glossary) 설계

### 3-1. 파일 구조 (제안)

```
glossary/
  common.yaml        # 주제 무관 STT 습관 오류 (딸각→딸깍, 전자측→전자책 …)
  ai-agent.yaml      # LLM/에이전트/하네스
  knowledge-mgmt.yaml# LLM Wiki/AKM/PKM
  db-data.yaml       # SQL, FTS5, PostgreSQL …
  ontology.yaml
  finance.yaml       # 투자 영상용
  people/<프로젝트>.yaml  # 발표자 닉네임 (프로젝트별)
profiles/
  <영상ID>.yaml      # 이 영상에 적용할 사전 목록 + 확정 고유명사 + 금지 변경
```

### 3-2. 항목 스키마

```yaml
- canonical: Learnback          # 교정 결과
  variants: [런백, 럼백, 림백, 환류]   # STT 표기 (정규식 허용)
  domain: knowledge-mgmt
  confidence: high               # high=자동 적용, medium/low=hold
  match: word                    # word | substring | regex
  josa: true                     # 뒤에 붙은 조사 보존 (런백에 → Learnback에)
  context_hints: [루프, 실패, 규칙]   # 창 안에 이 단어가 있을 때만 적용 (선택)
  exclude_if: []                 # 이 단어가 있으면 적용 금지
  note: 7단계 루프 마지막 단계
  source: akm-study-2026-08     # 어디서 확정됐는지
```

### 3-3. 오류 패턴 분류 (이번 사례에서 도출)

| 패턴 | 예시 | 처리 방식 |
|---|---|---|
| 영문 고유명사 음차 | 다클링→Docling, 카파시→Karpathy, 신싱→Syncthing | C 결정적 치환 |
| 발음 변형(동일어 다표기) | 놀리지/노리지/노일리지→Knowledge | variants 다중 등록 |
| 약어 자모 오인식 | KSA→KSI, FPT5/FTP5→FTS5, X41→EXAONE | context_hints 필수(SQLite, LG) |
| 유사 한국어 단어로 대체 | 사슴→DEER, 채굴→체크 스크립트, 환류→Learnback | D LLM 문맥 교정 대상, medium |
| 조사 결합 | 런백에, 놀리지를, 다클링이라는 | josa 옵션 |
| 큐 경계 단어 분절 | `인덱스.m` / `라는 파일` | A 문장 창에서 결합 후 판단 |
| 인명/닉네임 | 낭님→Knock, 꾸기장→꾸기작, 박상형→박성영 | 프로젝트 people 사전. 채팅 로그·참가자 명단이 근거 |
| 신조어/은어 | 딸각→딸깍, 잡돌이→잡도리 | common 사전 |
| 숫자/모델명 오인식 | GPT 5.6, 512M→512GB | 금지 변경 목록 또는 hold (임의 수정 위험) |
| 노이즈 | [음악], 외국어 한 글자, `&gt;&gt;` | A 정규화에서 제거/치환 |

---

## 4. LLM 문맥 교정(D) 프롬프트 템플릿

```
역할: 한국어 STT 자막 교정자. 영상 주제: {topic_tags}. 
아래 문장 창에서 (1) 주제와 무관해 보이는 단어, (2) 영문 고유명사의 음차, (3) 약어 오인식을 찾아라.
규칙:
- 의미가 바뀌는 재작성 금지. 단어 단위 치환 후보만 제안.
- 각 후보: {원문, 교정, 근거(문맥 단어), 확신도 high|medium|low}
- 이미 적용된 사전 항목은 제안하지 말 것: {applied_terms}
- 숫자, URL, 이미 확정된 고유명사({fixed_terms})는 건드리지 말 것.
- 확신 없으면 medium 이하로 표기하고 원문 유지.
출력: JSON 배열.
문장 창:
{window_text}
```

적용 규칙: high → 즉시 적용 + 로그. medium/low → hold.md에 "원문 / 후보 / 근거 / 큐 번호" 기록.

---

## 5. 변경 로그와 검증

`*.changes.jsonl` 한 줄 예시
```json
{"cue": 1256, "stage": "C", "from": "런백", "to": "Learnback", "rule": "knowledge-mgmt/Learnback", "confidence": "high"}
{"cue": 1706, "stage": "D", "from": "사슴", "to": "DEER", "reason": "직전 큐에 '디어', '논문'", "confidence": "medium", "status": "hold"}
```

검증(E) 체크리스트
- 큐 개수·시작/종료 시각이 원본과 100% 동일
- 큐당 글자 수 변화율 ±30% 이내(초과 시 원복)
- 금지 변경 목록(숫자, URL, fixed_terms) 미변경
- 동일 variants가 창마다 다르게 교정되지 않았는지(일관성 검사)

---

## 6. SRT/TXT 동기화 결정

| 선택지 | 판단 |
|---|---|
| SRT·TXT 각각 교정 | ✗ 불일치 위험, 이중 작업 |
| SRT만 교정 → TXT 파생 | ✓ 권장. TXT 생성기는 타임코드 제거+큐 병합만 담당 |
| TXT만 교정 | ✗ 타임코드 연계 상실 |

TXT가 이미 다른 방식(문장 병합 규칙)으로 생성돼 있다면, 그 생성 규칙을 F 단계에 그대로 옮긴다.

---

## 7. 초기 용어사전 시드 (이번 스터디에서 확정된 ○ 항목)

| canonical | variants | domain |
|---|---|---|
| Learnback | 런백, 럼백, 림백 | knowledge-mgmt |
| Knowledge | 놀리지, 노리지, 노일리지, 논리지 | knowledge-mgmt |
| Harness | 하네스, 한네스 | ai-agent |
| RAG / GraphRAG | 레그 / 그래프레그 | ai-agent |
| Docling | 다클링 | knowledge-mgmt |
| Graphify | 그래피파이, 그래픽파일 | knowledge-mgmt |
| Karpathy | 카파시 | knowledge-mgmt |
| OKF(Open Knowledge Format) | 오케 | knowledge-mgmt |
| DEER | 디어, 이어 | ai-agent |
| KSI | KSA | ai-agent |
| EXAONE | 엑사원, X41, X4원, 엑사온 | ai-agent |
| SQLite FTS5 | FPT5, FTP5 | db-data |
| PostgreSQL | 포스트그레이 SQL | db-data |
| Claude Fable / Opus | 페이블, 테이블(문맥) / 오퍼스, 오프스 | ai-agent |
| Gemini | 재미나이, 잼미니 | ai-agent |
| Suno | 순호, 손호 | common(ai-tools) |
| Cerebras | 세레브레스 | ai-agent |
| OWL / Ontology / Taxonomy / Topology / Cardinality | 아울·오울·마울 / 몬톨로지 / 텍소노미 / 토플로지 / 카디널리티 | ontology |
| Ingest / Classify / Contextualize | 인제스트·인체스 / 클래시파이 / 컨텍스처라이즈 | knowledge-mgmt |
| Front matter | 프론트메타, 브론트메터 | knowledge-mgmt |
| Syncthing / Cursor / Antigravity / NotebookLM / Confluence | 신싱 / 커서 / 안티그래버티 / 노트북 LM / 컴플런스 | common(tools) |
| 딸깍 / 잡도리 / 전자책 / 사례글 / 육하원칙 | 딸각 / 잡돌이·잡돌리 / 전자측 / 사례을·사례그 / 6원칙 | common |

hold 예시(medium): 사슴→DEER(농담 문맥), 채굴→체크 스크립트, 쵸비→CHORP, QMD, AKM 약어(Agent vs Atomic).

---

## 8. 운영 흐름 (영상마다)

1. `profiles/<영상ID>.yaml` 작성: 주제 태그, 참가자 명단(닉네임 사전), 금지 변경 목록
2. 파이프라인 실행 → corrected.srt/txt + changes.jsonl + hold.md
3. hold.md 검토(5~10분) → 확정 항목을 해당 도메인 사전에 추가, 기각은 `exclude` 기록
4. 재실행 시 hold 감소 여부를 지표로 추적(영상당 hold 수, 자동 적용 수)

---

## 9. 테스트 전략

- 골든 샘플: 이번 4편 SRT + 사람 확정 교정본을 회귀 테스트로 보관
- 지표: 정밀도(잘못 교정한 건수/전체 교정), 재현율(놓친 오류/전체 오류), hold 처리율
- 주제 교체 테스트: 투자 영상 1편에 `finance.yaml`만 로딩해 knowledge-mgmt 사전이 오적용되지 않는지 확인

---

## 10. 구현 전 결정 필요 사항

| 질문 | 선택지 |
|---|---|
| D 단계 LLM | Claude Code CLI 호출 / 로컬 모델 / API |
| 사전 포맷 | YAML(가독) vs JSON(파싱 단순) |
| hold 리뷰 UI | md 파일 수동 편집 vs 간단 CLI 승인 |
| 기존 TXT 생성 로직 | 재사용 가능 여부(문장 병합 규칙 확인) |
| 맥북 이전 계획과의 관계 | 이 기능을 이전 후 구현할지, 윈도우에서 먼저 구현할지 |
