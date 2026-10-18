> 此为0.2.0历史说明；命令以当时工程根目录为准。当前运行使用../README.md及OPERATIONS.md。

# 安徽公开数据独立采集器

这是**独立的数据生产项目**，不是文旅平台的 API、Worker、RAG 或预测模块。无需 C/D/E 安装包、业务数据库或其他成员代码；可单独复制、安装、运行。当前放在同一工作目录下只是便于访问文件，不作为团队工程子模块。

2026-10-15：已恢复全省采集第二步，解除“等待 D/E 知识联调完成”的前置条件。知识联调与数据采集是两条独立工作线。

## 本版已经实际完成

- 独立 Python 包 / CLI、独立来源配置、独立文件存储和 **8 张 `ac_` 表**。MySQL 只接受 `anhui_crawler` / `anhui_crawler_*` 库名；发现团队或其他表就拒绝初始化。不读取团队 `DATABASE_URL` / `TEST_DATABASE_URL`，不改 C 的 0006 / 31 表。
- 延续第一步的 **44 对象、33 来源**。复制校验过的 76 份原件到独立目录：41 份机场月报、3 份年度机场报表、32 份网页样本。旧资料只读，不移动、不覆盖。
- 8 个机场 **260 条月值**进入 SQLite 和真实 MySQL；两种库重跑都是 inserted=0 / skipped=260。6 个已有机场各41个月，亳州9个月，蚌埠滕湖5个月。不是8个机场都有44个月，不是全省数据已齐。
- 本月列解析器保存原始整数、原单位、页/行/原文、列名、原件 URL/SHA 和发布日期。PDF 抽样核对了“旅客吞吐量/本期完成”；年度值不拆月、不用累计相减填12月，不把新机场早期缺行填0。
- 官方 CAAC 在线最小链路通过：robots → 限速目录发现 → 公告日期 → PDF → 8 条相同月值跳过。`403`、验证码、TLS/robots异常不绕过；域名白名单、重定向检查、大小/超时/重试上限保留。
- 同源更新默认进入冲突复核，不静默覆盖。人工明确 `--accept-revisions` 才写历史修订；发布时间更旧的版本仍拒绝。两个 MySQL 连接的受控源锁竞争、修订和整批回滚已实测。

0.2.0 新增：9市2025年公报的 **17条事实**（11条年度交通量、6条年末景区/星级酒店存量），SQLite及独立MySQL均为277条；原260条机场月值不变。重复导入新增0条、跳过277条。城市年值不拆月，年末存量记为12月31日的日粒度；市域铁路发送量不映射为单站数据。池州口径未明确的客运总量没有写入，只提取其明确的年末景区数。见[城市批次验收](../acceptance/2026-10-15-bulletins/README.md)。

其他年度机场报表、景区和铁路站新闻仍仅归档。`validated` 指原件摘要、结构、实体/单位/周期等程序规则通过，不代表官方初步统计数变成最终审定数。完整进度见[独立三步计划](../docs/PLAN.md)和[首批验收](../acceptance/2026-10-15/README.md)。

## 安装和首次使用

Python 3.11+，建议3.13。以下从本目录执行，不需要返回团队目录。

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m anhui_crawler --data-root ./data/local init
```

Windows 使用 `.venv\Scripts\python.exe`。本轮实际运行环境是 macOS / Python3.13.16 / MySQL8.4.11，未声称完成本项目 Windows 实机验收。

不设置独立数据库环境变量时，本工具在指定 data-root 使用 SQLite，方便单机采集和审阅。正式共享存储建议单独 MySQL；管理员先创建空独立库和最小权限账号，再设置（连接串只在本地终端或未入库的 `.env` 中保存，工具**不会自动加载 `.env`**）：

```bash
export CRAWLER_DATABASE_URL='mysql+pymysql://crawler_user:LOCAL_PASSWORD@127.0.0.1/anhui_crawler'
.venv/bin/python -m anhui_crawler --data-root ./data/mysql init
```

库中若已有不属于本采集器的表，拒绝继续，不迁移、不清空。当前独立 schema_version=1；后续改表和来源配置升级需要独立迁移，不能套用团队 Alembic。

## 在线获取与历史回补

```bash
# 明确统计期与请求预算；默认每个域名至少间隔2秒。
.venv/bin/python -m anhui_crawler --data-root ./data/local crawl-airports \
  --start 2023-01 --end 2026-08 --pages 1 --max-reports 2 \
  --report ./data/online-run.json

# 用自带格式的可携带原件清单回补；不联网。
.venv/bin/python -m anhui_crawler --data-root ./data/local ingest \
  ./data/bootstrap/seed.json --report ./data/backfill-run.json

# 导出到新/空目录，包含 facts.json、facts.csv、coverage.json 和 registry。
.venv/bin/python -m anhui_crawler --data-root ./data/local export \
  --output ./data/export-new --start 2023-01 --end 2026-08
```

`--end` 是覆盖报告的统计月份窗口，不是抓取月份，也不是事实导出的筛选条件（导出包括库中所有标准事实）。上例日期是本次已核验资料窗口，不是写死的程序截止时间。目录页预算不足时不能据此宣称更早资料不存在；可增加 `--pages` 或分段回补。自动联网发现只支持机场月报；城市公报通过显式 `ingest` 解析已审阅的9市格式，其他网页继续仅归档。

0.1.0 来源审计台账保持不变，避免隐式迁移来源配置。0.2.0 已审阅的城市提取规则在 `bulletin.py` 中单独版本化；导出新增 `parser_capabilities.json`，说明实际支持的来源、指标、范围和粒度。台账中的 `archive_only` 是初始来源审计状态，不表示当前安装包完全没有离线解析能力；城市自动联网发现仍未启用。

初次从旧审计目录迁入数据时，可使用 `scripts/bootstrap_audited_sources.py --help`。它只读取来源台账、原件清单和文件，验证 SHA 后**复制**为本项目 `registry.json + seed.json + inputs/`；不导入任何团队包。完成复制后，这些旧目录不再是运行依赖。安装包内自带44对象/33来源配置；原件不塞进 wheel。

## 数据与职责边界

本工具交付来源、原件、候选/标准事实、修订、缺口及导出包。其他项目可以消费导出的数据，但本工具不创建它们的用户/文档/知识作业，不改 HTTP 契约，不负责算法效果。

当前为显式 CLI、单采集进程方式，不是已上线的后台定时服务。来源锁保护同源事实写入，不宣称所有下载/资产登记都支持分布式多实例竞争。第三步将补游标、条件请求、定时运行和失败恢复；无需 D 来运行采集器。

来源公开可读不等于原文可以无限再发布。完整 HTML/PDF 留在本地受管理目录，遵守来源政策；默认导出事实与定位，不自动发布全文。未申请付费 API、未使用账户令牌、未接入天气/地图服务。

## 目录

```text
src/anhui_crawler/   独立采集、解析、存储、导出与配置
scripts/            一次性资料迁入及独立MySQL验收
tests/              离线单元/安全/幂等测试
docs/PLAN.md        独立计划与剩余来源顺序
acceptance/         成功/失败及真实MySQL记录
release/            可安装wheel、源码包、首批数据包
data/               本地原件、数据库、种子与导出；Git忽略
```

本轮数据已保存于 `data/local/crawler.sqlite3`，MySQL数据位于本轮独立测试实例中，验收结束后关闭实例但保留文件。可交付的事实数据包不包含数据库口令或这些机器的连接地址。
