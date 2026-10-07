# -*- coding: utf-8 -*-
"""改写建议模块（v1.2）：秒出词频建议 + LLM 流式补强（兼容思考型模型）。

渐进式增强设计：
1. quick_advice()      —— 纯词频规则，零网络、毫秒级、永不失败，永远先出；
2. stream_llm_advice() —— 本机 LM Studio 在线时，AI 建议逐字流式追加；
   探活 3 秒不响应 / 生成超时 / 返回异常，一律放弃 AI 层并返回降级标记，
   界面保留词频建议并显示降级徽章。判断权永远在引擎，这里只表达。

思考型模型（Qwen3.5 等 Reasoning 模型）：token 先走 delta.reasoning_content
（思考），写完才轮到 delta.content（正文）。实测 /no_think、
chat_template_kwargs、reasoning_budget 均被 LM Studio 无视，因此本模块不
尝试关闭思考，而是把思考进度实时回报给界面（on_status 回调），正文一到
即流式输出；若预算被思考耗尽，返回可操作的降级提示。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

import requests

from matcher import MatchReport

DEFAULT_API_BASE = "http://127.0.0.1:1234"
PROBE_TIMEOUT = 3   # 探活必须快：服务没开就立刻放弃 AI 层，不让用户干等
GEN_TIMEOUT = 150   # 生成可以慢：本地小模型约 9 字/秒，给足时间

_THINK_RE = re.compile(r"<think>[\s\S]*?</think>")

# LM Studio 永远在本机回环地址上，绝不该走系统代理——
# 用户开代理软件时，trust_env=False 防止回环请求被劫持
_LOCAL_SESSION = requests.Session()
_LOCAL_SESSION.trust_env = False


@dataclass
class AdvisorResult:
    mode: str    # "llm" 或 "fallback"
    detail: str  # 模式说明，供界面徽章展示
    text: str    # 建议正文（Markdown）；fallback 时为空串，界面保留词频建议


def _strip_think(raw: str) -> str:
    """去掉混进正文的 <think>…</think> 块（含未闭合的）。"""
    cleaned = _THINK_RE.sub("", raw)
    idx = cleaned.find("<think>")
    return cleaned[:idx] if idx >= 0 else cleaned


def _looks_like_garbage(text: str) -> bool:
    """检测"整段问号"式输出——模型文件或运行时不兼容的典型症状。"""
    t = text.strip()
    if len(t) < 20:
        return False
    return (t.count("?") + t.count("？")) / len(t) > 0.9


def _pick_model(models: list) -> str:
    """从 /v1/models 里挑一个非 embedding 的模型 id。"""
    ids = [m.get("id", "") for m in models]
    real = [i for i in ids if i and "embed" not in i.lower()]
    return (real or ids or ["local-model"])[0]


def quick_advice(rep: MatchReport) -> str:
    """纯词频建议：只看"出现没出现、出现几次"，不做任何语义理解。"""
    lines: list[str] = [
        "> **词频建议（即时）**：只依据关键词出现与否、出现几次生成，不依赖 AI；"
        "下方 AI 建议由本机大模型生成（离线时本区即最终结果）。",
        "",
    ]
    gap_names = [h.name for h in rep.gaps]
    if gap_names:
        lines.append(f"**缺口 {len(gap_names)} 项**：{'、'.join(gap_names)}")
        lines.append("- 某项若你实际用过：把对应项目描述改写成明确包含该技能词的句子，"
                     "别让关键词缺席——机器筛简历和这份报告用的都是关键词。")
        lines.append("- 某项若确实没掌握：别硬写进技能栏（面试会穿帮），列入学习计划即可。")
    weak = [h for h in rep.covered if rep.resume_counts.get(h.name, 0) <= 1]
    if weak:
        names = "、".join(h.name for h in weak[:5])
        lines.append("")
        lines.append(f"**只出现 1 次的已覆盖技能**：{names}")
        lines.append("- 这些是 JD 的明确要求但简历仅一笔带过，"
                     "建议在项目经历里补一个具体场景或数字，让证据更硬。")
    if not gap_names and not weak:
        lines.append("JD 要求的技能在简历中全部覆盖且均有多次出现，无需改写。")
    return "\n".join(lines)


def _build_messages(rep: MatchReport, jd_text: str, resume_text: str,
                    limit: int = 1200) -> list[dict[str, str]]:
    """构造发给本地 LLM 的对话。只给节选，控制提示词长度。"""
    gap_list = "、".join(f"{h.name}（{h.category}）" for h in rep.gaps) or "无"
    system = (
        "你是资深的简历优化顾问。用户会给你招聘JD节选、简历节选，以及程序已经算出的"
        "技能缺口清单。任务：针对缺口给出具体、可执行的简历改写建议。规则："
        "①若求职者可能实际掌握某技能只是没写，参考简历中已有的项目经历，"
        "给出可直接抄进简历的中文句式示例；"
        "②若大概率没掌握，给一句最小学习路径；"
        "③总共不超过5条，每条一行，以「- 」开头；"
        "④不要复述缺口清单本身；⑤不要任何开场白和客套话。"
    )
    user = (
        f"【招聘JD（节选）】\n{jd_text[:limit]}\n\n"
        f"【简历（节选）】\n{resume_text[:limit]}\n\n"
        f"【程序算出的缺口技能】{gap_list}\n\n"
        "请给出一页内的改写建议。"
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def stream_llm_advice(rep: MatchReport, jd_text: str, resume_text: str,
                      api_base: str = DEFAULT_API_BASE,
                      on_delta: Callable[[str], None] = lambda acc: None,
                      on_status: Callable[[str], None] = lambda s: None,
                      ) -> AdvisorResult:
    """探活 -> 流式生成。on_delta 收"到目前为止的净化正文"，on_status 收阶段文案。

    任何失败都返回 mode="fallback"（text 为空，界面保留词频建议），绝不抛异常。
    """
    # 第 1 层：探活。3 秒内不响应就当离线。GET /v1/models 不碰模型，毫秒级。
    try:
        probe = _LOCAL_SESSION.get(f"{api_base}/v1/models", timeout=PROBE_TIMEOUT)
        probe.raise_for_status()
        model_id = _pick_model(probe.json().get("data") or [])
    except (requests.RequestException, ValueError):
        return AdvisorResult("fallback", "降级模式：LM Studio 未响应，词频建议即为最终结果", "")
    on_status(f"🔗 已连接 {model_id}，等待模型响应……")

    raw_content = ""
    think_n = 0
    next_report = 0
    try:
        with _LOCAL_SESSION.post(
            f"{api_base}/v1/chat/completions",
            json={
                "model": model_id,
                "messages": _build_messages(rep, jd_text, resume_text),
                "temperature": 0.6,
                "max_tokens": 2000,   # 思考型模型思考就可能上千 token，预算要留足
                "stream": True,
            },
            timeout=GEN_TIMEOUT,
            stream=True,
        ) as resp:
            resp.raise_for_status()
            # SSE 头里通常不带 charset，requests 会误按 Latin-1 解码出乱码
            resp.encoding = "utf-8"
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                payload = line[len("data: "):]
                if payload.strip() == "[DONE]":
                    break
                delta = json.loads(payload)["choices"][0].get("delta", {})
                think_n += len(delta.get("reasoning_content") or "")
                if think_n >= next_report:
                    next_report = think_n + 120
                    on_status(f"🧠 模型思考中… 已 {think_n} 字"
                              f"（思考结束才开始写正文）")
                c = delta.get("content") or ""
                if c:
                    raw_content += c
                    if raw_content == c:          # 第一片正文到达
                        on_status("✍️ 正文生成中……")
                    on_delta(_strip_think(raw_content))
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return AdvisorResult("fallback", "降级模式：LLM 调用出错，词频建议仍有效", "")

    cleaned = _strip_think(raw_content).strip()
    if cleaned:
        if _looks_like_garbage(cleaned):
            return AdvisorResult(
                "fallback",
                "降级模式：模型输出异常（整段都是问号）——这是模型文件或 LM Studio "
                "运行时的问题，不是程序问题；建议更新 LM Studio 或换一个成熟模型",
                "")
        return AdvisorResult("llm", f"LLM 模式：本地 {model_id}（流式）", cleaned)
    if think_n:
        return AdvisorResult(
            "fallback",
            "降级模式：模型把生成预算全部花在“思考”上、没写正文——"
            "请到 LM Studio 右侧模型的 Inference/Load 设置里关闭 Thinking（思考）后重试",
            "")
    return AdvisorResult("fallback", "降级模式：LLM 返回为空，词频建议仍有效", "")
