# 第三方来源与授权

核对日期：2026-09-08。根目录 LICENSE 适用于 NovelIndex 贡献者拥有权利的代码、文档和合成示例；不覆盖第三方代码、网站作品内容、封面、商标或历史版本中的第三方移植代码。

## 浏览器依赖

SheetJS Community Edition 0.20.0 — https://sheetjs.com/

Copyright (C) 2012-present SheetJS LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

页面通过官方 CDN 加载固定版本并使用 SHA-384 SRI，不打包此库；断网时 JSON/CSV 可用，Excel 导出提示依赖未加载。官方许可说明：https://cdn.sheetjs.com/ 。

## Python 依赖（由安装器安装，不随静态发布目录分发）

| 依赖 | 上游许可 | 来源 |
| --- | --- | --- |
| playwright | Apache-2.0 | https://github.com/microsoft/playwright-python |
| patchright | Apache-2.0 | https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python |
| openai | Apache-2.0 | https://github.com/openai/openai-python |
| python-dotenv | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| ruff（开发工具） | MIT | https://github.com/astral-sh/ruff |

浏览器、上述包的传递依赖由各自安装工具提供；其随包许可证继续适用。本项目不重新打包 Chrome、Chromium 或 Python 运行时。

## 参考与移植记录

| 来源 | 涉及位置 | 核对结论 |
| --- | --- | --- |
| Scrapling 0.4.15，https://github.com/D4Vinci/Scrapling | scrapers/base.py 资源拦截清单及浏览器会话优化参考 | 本地 LICENSE 为 BSD-3-Clause；保留全文于 licenses/Scrapling-LICENSE.txt。整包不分发。 |
| https://github.com/dylan-byte-max/novel-tracker | 历史浏览器注入、起点 JS 页面提取与标签/频道推断 | **已替代识别出的移植部分**：当前使用 Python HTMLParser、显式类别链接和浏览器原生配置；旧 JS/关键词推断已移除。未获得或伪造上游授权，旧 Git 历史不在源码发布包内。 |
| https://github.com/lien-Gu/JJClawler-full | 历史 API、Header 与请求策略参考 | **已替代相关实现**：当前使用本项目 JsonHttpClient、最小请求头和字段解析；不使用上游 HTTP 客户端、熔断器或配置。保留来源致谢；平台端点和字段是对接协议。 |
| 晋江爬虫/ 下另两个参考目录 | 本地参考资料 | 不分发。未发现生产代码对其直接导入；不代表其自身获得 MIT 授权。 |

本次按实际代码对照识别并替代相关部分，未把这两个项目重新声明为 MIT。当前源码包通过显式清单排除旧 Git 历史及参考整包；直接推送原仓库全部历史仍会包含替代前版本。核对的提交号和文件哈希见 docs/source-evidence.json，替代范围与验证见 docs/来源核对.md。

## 示例与采集数据

examples/demo_seed.json 及其生成结果均为合成示例，按本项目 MIT 许可提供，不包含真实书籍简介、封面或实际榜单数据。真实采集数据不随源码提交；公开部署真实数据前需另行核对使用范围。项目许可证不授予任何第三方网站内容的权利。
