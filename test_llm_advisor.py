# -*- coding: utf-8 -*-
"""llm_advisor 测试：纯函数 + 离线降级 + 假 SSE 服务器验证流式解析。

不需要真的 LM Studio：流式路径用一个本地假 SSE 服务器覆盖，
且假服务器模拟思考型模型（先 reasoning_content 后 content）和
embedding 模型混在模型列表里的真实情况。
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from llm_advisor import (_build_messages, _looks_like_garbage, _pick_model,
                         _strip_think, quick_advice, stream_llm_advice)
from matcher import load_lexicon, match

LEX = load_lexicon()
JD = "要求：熟悉C++、Python、ROS2、Linux"
RESUME = "技能：C/C++、Python"
REP = match(JD, RESUME, LEX)


def test_quick_advice_mentions_gaps():
    text = quick_advice(REP)
    assert "ROS" in text and "ROS2" in text and "Linux" in text
    assert "词频" in text


def test_quick_advice_weak_covered():
    text = quick_advice(REP)
    assert "只出现 1 次" in text
    assert "C++" in text


def test_build_messages_structure():
    msgs = _build_messages(REP, JD, RESUME)
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert "ROS2" in msgs[1]["content"]
    assert "C/C++" in msgs[1]["content"]


def test_strip_think():
    assert _strip_think("<think>abc</think>结论") == "结论"
    assert _strip_think("开头<think>未闭合") == "开头"
    assert _strip_think("没有标签") == "没有标签"


def test_pick_model_skips_embed():
    models = [{"id": "text-embedding-bge-m3"},
              {"id": "qwen3.5-9b"}]
    assert _pick_model(models) == "qwen3.5-9b"


def test_garbage_detector():
    assert _looks_like_garbage("？" * 30)
    assert _looks_like_garbage("?" * 30)
    assert not _looks_like_garbage("建议补上 ROS2 项目经历，突出导航部分。")
    assert not _looks_like_garbage("短")          # 太短不判废


def test_stream_degrades_when_offline():
    # 端口 9（discard）必然拒绝连接 -> 放弃 AI 层，返回降级标记且不抛异常
    r = stream_llm_advice(REP, JD, RESUME, api_base="http://127.0.0.1:9")
    assert r.mode == "fallback"
    assert r.text == ""


def test_stream_parses_sse_from_fake_server():
    """假 LM Studio：模型列表混入 embedding；流先思考再出正文。"""

    class FakeLMStudio(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"object": "list", "data": [
                {"id": "text-embedding-bge-m3"},
                {"id": "qwen3.5-9b"},
            ]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.end_headers()

            def sse(obj):
                self.wfile.write(("data: " + json.dumps(
                    obj, ensure_ascii=False) + "\n\n").encode("utf-8"))

            # 先思考（reasoning_content），再写正文（content）——真实思考型模型行为
            sse({"choices": [{"delta": {"reasoning_content": "让我想想"}}]})
            sse({"choices": [{"delta": {"reasoning_content": "……嗯"}}]})
            for piece in ["建议一：", "补上 ROS2 项目经历；", "建议二：学 CAN。"]:
                sse({"choices": [{"delta": {"content": piece}}]})
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), FakeLMStudio)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        seen: list[str] = []
        statuses: list[str] = []
        r = stream_llm_advice(REP, JD, RESUME, api_base=base,
                              on_delta=lambda acc: seen.append(acc),
                              on_status=lambda s: statuses.append(s))
        assert r.mode == "llm", r.detail
        assert r.text == "建议一：补上 ROS2 项目经历；建议二：学 CAN。"
        assert "qwen3.5-9b" in r.detail and "embed" not in r.detail
        assert len(seen) >= 3                       # 分批到达：确实是流式
        assert seen[-1] == r.text                   # 回调收到的是累积净化正文
        assert any("思考" in s for s in statuses)   # 思考阶段有状态回报
        assert any("正文" in s for s in statuses)   # 进入正文阶段有状态回报
    finally:
        server.shutdown()


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
