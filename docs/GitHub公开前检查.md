# GitHub 公开前检查

更新：2026-09-08。本清单区分已落实到项目的防护与建仓后才能设置的 GitHub 选项。通过功能测试不等于完成公开发布的安全检查。

## 项目风险与对应调整

| 风险 | 本项目情况 | 对应处理 |
| --- | --- | --- |
| API Key 混入源码、README、前端或生成 JSON | 原打包器只检查路径，无法识别允许文件中的密钥 | 源码、静态站点打包加入内容检查；识别常见凭据格式，并读取本地环境配置作精确比对；命中即停止，不生成新成品/替换指针。输出仅含路径、行号和规则。 |
| 删除文件后仍留在 Git 历史 | 原历史含 `.claude/settings.local.json` 和任务锁文件；既有来源问题也涉及旧历史 | 首次使用新源码包建仓；新增全部暂存内容、可达历史对象及历史路径扫描。未删除或改写原仓库。 |
| 日志、异常和对象打印泄露密钥 | 原脱敏仅识别部分 sk 前缀及 URL 参数，未保护原始异常链或 Endpoint repr | 按实际配置密钥及常见认证格式脱敏；Endpoint 不显示密钥/URL；抛出的 AI 异常隐藏原始 SDK 异常链。日志仍视为私有文件。 |
| API 密钥经明文网络或 URL 发送 | BASE_URL 原先没有传输安全约束 | 远程只接受 HTTPS，HTTP 仅允许 localhost/回环 IP；拒绝 URL 用户信息、查询串和片段；凭据放 API_KEY。 |
| Cookie、浏览器状态、证书、数据库或 HAR 误提交 | 根目录新增这类文件时，旧忽略规则不充分 | 扩充忽略与发布路径拒绝规则；Git hooks 扫描真实暂存内容，不依赖工作副本是否已删掉密钥。 |
| 页面把外部数据当 HTML | 主榜书卡 rank 输出原先未转义，但当前流程会先重建为数字排名，未复现该字段可利用的 XSS | 补充转义作为防御；书名/作者/简介/AI Markdown 已经过转义；CSV/Excel 公式转义继续保留。 |
| 第三方脚本、PR 或 Actions 凭据导致供应链风险 | 页面已有固定 SheetJS URL/SRI；CI 为只读、无需业务密钥，但 Actions 使用可变标签 | Actions 固定官方提交 SHA，checkout 不持久化凭据，新增公开内容/历史检查；Dependabot 定期提出依赖更新。没有 pull_request_target、自动部署或真实 API 调用。 |
| 静态托管错误暴露整个工作区 | 普通文件服务器会暴露 .env、Git 或数据库；原演示成品还带有一个空运行锁文件 | 演示构建也改为工作目录与成品分离，只复制公开文件；发布只使用 dist/releases 下具体版本；预览白名单继续拒绝私有路径，并增加 nosniff、no-referrer、禁止框架嵌入响应头。 |
| 真实作品、个人信息和许可证资料误公开 | 旧历史/参考目录/截图不属于默认发布内容 | 源码包只含合成示例与已记录来源的当前实现；真实数据另行核对公开范围；截图、提交作者邮箱、日志附件需人工检查。 |

`api/` 是供页面读取的公开榜单 JSON，不是保存 API Key 的配置目录。AI 请求只在 Python 构建时发送；前端不向 AI 供应商发送请求，也不存储业务密钥。

## 首次发布：使用最新源码包建立新仓库

在原工作区运行：

```text
python scripts/check_publication.py
python scripts/build_source_release.py
```

检查生成的 `NovelIndex/` 目录与包哈希。源码包没有 `.git`；不要复制原工作区的 `.git` 进去，不要直接上传整个工作区或旧版本 ZIP。

在新的 `NovelIndex/` 目录初始化仓库后，启用本地提交/推送检查：

```text
git init
git config core.hooksPath .githooks
```

Linux/macOS 还需执行 `chmod +x .githooks/pre-commit .githooks/pre-push`。hook 优先使用 NOVELINDEX_PYTHON 指定的解释器路径，其次项目 .venv，最后 PATH 中的 Python。NOVELINDEX_PYTHON 只填可执行文件路径，不填参数。

添加待提交文件后检查实际暂存内容；建立提交后检查历史：

```text
python scripts/check_publication.py --staged
python scripts/check_publication.py --staged --history
```

hooks 默认不随 Git clone 自动启用，每位贡献者需要执行一次 core.hooksPath 配置。pre-push 会在 Git 向远端发送对象前检查全部本地可达历史；CI 再做一次检查。GitHub Actions 使用完整检出；浅克隆运行历史检查会失败，避免把不完整历史标为通过。

原工作区运行 `--history` 时应命中历史本机配置路径。这不代表新源码包仍含这些文件，也不应通过关闭检查或添加忽略来伪装原历史已经清理。

## GitHub 建仓后的设置（尚未代为执行）

- 使用新源码目录的首次提交。公开前检查 Git 提交作者邮箱；需要隐藏个人邮箱时，在 GitHub 邮箱设置取得自己的 noreply 地址，再设置此新仓库的 user.email。
- 在仓库 Security/Settings 中确认 secret scanning、push protection 与 private vulnerability reporting 已启用；不要把业务 API Key 加入公开文件或普通 Actions Variables。
- 开启 Dependabot alerts/security updates。更新 PR 需通过测试并人工合并，不设置自动合并。
- 保护默认分支，要求通过 CI；CI 不需要 AI 密钥。来自 fork 的 PR 不应获得业务 Secrets 或部署权限，不把不可信 PR 交给个人自托管 runner。
- GitHub Pages 如需部署，只上传选定的 `dist/releases/<编号>/` 内容，不部署根目录；服务端响应头需在托管平台单独配置，Python 预览器的响应头不会自动带到 Pages。

## 日常发布与泄露处置

每次改动后，运行测试及 `check_publication.py`，重新构建源码包/静态版本；发布目录也可单独检查：

```text
python scripts/check_publication.py --directory dist/releases/<编号>
```

不要在公开 Issue、日志、截图、Actions artifact、Release 附件中上传 `.env`、认证请求头、Cookie、HAR、浏览器状态、数据库或整个工作区。先确认内容，再上传最小复现。扫描不解析图像中的文字，也不能保证识别自定义密钥及个人信息。

一旦凭据实际公开，先在供应商撤销/轮换，检查用量，再处理历史、fork、缓存或附件。当前检查未发现匹配不等于证明凭据从未在其他渠道公开。

官方依据：[GitHub secret scanning 范围](https://docs.github.com/en/code-security/reference/secret-security/secret-scanning-scope)、[push protection](https://docs.github.com/en/code-security/concepts/secret-security/push-protection)、[Actions 安全使用](https://docs.github.com/en/actions/reference/security/secure-use)、[移除敏感数据](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)。
