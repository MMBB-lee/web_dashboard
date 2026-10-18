# 2026-10-17 爬虫范围冻结与交付复验

本轮按当天17:00截止计划收口现有44对象、34来源。数据快照冻结于北京时间 **12:12:07**，统计窗口为2023-01-01至2026-10-17；不表示已取得当天尚未发布的数据。软件继续使用原 **0.3.0a1** wheel，新增收口工具及本轮证据，不覆盖旧发布件，不宣称1.0.0验收完成。

## 本轮结果

| 项目 | 结果与证据 |
|---|---|
| 源码回归 | 127项通过，0失败/0跳过：[source-tests.xml](./source-tests.xml) |
| 安装包回归 | 非editable wheel，127项通过，0失败/0跳过：[installed-wheel-tests.xml](./installed-wheel-tests.xml) |
| 软件一致性 | 20个包内文件与源码和已安装wheel逐字节一致；当前交付及旧8个发布件SHA256不变：[closeout.json](./closeout.json) |
| 数据与原件 | 135份原件、289条事实的摘要/大小/定位/周期等校验通过；287标准、2候选，24对象有标准事实：[quality.json](./quality.json) |
| 旧数据兼容 | 与0.2.0不可变事实包逐条比较，277条完整导出行不变；不是只比总数 |
| 备份恢复 | 日常库只读在线备份到新目录；含原件恢复包在另一个空目录恢复后，289条事实及完整性检查一致 |
| 本轮导出 | 标准/候选CSV及JSON、来源状态、覆盖、108目标矩阵、缺口、质量及指标字典：[export/manifest.json](./export/manifest.json) |
| MySQL | 沿用10月16日同SHA wheel的真实双库回执，本轮未重启或重测MySQL：[既有回执](../2026-10-16-implementation/native-mac/receipt.json) |

私有恢复包在本机 `data/closeout-2026-10-17-frozen/PRIVATE-runtime-recovery.zip`，含HTML/PDF原件和数据库恢复数据，不进入公开交付ZIP。可携带Windows验收种子仍使用原79份原件私有包。

## 今天自然调度与失败处置

已安装任务为每天北京时间09:00；系统记录今天运行1次、退出码2。运行报告实际记录09:14:20开始、09:43:37结束，系统查询未提供精确启动时刻，不能把09:00配置时间当作实际启动时间。16来源禁用待审、15来源未到期、3来源partial；淮南HTML缓存304重用并去重2条，未新增事实。

证据：[自然更新原报告](./latest-natural-update.json)、[只读系统任务查询](./native-task-query.json)。本轮没有重新触发日常任务。

| 来源 | 定位结果 | 处置与保留状态 |
|---|---|---|
| 淮南 | `.doc`原件745134字节，文件头`d0cf11e0a1b11ae1`为OLE复合文档；服务器媒体类型被记录为text/html，HTML解码报UnicodeDecodeError。另有PDF待适配 | 软件格式适配缺口，保留失败及待审；不能列为无数据。当前冻结版未新增Word/PDF提取器 |
| 铜陵 | robots检查连接失败 | 访问失败，保留URL/原因和待重试队列，不绕过robots检查 |
| 安庆 | robots不可用或受限 | 外部访问限制，保留原件与已采事实，不将本次检查计为成功 |

这三项的详细URL和原始原因见closeout.json；源状态和下一次到期时间见export/source_status.json。它们仍是实际缺口，本轮范围冻结并不关闭这些问题。

## 正式验收状态

[final-gate.json](./final-gate.json)仍为 **incomplete**：

1. 44对象、108目标的2023年至冻结日历史审计未闭环；城市历史及旅游指标、其余景区/站点、目录发现配置和部分格式适配仍待做。
2. Windows同wheel安装、SQLite/MySQL与恢复回执未收到。
3. Windows原生调度与失败恢复回执未收到。

已解析的24对象不代表其全部历史窗口已采齐。本次不以单元测试或原件完整性通过代替完整历史采集验收。工程阶段交付可复验；全省完整数据与双端正式验收未完成。

## 复验入口

新增 [scripts/closeout.py](../../scripts/closeout.py) 只读获取日常SQLite快照，使用同一wheel检查旧发布件、旧事实、原件、导出与恢复，生成新的证据目录。必须使用已安装wheel的独立Python，并为每次执行指定全新目录：

```bash
<独立环境>/bin/python -I scripts/closeout.py \
  --source-root '<日常数据目录>' \
  --output './data/closeout-new' \
  --evidence './acceptance/closeout-new' --end 2026-10-17
```

工具不会安装/触发系统任务，不修改日常数据库；检查到尚在运行的采集任务即停止，待任务结束后用新目录重做。`status=verified_existing_scope`仅表示本次复验成功，必须同时阅读`full_acceptance`及`blockers`。

Windows继续按 [OPERATIONS.md](../../docs/OPERATIONS.md) 执行`Run-WindowsAcceptance.ps1 -RequireMySql -WithScheduler`，源码包之外另提供私有种子。原件和完整恢复包不随回执公开传输。
