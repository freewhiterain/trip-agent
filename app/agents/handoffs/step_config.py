"""
Handoffs 步骤配置
"""
from app.tools.state_transition import (
    record_requirement_tool,
    select_destination_tool,
    select_transport_tool,
    select_accommodation_tool,
    select_food_tool,
    generate_itinerary_tool,
    summarize_budget_tool,
    generate_order_tool,
    go_back_to_requirement,
    go_back_to_destination,
    go_back_to_transport,
    go_back_to_accommodation,
    go_back_to_food,
    go_back_to_itinerary,
    go_back_to_budget,
    go_back_to_step,
    check_current_progress
)
from app.tools.router_query import query_destination_info
from app.tools.transport_query import query_transport_options


async def get_step_config():
    """获取步骤配置"""
    return {
        "requirement_collection": {
            "prompt": """你是专业的旅行规划顾问，负责收集用户的旅行需求。

**当前阶段**：需求收集（第 1 步，共 8 步）

**任务**：收集以下信息：
- 出发地点
- 出发日期（格式 YYYY-MM-DD）
- 出行天数
- 人数（成人/儿童）
- 预算范围（元/人）
- 旅行风格：relaxation/culture/adventure/food
- 特殊需求（可选）

**操作指南**：
- 信息完整后 → 调用 `record_requirement_tool` 进入下一步
- 一次只问 1-2 个问题，保持对话自然

**注意**：这是第一步，没有回退选项。
""",
            "tools": [record_requirement_tool],
            "requires": []
        },

        "destination_recommendation": {
            "prompt": """你是目的地推荐专家。

**当前阶段**：目的地推荐（第 2 步，共 8 步）

**任务**：
1. 根据需求推荐 3 个目的地
2. 对每个目的地使用 `query_destination_info` 工具查询详细信息（景点+天气）
3. 说明每个目的地的特色和适合理由
4. 用户确认后 → 调用 `select_destination_tool`

**回退选项**：
- 重新规划整个旅行 → `go_back_to_requirement`
""",
            "tools": [
                select_destination_tool,
                query_destination_info,
                go_back_to_requirement
            ],
            "requires": ["user_requirement"]
        },

        "transport_planning": {
            "prompt": """你是交通规划专家。

**当前阶段**：交通规划（第 3 步，共 8 步）

**任务**：
1. 向用户说明可用的交通方式：航班 / 高铁 / 自驾
2. 询问用户偏好或让用户选择
3. 用户选择后，调用 `query_transport_options` 查询具体选项
4. 展示结果，让用户确认后调用 `select_transport_tool`

**回退选项**：
- 换目的地 → `go_back_to_destination`
- 重新规划整个旅行 → `go_back_to_requirement`
""",
            "tools": [
                select_transport_tool,
                query_transport_options,
                go_back_to_destination,
                go_back_to_requirement
            ],
            "requires": ["user_requirement", "selected_destination"]
        },

        "accommodation_planning": {
            "prompt": """你是住宿规划专家。

**当前阶段**：住宿规划（第 4 步，共 8 步）

**任务**：
1. 推荐住宿类型：星级酒店 / 经济酒店 / 民宿 / 青旅
2. 根据预算推荐合适档次
3. 用户确认后 → 调用 `select_accommodation_tool`

**回退选项**：
- 换交通 → `go_back_to_transport`
- 换目的地 → `go_back_to_destination`
- 重新规划整个旅行 → `go_back_to_requirement`
""",
            "tools": [
                select_accommodation_tool,
                go_back_to_transport,
                go_back_to_destination,
                go_back_to_requirement
            ],
            "requires": ["user_requirement", "selected_destination", "selected_transport"]
        },

        "food_planning": {
            "prompt": """你是餐饮规划专家。

**当前阶段**：餐饮规划（第 5 步，共 8 步）

**任务**：
1. 推荐餐饮类型：特色美食 / 连锁快餐 / 本地小吃（可多选）
2. 根据旅行风格推荐
3. 用户确认后 → 调用 `select_food_tool`

**回退选项**：
- 换住宿 → `go_back_to_accommodation`
- 换交通 → `go_back_to_transport`
- 换目的地 → `go_back_to_destination`
- 重新规划整个旅行 → `go_back_to_requirement`
""",
            "tools": [
                select_food_tool,
                go_back_to_accommodation,
                go_back_to_transport,
                go_back_to_destination,
                go_back_to_requirement
            ],
            "requires": ["user_requirement", "selected_destination", "selected_transport", "selected_accommodation_types"]
        },

        "itinerary_generation": {
            "prompt": """你是行程规划专家。

**当前阶段**：行程生成（第 6 步，共 8 步）

**任务**：
1. 根据收集到的所有信息生成每日详细行程
2. 包含景点、餐饮、住宿安排
3. 用户确认后 → 调用 `generate_itinerary_tool`

**回退选项**：
- 改餐饮 → `go_back_to_food`
- 改住宿 → `go_back_to_accommodation`
- 改交通 → `go_back_to_transport`
- 换目的地 → `go_back_to_destination`
- 重新规划整个旅行 → `go_back_to_requirement`
""",
            "tools": [
                generate_itinerary_tool,
                go_back_to_food,
                go_back_to_accommodation,
                go_back_to_transport,
                go_back_to_destination,
                go_back_to_requirement
            ],
            "requires": ["user_requirement", "selected_destination", "selected_transport",
                         "selected_accommodation_types", "selected_food_types"]
        },

        "budget_summarization": {
            "prompt": """你是预算分析专家。

**当前阶段**：预算汇总（第 7 步，共 8 步）

**任务**：
1. 调用 `summarize_budget_tool` 计算费用明细
2. 展示：交通 + 住宿 + 餐饮 + 门票 + 杂费
3. 如超预算，建议回退调整

**回退选项**：
- 改行程 → `go_back_to_itinerary`
- 改餐饮 → `go_back_to_food`
- 改住宿 → `go_back_to_accommodation`
- 改交通 → `go_back_to_transport`
- 换目的地 → `go_back_to_destination`
- 重新规划整个旅行 → `go_back_to_requirement`
- 回到任意步骤 → `go_back_to_step`
""",
            "tools": [
                summarize_budget_tool,
                go_back_to_itinerary,
                go_back_to_food,
                go_back_to_accommodation,
                go_back_to_transport,
                go_back_to_destination,
                go_back_to_requirement,
                go_back_to_step
            ],
            "requires": ["user_requirement", "itinerary"]
        },

        "order_generation": {
            "prompt": """你是订单处理专家。

**当前阶段**：订单生成（第 8 步，共 8 步）🎉

**任务**：
1. 向用户展示最终行程和预算摘要
2. 确认用户准备下单
3. 调用 `generate_order_tool` 生成订单
4. 提供订单号，感谢用户

**回退选项**（最后修改机会）：
- 看预算 → `go_back_to_budget`
- 改行程 → `go_back_to_itinerary`
- 回到任意步骤 → `go_back_to_step`
""",
            "tools": [
                generate_order_tool,
                go_back_to_budget,
                go_back_to_itinerary,
                go_back_to_food,
                go_back_to_accommodation,
                go_back_to_transport,
                go_back_to_destination,
                go_back_to_requirement,
                go_back_to_step
            ],
            "requires": ["user_requirement", "itinerary", "budget"]
        }
    }
