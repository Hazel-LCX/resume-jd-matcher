# -*- coding: utf-8 -*-
"""命令行演示：样例 JD x 样例简历，打印文字版匹配报告。

用法：D:/Anaconda/python.exe demo.py
"""
from pathlib import Path

from matcher import load_lexicon, match

HERE = Path(__file__).resolve().parent


def main() -> None:
    jd = (HERE / "sample_data" / "jd_demo.txt").read_text(encoding="utf-8")
    resume = (HERE / "sample_data" / "resume_demo.txt").read_text(encoding="utf-8")
    lexicon = load_lexicon()
    r = match(jd, resume, lexicon)
    total = len(r.jd_skills)
    covered = len(r.covered)

    print("=" * 50)
    print(f"匹配报告   覆盖率 {r.coverage_rate:.0%}   ({covered}/{total})")
    print(f"词库规模 {len(lexicon)} 词条")
    print("=" * 50)

    print("【分类覆盖】")
    for cat, cov, tot in r.category_stats():
        filled = round(cov / tot * 10)
        bar = "█" * filled + "░" * (10 - filled)
        print(f"  {cat}  {cov}/{tot}  {bar}")

    print("【已覆盖】")
    for h in r.covered:
        print(f"  + {h.name}  x{r.resume_counts[h.name]}")

    print("【缺口清单】")
    for h in r.gaps:
        print(f"  - {h.name}（{h.category}）")


if __name__ == "__main__":
    main()
