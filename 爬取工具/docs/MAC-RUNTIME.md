# 本机Mac日常采集位置

这是本机部署记录，不是Windows路径配置。

- 运行环境：`/Users/Admin/Library/Application Support/AnhuiPublicDataCrawler/runtime-0.3.0a1/`
- 日常数据库及原件：`/Users/Admin/Library/Application Support/AnhuiPublicDataCrawler/data/`
- 系统任务配置：`/Users/Admin/Library/Application Support/AnhuiPublicDataCrawler/schedule-0.3.0a1/crawler.plist`
- 任务标签：`org.anhui.public-data-crawler`，当前用户GUI域，系统时区Asia/Shanghai，每天09:00。
- 日志：日常数据目录下`logs/scheduler.stdout.log`和`scheduler.stderr.log`；完整更新JSON在`runs/`。

项目内`data/local/`及`data/export-030-final/`保留本轮289条记录的开发与交付快照。日常任务更新Application Support下的独立副本，两者不自动互写。后续查看最新采集结果须指定日常目录。

```bash
'/Users/Admin/Library/Application Support/AnhuiPublicDataCrawler/runtime-0.3.0a1/bin/python' -I -m anhui_crawler \
  --data-root '/Users/Admin/Library/Application Support/AnhuiPublicDataCrawler/data' status

launchctl print gui/$(id -u)/org.anhui.public-data-crawler
# 需要停止日常任务时，仅移除本采集器的任务；不删除数据
launchctl bootout gui/$(id -u)/org.anhui.public-data-crawler
```

初次Documents目录下的临时验收入口未及时产生运行报告；移到Application Support后真实触发、失败恢复和重复检查通过。未关闭系统隐私或TLS保护，也未修改系统时区。

独立MySQL仅用于本轮验收，不作为日常服务；验收结束关闭实例但保留文件。正式默认SQLite，Windows未启用日常任务。
