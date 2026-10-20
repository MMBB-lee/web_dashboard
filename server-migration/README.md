# 数据与运行资料交付包

本目录用于交付当前项目的数据快照和知识库运行资料，供 E 及整合端接收复核。数据包含公开资料、估算及模拟序列，并非全部来自真实采集；应按记录中的分类和来源使用，模拟数据回测不代表真实景区预测效果。

## 文件

- `tourism_flow.public.sql`：MySQL 数据库结构及业务数据导出副本，包含 `project_flow_data`、公开资料、知识索引、录像计数等记录。
- `runtime-data.tar.gz`：`api_platform/data/` 下的知识原件、索引及运行资料，共332个文件，需与数据库快照配套接收。
- `manifest.json`：导出副本的SHA256、文件大小及脱敏处理记录。

原始 `tourism_flow.sql` 备份没有纳入 Git。公开副本移除了既有登录会话，替换了原密码哈希和作业租约令牌，并将原账户设为禁用；保留账户ID以维持业务外键关系。原始备份和线上账户不受此处理影响。

## 接收与恢复

1. 按项目根目录 README 准备 Python 3.13、MySQL、依赖和后端 `.env`。运行密钥须自行配置。
2. 在新建的空数据库中导入 `tourism_flow.public.sql`，不要直接覆盖正在运行的数据库。该导出包含建表语句，导入前先核对目标库。
3. 在项目根目录执行 `tar -xzf server-migration/runtime-data.tar.gz`，使文件恢复到 `api_platform/data/`。已有同路径资料时，先备份并核对快照，避免混用新旧索引。
4. 配置 `DATA_DIR=./data`。在 `api_platform/` 目录执行 `python -m scripts.create_admin migration_admin --display-name 管理员`，按交互提示设置密码。使用未占用的新用户名；公开副本中的原账户默认不可登录。
5. 录像实体另在 `videos/`，使用 Git LFS 交付；克隆后执行 `git lfs pull`。模型目录仍为 `intelligent_analysis/model_bundle/p2pnet/`。

本包是数据与资料快照，不代表 E 的22文件源码修复包已合入，也不代表当前项目已通过全量回归。SQL中可能保留原环境的来源URL、作业载荷或历史路径，接收端仍需按实际部署配置核对。
