import aiohttp
import asyncio
import json
import re


async def fetch_stream(url, headers, data):
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as response:
            # 检查是否请求成功
            if response.status == 200:
                # 逐块读取响应
                async for chunk, _ in response.content.iter_chunks():
                    if chunk:
                        chunk = chunk.decode("utf-8")
                        print("接收到的数据块:", chunk)

                        # 尝试匹配 "<JSON_BEGIN>" 和 "<JSON_END>"
                        if "<JSON_BEGIN>" in chunk:
                            match = re.search(r"<JSON_BEGIN>(.*?)<JSON_END>", chunk)
                            if match:
                                json_str = match.group(1)
                                try:
                                    # 将 JSON 字符串转换为 Python 字典
                                    json_data = json.loads(json_str)
                                    print("提取的 JSON 数据:", json_data)
                                except json.JSONDecodeError as e:
                                    print("JSON 解析错误:", e)
            else:
                print(f"请求失败，状态码：{response.status}")


# 使用 asyncio 运行异步函数
url = "http://127.0.0.1:8024/chat/stream"
headers = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}
data = {"messages": [{"role": "user", "content": "你好呀"}]}

asyncio.run(fetch_stream(url, headers, data))
