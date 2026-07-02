import json
import os
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

async def extract_information_json(text: str, json_schema: str, model: str | None = None) -> dict:
    """强制 DeepSeek 输出纯 JSON 格式的数据（供一次性模式 + Planner.extract 复用）"""
    client = AsyncOpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com"
    )

    system_prompt = (
        "你是一个专业的数据结构化工程师。你的任务是从提供的网页抓取文本中提取信息，"
        "并【严格按照用户要求的 JSON 结构】输出。不要包含任何 markdown 标记，不要解释，只输出合法的 JSON 字符串。"
    )
    
    # 获取默认模型
    target_model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    
    try:
        response = await client.chat.completions.create(
            model=target_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"【网页纯文本】:\n{text}\n\n【我需要的 JSON 结构与数据要求】:\n{json_schema}"}
            ],
            temperature=0.0,
            max_tokens=8192  # 防止长表格 JSON 被截断成非法 JSON
        )
        content = response.choices[0].message.content or ""
        if getattr(response.choices[0], "finish_reason", "") == "length":
            return {"error": "抽取结果超出输出上限被截断，请缩小 schema 或分块抽取"}
        return json.loads(content)
    except json.JSONDecodeError:
        return {"error": "AI 返回的数据不是合法的 JSON 格式"}
    except Exception as e:
        return {"error": f"API 调用失败: {e}"}
