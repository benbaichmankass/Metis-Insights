| Model | Sym/TF | n | base | AUC | brier_lift | Verdict |
|---|---|---:|---:|---:|---:|---|
| btc-regime-5m-lgbm-flow-v1-offload | BTCUSDT/5m | 15816 | 0.0027 | 0.9487 | -0.00369 | 🟢 TRUSTWORTHY_SIGNAL |
| mes-regime-1d-lgbm-v2 | MES/1d | 2927 | 0.5002 | 0.9195 | 0.1334 | 🟢 TRUSTWORTHY_SIGNAL |
| mes-regime-15m-lgbm-v2 | MES/15m | 14237 | 0.3695 | 0.8827 | 0.09141 | 🟢 TRUSTWORTHY_SIGNAL |
| btc-regime-5m-lgbm-yz-v1 | BTCUSDT/5m | 30023 | 0.0017 | 0.8648 | -0.00425 | 🟢 TRUSTWORTHY_SIGNAL |
| sol-regime-5m-lgbm-v1 | SOLUSDT/5m | 30023 | 0.0108 | 0.8577 | -0.03877 | 🟢 TRUSTWORTHY_SIGNAL |
| btc-regime-5m-baseline-v1 | BTCUSDT/5m | 15816 | 0.0027 | 0.8565 | -4e-05 | 🟢 TRUSTWORTHY_SIGNAL |
| btc-regime-1h-lgbm-yz-v1 | BTCUSDT/1h | 30023 | 0.1954 | 0.8422 | 0.01267 | 🟢 TRUSTWORTHY_SIGNAL |
| btc-regime-15m-lgbm-yz-v1 | BTCUSDT/15m | 30023 | 0.0301 | 0.8412 | -0.03595 | 🟢 TRUSTWORTHY_SIGNAL |
| sol-regime-15m-lgbm-fc-pcv-v1 | SOLUSDT/15m | 22055 | 0.1004 | 0.8342 | 0.01554 | 🟢 TRUSTWORTHY_SIGNAL |
| btc-regime-5m-lgbm-v2 | BTCUSDT/5m | 30023 | 0.0017 | 0.8302 | -0.00475 | 🟢 TRUSTWORTHY_SIGNAL |
| btc-regime-1h-lgbm-v2 | BTCUSDT/1h | 30023 | 0.1954 | 0.8296 | 0.01432 | 🟢 TRUSTWORTHY_SIGNAL |
| btc-regime-15m-lgbm-v2 | BTCUSDT/15m | 30023 | 0.0301 | 0.8289 | -0.03715 | 🟢 TRUSTWORTHY_SIGNAL |
| eth-regime-5m-lgbm-v1 | ETHUSDT/5m | 30023 | 0.0055 | 0.8201 | -0.02617 | 🟢 TRUSTWORTHY_SIGNAL |
| eth-regime-1h-lgbm-v1 | ETHUSDT/1h | 30023 | 0.3366 | 0.8014 | -0.00986 | 🟢 TRUSTWORTHY_SIGNAL |
| btc-regime-1h-lgbm-funding-svble-v1 | BTCUSDT/1h | 30023 | 0.1954 | 0.7901 | 0.02722 | 🟢 TRUSTWORTHY_SIGNAL |
| eth-regime-15m-lgbm-selfonly-v901ctrl | ETHUSDT/15m | 23303 | 0.2415 | 0.7873 | -0.20177 | 🟢 TRUSTWORTHY_SIGNAL |
| btc-regime-15m-lgbm-fc-pcv-v1 | BTCUSDT/15m | 21479 | 0.0381 | 0.7792 | 0.00132 | 🟢 TRUSTWORTHY_SIGNAL |
| mes-regime-5m-baseline-v1 | MES/5m | 16991 | 0.4967 | 0.7226 | 0.042 | 🟢 TRUSTWORTHY_SIGNAL |
| eth-regime-15m-lgbm-xasset-v1 | ETHUSDT/15m | 23303 | 0.2415 | 0.7157 | 0.01365 | 🟢 TRUSTWORTHY_SIGNAL |
| btc-regime-15m-baseline-v1 | BTCUSDT/15m | 21479 | 0.0381 | 0.7116 | 0.00078 | 🟢 TRUSTWORTHY_SIGNAL |
| eth-regime-1h-lgbm-xasset-v1 | ETHUSDT/1h | 30023 | 0.3366 | 0.6969 | -0.04243 | 🟢 TRUSTWORTHY_SIGNAL |
| mes-regime-15m-baseline-v1 | MES/15m | 14237 | 0.3695 | 0.6506 | 0.017 | 🟢 TRUSTWORTHY_SIGNAL |

_🟢 AUC≥0.55 (discriminates) · 🟡 0.45–0.55 (no edge) · 🔴 <0.45 (anti-predictive). A head that fails here should not earn a live-shadow slot; one already shadow-soaking that scores 🔴 is a demotion candidate._
