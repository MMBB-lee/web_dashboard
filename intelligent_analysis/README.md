# E 智能分析 · 0.6.2

提供知识问答、固定ROI画面人数计数与短时预测。正式计数模型为 **P2PNet / PyTorch CPU**，指标为 `camera_visible_people`，不代表进出人次或全景区在园人数。

## 拉取后运行

仓库已包含模型源码、SHTechA权重、试跑短片、配置及安装脚本，无需另收模型ZIP。Windows安装Python3.13和Git后：

```powershell
git clone https://github.com/w3022843643/shumei_intelligent_analysis.git
cd shumei_intelligent_analysis
powershell -ExecutionPolicy Bypass -File .\setup-p2pnet.ps1
```

已有副本先 `git pull --ff-only`。首次会联网安装CPU依赖；已有依赖可加 `-SkipInstall`。结果在 `artifacts/clone-and-run/`。干净Git副本已验证模型摘要和短片CPU推理；本机验证复用了现有依赖。

[安装与具体配置](docs/P2PNet安装与具体配置-2026-10-18.md) 说明模型目录、参数、单独核验和D的环境变量。这是E模块试跑；完整Web后端由D启动。

## 按目的找文件

| 目的 | 入口 |
|---|---|
| 查看每一步任务与完成标准 | [E全流程任务与自检](docs/E全流程任务与自检-2026-10-18.md) |
| D接入计算、任务和结果 | [服务契约](E_SERVICE_CONTRACT.md)、[D当前安装与回传要求](docs/致D-E062自检修复与安装回执要求-2026-10-18.md) |
| C准备下一批输入 | [C当前输入需求](docs/致C-自检后剩余输入需求-2026-10-18.md) |
| A/B/C/D各自下一步 | [团队对接导航](docs/团队需求-2026-10-15/README.md) |
| 查看已测结果与局限 | [验收证据导航](docs/acceptance/README.md) |
| 安装wheel或核对发布摘要 | [发布目录](release/README.md) |
| 找脚本、历史回执 | [脚本导航](demo/README.md)、[全部文档](docs/README.md) |

## 当前状态

| 能力 | 已验证 | 仍待完成 |
|---|---|---|
| 知识与数值问答 | 16份真实资料、严格加载/重启/故障保护；D真实resolver的隔离HTTP成功与不足分支 | 人工语义复核、部署/MySQL原始证据 |
| P2PNet | 随仓库模型可加载；自带短片及五段公开视频CPU试跑 | 授权录像正式HTTP闭环、独立人工MAE |
| 计数存储 | D适配→E计算→C SQLite合成写回、原子完成及读回 | 真实连续录像完整5分钟窗 |
| 短时预测 | 阻尼Holt/最近值基线、时间前缀回测、合成15/30分钟存储 | 真实分跨度误差；60分钟未开放 |

E回归 **139通过、1项历史对照跳过**，隔离知识HTTP **18项检查通过**，B纯契约 **8项通过**。D原套件在明确初始化空知识快照的前提下 **405通过**；原空环境400通过/5失败的记录保留。GPU/OpenVINO、业务MySQL、部署视频HTTP与浏览器联验仍待验证。完整边界见自检和证据导航。

## 目录

| 位置 | 用途 |
|---|---|
| `setup-p2pnet.ps1` | Windows安装及CPU试跑入口 |
| `config/` | 可校验的配置样例 |
| `model_bundle/p2pnet/` | 原始模型源码、权重、短片和摘要 |
| `intelligent_analysis/` | 服务、知识、视觉、预测和纯计算处理器 |
| `tests/`、`demo/` | 回归测试、接收/验收/发布工具 |
| `docs/` | 当前说明、团队回执、证据和样例 |
| `release/` | 各版本发布快照及SHA回执 |
| `artifacts/` | 本地计算结果、人工工作包、原件和隔离数据库；Git忽略 |

D唯一Worker负责领取、续租、重试、恢复及通过C写库；E只计算，B/A负责展示。固定ROI逐帧人数不能累加成人次，人工未复核保持null，缺测不补零。

开发时安装 `python -m pip install -e ".[dev]"`，运行 `python -m pytest -q` 与 `python -m ruff check intelligent_analysis tests demo`。真实C接收测试有额外环境前置条件，见脚本导航。本轮整理不改变0.6.2业务源码或覆盖已发布包。
