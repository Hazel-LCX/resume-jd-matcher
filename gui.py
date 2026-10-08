# -*- coding: utf-8 -*-
"""gui.py — 桌面界面（v2 模块 3，CustomTkinter）

事件模型：按钮 -> 回调函数 -> 更新控件（没有 Streamlit 的整页重跑）。
LLM 调用放后台线程，经 queue 把 status/delta/done 事件交回主线程刷新
——tkinter 控件只允许主线程碰，跨线程一律走队列。
"""
from __future__ import annotations

import queue
import threading

import customtkinter as ctk

import advisor
import greeter
from matcher import load_lexicon, match
from providers import (  # noqa: F401  (ProviderConfig 供自定义测试连接构造用)
    ProviderConfig,
    builtin_presets,
    llm_fallback_chain,
    load_settings,
    probe,
    resolve_cloud_provider,
    save_settings,
)

APP_NAME = "简历-JD 匹配分析器"
CLOUD_PRESETS = ["书生·端砚", "DeepSeek", "自定义"]

# 暗色主题调色板（主色呼应网页版的企业蓝）
ACCENT = "#4589ff"
GOOD = "#42be65"
BAD = "#fb4b53"
AMBER = "#f1c21b"
MUTED = "#9a9a9a"

SAMPLE_JD = """【岗位】嵌入式软件工程师（机器人方向）
【职责】
1. 负责移动机器人控制板的嵌入式软件开发；
2. 基于 STM32 完成电机控制、传感器数据采集与通信协议实现；
3. 参与 ROS2 节点开发与调试，配合算法组完成导航功能联调。
【任职要求】
1. 本科及以上学历，电子、自动化、计算机相关专业；
2. 熟练掌握 C/C++，熟悉 Python；
3. 熟悉 UART/I2C/SPI 等常用接口，会用 Git 管理代码；
4. 了解 ROS2、Linux 开发环境者优先。"""

SAMPLE_RESUME = """张三 ｜ 清华大学 ｜ 电子信息工程 ｜ 2027 届
【技能】
- 熟练使用 C/C++ 与 Python，掌握 Git、CMake 开发流程；
- 熟悉 STM32F103 与 51 单片机开发，掌握 UART、I2C、PWM；
- 了解 Linux 常用命令与 Shell 脚本；
- 熟悉 PID 控制算法，做过直流电机 PID 调速项目。
【项目经历】
倒立摆控制系统（2025.09 - 2026.01）
- 基于 STM32 与 MPU6050 实现一级倒立摆的稳摆控制；
- 使用 C++ 编写控制算法，通过串口上位机实时调参。"""

GREET_STYLE_NAMES = ["① 直击重合", "② 项目证据", "③ 主动提问"]


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.title(APP_NAME + " · 桌面版")
        self.geometry("1080x720")
        self.minsize(900, 620)

        self._lexicon = load_lexicon()
        self._queue: queue.Queue = queue.Queue()
        self._settings = load_settings()
        self._busy = False
        self._advice_ai_acc = ""
        self._last_wf = ""

        self._build_header()
        self._build_tabs()
        self._build_statusbar()
        self._load_settings_into_form()
        self._refresh_source_label()
        self.after(120, self._poll_queue)

    # ---------------- 结构 ----------------

    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(14, 0))
        ctk.CTkLabel(bar, text=APP_NAME,
                     font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
        ctk.CTkLabel(bar, text="  v2 · 桌面版", text_color=MUTED).pack(side="left")
        ctk.CTkLabel(bar, text="匹配纯本地 · AI 可选云端",
                     text_color=MUTED).pack(side="right")

    def _build_tabs(self):
        self.tabs = ctk.CTkTabview(self)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=(8, 4))
        for name in ("匹配报告", "改写建议", "招呼语", "设置"):
            self.tabs.add(name)
        self._build_report_tab(self.tabs.tab("匹配报告"))
        self._build_advice_tab(self.tabs.tab("改写建议"))
        self._build_greet_tab(self.tabs.tab("招呼语"))
        self._build_settings_tab(self.tabs.tab("设置"))

    def _build_statusbar(self):
        self.badge = ctk.CTkLabel(self, text="就绪", anchor="w",
                                  font=ctk.CTkFont(size=13))
        self.badge.pack(fill="x", padx=14, pady=(0, 8))

    def _set_badge(self, text: str, color: str | None = None):
        self.badge.configure(text=text, text_color=color or "#dedede")

    def _tag(self, tb: ctk.CTkTextbox, name: str, color: str):
        try:
            tb.tag_config(name, foreground=color)
        except Exception:
            tb._textbox.tag_config(name, foreground=color)

    # ---------------- 匹配报告页 ----------------

    def _build_report_tab(self, tab):
        tab.grid_columnconfigure((0, 1), weight=1)
        tab.grid_rowconfigure(1, weight=3)
        tab.grid_rowconfigure(4, weight=2)
        ctk.CTkLabel(tab, text="岗位 JD（粘贴）").grid(
            row=0, column=0, sticky="w", padx=6, pady=(6, 2))
        ctk.CTkLabel(tab, text="简历（粘贴）").grid(
            row=0, column=1, sticky="w", padx=6, pady=(6, 2))
        self.jd_box = ctk.CTkTextbox(tab)
        self.resume_box = ctk.CTkTextbox(tab)
        self.jd_box.grid(row=1, column=0, sticky="nsew", padx=6)
        self.resume_box.grid(row=1, column=1, sticky="nsew", padx=6)

        btns = ctk.CTkFrame(tab, fg_color="transparent")
        btns.grid(row=2, column=0, columnspan=2, sticky="ew", padx=6, pady=8)
        ctk.CTkButton(btns, width=130, height=34,
                      text="⚡ 生成匹配报告",
                      command=self.on_match).pack(side="right")
        ctk.CTkButton(btns, width=120, height=34,
                      text="📥 载入样例",
                      fg_color="transparent", border_width=1,
                      border_color=ACCENT, text_color=ACCENT,
                      command=self.on_load_sample).pack(side="right", padx=(0, 8))
        ctk.CTkLabel(btns, text="样例=张三（清华大学·嵌入式方向）",
                     text_color=MUTED).pack(side="right", padx=(0, 8))

        self.cat_frame = ctk.CTkFrame(tab, fg_color="transparent")
        self.cat_frame.grid(row=3, column=0, columnspan=2, sticky="ew", padx=6)
        ctk.CTkLabel(self.cat_frame, text="（生成报告后，这里显示各分类覆盖率）",
                     text_color=MUTED).pack(anchor="w", padx=4)

        self.report_box = ctk.CTkTextbox(tab, font=ctk.CTkFont(size=14))
        self.report_box.grid(row=4, column=0, columnspan=2,
                             sticky="nsew", padx=6, pady=(0, 6))
        self.report_box.configure(state="disabled")
        self._tag(self.report_box, "accent", ACCENT)
        self._tag(self.report_box, "good", GOOD)
        self._tag(self.report_box, "bad", BAD)
        self._tag(self.report_box, "muted", MUTED)

    def _render_categories(self, rep):
        for w in self.cat_frame.winfo_children():
            w.destroy()
        stats = rep.category_stats()
        if not stats:
            return
        for cat, cov, tot in stats:
            row = ctk.CTkFrame(self.cat_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(row, text=cat, width=110, anchor="w").pack(side="left")
            bar = ctk.CTkProgressBar(row)
            bar.pack(side="left", fill="x", expand=True, padx=8)
            bar.set(cov / tot if tot else 0)
            bar.configure(progress_color=GOOD if cov == tot else ACCENT)
            ctk.CTkLabel(row, text=f"{cov}/{tot}", width=56,
                         anchor="e").pack(side="left")

    def on_load_sample(self):
        for box, text in ((self.jd_box, SAMPLE_JD),
                          (self.resume_box, SAMPLE_RESUME)):
            box.delete("1.0", "end")
            box.insert("1.0", text)
        self._set_badge("样例已载入，点「生成匹配报告」查看结果", ACCENT)

    def on_match(self):
        jd = self.jd_box.get("1.0", "end").strip()
        resume = self.resume_box.get("1.0", "end").strip()
        if not jd or not resume:
            self._set_badge("先粘贴 JD 和简历（或点「载入样例」）", AMBER)
            return
        rep = match(jd, resume, self._lexicon)
        self.report_box.configure(state="normal")
        self.report_box.delete("1.0", "end")
        self._render_report(rep)
        self.report_box.configure(state="disabled")
        self._render_categories(rep)
        rate = f"{rep.coverage_rate:.0%}"
        self._set_badge(f"匹配完成：覆盖率 {rate}（纯本地计算）", GOOD)

    def _render_report(self, rep):
        tb = self.report_box
        tb.insert("end", "技能覆盖率  ", "muted")
        tb.insert("end", f"{rep.coverage_rate:.0%}", "accent")
        tb.insert("end", f"    {len(rep.covered)}/{len(rep.jd_skills)} 项\n\n",
                  "muted")
        if rep.covered:
            tb.insert("end", "● 已覆盖\n", "good")
            tb.insert("end", "、".join(h.name for h in rep.covered) + "\n")
        if rep.gaps:
            tb.insert("end", "\n● 缺口\n", "bad")
            tb.insert("end", "、".join(h.name for h in rep.gaps) + "\n")
        if not rep.jd_skills:
            tb.insert("end",
                      "\n（JD 里没有识别到词库技能——可往 skills_lexicon.json 加词条）",
                      "muted")

    # ---------------- 改写建议页 ----------------

    def _build_advice_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        self.advice_btn = ctk.CTkButton(top, height=34, text="⚡ 生成改写建议",
                                        command=self.on_advice)
        self.advice_btn.pack(side="left", padx=6)
        self.advice_status = ctk.CTkLabel(top, text="", anchor="w",
                                          text_color=MUTED)
        self.advice_status.pack(side="left", fill="x", expand=True, padx=6)

        self.advice_box = ctk.CTkTextbox(tab, font=ctk.CTkFont(size=14))
        self.advice_box.grid(row=2, column=0, sticky="nsew", padx=6, pady=(0, 6))
        self.advice_box.configure(state="disabled")
        self._tag(self.advice_box, "accent", ACCENT)
        self._tag(self.advice_box, "wf_head", MUTED)
        self._tag(self.advice_box, "ai_head", ACCENT)

        ctk.CTkLabel(tab, text="建议素材同样来自「匹配报告」页的 JD 和简历。"
                               "词频建议永远秒出打底；AI 建议按设置页选定的供应商生成"
                               "（选本地则禁升云），失败自动落回词频版。",
                     anchor="w", text_color=MUTED).grid(
            row=3, column=0, sticky="ew", padx=8, pady=(4, 0))

    def on_advice(self):
        if self._busy:
            return
        jd = self.jd_box.get("1.0", "end").strip()
        resume = self.resume_box.get("1.0", "end").strip()
        if not jd or not resume:
            self._set_badge("改写建议也依赖 JD 和简历：请先在「匹配报告」页粘贴", AMBER)
            return
        self._busy = True
        self.advice_btn.configure(state="disabled")
        self.advice_status.configure(text="准备中……")
        rep = match(jd, resume, self._lexicon)
        self._last_wf = advisor.quick_advice(rep)
        self._advice_ai_acc = ""
        self._render_advice(self._last_wf, "", True)  # 词频秒出打底
        threading.Thread(target=self._advice_worker,
                         args=(rep, jd, resume, self._current_chain()),
                         daemon=True).start()

    def _advice_worker(self, rep, jd, resume, chain):
        def on_status(s: str):
            self._queue.put({"type": "advice_status", "text": s})

        def on_delta(acc: str):
            self._queue.put({"type": "advice_delta", "acc": acc})

        try:
            result = advisor.generate_advice(
                rep, jd, resume, chain, on_status=on_status, on_delta=on_delta)
        except Exception as e:  # advisor 理论上永不抛；这里只是保险
            result = advisor.AdviceResult(
                advisor.quick_advice(rep), "", "wordfreq",
                f"出错：{e.__class__.__name__}", [str(e)])
        self._queue.put({"type": "advice_done", "result": result})

    def _render_advice(self, wordfreq_text: str, ai_text: str, streaming: bool):
        tb = self.advice_box
        tb.configure(state="normal")
        tb.delete("1.0", "end")
        tb.insert("end", "📄 词频建议（即时打底，永不失败）\n", "wf_head")
        tb.insert("end", wordfreq_text.replace("**", "") + "\n")
        if streaming:
            tb.insert("end", "\n✨ AI 建议（流式生成中……）\n", "ai_head")
            if ai_text:
                tb.insert("end", ai_text)
        elif ai_text:
            tb.insert("end", "\n✨ AI 建议\n", "ai_head")
            tb.insert("end", ai_text)
        tb.configure(state="disabled")

    def _advice_done(self, ev):
        self._busy = False
        self.advice_btn.configure(state="normal")
        result: advisor.AdviceResult = ev["result"]
        self._render_advice(result.wordfreq_text, result.ai_text, False)
        note = "；".join(result.notes) if result.notes else "完成"
        self.advice_status.configure(text=note[:200])
        color = {"cloud": ACCENT, "local": GOOD}.get(result.mode, MUTED)
        self._set_badge(f"改写建议 · {result.badge}", color)

    # ---------------- 招呼语页 ----------------

    def _build_greet_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        self.greet_src_label = ctk.CTkLabel(
            top, text="", anchor="w", text_color=MUTED)
        self.greet_src_label.pack(side="left", padx=6)
        self.greet_btn = ctk.CTkButton(top, height=34, text="生成 3 条招呼语",
                                       command=self.on_greet)
        self.greet_btn.pack(side="left", padx=6)
        self.greet_status = ctk.CTkLabel(top, text="", anchor="w",
                                         text_color=MUTED)
        self.greet_status.pack(side="left", fill="x", expand=True, padx=6)

        self.greet_boxes: list[ctk.CTkTextbox] = []
        for i in range(3):
            row = ctk.CTkFrame(tab, fg_color="transparent")
            row.grid(row=i + 1, column=0, sticky="ew", padx=6, pady=4)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=GREET_STYLE_NAMES[i], anchor="w",
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=ACCENT).grid(
                row=0, column=0, sticky="w", padx=10, pady=(4, 0))
            box = ctk.CTkTextbox(row, height=76)
            box.grid(row=1, column=0, sticky="ew")
            box.configure(state="disabled")
            ctk.CTkButton(row, width=64, height=34, text="复制",
                          command=lambda b=box: self._copy(b)).grid(
                row=1, column=1, padx=(6, 0), sticky="n")
            self.greet_boxes.append(box)

        ctk.CTkLabel(tab, text="招呼语用的是「匹配报告」页里粘贴的 JD 和简历"
                               "——先在那边粘贴好（可点「载入样例」），招呼语才会踩中重合点。",
                     anchor="w", text_color=MUTED).grid(
            row=4, column=0, sticky="ew", padx=8, pady=(4, 0))

    def on_greet(self):
        if self._busy:
            return
        jd = self.jd_box.get("1.0", "end").strip()
        resume = self.resume_box.get("1.0", "end").strip()
        if not jd or not resume:
            self._set_badge("招呼语也依赖 JD 和简历：请先在「匹配报告」页粘贴", AMBER)
            return
        self._busy = True
        self.greet_btn.configure(state="disabled")
        self.greet_status.configure(text="准备中……")
        threading.Thread(target=self._greet_worker,
                         args=(jd, resume, self._current_chain()),
                         daemon=True).start()

    def _greet_worker(self, jd: str, resume: str, chain):
        def on_status(s: str):
            self._queue.put({"type": "status", "text": s})

        try:
            result = greeter.generate_greetings(
                jd, resume, chain, lexicon=self._lexicon, on_status=on_status)
        except Exception as e:  # greeter 理论上永不抛；这里只是保险
            result = greeter.GreetingResult([], "wordfreq",
                                            f"出错：{e.__class__.__name__}",
                                            [str(e)])
        self._queue.put({"type": "done", "result": result})

    def _poll_queue(self):
        try:
            while True:
                ev = self._queue.get_nowait()
                if ev["type"] == "status":
                    self.greet_status.configure(text=ev["text"])
                elif ev["type"] == "advice_status":
                    self.advice_status.configure(text=ev["text"])
                elif ev["type"] == "advice_delta":
                    if len(ev["acc"]) > len(self._advice_ai_acc):
                        self._advice_ai_acc = ev["acc"]
                        self._render_advice(self._last_wf, ev["acc"], True)
                elif ev["type"] == "advice_done":
                    self._advice_done(ev)
                elif ev["type"] == "done":
                    self._greet_done(ev)
        except queue.Empty:
            pass
        self.after(120, self._poll_queue)

    def _greet_done(self, ev):
        self._busy = False
        self.greet_btn.configure(state="normal")
        result: greeter.GreetingResult = ev["result"]
        for i, box in enumerate(self.greet_boxes):
            box.configure(state="normal")
            box.delete("1.0", "end")
            if i < len(result.greetings):
                box.insert("1.0", result.greetings[i])
            box.configure(state="disabled")
        note = "；".join(result.notes) if result.notes else "完成（通过全部校验）"
        self.greet_status.configure(text=note[:200])
        color = {"cloud": ACCENT, "local": GOOD}.get(result.mode, MUTED)
        self._set_badge(f"招呼语 · {result.badge}", color)

    def _copy(self, box: ctk.CTkTextbox):
        text = box.get("1.0", "end").strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._set_badge("已复制到剪贴板", GOOD)

    def _current_chain(self) -> list[ProviderConfig]:
        """全局 AI 供应商 -> 回退链（跟随选择：选本地禁升云）。"""
        s = self._settings
        if s.get("ai_source") == "local":
            selected = builtin_presets()["本地 LM Studio"]
        else:
            selected = resolve_cloud_provider(s)
        local = builtin_presets()["本地 LM Studio"]
        allow = bool(s.get("allow_local_fallback", True))
        return llm_fallback_chain(selected, local, allow)

    def _refresh_source_label(self):
        s = self._settings
        if s.get("ai_source") == "local":
            self.greet_src_label.configure(text="AI 供应商：本地 LM Studio")
        else:
            name = resolve_cloud_provider(s).name
            self.greet_src_label.configure(text=f"AI 供应商：云端（{name}）")

    # ---------------- 设置页 ----------------

    def _build_settings_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(tab, text="AI 供应商").grid(
            row=0, column=0, sticky="e", padx=8, pady=6)
        self.set_source_menu = ctk.CTkOptionMenu(
            tab, values=["云端", "本地 LM Studio"])
        self.set_source_menu.grid(row=0, column=1, sticky="w", padx=8, pady=6)
        ctk.CTkLabel(tab, text="（招呼语与改写建议共用）", text_color=MUTED).grid(
            row=0, column=1, sticky="w", padx=(200, 8))

        ctk.CTkLabel(tab, text="云端预设").grid(
            row=1, column=0, sticky="e", padx=8, pady=6)
        self.set_preset_menu = ctk.CTkOptionMenu(
            tab, values=CLOUD_PRESETS, command=self._on_preset_change)
        self.set_preset_menu.grid(row=1, column=1, sticky="w", padx=8, pady=6)

        ctk.CTkLabel(tab, text="接口地址").grid(
            row=2, column=0, sticky="e", padx=8, pady=6)
        self.set_base_entry = ctk.CTkEntry(tab, placeholder_text="https://.../v1")
        self.set_base_entry.grid(row=2, column=1, sticky="ew", padx=8, pady=6)

        ctk.CTkLabel(tab, text="模型名").grid(
            row=3, column=0, sticky="e", padx=8, pady=6)
        self.set_model_entry = ctk.CTkEntry(tab, placeholder_text="模型 id")
        self.set_model_entry.grid(row=3, column=1, sticky="ew", padx=8, pady=6)

        ctk.CTkLabel(tab, text="API Key").grid(
            row=4, column=0, sticky="e", padx=8, pady=6)
        self.set_key_entry = ctk.CTkEntry(
            tab, show="*", placeholder_text="sk-...（只存本机用户目录）")
        self.set_key_entry.grid(row=4, column=1, sticky="ew", padx=8, pady=6)

        self.set_fallback_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(tab, text="云端失败时允许降级到本地 LM Studio",
                        variable=self.set_fallback_var).grid(
            row=5, column=1, sticky="w", padx=8, pady=6)

        btns = ctk.CTkFrame(tab, fg_color="transparent")
        btns.grid(row=6, column=1, sticky="w", padx=8, pady=6)
        ctk.CTkButton(btns, width=100, text="测试连接",
                      command=self.on_test).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, width=100, text="保存设置",
                      command=self.on_save).pack(side="left")

        self.set_status = ctk.CTkLabel(tab, text="", anchor="w")
        self.set_status.grid(row=7, column=0, columnspan=2, sticky="ew", padx=8)

        note = ("说明：预设模式只需填 key（地址和模型名由程序内置）；"
                "自定义模式三栏都必填。key 保存在本机用户目录"
                "（%APPDATA%\\resume-jd-matcher\\settings.json），不进代码仓库。"
                "也可以改用环境变量 INTERNAI_API_KEY / DEEPSEEK_API_KEY 供预设读取。")
        ctk.CTkLabel(tab, text=note, anchor="w", text_color=MUTED,
                     wraplength=920, justify="left").grid(
            row=8, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 0))

    def _on_preset_change(self, name: str):
        presets = builtin_presets()
        if name in presets:
            self.set_base_entry.delete(0, "end")
            self.set_model_entry.delete(0, "end")
            self.set_base_entry.insert(0, presets[name].base_url)
            self.set_model_entry.insert(0, presets[name].model)

    def _load_settings_into_form(self):
        s = self._settings
        self.set_source_menu.set(
            "本地 LM Studio" if s.get("ai_source") == "local" else "云端")
        preset = s.get("cloud_preset") or "书生·端砚"
        self.set_preset_menu.set(preset if preset in CLOUD_PRESETS else "自定义")
        if preset == "自定义":
            c = s.get("custom") or {}
            self.set_base_entry.insert(0, c.get("base_url", ""))
            self.set_model_entry.insert(0, c.get("model", ""))
            self.set_key_entry.insert(0, c.get("api_key", ""))
        else:
            p = builtin_presets().get(preset)
            if p:
                self.set_base_entry.insert(0, p.base_url)
                self.set_model_entry.insert(0, p.model)
            key = (s.get("keys") or {}).get(preset, "")
            if key:
                self.set_key_entry.insert(0, key)
        self.set_fallback_var.set(bool(s.get("allow_local_fallback", True)))

    def on_save(self):
        preset = self.set_preset_menu.get()
        s = dict(self._settings)
        s["ai_source"] = ("local" if self.set_source_menu.get() == "本地 LM Studio"
                          else "cloud")
        s["cloud_preset"] = preset
        s["allow_local_fallback"] = bool(self.set_fallback_var.get())
        if preset == "自定义":
            s["custom"] = {
                "base_url": self.set_base_entry.get().strip(),
                "model": self.set_model_entry.get().strip(),
                "api_key": self.set_key_entry.get().strip(),
            }
        else:
            keys = dict(s.get("keys") or {})
            keys[preset] = self.set_key_entry.get().strip()
            s["keys"] = keys
        save_settings(s)
        self._settings = s
        self._refresh_source_label()
        self.set_status.configure(text="已保存（本机用户目录 settings.json）")
        self._set_badge("设置已保存")

    def on_test(self):
        preset = self.set_preset_menu.get()
        if preset == "自定义":
            pc = ProviderConfig("自定义云端",
                                self.set_base_entry.get().strip(),
                                self.set_model_entry.get().strip(),
                                api_key=self.set_key_entry.get().strip())
        else:
            pc = resolve_cloud_provider({
                "cloud_preset": preset,
                "keys": {preset: self.set_key_entry.get().strip()},
            })
        self.set_status.configure(text="测试中……")
        ok, why = probe(pc)
        self.set_status.configure(text=("✅ " if ok else "❌ ") + why)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
