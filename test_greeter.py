# -*- coding: utf-8 -*-
"""greeter 模块测试：解析、四禁令校验、词频保底、假 SSE 服务器全链路。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import greeter
from matcher import load_lexicon, match
from providers import ProviderConfig

LEX = load_lexicon()
JD = "岗位要求：熟悉 Python。"
RESUME = "项目经历：用 Python 做过自动化小工具。"
REP = match(JD, RESUME, LEX)  # covered 应含 Python


# ---------------- 解析 ----------------

def test_parse_json_array_with_surrounding_noise():
    text = ('好的，以下是招呼语：["第一条招呼语内容足够长便于测试通过", '
            '"第二条招呼语内容也足够长便于测试", "第三条同样足够长便于通过测试"]')
    got = greeter.parse_greetings(text)
    assert len(got) == 3 and all(len(g) >= 10 for g in got)


def test_parse_line_fallback_without_json():
    text = "1、第一条招呼语内容足够长便于测试。\n2、第二条招呼语内容也足够长测试。"
    assert len(greeter.parse_greetings(text)) == 2


# ---------------- 校验（四禁令） ----------------

def test_validate_drops_boast_word():
    ok, problems = greeter.validate(
        ["我精通Python，做过相关项目，希望和您进一步沟通机会。"], REP, LEX)
    assert not ok and "夸大" in problems[0]


def test_validate_drops_true_hallucination():
    # MATLAB 不在 JD 也不在简历里：凭空捏造，拦截
    g = "您好，我有Python和MATLAB的开发经验，与岗位要求吻合，想进一步沟通机会。"
    ok, problems = greeter.validate([g], REP, LEX)
    assert not ok and "无出处" in problems[0] and "MATLAB" in problems[0]


def test_validate_allows_jd_skills_even_if_resume_lacks():
    # JD 里要求的技能，招呼语里转述/承认在学：合法，不算编造
    rep = match("岗位要求：熟悉 Python，会 ROS2。", "项目：用 Python 做过小工具。", LEX)
    g = "您好，看到岗位需要Python和ROS2。我Python很熟，ROS2刚开始学，希望有机会加入补齐。"
    ok, problems = greeter.validate([g], rep, LEX)
    assert ok == [g] and not problems


def test_validate_keeps_clean_greeting():
    g = "您好，看到岗位需要Python。我用Python做过自动化小工具，想进一步聊聊。"
    ok, problems = greeter.validate([g], REP, LEX)
    assert ok == [g] and not problems


def test_validate_allows_resume_only_skills():
    # 简历里有、JD 没要求的技能可以提：那是额外证据，不是编造
    rep = match("岗位要求：熟悉 Python。", "项目：基于 STM32 做过小车；熟悉 Python。", LEX)
    g = ("您好，看到岗位需要Python。我做过基于STM32的小车项目，"
         "也熟悉Python，想进一步聊聊。")
    ok, problems = greeter.validate([g], rep, LEX)
    assert ok == [g] and not problems


# ---------------- 词频保底 ----------------

def test_wordfreq_uses_covered_skill_only():
    gs = greeter.wordfreq_greetings(REP)
    assert len(gs) == 3 and all("Python" in g for g in gs)


def test_wordfreq_empty_covered_generic_templates():
    empty = match("岗位要求：会 ROS2。", "简历：暂无相关内容。", LEX)
    assert not empty.covered
    gs = greeter.wordfreq_greetings(empty)
    assert len(gs) == 3


# ---------------- 全链路（假 SSE 服务器） ----------------

class _FakeHandler(BaseHTTPRequestHandler):
    """探活 GET /models + 流式 POST /chat/completions 的最小假服务器。"""

    def do_GET(self):
        body = b'{"data":[{"id":"fake-model"}]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        # 鉴权回归闸：POST 漏带 Authorization 头时这里必须返回 401
        if self.headers.get("Authorization") != "Bearer k":
            body = b'{"error":"unauthorized"}'
            self.send_response(401)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.end_headers()
        first = json.dumps({"choices": [{"delta": {"reasoning_content": "思考"}}]},
                           ensure_ascii=False)
        self.wfile.write(f"data: {first}\n\n".encode("utf-8"))
        parts = [
            '["您好，看到岗位需要Python。我用Python做过自动化小工具，想进一步聊聊。"',
            ',"您好，关于岗位里的Python，我动手实践过，简历已写明，期待有机会沟通。"',
            ',"您好，想请教团队Python技术方案的选型思路？我有实践，希望交流。"]',
        ]
        for p in parts:
            chunk = json.dumps({"choices": [{"delta": {"content": p}}]},
                               ensure_ascii=False)
            self.wfile.write(f"data: {chunk}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *args):
        pass


def _serve() -> tuple[HTTPServer, str]:
    srv = HTTPServer(("127.0.0.1", 0), _FakeHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"


def test_generate_cloud_path_full_chain():
    srv, base = _serve()
    try:
        cloud = ProviderConfig("假云端", base, "fake-model", api_key="k")
        dead_local = ProviderConfig("死本地", "http://127.0.0.1:9/v1", "q",
                                    is_local=True)
        res = greeter.generate_greetings(JD, RESUME, [cloud, dead_local],
                                         lexicon=LEX)
        assert res.mode == "cloud" and "云端模式" in res.badge
        assert len(res.greetings) == 3
        assert all("Python" in g for g in res.greetings)
    finally:
        srv.shutdown()
        srv.server_close()


def test_generate_local_selected_never_upgrades_to_cloud():
    srv, base = _serve()
    try:
        local = ProviderConfig("假本地", base, "fake-model", api_key="k",
                               is_local=True)
        res = greeter.generate_greetings(JD, RESUME, [local], lexicon=LEX)
        assert res.mode == "local" and "本地模式" in res.badge
    finally:
        srv.shutdown()
        srv.server_close()


def test_generate_falls_to_wordfreq_when_all_dead():
    dead_cloud = ProviderConfig("死云", "http://127.0.0.1:9/v1", "m", api_key="k")
    dead_local = ProviderConfig("死本地", "http://127.0.0.1:9/v1", "m",
                                is_local=True)
    res = greeter.generate_greetings(JD, RESUME, [dead_cloud, dead_local],
                                     lexicon=LEX)
    assert res.mode == "wordfreq" and len(res.greetings) == 3
    assert "词频保底" in res.badge
    assert res.notes  # 降级原因要留痕
