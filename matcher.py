# -*- coding: utf-8 -*-
"""
简历-JD 匹配引擎（v1）

纯 Python 实现：技能词提取、覆盖率计算、缺口统计，只用标准库，零第三方依赖。
技能词库在同目录 skills_lexicon.json 中维护（可自由增删词条，无需改代码）。

匹配规则：
- 文本先做 NFKC 归一化再统一小写（"Ｃ＋＋" -> "c++"）；
- 含中文的别名用普通子串匹配（中文没有分词问题）；
- 纯英文别名用边界正则，分三档：
  * 长度 <= 2：左右两侧都必须是非字母数字 —— "CPU"/"MCU" 不会误判出 "C"；
  * 长度 >= 3：右侧只拦截小写字母（放行数字和符号）—— "ros2"、"c++11" 能命中；
  * 词库标记 fuzzy_tail 的型号类词条（STM32/ESP32/IMU）：右侧完全不设限，
    "STM32F103" 必须能算作 STM32。
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

LEXICON_PATH = Path(__file__).resolve().parent / "skills_lexicon.json"

_HAS_HAN = re.compile(r"[\u4e00-\u9fff]")


def normalize(text: str) -> str:
    """NFKC 归一化（全角转半角等）+ 统一小写。"""
    return unicodedata.normalize("NFKC", text).lower()


@dataclass(frozen=True)
class SkillDef:
    """词库中的一个词条：规范名 + 分类 + 全部小写别名。"""
    name: str
    category: str
    aliases: tuple[str, ...]
    fuzzy_tail: bool = False


@dataclass
class SkillHit:
    """某个技能在一段文本中的命中结果。"""
    name: str
    category: str
    alias: str   # 命中所用的变体（取最长的那个，最具区分度）
    count: int   # 出现次数


@dataclass
class MatchReport:
    """一次 JD x 简历 匹配的完整结果。"""
    jd_skills: list[SkillHit]      # 从 JD 提取到的技能要求项（分母）
    gaps: list[SkillHit]           # 其中简历里找不到证据的（缺口）
    resume_counts: dict[str, int]  # 简历中各技能出现次数（可能含 JD 没提的）

    @property
    def covered(self) -> list[SkillHit]:
        """JD 要求且简历中有证据的技能。"""
        return [h for h in self.jd_skills if h.name in self.resume_counts]

    @property
    def coverage_rate(self) -> float:
        """覆盖率 = 覆盖数 / JD 要求项数。分母取 JD 侧：匹配衡量的是
        "JD 的要求被满足了几成"，而不是"简历的技能被 JD 用上了几成"。"""
        return len(self.covered) / len(self.jd_skills) if self.jd_skills else 0.0

    def category_stats(self) -> list[tuple[str, int, int]]:
        """按分类汇总为 (分类名, 覆盖数, 总数)，按总数降序。"""
        agg: dict[str, list[int]] = {}
        for h in self.jd_skills:
            cov, tot = agg.get(h.category, [0, 0])
            tot += 1
            if h.name in self.resume_counts:
                cov += 1
            agg[h.category] = [cov, tot]
        return sorted(
            ((cat, v[0], v[1]) for cat, v in agg.items()),
            key=lambda x: -x[2],
        )


def load_lexicon(path: Path | str = LEXICON_PATH) -> list[SkillDef]:
    """读取词库 JSON 并做基本清洗：去空、去重、统一小写。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    skills: list[SkillDef] = []
    for item in data["skills"]:
        aliases = tuple(dict.fromkeys(
            a.strip().lower() for a in item["aliases"] if a.strip()
        ))
        if not aliases:
            raise ValueError(f"词条 {item.get('name')!r} 没有可用别名")
        skills.append(SkillDef(
            name=item["name"],
            category=item.get("category", "其他"),
            aliases=aliases,
            fuzzy_tail=bool(item.get("fuzzy_tail", False)),
        ))
    return skills


_PATTERN_CACHE: dict[tuple[str, bool], re.Pattern | None] = {}


def _pattern_for(alias: str, fuzzy_tail: bool) -> re.Pattern | None:
    """为英文别名编译边界正则；含中文的别名返回 None（走子串匹配）。"""
    key = (alias, fuzzy_tail)
    if key not in _PATTERN_CACHE:
        if _HAS_HAN.search(alias):
            _PATTERN_CACHE[key] = None
        else:
            esc = re.escape(alias)
            if len(alias) <= 2:
                body = rf"(?<![a-z0-9]){esc}(?![a-z0-9])"
            elif fuzzy_tail:
                body = rf"(?<![a-z0-9]){esc}"
            else:
                body = rf"(?<![a-z0-9]){esc}(?![a-z])"
            _PATTERN_CACHE[key] = re.compile(body)
    return _PATTERN_CACHE[key]


def find_skills(text: str, lexicon: list[SkillDef]) -> list[SkillHit]:
    """在一段文本中找出词库里出现过的所有技能，同一技能取最长命中变体。"""
    norm = normalize(text)
    hits: list[SkillHit] = []
    for sk in lexicon:
        best: tuple[str, int] | None = None
        for alias in sk.aliases:
            pat = _pattern_for(alias, sk.fuzzy_tail)
            n = norm.count(alias) if pat is None else len(pat.findall(norm))
            if n and (best is None or len(alias) > len(best[0])):
                best = (alias, n)
        if best:
            hits.append(SkillHit(sk.name, sk.category, best[0], best[1]))
    return hits


def match(jd_text: str, resume_text: str,
          lexicon: list[SkillDef] | None = None) -> MatchReport:
    """主入口：JD 提取技能 -> 简历查证据 -> 生成报告。"""
    lexicon = lexicon if lexicon is not None else load_lexicon()
    jd_hits = find_skills(jd_text, lexicon)
    resume_counts = {h.name: h.count for h in find_skills(resume_text, lexicon)}
    gaps = [h for h in jd_hits if h.name not in resume_counts]
    return MatchReport(jd_hits, gaps, resume_counts)
