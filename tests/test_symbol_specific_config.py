import pytest
import os
from xauusd_bot.config import Config, TradingConfig

def test_symbol_specific_defaults():
    tc = TradingConfig()
    # Gold defaults
    assert tc.get_target_r("XAUUSD") == 2.0
    assert tc.get_breakeven_trigger_r("XAUUSD") == 1.75
    assert tc.get_max_holding_bars("XAUUSD") == 0

    # EURUSD dedicated defaults
    assert tc.get_target_r("EURUSD") == 1.6
    assert tc.get_breakeven_trigger_r("EURUSD") == 1.2
    assert tc.get_max_holding_bars("EURUSD") == 180

def test_symbol_specific_case_insensitivity():
    tc = TradingConfig()
    assert tc.get_target_r("eurusd") == 1.6
    assert tc.get_target_r("EUR_USD") == 1.6
    assert tc.get_target_r("xauusd") == 2.0
    assert tc.get_max_holding_bars("eurusd") == 180
    assert tc.get_max_holding_bars("xauusd") == 0

def test_symbol_specific_env_overrides(monkeypatch):
    monkeypatch.setenv("EUR_TARGET_R", "1.8")
    monkeypatch.setenv("EUR_BREAKEVEN_TRIGGER_R", "1.3")
    monkeypatch.setenv("EUR_MAX_HOLDING_BARS", "240")

    tc = TradingConfig.from_env()
    assert tc.eur_target_r == 1.8
    assert tc.eur_breakeven_trigger_r == 1.3
    assert tc.eur_max_holding_bars == 240

    assert tc.get_target_r("EURUSD") == 1.8
    assert tc.get_breakeven_trigger_r("EURUSD") == 1.3
    assert tc.get_max_holding_bars("EURUSD") == 240

    # Ensure Gold remains unchanged
    assert tc.get_target_r("XAUUSD") == 2.0
    assert tc.get_breakeven_trigger_r("XAUUSD") == 1.75
    assert tc.get_max_holding_bars("XAUUSD") == 0

def test_symbol_specific_min_sl_distance():
    tc = TradingConfig()
    assert tc.get_min_sl_distance("XAUUSD") == 5.0
    assert tc.get_min_sl_distance("EURUSD") == 0.0

def test_symbol_specific_min_sl_distance_env_overrides(monkeypatch):
    monkeypatch.setenv("XAU_MIN_SL_DISTANCE", "6.5")
    monkeypatch.setenv("EUR_MIN_SL_DISTANCE", "0.0005")

    tc = TradingConfig.from_env()
    assert tc.xau_min_sl_distance == 6.5
    assert tc.eur_min_sl_distance == 0.0005
    assert tc.get_min_sl_distance("XAUUSD") == 6.5
    assert tc.get_min_sl_distance("EURUSD") == 0.0005

