import os, json, time, uuid
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import folium
from folium import plugins
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, HTMLResponse
from starlette.routing import Route
from starlette.staticfiles import StaticFiles
import uvicorn

# ── 配置 ──────────────────────────────────────────────────
JINA_API_KEY = os.getenv("JINA_API_KEY", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "")
STATIC_DIR = Path("static")
STATIC_DIR.mkdir(exist_ok=True)

geolocator = Nominatim(user_agent="combo-mcp")

WORLD_GEOJSON = None
try:
    r = httpx.get(
        "https://raw.githubusercontent.com/johan/world.geo.json/master/countries.geo.json",
        timeout=30
    )
    WORLD_GEOJSON = r.json()
except Exception as e:
    print(f"⚠️ GeoJSON 加载失败: {e}")
    WORLD_GEOJSON = {"type": "FeatureCollection", "features": []}

COUNTRY_ALIASES = {
    "usa": "United States of America", "united states": "United States of America",
    "uk": "United Kingdom", "britain": "United Kingdom", "uae": "United Arab Emirates",
    "russia": "Russian Federation", "south korea": "Korea, Republic of",
    "iran": "Iran (Islamic Republic of)", "syria": "Syrian Arab Republic",
    "vietnam": "Viet Nam", "venezuela": "Venezuela (Bolivarian Republic of)",
    "turkey": "Turkey", "egypt": "Egypt", "germany": "Germany",
    "france": "France", "japan": "Japan", "china": "China",
}

SEVERITY = {
    "critical": ("#ff1744", 10),
    "major":    ("#ff9100", 8),
    "minor":    ("#ffea00", 6),
    "watch":    ("#00e5ff", 5),
}

# ── 工具定义 ──────────────────────────────────────────────
TOOLS = [
    {
        "name": "fetch_url",
        "description": "抓取任意网页，返回干净的 Markdown 格式文本。适用场景：写作素材收集、文章风格分析、观点提取。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要抓取的网页完整地址"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "geocode_location",
        "description": "将地名转换为经纬度坐标。参数: location - 地名，如 'Tokyo, Japan'",
        "inputSchema": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "地名，如 'Paris, France'"}
            },
            "required": ["location"]
        }
    },
    {
        "name": "generate_world_map",
        "description": "生成带有标记点、路线和国家染色的世界交互地图。返回可访问的网页链接。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "地图标题"},
                "markers": {"type": "string", "description": "标记点JSON数组"},
                "routes": {"type": "string", "description": "路线JSON数组, 默认 []"},
                "zones": {"type": "string", "description": "国家染色JSON数组, 默认 []"},
                "theme": {"type": "string", "description": "dark 或 light, 默认 dark"},
                "center_lat": {"type": "number", "description": "中心纬度, 默认 25"},
                "center_lng": {"type": "number", "description": "中心经度, 默认 10"},
                "zoom": {"type": "integer", "description": "缩放级别, 默认 2"}
            },
            "required": ["title", "markers"]
        }
    },
    {
        "name": "list_maps",
        "description": "列出所有已生成的世界地图链接",
        "inputSchema": {"type": "object", "properties": {}}
    },
]

# ── 辅助函数 ──────────────────────────────────────────────

def geocode(location: str):
    for attempt in range(3):
        try:
            result = geolocator.geocode(location, timeout=10)
            if result:
                time.sleep(0.6)
                return (result.latitude, result.longitude)
        except:
            if attempt < 2:
                time.sleep(2 ** attempt)
    return None

def resolve_country(name: str) -> str:
    return COUNTRY_ALIASES.get(name.strip().lower(), name.strip())

def cleanup_maps():
    now = datetime.now()
    for f in STATIC_DIR.glob("*.html"):
        if datetime.fromtimestamp(f.stat().st_mtime) < now - timedelta(hours=24):
            f.unlink()

# ── 工具实现 ──────────────────────────────────────────────

async def do_fetch(url: str) -> str:
    if not url:
        return "❌ 请提供 URL"
    headers = {}
    if JINA_API_KEY:
        headers["Authorization"] = f"Bearer {JINA_API_KEY}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"https://r.jina.ai/{url}", headers=headers)
            resp.raise_for_status()
            text = resp.text
            if len(text) > 15000:
                text = text[:15000] + "\n\n... (内容过长，已截断)"
            return text
    except Exception as e:
        return f"❌ 抓取失败：{str(e)}"

def do_geocode(location: str) -> str:
    coords = geocode(location)
    if coords:
        return json.dumps({
            "location": location,
            "lat": round(coords[0], 5),
            "lng": round(coords[1], 5),
            "status": "success"
        }, ensure_ascii=False, indent=2)
    return json.dumps({
        "location": location,
        "status": "failed",
        "hint": "请用具体地名，如 'Paris, France'"
    }, ensure_ascii=False, indent=2)

def do_generate_map(params: dict) -> str:
    title = params.get("title", "Untitled Map")
    theme = params.get("theme", "dark")
    center_lat = float(params.get("center_lat", 25))
    center_lng = float(params.get("center_lng", 10))
    zoom = int(params.get("zoom", 2))

    try:
        marker_list = json.loads(params.get("markers", "[]"))
        route_list = json.loads(params.get("routes", "[]"))
        zone_list = json.loads(params.get("zones", "[]"))
    except json.JSONDecodeError as e:
        return f"❌ JSON 解析错误: {e}"

    tile = "CartoDB dark_matter" if theme == "dark" else "OpenStreetMap"

    m = folium.Map(
        location=[center_lat, center_lng],
        zoom_start=zoom,
        tiles=tile,
        control_scale=True,
        world_copy_jump=True,
    )
    plugins.Fullscreen().add_to(m)

    # 国家染色
    if zone_list and WORLD_GEOJSON.get("features"):
        zone_group = folium.FeatureGroup(name="影响区域")
        for z in zone_list:
            target = resolve_country(z.get("country", ""))
            color = z.get("color", "#ff1744")
            opacity = float(z.get("opacity", 0.2))
            for feat in WORLD_GEOJSON["features"]:
                props = feat.get("properties", {})
                candidates = [props.get(k, "") for k in ("name","name_long","sovereignt","admin","formal_en")]
                if target in candidates:
                    folium.GeoJson(
                        feat,
                        style_function=lambda x, c=color, o=opacity: {
                            "fillColor": c, "fillOpacity": o,
                            "color": c, "weight": 1.5, "opacity": 0.6,
                        },
                        tooltip=folium.Tooltip(target, sticky=False),
                    ).add_to(zone_group)
                    break
        zone_group.add_to(m)

    # 路线
    if route_list:
        route_group = folium.FeatureGroup(name="路线")
        for r in route_list:
            pts = r.get("points", [])
            if len(pts) >= 2:
                latlngs = [[p[0], p[1]] for p in pts]
                dash = "10, 10" if r.get("dashed") else None
                folium.PolyLine(
                    latlngs,
                    color=r.get("color", "#ff6d00"),
                    weight=r.get("weight", 3),
                    dash_array=dash,
                    opacity=0.8,
                    tooltip=r.get("name", ""),
                ).add_to(route_group)
                for i in range(len(latlngs) - 1):
                    mid = [(latlngs[i][0] + latlngs[i+1][0]) / 2,
                           (latlngs[i][1] + latlngs[i+1][1]) / 2]
                    folium.RegularPolygonMarker(
                        mid, number_of_sides=3, radius=5,
                        color=r.get("color", "#ff6d00"), fill=True, fill_opacity=0.8,
                    ).add_to(route_group)
        route_group.add_to(m)

    # 标记点
    geo_count = 0
    if marker_list:
        marker_group = folium.FeatureGroup(name="事件标记")
        for mk in marker_list:
            if "lat" in mk and "lng" in mk:
                lat, lng = mk["lat"], mk["lng"]
            elif "location" in mk:
                coords = geocode(mk["location"])
                if coords:
                    lat, lng = coords
                    geo_count += 1
                else:
                    continue
            else:
                continue

            sev = mk.get("severity", "watch")
            color, radius = SEVERITY.get(sev, ("#00e5ff", 5))

            folium.CircleMarker(
                [lat, lng], radius=radius * 3, color=color,
                weight=0, fill=True, fill_opacity=0.15,
            ).add_to(marker_group)

            labels = {"critical":"一级","major":"二级","minor":"三级","watch":"观察哨"}
            popup = (
                f"<b style='font-size:15px;'>{mk.get('name','')}</b><br>"
                f"<span style='color:#999;font-size:11px;'>"
                f"📅 {mk.get('date','?')} &nbsp;| "
                f"<span style='color:{color};font-weight:700;'>{labels.get(sev,'?')}</span></span>"
                f"<p style='font-size:12px;color:#bbb;margin:4px 0 0;'>{mk.get('description','')}</p>"
            )
            folium.CircleMarker(
                [lat, lng], radius=radius, color="white", weight=2.5,
                fill=True, fill_color=color, fill_opacity=0.95,
                popup=folium.Popup(popup, max_width=260),
                tooltip=mk.get("name", ""),
            ).add_to(marker_group)
        marker_group.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    m.get_root().html.add_child(folium.Element(f"""
    <div style="position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:9999;
                background:rgba(10,10,20,0.85);backdrop-filter:blur(12px);color:#fff;
                padding:8px 20px;border-radius:20px;font-size:15px;font-weight:700;
                border:1px solid rgba(255,255,255,0.15);pointer-events:none;">
      {title}
    </div>"""))

    map_id = uuid.uuid4().hex[:8]
    filename = f"map_{map_id}.html"
    filepath = STATIC_DIR / filename
    m.save(str(filepath))
    cleanup_maps()

    url = f"{PUBLIC_URL}/maps/{filename}" if PUBLIC_URL else f"/maps/{filename}"

    return "\n".join([
        "✅ 地图已生成！\n",
        f"🔗 {url}",
        "",
        f"📍 标记: {len(marker_list)} | 🛤️ 路线: {len(route_list)} | 🗺️ 染色: {len(zone_list)}",
        f"🔍 自动地理编码: {geo_count} 个地名",
        "",
        "💡 浏览器打开链接即可交互",
    ])

def do_list_maps() -> str:
    files = sorted(STATIC_DIR.glob("*.html"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return "📭 暂无地图"
    lines = ["📂 已生成的地图\n"]
    for f in files[:15]:
        size = f.stat().st_size / 1024
        mt = datetime.fromtimestamp(f.stat().st_mtime).strftime("%m-%d %H:%M")
        link = f"{PUBLIC_URL}/maps/{f.name}" if PUBLIC_URL else f"/maps/{f.name}"
        lines.append(f"• [{f.name}]({link}) ({size:.0f}KB · {mt})")
    return "\n".join(lines)

# ── MCP JSON-RPC 处理 ─────────────────────────────────────

async def mcp_handler(request: Request):
    # GET 请求：返回服务信息
    if request.method == "GET":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": None,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "combo-toolbox", "version": "2.0.0"}
            }
        })

    # POST 请求
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": "Parse error"}
        }, status_code=400)

    method = body.get("method", "")
    msg_id = body.get("id", 0)

    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "combo-toolbox", "version": "2.0.0"}
            }
        })

    if method == "notifications/initialized":
        return Response(status_code=200)

    if method == "tools/list":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": TOOLS}
        })

    if method == "tools/call":
        tool_name = body["params"]["name"]
        args = body["params"].get("arguments", {})

        if tool_name == "fetch_url":
            result = await do_fetch(args.get("url", ""))
        elif tool_name == "geocode_location":
            result = do_geocode(args.get("location", ""))
        elif tool_name == "generate_world_map":
            result = do_generate_map(args)
        elif tool_name == "list_maps":
            result = do_list_maps()
        else:
            result = f"❌ 未知工具: {tool_name}"

        return JSONResponse({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"content": [{"type": "text", "text": result}]}
        })

    return JSONResponse({
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": "Method not found"}
    })

# ── 首页 ──────────────────────────────────────────────────

async def home(request):
    return HTMLResponse("""
    <html><head><meta charset="UTF-8"><title>MCP 工具箱</title>
    <style>
      body { font-family:system-ui; background:#0a0a14; color:#e0e0e0;
             display:flex; align-items:center; justify-content:center; min-height:100vh; margin:0; }
      .card { text-align:center; padding:40px; background:rgba(255,255,255,0.04);
              border-radius:20px; border:1px solid rgba(255,255,255,0.08); }
      h1 { font-size:28px; } .badge { display:inline-block; background:#4caf5022;
              color:#4caf50; padding:4px 14px; border-radius:10px; font-size:13px; margin:6px; }
    </style></head>
    <body><div class="card">
      <h1>🛠️ MCP 全能工具箱</h1>
      <p><span class="badge">🌐 网页抓取</span><span class="badge">🗺️ 世界地图</span></p>
      <p>端点: /mcp</p>
    </div></body></html>
    """)

# ── 应用 ──────────────────────────────────────────────────

app = Starlette(routes=[
    Route("/", home, methods=["GET"]),
    Route("/mcp", mcp_handler, methods=["POST", "GET"]),
])
app.mount("/maps", StaticFiles(directory=str(STATIC_DIR)))

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"🚀 工具箱启动 | 端口 {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)   
