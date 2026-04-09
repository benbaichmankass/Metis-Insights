from scripts import print_runtime_profile as prp


def test_print_runtime_profile_outputs_summary(monkeypatch, capsys):
    # Safe Binance testnet dry-run profile
    monkeypatch.setenv("EXCHANGE", "binance")
    monkeypatch.setenv("MODE", "testnet")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "false")
    monkeypatch.setenv("SYMBOL", "BTCUSDT")
    monkeypatch.setenv("BYBIT_API_KEY", "demo_key")
    monkeypatch.setenv("BYBIT_API_SECRET", "demo_secret")
    monkeypatch.setenv("BINANCE_API_KEY", "demo_binance_key")
    monkeypatch.setenv("BINANCE_API_SECRET", "demo_binance_secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "demo_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456789")
    monkeypatch.setenv("TIMEFRAME", "15")
    monkeypatch.setenv("RISK_PER_TRADE", "0.01")
    monkeypatch.setenv("MAX_QTY", "10")

    # Avoid loading a real .env
    monkeypatch.setattr(prp, "load_dotenv", lambda: None)

    prp.main()
    out = capsys.readouterr().out.strip()

    assert "EXCHANGE=binance" in out
    assert "MODE=testnet" in out
    assert "DRY_RUN=true" in out
    assert "SYMBOL=BTCUSDT" in out
