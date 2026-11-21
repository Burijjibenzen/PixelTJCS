"""LLM 客户端 —— 全系统所有大模型调用的唯一出口

设计说明（为什么 M1 暂不直接用 LangChain/LangGraph）:
    同济网关上的 GLM 是"推理模型"，返回结构中有独立的 reasoning 字段，
    且 max_tokens 不足时正文 content 为 null（我们实测踩过）。
    这类兼容性必须在传输层手工处理，直接套框架反而把问题藏进黑盒。
    本模块刻意保持接口极简（chat / chat_json / embed），
    conversation.py 的编排接口已按"未来可平移为 LangGraph 节点"设计，
    迁移路线见 docs/CODE_GUIDE.md §8。

离线兜底: 网关不可用/未配 key 时，各方法返回 None / []，
上层（conversation.py）会降级为预设台词，保证演示永不中断。
"""

import json
import re
from threading import BoundedSemaphore
from time import monotonic
from typing import List, Optional, Type, TypeVar

import httpx
from openai import OpenAI
from pydantic import BaseModel

from backend.config import LLM as CFG
from backend.logger import get_logger

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    """对 OpenAI 兼容网关的薄封装。全系统共享一个实例。"""

    def __init__(self, cfg=CFG):
        if cfg.max_concurrent_requests < 1:
            raise ValueError("LLM_MAX_CONCURRENT_REQUESTS 必须大于零")
        self.cfg = cfg
        self._request_slots = BoundedSemaphore(cfg.max_concurrent_requests)
        # M3: 未配置 key 时立即启用已有离线兜底，避免 SDK 初始化失败或长时间网络重试。
        self._client = None
        if not cfg.api_key:
            return
        # trust_env=False: 无视 HTTP_PROXY 等代理环境变量。
        # 教训: 校园网关对代理出口 IP 有风控，走 Clash 会被拒
        # （403 "当前网络环境不支持"），必须直连。
        # timeout: 推理模型先思考再回答，单次可能超过 1 分钟，给足 180s
        self._client = OpenAI(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            http_client=httpx.Client(
                trust_env=False,
                timeout=httpx.Timeout(180.0, connect=15.0),
            ),
        )


    # ------------------------------------------------------------------
    # 基础对话
    # ------------------------------------------------------------------
    def chat(
        self,
        system: str,
        user: str,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        timeout: Optional[float] = None,
    ) -> Optional[str]:
        """单轮对话，返回正文文本；失败返回 None（上层降级）。

        内部处理推理模型的两个坑:
          1) content 为 null 且 finish_reason=length → token 不够，加倍重试一次
          2) 网络异常 → 记录日志后返回 None
        """
        # M3: 离线模式复用上层预设台词/记忆兜底。
        if self._client is None:
            return None
        model = model or self.cfg.chat_model
        max_tokens = max_tokens or self.cfg.max_tokens
        temperature = self.cfg.temperature if temperature is None else temperature
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        deadline = monotonic() + timeout if timeout is not None else None
        for attempt in range(2):  # 最多重试一次
            remaining = None if deadline is None else deadline - monotonic()
            if (remaining is not None and remaining <= 0) or not self._request_slots.acquire(timeout=remaining):
                log.warning("LLM 请求在并发队列中达到时限")
                return None
            try:
                client = self._client
                if deadline is not None:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        return None
                    client = client.with_options(timeout=remaining, max_retries=0)
                resp = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                choice = resp.choices[0]
                content = choice.message.content
                if content:  # 正常拿到正文
                    return content.strip()
                # 正文为空: 推理模型思考没写完就被截断了 → 加倍 token 重试
                if choice.finish_reason == "length" and attempt == 0:
                    max_tokens *= 2
                    log.warning("推理被截断(content=null)，max_tokens 加倍重试")
                    continue
                log.warning("LLM 返回空正文 (finish_reason=%s)", choice.finish_reason)
                return None
            except Exception as e:  # noqa: BLE001 网关异常种类不可枚举
                log.error("LLM chat 调用失败: %s", e)
                return None
            finally:
                self._request_slots.release()
        return None

    # ------------------------------------------------------------------
    # 结构化输出: 强制模型返回符合 Pydantic schema 的 JSON
    # ------------------------------------------------------------------
    def chat_json(
        self,
        system: str,
        user: str,
        schema: Type[T],
        model: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Optional[T]:
        """让模型输出 JSON 并解析为 Pydantic 对象；失败返回 None。

        说明: 未用 function-calling（部分聚合网关不支持），而是
        "提示词要求 JSON + 正则抽取 + Pydantic 校验 + 失败带错误重试一次"，
        对任意 OpenAI 兼容模型都稳。
        """
        model = model or self.cfg.think_model
        schema_hint = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        sys_prompt = (
            f"{system}\n\n【输出要求】只输出一个 JSON 对象，不要输出任何其他文字、"
            f"不要用 markdown 代码块。字段必须符合以下 schema:\n{schema_hint}"
        )
        options = {'timeout':timeout} if timeout is not None else {}
        raw = self.chat(sys_prompt, user, model=model, temperature=0.3, **options)
        if raw is None:
            return None
        parsed = self._try_parse_json(raw, schema)
        if parsed is not None:
            return parsed
        # 校验失败 → 把错误信息喂回去重试一次
        log.warning("JSON 校验失败，带错误重试一次")
        retry_user = (
            f"{user}\n\n你上一次的输出无法通过校验，请严格按 schema 重新输出 JSON。\n"
            f"上次输出: {raw[:500]}"
        )
        raw = self.chat(sys_prompt, retry_user, model=model, temperature=0.2, **options)
        if raw is None:
            return None
        return self._try_parse_json(raw, schema)

    @staticmethod
    def _try_parse_json(raw: str, schema: Type[T]) -> Optional[T]:
        """从模型输出中抽取 JSON 并校验（容忍 ```json 包裹/前后废话）。"""
        match = re.search(r"\{.*\}", raw, re.DOTALL)  # 贪婪取最外层大括号
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            return schema.model_validate(data)
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # 文本向量（记忆语义检索用）
    # ------------------------------------------------------------------
    def embed(self, texts: List[str]) -> Optional[List[List[float]]]:
        """批量文本 → 向量列表；失败返回 None。"""
        if not texts:
            return []
        try:
            # M3: 无 key 时使用原有关键词检索降级。
            if self._client is None:
                return None
            with self._request_slots:
                resp = self._client.embeddings.create(
                    model=self.cfg.embed_model, input=texts
                )
            return [item.embedding for item in sorted(resp.data, key=lambda d: d.index)]
        except Exception as e:  # noqa: BLE001
            log.error("LLM embed 调用失败: %s", e)
            return None
