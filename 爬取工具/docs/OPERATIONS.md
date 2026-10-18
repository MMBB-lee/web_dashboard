# 0.3.0a1 操作与双端验收

Python3.13为本轮实测基线。源代码测试使用项目上级`.tmp/province-runtime/bin/python`；独立wheel验收使用新环境。不得依赖团队Python模块。

## 安装、升级与独立数据库

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install './release/anhui_public_data_crawler-0.3.0a1-py3-none-any.whl[dev]'
.venv/bin/python -m anhui_crawler --data-root ./data/local init
# 已有0.2.0库：先显式升级，自动创建带原件的私有恢复备份
.venv/bin/python -m anhui_crawler --data-root ./data/local migrate --report ./data/migration.json
```

已有库遇到schema或registry变化时init/status拒绝，必须migrate。只接受独立`anhui_crawler`或`anhui_crawler_*` MySQL库；有外来表/视图或未知结构时拒绝。MySQL连接通过本地`CRAWLER_DATABASE_URL`设置，程序不自动加载.env，不打印连接串；忽略团队DATABASE_URL。

升级备份包含全部ac_表、受管理原件以及audit/migration/run JSON。恢复只能写入空库及空目录；新库恢复到旧schema后仍需显式migrate。MySQL DDL部分完成时初始化继续拒绝，migrate可显式续做；保留升级前快照。

## 采集、更新和导出

```bash
python -m anhui_crawler --data-root ./data/local ingest ./data/bootstrap-030/seed.json --report ./data/import.json
python -m anhui_crawler --data-root ./data/local crawl --source-id caac-east-monthly --start 2023-01-01 --end 2026-10-16 --max-pages 20 --max-documents 50 --report ./data/crawl.json
python -m anhui_crawler --data-root ./data/local update --due --report ./data/update.json
python -m anhui_crawler --data-root ./data/local export --output ./data/export-new --start 2023-01 --end 2026-10 --facts-start 2023-01-01 --facts-end 2026-10-16
python -m anhui_crawler --data-root ./data/local verify --end 2026-10-16 --report ./data/quality.json
```

`crawl`显式检查选中的来源；`update`只检查启用了自动检查且已到期的来源。禁用来源显示disabled_requires_review，不代表数据审计完成。预算不足为partial，待处理URL持久化。`--as-of`仅用于调度测试，报告标注clock_override，不能用来冒充真实运行或已发布数据。

`export --start/--end`继续只控制覆盖报告月份；事实筛选单独使用`--facts-start/--facts-end`，按区间相交筛选，不裁剪年/假期/累计事实。审计窗口不会超出本机北京时间当天。CSV即使没有事实也保留表头。标准事实与候选分开，导出不包含原文、数据库地址或口令。

变更值默认隔离。仅经人工核对原文后使用`ingest --accept-revisions`接受修订；保留旧值，较旧发布时间不能回滚新值。HTTP304必须关联有效SHA缓存；缺失/损坏时无条件获取或明确失败，不信任无原件的304。

退出码：0为命令正常完成，1为配置/输入错误，2为partial、质量失败或最终验收未完成，3为已有进程持锁。每源失败不阻塞其他源。先阅读JSON，不将2一概认定为整个程序不能运行。

## 审计证据与最终门禁

`audit-import`读取`anhui-crawler-audit-1` JSON，entries以entity_id+target_metric唯一。每项含start/end、status、evidence及next_check；evidence含HTTPS URL、checked_at、route、outcome。collect结论使用`collected`且需要`discovered_documents_exhausted=true`及真实事实。负面可结项状态为not_published/not_found_in_recorded_search/access_restricted/no_target_metric，至少两天和两条检索途径；不得用pending、scope_unconfirmed、download_failed、parse_failed关闭验收。

真实访问限制可有证据结项，但解析失败和未处理的可获取资料仍阻塞。记录已有数值不自动关闭历史审计。完整计划见IMPLEMENTATION-2026-10-16.md。

```bash
python -m anhui_crawler --data-root ./data/local verify --final --end 2026-10-16 \
  --wheel ./release/anhui_public_data_crawler-0.3.0a1-py3-none-any.whl \
  --mac-receipt ./receipts/Darwin-package.json --windows-receipt ./receipts/Windows-package.json \
  --scheduler-receipts ./receipts/schedulers --report ./data/final.json
```

门禁要求同一wheel摘要、当前版本、Mac/Windows平台、SQLite/MySQL及恢复通过，两个平台原生调度证据通过，并且108目标审计全部关闭。完整性verify的passed不等于最终放行。

## 原生调度

```bash
python -m anhui_crawler --data-root /持久化采集目录 schedule --output ./schedule-new --python /独立环境/bin/python
```

生成可审阅的plist和Windows安装脚本，不自动安装。Mac使用系统Asia/Shanghai时区；子进程TZ不能改变launchd触发时区。每天09:00只检查到期源。机场每周，年度公报1—6月每周、其余月份每30天；配置了明确假期区间的来源在假期及结束后14天每日检查，其余默认每周。

推荐Mac后台运行环境与数据放Application Support内，避免后台入口依赖Documents项目目录。安装通过launchctl bootstrap；移除通过bootout。Windows需China Standard Time，安装脚本使用当前登录用户及IgnoreNew重入策略；不保存账户密码。两端日常仅启用一台。

`scripts/exercise_native_scheduler.py --wheel <wheel> --output <新目录>`安装专用临时任务，使用隔离库制造允许列表拒绝，再从真实官方万佛湖原文恢复，第三次到期检查无新增。它记录每次实际系统触发、run摘要和原生任务查询；结束移除临时任务。这是提前触发的工程验收，不冒充经过多个自然日。

不要把手动update报告包装成调度证明。`collect_schedule_evidence.py`还要求审阅过的原生触发历史，单独查询到一个任务不能证明给定报告由它产生。

## Windows验收

复制源码验收包、同一wheel和私有原件种子到Windows本地新目录。完整原件包仅内部使用，不随公开事实包发布。管理员预建空的独立`anhui_crawler_*_test`库，最小权限账户通过当前进程`CRAWLER_ACCEPTANCE_MYSQL_URL`配置。

```powershell
powershell -File .\scripts\Run-WindowsAcceptance.ps1 `
  -Wheel .\release\anhui_public_data_crawler-0.3.0a1-py3-none-any.whl `
  -Seed .\private-seed\seed.json -Output C:\crawler-acceptance-new `
  -RequireMySql -WithScheduler
```

需要Python3.13、当前用户登录及可联网访问包索引/官方源；网络失败保留证据，不改TLS/robots。验收脚本会比对已安装文件与wheel、隔离团队模块、复核原件和事实、重复导入、新目录恢复及双库一致性。脚本不会创建MySQL库，不会清空既有库。

回传receipt.json、tests.xml、database-sqlite.json、database-mysql.json及scheduler/Windows.json与相应触发记录；私有备份、库和原件留在本机。未收到Windows回执时，正式双端验收未完成。
