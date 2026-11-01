"""调用不同厂商的模型，统一成"给 system + user，拿回一个校验过的 JSON 对象"。

- anthropic：用官方 anthropic SDK，output_config.format 约束 JSON 结构。
- openai_compatible：用官方 openai SDK + base_url，适用于 OpenAI、Gemini、DeepSeek、
  Kimi、通义千问、智谱 GLM、豆包等提供 OpenAI 兼容接口的服务。
- mock：不联网，按 JSON Schema 造假数据，用来试跑流程和测试。
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
from dataclasses import dataclass, field
from typing import TypeVar

import anthropic
import openai
from pydantic import BaseModel, ValidationError

from .config import ProviderConfig
from .schemas import DESCRIPTIONS_EN
from .texts import TEXTS

T = TypeVar("T", bound=BaseModel)

# 结构化输出不支持的 JSON Schema 关键字，发送前去掉（pydantic 侧仍会校验）
_UNSUPPORTED_KEYS = {
    "default", "minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "multipleOf", "minItems", "maxItems", "uniqueItems", "pattern",
}


class LLMError(Exception):
    pass


@dataclass
class UsageTracker:
    by_provider: dict[str, dict[str, int]] = field(default_factory=dict)

    def add(self, provider: str, model: str, input_tokens: int | None, output_tokens: int | None) -> None:
        row = self.by_provider.setdefault(provider, {"model": model, "calls": 0, "input_tokens": 0, "output_tokens": 0})
        row["calls"] += 1
        row["input_tokens"] += input_tokens or 0
        row["output_tokens"] += output_tokens or 0

    def merge(self, saved: dict) -> None:
        for name, row in saved.items():
            mine = self.by_provider.setdefault(name, {"model": row.get("model", ""), "calls": 0, "input_tokens": 0, "output_tokens": 0})
            for k in ("calls", "input_tokens", "output_tokens"):
                mine[k] += row.get(k, 0)


def _english_descriptions(schema: dict) -> None:
    for node in [schema, *schema.get("$defs", {}).values()]:
        name = node.get("title", "")
        if "description" in node:
            node["description"] = DESCRIPTIONS_EN.get(name, node["description"])
        for field_name, prop in node.get("properties", {}).items():
            if "description" in prop:
                prop["description"] = DESCRIPTIONS_EN.get(f"{name}.{field_name}", prop["description"])


def strict_schema(model_cls: type[BaseModel], lang: str = "zh") -> dict:
    """pydantic 生成的 schema → 结构化输出能接受的 schema：每个对象都关掉额外字段、所有字段必填。
    lang = "en" 时字段说明换成英文。"""
    schema = copy.deepcopy(model_cls.model_json_schema())
    if lang == "en":
        _english_descriptions(schema)

    def walk(node):
        if isinstance(node, dict):
            for key in list(node):
                if key in _UNSUPPORTED_KEYS:
                    node.pop(key)
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return schema


def extract_json(text: str) -> dict:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("输出里没有找到 JSON 对象")
    return json.loads(text[start:end + 1])


class Provider:
    def __init__(self, cfg: ProviderConfig, tracker: UsageTracker):
        self.cfg = cfg
        self.tracker = tracker
        self.semaphore = asyncio.Semaphore(cfg.concurrency)

    @property
    def name(self) -> str:
        return self.cfg.name

    async def _complete(self, system: str, user: str, schema: dict, schema_name: str, lang: str) -> str:
        raise NotImplementedError

    async def structured(self, system: str, user: str, model_cls: type[T], retries: int = 2, lang: str = "zh") -> T:
        schema = strict_schema(model_cls, lang)
        prompt = user
        last_error = ""
        for attempt in range(retries + 1):
            if last_error:
                prompt = f"{user}\n\n---\n" + TEXTS[lang]["retry"].format(error=last_error[:800])
            async with self.semaphore:
                text = await self._complete(system, prompt, schema, model_cls.__name__, lang)
            try:
                return model_cls.model_validate(extract_json(text))
            except (ValueError, ValidationError) as e:
                last_error = str(e)
        raise LLMError(f"{self.name} 连续 {retries + 1} 次没有输出合格的 {model_cls.__name__}：{last_error[:300]}")


class AnthropicProvider(Provider):
    def __init__(self, cfg: ProviderConfig, tracker: UsageTracker):
        super().__init__(cfg, tracker)
        key = cfg.api_key()
        if cfg.api_key_env and not key:
            raise LLMError(f"{cfg.name}：环境变量 {cfg.api_key_env} 没有设置")
        kwargs = {"max_retries": 4}
        if key:
            kwargs["api_key"] = key
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        self.client = anthropic.AsyncAnthropic(**kwargs)

    async def _complete(self, system: str, user: str, schema: dict, schema_name: str, lang: str) -> str:
        output_config: dict = {"format": {"type": "json_schema", "schema": schema}}
        if self.cfg.effort:
            output_config["effort"] = self.cfg.effort
        request = dict(
            model=self.cfg.model,
            max_tokens=self.cfg.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
        )
        try:
            if self.cfg.fallbacks and self.cfg.fallbacks != "off":
                # 被安全分类器拒答时，服务端自动换到推荐的兜底模型重跑
                async with self.client.beta.messages.stream(
                    **request, betas=["server-side-fallback-2026-07-01"], fallbacks=self.cfg.fallbacks
                ) as stream:
                    message = await stream.get_final_message()
            else:
                async with self.client.messages.stream(**request) as stream:
                    message = await stream.get_final_message()
        except anthropic.APIStatusError as e:
            raise LLMError(f"{self.name} 接口报错（{e.status_code}）：{e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"{self.name} 连不上：{e}") from e

        usage = message.usage
        self.tracker.add(self.name, self.cfg.model, usage.input_tokens, usage.output_tokens)
        if message.stop_reason == "refusal":
            raise LLMError(f"{self.name} 拒绝了这次请求")
        if message.stop_reason == "max_tokens":
            raise LLMError(f"{self.name} 输出被 max_tokens 截断，可以在配置里调大 max_tokens")
        return "".join(b.text for b in message.content if b.type == "text")


class OpenAICompatibleProvider(Provider):
    def __init__(self, cfg: ProviderConfig, tracker: UsageTracker):
        super().__init__(cfg, tracker)
        key = cfg.api_key()
        if not key:
            raise LLMError(f"{cfg.name}：环境变量 {cfg.api_key_env or '（没填 api_key_env）'} 没有设置")
        self.client = openai.AsyncOpenAI(api_key=key, base_url=cfg.base_url, max_retries=4)

    async def _complete(self, system: str, user: str, schema: dict, schema_name: str, lang: str) -> str:
        user = (
            f"{user}\n\n---\n{TEXTS[lang]['json_instruction'].format(name=schema_name)}\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )
        request = dict(
            model=self.cfg.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            **{self.cfg.max_tokens_param: self.cfg.max_tokens},
        )
        if self.cfg.json_mode:
            request["response_format"] = {"type": "json_object"}
        if self.cfg.extra_body:
            request["extra_body"] = self.cfg.extra_body
        try:
            resp = await self.client.chat.completions.create(**request)
        except openai.APIStatusError as e:
            raise LLMError(f"{self.name} 接口报错（{e.status_code}）：{e.message}") from e
        except openai.APIConnectionError as e:
            raise LLMError(f"{self.name} 连不上：{e}") from e

        if resp.usage:
            self.tracker.add(self.name, self.cfg.model, resp.usage.prompt_tokens, resp.usage.completion_tokens)
        else:
            self.tracker.add(self.name, self.cfg.model, 0, 0)
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise LLMError(f"{self.name} 输出被截断，可以在配置里调大 max_tokens")
        return choice.message.content or ""


class MockProvider(Provider):
    """按 schema 造数据，不联网。字段名是 paper_id / paper_ids 时，从提示词里挑论文编号。"""

    async def _complete(self, system: str, user: str, schema: dict, schema_name: str, lang: str) -> str:
        ids = list(dict.fromkeys(re.findall(r"\b([PL]\d{2,3})\b", user))) or ["P01"]
        defs = schema.get("$defs", {})
        counter = [0]

        def make(node: dict, key: str = ""):
            if "$ref" in node:
                return make(defs[node["$ref"].split("/")[-1]], key)
            if "anyOf" in node:
                return make(next((n for n in node["anyOf"] if n.get("type") != "null"), node["anyOf"][0]), key)
            if "enum" in node:
                counter[0] += 1
                return node["enum"][counter[0] % len(node["enum"])]
            kind = node.get("type")
            if kind == "object":
                return {k: make(v, k) for k, v in node.get("properties", {}).items()}
            if kind == "array":
                if key == "items" and schema_name == "ScreenBatch":
                    return [{"paper_id": pid, "relevance": ["high", "medium", "low"][i % 3], "reason": f"[mock] #{i + 1}"} for i, pid in enumerate(ids)]
                if key == "paper_ids":
                    return ids[:2]
                if key == "seeds":
                    return [make(node["items"], "seed") for _ in range(5)]
                return [make(node["items"], key) for _ in range(2)]
            if kind == "boolean":
                counter[0] += 1
                return counter[0] % 2 == 0
            if kind in ("integer", "number"):
                return 1
            if key == "paper_id":
                counter[0] += 1
                return ids[counter[0] % len(ids)]
            if key == "raised_by":
                return self.name
            return f"[mock:{self.name}] {key}"

        await asyncio.sleep(0)
        self.tracker.add(self.name, self.cfg.model, len(user) // 2, 500)
        return json.dumps(make(schema), ensure_ascii=False)


def make_provider(cfg: ProviderConfig, tracker: UsageTracker) -> Provider:
    if cfg.type == "anthropic":
        return AnthropicProvider(cfg, tracker)
    if cfg.type == "openai_compatible":
        return OpenAICompatibleProvider(cfg, tracker)
    return MockProvider(cfg, tracker)


class ProviderPool:
    """按名字懒加载模型，同一个名字只建一个客户端（共享并发限制）。"""

    def __init__(self, providers: dict[str, ProviderConfig], tracker: UsageTracker):
        self.configs = providers
        self.tracker = tracker
        self._cache: dict[str, Provider] = {}

    def get(self, name: str) -> Provider:
        if name not in self._cache:
            self._cache[name] = make_provider(self.configs[name], self.tracker)
        return self._cache[name]
