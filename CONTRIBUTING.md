# 参与开发

使用 Python 3.10+；前端离线检查需要 Node.js 18+。平台配置以 `config/platforms.json` 为准，修改榜单时同步 `js/config.js`。

目录职责与日常入口见 [项目导航](docs/PROJECT_MAP.md)，页面、响应式布局和交互验收见 [前端检查](docs/前端检查.md)。

提交前运行：

```text
python -m unittest discover -s tests -v
python smoke_ai_client.py
python scripts/check_config_sync.py
node --test tests/frontend_quality.test.cjs
python -m ruff check .
python scripts/check_publication.py
python scripts/check_publication.py --staged --history
```

这些检查使用临时数据和模拟服务，不发起真实爬取。测试包含合成站点构建、发布隔离与 Windows 批处理入口；真正注册 Windows 临时任务的验收单独运行 `scripts/test_scheduled_task.ps1`。

不要提交 `.env`、真实榜单、日志、任务状态、数据库、`dist/` 或第三方参考目录。新增依赖、移植或参考实现请同时补齐来源与许可记录，不要只写“来自 GitHub”。历史来源、替代范围及发布边界见 [来源核对](docs/来源核对.md)。

首次体验可运行 `python scripts/build_release.py --demo`，无需先爬取真实数据。

在新的公开源码仓库执行 `git config core.hooksPath .githooks` 启用本地提交/推送前检查；Linux/macOS 先给 hooks 执行权限。hooks 不随 clone 自动启用。检查边界、GitHub 设置及旧历史处理见 [GitHub 公开前检查](docs/GitHub公开前检查.md)，敏感问题请按 [SECURITY.md](SECURITY.md) 私下报告。
