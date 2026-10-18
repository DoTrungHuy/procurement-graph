# 网站调整更新包（2026-10-18）

本包只包含前端源码、生产构建文件和后端 Excel 导出修复，不含数据库、上传文件、模型配置、令牌或缓存。它是网站更新包，不是完整比赛提交包。

## 阿里云 Workbench 更新

先通过 Workbench 上传 `competition-site-readiness-20261018.tar.gz` 到服务器 `/tmp/`。在网页终端执行：

```bash
mkdir -p /tmp/competition-site-readiness-20261018
tar -xzf /tmp/competition-site-readiness-20261018.tar.gz -C /tmp/competition-site-readiness-20261018
cd /tmp/competition-site-readiness-20261018/competition-site-update
python3 scripts/apply_site_update.py --validate-only
python3 scripts/apply_site_update.py --root /opt/procurement-project-next
python3 scripts/apply_site_update.py --root /opt/procurement-project-next --apply
```

前两条 Python 命令只做检查。最后一条在确认服务属于此目录、当前没有抽取任务后，备份待替换源码，更新代码并重建原 Compose 项目的 api/web 镜像。沿用现有 Compose 文件和私有 `.env`；不运行 `down`，不替换全量库或模型设置。若模型测试正在运行，应等待结束再更新。

实际运行路径已由 2026-10-18 的容器标签确认：`procurement-ecs-api-1` 和 `procurement-ecs-web-1` 的 Compose 工作目录均为 `/opt/procurement-project-next/demo/procurement`。此前的 `/opt/procurement-project` 路径不适用于这套运行服务。以后应先核对容器标签，再指定 `--root`，不要根据旧目录名猜测。

更新后在浏览器刷新并检查：

- 首次访问默认选择最新有数据的全量集；已选择的其他非空数据集仍会保留。
- 下拉框默认隐藏空数据集；连接设置里的“显示空数据集”可以恢复导入入口。
- 登录先展示服务访问；模型服务在单独的设置页。
- 标的结果标注公告正文或采购附件，来源记录仍分别保留。
- 关系结果在图表完成后显示“查询与完整渲染”。
- 导出三表的字段、金额单位、来源值和行数保持不变。

## 验收边界

本地生产构建与全量副本测试不能代替服务器验收。更新后还应核对外部 HTTPS、1,038 篇公告 / 2,236 个采购包 / 21,728 条来源记录、五类查询及完整渲染耗时。真实模型只允许最多 2 篇小公告，先 1 篇后 1 篇，并发 1，使用工作数据集，不向全量集写入。

本次严格导出校验发现 30 处同一公告下合同包为空的键冲突；需对照原文确认分包或同包关系。更新包没有改动这些数据，也没有宣称获得官方准确率。
