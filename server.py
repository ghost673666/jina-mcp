import os
from mcp.server.fastmcp import FastMCP
import httpx

mcp = FastMCP("Jina Web Fetcher")
JINA_API_KEY = os.getenv("JINA_API_KEY", "")

@mcp.tool()
async def fetch_url(url: str) -> str:
    """
    抓取任意网页，返回干净的 Markdown 格式文本。
    适用场景：写作素材收集、文章风格分析、观点提取。
    """
    if not url:
        return "错误：请提供要抓取的网页 URL"
    headers = {}
    if JINA_API_KEY:
        headers["Authorization"] = f"Bearer {JINA_API_KEY}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"https://r.jina.ai/{url}", headers=headers)
            response.raise_for_status()
            text = response.text
            max_chars = 15000
            if len(text) > max_chars:
                text = text[:max_chars] + f"\n\n... (内容过长，已截断。原文共 {len(text)} 字符)"
            return text
    except httpx.HTTPStatusError as e:
        return f"抓取失败：HTTP {e.response.status_code}"
    except httpx.TimeoutException:
        return "抓取失败：请求超时，请稍后重试。"
    except Exception as e:
        return f"抓取失败：{str(e)}"

@mcp.tool()
async def search_and_fetch(query: str, num_results: int = 3) -> str:
    """
    搜索网页并返回内容。用于「搜一下XX话题的最新文章」。
    """
    if not JINA_API_KEY:
        return "错误：搜索功能需要 JINA_API_KEY"
    num_results = min(num_results, 5)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                "https://s.jina.ai/",
                params={"q": query, "num": num_results},
                headers={"Authorization": f"Bearer {JINA_API_KEY}"}
            )
            resp.raise_for_status()
            return resp.text
    except Exception as e:
        return f"搜索失败：{str(e)}"

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(mcp._mcp_server, host="0.0.0.0", port=port)
