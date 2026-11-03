# 第三方来源

## doocs/md 主题样式

本项目 v0.5 引入 [doocs/md](https://github.com/doocs/md) 的 base、default、simple、grace CSS，来源日期为 2026-10-29。文件位于 `paperstudio/vendor/doocs-md/`，原始许可见同目录 `LICENSE`（WTFPL Version 2）。`sources.json` 保留每个上游文件的 Git blob SHA 和原始链接。

原始 CSS 与许可文件按上游字节保存，并核对 Git blob SHA 一致。作者标注保留在 CSS 中：grace 主题标注 @brzhang；simple 主题标注 @okooo5km。

`paperstudio/open_themes.py` 是本项目独立编写的适配器：把本站文稿转换为段落、小标题、引用与图片，按上游的 base → default → variant 顺序应用 CSS，解析变量和常用计算并转成行内样式；新增本站文稿封面与署名适配。未集成 doocs/md 的完整 Markdown、AI、图床或发布系统，也不代表得到原项目背书。

## 仅作实现参考的项目

- [mdnice/markdown-nice](https://github.com/mdnice/markdown-nice)：查看主题切换与样式编辑设计，未复制源码。
- [lyricat/wechat-format](https://github.com/lyricat/wechat-format)：查看语义区块、基础排版与主题拆分，未复制源码。

## 可选秀米适配器

[hanselhan23/all-to-xiumi](https://github.com/hanselhan23/all-to-xiumi) 作为可选外部源码在运行时调用。本仓库没有复制或打包其源码；安装者应自行核对该上游版本的许可和依赖。适配层使用其内部函数，因此不保证不同上游版本都兼容。没有官方秀米授权或稳定 API 的含义。

## 示例摄影

- 文件：`web/auditorium.jpg`
- 作者：Phil Roeder
- 作品：[Open Seating (16614957680)](https://commons.wikimedia.org/wiki/File:Open_Seating_(16614957680).jpg)
- 许可：[CC BY 2.0](https://creativecommons.org/licenses/by/2.0/)
- 本地版本为1280px预览，仅缩小尺寸。预览和导出均保留作者、来源、许可及尺寸说明。

摄影是虚构排版示例的示意配图，不能代表示例所写活动的现场。`web/sample.png` 是历史几何示意图，保留以兼容旧文稿。

## 其他资源

应用图标为本项目中的内联 SVG。示例人物与叙事为虚构；内置文字规范仅保留通用格式规则，没有分发原始运营文档或人员名单。项目自己的 MIT 许可不替代上述第三方许可。
