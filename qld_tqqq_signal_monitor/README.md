# QLD / TQQQ 自动信号通知

该程序在每个美国股市工作日收盘后检查已固定的月度趋势与波动率策略，并把结果发布到仓库 issue #1。它只生成通知，不连接券商，也不自动下单。

## 固定策略

- 趋势输入：QQQ 价格收盘序列。
- 三组趋势参数：50/200、55/220、60/240。
- 每组中 SMA 或 EMA 任一快线高于慢线，该子模型即开启。
- 趋势分数：开启子模型数除以 3。
- 风险输入：NASDAQ-100 最近 21 个交易日的年化已实现波动率。
- 目标波动率：60%。
- QLD 权重：`score × min(2, 0.60 / vol) / 2`。
- TQQQ 权重：`score × min(3, 0.60 / vol) / 3`。
- 剩余仓位：短期美国国债或现金。
- 仅在月末收盘生成新目标，下一交易日开盘执行。其他交易日报告继续持有。

QLD 和 TQQQ 是两种替代实施方案，不能把两行权重同时叠加。

## 数据与安全控制

- QQQ：Yahoo Finance chart endpoint 的价格收盘字段。程序不使用含现金分配调整的 `adjclose`。
- NASDAQ-100：FRED `NASDAQ100` 日收盘序列。
- 两个数据源都必须包含报告日，并通过长度、重复日期、正价格和最近 60 日收益相关性检查。
- 数据缺失、过期、格式变化或交叉校验失败时，程序拒绝产生交易指令，并在 issue 中报告错误。

## 自动运行

GitHub Actions 在每周二至周六 05:30 UTC 运行，对应前一个美国市场交易日收盘后的夜间。工作流同时支持手动指定报告日期。

## 本地运行

```bash
python -m pip install -r qld_tqqq_signal_monitor/requirements.txt
python qld_tqqq_signal_monitor/monitor.py
```

指定历史报告日：

```bash
python qld_tqqq_signal_monitor/monitor.py --report-date 2026-08-28
```

输出写入 `signal_output/report.md` 与 `signal_output/signal.json`。
