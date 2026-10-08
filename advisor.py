# -*- coding: utf-8 -*-
"""advisor.py — 改写建议引擎（桌面版）

网页版 llm_advisor 的完全上位替代：词频建议秒出打底（永不失败），
AI 建议经 providers 的多供应商链流式生成（跟随全局供应商选择，
选本地禁升云），全部失败自动落回词频版——判断权在引擎，LLM 只表达。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from matcher import MatchReport
from providers import ProviderConfig, chat_stream


@dataclass
class AdviceResult:
    wordfreq_text: str   # 词频建议（永远可用）
    ai_text: str         # AI 建议；失败为空串
    mode: str            # "cloud" | "local" | "wordfreq"
    badge: str
    notes: list[str] = field(default_factory=list)


def quick_advice(rep: MatchReport) -> str:
    """纯词频建议：只看出现与否/次数，零网络，永不失败。（网页版原样迁移）"""
    lines: list[str] = [
        "只依据关键词出现与否、出现几次生成，不依赖 AI。",
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


def build_advice_messages(rep: MatchReport, jd_text: str, resume_text: str,
                          limit: int = 1200) -> list[dict[str, str]]:
    """构造发给 LLM 的对话。只给节选，控制提示词长度。（网页版原样迁移）"""
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


def generate_advice(
    rep: MatchReport,
    jd_text: str,
    resume_text: str,
    chain: list[ProviderConfig],
    on_status: Callable[[str], None] = lambda s: None,
    on_delta: Callable[[str], None] = lambda acc: None,
    max_tokens: int = 4000,  # 思考型模型思考深度随机，2000 曾被单次思考烧光
) -> AdviceResult:
    """按回退链依次尝试 AI 建议；全部失败落词频版。永不抛异常。"""
    wordfreq = quick_advice(rep)
    notes: list[str] = []
    for idx, pc in enumerate(chain):
        on_status(f"尝试 {pc.name}……")
        text, why = chat_stream(
            pc,
            build_advice_messages(rep, jd_text, resume_text),
            max_tokens=max_tokens,
            temperature=0.6,
            on_status=on_status,
            on_delta=on_delta,
        )
        if not text:
            notes.append(why)
            continue
        mode = "local" if pc.is_local else "cloud"
        if idx == 0:
            badge = ("🔒 本地模式（文本不出本机）" if pc.is_local
                     else f"☁️ 云端模式（{pc.name}）")
        else:
            badge = "⚠️ 已降级至本地（云端不可用）"
        return AdviceResult(wordfreq, text, mode, badge, notes)
    return AdviceResult(wordfreq, "", "wordfreq",
                        "📄 词频建议（AI 不可用，已自动降级）", notes)
