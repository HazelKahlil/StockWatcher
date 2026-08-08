# 数据中断处理

| 情况 | 行为 |
|---|---|
| 401 | 不重试；凭据无效；STOPPED；提示更新 Key |
| 403 | 不重试；权限不足；STOPPED |
| 429 | 遵守 Retry-After；限频保护；不在两个接口间抖动 |
| 503 freshness | 不使用旧值；本轮无候选 |
| 500/502/504 | 历史请求有界重试；实时单轮失败即无候选 |
| timeout/network | 实时请求遵守总 deadline；连续失败进入 STOPPED |
| schema 变化 | fail closed；只保存字段名摘要，不保存正文 |
| source_ts 缺失 | DEGRADED；received_ts 不得替代；候选关闭 |
| 时间回滚 | 清空基线并 STOPPED |
| 切源/换 Key | 候选关闭、清空基线、三周期新鲜预热 |

恢复后不补发中断期间的旧提醒。

