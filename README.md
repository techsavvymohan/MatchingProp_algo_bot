# MatchingProp_algo_bot

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MetaTrader 5](https://img.shields.io/badge/MetaTrader-5-green.svg)](https://www.metatrader5.com/)
[![Tests](https://img.shields.io/badge/tests-250%20passed-brightgreen.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**MatchingProp_algo_bot** is a multi-symbol, volatility-adaptive algorithmic trading bot engineered for proprietary trading firm challenges (e.g., FTMO, Funding Pips) and live execution on **XAUUSD** (Gold) and **EURUSD** using **MetaTrader 5 (MT5)**.

The bot integrates institutional risk management controls with quantitative momentum, trend, and breakout models adapted from [`je-suis-tm/quant-trading`](https://github.com/je-suis-tm/quant-trading), backed by an advanced 5-layer sideways market detection filter to strictly avoid false breakouts during low-volatility consolidation.

---

## Key Features

### 1. Prop-Firm Risk Engine
- **Daily Loss Budget Protection**: Real-time tracking of realized and unrealized P&L against daily account equity baseline with safety buffers.
- **Maximum Drawdown Killswitch**: Strict enforcement of maximum trailing drawdown thresholds.
- **Dynamic Position Sizing**: Volatility-adjusted lot sizing via ATR (Average True Range) and precise symbol specification normalization (contract size, tick size, tick value).
- **Hard Stop-Loss & Take-Profit Enforcement**: Guaranteed initial bracket orders submitted with every entry.

### 2. Multi-Symbol Concurrency
- **Concurrent Trading**: Simultaneously manages independent trade clusters across **XAUUSD** and **EURUSD**.
- **Per-Symbol Risk & Spread Modeling**: Custom spread thresholds and tick value calculations tailored to gold vs. forex pairs.
- **Currency-Specific News Filters**: Blocks trading during high-impact news releases specific to USD or EUR currencies.

### 3. Sideways Market Avoidance Engine
Strictly eliminates false signals during chop and consolidation via a 5-layer quantitative scoring model:
- **Choppiness Index (CHOP)**: Flags consolidation when reading > 61.8.
- **Average Directional Index (ADX)**: Confirms lack of directional trend when < 22.0.
- **Bollinger Bandwidth Squeeze**: Detects volatility contraction cycles.
- **EMA Ribbon Tangling & Slope**: Analyzes ribbon dispersion and angles across EMA 9, 21, and 50.
- **Heikin-Ashi Indecision Dojis**: Measures candle body compression and dual wicks.

### 4. Quantitative Strategy Engines
- **Awesome Oscillator (AO)**: Median-price 5/34 momentum oscillator featuring Bullish/Bearish Saucer patterns and Zero-Line crossovers.
- **Heikin-Ashi Trend Filtering**: Candlestick noise smoothing with flat bottom (bullish) and flat top (bearish) confirmation.
- **Parabolic SAR (PSAR)**: Dynamic step-acceleration trailing stops and trend reversal detection.
- **Dual Thrust Breakout**: Volatility-adjusted intraday breakout trigger boundaries.

### 5. Smart Trade Management & Pyramiding
- **Controlled Pyramiding**: Scales into winning positions up to a maximum of 4 entries once trades reach designated R-multiples (`PYRAMID_ADD_TRIGGER_R=0.5`).
- **Partial Profit Taking**: Automated partial profit booking at 1R (`PARTIAL_CLOSE_PCT=50%`).
- **Breakeven Stop Activation**: Shifts stop-loss to entry price after favorable excursion to secure risk-free trades.
- **Dynamic Trailing Stops**: Multi-mode trailing via PSAR and Chandelier Volatility stops.
- **24/5 Market Access**: Flexible session scheduling with 24/5 market operations across Asian, London, and New York sessions.

---

## Repository Structure

```
MatchingProp_algo_bot/
├── data/                         # Historical datasets and market data exports
│   ├── backtest_data.json        # Sample backtest dataset
│   └── ...
├── tests/                        # Comprehensive unit and integration test suite
│   ├── test_account.py
│   ├── test_backtest.py
│   ├── test_quant_indicators.py
│   ├── test_risk.py
│   ├── test_sideways_detector.py
│   └── ...
├── xauusd_bot/                   # Core bot package
│   ├── backtesting/              # Backtesting engine, data replay & reporting
│   ├── broker/                   # MetaTrader 5 connector, account & order models
│   ├── calendar/                 # ForexFactory / economic calendar filters
│   ├── indicators/               # ATR, RSI, Chandelier, and Quant indicators (AO, PSAR, etc.)
│   ├── order/                    # Entry, exit, and partial close logic
│   ├── risk/                     # Prop-firm daily loss, max DD, position sizer & pyramiding
│   ├── state/                    # SQLite state persistence & trade logging
│   ├── strategy/                 # Sideways detector, bias detector, triggers, timeframe hierarchy
│   ├── trade/                    # Trade cluster management and lifecycle tracking
│   ├── config.py                 # Configuration parser and schema validation
│   └── main.py                   # Bot application entry point
├── export_jan_jul_2026.py        # MT5 historical data export utility
├── export_july_2026.py           # Single-month export utility
├── generate_backtest_data.py     # Synthetic backtest data generator
├── requirements.txt              # Project dependencies
└── README.md                     # Documentation
```

---

## Installation & Setup

### 1. Prerequisites
- **Python 3.10+** (64-bit recommended)
- **MetaTrader 5 Client Terminal** installed on Windows
- An active demo or live account with an MT5-supported broker or prop firm

### 2. Clone the Repository
```bash
git clone https://github.com/techsavvymohan/MatchingProp_algo_bot.git
cd MatchingProp_algo_bot
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment
Copy the example environment file and customize your settings:
```bash
cp .env.example .env
```

Open `.env` and fill in your MetaTrader 5 credentials and risk limits:
```ini
# MetaTrader 5 Credentials
MT5_LOGIN=12345678
MT5_PASSWORD=YourSecurePassword
MT5_SERVER=YourBroker-Server
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe

# Target Symbols
SYMBOLS=XAUUSD,EURUSD

# Prop Firm Risk Constraints
DAILY_LOSS_LIMIT_PCT=3.0
MAX_DD_LIMIT_PCT=10.0
DAILY_LOSS_BUFFER_PCT=1.0
MAX_DD_BUFFER_PCT=2.0

# Strategy & Filters
ENABLE_SIDEWAYS_FILTER=True
ENABLE_AO_SAUCER=True
ENABLE_HA_FILTER=True
ENABLE_PSAR_TRAILING=True
```

---

## Usage

### Run Live / Demo Bot
Launch the trading bot in live or demo mode:
```bash
python -m xauusd_bot.main
```

### Run Backtesting Engine
Run historical backtests using local bar data:
```bash
python -m xauusd_bot.main --backtest data/backtest_data.json
```

### Generate Sample Backtest Data
Generate synthetic multi-timeframe OHLCV dataset:
```bash
python generate_backtest_data.py
```

### Export Historical Market Data from MT5
Connect to MT5 and download historical rates for backtesting:
```bash
python export_jan_jul_2026.py
```

---

## Testing

The project includes an extensive test suite with 250 unit and integration tests covering indicators, risk constraints, order routing, and strategy triggers:

```bash
pytest
```

---

## Configuration Reference

| Variable | Default | Description |
| :--- | :--- | :--- |
| `SYMBOLS` | `XAUUSD,EURUSD` | Comma-separated list of symbols to trade concurrently |
| `ENABLE_SIDEWAYS_FILTER` | `True` | Activates multi-indicator chop and sideways market filter |
| `SIDEWAYS_CHOP_THRESHOLD` | `61.8` | Choppiness Index threshold above which market is flagged as sideways |
| `SIDEWAYS_ADX_THRESHOLD` | `22.0` | ADX threshold below which market lacks directional momentum |
| `DAILY_LOSS_LIMIT_PCT` | `3.0` | Maximum daily equity loss percentage allowed before killswitch activates |
| `MAX_DD_LIMIT_PCT` | `10.0` | Maximum overall trailing drawdown percentage allowed |
| `MAX_PYRAMID_ENTRIES` | `4` | Maximum concurrent positions scaled into a winning cluster |
| `PYRAMID_ADD_TRIGGER_R` | `0.5` | Profit distance in R-multiples required before adding an entry |
| `PARTIAL_TAKE_PROFIT_R` | `1.0` | R-multiple level at which partial close is triggered |
| `PARTIAL_CLOSE_PCT` | `50.0` | Percentage of position closed at partial take-profit |
| `ENABLE_AO_SAUCER` | `True` | Enables Awesome Oscillator saucer and zero-cross momentum triggers |
| `ENABLE_HA_FILTER` | `True` | Smooths price action via Heikin-Ashi candlestick filtering |
| `ENABLE_PSAR_TRAILING` | `True` | Utilizes Parabolic SAR dynamic step trailing stops |

---

## Disclaimer

This software is for educational, research, and algorithmic development purposes only. Financial trading involves significant risk of loss. Past performance does not guarantee future results. Always test thoroughly in demo environments before deploying capital in live trading or prop firm evaluations.
