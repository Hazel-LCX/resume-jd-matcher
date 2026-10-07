# -*- coding: utf-8 -*-
"""简历-JD 匹配分析器 v1 —— Streamlit 界面。

运行：D:/Anaconda/python.exe -m streamlit run app.py --server.headless=true
"""
from pathlib import Path

import streamlit as st

from llm_advisor import quick_advice, stream_llm_advice
from matcher import load_lexicon, match

HERE = Path(__file__).resolve().parent

st.set_page_config(page_title="简历-JD 匹配分析器", page_icon="🎯", layout="wide")

st.markdown(
    """
    <style>
      #MainMenu, header, footer {visibility: hidden;}
      .block-container {padding-top: 2rem; max-width: 1180px;}

      /* 标题下的强调线（Carbon Blue 60） */
      h1 {border-bottom: 3px solid #0f62fe; padding-bottom: .35rem;}

      /* 三个指标做成卡片 */
      [data-testid="stMetric"] {
        background: #f4f4f4; border: 1px solid #e0e0e0;
        border-radius: 10px; padding: 14px 18px;
      }
      [data-testid="stMetricLabel"] p {color: #525252; font-size: .92rem;}
      [data-testid="stMetricValue"] {font-size: 2.1rem;}

      /* 进度条圆角 */
      [data-testid="stProgress"] {border-radius: 6px; overflow: hidden;}

      .chip {
        display: inline-block; padding: 3px 12px; margin: 3px 6px 3px 0;
        border-radius: 14px; font-size: 13.5px;
      }
      .chip-ok  {background: #e7f5ec; color: #14713d; border: 1px solid #bfe3cd;}
      .chip-gap {background: #fdf1de; color: #a15c00; border: 1px solid #f0d5a8;}
      .chip-none {background: #f1f3f4; color: #5f6368; border: 1px solid #dadce0;}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data
def get_lexicon():
    return load_lexicon()


@st.cache_data
def get_sample(name: str) -> str:
    return (HERE / "sample_data" / name).read_text(encoding="utf-8")


def chip_block(labels: list[str], cls: str) -> str:
    """把技能名列表渲染成一排彩色圆角小芯片。"""
    if not labels:
        return '<span class="chip chip-none">（无）</span>'
    return "".join(f'<span class="chip {cls}">{t}</span>' for t in labels)


# ---------- 输入区 ----------

st.title("🎯 简历 · JD 匹配分析器")
st.caption("🔒 纯本地运行，文本不上传 · 词库查表匹配，秒出报告 · v1")

b1, b2 = st.columns(2)
analyze = b1.button("🚀 生成匹配报告", type="primary", use_container_width=True)
sample = b2.button("📥 一键载入样例", use_container_width=True)

# 载入样例必须在创建对应 text_area 之前写入 state，随后整页重跑
if sample:
    st.session_state["jd_input"] = get_sample("jd_demo.txt")
    st.session_state["resume_input"] = get_sample("resume_demo.txt")
    st.rerun()

col_jd, col_res = st.columns(2)
with col_jd:
    jd_text = st.text_area("📄 招聘 JD", height=240, key="jd_input",
                           placeholder="粘贴招聘 JD 全文……")
with col_res:
    resume_text = st.text_area("📄 我的简历", height=240, key="resume_input",
                               placeholder="粘贴简历全文……")

if analyze:
    if not jd_text.strip() or not resume_text.strip():
        st.warning("JD 和简历两边都要有内容，才能开始分析。")
    else:
        rep = match(jd_text, resume_text, get_lexicon())
        if not rep.jd_skills:
            st.info("词库没有在这段 JD 里认出任何技能。检查一下内容，"
                    "或者往 skills_lexicon.json 里加词条。")
            st.session_state["report"] = None
        else:
            st.session_state["report"] = rep
            # 记下本次分析用的原文，供改写建议复用；新报告作废旧建议
            st.session_state["report_input"] = (jd_text, resume_text)
            st.session_state["advice"] = None

# ---------- 报告区 ----------

rep = st.session_state.get("report")
if rep is not None:
    st.divider()

    m1, m2, m3 = st.columns(3)
    m1.metric("技能覆盖率", f"{rep.coverage_rate:.0%}",
              help="覆盖率 = 已覆盖技能数 ÷ JD 提取到的技能总数")
    m2.metric("已覆盖", f"{len(rep.covered)} 项",
              help="JD 要求、且简历中找到证据的技能")
    m3.metric("缺口", f"{len(rep.gaps)} 项",
              help="JD 提到、但简历中找不到证据的技能")

    st.subheader("分类覆盖")
    for cat, cov, tot in rep.category_stats():
        label_col, bar_col = st.columns([2, 6])
        label_col.markdown(f"**{cat}** · {cov}/{tot}")
        bar_col.progress(cov / tot)

    left, right = st.columns(2)
    with left:
        st.subheader("✅ 已覆盖")
        labels = [
            h.name + (f" ×{rep.resume_counts[h.name]}"
                      if rep.resume_counts[h.name] > 1 else "")
            for h in rep.covered
        ]
        st.markdown(chip_block(labels, "chip-ok"), unsafe_allow_html=True)
    with right:
        st.subheader("⛔ 缺口")
        st.markdown(chip_block([h.name for h in rep.gaps], "chip-gap"),
                    unsafe_allow_html=True)

    st.divider()
    st.subheader("💡 简历改写建议")
    if st.button("✍️ 生成改写建议",
                 help="词频建议秒出；本机 LM Studio 在线时 AI 建议流式逐字追加，离线不阻塞"):
        jd_used, resume_used = st.session_state.get("report_input", ("", ""))
        st.session_state["advice"] = None      # 清掉旧结果，这一趟边生成边画
        live = st.container(border=True)
        live.markdown('<span class="chip chip-gap">🟡 即时建议：词频模式（秒出）</span>',
                      unsafe_allow_html=True)
        live.markdown(quick_advice(rep))
        badge = st.empty()
        stream_box = st.empty()
        badge.markdown("⏳ 正在连接本地 LLM……")
        result = stream_llm_advice(
            rep, jd_used, resume_used,
            on_delta=lambda acc: stream_box.markdown(acc),
            on_status=lambda s: badge.markdown(s),
        )
        st.session_state["advice"] = result
        st.rerun()                             # 生成完毕，从 state 统一重画最终态
    advice = st.session_state.get("advice")
    if advice is not None:
        cls = "chip-ok" if advice.mode == "llm" else "chip-gap"
        icon = "🟢" if advice.mode == "llm" else "🟡"
        st.markdown(f'<span class="chip {cls}">{icon} {advice.detail}</span>',
                    unsafe_allow_html=True)
        # 降级结果的 text 是空串，必须重画词频建议，否则它会从页面上消失
        st.markdown(advice.text if advice.mode == "llm" else quick_advice(rep))

st.caption(f"简历-JD 匹配分析器 v1 · 词库 {len(get_lexicon())} 词条 · Streamlit · 数据不出本机")
