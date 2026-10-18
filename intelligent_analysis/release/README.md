# E 发布文件

**要拉取后直接运行P2PNet，请使用Git仓库与根目录setup-p2pnet.ps1。** 模型、权重和试跑短片在仓库model_bundle/p2pnet。下列wheel/ZIP为发布时封存快照，生成早于随后提交的随仓库模型和启动脚本，不是当前仓库的完整副本。它们继续用于包安装和历史接收，原摘要保持不变。

## 当前交付：E 0.6.2（2026-10-18）

修复数值工具错误成功结果校验：拒绝NaN/Infinity、负数、布尔值、错误来源列表及无效时间。P2PNet CPU、知识产物格式和服务签名不变，合法零值/小数/数值字符串保留。

- [wheel](shumei_intelligent_analysis-0.6.2-py3-none-any.whl)
- [完整ZIP](E-visible-people-0.6.2-2026-10-18.zip)
- [SHA256回执](E-visible-people-0.6.2-2026-10-18.receipt.json)
- [全流程任务与自检](../docs/E全流程任务与自检-2026-10-18.md)
- [D安装与回传要求](../docs/致D-E062自检修复与安装回执要求-2026-10-18.md)

139项回归通过、1项历史对照跳过；16份C a2真实资料、18项隔离知识HTTP、合成计数/预测和B纯契约通过。业务MySQL、部署视频HTTP、人工MAE、真实预测和浏览器仍待联合验收。下面的0.6.1及更早文件保持原件。

## 0.6.1（2026-10-17，历史发布）

修正原文/索引版本耦合，支持 D 指定 analysis_id，强化数值工具不完整结果的依赖异常；接收 C 官方 0.2.1a1 / 0007。P2PNet CPU 口径不变，真实录像/人工MAE/预测和 D C021 HTTP 仍待验收。

- [wheel](shumei_intelligent_analysis-0.6.1-py3-none-any.whl)
- [完整 ZIP](E-visible-people-0.6.1-2026-10-17.zip)
- [SHA256 回执](E-visible-people-0.6.1-2026-10-17.receipt.json)
- [接收及对接说明](../docs/E061-接收C021与答复D-2026-10-17.md)

发布文件是发布时快照，内部验收和 C 版本保持原记录。发布后的 C 0.2.1a2 接收及新增工具/证据在 Git 当前源码中，详见 [当前完成情况](../docs/E当前完成情况与剩余依赖-2026-10-17.md)；本次整理不重新打包。

以下为历史发布记录，原件及摘要未修改。


## 0.6.0：正式使用 P2PNet

日期2026-10-15。视频接口只接受p2pnet，固定阈值0.5，提供的SHTechA权重通过本机PyTorch CPU推理；停止DM/P2P对照。D/B同步适配后再联合发布，详见 [0.6.0执行单](../docs/E060-P2PNet正式交付与DB执行单.md)。人工MAE、GPU及正式视频HTTP仍待验收。

- [0.6.0 wheel](shumei_intelligent_analysis-0.6.0-py3-none-any.whl)
- [0.6.0 ZIP](E-visible-people-0.6.0-2026-10-15.zip)
- [摘要回执](E-visible-people-0.6.0-2026-10-15.receipt.json)

核心服务仍可独立安装；实际推理需vision可选依赖及单独接收的队长源码/权重。E不把收到的第三方源码、权重和视频上传到发布包。

```powershell
python -m pip install ./release/shumei_intelligent_analysis-0.6.0-py3-none-any.whl
```

## 0.5.1：C 已验证索引加载

日期：2026-10-15。新增 C 官方 0.2.0a2 引用加载、原文/索引版本独立绑定、片段核对及损坏时拒绝旧内存回退；保留 0.5.0 视频契约。

官方 C wheel Windows 全量回归 310 通过、1 项平台 FIFO 跳过；16 份真实资料及重启问答通过。D/E 独立 SQLite 桥 HTTP 检查通过，正式 C/MySQL 联调仍待 D 切换。请看 [正式回执](../docs/E051正式交付与CD回执.md) 和 [D 最新执行单](../docs/archive/D升级E051对接清单-2026-10-15.md)。

- [可安装 wheel](shumei_intelligent_analysis-0.5.1-py3-none-any.whl)
- [完整交接 ZIP](E-visible-people-0.5.1-2026-10-15.zip)
- [完整性回执](E-visible-people-0.5.1-2026-10-15.receipt.json)

```powershell
python -m pip install ./release/shumei_intelligent_analysis-0.5.1-py3-none-any.whl
```

依赖由 wheel 的 METADATA 声明；真实推理另安装 `torch>=2.6` 和 `opencv-python>=4.10`，权重独立下载校验。该交付不是离线依赖全集。知识加载使用 D 的 ServiceBindings 注入，详细签名见服务契约。

## 0.5.0：保留的历史交付

日期：2026-10-14。

- `shumei_intelligent_analysis-0.5.0-py3-none-any.whl`：D 可安装的 Python 包。
- `E-visible-people-0.5.0-2026-10-14.zip`：安装包、源码、测试、服务契约和验收记录。
- `E-visible-people-0.5.0-2026-10-14.receipt.json`：wheel/ZIP 的 SHA-256 和验证边界。

在 D 使用的 Python 3.13 环境中安装 wheel 后，还需按 [服务契约](../E_SERVICE_CONTRACT.md) 注入 D 的真实任务、读取和索引路径，安装本身不代表联调已完成。

```powershell
python -m pip install ./release/shumei_intelligent_analysis-0.5.0-py3-none-any.whl
```

49 项测试通过，包括独立 SQLite 的 C 0.1.9 真实资料接收；16 份文档、655 片段及重启问答验证通过。DM-Count CPU 与官方实现输出一致。

授权录像/标注、新画面人数存储、P2PNet 真实对照、真实预测和 D/B 正式联调仍待验收。原件、环境配置、录像和权重不包含在本发布目录中。详见 [交付与联调说明](../docs/archive/E050交付与联调说明.md)。
