# -*- coding: utf-8 -*-
"""真实数据评估工具：量化"引擎提取"与"人工标注"的差距。

背景：单元测试保证代码逻辑正确，样例保证演示可跑，但词库能否覆盖真实
世界，需要拿真实 JD/简历来评估。本脚本对比引擎结果与人工标注，输出
召回率、漏报与待复核清单，驱动词库迭代。

用法：
  python eval.py <文本文件> <人工标注文件>
  人工标注文件：一行一个技能（写规范名或任意别名都行）

流程：通读文本 -> 把"你认为文本里要求的技能"逐行写进标注文件 -> 跑脚本
-> 对照漏报/误报清单 -> 补词库 -> 重跑，看召回率变化。
"""
from __future__ import annotations

import sys
from pathlib import Path

from matcher import find_skills, load_lexicon


def resolve(term: str, lexicon) -> str | None:
    """把人工标注词对应到词库规范名：规范名 > 别名 > 包含关系。"""
    t = term.strip().lower()
    for sk in lexicon:
        if sk.name.lower() == t:
            return sk.name
    for sk in lexicon:
        if t in sk.aliases:
            return sk.name
    # 包含兜底：双方至少 2 字符。否则单字母规范名（如 "C"）会把
    # μC/OS-II、TCP/IP 这类标注吞掉——"c" 是它们的子串
    for sk in lexicon:
        name_l = sk.name.lower()
        if len(name_l) >= 2 and len(t) >= 2 and (t in name_l or name_l in t):
            return sk.name
    return None


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(1)
    text_path, expected_path = Path(sys.argv[1]), Path(sys.argv[2])
    text = text_path.read_text(encoding="utf-8")
    terms = [line.strip() for line
             in expected_path.read_text(encoding="utf-8").splitlines()
             if line.strip()]

    lexicon = load_lexicon()
    hits = find_skills(text, lexicon)
    extracted = {h.name for h in hits}

    matched_pairs: list[tuple[str, str]] = []
    unresolved: list[str] = []
    for t in terms:
        name = resolve(t, lexicon)
        if name is None:
            unresolved.append(t)
        else:
            matched_pairs.append((t, name))
    expected_names = {name for _, name in matched_pairs}

    covered = expected_names & extracted
    missed = expected_names - extracted
    flagged = [h.name for h in hits if h.name not in expected_names]

    # 词库外标注也是引擎没覆盖到的技能，必须计入分母——否则词库缺口
    # 会把召回率稀释成虚假的高分
    total = len(expected_names) + len(unresolved)
    recall = len(covered) / total if total else 0.0

    print(f"文本: {text_path.name}    人工标注 {len(terms)} 条 -> 规范技能 {total} 项")
    print("=" * 56)
    print(f"引擎提取 {len(hits)} 项：")
    for h in hits:
        print(f"  + {h.name}（命中: {h.alias}）")
    print("\n标注对应关系：")
    for t, name in matched_pairs:
        print(f"  {t} -> {name}")
    for t in unresolved:
        print(f"  {t} -> （词库外，计入漏报）")
    print(f"\n召回率 {recall:.0%}（{len(covered)}/{total}）")
    if missed:
        print("\n❌ 漏报（人标了、引擎没提到）——优先排查别名是否收录：")
        for n in sorted(missed):
            print(f"  - {n}")
    if unresolved:
        print("\n🚫 标注词在词库中查无此项（多半是真缺技能，考虑加词条）：")
        for t in unresolved:
            print(f"  - {t}")
    if flagged:
        print("\n⚠️ 待人工复核（引擎提了、你没标——可能是误报，也可能你漏标）：")
        for n in sorted(flagged):
            print(f"  - {n}")
    print("\n注意：'待人工复核'不是自动判死的误报，最终结论要人工过目。")


if __name__ == "__main__":
    main()
