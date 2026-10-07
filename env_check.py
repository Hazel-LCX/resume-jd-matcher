# -*- coding: utf-8 -*-
"""环境自检：确认解释器版本与后续模块需要的第三方包是否就绪。"""
import importlib.util
import sys

print("Python:", sys.version.split()[0])
for m in ("streamlit", "pytest", "requests"):
    state = "OK" if importlib.util.find_spec(m) else "MISSING"
    print(f"{m}: {state}")
