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

### API 已构建、Node 镜像源返回 502 时

2026-10-18 出现前端 `node:22-alpine` 经 `docker.m.daocloud.io` 请求元数据返回 502，API 镜像已成功生成。普通更新脚本因此停在构建阶段，尚未执行 Compose 容器切换，但源码已写入且旧源码已备份。

可运行更新包中的 `bash scripts/finish_site_update.sh`：先校验当前目录和任务空闲状态，验证已构建的 API 镜像确实含包内的导出修复，准备并验证 API 回滚镜像，再从运行中前端创建停止的临时容器，将包内生产 `dist` 写入该容器并提交为新前端镜像，通过 Nginx 配置检查后用 `--no-build --pull never` 切换 API 与 web。无需 Node 镜像、npm 下载或修改全局镜像源。旧运行镜像另存回滚标签，临时容器自动清理，数据库与模型设置仍沿用原卷。

该恢复方式以“API 构建已成功”为前提，API 文件哈希不符时会停止。本地检查只覆盖脚本语法、元数据转换和模拟 Docker 的流程，目标 ECS 仍需执行后检查。

恢复脚本全程使用 Compose 构建产物的 `procurement-ecs-api:latest` / `procurement-ecs-web:latest` 和明确的回滚标签，不将容器 `Image` 或 `docker commit` 输出的内部摘要用于 `docker image inspect`、`docker create` 或 `docker tag`。本次服务器上 web 容器内部摘要为 `b336ecee...`，镜像列表中的 web 标签摘要为 `55e8256f...`；这不足以证明旧镜像被删除。

### 旧 API 备份出现 content digest not found 时

用户随后确认 Docker 为 29.9.1，使用 `io.containerd.snapshotter.v1`。已构建 API 可独立启动，`exports.py` SHA-256 为 `9d059b7a8b81e87a1c30bd4784558c177f25e0f3c8a9c2e2dfa6f683e216d8ad`，与更新包相符；旧前端回滚标签已生成，尚未出现旧 API 回滚标签。按旧恢复脚本顺序，证据指向旧 API 的 `docker commit` 备份失败，尚未证明镜像内容缺失的底层原因。

新版通过 `snapshot_api_rootfs.py` 流式执行 `docker export` 和 `docker import`，生成独立的 API 回滚镜像，不依赖旧镜像的内容摘要。保留启动命令、工作目录、Python 基础环境与健康检查，只保留白名单基础环境变量，不把运行时令牌、模型密钥或 Compose 身份标签写入回滚镜像。运行时配置仍由原 `.env` 和挂载目录提供。[Docker 文档说明导出不包含挂载卷内容](https://docs.docker.com/reference/cli/docker/container/export/)，[导入可以恢复启动及健康检查元数据](https://docs.docker.com/reference/cli/docker/image/import/)。

备份导入需要额外磁盘空间，脚本在 Docker 数据目录和默认 containerd 目录预检至少 4 GiB 空闲空间；这项检查针对本次约 1.7 GB 的 API 镜像，不是通用容量保证。备份后以关闭网络、未挂载项目数据的临时容器核对旧 `exports.py` 文件哈希；同一容器的已验证回滚镜像可复用。导入失败或哈希不一致会清理本次创建的回滚标签，并停止切换服务。恢复脚本显示七个阶段，失败时会标出阶段。不会修改 Docker 守护进程配置或重新启动 Docker。

已上传旧网站更新包的服务器，只需上传小包 `competition-site-recovery-20261018.tar.gz` 到 `/tmp/`。它只更新三个脚本和说明，沿用已解包的业务文件和清单，不含业务数据。在 Workbench 复制执行下面一整行：

```bash
tar -xzf /tmp/competition-site-recovery-20261018.tar.gz -C /tmp/competition-site-readiness-20261018 && bash /tmp/competition-site-readiness-20261018/competition-site-update/scripts/finish_site_update.sh
```

这次不要调用早先上传的 `/tmp/finish_site_update.sh`；单独更新一个 Shell 文件会缺少新增的 Python 备份助手。阶段 3 可能需要数分钟，阶段 7 会短暂切换前后端服务。完成后应保存输出中的两个回滚镜像标签，并继续执行公网验收。若阶段 7 失败，先保留输出与现有容器状态，不清理镜像、数据库或数据卷。

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
