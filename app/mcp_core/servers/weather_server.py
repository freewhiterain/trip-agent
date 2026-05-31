"""
天气服务 MCP Server
使用高德天气 API 查询天气预报

启动方式：python app/mcp_core/servers/weather_server.py
"""
import os
import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

mcp = FastMCP("天气服务")

AMAP_API_KEY = os.getenv("AMAP_API_KEY", "")

CITY_ADCODE = {
    "北京": "110000",
    "上海": "310000",
    "广州": "440100",
    "深圳": "440300",
    "西安": "610100",
    "成都": "510100",
    "杭州": "330100",
    "南京": "320100",
    "重庆": "500000",
    "武汉": "420100",
    "青岛": "370200",
    "厦门": "350200",
    "大理": "532900",
    "丽江": "530700",
}


@mcp.tool()
async def get_weather(city: str, forecast: bool = True) -> str:
    """
    查询城市天气信息

    参数：
    - city: 城市名称，如 "西安"、"成都"
    - forecast: True 获取预报天气（3天），False 获取实时天气

    返回：
    - 格式化的天气信息字符串
    """
    if not AMAP_API_KEY:
        return "❌ 高德 API Key 未配置，请在 .env 文件中设置 AMAP_API_KEY"

    adcode = CITY_ADCODE.get(city)
    if not adcode:
        return f"❌ 暂不支持城市：{city}，支持城市：{', '.join(CITY_ADCODE.keys())}"

    extensions = "all" if forecast else "base"
    url = "https://restapi.amap.com/v3/weather/weatherInfo"
    params = {"key": AMAP_API_KEY, "city": adcode, "extensions": extensions, "output": "JSON"}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=10)
            data = response.json()

        if data.get("status") != "1":
            return f"❌ 天气查询失败：{data.get('info', '未知错误')}"

        if forecast:
            forecasts = data.get("forecasts", [{}])[0].get("casts", [])
            lines = [f"## {city} 天气预报"]
            for cast in forecasts[:3]:
                lines.append(
                    f"📅 {cast.get('date')}（{cast.get('week', '')}）：\n"
                    f"   白天 {cast.get('dayweather')} {cast.get('daytemp')}°C\n"
                    f"   夜间 {cast.get('nightweather')} {cast.get('nighttemp')}°C"
                )
            return "\n".join(lines)
        else:
            lives = data.get("lives", [{}])[0]
            return (
                f"## {city} 实时天气\n"
                f"天气：{lives.get('weather')}\n"
                f"温度：{lives.get('temperature')}°C\n"
                f"湿度：{lives.get('humidity')}%\n"
                f"风向：{lives.get('winddirection')}\n"
                f"风力：{lives.get('windpower')} 级\n"
                f"更新时间：{lives.get('reporttime')}"
            )

    except Exception as e:
        return f"❌ 天气查询异常：{e}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=9001)
