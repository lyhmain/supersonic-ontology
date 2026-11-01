"""
LLM 客户端工具

- 文本 LLM：GLM 网关（OpenAI 兼容）
- 视觉 LLM：GPUStack（Qwen3.6-VL-27B-Instruct，思考型）
"""

from typing import Generator, Optional

from openai import OpenAI

from kb_rag.config import get_config
from kb_rag.tool.logger import setup_logger

logger = setup_logger("utils.llm")

_llm_client: Optional[OpenAI] = None
_vl_client: Optional[OpenAI] = None


def get_llm_client() -> OpenAI:
    """GLM 网关客户端（单例）"""
    global _llm_client
    if _llm_client is None:
        cfg = get_config()
        _llm_client = OpenAI(base_url=cfg.llm_api_base, api_key=cfg.llm_api_key)
    return _llm_client


def get_vl_client() -> OpenAI:
    """GPUStack 视觉模型客户端（单例）"""
    global _vl_client
    if _vl_client is None:
        cfg = get_config()
        _vl_client = OpenAI(base_url=cfg.vl_api_base, api_key=cfg.vl_api_key)
    return _vl_client


def chat(
    messages: list,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    stream: bool = False,
    disable_thinking: bool = False,
):
    """
    调用文本 LLM（GLM 网关）

    :param messages: OpenAI 格式消息列表
    :param model: 模型名，默认取配置 LLM_DEFAULT_MODEL
    :param stream: 是否流式返回（返回 Generator[str]）
    :param disable_thinking: 关闭思考模式（判别类任务提速约 5 倍，GLM thinking 协议）
    :return: 非流式返回完整文本 str；流式返回逐段文本生成器
    """
    cfg = get_config()
    client = get_llm_client()
    resp = client.chat.completions.create(
        model=model or cfg.default_model,
        messages=messages,
        temperature=cfg.llm_temperature if temperature is None else temperature,
        max_tokens=max_tokens,
        stream=stream,
        extra_body={"thinking": {"type": "disabled"}} if disable_thinking else None,
    )
    if not stream:
        content = resp.choices[0].message.content or ""
        logger.debug(f"LLM 响应长度: {len(content)}")
        return content
    return _stream_chunks(resp)


def _stream_chunks(resp) -> Generator[str, None, None]:
    for chunk in resp:
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


class ThinkFilter:
    """
    有状态的流式 <think>...</think> 过滤器

    思考型模型的推理段可能跨越任意多个增量块，逐块正则无法处理，
    此处以字符流状态机方式抑制 think 段输出。
    """

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self, on_thinking=None):
        self._inside = False
        self._buf = ""       # 疑似标签前缀缓冲
        self.on_thinking = on_thinking  # 思考段增量回调 fn(text)，前端可折叠展示

    def feed(self, piece: str) -> str:
        """喂入增量文本，返回可安全输出的文本"""
        out = []
        self._buf += piece
        while self._buf:
            if self._inside:
                idx = self._buf.find(self.CLOSE)
                if idx == -1:
                    keep = self._buf[-(len(self.CLOSE) - 1):]
                    thought, self._buf = self._buf[:-len(keep)] if keep else self._buf, keep
                    if thought and self.on_thinking:
                        self.on_thinking(thought)
                    break
                thought = self._buf[:idx]
                self._buf = self._buf[idx + len(self.CLOSE):]
                self._inside = False
                if thought and self.on_thinking:
                    self.on_thinking(thought)
            else:
                idx = self._buf.find(self.OPEN)
                if idx == -1:
                    # 输出除疑似 "<think" 前缀外的部分（最多保留 6 字符防误切）
                    keep = len(self.OPEN) - 1
                    if len(self._buf) > keep:
                        out.append(self._buf[:-keep])
                        self._buf = self._buf[-keep:]
                    break
                out.append(self._buf[:idx])
                self._buf = self._buf[idx + len(self.OPEN):]
                self._inside = True
        return "".join(out)

    def flush(self) -> str:
        """流结束时调用：输出滞留缓冲（非思考态下全部放行；思考态未闭合则丢弃）"""
        tail = self._buf
        self._buf = ""
        if self._inside or not tail:
            return ""
        if self.on_thinking is None or self.OPEN[:1] not in tail:
            return tail
        # 残留含疑似开头的裸文本，按原文输出
        return tail


def stream_answer(messages, temperature: float = 0.2, on_delta=None, on_thinking=None) -> str:
    """
    流式生成并返回完整文本（增量经 ThinkFilter 清洗）

    :param on_delta: 可选回调 fn(clean_delta_text) 正文增量
    :param on_thinking: 可选回调 fn(think_text) 思考段增量（前端折叠展示用）
    """
    filt = ThinkFilter(on_thinking=on_thinking)
    parts = []
    for piece in chat(messages, stream=True, temperature=temperature):
        clean = filt.feed(piece)
        if clean:
            parts.append(clean)
            if on_delta:
                on_delta(clean)
    tail = filt.flush()
    if tail:
        parts.append(tail)
        if on_delta:
            on_delta(tail)
    return "".join(parts).strip()


def vl_chat(
    messages: list,
    max_tokens: int = 1024,
    stream: bool = False,
):
    """
    调用视觉 LLM（GPUStack，思考型模型，max_tokens 建议 >= 512）
    """
    cfg = get_config()
    client = get_vl_client()
    resp = client.chat.completions.create(
        model=cfg.vl_model,
        messages=messages,
        max_tokens=max_tokens,
        stream=stream,
    )
    if not stream:
        return resp.choices[0].message.content or ""
    return _stream_chunks(resp)
