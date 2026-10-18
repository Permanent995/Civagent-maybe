"""训练场（arena）：离线评测环，与在线对局栈解耦。

分层
----
    paths.py            配置单点（全部可被环境变量覆盖）—— 唯一允许读 env 的地方
    procs.py            进程层：残留清理 / 单进程强杀（退出机制）
    gamelog.py          日志与索引层：解析 log → record（含 complete 口径）/ 覆盖率
    scoring.py          评分口径：power 权重、征服规则、名次、归一化
    report.py           单局三轴 → 跨局榜（主榜=均名次）
    nation_strength.py  国榜合成 + markdown 渲染（可复现产物）
    harness.py          单局执行层：地图注入 + 启动 JVM + 收尸 + 写索引
    run_rotation.py     轮换计划层：拉丁方排赛 + 墙钟预算（只编排，不碰进程细节）
    run_batch.py        1v1 批次（T1/T2 挂大脑时用）
    fit_weights.py      回归层：从对局结果拟合因子权重（旧榜可作 ×0.1 先验特征）

导入约定
--------
包内一律 `from . import x`；直接用脚本跑（`python arena/report.py`）时靠各模块内的
`except ImportError` 回退把本目录加进 sys.path。两种方式都能用，不要再在调用方写
`sys.path.insert`。

CLI
---
    python -m arena.report --prior weight 0.1
    python arena/report.py --prior-weight 0.1        # 等价
"""

__all__ = [
    "paths", "procs", "gamelog", "scoring", "report",
    "nation_strength", "harness", "run_rotation", "run_batch", "fit_weights",
]
