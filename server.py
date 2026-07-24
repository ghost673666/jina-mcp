import os
import json
import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
import uvicorn

JINA_API_KEY = os.getenv("JINA_API_KEY", "")

async def mcp_handler(request: Request):
    body = await request.json()
    method = body.get("method", "")
    msg_id = body.get("id", 0)

    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "jina-fetcher", "version": "1.0.0"}
            }
        })

    if method == "notifications/initialized":
        return Response(status_code=200)

    if method == "tools/list":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": [{
                    "name": "fetch_url",
                    "description": "抓取任意网页，返回干净的 Markdown 格式文本。适用场景：写作素材收集、文章风格分析、观点提取。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "要抓取的网页完整地址"}
                        },
                        "required": ["url"]
                    }
                }]
            }
        })

    if method == "tools/call":
        tool_name = body["params"]["name"]
        args = body["params"].get("arguments", {})
        url = args.get("url", "")
        result = await do_fetch(url)
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

async def do_fetch(url: str) -> str:
    if not url:
        return "错误：请提供要抓取的网页 URL"
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
        return f"抓取失败：{str(e)}"

app = Starlette(routes=[
    Route("/mcp", mcp_handler, methods=["POST", "GET"]),
    Route("/", mcp_handler, methods=["POST", "GET"]),
])

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
