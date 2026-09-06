"""Shared human-readable backfill status, separate from settlement statistics."""
from __future__ import annotations


def backfill_status_text(value: object) -> str:
    pending = "历史回补状态待确认；从新固定提醒开始记录不受影响。"
    if not isinstance(value, dict):
        return pending
    status = value.get("status")
    if status == "running":
        return "正在检查可验证的固定提醒历史……"
    if status == "completed":
        return "可验证历史已回补；无法验证的数据不计入统计。"
    if status == "failed":
        return "历史回补检查失败；从新固定提醒开始记录不受影响。"
    if status != "partial":
        return pending

    def count(key: str) -> int:
        try:
            return max(0, int(str(value.get(key) or 0)))
        except (ValueError, TypeError):
            return 0

    return (
        f"本轮已回补{count('settled')}笔，未取得行情{count('unavailable')}笔，"
        f"跳过{count('skipped')}笔，未完成{count('pending')}笔。"
        "这是回补任务记录，跳过不代表未结算；当前结算数量以上方统计为准。"
    )
