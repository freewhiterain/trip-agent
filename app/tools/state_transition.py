"""
状态转换工具
用于 Handoffs 流程中的步骤跳转和数据记录
"""
from datetime import datetime
from typing import Literal, Optional
from langchain.tools import tool, ToolRuntime
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from app.core.state import TravelState, UserRequirement
from app.utils.logger import app_logger


# ============== 1. 需求收集工具 ==============

@tool
def record_requirement_tool(
        departure_city: str,
        departure_date: str,
        travel_days: int,
        budget_min: float,
        budget_max: float,
        travel_styles: list[str],
        special_needs: str = "",
        adult_count: Optional[int] = 1,
        children_count: Optional[int] = 0,
        destination: Optional[str] = None,
        runtime: ToolRuntime[None, TravelState] = None
) -> Command:
    """
    记录用户旅行需求，并转换到目的地推荐步骤。

    参数说明：
    - departure_city: 出发地点
    - departure_date: 出发日期，格式 YYYY-MM-DD
    - travel_days: 出行天数
    - adult_count: 成人数量
    - children_count: 儿童数量（< 12 岁）
    - budget_min: 预算下限（元/人）
    - budget_max: 预算上限（元/人）
    - travel_styles: 旅行风格列表，可选值：["relaxation", "culture", "adventure", "food"]
    - special_needs: 特殊需求（可选）
    - destination: 目的地（可选，如果用户已经指定）
    """
    app_logger.info(f"记录用户需求: {departure_date}, {travel_days}天, 预算 {budget_min}-{budget_max}")

    try:
        datetime.strptime(departure_date, "%Y-%m-%d")
    except ValueError:
        return Command(update={
            "messages": [
                ToolMessage(
                    content="❌ 日期格式错误，请使用 YYYY-MM-DD 格式，如 2025-08-01",
                    tool_call_id=runtime.tool_call_id
                )
            ]
        })

    avg_budget = (budget_min + budget_max) / 2
    if avg_budget < 3000:
        budget_level = "economy"
    elif avg_budget < 8000:
        budget_level = "comfort"
    else:
        budget_level = "luxury"

    requirement = UserRequirement(
        departure_city=departure_city,
        destination=destination,
        departure_date=departure_date,
        travel_days=travel_days,
        adult_count=adult_count,
        children_count=children_count,
        budget_min=budget_min,
        budget_max=budget_max,
        budget_level=budget_level,
        travel_styles=travel_styles,
        special_needs=special_needs if special_needs else None
    )

    return Command(update={
        "messages": [
            ToolMessage(
                content=f"需求已记录！\n"
                        f"出发日期：{departure_date}\n"
                        f"{travel_days} 天 | {adult_count + children_count} 人\n"
                        f"预算：{budget_min}-{budget_max} 元/人（{budget_level}级）\n"
                        f"风格：{', '.join(travel_styles)}",
                tool_call_id=runtime.tool_call_id
            )
        ],
        "user_requirement": requirement,
        "current_step": "destination_recommendation"
    })


# ============== 2. 目的地选择工具 ==============

@tool
def select_destination_tool(
        destination: str,
        runtime: ToolRuntime[None, TravelState] = None
) -> Command:
    """
    确认用户选择的目的地，并转换到交通规划步骤。

    参数说明：
    - destination: 目的地名称，如 "西安"、"成都"
    """
    app_logger.info(f"用户选择目的地: {destination}")

    return Command(update={
        "messages": [
            ToolMessage(
                content=f"目的地已确认：{destination}",
                tool_call_id=runtime.tool_call_id
            )
        ],
        "selected_destination": destination,
        "current_step": "transport_planning"
    })


# ============== 3. 交通方式选择工具 ==============

@tool
def select_transport_tool(
        transport_type: str,
        runtime: ToolRuntime[None, TravelState] = None
) -> Command:
    """
    确认用户选择的交通方式，并转换到住宿规划步骤。

    参数说明：
    - transport_type: 交通方式，可选值：flight（航班）、train（高铁）、driving（自驾）
    """
    app_logger.info(f"用户选择交通方式: {transport_type}")

    if transport_type not in ["flight", "train", "driving"]:
        return Command(update={
            "messages": [
                ToolMessage(
                    content="❌交通方式无效，请选择：flight、train 或 driving",
                    tool_call_id=runtime.tool_call_id
                )
            ]
        })

    transport_labels = {"flight": "航班", "train": "高铁", "driving": "自驾"}

    return Command(update={
        "messages": [
            ToolMessage(
                content=f"交通方式已确认：{transport_labels[transport_type]}",
                tool_call_id=runtime.tool_call_id
            )
        ],
        "selected_transport": transport_type,
        "current_step": "accommodation_planning"
    })


# ============== 4. 住宿偏好选择工具 ==============

@tool
def select_accommodation_tool(
        accommodation_types: list[str],
        runtime: ToolRuntime[None, TravelState] = None
) -> Command:
    """
    确认用户选择的住宿偏好（可多选），并转换到餐饮规划步骤。

    参数说明：
    - accommodation_types: 住宿类型列表，可选值：
      ["star_hotel", "economy_hotel", "hostel", "youth_hostel"]
    """
    app_logger.info(f"用户选择住宿类型: {accommodation_types}")

    valid_types = {"star_hotel", "economy_hotel", "hostel", "youth_hostel"}
    if not all(t in valid_types for t in accommodation_types):
        return Command(update={
            "messages": [
                ToolMessage(
                    content=f"❌住宿类型无效，请从以下选择：{', '.join(valid_types)}",
                    tool_call_id=runtime.tool_call_id
                )
            ]
        })

    type_labels = {
        "star_hotel": "星级酒店",
        "economy_hotel": "经济酒店",
        "hostel": "特色民宿",
        "youth_hostel": "青年旅社"
    }
    selected_labels = [type_labels[t] for t in accommodation_types]

    return Command(update={
        "messages": [
            ToolMessage(
                content=f"住宿偏好已确认：{', '.join(selected_labels)}",
                tool_call_id=runtime.tool_call_id
            )
        ],
        "selected_accommodation_types": accommodation_types,
        "current_step": "food_planning"
    })


# ============== 5. 餐饮偏好选择工具 ==============

@tool
def select_food_tool(
        food_types: list[str],
        runtime: ToolRuntime[None, TravelState] = None
) -> Command:
    """
    确认用户选择的餐饮偏好（可多选），并转换到行程生成步骤。

    参数说明：
    - food_types: 餐饮类型列表，可选值：["specialty", "chain", "local"]
    """
    app_logger.info(f"用户选择餐饮类型: {food_types}")

    valid_types = {"specialty", "chain", "local"}
    if not all(t in valid_types for t in food_types):
        return Command(update={
            "messages": [
                ToolMessage(
                    content=f"❌ 餐饮类型无效，请从以下选择：{', '.join(valid_types)}",
                    tool_call_id=runtime.tool_call_id
                )
            ]
        })

    type_labels = {"specialty": "特色美食", "chain": "连锁快餐", "local": "本地小吃"}
    selected_labels = [type_labels[t] for t in food_types]

    return Command(update={
        "messages": [
            ToolMessage(
                content=f"餐饮偏好已确认：{', '.join(selected_labels)}",
                tool_call_id=runtime.tool_call_id
            )
        ],
        "selected_food_types": food_types,
        "current_step": "itinerary_generation"
    })


# ============== 6. 行程生成工具 ==============

@tool
def generate_itinerary_tool(
        runtime: ToolRuntime[None, TravelState] = None
) -> Command:
    """
    生成完整行程安排，并转换到预算汇总步骤。
    综合用户需求、目的地、交通、住宿、餐饮信息生成详细的每日行程。
    """
    app_logger.info("开始生成行程...")

    state = runtime.state

    required_fields = [
        "user_requirement", "selected_destination", "selected_transport",
        "selected_accommodation_types", "selected_food_types"
    ]
    missing = [f for f in required_fields if f not in state or state[f] is None]
    if missing:
        return Command(update={
            "messages": [
                ToolMessage(
                    content=f"❌ 信息不完整，缺少：{', '.join(missing)}",
                    tool_call_id=runtime.tool_call_id
                )
            ]
        })

    travel_days = state["user_requirement"]["travel_days"]
    itinerary = []
    for day in range(1, travel_days + 1):
        itinerary.append({
            "day_number": day,
            "activities": [f"第{day}天上午活动", f"第{day}天下午活动"],
            "meals": ["早餐", "午餐", "晚餐"],
            "accommodation": "酒店"
        })

    return Command(update={
        "messages": [
            ToolMessage(
                content=f"已生成 {travel_days} 天详细行程！",
                tool_call_id=runtime.tool_call_id
            )
        ],
        "itinerary": itinerary,
        "current_step": "budget_summarization"
    })


# ============== 7. 预算汇总工具 ==============

@tool
def summarize_budget_tool(
        runtime: ToolRuntime[None, TravelState] = None
) -> Command:
    """
    汇总各项费用，生成预算明细，并转换到行程草稿确认步骤。
    包含：交通、住宿、餐饮、景点门票、其他杂费。
    """
    app_logger.info("开始计算预算...")

    state = runtime.state
    requirement = state["user_requirement"]
    total_people = requirement["adult_count"] + requirement["children_count"]
    travel_days = requirement["travel_days"]

    transport_cost = 500 * total_people
    accommodation_cost = 300 * travel_days * total_people
    food_cost = 150 * travel_days * total_people
    attractions_cost = 200 * travel_days * total_people
    misc_cost = 100 * travel_days * total_people
    total_cost = transport_cost + accommodation_cost + food_cost + attractions_cost + misc_cost

    budget_breakdown = {
        "transport": transport_cost,
        "accommodation": accommodation_cost,
        "food": food_cost,
        "attractions": attractions_cost,
        "misc": misc_cost,
        "total": total_cost
    }

    return Command(update={
        "messages": [
            ToolMessage(
                content=f"预算汇总完成！\n"
                        f"总计：{total_cost:.2f} 元\n"
                        f"   - 交通：{transport_cost:.2f}\n"
                        f"   - 住宿：{accommodation_cost:.2f}\n"
                        f"   - 餐饮：{food_cost:.2f}\n"
                        f"   - 门票：{attractions_cost:.2f}\n"
                        f"   - 其他：{misc_cost:.2f}",
                tool_call_id=runtime.tool_call_id
            )
        ],
        "budget": budget_breakdown,
        "plan_status": "draft",
        "current_step": "plan_review"
    })


# ============== 8. 行程草稿确认工具 ==============

@tool
def confirm_plan_draft_tool(
        runtime: ToolRuntime[None, TravelState] = None
) -> Command:
    """
    确认当前行程草稿，完成首版旅行规划流程。

    此操作只更新会话内的规划状态，不创建订单、不执行预订、
    不发起支付，也不向外部平台发送数据。
    """
    app_logger.info("确认行程草稿")

    return Command(update={
        "messages": [
            ToolMessage(
                content="✅ 行程草稿已确认。当前版本仅提供规划与推荐，"
                        "不会创建订单、执行预订或发起支付。",
                tool_call_id=runtime.tool_call_id
            )
        ],
        "plan_status": "confirmed",
        "current_step": "planning_complete",
    })


# ============== 回退工具 ==============

ALL_STEPS = [
    "requirement_collection",
    "destination_recommendation",
    "transport_planning",
    "accommodation_planning",
    "food_planning",
    "itinerary_generation",
    "budget_summarization",
    "plan_review",
    "planning_complete",
]

STEP_LABELS = {
    "requirement_collection": "需求收集",
    "destination_recommendation": "目的地推荐",
    "transport_planning": "交通规划",
    "accommodation_planning": "住宿规划",
    "food_planning": "餐饮规划",
    "itinerary_generation": "行程生成",
    "budget_summarization": "预算汇总",
    "plan_review": "草稿确认",
    "planning_complete": "规划完成",
}

STEP_STATE_FIELDS = {
    "requirement_collection": ["user_requirement"],
    "destination_recommendation": ["selected_destination", "destination_options"],
    "transport_planning": ["selected_transport", "transport_options"],
    "accommodation_planning": ["selected_accommodation_types", "accommodation_options"],
    "food_planning": ["selected_food_types", "food_options"],
    "itinerary_generation": ["itinerary"],
    "budget_summarization": ["budget"],
    "plan_review": ["plan_status"],
    "planning_complete": [],
}


@tool
def go_back_to_step(
        target_step: Literal[
            "requirement_collection",
            "destination_recommendation",
            "transport_planning",
            "accommodation_planning",
            "food_planning",
            "itinerary_generation",
            "budget_summarization",
            "plan_review",
        ],
        reason: str,
        clear_subsequent_data: bool = True,
        runtime: ToolRuntime = None
) -> Command:
    """
    回退到指定的历史步骤，允许用户重新进行规划。

    参数说明：
    - target_step: 要回退到的目标步骤名称
    - reason: 回退原因，如 "用户希望更换目的地"
    - clear_subsequent_data: 是否清除目标步骤之后的所有数据（默认 True）
    """
    app_logger.info(f"回退请求: target_step={target_step}, reason={reason}")

    if target_step not in ALL_STEPS:
        return Command(update={
            "messages": [ToolMessage(content=f"无效的目标步骤: {target_step}", tool_call_id=runtime.tool_call_id)]
        })

    if target_step == "planning_complete":
        return Command(update={
            "messages": [ToolMessage(content="规划完成是最终状态，无法回退到此状态。", tool_call_id=runtime.tool_call_id)]
        })

    state_update = {"current_step": target_step}
    cleared_fields = []

    if clear_subsequent_data:
        target_index = ALL_STEPS.index(target_step)
        for step in ALL_STEPS[target_index:]:
            for field in STEP_STATE_FIELDS.get(step, []):
                state_update[field] = None
                cleared_fields.append(field)

    step_label = STEP_LABELS.get(target_step, target_step)
    response_parts = [f"已回退到【{step_label}】阶段", f"原因: {reason}"]
    if clear_subsequent_data and cleared_fields:
        response_parts.append("已清除后续步骤的数据")

    state_update["messages"] = [
        ToolMessage(content="\n".join(response_parts), tool_call_id=runtime.tool_call_id)
    ]

    return Command(update=state_update)


@tool
def go_back_to_requirement(reason: str = "用户需要修改旅行需求", runtime: ToolRuntime = None) -> Command:
    """快捷回退：返回到需求收集步骤，重新开始规划。适用于：重新规划、修改出发日期、人数、预算等。"""
    return go_back_to_step.invoke({"target_step": "requirement_collection", "reason": reason, "clear_subsequent_data": True, "runtime": runtime})


@tool
def go_back_to_destination(reason: str = "用户需要重新选择目的地", runtime: ToolRuntime = None) -> Command:
    """快捷回退：返回到目的地推荐步骤。适用于：换目的地、这个地方不想去了。"""
    return go_back_to_step.invoke({"target_step": "destination_recommendation", "reason": reason, "clear_subsequent_data": True, "runtime": runtime})


@tool
def go_back_to_transport(reason: str = "用户需要更换交通方式", runtime: ToolRuntime = None) -> Command:
    """快捷回退：返回到交通规划步骤。适用于：不想坐飞机了、改成高铁、还是自驾吧。"""
    return go_back_to_step.invoke({"target_step": "transport_planning", "reason": reason, "clear_subsequent_data": True, "runtime": runtime})


@tool
def go_back_to_accommodation(reason: str = "用户需要调整住宿偏好", runtime: ToolRuntime = None) -> Command:
    """快捷回退：返回到住宿规划步骤。适用于：住宿要求改一下、想住民宿。"""
    return go_back_to_step.invoke({"target_step": "accommodation_planning", "reason": reason, "clear_subsequent_data": True, "runtime": runtime})


@tool
def go_back_to_food(reason: str = "用户需要调整餐饮偏好", runtime: ToolRuntime = None) -> Command:
    """快捷回退：返回到餐饮规划步骤。适用于：餐饮偏好改一下、想多吃特色美食。"""
    return go_back_to_step.invoke({"target_step": "food_planning", "reason": reason, "clear_subsequent_data": True, "runtime": runtime})


@tool
def go_back_to_itinerary(reason: str = "用户需要调整行程安排", runtime: ToolRuntime = None) -> Command:
    """快捷回退：返回到行程生成步骤。适用于：行程安排不合理、想加景点、太累了。"""
    return go_back_to_step.invoke({"target_step": "itinerary_generation", "reason": reason, "clear_subsequent_data": True, "runtime": runtime})


@tool
def go_back_to_budget(reason: str = "用户需要重新计算预算", runtime: ToolRuntime = None) -> Command:
    """快捷回退：返回到预算汇总步骤。适用于：预算超了、重新算一下费用。"""
    return go_back_to_step.invoke({"target_step": "budget_summarization", "reason": reason, "clear_subsequent_data": True, "runtime": runtime})


@tool
def check_current_progress(runtime: ToolRuntime = None) -> str:
    """查询当前规划进度，展示已完成和待完成的步骤。适用于：用户问'现在到哪一步了'、'还有几步'。"""
    state = runtime.state
    current_step = state.get("current_step", "requirement_collection")

    try:
        current_index = ALL_STEPS.index(current_step)
    except ValueError:
        current_index = 0

    progress_lines = ["当前规划进度", ""]
    for i, step in enumerate(ALL_STEPS):
        label = STEP_LABELS.get(step, step)
        step_num = i + 1
        if i < current_index:
            progress_lines.append(f"  [{step_num}] {label} - 已完成")
        elif i == current_index:
            progress_lines.append(f"  [{step_num}] {label} - 当前步骤")
        else:
            progress_lines.append(f"  [{step_num}] {label} - 待完成")

    progress_lines.append("")
    progress_lines.append("已收集信息:")
    if state.get("user_requirement"):
        req = state["user_requirement"]
        progress_lines.append(f"  - 出发日期: {req.get('departure_date', '未设置')}")
        progress_lines.append(f"  - 出行天数: {req.get('travel_days', '未设置')} 天")
    if state.get("selected_destination"):
        progress_lines.append(f"  - 目的地: {state['selected_destination']}")
    if state.get("selected_transport"):
        transport_labels = {"flight": "航班", "train": "高铁", "driving": "自驾"}
        progress_lines.append(f"  - 交通: {transport_labels.get(state['selected_transport'], state['selected_transport'])}")

    return "\n".join(progress_lines)


ALL_ROLLBACK_TOOLS = [
    go_back_to_step,
    go_back_to_requirement,
    go_back_to_destination,
    go_back_to_transport,
    go_back_to_accommodation,
    go_back_to_food,
    go_back_to_itinerary,
    go_back_to_budget,
    check_current_progress
]
