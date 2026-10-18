# 安徽公开数据独立采集器

本地集成新增：控制台的“景区客流数据”区域可查看项目序列、导出CSV/JSON，并通过“采集并同步MySQL”调用C的独立导入模块。原有SQLite采集数据保持只读；项目序列保留公开、估算、模拟标记，单独写入业务库`project_flow_data`。

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++\爬取工具"
..\.venv\python.exe -X utf8 scripts\update_project_data.py --apply
```

使用`--offline --apply`可使用保存的公开依据重新生成并同步。详见`../api_platform/客流数据补充与预测使用说明.md`。

当前预发布 **0.3.0a1**。独立Python包、Git工程和数据库，不依赖团队C/D/E。完整计划仍在实施，**未发布或验收1.0.0**。

独立仓库：[Hydr0genG/anhui-public-data-crawler](https://github.com/Hydr0genG/anhui-public-data-crawler)。包含采集器、只读实时控制台、测试与公开验收材料；本机数据库、完整HTML/PDF原件、私有验收种子及恢复备份不随仓库发布。

2026-10-17已完成现有范围冻结复验：源码/安装wheel各127项通过，日常库135份原件及289条记录检查、备份恢复通过；今天自然更新3源partial及正式验收缺口保留。最新数据与交付证据见[本日收口报告](./acceptance/2026-10-17-closeout/README.md)。

## 本机实时控制台

双击本目录的 [打开爬虫控制台.command](./打开爬虫控制台.command)，或运行`python3 scripts/dashboard.py --open`，访问[本机控制台](http://127.0.0.1:8765)。

页面直接读取Mac日常采集库，每15秒刷新，包含运行总览、来源监控、结果筛选与导出、运行日志、月度覆盖及来源证据。新增界面及只读访问测试后源码共137项通过。详见[操作说明](./docs/DASHBOARD.md)与[验证记录](./acceptance/2026-10-17-dashboard/README.md)。

## 当前实际结果

- 固定44对象：16市、8机场、12景区、8铁路站；当前34来源（原33来源加安徽日报单站来源）。
- 当前289条唯一记录：287条标准事实、2条保留限定语的候选。原277条数据不变。
- 标准事实包括260机场月值、17城市年度/年末事实、6条景区假期及单站日实绩、3条黄山年度进山量、1条黄山北站半年发送量。
- 0.3.0a1新增显式备份迁移、恢复、跨进程锁、持久化续采、条件请求、延期重试、来源到期检查、指标字典、108目标审计矩阵及正式放行门禁。
- 城市历史回补、其余对象来源核查、部分格式适配和Windows实机回执仍待完成。已有事实和测试通过不等于全省数据完整。

## 开始使用

首次从GitHub取得源码后，先初始化自己的空库：

```bash
git clone https://github.com/Hydr0genG/anhui-public-data-crawler.git
cd anhui-public-data-crawler
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m anhui_crawler --data-root ./data/local init
.venv/bin/python scripts/dashboard.py --data-root ./data/local --open
```

初始化只创建44对象、34来源配置，不自动生成示例事实。公开的当前事实及覆盖快照见[冻结导出](./acceptance/2026-10-17-closeout/export/manifest.json)；本机控制台默认读取用户自己采集的SQLite库，不会把公开导出冒充为当前实时数据。采集命令及其范围见下文。

Python3.13为本轮验收基线，包声明支持Python3.11及以上。新建环境安装当前wheel，或开发时安装本目录：

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m anhui_crawler --data-root ./data/local init
```

已有0.2.0库使用显式升级，不能以init替代：

```bash
.venv/bin/python -m anhui_crawler --data-root ./data/local migrate --report ./data/migration.json
```

默认SQLite；独立MySQL只读取本地`CRAWLER_DATABASE_URL`，不自动加载.env。当前schema_version=2，10张ac_表；遇到外来库、视图或未知结构时拒绝。不使用团队数据库环境变量和业务表。

```bash
# 使用复制并逐文件校验的私有原件种子，无需联网或团队工程
python -m anhui_crawler --data-root ./data/local ingest ./data/bootstrap-030/seed.json --report ./data/import.json
# 有明确请求预算的发现与历史回补
python -m anhui_crawler --data-root ./data/local crawl --source-id caac-east-monthly --start 2023-01-01 --end 2026-10-16 --max-pages 20 --max-documents 50 --report ./data/crawl.json
# 只检查已启用且到期的来源
python -m anhui_crawler --data-root ./data/local update --due --report ./data/update.json
# 标准、候选、覆盖、逐项缺口和质量分开导出
python -m anhui_crawler --data-root ./data/local export --output ./data/export-new --start 2023-01 --end 2026-10 --facts-start 2023-01-01 --facts-end 2026-10-16
```

`--end`等日期是示例窗口，不是程序固定截止。原有`export --start/--end`继续只控制覆盖报告；事实筛选使用独立的`--facts-start/--facts-end`，按区间相交，不裁剪假期、年或累计周期。

标准事实保留原单位、来源、发布时间、采集时间、完整SHA256及HTML XPath/PDF页位置。月值不拆日/周，城市铁路量不套单站，年累计不减出月值，宏村西递合计不拆分。“约/超过”等保留候选，不冒充精确值。黄山进山量按原“人”单位单列，不混入索道量，也不宣称去重游客。

## 运行与验收

- [当前阶段、完整验收范围及未完成工作](./docs/IMPLEMENTATION-2026-10-16.md)
- [安装、迁移、备份恢复、调度和Windows操作](./docs/OPERATIONS.md)
- [独立计划入口](./docs/PLAN.md)
- [本轮验收材料](./acceptance/2026-10-16-implementation/README.md)
- [0.2.0历史说明](./docs/README-0.2.0.md)

`verify`检查完整性，`verify --final`另外要求108目标审计结项、同一wheel的Mac/Windows双数据库和恢复回执、两端真实调度与恢复证据。最终门禁未通过时不宣称完成。Windows按约定由用户方运行验收包回传，工具不伪造Windows结果。

`schedule`只生成可审阅的Mac plist和Windows任务脚本。实际安装/移除及当前主机状态见本轮回执。Mac后台运行建议使用Application Support中的独立环境和数据，不依赖Documents下源码；Windows验收后只保留一台日常主机。

## 工程目录

本机文旅交通补充入口：`http://127.0.0.1:8765` 中的“文旅交通客流数据”区域可查看3个景区、4个铁路站、8个机场的补充序列，导出CSV/JSON，或点击“采集并同步MySQL”。该区域读取独立项目数据包，公开值、估算与模拟在元数据中区分，原爬虫SQLite及公开事实不覆盖。当前范围为2023-01-01至2026-10-07，支持日、周、月和假期。生成及导入脚本为 `scripts/update_project_data.py --apply`，离线使用 `--offline --apply`；使用项目根目录的Python 3.13虚拟环境。详细说明见 `../api_platform/客流数据补充与预测使用说明.md`。

```text
src/anhui_crawler/  采集、解析、存储、升级、报告、锁和调度配置
scripts/           干净安装、双库升级、原生调度、审计及打包验收入口
tests/             离线解析、数据口径、恢复、并发保护和CLI回归
docs/              操作、实施跟踪和历史记录
acceptance/        脱敏验收报告、来源检查与明确未完成项
release/           不覆盖旧版本的wheel、源码与事实交付包
data/              本机原件、库、私有备份、种子与验收环境；Git忽略
```

原文未明确再发布许可时，HTML/PDF只保存在受管理原件及内部恢复/验收包。默认公开交付不包含原文、数据库、凭据或团队包。未开户、购买API或发送数据申请；本工程已发布到上述独立公开仓库。
