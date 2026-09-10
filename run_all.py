"""统一调度入口：依次运行所有平台爬虫 + 构建引擎。

用法：
    python run_all.py                # 全量：所有平台爬虫 + build_latest
    python run_all.py --no-scrape    # 跳过爬虫，仅重新构建数据
    python run_all.py --platform qidian   # 仅抓起点并构建

番茄爬虫仍是根目录的 scrape_fanqie_ranks.py（Playwright，耗时 10-15 分钟）。
新平台爬虫在 scrapers/ 包内。晋江无需浏览器；番茄用 Playwright，起点用 patchright
（Playwright 反检测分支）+ 真实 Chrome 无头抓取。
"""
import datetime
import os
import subprocess
import sys

from scripts.runtime_state import single_run

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 平台 -> 爬虫调用方式
# fanqie: 根目录脚本（保持原状）；其他: scrapers 包模块
PLATFORM_SCRAPERS = {
    "fanqie":  [sys.executable, os.path.join(BASE_DIR, "scrape_fanqie_ranks.py")],
    "qidian":  [sys.executable, "-m", "scrapers.qidian"],
    "jjwxc":   [sys.executable, "-m", "scrapers.jjwxc"],
    # zongheng / qimao 待接入后启用
}


def _decode_line(raw: bytes) -> str:
    """子进程输出按 UTF-8 优先解码，失败回退 GBK（未 reconfigure 的子进程）。

    真实爬虫/构建脚本（scrape_fanqie_ranks/jjwxc/base/build_latest）均已
    reconfigure stdout 为 UTF-8；回退分支仅为兼容 setup.bat 等遗留子进程。
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("gbk", errors="replace")
        except Exception:
            return raw.decode("utf-8", errors="replace")


def run_cmd(cmd, label):
    print(f"\n{'='*60}")
    print(f"▶ {label}")
    print(f"{'='*60}")
    # 子进程实时输出：强制 Python 不缓冲 stdout/stderr。
    # 否则经管道/tee 时输出会被攒在缓冲区，长时间无日志写入，易误判爬虫卡死
    # （如起点逐本详情页补全 20+ 分钟，期间日志文件看似静止，实则在正常推进）。
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    # 调度层日志：run_all 自身的 stdout 按日期落盘，供定时任务等无终端场景排查
    # （各爬虫已有自己的 task_state 续传，但调度层的成败/退出码此前无处可查）。
    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"run_all_{datetime.date.today():%Y%m%d}.log")
    # N48: 先打开日志文件（含行缓冲落盘）再启动子进程——原先顺序相反，日志打开失败
    # （权限/锁定）时子进程已被 Popen 但无人接管 stdout 管道，成为孤儿：管道写满后
    # 阻塞/崩溃，且异常分支不会 kill。开日志成功是启动子进程的前置条件。
    try:
        lf = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)
    except OSError as e:
        print(f"❌ {label} 未启动：调度日志无法打开 {log_path}（{e}）")
        return False
    proc = None
    try:
        # N3: 去掉 bufsize=1——stdout=PIPE 是二进制模式，Python 3.14 不支持 line buffering
        # 会打印 RuntimeWarning（"line buffering isn't supported in binary mode"）。
        # `for raw in proc.stdout` 逐行迭代读取本就不受影响，去掉即可消除噪音。
        proc = subprocess.Popen(
            cmd, cwd=BASE_DIR, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        # N0: 日志句柄用行缓冲（buffering=1 文本模式有效），每行实时落盘——
        # 原先默认块缓冲（非 tty 下 8KB），爬虫运行期日志滞留内存、崩溃时整批丢失，
        # 实测 run_all_20260809.log 在进程结束前一直是 0 字节。
        lf.write(f"\n--- {datetime.datetime.now():%H:%M:%S} ▶ {label} ---\n")
        lf.flush()
        for raw in proc.stdout:
            line = _decode_line(raw)
            print(line, end="", flush=True)
            lf.write(line)
        proc.wait()
        if proc.returncode != 0:
            print(f"❌ {label} 失败：进程退出码 {proc.returncode}"
                  f"（详见上方日志，调度日志：{log_path}）")
            return False
        return True
    except Exception as e:
        print(f"❌ {label} 异常：{e}")
        return False
    finally:
        # 异常和 Ctrl+C 都必须回收子进程，避免后台继续抓取并占有任务锁。
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=10)
            except Exception:
                pass
        if proc is not None and proc.stdout is not None:
            proc.stdout.close()
        lf.close()


@single_run("pipeline")
def main():
    import argparse
    parser = argparse.ArgumentParser(description="全平台抓取 + 构建调度")
    parser.add_argument("--no-scrape", action="store_true", help="跳过爬虫，仅构建数据")
    parser.add_argument("--platform", type=str, default="",
                        help="仅抓取并构建指定平台（fanqie/qidian/jjwxc）")
    parser.add_argument("--no-build", action="store_true", help="跳过构建引擎")
    parser.add_argument("--no-ai", action="store_true", help="构建时不调用 AI，仅用规则分析")
    args = parser.parse_args()

    # N6: 前置调用配置同步校验（软门禁，仅警告不阻断）
    check_cmd = [sys.executable, os.path.join(BASE_DIR, "scripts", "check_config_sync.py")]
    try:
        check_result = subprocess.run(check_cmd, cwd=BASE_DIR, check=False)
        if check_result.returncode != 0:
            print("⚠️  配置同步校验未通过（前端 config.js 与 platforms.json 存在差异），"
                  "不阻断抓取，请后续手动同步前端 config.js")
    except Exception as e:
        print(f"⚠️  配置同步校验执行异常（不阻断）: {e}")

    platforms = [args.platform] if args.platform else list(PLATFORM_SCRAPERS.keys())

    # N3: 累计失败计数，全部跑完后汇总非零退出
    failed = []

    if not args.no_scrape:
        for plat in platforms:
            if plat in PLATFORM_SCRAPERS:
                if not run_cmd(PLATFORM_SCRAPERS[plat], f"抓取 {plat}"):
                    failed.append(f"抓取 {plat}")
            else:
                # NEW-B7: 未知平台计入失败，避免「全部完成」假绿灯
                print(f"⚠️  未知平台 {plat}，跳过")
                failed.append(f"未知平台 {plat}")

    if not args.no_build:
        build_cmd = [sys.executable, os.path.join(BASE_DIR, "scripts", "build_latest.py")]
        if args.no_ai:
            build_cmd.append("--no-ai")
        if args.platform:
            # 单平台构建：遍历该平台所有榜单 slug 逐个构建（修复原 ranks[0] 只建首个、多榜平台漏建的问题）
            from config.loader import load_config
            plat = load_config()["platforms"].get(args.platform, {})
            ranks = list(plat.get("ranks", {}).keys())
            if ranks:
                for rank_id in ranks:
                    slug = f"{args.platform}_{rank_id}"
                    if not run_cmd(build_cmd + ["--rank-type", slug], f"构建数据（build_latest: {slug}）"):
                        failed.append(f"构建 {slug}")
            else:
                print(f"⚠️  平台 {args.platform} 无已配置榜单，跳过构建")
                if args.platform not in PLATFORM_SCRAPERS:
                    failed.append(f"无榜单可构建 {args.platform}")
        else:
            if not run_cmd(build_cmd, "构建数据（build_latest）"):
                failed.append("构建数据（build_latest）")

    if failed:
        print("\n❌ 以下任务失败：")
        for label in failed:
            print(f"  - {label}")
        print(f"共 {len(failed)} 项失败")
        sys.exit(1)

    # N7: 全部成功后清理非当日的 task_state（断点续传只对当天文件有效，历史 task_state
    # 即使对应快照存在也永不会被续传），避免 data/ 长期累积（此前累积 200+ 个）。
    # --all：删所有非当日；失败时跳过——保留当天 task_state 供续传。
    try:
        cleanup_cmd = [
            sys.executable, os.path.join(BASE_DIR, "scripts", "cleanup_task_states.py"), "--all",
        ]
        subprocess.run(cleanup_cmd, cwd=BASE_DIR, check=False)
    except Exception as e:
        print(f"⚠️  task_state 清理执行异常（不阻断）: {e}")

    print("\n🎉 全部完成")


if __name__ == "__main__":
    main()
