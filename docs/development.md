# 开发、测试与打包

Python 3.11+；开发前端测试需要 Node.js 20+。项目无需 npm 安装或前端构建。

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
node --check web/app.js
node --check web/ai.js
node --check web/standards.js
node --test tests/test_xiumi_ui.cjs tests/test_frontend.cjs
```

大部分浏览器作业测试使用内存替身，不打开 Chrome 或访问秀米。一个可选上游导航兼容性测试，在未安装外部源码时跳过；不影响本地排版测试。

GitHub Actions 在 Windows / Ubuntu、Python 3.11 / 3.12 上运行自动测试，并验证构建的 wheel 包可以携带静态资源启动本机服务。

## 构建独立安装包

```bash
python -m pip wheel --no-deps --wheel-dir dist .
```

安装到另一个虚拟环境后，在项目目录之外执行 `paper-studio --open`。安装包包含HTML、CSS、JS、示例图片与主题文件；用户数据默认存于用户应用目录，不写入 site-packages。源码运行继续使用项目的 `data/`。

可用 `PAPER_STUDIO_DATA_DIR` 或 `--data-dir` 自定义数据位置。

## 目录

| 路径 | 用途 |
| --- | --- |
| `paperstudio/` | 文稿、渲染、HTTP服务、任务与外部适配 |
| `web/` | 应用静态资源，打包为 `paperstudio_web` |
| `tests/` | 自动测试与本地界面测试夹具 |
| `docs/` | 公开使用说明和脱敏截图 |
| `data/` | 运行数据，不提交 |
| `artifacts/` | 本地测试产物与历史记录，不提交 |

不要把 API Key 或用户文稿写入测试夹具。新的远端写入必须保留原稿保护、幂等请求、结果不确定时停止及重新打开核对机制。
