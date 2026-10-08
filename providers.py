"""providers.py — 供应商抽象层（v2 模块 1）

不同的 LLM 服务在协议上都是 OpenAI 兼容的，差异只有三样：地址、钥匙、模型名。
本模块把差异收进 ProviderConfig 一张卡片，下游（greeter/gui）只面向卡片编程，
不面向具体供应商。

key 纪律（隔离而非加密，真兜底是可吊销）：
- 代码与仓库里永远没有真 key，预设的 key 从环境变量读
- 分发版用户在界面里贴的 key 存 %APPDATA% 用户配置，不进仓库、不随 exe 分发
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

_SESSION = requests.Session()
_SESSION.trust_env = False  # 国内云端与本地回环都直连，绕开系统代理的偶发劫持

_THINK_RE = re.compile(r"<think>[\s\S]*?</think>")


@dataclass
class ProviderConfig:
    """一张供应商配置卡片：下游只认识这张卡片，不认识具体供应商。"""

    name: str
    base_url: str
    model: str
    api_key: str = ""
    is_local: bool = False  # 隐私地基：True 的供应商在回退链上不得出现云端


def builtin_presets() -> dict[str, ProviderConfig]:
    """内置预设。key 为空不代表不可用：空 key 探活失败后自动走降级链。"""
    return {
        "书生·端砚": ProviderConfig(
            name="书生·端砚",
            base_url="https://discovery-api.intern-ai.org.cn/v1",
            model="deepseek-v4-flash-0731",
            api_key=os.environ.get("INTERNAI_API_KEY", ""),
        ),
        "本地 LM Studio": ProviderConfig(
            name="本地 LM Studio",
            base_url="http://127.0.0.1:1234/v1",
            model="qwen3.5-9b",
            is_local=True,
        ),
        "DeepSeek": ProviderConfig(
            name="DeepSeek",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-flash",
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        ),
    }


def llm_fallback_chain(
    selected: ProviderConfig,
    local: ProviderConfig,
    allow_local_fallback: bool = True,
) -> list[ProviderConfig]:
    """按用户的选择生成要依次尝试的 LLM 列表（词频保底层由 greeter 负责，不在此列）。

    这是隐私政策，不是技术限制：
      选云端 -> [云端, 本地]    云挂了允许降本地（可在设置里关掉这条降级）
      选云端且禁降级 -> [云端]
      选本地 -> [本地]          链上没有云端：选本地即"文本不出本机"的承诺，
                               程序不得替用户把文本送出这台机器。
    """
    if selected.is_local:
        return [selected]
    chain = [selected]
    if allow_local_fallback:
        chain.append(local)
    return chain


def resolve_cloud_provider(settings: dict) -> ProviderConfig:
    """按界面设置解析当前云端供应商。

    settings 结构（gui 的设置页负责读写）：
      {"cloud_preset": "书生·端砚" | "DeepSeek" | "自定义",
       "keys": {"书生·端砚": "sk-..."},           # 界面贴的 key，优先于环境变量
       "custom": {"base_url": "...", "model": "...", "api_key": "..."}}
    """
    presets = builtin_presets()
    name = settings.get("cloud_preset") or "书生·端砚"
    if name == "自定义":
        c = settings.get("custom") or {}
        return ProviderConfig(
            name="自定义云端",
            base_url=(c.get("base_url") or "").rstrip("/"),
            model=c.get("model") or "",
            api_key=c.get("api_key") or "",
        )
    pc = presets.get(name) or presets["书生·端砚"]
    key = (settings.get("keys") or {}).get(name, "") or pc.api_key
    return ProviderConfig(
        name=pc.name, base_url=pc.base_url, model=pc.model, api_key=key
    )


def probe(pc: ProviderConfig, timeout: float = 3.0) -> tuple[bool, str]:
    """探活：GET /models，限时内答得上就算活着。返回 (是否可用, 说明)。

    本机安全软件会间歇掐断已建立的回环连接（WinError 10053），
    连接类错误重试一次再下结论，避免把瞬时抖动误判为离线。
    """
    url = pc.base_url.rstrip("/") + "/models"
    for attempt in (1, 2):
        try:
            r = _SESSION.get(
                url, headers={"Authorization": f"Bearer {pc.api_key}"},
                timeout=timeout,
            )
        except requests.ConnectionError:
            if attempt == 1:
                continue
            return False, f"{pc.name} 探活失败：连接被中断"
        except requests.RequestException as e:
            return False, f"{pc.name} 探活失败：{e.__class__.__name__}"
        if r.status_code == 200:
            return True, f"{pc.name} 在线"
        return False, f"{pc.name} 探活 HTTP {r.status_code}"
    return False, f"{pc.name} 探活失败"


def _strip_think(raw: str) -> str:
    """去掉混进正文的 <think>…</think> 块（含未闭合的）。"""
    cleaned = _THINK_RE.sub("", raw)
    idx = cleaned.find("<think>")
    return cleaned[:idx] if idx >= 0 else cleaned


def _looks_like_garbage(text: str) -> bool:
    """检测"整段问号"式输出——模型或运行时不兼容的典型症状。"""
    t = text.strip()
    if len(t) < 20:
        return False
    return (t.count("?") + t.count("？")) / len(t) > 0.9


def chat_stream(
    pc: ProviderConfig,
    messages: list[dict],
    *,
    max_tokens: int = 2000,
    temperature: float = 0.8,
    gen_timeout: float = 150.0,
    on_status: Callable[[str], None] = lambda s: None,
    on_delta: Callable[[str], None] = lambda acc: None,
) -> tuple[str, str]:
    """发一次流式 OpenAI 兼容对话。返回 (净化正文, 失败原因)：失败时正文为空串。

    统一处理思考流：不论哪家供应商，delta.reasoning_content 一律当进度信号
    （本地 Qwen 和书生云端的 DeepSeek 都会吐这个字段，不做区分）。
    """
    ok, why = probe(pc)
    if not ok:
        return "", why
    on_status(f"已连接 {pc.name}（{pc.model}），等待响应……")
    url = pc.base_url.rstrip("/") + "/chat/completions"
    raw = ""
    think_n = 0
    next_report = 0
    for attempt in (1, 2):  # 连接被安全软件间歇掐断（10053）时重试一次
        try:
            with _SESSION.post(
                url,
                headers={"Authorization": f"Bearer {pc.api_key}"},
                json={
                    "model": pc.model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,  # 思考型模型思考就可能上千 token，预算要留足
                    "stream": True,
                },
                timeout=gen_timeout,
                stream=True,
            ) as resp:
                resp.raise_for_status()
                # SSE 头通常不带 charset，requests 会误按 Latin-1 解码出乱码
                resp.encoding = "utf-8"
                for line in resp.iter_lines(decode_unicode=True):
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[len("data: "):]
                    if payload.strip() == "[DONE]":
                        break
                    chunk = json.loads(payload)
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue  # 书生端点首包是只有元数据的 preamble，无 choices
                    delta = choices[0].get("delta") or {}
                    think_n += len(delta.get("reasoning_content") or "")
                    if think_n >= next_report:
                        next_report = think_n + 120
                        on_status(f"🧠 思考中… 已 {think_n} 字（思考结束才开始写正文）")
                    c = delta.get("content") or ""
                    if c:
                        raw += c
                        if raw == c:  # 第一片正文到达
                            on_status("✍️ 正文生成中……")
                        on_delta(_strip_think(raw))
            break  # 成功走完流，跳出重试环
        except requests.ConnectionError:
            if attempt == 1:
                on_status("连接被中断，正在重试……")
                continue
            return "", f"生成失败：连接被中断（{pc.name}）"
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            detail = ""
            if e.response is not None:
                detail = e.response.text[:200]
            return "", f"生成失败：HTTP {code} {detail}".rstrip()
        except (KeyError, IndexError, ValueError) as e:
            return "", f"生成失败：{e.__class__.__name__}"
    cleaned = _strip_think(raw).strip()
    if not cleaned:
        if think_n:
            return "", f"{pc.name} 把生成预算全部花在思考上、没写正文"
        return "", f"{pc.name} 返回为空"
    if _looks_like_garbage(cleaned):
        return "", f"{pc.name} 输出异常（整段都是问号）"
    return cleaned, ""


# ---- 用户设置持久化：只服务于桌面界面的"贴 key"输入 ----

def settings_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "resume-jd-matcher" / "settings.json"


def load_settings() -> dict:
    p = settings_path()
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def save_settings(data: dict) -> None:
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
