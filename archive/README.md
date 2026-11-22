# 历史资料索引

清理前的工作区保存在 Git 标签 `archive/pre-cleanup-20261122`（`736f8ec`）。旧 UI 原型在 `archive/UI/`，vNext 产品、架构与专家评审在 `archive/next-major/`，1.x 和 2.x 的阶段报告与证据分别在 `archive/1.x/`、`archive/ads-v2/`。旧脚本、资源和完整历次版本记录也能从该标签查阅。

在项目根目录运行以下命令可以查看旧文件清单或读出指定文档。

```sh
git ls-tree -r --name-only archive/pre-cleanup-20261122 -- archive releases
git show 'archive/pre-cleanup-20261122:releases/版本发布记录.md'
```

本机的 `Stock-Abnormal-frozen-20261029.tar.xz` 保留独立 Git 仓库、原分支、未提交改动和七份推广物料。提交为 `af7175b`，已验证完整解包并核对 Git 状态。副本里的三份凭据统一保留在项目的 `.local/key/`。压缩包由主仓库忽略。

```sh
tar -xJf archive/Stock-Abnormal-frozen-20261029.tar.xz -C /tmp
```

压缩包 SHA-256 为 `e97458a89ecebc25b3e69b5aeb3513839ec097a70032e06e487392bfbe3cb433`。

版本回执、最终验收和正式版快照见[发布归档](../releases/README.md)。日常维护入口见[现行文档](../docs/README.md)。
