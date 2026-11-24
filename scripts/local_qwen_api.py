"""Serve local Qwen3.5 text generation on a single CUDA device."""

import argparse
from pathlib import Path
from threading import Lock
from time import time
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


MODEL_ID = "Qwen3.5-9B"
DEFAULT_CONTEXT_TOKENS = 8192


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class CompletionRequest(BaseModel):
    model: Literal["Qwen3.5-9B"]
    messages: list[Message] = Field(min_length=1)
    max_tokens: int = Field(default=512, ge=1, le=2048)
    temperature: float = Field(default=0.7, ge=0, le=2)
    stream: Literal[False] = False


def create_app(model, tokenizer, context_tokens=DEFAULT_CONTEXT_TOKENS):
    import torch

    app = FastAPI()
    generation_lock = Lock()

    @app.get("/v1/models")
    def models():
        return {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "local"}]}

    @app.post("/v1/chat/completions")
    def chat(request: CompletionRequest):
        with generation_lock, torch.inference_mode():
            inputs = tokenizer.apply_chat_template(
                [message.model_dump() for message in request.messages],
                tokenize=True, return_dict=True, return_tensors="pt",
                add_generation_prompt=True, enable_thinking=False,
            )
            prompt_tokens = inputs["input_ids"].shape[-1]
            available = context_tokens - prompt_tokens
            if available <= 0:
                raise HTTPException(400, f"输入已有 {prompt_tokens} 个 token，超出 {context_tokens} token 上下文")
            max_new_tokens = min(request.max_tokens, available)
            inputs = inputs.to("cuda:0")
            sampling = request.temperature > 0
            options = {"temperature": request.temperature} if sampling else {}
            output = model.generate(
                **inputs, max_new_tokens=max_new_tokens, do_sample=sampling, **options,
            )
            generated = output[0, prompt_tokens:]
            content = tokenizer.decode(generated, skip_special_tokens=True).strip()
            completion_tokens = len(generated)
            finish = "length" if completion_tokens == max_new_tokens else "stop"

        return {
            "id": f"chatcmpl-{uuid4().hex}",
            "object": "chat.completion",
            "created": int(time()),
            "model": MODEL_ID,
            "choices": [{
                "index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish,
            }],
            "usage": {
                "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

    return app


def main():
    import torch
    import uvicorn
    from transformers import AutoConfig, AutoModelForImageTextToText, AutoTokenizer

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8909)
    parser.add_argument("--context-tokens", type=int, default=DEFAULT_CONTEXT_TOKENS)
    args = parser.parse_args()
    if not torch.cuda.is_available():
        parser.error("CUDA 不可用，请在能访问 NVIDIA 显卡的服务器终端运行")
    model_dir = args.model_dir.resolve()
    config = AutoConfig.from_pretrained(model_dir, local_files_only=True)
    if not 1024 <= args.context_tokens <= config.text_config.max_position_embeddings:
        parser.error(f"--context-tokens 必须介于 1024 和 {config.text_config.max_position_embeddings} 之间")
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    model = AutoModelForImageTextToText.from_pretrained(
        model_dir, config=config, local_files_only=True, dtype=torch.bfloat16,
        device_map={"": 0}, attn_implementation="sdpa",
    ).eval()
    uvicorn.run(create_app(model, tokenizer, args.context_tokens), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
