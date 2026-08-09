"""자막 변환·파일명 유틸리티 (VTT→SRT→TXT, 청킹)."""
import re
import config


# ─── 파일명 ──────────────────────────────────────────────────────────────────
def sanitize(text: str, max_len: int = config.TITLE_MAX_LEN) -> str:
    """파일시스템 안전 파일명."""
    text = re.sub(r'[/\\:*?"<>|\n\r\t]', '', text)
    text = re.sub(r'\s+', '_', text.strip())
    return text[:max_len].rstrip('_')


def make_basename(upload_date: str, title: str) -> str:
    """YYYYMMDD_제목 형식 베이스명."""
    return f"{upload_date}_{sanitize(title)}"


# ─── 타임스탬프 변환 ─────────────────────────────────────────────────────────
def ts_to_sec(ts: str) -> float:
    ts = ts.strip().replace(',', '.')
    h, m, s = ts.split(':')
    return int(h) * 3600 + int(m) * 60 + float(s)


def sec_to_srt(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace('.', ',')


def _clean_text(text: str) -> str:
    text = re.sub(r'<\d{2}:\d{2}:\d{2}\.\d{3}>', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('\u200b', '').replace('\ufeff', '')
    return text.strip()


# ─── VTT 파싱 ────────────────────────────────────────────────────────────────
def parse_vtt(content: str) -> list:
    """VTT → [(start_sec, end_sec, text), ...]."""
    content = content.lstrip('\ufeff')
    lines = content.splitlines()
    blocks = []
    i = 0
    while i < len(lines):
        if '-->' not in lines[i]:
            i += 1
            continue
        time_line = re.sub(r'\s+(align|position|line|size|vertical):\S+', '', lines[i])
        try:
            left, right = time_line.split('-->')
            start, end = ts_to_sec(left), ts_to_sec(right)
        except Exception:
            i += 1
            continue
        i += 1
        raw = []
        while i < len(lines) and lines[i].strip() and '-->' not in lines[i]:
            raw.append(lines[i])
            i += 1
        text = _clean_text('\n'.join(raw))
        if text:
            blocks.append((start, end, text))
    return blocks


def _line_overlap(prev_lines: list, cur_lines: list) -> int:
    """prev의 꼬리 k줄 == cur의 머리 k줄인 최대 k (슬라이딩 롤링 캡션 겹침)."""
    for k in range(min(len(prev_lines), len(cur_lines)), 0, -1):
        if prev_lines[-k:] == cur_lines[:k]:
            return k
    return 0


def _dedup(blocks: list) -> list:
    """자동생성 자막 중복 제거 — 두 패턴 모두 처리.

    ① 누적형: 다음 블록이 현재 블록 전체로 시작 ("안녕" → "안녕 반갑습니다")
       → 불완전한 현재 블록을 버린다.
    ② 슬라이딩형: 현재 블록의 꼬리 줄(들) == 다음 블록의 머리 줄(들)
       ("L1\\nL2" → "L2\\nL3") — 유튜브 자동자막의 2줄 롤링 창.
       겹친 줄은 먼저 나온 블록에 남기고 뒤 블록에서 떼어낸다 (F-7).
    """
    result = []
    n = len(blocks)
    for i, (s, e, t) in enumerate(blocks):
        if i + 1 < n and blocks[i + 1][2].startswith(t) and t != blocks[i + 1][2]:
            continue
        if result and result[-1][2] == t:
            ps, _, pt = result[-1]
            result[-1] = (ps, e, pt)
            continue
        if result:
            prev_lines = result[-1][2].split('\n')
            cur_lines = t.split('\n')
            k = _line_overlap(prev_lines, cur_lines)
            if k:
                cur_lines = cur_lines[k:]
                if not cur_lines:            # 전부 겹침 → 이전 블록 시간만 연장
                    ps, _, pt = result[-1]
                    result[-1] = (ps, e, pt)
                    continue
                t = '\n'.join(cur_lines)
        result.append((s, e, t))
    return result


def vtt_to_srt(vtt_content: str) -> str:
    """VTT → SRT 문자열."""
    blocks = _dedup(parse_vtt(vtt_content))
    if not blocks:
        return ''
    out = []
    for idx, (s, e, t) in enumerate(blocks, 1):
        out += [str(idx), f"{sec_to_srt(s)} --> {sec_to_srt(e)}", t, '']
    return '\n'.join(out)


def reflow_sentences(text: str) -> str:
    """줄바꿈을 문장 경계로 재배치 (FR23, 한·영 공통).

    자막의 줄은 화면 표시 폭 기준이라 문장 중간에서 끊긴다. 전부 이어 붙인 뒤
    문장부호([.!?…] + 닫는 따옴표·괄호) 뒤에서만 개행한다.
    - 부호 뒤 공백: 개행 ("합니다. 그리고" / "done. Next")
    - 부호 바로 뒤 한글: 개행 ("합니다.이" — 자동자막에 흔한 무공백 연결)
    - 소수점·버전("3.5", "v4.2")은 부호 뒤가 숫자/영문이고 공백이 없어 보존됨
    """
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'([.!?…]["\'”’)\]]*)\s+', r'\1\n', text)
    text = re.sub(r'([.!?…])(?=[가-힣])', r'\1\n', text)
    return text


def srt_to_txt(srt: str) -> str:
    """SRT → 순수 텍스트. 화면 줄 단위가 아닌 문장 단위로 개행 (FR23)."""
    out = []
    for line in srt.splitlines():
        s = line.strip()
        if not s or re.match(r'^\d+$', s):
            continue
        if re.match(r'^\d{2}:\d{2}:\d{2},\d{3}\s*-->', s):
            continue
        out.append(s)
    return reflow_sentences(' '.join(out))


# ─── SRT 청킹 (FR6.2: 120초 윈도우) ──────────────────────────────────────────
def parse_srt(srt_content: str) -> list:
    """SRT → [(start_sec, end_sec, text), ...]."""
    blocks = []
    for chunk in re.split(r'\n\s*\n', srt_content.strip()):
        lines = [l for l in chunk.splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        ts_idx = 0 if '-->' in lines[0] else (1 if len(lines) > 1 and '-->' in lines[1] else None)
        if ts_idx is None:
            continue
        try:
            left, right = lines[ts_idx].split('-->')
            start, end = ts_to_sec(left), ts_to_sec(right)
        except Exception:
            continue
        text = ' '.join(lines[ts_idx + 1:])
        if text:
            blocks.append((start, end, text))
    return blocks


def chunk_by_srt(srt_content: str, window_sec: int = config.SRT_WINDOW_SEC) -> list:
    """
    SRT를 window_sec 단위로 묶어 청크 생성.
    반환: [{start_sec, end_sec, text}, ...]
    """
    blocks = parse_srt(srt_content)
    if not blocks:
        return []

    chunks = []
    cur_start = blocks[0][0]
    cur_texts = []
    cur_end = blocks[0][1]

    for start, end, text in blocks:
        if start - cur_start >= window_sec and cur_texts:
            chunks.append({
                "start_sec": int(cur_start),
                "end_sec": int(cur_end),
                "text": ' '.join(cur_texts),
            })
            cur_start = start
            cur_texts = []
        cur_texts.append(text)
        cur_end = end

    if cur_texts:
        chunks.append({
            "start_sec": int(cur_start),
            "end_sec": int(cur_end),
            "text": ' '.join(cur_texts),
        })
    return chunks


def chunk_text(text: str, max_chars: int = config.DESC_CHUNK_TOKENS * 3) -> list:
    """설명 텍스트 단순 청킹 (문장 경계 기준, 근사)."""
    if not text.strip():
        return []
    sentences = re.split(r'(?<=[.!?。\n])\s+', text)
    chunks, cur = [], ''
    for sent in sentences:
        if len(cur) + len(sent) > max_chars and cur:
            chunks.append(cur.strip())
            cur = ''
        cur += sent + ' '
    if cur.strip():
        chunks.append(cur.strip())
    return chunks
