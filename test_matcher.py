# -*- coding: utf-8 -*-
"""
matcher 引擎的独立测试。
两种跑法：`python test_matcher.py` 直接执行；或被 pytest 收集（test_ 前缀）。
"""
from matcher import LEXICON_PATH, find_skills, load_lexicon, match

LEX = load_lexicon()


def _names(text: str) -> set[str]:
    return {h.name for h in find_skills(text, LEX)}


# ---------- 文本归一化 ----------

def test_fullwidth_normalized():
    assert {"C++", "Python"} <= _names("熟悉Ｃ＋＋和Ｐｙｔｈｏｎ")


def test_english_case_insensitive():
    assert "Python" in _names("熟练使用PYTHON进行数据处理")


def test_chinese_substring():
    assert "机器学习" in _names("了解机器学习基础")


# ---------- 英文边界规则 ----------

def test_cpu_does_not_mean_c():
    assert "C" not in _names("负责CPU与MCU选型")


def test_cplusplus_implies_c():
    # 文本写 C++ 时，C 与 C++ 都应点亮（行业默认语义）
    assert {"C", "C++"} <= _names("熟悉C++")


def test_model_suffix_counts():
    # 型号后缀：STM32F103 必须能算作 STM32
    assert "STM32" in _names("基于STM32F103完成固件开发")


def test_ros2_also_flags_ros():
    # 两种写法都要命中 ROS2，且带出 ROS
    names = _names("使用ROS2与 ROS 2 开发")
    assert {"ROS", "ROS2"} <= names


def test_rapid_does_not_mean_pid():
    assert "PID" not in _names("参与rapid原型开发")


# ---------- 报告与统计 ----------

def test_report_math():
    jd = "要求：熟悉C++、Python、ROS2、Linux，了解强化学习"
    resume = "技能：C/C++、Python、Linux、STM32"
    r = match(jd, resume, LEX)
    # JD 提取 7 项：C++、C、Python、ROS2、ROS、Linux、强化学习
    # 简历只覆盖 4 项（C/C++、Python、Linux），缺口 3 项
    assert r.coverage_rate == 4 / 7
    assert {h.name for h in r.gaps} == {"ROS", "ROS2", "强化学习"}


def test_empty_inputs_no_crash():
    r = match("", "", LEX)
    assert r.coverage_rate == 0.0 and r.jd_skills == []
    r2 = match("要会Python", "", LEX)
    assert r2.coverage_rate == 0.0
    assert [g.name for g in r2.gaps] == ["Python"]


def test_resume_count():
    r = match("要会Python", "用python做了A，又用Python做了B", LEX)
    assert r.resume_counts["Python"] == 2


def test_category_stats():
    jd = "要求：熟悉C++、Python、ROS2、Linux"
    resume = "技能：C/C++、Python"
    r = match(jd, resume, LEX)
    stats = dict((c, (v, t)) for c, v, t in r.category_stats())
    assert stats["编程语言"] == (3, 3)      # C/C++/Python 覆盖（Linux 在硬件分类）
    assert stats["嵌入式/硬件"] == (0, 1)   # Linux 缺
    assert stats["机器人"] == (0, 2)        # ROS2 连带 ROS，简历均未提及


# ---------- 词库完整性 ----------

def test_lexicon_integrity():
    seen: dict[str, str] = {}
    assert len(LEX) > 50, "词库疑似加载失败"
    for sk in LEX:
        assert sk.aliases, f"{sk.name} 没有别名"
        for a in sk.aliases:
            assert a == a.lower(), f"{sk.name} 别名必须小写: {a}"
            assert a not in seen, f"别名 {a!r} 重复: {seen[a]} vs {sk.name}"
            seen[a] = sk.name


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}  {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} 通过")
    raise SystemExit(1 if failed else 0)
