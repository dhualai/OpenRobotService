import logging
from typing import List, Dict, Optional, Union, AsyncGenerator
from openai import AsyncOpenAI
from app.core.config import settings
from app.utils.data_utils import sanitize_input
import httpx
import json

logger = logging.getLogger(__name__)


class ModelService:
    _client: Optional[AsyncOpenAI] = None
    _mqtt_client = None
    _mqtt_connected = False

    @classmethod
    def get_client(cls) -> AsyncOpenAI:
        if cls._client is None:
            if not settings.LLM_API_KEY:
                raise ValueError("LLM API密钥未配置，请在.env中设置 LLM_API_KEY")
            cls._client = AsyncOpenAI(
                api_key=settings.LLM_API_KEY,
                base_url=settings.LLM_API_URL.rsplit('/chat/completions', 1)[0]
            )
        return cls._client

    @classmethod
    def get_mqtt_client(cls):
        if cls._mqtt_client is None:
            cls._initialize_mqtt()
        return cls._mqtt_client

    @classmethod
    def _initialize_mqtt(cls):
        import paho.mqtt.client as mqtt
        
        def on_connect(client, userdata, flags, rc, properties=None):
            if rc == 0:
                client.subscribe(settings.MQTT_TOPIC_RESPONSE)
                cls._mqtt_connected = True
                logger.info("MQTT连接成功")
            else:
                cls._mqtt_connected = False
                logger.error(f"MQTT连接失败，错误码: {rc}")
        
        cls._mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        cls._mqtt_client.username_pw_set(settings.MQTT_USER, settings.MQTT_PASSWORD)
        cls._mqtt_client.on_connect = on_connect
        
        try:
            cls._mqtt_client.connect(settings.MQTT_BROKER, settings.MQTT_PORT, 60)
            cls._mqtt_client.loop_start()
            import time
            time.sleep(1)
        except Exception as e:
            logger.error(f"初始化MQTT客户端失败: {str(e)}")
            cls._mqtt_client = None
            cls._mqtt_connected = False

    @classmethod
    def close_mqtt(cls):
        if cls._mqtt_client:
            try:
                cls._mqtt_client.loop_stop()
                cls._mqtt_client.disconnect()
                logger.info("MQTT连接已关闭")
            except Exception as e:
                logger.error(f"关闭MQTT连接失败: {str(e)}")
            finally:
                cls._mqtt_client = None
                cls._mqtt_connected = False

    @staticmethod
    def _build_messages(
        user_question: str,
        system_prompt: str,
        conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> List[Dict[str, str]]:
        user_question = sanitize_input(user_question)
        system_prompt = sanitize_input(system_prompt)

        messages = [
            {"role": "system", "content": system_prompt}
        ]

        if conversation_history:
            for msg in conversation_history:
                if "role" in msg and "content" in msg:
                    messages.append({
                        "role": msg["role"],
                        "content": sanitize_input(msg["content"])
                    })

        messages.append({
            "role": "user",
            "content": user_question
        })

        return messages

    @staticmethod
    async def generate_answer(
        user_question: str,
        system_prompt: str = "你是一个有用的AI助手。",
        conversation_history: Optional[List[Dict[str, str]]] = None,
        action: str = "",
        selected_id: str = "",
        current_step: str = ""
    ) -> Union[Optional[str], AsyncGenerator[Dict[str, any], None]]:
        if settings.AI_SERVICE_PROVIDER == "custom":
            if settings.LLM_STREAM:
                return ModelService._generate_custom_stream_mqtt(user_question, conversation_history, action, selected_id, current_step, system_prompt)
            else:
                return await ModelService._generate_custom_non_stream(user_question, conversation_history, action, selected_id, current_step)
        else:
            messages = ModelService._build_messages(user_question, system_prompt, conversation_history)
            if settings.LLM_STREAM:
                return ModelService._generate_stream(messages)
            else:
                return await ModelService._generate_non_stream(messages)

    @staticmethod
    async def _generate_non_stream(messages: List[Dict[str, str]]) -> Optional[str]:
        try:
            client = ModelService.get_client()
            response = await client.chat.completions.create(
                model=settings.LLM_MODEL_NAME,
                messages=messages,
                temperature=settings.LLM_TEMPERATURE,
                stream=False
            )

            if response.choices and response.choices[0].message.content:
                return response.choices[0].message.content
            return None

        except Exception as e:
            logger.error(f"LLM API请求失败: {str(e)}", exc_info=True)
            return None

    @staticmethod
    async def _generate_stream(messages: List[Dict[str, str]]) -> AsyncGenerator[Dict[str, any], None]:
        try:
            client = ModelService.get_client()
            response = await client.chat.completions.create(
                model=settings.LLM_MODEL_NAME,
                messages=messages,
                temperature=settings.LLM_TEMPERATURE,
                stream=True
            )

            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield {"type": "content", "data": chunk.choices[0].delta.content}

        except Exception as e:
            logger.error(f"LLM API流式请求失败: {str(e)}", exc_info=True)
            yield {"type": "content", "data": f"错误: {str(e)}"}

    @staticmethod
    async def _generate_custom_non_stream(user_question: str, conversation_history: Optional[List[Dict[str, str]]] = None, action: str = "", selected_id: str = "", current_step: str = "") -> Optional[Dict[str, str]]:
        try:
            import uuid

            request_id = str(uuid.uuid4())

            payload = {
                "question": user_question,
                "action": action,
                "selected_id": selected_id,
                "current_step": current_step,
                "history": conversation_history if conversation_history else []
            }

            mqtt_message = {
                "request_id": request_id,
                "data": payload
            }

            response_data = None
            response_received = False

            def on_message(client, userdata, msg, properties=None):
                nonlocal response_data, response_received
                try:
                    message_content = msg.payload.decode()
                    response_msg = json.loads(message_content)

                    if response_msg.get("request_id") == request_id:
                        if response_msg.get("code") == 200:
                            response_data = response_msg.get("data", {})
                        response_received = True
                except Exception as e:
                    logger.error(f"处理MQTT响应异常: {str(e)}", exc_info=True)
                    response_received = True

            client = ModelService.get_mqtt_client()
            if not client or not ModelService._mqtt_connected:
                logger.error("MQTT客户端未初始化或未连接")
                return None

            client.on_message = on_message

            client.publish(settings.MQTT_TOPIC_REQUEST, json.dumps(mqtt_message, ensure_ascii=False))

            import time
            start_time = time.time()
            while not response_received and time.time() - start_time < 30:
                time.sleep(0.1)

            if response_data and "answer" in response_data:
                answer = response_data["answer"]
                result = {
                    "answer": "",
                    "action": "",
                    "selected_id": "",
                    "current_step": ""
                }
                
                if "---" in answer:
                    parts = answer.split("---")
                    if parts[0].strip():
                        try:
                            metadata = json.loads(parts[0].strip())
                            logger.info(f"收到元数据: {metadata}")
                            result["action"] = metadata.get("action", "")
                            result["selected_id"] = metadata.get("selected_id", "")
                            result["current_step"] = metadata.get("current_step", "")
                        except json.JSONDecodeError:
                            logger.error(f"解析JSON元数据失败: {parts[0].strip()}")
                    if len(parts) > 1:
                        result["answer"] = "".join(parts[1:]).strip()
                        return result
                else:
                    lines = answer.splitlines()
                    if lines:
                        first_line = lines[0].strip()
                        if first_line and (first_line.startswith("{") and first_line.endswith("}")):
                            try:
                                metadata = json.loads(first_line)
                                logger.info(f"收到元数据: {metadata}")
                                result["action"] = metadata.get("action", "")
                                result["selected_id"] = metadata.get("selected_id", "")
                                result["current_step"] = metadata.get("current_step", "")
                            except json.JSONDecodeError:
                                logger.error(f"解析JSON元数据失败: {first_line}")

                        content_started = False
                        answer_lines = []
                        for line in lines[1:]:
                            line_stripped = line.strip()
                            if not content_started:
                                if line_stripped:
                                    content_started = True
                                    answer_lines.append(line)
                            else:
                                answer_lines.append(line)

                        if answer_lines:
                            result["answer"] = "\n".join(answer_lines).strip()
                        else:
                            result["answer"] = answer
                        return result
                    result["answer"] = answer
                    return result
            else:
                logger.error("未收到MQTT响应或响应格式错误")
                return None

        except Exception as e:
            logger.error(f"MQTT请求异常: {str(e)}", exc_info=True)
            return None

    @staticmethod
    async def _generate_custom_stream(user_question: str, conversation_history: Optional[List[Dict[str, str]]] = None, action: str = "", selected_id: str = "", current_step: str = "") -> AsyncGenerator[Dict[str, any], None]:
        try:
            payload = {
                "question": user_question,
                "action": action,
                "selected_id": selected_id,
                "current_step": current_step,
                "history": conversation_history if conversation_history else []
            }

            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{settings.CUSTOM_AI_BASE_URL}{settings.CUSTOM_AI_API_PATH}",
                    json=payload,
                    timeout=30.0
                ) as response:
                    if response.status_code == 200:
                        full_response = ""
                        async for chunk in response.aiter_text():
                            full_response += chunk
                        
                        if "---" in full_response:
                            parts = full_response.split("---")
                            if parts[0].strip():
                                try:
                                    metadata = json.loads(parts[0].strip())
                                    yield {"type": "metadata", "data": metadata}
                                except json.JSONDecodeError:
                                    logger.error(f"解析JSON元数据失败: {parts[0].strip()}")
                            if len(parts) > 1:
                                for part in parts[1:]:
                                    if part.strip():
                                        yield {"type": "content", "data": part.strip()}
                        else:
                            lines = full_response.splitlines()
                            if lines:
                                first_line = lines[0].strip()
                                if first_line and (first_line.startswith("{") and first_line.endswith("}")):
                                    try:
                                        metadata = json.loads(first_line)
                                        yield {"type": "metadata", "data": metadata}
                                    except json.JSONDecodeError:
                                        logger.error(f"解析JSON元数据失败: {first_line}")
                                
                                content_started = False
                                answer_lines = []
                                for line in lines[1:]:
                                    line_stripped = line.strip()
                                    if not content_started:
                                        if line_stripped:
                                            content_started = True
                                            answer_lines.append(line)
                                    else:
                                        answer_lines.append(line)
                                
                                if answer_lines:
                                    full_answer = "\n".join(answer_lines).strip()
                                    if full_answer:
                                        yield {"type": "content", "data": full_answer}
                            else:
                                yield {"type": "content", "data": ""}
                    else:
                        logger.error(f"自定义AI服务流式请求失败: {response.status_code}")
                        yield {"type": "content", "data": f"错误: 自定义AI服务返回状态码 {response.status_code}"}

        except Exception as e:
            logger.error(f"自定义AI服务流式请求异常: {str(e)}", exc_info=True)
            yield {"type": "content", "data": f"错误: {str(e)}"}

    @staticmethod
    async def _generate_custom_stream_mqtt(user_question: str, conversation_history: Optional[List[Dict[str, str]]] = None, action: str = "", selected_id: str = "", current_step: str = "", system_prompt: str = "你是一个有用的AI助手。") -> AsyncGenerator[Dict[str, any], None]:
        #try:
        import asyncio
        import uuid
        import paho.mqtt.client as mqtt

        request_id = str(uuid.uuid4())

        payload = {
            "question": user_question,
            "action": action,
            "selected_id": selected_id,
            "current_step": current_step,
            "history": conversation_history if conversation_history else [],
            "system_prompt": system_prompt
        }

        mqtt_message = {
            "request_id": request_id,
            "data": payload,
            "stream": True
        }

        chunk_contents = []
        final_answer = None
        response_received = False
        max_wait_time = 30

        message_queue = asyncio.Queue()

        main_loop = asyncio.get_event_loop()

        def on_message(client, userdata, msg):
            nonlocal chunk_contents, final_answer, response_received
            try:
                message_content = msg.payload.decode()
                response_msg = json.loads(message_content)

                msg_type = response_msg.get("type", "")

                if msg_type == "start":
                    logger.info(f"收到流式回复开始消息: {response_msg.get('request_id', '')}")

                elif msg_type == "chunk":
                    data = response_msg.get("data", {})
                    content = data.get("content", "")
                    if content:
                        chunk_contents.append(content)
                        asyncio.run_coroutine_threadsafe(
                            message_queue.put({"type": "content", "data": content}),
                            main_loop
                        )

                elif msg_type == "end":
                    data = response_msg.get("data", {})
                    final_answer = data.get("final_text", "")
                    response_received = True
                    logger.info(f"收到流式回复结束消息，总块数: {data.get('total_chunks', 0)}")

            except Exception as e:
                logger.error(f"处理MQTT响应异常: {str(e)}", exc_info=True)

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.username_pw_set(settings.MQTT_USER, settings.MQTT_PASSWORD)
        client.on_message = on_message

        try:
            client.connect(settings.MQTT_BROKER, settings.MQTT_PORT, 60)
            client.loop_start()

            client.subscribe(settings.MQTT_TOPIC_RESPONSE)

            await asyncio.sleep(1)

            client.publish(settings.MQTT_TOPIC_REQUEST, json.dumps(mqtt_message, ensure_ascii=False))
            logger.info(f"已发送MQTT请求，request_id: {request_id}")

            start_time = asyncio.get_event_loop().time()
            while not response_received and asyncio.get_event_loop().time() - start_time < max_wait_time:
                try:
                    message = await asyncio.wait_for(message_queue.get(), timeout=0.1)

                    if message.get("type") == "content":
                        content = message.get("data", "")
                        if content:
                            yield {"type": "content", "data": content}

                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"处理队列消息异常: {str(e)}", exc_info=True)

            await asyncio.sleep(0.5)

            if not response_received:
                logger.error("未收到MQTT响应")
                yield {"type": "content", "data": "错误: 未收到AI服务响应"}

        finally:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception as e:
                logger.error(f"关闭MQTT连接失败: {str(e)}")

        #except Exception as e:
         #   logger.error(f"MQTT流式请求异常: {str(e)}", exc_info=True)
          #  yield {"type": "content", "data": f"错误: {str(e)}"}