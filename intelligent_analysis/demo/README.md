# 脚本导航

从E仓库根目录运行。当前模型P2PNet / PyTorch CPU，默认使用随仓库交付的model_bundle/p2pnet。各组前置条件不同，无需依次运行所有脚本。

## 拉取后使用

| 入口 | 用途/前置条件 |
|---|---|
| [setup-p2pnet.ps1](../setup-p2pnet.ps1) | 安装CPU依赖、模型核验、自带短片试跑 |
| [verify_p2pnet_bundle.py](verify_p2pnet_bundle.py) | 不需要录像；默认核验随仓库模型，可用--bundle指定D可信目录；必须指定--output |
| [check_p2pnet.py](check_p2pnet.py) | 自带短片三帧CPU推理；重复运行请用--output写artifacts，不覆盖封存证据 |
| [end_to_end_demo.py](end_to_end_demo.py) | 明确标为synthetic的计算/预测演示 |

参数和运行命令见 [安装与配置](../docs/P2PNet安装与具体配置-2026-10-18.md)。

## 当前评测和联合验收

| 脚本 | 范围/前置条件 |
|---|---|
| [check_fixed_camera_videos.py](check_fixed_camera_videos.py) | --zip指定五段输入包，--output指定新报告；不登记业务录像 |
| [prepare_annotation_pack.py](prepare_annotation_pack.py) | --report指定已有视频报告；生成无标点原图和空白人工表，不生成真值 |
| [evaluate_p2pnet.py](evaluate_p2pnet.py) | 人工审核manifest、授权和帧摘要齐备后做P2PNet单模型MAE；空模板保持blocked |
| [check_rag_questions.py](check_rag_questions.py) | 随仓库真实历史公告题集9项自动检查，人工语义复核另验 |
| [accept_d_c021_http.py](accept_d_c021_http.py) | D源码、已接收C a2及相关依赖；真实路由/Worker和隔离SQLite，不是部署MySQL |
| [accept_d_visible_storage.py](accept_d_visible_storage.py) | D适配→E处理器→真实C仓储，帧/计数器/授权为合成夹具 |
| [accept_forecast_storage.py](accept_forecast_storage.py) | C a2隔离环境；合成连续窗、指纹、独立作业及读回 |
| [check_b_contract.py](check_b_contract.py) | B正确P2PNet分支、D源码、Node24；纯TS契约，无浏览器 |
| [verify_d_regression.py](verify_d_regression.py) | D源码与C a2；复制后运行，--initialize-empty-snapshot显式标记测试前置条件 |

有--output的脚本请给新路径；建议放artifacts。当前结果见 [证据导航](../docs/acceptance/README.md)，不是把脚本退出0解释为最终项目验收。

## C接收与发布工具

- [receive_c021_zip.py](receive_c021_zip.py)：当前C0.2.1a2 ZIP逐文件manifest/CRC接收，已有目录不覆盖。
- [accept_c_knowledge.py](accept_c_knowledge.py)、[accept_c020_failures.py](accept_c020_failures.py)：指定C_HANDOFF_DIR、C_RUNTIME_DIR、E_ACCEPTANCE_REPORT后验真实资料/严格保护；默认历史环境需先核对，不能直接连业务库。
- [verify_release_wheel.py](verify_release_wheel.py)：当前构建wheel独立安装、CPU推理、坏数值拒绝和C重启读取；需要对应接收证据。
- [package_release.py](package_release.py)：需要通过的JUnit、wheel及实装/接收记录；拒绝覆盖已有发布。

## 历史和专用工具

这些文件保留原路径，支持已封存证据、旧发布或其他工具的内部导入，不进入当前模型选择流程：

| 文件 | 原用途 |
|---|---|
| receive_c.py | C0.1.9接收 |
| receive_c020.py / check_c020_windows.py / verify_c020_regression.py | C0.2.0阶段接收与回归 |
| prepare_c020_candidate.py | 早期候选修补，正式a2不使用 |
| accept_c021_integration.py | C021早期集成，含旧D不足分支断言；当前使用新的D HTTP/visible验收 |
| accept_d_bridge.py / verify_e051_wheel.py | 旧桥和0.5.1发布实装 |
| prepare_dm_count.py / check_dm_count.py | 历史DM-Count |
| benchmark_frames.py / prepare_leader_comparison.py / score_leader_comparison.py | 已停止的模型比较；部分纯标注校验仍被导入 |
| check_leader_p2pnet.py / leader_p2p_factory.py | 首次队长包试跑 |

历史工具的存在不表示要重启比较、下载DM权重或开放GPU/OpenVINO。
