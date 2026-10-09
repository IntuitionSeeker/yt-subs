#!/usr/bin/env python3
"""CLI 진입점 — add / run / review / reextract / index / list / remove / ask / summarize / serve / backfill-tickers / terms / correct / audit / doctor."""
import sys
import argparse
import logging

import config

# `channel_registry`는 **지연 임포트**다 — 이 모듈은 PyYAML이 없는 환경에서도
# `audit`이 돌아야 한다(FR38.12: output/·Docker·네트워크·pytest 없이 순수 텍스트로 완결).
# 최상단에서 import하면 yaml 부재만으로 CLI 진입 자체가 죽는다.

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("main")

# CLI로 들어온 영상의 출처 (FR39.2 진입점 매핑). `add`·`run` 두 곳이 공유한다 —
# `reextract`는 재처리이므로 **출처를 주지 않는다**(FR39.4ⓒ).
CLI_ORIGIN = {"kind": "channel", "via": "cli"}


def cmd_add(args):
    """채널 등록 + 전체 자막 추출 자동 시작. FR7.2"""
    from extractor import Extractor
    from channel_registry import ChannelRegistry
    reg = ChannelRegistry()
    existed = reg.resolve_name(args.url) in reg.names()      # FR7.7 upsert 판정
    name = reg.add(args.url, lang=args.lang)
    # add()는 upsert다 — 기존 항목의 group·auto_run·channel_id는 보존된다 (FR7.7)
    log.info(f"✅ 기존 채널 갱신: {name} (폴더·설정 보존)" if existed
             else f"✅ 채널 등록: {name}")
    log.info("자막 추출을 시작합니다...\n")
    Extractor(reg.get(name)).run(origin=CLI_ORIGIN)


def bulk_targets(reg, channel: str = None) -> list:
    """
    일괄 명령(run·transcribe)의 대상 채널 산출. FR34.7 (DQ-25)

    채널을 명시하면 플래그와 무관하게 그 채널만, 인자가 없으면 `auto_run: false`
    (검색 추출로 유입된 채널)를 제외한 전체를 순회한다.
    **run·transcribe 두 곳이 이 헬퍼를 공유한다** — 한쪽만 고쳐지는 드리프트를 막는다.
    """
    return [channel] if channel else reg.names(auto_only=True)


def cmd_run(args):
    """전체 또는 특정 채널 업데이트. FR7.3"""
    from extractor import Extractor
    from channel_registry import ChannelRegistry
    reg = ChannelRegistry()
    targets = bulk_targets(reg, args.channel)
    if not targets:
        log.info("등록된 채널이 없습니다. './yt.sh add URL' 로 추가하세요.")
        return
    for name in targets:
        Extractor(reg.get(name)).run(limit=args.limit, origin=CLI_ORIGIN)


def cmd_transcribe(args):
    """무자막(sub_type=none) 영상 Whisper 전사. FR30.1"""
    from transcriber import Transcriber
    from channel_registry import ChannelRegistry
    reg = ChannelRegistry()
    targets = bulk_targets(reg, args.channel)
    if not targets:
        log.info("등록된 채널이 없습니다. './yt.sh add URL' 로 추가하세요.")
        return
    for name in targets:
        Transcriber(reg.get(name)).run(limit=args.limit)


def cmd_review(args):
    """품질 검토. FR4"""
    from quality_checker import QualityChecker
    from channel_registry import ChannelRegistry
    reg = ChannelRegistry()
    targets = [args.channel] if args.channel else reg.names()
    for name in targets:
        QualityChecker(name).review(use_llm=args.llm)


def cmd_reextract(args):
    """SUSPECT 영상 재추출. FR5.1"""
    from extractor import Extractor
    from quality_checker import QualityChecker
    from state_manager import StateManager
    import json
    from channel_registry import ChannelRegistry
    reg = ChannelRegistry()
    targets = [args.channel] if args.channel else reg.names()
    for name in targets:
        suspects = QualityChecker(name).get_suspects()
        if not suspects:
            log.info(f"{name}: SUSPECT 없음")
            continue
        # basename → video_id 매핑 (meta 역참조)
        ext = Extractor(reg.get(name))
        state = StateManager(name)
        dirs = config.channel_subdirs(name)
        for basename in suspects:
            meta_path = dirs["meta"] / f"{basename}.json"
            if meta_path.exists():
                vid = json.loads(meta_path.read_text(encoding="utf-8")).get("id")
                if vid:
                    state.remove(vid)
                    ext.state.remove(vid)
                    ext.process_video(vid, action="updated")
        ext.state.save()


def cmd_index(args):
    """KL 인덱싱. FR6"""
    from kl_indexer import KLIndexer
    from channel_registry import ChannelRegistry
    reg = ChannelRegistry()
    targets = [args.channel] if args.channel else reg.names()
    for name in targets:
        KLIndexer(name).index_all()


def cmd_list(args):
    """채널 목록. FR7.5"""
    from channel_registry import ChannelRegistry
    reg = ChannelRegistry()
    chans = reg.list()
    if not chans:
        log.info("등록된 채널이 없습니다.")
        return
    log.info(f"{'채널':<20} {'언어':<6} {'등록일':<12} URL")
    log.info("─" * 70)
    for name, c in chans.items():
        log.info(f"{name:<20} {c.get('lang',''):<6} {c.get('added_at',''):<12} {c.get('url','')}")


def cmd_remove(args):
    """채널 삭제. FR7.5"""
    from channel_registry import ChannelRegistry
    reg = ChannelRegistry()
    if reg.remove(args.channel):
        log.info(f"✅ 삭제: {args.channel} (출력 폴더는 수동 삭제 필요)")
    else:
        log.info(f"채널을 찾을 수 없습니다: {args.channel}")


def cmd_ask(args):
    """RAG 질의 또는 멀티스텝. FR9.1"""
    if args.multistep:
        from kl_harness import KLHarness
        result = KLHarness(args.channel).run(args.question)
        print(result["answer"])
        print(f"\n[{result['steps']}단계 · 도구 {len(result['trace'])}회]")
    else:
        from kl_query import KLQuery
        result = KLQuery(args.channel).ask(args.question, since=args.since, until=args.until)
        print(result["answer"])
        print("\n출처:")
        for s in result["sources"]:
            print(f"  - {s['title']} ({s['upload_date']}) {s['url']}")


def cmd_summarize(args):
    """영상 요약. FR9.2"""
    from kl_query import KLQuery
    print(KLQuery(args.channel).summarize(video_id=args.video_id))


def cmd_search(args):
    """벡터 검색만. FR9.3"""
    from kl_query import KLQuery
    results = KLQuery(args.channel).search(args.query, since=args.since, until=args.until)
    for r in results:
        print(f"[{r['score']}] {r['title']} ({r['upload_date']})")
        print(f"    {r['text'][:100]}...")
        print(f"    → {r['source_url']}\n")


def cmd_serve(args):
    """대시보드 서버 실행. FR11"""
    import uvicorn
    sys.path.insert(0, str(config.BASE_DIR / "dashboard"))
    uvicorn.run("server:app", host="0.0.0.0", port=args.port,
                app_dir=str(config.BASE_DIR / "dashboard"))


def cmd_migrate_groups(args):
    """
    폴더 디렉터리화 마이그레이션 — 기본 dry-run. FR35.11~35.12

    `output/<채널>/` → `output/<폴더>/<채널>/`. `channels.yaml`은 한 글자도 바꾸지 않는다
    (`group` 값은 그대로이고 해석 규칙만 달라진다) → 롤백이 디렉터리 되돌리기로 완결된다.
    """
    import folder_ops

    if args.unlock:                                     # FR35.10 stale 락 수동 제거
        log.info("✅ 락을 제거했습니다." if folder_ops.unlock()
                 else "락이 없습니다 (output/.migration.lock).")
        return

    if args.rollback:                                   # FR35.12
        # 락이 살아 있어도 롤백은 진행할 수 있어야 한다 (F-2) — 급사한 마이그레이션의
        # 복구 수단이 락에 막히면 `--unlock`↔`--rollback` 순환에 갇힌다. 다만 정말로
        # 다른 마이그레이션이 도는 중일 수 있으므로 **확인 후 탈취**한다.
        takeover = bool(args.yes)
        if not takeover and folder_ops.is_locked():
            info = folder_ops.read_lock_info()
            log.warning(f"⚠️ 마이그레이션 락이 있습니다 (pid={info.get('pid')} · "
                        f"시작={info.get('started_at')}).")
            try:
                answer = input("다른 마이그레이션이 진행 중이 아니라면 락을 넘겨받아 "
                               "롤백합니다. 계속할까요? [y/N] ").strip().lower()
            except EOFError:
                answer = ""
            if answer not in ("y", "yes"):
                log.info("취소했습니다 — 아무것도 되돌리지 않았습니다. "
                         "(비대화형이면 `--rollback --yes`)")
                sys.exit(1)
            takeover = True
        try:
            result = folder_ops.rollback_migration(takeover=takeover)
        except Exception as exc:
            log.error(f"❌ 롤백 실패: {exc}")
            sys.exit(1)
        if not result["restored"]:                      # F-1: 성공이라고 말하지 않는다
            log.error(f"⚠️ 되돌린 것이 없습니다 — {result.get('detail') or ''}\n"
                      f"   저널이 이미 정리됐다면 호스트 백업으로 복원하세요: "
                      f"rm -rf output && mv output_backup_<ts> output")
            sys.exit(2)
        log.info(f"↩️ 롤백 완료 — {result['restored']}건 원복 "
                 f"({result['source']} 저널: {result['journal']})")
        if result.get("remaining"):
            log.info("   되돌릴 저널이 더 남아 있습니다 — 한 번 더 `--rollback` 하세요.")
        return

    from channel_registry import ChannelRegistry
    reg = ChannelRegistry()
    plan = folder_ops.plan_migration(reg)
    log.info(f"▢ 이동 예정 {len(plan['moves'])}건 · 유지 {len(plan['skipped'])}건 "
             f"· 미등록 잔존 폴더 {len(plan['unregistered'])}건(미이동)\n")
    for mv in plan["moves"]:
        log.info(f"  {mv['channel']:<28} output/{mv['channel']} → "
                 f"output/{mv['group']}/{mv['channel']}")
    if plan["skipped"]:
        log.info("\n[유지]")
        for sk in plan["skipped"]:
            log.info(f"  {sk['channel']:<28} {sk['reason']}")
    if plan["unregistered"]:
        log.info("\n[미등록 잔존 폴더 — 건드리지 않습니다 (FR32.4 수동 정리)]")
        for name in plan["unregistered"]:
            log.info(f"  output/{name}")

    if not args.apply:
        log.info("\n※ dry-run입니다. 아무것도 옮기지 않았습니다. "
                 "실제 이동은 `./yt.sh migrate-groups --apply`")
        return
    if not plan["moves"]:
        log.info("\n옮길 채널이 없습니다 (멱등).")
        return

    if not args.yes:                                    # ⑥ 대화형 확인
        try:
            answer = input(f"\n{len(plan['moves'])}개 채널 폴더를 이동합니다. "
                           f"계속할까요? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            log.info("취소했습니다 — 아무것도 옮기지 않았습니다.")
            return

    try:
        result = folder_ops.apply_migration(plan, reg)
    except Exception as exc:
        log.error(f"\n❌ {exc}")
        sys.exit(1)
    log.info(f"\n✅ 완료 — {result['moved']}건 이동 (저널 보관: {result['journal']})")
    if getattr(args, "backup_path", None):
        log.info(f"   백업: {args.backup_path} (자동 삭제하지 않습니다 — 직접 지우세요)")


def cmd_backfill_tickers(args):
    """
    기존 meta/*.json의 `tickers` 재계산 — 기본 dry-run. FR12.2 (DQ-43)

    구 규칙(6자리 숫자 전부 채택)이 남긴 오탐을 네트워크 없이 걷어낸다.
    제목·태그(meta) + 설명(desc/*.txt)만 쓰므로 재추출이 필요 없다.
    """
    from meta_collector import backfill_tickers
    from channel_registry import ChannelRegistry
    reg = ChannelRegistry()
    targets = [args.channel] if args.channel else reg.names()
    if not targets:
        log.info("등록된 채널이 없습니다.")
        return
    total = {"scanned": 0, "changed": 0, "removed": 0, "added": 0}
    for name in targets:
        stat = backfill_tickers(name, apply=args.apply)
        for k in total:
            total[k] += stat[k]
        if stat["changed"]:
            log.info(f"▢ {name}: {stat['changed']}/{stat['scanned']}건 변경 "
                     f"(제거 {stat['removed']} · 추가 {stat['added']})")
            for smp in stat["samples"]:
                log.info(f"    {smp['before']} → {smp['after']} | {smp['title'][:50]}")
        else:
            log.info(f"▢ {name}: 변경 없음 ({stat['scanned']}건 확인)")
    log.info(f"\n합계 {total['changed']}/{total['scanned']}건 변경 "
             f"(제거 {total['removed']} · 추가 {total['added']})")
    if not args.apply:
        log.info("※ dry-run입니다. 아무것도 쓰지 않았습니다. "
                 "실제 반영은 `./yt.sh backfill-tickers --apply`")


def correction_targets(reg, target: str = None) -> list:
    """`terms`·`correct`의 대상 채널 산출 — 채널명 → 폴더명 → 전체. FR40.6·40.18

    `bulk_targets`(FR34.7)와 달리 `auto_run`을 보지 않는다 — 교정은 추출이 아니라
    **이미 있는 파일을 읽는 작업**이고, 검색으로 유입된 채널의 자막도 같은 코퍼스다.
    """
    names = list(reg.names())
    if not target:
        return names
    if target in names:
        return [target]
    in_group = [n for n in names if (reg.get(n) or {}).get("group") == target]
    if in_group:
        return in_group
    return []


def cmd_terms(args):
    """
    교정 후보 계량 — **읽기 전용·네트워크 0**. FR40.18

    사전은 가이드를 베끼지 않고 이 코퍼스 실측으로 만든다(DQ-64: 가이드가 확실(○)로
    분류한 `디어`가 617건/192편 **전량 오탐**이었다). 이 명령은 그 절차를 도구화한다 —
    후보별 건수·영상 수·**어절 분포**(오탐의 직접 증거)·문맥 샘플·자동 판정 제안.
    후보 자동 발굴은 하지 않는다(측정된 병목은 발굴이 아니라 오탐 검증이다).
    """
    import json as _json
    from pathlib import Path
    import glossary
    from channel_registry import ChannelRegistry
    reg = ChannelRegistry()
    targets = correction_targets(reg, args.target)
    if not targets:
        log.info(f"대상을 찾을 수 없습니다: {args.target} (채널명 또는 폴더명)")
        return
    if not args.from_file:
        log.info("후보 파일이 필요합니다 — `--from glossary/seeds/guide-2026-08.txt`\n"
                 "  (가이드 시드도 **가설로서만** 이 경로로 들어옵니다 — FR40.18)")
        return
    path = config.BASE_DIR / args.from_file if not str(args.from_file).startswith("/") \
        else Path(args.from_file)
    if not path.exists():
        log.info(f"후보 파일이 없습니다: {path}")
        return
    cands = glossary.parse_candidates(path.read_text(encoding="utf-8"))
    log.info(f"▢ 후보 {len(cands)}개 · 채널 {len(targets)}개 계량 중 (읽기 전용)…")
    rows = glossary.survey(targets, cands, min_hits=args.min)
    if args.json:
        print(_json.dumps({"targets": targets, "candidates": rows},
                          ensure_ascii=False, indent=2))
    else:
        log.info(f"\n{'후보':<12} {'건수':>6} {'영상':>5} {'채널':>5} "
                 f"{'완전일치':>9} {'판정':<8} 어절 분포 상위")
        log.info("─" * 110)
        for r in rows:
            dist = " · ".join(f"{t['token']}({t['n']})" for t in r["tokens"][:5])
            log.info(f"{r['candidate']:<12} {r['hits']:>6} {r['videos']:>5} "
                     f"{r['channels']:>5} {r['exact']:>4}/{r['hits']:<4} "
                     f"{r['verdict']:<8} {dist}")
        log.info(f"\n※ `reject`는 어절 완전일치 비율 "
                 f"{int(glossary.EXACT_RATIO_MIN * 100)}% 미만 = 대부분이 다른 단어의 "
                 f"일부라는 뜻입니다. 채택하려면 `left_exclude`·`require`로 좁히세요.")
        log.info("※ 판정은 **제안**입니다 — 근거는 어절 분포입니다. 후보 뒤 1음절이 "
                 "우연히 조사와 같으면 완전일치가 부풀려집니다(예: `오케`의 `오케이`).")
        log.info("※ 이 명령은 아무것도 쓰지 않았습니다(사전 수정은 사람이 합니다).")
    if args.hold:
        # hold는 **규칙 단위**다(DQ-71) — 영상별 파일을 만들지 않는다. 상한 50.
        out = glossary.GLOSSARY_DIR / glossary.HOLD_DIRNAME / f"{args.hold}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(glossary.hold_report(args.hold, rows), encoding="utf-8")
        log.info(f"\n📝 후보 리포트: {out.relative_to(config.BASE_DIR)} "
                 f"(상위 50 · 사람이 보고 사전에 확정)")


def cmd_correct(args):
    """
    자막 용어 교정 — **dry-run이 기본**이다. FR40.13~40.14·40.21

    `--apply` 없이는 한 바이트도 쓰지 않고, 계획에 **교정 대상 편수 · 변경 예정 건수 ·
    재임베딩될 청크 수**를 함께 낸다(예고 없이 겪으면 사고로 보인다). 원본 `srt/`·`txt/`는
    어느 경로에서도 쓰지 않는다(FR40.3).
    """
    import json as _json
    import corrector
    from channel_registry import ChannelRegistry
    reg = ChannelRegistry()
    targets = correction_targets(reg, args.target)
    if not targets:
        log.info(f"대상을 찾을 수 없습니다: {args.target} (채널명 또는 폴더명)")
        return

    if args.status:                                  # FR40.22 — doctor 대신 여기 둔다
        st = corrector.status(targets)
        if args.json:
            print(_json.dumps(st, ensure_ascii=False, indent=2))
            return
        log.info(f"▢ 교정본 {st['corrected']}편 / 상태 기록 {st['scanned']}건 "
                 f"· 채널 {len(st['channels'])}개")
        for item in st["stale"]:
            log.info(f"  [stale] {item['channel']}/{item['basename']} — {item['reason']} "
                     f"→ 원본으로 폴백 중 (해소: correct --apply 재실행)")
        for item in st["orphans"]:
            log.info(f"  [고아]  {item['channel']}/{item['basename']} — {item['reason']}")
        if st["dead_rules"]:
            log.info(f"  [죽은 규칙] 적용 0건: {', '.join(st['dead_rules'])}")
        if not (st["stale"] or st["orphans"] or st["dead_rules"]):
            log.info("  이상 없음 (stale·고아·죽은 규칙 0건)")
        return

    total = {"videos": 0, "corrected": 0, "changes": 0, "reverted": 0,
             "excluded": 0, "chunks": 0, "held": 0}
    rule_stats, holds, plans = {}, [], []
    for name in targets:
        plan = corrector.correct_channel(name, apply=args.apply)
        plans.append(plan)
        for k in total:
            total[k] += plan["totals"][k]
        for rid, st in plan["rule_stats"].items():
            cur = rule_stats.setdefault(rid, {"applied": 0, "excluded": 0})
            cur["applied"] += st["applied"]
            cur["excluded"] += st["excluded"]
        holds += [dict(h, channel=name) for h in plan["holds"]]
        if plan["totals"]["corrected"] or plan["holds"]:
            log.info(f"▢ {name}: 교정 {plan['totals']['corrected']}/"
                     f"{plan['totals']['videos']}편 · 변경 {plan['totals']['changes']}건 "
                     f"· 원복 {plan['totals']['reverted']} · 배제 "
                     f"{plan['totals']['excluded']} · 사전 {plan['rules']}항목"
                     f"({'·'.join(plan['domains'])})")
    if args.json:
        print(_json.dumps({"totals": total, "rule_stats": rule_stats,
                           "holds": holds, "plans": plans},
                          ensure_ascii=False, indent=2))
        return

    log.info(f"\n합계 — 교정 대상 **{total['corrected']}편** / 전체 {total['videos']}편 "
             f"· 변경 **{total['changes']}건** · 원복 {total['reverted']} "
             f"· 배제 {total['excluded']}")
    log.info(f"     재임베딩 예상 **{total['chunks']}청크** "
             f"(FR33 증분 — 본문이 바뀐 영상만 다시 임베딩됩니다)")
    if rule_stats:
        log.info("\n[규칙별]  적용 / 배제   ※ 배제 0은 안전 조건이 죽었다는 신호입니다")
        for rid, st in sorted(rule_stats.items()):
            log.info(f"  {rid:<28} {st['applied']:>5} / {st['excluded']:<5}"
                     f"{'   ← 죽은 규칙(적용 0건)' if not st['applied'] else ''}")
    else:
        log.info("\n사전이 비어 있습니다 — 교정 결과 0건이 정상입니다(FR40.6).\n"
                 "  후보 계량: ./yt.sh terms [폴더|채널] "
                 "--from glossary/seeds/guide-2026-08.txt")
    if holds:
        log.info(f"\n[hold {len(holds)}건 — 회로차단·구조 위반으로 교정본을 쓰지 않았습니다]")
        for h in holds[:20]:
            log.info(f"  {h['channel']}/{h['basename']}: {h['reason']}")
    if not args.apply:
        log.info("\n※ dry-run입니다. 아무것도 쓰지 않았습니다. "
                 "실제 산출은 `./yt.sh correct [대상] --apply`\n"
                 "  (원본 srt/·txt/는 --apply에서도 쓰지 않습니다 — 산출물은 fix/ 하위뿐이고 "
                 "되돌리기는 그 디렉터리 삭제입니다)")
    else:
        log.info(f"\n✅ 교정본 산출 완료 — 다음 인덱싱이 교정 본문을 반영합니다 "
                 f"(`./yt.sh index`, 예상 {total['chunks']}청크)")


def _selfcheck(command, args):
    """
    `audit`·`doctor` 공통 실행부. FR38.4~38.6

    **고치지 않는다** — 발견을 출력하고 종료코드로만 말한다(FR38.1). 종료코드는
    0 이상없음 / 1 경고만 / 2 오류 / **3 점검 자체 실패**이며, 3을 분리하는 이유는
    "이상 없음"과 "확인 못 함"을 같은 코드로 내면 도구가 거짓말을 하기 때문이다.
    """
    import selfcheck
    import datetime
    started = datetime.datetime.now()          # 입력 적재부터 재야 성능 상한 감시가 된다
    try:
        if command == "audit":
            ctx = selfcheck.AuditContext(baseline=getattr(args, "baseline", None))
        else:
            ctx = selfcheck.DoctorContext(channel=getattr(args, "channel", None))
        result = selfcheck.run(command, ctx, only=args.check, strict=args.strict,
                               started=started)
    except selfcheck.CheckFailure as exc:
        print(f"[!] 점검 실패 — {exc}")
        sys.exit(3)
    if args.json:
        print(selfcheck.render_json(command, result["findings"], result["waived"],
                                    result["checks_run"], result["skipped"],
                                    result["elapsed"], result["exit_code"],
                                    stale=result["stale"]))
    else:
        print(selfcheck.render_text(command, result["findings"], result["waived"],
                                    result["checks_run"], result["skipped"],
                                    result["elapsed"], result["exit_code"]))
    sys.exit(result["exit_code"])


def cmd_audit(args):
    """문서·코드 정합 감사 — `output/`·네트워크 없이 완결. FR38.11~38.12"""
    _selfcheck("audit", args)


def cmd_doctor(args):
    """데이터 건전성 점검 — `output/` 전수, 읽기 전용. FR38.13~38.15"""
    _selfcheck("doctor", args)


def cmd_test(args):
    """검증 실행. 섹션 7"""
    import subprocess
    cmd = ["python", "-m", "pytest", "tests/", "-v"]
    if not args.integration:
        cmd += ["-m", "not integration"]
    subprocess.run(cmd, cwd=str(config.BASE_DIR))


def build_parser():
    p = argparse.ArgumentParser(prog="yt", description="YouTube 자막 KL 파이프라인")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("add", help="채널 등록 + 추출")
    sp.add_argument("url"); sp.add_argument("--lang", default=config.DEFAULT_LANG)
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("run", help="채널 업데이트")
    sp.add_argument("channel", nargs="?")
    sp.add_argument("--limit", type=int, help="카나리아 실행: 최대 N개 영상만 처리")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("transcribe", help="무자막 영상 Whisper 전사 (FR30)")
    sp.add_argument("channel", nargs="?")
    sp.add_argument("--limit", type=int, help="최대 N개만 전사")
    sp.set_defaults(func=cmd_transcribe)

    sp = sub.add_parser("review", help="품질 검토")
    sp.add_argument("channel", nargs="?"); sp.add_argument("--llm", action="store_true")
    sp.set_defaults(func=cmd_review)

    sp = sub.add_parser("reextract", help="SUSPECT 재추출")
    sp.add_argument("channel", nargs="?"); sp.set_defaults(func=cmd_reextract)

    sp = sub.add_parser("index", help="KL 인덱싱")
    sp.add_argument("channel", nargs="?"); sp.set_defaults(func=cmd_index)

    sp = sub.add_parser("list", help="채널 목록"); sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("remove", help="채널 삭제")
    sp.add_argument("channel"); sp.set_defaults(func=cmd_remove)

    sp = sub.add_parser("ask", help="질의 (RAG/멀티스텝)")
    sp.add_argument("channel"); sp.add_argument("question")
    sp.add_argument("--multistep", action="store_true")
    sp.add_argument("--since"); sp.add_argument("--until")
    sp.set_defaults(func=cmd_ask)

    sp = sub.add_parser("summarize", help="영상 요약")
    sp.add_argument("channel"); sp.add_argument("video_id")
    sp.set_defaults(func=cmd_summarize)

    sp = sub.add_parser("search", help="벡터 검색")
    sp.add_argument("channel"); sp.add_argument("query")
    sp.add_argument("--since"); sp.add_argument("--until")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("serve", help="대시보드 서버")
    sp.add_argument("--port", type=int, default=8800); sp.set_defaults(func=cmd_serve)

    # FR35.11 — 기본 dry-run, --apply 가 있어야 실제로 움직인다
    sp = sub.add_parser("migrate-groups", help="폴더 디렉터리화 마이그레이션 (기본 dry-run)")
    sp.add_argument("--apply", action="store_true", help="실제 이동 (사전 검증 통과 시)")
    sp.add_argument("--yes", action="store_true",
                    help="대화형 확인 생략 (--rollback 에서는 락 탈취 동의)")
    sp.add_argument("--rollback", action="store_true",
                    help="저널 역순 되돌리기 (완료된 마이그레이션 .done.json 포함)")
    sp.add_argument("--unlock", action="store_true", help="stale 락 수동 제거")
    # --no-backup / --backup-path 는 **호스트 셸(yt.sh)** 이 처리한다 (컨테이너에는
    # output/ 형제 경로를 만들 권한이 없다). 여기서는 인자만 수용한다.
    sp.add_argument("--no-backup", action="store_true",
                    help="호스트 자동 백업 생략 (yt.sh가 처리)")
    sp.add_argument("--backup-path", dest="backup_path",
                    help="yt.sh가 만든 백업 경로 (완료 메시지용)")
    sp.set_defaults(func=cmd_migrate_groups)

    # FR12.2 — 기본 dry-run, --apply 가 있어야 meta/*.json에 쓴다
    sp = sub.add_parser("backfill-tickers",
                        help="기존 meta의 tickers 재계산 (FR12.2, 기본 dry-run)")
    sp.add_argument("channel", nargs="?")
    sp.add_argument("--apply", action="store_true", help="실제 파일 쓰기")
    sp.set_defaults(func=cmd_backfill_tickers)

    # FR40.18 — 후보 계량. 읽기 전용이고 `--hold`만 리포트 파일을 쓴다
    sp = sub.add_parser("terms", help="교정 후보 계량 (FR40.18, 읽기 전용)")
    sp.add_argument("target", nargs="?", help="폴더명 또는 채널명 (생략 시 전체)")
    sp.add_argument("--from", dest="from_file", help="후보 파일 (한 줄에 후보 1개)")
    sp.add_argument("--min", type=int, default=1, help="이 건수 미만 후보는 생략")
    sp.add_argument("--hold", metavar="도메인",
                    help="후보 리포트를 glossary/hold/<도메인>.md로 저장 (상한 50)")
    sp.add_argument("--json", action="store_true", help="기계 판독 출력")
    sp.set_defaults(func=cmd_terms)

    # FR40.14 — 기본 dry-run. `--apply`가 있어야 fix/에 쓴다 (원본은 어느 경우에도 불변)
    sp = sub.add_parser("correct", help="자막 용어 교정 (FR40, 기본 dry-run)")
    sp.add_argument("target", nargs="?", help="폴더명 또는 채널명 (생략 시 전체)")
    sp.add_argument("--apply", action="store_true", help="교정본 산출 (fix/ 하위)")
    sp.add_argument("--status", action="store_true",
                    help="상태 점검 — stale·고아 교정본·죽은 규칙 (FR40.22)")
    sp.add_argument("--json", action="store_true", help="기계 판독 출력")
    sp.set_defaults(func=cmd_correct)

    # FR38 — 읽기 전용 점검 2종. `--fix` 류는 만들지 않는다(FR38.1·DQ-52)
    sp = sub.add_parser("audit", help="문서·코드 정합 감사 (읽기 전용, FR38)")
    sp.add_argument("--json", action="store_true", help="기계 판독 출력")
    sp.add_argument("--strict", action="store_true", help="경고도 실패로 취급")
    sp.add_argument("--check", action="append", help="검사ID 지정 (반복 가능)")
    sp.add_argument("--baseline", help='pytest 실측값 대조 (예: "185 passed / 1 skipped")')
    sp.set_defaults(func=cmd_audit)

    sp = sub.add_parser("doctor", help="데이터 건전성 점검 (읽기 전용 전수, FR38)")
    sp.add_argument("channel", nargs="?", help="지정 시 해당 채널만")
    sp.add_argument("--json", action="store_true", help="기계 판독 출력")
    sp.add_argument("--strict", action="store_true", help="경고도 실패로 취급")
    sp.add_argument("--check", action="append", help="검사ID 지정 (반복 가능)")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("test", help="검증 실행")
    sp.add_argument("--integration", action="store_true"); sp.set_defaults(func=cmd_test)

    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)
