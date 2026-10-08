# -*- coding: utf-8 -*-
"""假服务器测试的卫生设施。

多个测试文件各自起 HTTPServer 假流式服务器，且 providers/llm_advisor 的
requests.Session 是模块级共享的：keep-alive 旧连接 + 端口 TIME_WAIT 串扰
会导致"单跑全过、全量随机挂"。此夹具在每个测试结束后把两个共享 Session
的连接池换成全新 adapter，斩断测试间状态。
"""
import pytest
from requests.adapters import HTTPAdapter


@pytest.fixture(autouse=True)
def fresh_http_pools():
    yield
    import llm_advisor
    import providers
    for session in (providers._SESSION, llm_advisor._LOCAL_SESSION):
        session.mount("http://", HTTPAdapter())
        session.mount("https://", HTTPAdapter())
