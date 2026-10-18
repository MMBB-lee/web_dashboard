# 0.3.0a1 本轮实施与验收

日期：2026-10-16。**当前交付为预发布，完整计划尚未完成，1.0.0未放行。**

## 已核实结果

| 项目 | 结果 | 证据 |
|---|---|---|
| 源码/安装wheel | 各127项通过、无失败或跳过 | [源码](./source-tests.xml)、[安装包](./installed-wheel-tests.xml) |
| 旧交付与事实 | 原277条逐条指纹不变，旧8个发布件摘要不变 | [基线对比](./baseline-preserved.json) |
| schema1→2 | 本地真实升级及MySQL真实旧快照恢复后升级通过；277条和原件不变 | [本地升级](./migration.json)、[MySQL升级](./mysql-real-upgrade.json) |
| 当前事实 | 289条唯一记录：287标准、2候选；24个对象有标准事实 | [完整性及最终门禁](./final-gate.json) |
| 私有回放种子 | 79份原件，可移出工程目录运行；首次唯一289，重复新增0 | [Mac安装回执](./native-mac/receipt.json) |
| SQLite/MySQL | 原件和定位、重复导入、业务内容一致、备份恢复通过 | [SQLite](./native-mac/database-sqlite.json)、[MySQL](./native-mac/database-mysql.json) |
| MySQL保护 | 不同连接真实写竞争、冲突隔离、显式修订、旧发布拒绝、事务回滚5项 | [保护测试](./mysql-guards.json) |
| 真实联网 | 原33来源加新增安徽日报来源均有本轮检查；失败与预算不足分别记录 | [机场续采](./online-airports-legacy-format.json)、[其他32源](./online-other-sources.json)、[新增来源与修正复核](./online-additional-sources.json) |
| Mac原生调度 | 临时launchd实际触发：受控允许列表失败→真实官方来源恢复→再次到期检查；临时任务已移除 | [调度回执](./schedulers/Darwin.json) |
| Mac日常运行 | 独立Application Support环境和数据；已安装每天北京时间09:00任务，默认仅Mac日常运行 | [运行位置和下次自然触发](./mac-runtime.json) |

原件共135份为当前工作库的受管理资产数；79份为本批可携带回放种子，二者范围不同。完整年度报告有明确上年同期值，原件解析行数有重复，重复导入的skipped=290，不是唯一事实数量；唯一记录为289。

## 最终门禁仍未通过

[最终报告](./final-gate.json)为incomplete，明确保留三类阻塞：

1. 44对象×目标指标的2023年至截止日完整审计与历史回补未完成；108目标当前保留待审阅状态。有事实的24对象不等于历史资料已取尽。
2. Windows同摘要wheel安装、SQLite/MySQL及恢复回执未收到。
3. Windows原生任务触发和失败恢复回执未收到。

具体软件和数据工作见[实施跟踪](../../docs/IMPLEMENTATION-2026-10-16.md)，包括城市历史及旅游指标、其余景区/站点、目录配置、PDF/Word适配、两轮检索及缺期证据。不能将这些待完成项说成仅缺一个Windows签字。

Mac调度验收是系统提前触发的工程验证，未假称经过两周自然运行。正式日常任务安装时未立即触发，下一次自然触发以mac-runtime.json记录为准。Documents路径第一次临时任务未及时产出报告；Application Support环境复验通过，失败工作目录仍保留在本机data中。

## 用户方Windows验收

1. 解压源码交付包得到`anhui_public_data_crawler`目录。
2. 将单独的`PRIVATE-acceptance-seed-0.3.0a1.zip`解压到该目录内，得到`private-seed/seed.json`和`inputs/`。
3. 在Windows预建空的独立`anhui_crawler_*_test`库，通过当前进程`CRAWLER_ACCEPTANCE_MYSQL_URL`设置账号连接。源码和种子不含口令。
4. 从工程根目录运行：

```powershell
powershell -File .\scripts\Run-WindowsAcceptance.ps1 `
  -Wheel .\release\anhui_public_data_crawler-0.3.0a1-py3-none-any.whl `
  -Seed .\private-seed\seed.json -Output C:\crawler-acceptance-new `
  -RequireMySql -WithScheduler
```

需要Python3.13、当前用户登录和必要联网。回传`native/receipt.json`、`tests.xml`、`native/database-*.json`及`scheduler/Windows.json`和引用的运行证据。完整原件、库、内部恢复备份留在本机；不作为公开事实包发布。

正式Windows日常任务保持关闭，除非以后明确切换主机。详细操作见[运行说明](../../docs/OPERATIONS.md)。

本轮独立MySQL实例已关闭，数据文件保留，正式Mac SQLite任务不受影响。见[关闭验证](./mysql-shutdown.json)。
