# -*- coding: utf-8 -*-
"""greeter.py — AI 招呼语生成器（v2 模块 2）

BOSS 直聘"立即沟通"前的第一句话，设计目标只有一个：不像群发。
- 个性化素材来自 matcher 的真实匹配报告：招呼语只能踩"JD 要求且简历有证据"的点；
- 四条禁令写进 system prompt（禁编造/禁夸大/禁AI腔/禁谄媚），
  产物再用引擎做二次校验——违规条目直接剔除，宁缺毋滥；
- 回退链跟随用户在界面上的选择（providers.llm_fallback_chain），
  词频模板是永远可用的最后保底。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable

from matcher import MatchReport, find_skills, load_lexicon, match
from providers import ProviderConfig, chat_stream, llm_fallback_chain

MAX_LEN = 120  # 每条上限（BOSS 打招呼的合理长度；校验放宽到 +20）

# 三条硬禁令（"禁编造"不靠词表，由 validate 用匹配引擎二次校验）
_FORBIDDEN: dict[str, tuple[str, ...]] = {
    "夸大": ("精通", "深入理解", "资深", "专家"),
    "AI腔": ("充满热情", "我相信我能够", "总而言之", "作为一名", "综合而言"),
    "谄媚": ("行业领先", "久仰", "享有盛誉", "实力雄厚", "贵公司作为"),
}

_JSON_ARRAY_RE = re.compile(r"\[[\s\S]*\]")


@dataclass
class GreetingResult:
    greetings: list[str]
    mode: str    # "cloud" | "local" | "wordfreq"
    badge: str   # 界面徽章：明示"用户选的"和"实际在用的"是否一致
    notes: list[str] = field(default_factory=list)


def build_messages(
    rep: MatchReport, jd_text: str, resume_text: str, limit: int = 1000
) -> list[dict[str, str]]:
    covered = "、".join(h.name for h in rep.covered) or "（无）"
    system = (
        "你帮求职者写 BOSS 直聘的打招呼私信。你会拿到岗位JD、简历节选，"
        "以及程序算出的【已具备技能】清单（JD要求且简历有证据）。"
        "写 3 条不同风格的招呼语：直击重合、项目证据、主动提问。硬性规则："
        "1. 第一人称，口语自然，像真人随手打的；"
        f"2. 每条不超过 {MAX_LEN} 字；"
        "3. 只能使用简历里真实出现过的技能与项目（优先挑与JD重合的），"
        "严禁编造简历和JD里都没有的技能、经历、数字；"
        "4. 禁用词：精通、深入理解、资深、专家；"
        "5. 禁AI腔：不许出现“充满热情”“我相信我能够”“总而言之”“作为一名”；"
        "6. 禁谄媚：不许吹捧对方公司（行业领先、久仰之类）；"
        "7. 只输出一个JSON数组（3个字符串，依次对应三种风格），不要任何解释。"
    )
    user = (
        f"【岗位JD】\n{jd_text[:limit]}\n\n"
        f"【简历节选】\n{resume_text[:limit]}\n\n"
        f"【已具备技能】{covered}\n\n请输出JSON数组。"
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def parse_greetings(text: str) -> list[str]:
    """从模型输出抠出招呼语：先试 JSON 数组，失败再按行拆（去序号）。"""
    items: list[str] = []
    m = _JSON_ARRAY_RE.search(text)
    if m:
        try:
            data = json.loads(m.group(0))
            items = [str(x).strip() for x in data if str(x).strip()]
        except ValueError:
            items = []
    if not items:
        for ln in text.splitlines():
            ln = re.sub(r"^\s*(?:\d+[.、)）]|[-*·])\s*", "", ln).strip()
            if len(ln) >= 10:
                items.append(ln)
    return items[:3]


def validate(
    greetings: list[str], rep: MatchReport, lexicon: list
) -> tuple[list[str], list[str]]:
    """产物二次校验：超长、违禁词、真幻觉技能，任何一条中枪即整条剔除。

    幻觉判定：招呼语里的技能名在"JD 要求"和"简历证据"里都查无出处——
    JD 里要求的技能即使简历没写（转述要求/承认在学）也放行；
    简历里有但 JD 没要求的技能更放行（额外证据）。
    词法校验只拦"凭空捏造"，语义等价（"直流电机 PID 调速"≈"电机控制"）
    交给模型判断——那是 LLM 擅长而词库不擅长的部分。
    """
    known = {h.name for h in rep.jd_skills} | set(rep.resume_counts)
    ok: list[str] = []
    problems: list[str] = []
    for i, g in enumerate(greetings, 1):
        why = None
        if len(g) > MAX_LEN + 20:
            why = f"超长（{len(g)}字）"
        else:
            for cat, words in _FORBIDDEN.items():
                hit = next((w for w in words if w in g), None)
                if hit:
                    why = f"违反{cat}禁令（出现“{hit}”）"
                    break
        if why is None:
            hallucinated = {h.name for h in find_skills(g, lexicon)} - known
            if hallucinated:
                why = "JD 和简历均无出处的技能（" + "、".join(sorted(hallucinated)) + "）"
        if why:
            problems.append(f"第{i}条：{why}，已剔除")
        else:
            ok.append(g)
    return ok, problems


def wordfreq_greetings(rep: MatchReport) -> list[str]:
    """无 LLM 的保底模板：只使用匹配报告里的真实重合点，零编造、永不失败。"""
    cov = [h.name for h in rep.covered]
    if not cov:
        return [
            "您好，看到您发布的岗位，我对这个方向很感兴趣，方便的话想详细了解岗位要求。",
            "您好，关注到这个职位，想进一步了解团队目前的技术栈和业务方向，期待交流。",
            "您好，我对该岗位很感兴趣，期待有机会进一步沟通。",
        ]
    t1 = cov[0]
    t2 = cov[1] if len(cov) > 1 else cov[0]
    return [
        f"您好，看到您发布的岗位。我有{t1}、{t2}的使用经验，与要求比较吻合，想进一步聊聊。",
        f"您好，关于岗位里的{t1}，我有过实际动手实践，简历里已写明，期待有机会详细沟通。",
        f"您好，关注到岗位提到{t1}。想请教团队这块目前主要用什么方案？我有相关实践，希望交流。",
    ]


def generate_greetings(
    jd_text: str,
    resume_text: str,
    chain: list[ProviderConfig],
    lexicon: list | None = None,
    on_status: Callable[[str], None] = lambda s: None,
    on_delta: Callable[[str], None] = lambda acc: None,
    max_tokens: int = 4000,  # 思考型模型思考深度随机，2000 曾被单次思考烧光
) -> GreetingResult:
    """按回退链依次尝试 LLM 生成；全部失败则落词频模板。永不抛异常。"""
    lexicon = lexicon if lexicon is not None else load_lexicon()
    rep = match(jd_text, resume_text, lexicon)
    notes: list[str] = []
    for idx, pc in enumerate(chain):
        on_status(f"尝试 {pc.name}……")
        text, why_fail = chat_stream(
            pc,
            build_messages(rep, jd_text, resume_text),
            max_tokens=max_tokens,
            on_status=on_status,
            on_delta=on_delta,
        )
        if not text:
            notes.append(why_fail)
            continue
        ok, problems = validate(parse_greetings(text), rep, lexicon)
        if not ok:
            notes.extend(problems)
            notes.append(f"{pc.name} 的产出全部未通过校验")
            continue
        mode = "local" if pc.is_local else "cloud"
        if idx == 0:
            badge = (
                "🔒 本地模式（文本不出本机）" if pc.is_local
                else f"☁️ 云端模式（{pc.name}）"
            )
        else:
            badge = "⚠️ 已降级至本地（云端不可用）" if pc.is_local else "⚠️ 已降级"
        return GreetingResult(ok, mode, badge, notes)
    return GreetingResult(
        wordfreq_greetings(rep), "wordfreq", "📄 词频保底（无 AI，零编造）", notes
    )
