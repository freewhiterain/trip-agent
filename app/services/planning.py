"""自然语言需求提取与行程草稿展示。"""

from __future__ import annotations

import re
from datetime import date

from app.config import settings
from app.schemas.planning import TravelPlanDraft, TravelRequirementDraft


CHINESE_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


class RequirementExtractor:
    async def extract(self, text: str) -> TravelRequirementDraft:
        draft = self._extract_rules(text)
        if not draft.missing_fields() or not settings.dashscope_api_key:
            return draft
        try:
            from app.agents.handoffs.travel_agent import get_llm

            structured = get_llm().with_structured_output(TravelRequirementDraft)
            return await structured.ainvoke(
                [
                    {"role": "system", "content": "从用户文本提取国内旅行需求。未明确的信息必须返回 null，不得猜测日期、城市、预算或人数。"},
                    {"role": "user", "content": text},
                ]
            )
        except Exception:
            return draft

    @staticmethod
    def _extract_rules(text: str) -> TravelRequirementDraft:
        origin_match = re.search(r"从([^，,。\s]{2,12}?)(?:出发|去)", text)
        destination_match = re.search(r"(?:去|规划|前往)([^，,。\s\d一二三四五六七八九十]{2,12}?)(?=\d|[一二三四五六七八九十]|旅|游|，|,|。|$)", text)
        days_match = re.search(r"(\d{1,2}|[一二三四五六七八九十])(?:天|日)(?:游|行程)", text)
        if days_match is None:
            days_match = re.search(r"(?:游玩|行程|玩)(\d{1,2}|[一二三四五六七八九十])天", text)
        budget_match = re.search(r"预算(?:约|大约|为)?\s*(\d+(?:\.\d+)?)", text)
        date_match = re.search(r"(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})日?", text)
        days = None
        if days_match:
            raw = days_match.group(1)
            days = int(raw) if raw.isdigit() else CHINESE_NUMBERS.get(raw)
        departure_date = None
        if date_match:
            departure_date = date(*(int(value) for value in date_match.groups()))
        styles = [keyword for keyword in ["文化", "美食", "亲子", "户外", "休闲", "自然"] if keyword in text]
        return TravelRequirementDraft(
            origin=origin_match.group(1) if origin_match else None,
            destination=destination_match.group(1) if destination_match else None,
            departure_date=departure_date,
            days=days,
            budget=float(budget_match.group(1)) if budget_match else None,
            styles=styles,
        )


def render_plan_markdown(draft: TravelPlanDraft) -> str:
    lines = [f"# {draft.requirement.destination}{draft.requirement.days}日行程草稿", ""]
    for day in draft.itinerary:
        lines.append(f"## 第{day.day}天 · {day.date.isoformat()}")
        labels = {"morning": "上午", "afternoon": "下午", "evening": "晚上"}
        for slot in day.slots:
            lines.append(f"- **{labels[slot.period]}**：{slot.title}。{slot.description}")
        lines.append("")
    if draft.warnings:
        lines.append("## 数据说明")
        lines.extend(f"- {warning}" for warning in draft.warnings)
    lines.append("\n当前为可编辑规划草稿，不包含下单、预订或支付服务。")
    return "\n".join(lines)
