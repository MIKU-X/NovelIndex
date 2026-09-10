"""少量真实起点检查：一页分类榜、首本详情，可选一页三江。

复用正式抓取的任务锁、冷却与 HTTP 检查；不写 data/ 和 api/。
结果放入 probe_out/live_qidian/，遇拦截立即停止。
用法：python -X utf8 smoke_qidian_patchright.py [--rank-only | --sanjiang]
"""
import argparse
import time
from pathlib import Path

from config.environment import load_environment
from scrapers.base import write_json
from scrapers.qidian import (
    IpBlockedError,
    NetworkUnavailable,
    QidianScraper,
    _is_captcha_page,
)
from scrapers.qidian_html import parse_detail, parse_rank, parse_sanjiang
from scrapers.qidian_state import QidianState, observed_now
from scripts import runtime_state
from scripts.runtime_state import single_run

RANK_URL = "https://www.qidian.com/rank/hotsales/chn1/"
OUTPUT_DIR = Path(__file__).resolve().parent / "probe_out" / "live_qidian"


def check_page(scraper, page, url, selector, parse, *, detail=False):
    started = time.monotonic()
    response = scraper._navigate(page, url, detail=detail)
    try:
        page.wait_for_selector(selector, timeout=10000)
    except Exception:
        if _is_captcha_page(page, use_fallback=False):
            scraper._block_site("少量检查遇到验证码，暂停起点请求")
        raise
    if _is_captcha_page(page, use_fallback=False):
        scraper._block_site("少量检查遇到验证码，暂停起点请求")
    payload = parse(page.content())
    return {"url": url, "status": response.status if response else None,
            "seconds": round(time.monotonic() - started, 2), "payload": payload}


@single_run("scrape-qidian")
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--rank-only", action="store_true", help="只检查一页榜单，不请求详情或三江")
    scope.add_argument("--sanjiang", action="store_true", help="额外检查一页三江榜")
    args = parser.parse_args()
    load_environment()
    scraper = QidianScraper()
    scraper.max_detail_requests = 1
    state = QidianState(runtime_state.RUNTIME_DIR / "qidian.sqlite3")
    scraper._state = state
    report = {"started_at": observed_now(), "checks": [], "ok": False}
    code = 1
    try:
        remaining, reason = state.remaining_cooldown()
        if remaining:
            report["error"] = f"已有冷却，剩余 {remaining} 秒：{reason}"
            code = 75
            return code
        factory = scraper._sync_playwright()
        if factory.__module__.split(".")[0] != "patchright":
            raise RuntimeError("需要安装 patchright 后验证正式引擎")
        with factory() as driver:
            browser, context, page = scraper.launch_browser(driver, headless=True)
            failed_resources = []
            page.on("requestfailed", lambda request: failed_resources.append(request.resource_type))
            try:
                rank = check_page(scraper, page, RANK_URL, '.book-img-text li', parse_rank)
                report["checks"].append(rank)
                books = rank["payload"]
                if not books or not books[0].get("bookId") or not books[0].get("bookName"):
                    raise ValueError("榜单提取为空或缺少书籍身份")
                if not books[0].get("thumbUrl"):
                    raise ValueError("首本封面地址缺失")
                print(f"榜单：HTTP {rank['status']}，{len(books)} 本，{rank['seconds']} 秒")
                if args.rank_only:
                    report["failed_resource_types"] = sorted(set(failed_resources))
                    report["browser_version"] = browser.version
                    report["ok"] = True
                    code = 0
                    return code
                time.sleep(5)
                detail_url = f"https://www.qidian.com/book/{books[0]['bookId']}/"
                detail = check_page(scraper, page, detail_url, '.count em, .book-info em',
                                    parse_detail, detail=True)
                report["checks"].append(detail)
                if not all(detail["payload"].get(key) for key in ("wc", "author", "intro")):
                    raise ValueError("详情字数/作者/简介未完整提取")
                print(f"详情：HTTP {detail['status']}，字数/作者/简介齐全，{detail['seconds']} 秒")
                if args.sanjiang:
                    time.sleep(5)
                    sanjiang = check_page(scraper, page, "https://www.qidian.com/sanjiang/",
                                          '.strongrec-wrap .book-list li', parse_sanjiang)
                    report["checks"].append(sanjiang)
                    if not sanjiang["payload"]:
                        raise ValueError("三江榜提取为空")
                    print(f"三江：HTTP {sanjiang['status']}，{len(sanjiang['payload'])} 本，{sanjiang['seconds']} 秒")
                report["failed_resource_types"] = sorted(set(failed_resources))
                report["browser_version"] = browser.version
                report["ok"] = True
                code = 0
            finally:
                browser.close()
    except (IpBlockedError, NetworkUnavailable) as exc:
        report["error"] = str(exc)
        code = 75
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        report["finished_at"] = observed_now()
        report["navigation_counts"] = scraper._stats
        report["cooldown_seconds_remaining"], report["cooldown_reason"] = state.remaining_cooldown()
        state.close()
        scraper._state = None
        output = OUTPUT_DIR / f"check_{time.time_ns()}.json"
        write_json(str(output), report)
        print(f"检查{'通过' if report['ok'] else '未通过'}：{report.get('error', '')}；报告 {output}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
