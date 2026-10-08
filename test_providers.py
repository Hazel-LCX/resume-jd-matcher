# -*- coding: utf-8 -*-
"""providers 模块测试：回退链隐私规则、设置持久化、key 解析。全程无网络。"""
import providers
from providers import (  # noqa: F401
    ProviderConfig,
    builtin_presets,
    llm_fallback_chain,
    resolve_cloud_provider,
)

CLOUD = ProviderConfig("云", "https://x/v1", "m1", api_key="k1")
LOCAL = ProviderConfig("本地", "http://127.0.0.1:1234/v1", "m2", is_local=True)


def test_chain_local_never_contains_cloud():
    chain = llm_fallback_chain(LOCAL, CLOUD)
    assert chain == [LOCAL]
    assert all(pc.is_local for pc in chain)


def test_chain_cloud_falls_to_local_by_default():
    assert llm_fallback_chain(CLOUD, LOCAL) == [CLOUD, LOCAL]


def test_chain_cloud_no_fallback_when_disabled():
    assert llm_fallback_chain(CLOUD, LOCAL, allow_local_fallback=False) == [CLOUD]


def test_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    providers.save_settings({"cloud_preset": "DeepSeek", "keys": {"DeepSeek": "k"}})
    assert providers.load_settings()["keys"]["DeepSeek"] == "k"


def test_resolve_preset_prefers_ui_key_over_env(monkeypatch):
    monkeypatch.setenv("INTERNAI_API_KEY", "env-key")
    pc = resolve_cloud_provider(
        {"cloud_preset": "书生·端砚", "keys": {"书生·端砚": "ui-key"}})
    assert pc.api_key == "ui-key"


def test_resolve_preset_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("INTERNAI_API_KEY", "env-key")
    pc = resolve_cloud_provider({"cloud_preset": "书生·端砚"})
    assert pc.api_key == "env-key"


def test_resolve_custom_provider():
    pc = resolve_cloud_provider({
        "cloud_preset": "自定义",
        "custom": {"base_url": "https://a/v1/", "model": "m", "api_key": "k"},
    })
    assert pc.base_url == "https://a/v1"
    assert pc.model == "m" and pc.api_key == "k" and not pc.is_local


def test_builtin_local_flag():
    assert builtin_presets()["本地 LM Studio"].is_local
    assert not builtin_presets()["书生·端砚"].is_local
