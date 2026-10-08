# -*- coding: utf-8 -*-
"""advisor 模块测试：词频建议、双层结构、回退链、假 SSE 服务器全链路。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import advisor
from matcher import load_lexicon, match
from providers import ProviderConfig

LEX = load_lexicon()
JD = "岗位要求：熟悉 Python，会 ROS2。"
RESUME = "项目经历：用 Python 做过自动化小工具。"
REP = match(JD, RESUME, LEX)  # covered: Python；gaps: ROS2


# ---------------- 词频建议 ----------------

def test_quick_advice_mentions_gaps():
    text = advisor.quick_advice(REP)
    assert "缺口" in text and "ROS2" in text


def test_quick_advice_all_covered():
    rep = match("岗位要求：熟悉 Python。", "用过 Python，也教过 Python。", LEX)
    assert "无需改写" in advisor.quick_advice(rep)


# ---------------- 双层结构与回退 ----------------

def test_generate_advice_wordfreq_fallback_all_dead():
    dead = ProviderConfig("死云", "http://127.0.0.1:9/v1", "m", api_key="k")
    res = advisor.generate_advice(REP, JD, RESUME, [dead])
    assert res.mode == "wordfreq" and res.ai_text == ""
    assert res.wordfreq_text and "词频" in res.badge
    assert res.notes  # 降级原因留痕


def test_generate_advice_double_layer_with_fake_server():
    srv = HTTPServer(("127.0.0.1", 0), _AuthHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cloud = ProviderConfig("假云端",
                               f"http://127.0.0.1:{srv.server_address[1]}/v1",
                               "fake-model", api_key="k")
        res = advisor.generate_advice(REP, JD, RESUME, [cloud])
        assert res.mode == "cloud" and "云端模式" in res.badge
        assert res.wordfreq_text and res.ai_text  # 双层：词频打底 + AI 建议
        assert res.ai_text.lstrip().startswith("-")
    finally:
        srv.shutdown()
        srv.server_close()


def test_generate_advice_local_selected_badge():
    srv = HTTPServer(("127.0.0.1", 0), _AuthHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        local = ProviderConfig("假本地",
                               f"http://127.0.0.1:{srv.server_address[1]}/v1",
                               "fake-model", api_key="k", is_local=True)
        res = advisor.generate_advice(REP, JD, RESUME, [local])
        assert res.mode == "local" and "本地模式" in res.badge
    finally:
        srv.shutdown()
        srv.server_close()


class _AuthHandler(BaseHTTPRequestHandler):
    """探活 + 流式建议的最小假服务器；POST 强制校验鉴权头。"""

    def do_GET(self):
        body = b'{"data":[{"id":"fake-model"}]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.headers.get("Authorization") != "Bearer k":
            self.send_response(401)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.end_headers()
        chunks = [
            "- 若用过 ROS2：把项目经历改写成明确包含 ROS2 的句子，"
            "如「基于 ROS2 完成导航节点联调」；",
            "\n- 若未掌握 ROS2：先跑通 turtlesim 仿真熟悉话题机制，"
            "再写一个订阅 /cmd_vel 的最小节点练手。",
        ]
        for c in chunks:
            payload = json.dumps({"choices": [{"delta": {"content": c}}]},
                                 ensure_ascii=False)
            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *args):
        pass
