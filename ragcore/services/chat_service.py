import openai
import time
import json
from config.config import API_KEY, BASE_URL, MODEL
from services.langsmith_service import langsmith_service
from utils.logger import logger

class ChatService:
    def __init__(self):
        try:
            self.client = openai.AsyncOpenAI(
                api_key=API_KEY,
                base_url=BASE_URL,
            )
            logger.info("OpenAI Async client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI client: {e}")
            raise
    
    async def chat_stream(self, messages):
        try:
            logger.info(f"Received chat request with {len(messages)} messages")
            
            # LangSmith追踪
            with langsmith_service.trace_context(
                name="chat_stream",
                metadata={
                    "service": "ChatService",
                    "message_count": len(messages),
                    "model": MODEL
                }
            ):
                # 记录开始时间
                start_time = time.time()
            
                # 调用OpenAI API (流式)
                response = await self.client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    stream=True,  # 流式响应
                )
                
                # 收集响应内容
                full_content = ""
                usage = None
                chunk_count = 0
                
                async for chunk in response:
                    chunk_count += 1
                    if chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        full_content += content
                        # 发送流式数据
                        yield json.dumps({
                            "type": "content",
                            "content": content
                        }) + "\n"
                    # 检查是否包含usage字段（在最后一个chunk中）
                    if hasattr(chunk, 'usage') and chunk.usage:
                        usage = chunk.usage
                
                # 计算思考时间
                thinking_time = round(time.time() - start_time, 2)
                
                # 获取token消耗数据
                if usage:
                    input_tokens = usage.prompt_tokens
                    output_tokens = usage.completion_tokens
                    total_tokens = usage.total_tokens
                    logger.info(f"API usage: prompt_tokens={input_tokens}, completion_tokens={output_tokens}, total_tokens={total_tokens}")
                else:
                    # 备用方案：估算token消耗
                    input_tokens = sum(len(msg["content"].split()) for msg in messages)
                    output_tokens = len(full_content.split())
                    total_tokens = input_tokens + output_tokens
                    logger.warning("API usage not available, using estimated values")
                
                # 发送结束信息
                yield json.dumps({
                    "type": "metadata",
                    "thinking_time": thinking_time,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": total_tokens
                }) + "\n"
                
                logger.info(f"Chat completed in {thinking_time}s with {chunk_count} chunks")
                
        except Exception as e:
            logger.error(f"Error in chat stream: {e}")
            yield json.dumps({
                "type": "error",
                "message": str(e)
            }) + "\n"
