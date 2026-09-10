# 合成示例

`demo_seed.json` 是可提交的示例输入，按根目录 MIT 许可提供。日期、作者、书名片段、简介和指标均用于功能演示，不代表真实作品或排名。

在项目根目录执行 `python scripts/build_release.py --demo`，会生成包含 9 个榜单、3 天历史、分类 API、趋势和规则分析的独立发布目录；不需要网络或 AI 密钥，也不写入现有 `data/` / `api/`。

再执行 `python scripts/serve_preview.py 8000 --root dist` 查看。完整说明见 [发布与定时任务](../docs/发布与定时任务.md)。
