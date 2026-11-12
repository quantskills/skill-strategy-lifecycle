# 数据协议

合并长表需要 `date / symbol / factor / value / next_return`。同一日期和因子的股票数默认至少 20；`next_return` 必须是因子形成后的下一交易期收益。

目录模式下，每个 CSV/Parquet 文件需要 `date / symbol / factor_value`，文件名作为因子名；收益表需要 `date / symbol / next_return`。程序按日期和代码做内连接。

宽表也受支持：除 `date / symbol / next_return` 外的列被视为因子。

真实监控应保存每日输入数据指纹，并冻结历史值。供应商修订数据后应作为新版本重跑，不能覆盖原预警记录。
