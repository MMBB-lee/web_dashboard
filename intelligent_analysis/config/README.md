# 配置样例

[p2pnet-cpu.example.json](p2pnet-cpu.example.json) 是E内部VideoAnalysisConfig的完整样例：P2PNet、指定权重版本、CPU/pytorch、阈值0.5、10秒抽帧、5分钟聚合。

全画面ROI仅供安装试跑，实际机位需要冻结自己的ROI；不能把该对象直接当作D公共HTTP请求，D负责字段映射。模型目录在服务端通过P2PNET_BUNDLE_DIR配置，不从用户请求取得。

[安装与接入](../docs/P2PNet安装与具体配置-2026-10-18.md)，[字段契约](../E_SERVICE_CONTRACT.md)。
