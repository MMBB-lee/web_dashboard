# 独立采集器首批验收

日期：2026-10-15。本批完全独立于 C/D/E 运行与验收；不是团队业务库、HTTP 或算法联调记录。

## 已运行

- `sqlite-backfill-first.json` / `sqlite-backfill-repeat.json`：76原件、260条月值；首次 inserted=260，重跑 inserted=0/skipped=260，均0解析失败。
- `mysql-backfill-first.json` / `mysql-backfill-repeat.json`：独立 MySQL `anhui_crawler_*_test` 库同样结果；8张ac_表，不连接团队库。
- `mysql-guards.json`：5项合成MySQL保护检查。
- `mysql-guards-locked-race.json`：最终受控竞争复验，两个不同MySQL连接，第二个在读取Asset后确实到达已持有的Source锁；释放后为1插入/1跳过。另验证默认冲突隔离、人工修订留痕、旧发布日期拒绝、整批回滚。合成数据在另一独立_test库，不混入真实260条。
- `online-one-report-with-ca.json`：在线完整链路成功，robots 404按无robots文件处理；低频抓取官方目录、文章、PDF；8条月值都与库内一致，跳过而非重复插入。
- `unit-tests-final.xml`：54项离线测试通过，涵盖独立数据库、实体/周期/单位、幂等、修订、回滚、文件路径、域名/robots/重定向/退避等。
- `unit-tests.xml`：早期43项通过记录；后续补充11项网络政策检查。

源PDF保留页/行/列定位。按PDF技能对2026年8月原页进行可视检查，确认旅客吞吐量的本期列与累计列；不能仅因为文本提取成功就把任意数字当本月值。程序验证不是对260个数值逐条人工复核的声明。

## 保留的失败

`online-one-report.json` 是首次在线失败：Python标准环境缺公共发行CA链，robots请求证书验证失败，未继续抓取。只读诊断得到 `SSLCertVerificationError / unable to get local issuer certificate`。后续添加声明依赖certifi的公共CA包到正常SSL上下文，仍验证证书和主机名；未用verify=False、忽略robots或替换网络身份。成功记录与失败记录分开。

最初Poppler渲染器有本机字体配置告警，得到的样本PNG仍可读且已人工核对。PNG为临时检查材料，不是数据原件；事实引用始终指向原PDF及SHA。

## 数据窗口与缺口

6个既有机场各41个月（2023-01至2026-08，缺三个12月），亳州9个月（自2025-11已观察到的窗口仍缺2025-12），蚌埠滕湖5个月（已观察到2026-04至08）。这些起点不证明实际开通日期，之前月份不当0。其余36个研究对象只说明有来源台账和部分原件，未给出数值时序。

35份非月报原件包含3年度表和32网页样本，未进入月度事实。默认导出的260条为原件/格式/周期校验通过值，不代表全省所有类型数据已收齐。

## 交付证据

`verification-summary.json` 记录表数/版本、独立安装来源、无团队包、与旧82个月值一致性、数据包摘要、54项测试与临时MySQL关闭结果。`release/` 中 wheel/源码包/数据包的摘要见其 `receipt.json`；原件只保留本地，不默认发布全文。

无需启动团队API、Worker、E模型或等待其提交，后续可以直接继续城市与专题来源适配。
