# 项目导航

小说指数由 Python 数据管线和原生 HTML/CSS/JavaScript 前端组成。没有 npm 安装或前端打包步骤；Node.js 仅用于前端离线回归。本文描述当前多平台版本，旧版图谱保留在 `docs/archive/`。

## 从哪里开始

| 目的 | 入口 |
| --- | --- |
| 初次安装、抓取、定时任务 | 根目录 `setup.bat`、[README](../README.md)、[发布与定时任务](发布与定时任务.md) |
| 先体验页面，不请求真实平台和 AI | `python scripts/build_release.py --demo`，然后 `python scripts/serve_preview.py 8000 --root dist` |
| 查看本机已有真实数据 | `python scripts/serve_preview.py 8000` |
| 全平台抓取并构建 | `python run_all.py` |
| 仅重新生成数据和分析 | `python run_all.py --no-scrape`（已配置 AI 时可能调用 AI） |
| 开发验证 | [CONTRIBUTING.md](../CONTRIBUTING.md)、[前端检查](前端检查.md) |
| 生成独立站点或开源源码包 | [发布与定时任务](发布与定时任务.md)、[GitHub 公开前检查](GitHub公开前检查.md) |

## 目录职责

| 路径 | 内容与约定 |
| --- | --- |
| `index.html` / `trend.html` / `author.html` | 榜单、类型风向标、创作灵感三个页面 |
| `js/` | `config.js` 榜单注册表；`nav.js` 共享导航；`utils.js` 格式化与数据质量；`export.js` 导出；其余三个文件对应页面 |
| `css/` | `style.css` 共享样式和榜单/风向标布局；`author.css` 创作灵感布局 |
| `config/` | `platforms.json` 是后端平台配置源，前端副本由同步检查校验；`source_release.json` 是源码发布清单 |
| `scrapers/` | 通用抓取能力与起点、晋江实现；番茄入口暂保留在根目录 |
| `scripts/` | 数据构建、发布、预览、调度和诊断；一次性迁移脚本在 `scripts/archive/` |
| `tests/` | 离线 Python 回归、Node.js 前端回归 |
| `examples/` | 合成示例数据，供无凭据演示和回归使用 |
| `docs/` | 当前操作文档、检查说明；`审查报告/` 记录历次检查，`archive/` 保存过时文档 |
| `licenses/` | 第三方许可证，配合根目录 `THIRD_PARTY_NOTICES.md` |
| `data/` / `api/` | 本机生成的原始快照、分析和静态接口，均被 Git 忽略 |
| `dist/` | 独立发布产物，当前发布由 `current.json` 指向，不手工修改历史 release |
| `.runtime/` | 任务锁、缓存、冷却状态和临时检查样本，不删除状态以绕过任务锁或冷却 |
| `logs/` | 运行日志；`archive/` 保存从根目录归档的旧日志，`analysis/` 保存历史文本分析和解读 |

`probe_*.py`、`smoke_*.py`、`verify_font_mapping.py` 是诊断入口，保留现有位置以兼容调用。`晋江爬虫/`、`Scrapling-main/` 和 `probe_out/` 是本机参考与实验目录，不参与站点和源码发布。

## 数据如何到达前端

```text
config/platforms.json → 爬虫 → data/{slug}_ranks_YYYYMMDD.json
                                   ↓ scripts/build_latest.py
           latest / dates / trends / market_summary / author / api
                                   ↓ 本地静态服务或独立发布目录
                      榜单 / 类型风向标 / 创作灵感
```

`slug` 为 `{平台}_{榜单}`，例如 `qidian_hotsales`。三个页面用 `?rank=<slug>` 选择榜单；旧番茄参数继续兼容。新增榜单时同时修改 `config/platforms.json`、`js/config.js` 并运行配置同步检查。

## 本地文件归档

2026-09-08 将根目录 12 个 `logs_*.txt` 和 19 个 `分析_*` 文件分别移入 `logs/archive/` 与 `logs/analysis/`。文件内容逐一以 SHA-256 校验，移动清单保存在本机 `logs/archive/整理清单_20260908.json`，可按清单恢复原位置。历史审查报告中的旧文件名按该清单查找。

后续运行日志写入 `logs/`，分析文本写入 `logs/analysis/`。`scripts/analyze_today.py` 和 `scripts/analyze_deep.py` 默认打印到终端，需要留存时将输出重定向到上述目录。
