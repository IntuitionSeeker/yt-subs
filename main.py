#!/usr/bin/env python3
"""CLI 진입점 — add / run / review / reextract / index / list / remove / ask / summarize / serve."""
import sys
import argparse
import logging

import config
from channel_registry import ChannelRegistry

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("main")


def cmd_add(args):
    """채널 등록 + 전체 자막 추출 자동 시작. FR7.2"""
    from extractor import Extractor
    reg = ChannelRegistry()
    name = reg.add(args.url, lang=args.lang)
    log.info(f"✅ 채널 등록: {name}")
    log.info("자막 추출을 시작합니다...\n")
    Extractor(reg.get(name)).run()


def cmd_run(args):
    """전체 또는 특정 채널 업데이트. FR7.3"""
    from extractor import Extractor
    reg = ChannelRegistry()
    targets = [args.channel] if args.channel else reg.names()
    if not targets:
        log.info("등록된 채널이 없습니다. './yt.sh add URL' 로 추가하세요.")
        return
    for name in targets:
        Extractor(reg.get(name)).run(limit=args.limit)


def cmd_review(args):
    """품질 검토. FR4"""
    from quality_checker import QualityChecker
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
    reg = ChannelRegistry()
    targets = [args.channel] if args.channel else reg.names()
    for name in targets:
        KLIndexer(name).index_all()


def cmd_list(args):
    """채널 목록. FR7.5"""
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

    sp = sub.add_parser("test", help="검증 실행")
    sp.add_argument("--integration", action="store_true"); sp.set_defaults(func=cmd_test)

    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)
