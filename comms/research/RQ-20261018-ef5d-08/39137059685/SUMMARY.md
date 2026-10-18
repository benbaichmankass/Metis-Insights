# Stage-0 (synthetic leg) -- squeeze 1h -- fail

FAIL: TRAIL: pooled net_r <= 0 or folds short; TP2: net_r/trade -0.0196 beats the baseline's -0.0044 by -0.0152 < the 0.05 materiality bar; TP15: net_r/trade -0.0248 beats the baseline's -0.0044 by -0.0204 < the 0.05 materiality bar. Thresholds: n_floor 39, pooled_n_floor 88, folds 6 majority 4, breadth 0.5 over >= 2 powered cells, holm alpha 0.05, baseline TRAIL with materiality 0.05 R/trade. Crypto candles are Binance USD-M (PROXY for Bybit linear); the cost stack is the harness's venue-aware fee + slippage + funding. A pass is an input to a re-entry case, never a roster act.

## arm `TRAIL` (baseline) -- fail -- pooled net_r <= 0 or folds short

| cell | n | net R | R/trade | folds+ | maxDD R | R/cap-day | hold d | p (1-sided) | grade |
|---|--:|--:|--:|--:|--:|--:|--:|--:|---|
| BTCUSDT | 1106 | -37.9135 | -0.0343 | 2/6 | 52.8695 | -0.0372 | 0.921 | 0.7971789602577841 | fail |
| ETHUSDT | 1090 | -2.9405 | -0.0027 | 4/6 | 34.6246 | -0.003 | 0.909 | 0.5263550357270823 | fail |
| SOLUSDT | 1016 | 6.7815 | 0.0067 | 3/6 | 27.3015 | 0.0069 | 0.969 | 0.4355322044345796 | fail |
| XRPUSDT | 1076 | -17.8967 | -0.0166 | 1/6 | 58.6083 | -0.0187 | 0.889 | 0.6228894590416966 | fail |
| ADAUSDT | 1018 | -34.646 | -0.034 | 2/6 | 65.0664 | -0.0345 | 0.988 | 0.8153462548681485 | fail |
| AVAXUSDT | 1017 | 58.8282 | 0.0578 | 4/6 | 28.0999 | 0.0613 | 0.943 | 0.08093726039061366 | pass |
| **pooled** | 6323 | -27.791 | -0.0044 | 2/6 | 133.5042 (seq) | - | - | 0.598859458192634 | - |

Holm survivors (alpha 0.05): []

## arm `TP2` (candidate) -- fail -- net_r/trade -0.0196 beats the baseline's -0.0044 by -0.0152 < the 0.05 materiality bar

| cell | n | net R | R/trade | folds+ | maxDD R | R/cap-day | hold d | p (1-sided) | grade |
|---|--:|--:|--:|--:|--:|--:|--:|--:|---|
| BTCUSDT | 1551 | -56.7577 | -0.0366 | 1/6 | 81.3468 | -0.0905 | 0.405 | 0.9461777536977978 | fail |
| ETHUSDT | 1510 | -51.6783 | -0.0342 | 2/6 | 73.3522 | -0.084 | 0.407 | 0.9312622135408539 | fail |
| SOLUSDT | 1461 | 0.531 | 0.0004 | 3/6 | 32.0082 | 0.0009 | 0.418 | 0.4936939867432393 | fail |
| XRPUSDT | 1498 | -53.8277 | -0.0359 | 1/6 | 72.5677 | -0.0885 | 0.406 | 0.9422168378786575 | fail |
| ADAUSDT | 1474 | -32.3154 | -0.0219 | 3/6 | 58.3591 | -0.0519 | 0.422 | 0.8408707598132151 | fail |
| AVAXUSDT | 1428 | 19.1885 | 0.0134 | 4/6 | 18.9755 | 0.0326 | 0.412 | 0.2854281752607298 | pass |
| **pooled** | 8922 | -174.8634 | -0.0196 | 0/6 | 225.206 (seq) | - | - | 0.982093288941349 | - |

Holm survivors (alpha 0.05): []

## arm `TP15` (candidate) -- fail -- net_r/trade -0.0248 beats the baseline's -0.0044 by -0.0204 < the 0.05 materiality bar

| cell | n | net R | R/trade | folds+ | maxDD R | R/cap-day | hold d | p (1-sided) | grade |
|---|--:|--:|--:|--:|--:|--:|--:|--:|---|
| BTCUSDT | 1551 | -55.9981 | -0.0361 | 1/6 | 77.2028 | -0.0926 | 0.39 | 0.9514041250602016 | fail |
| ETHUSDT | 1510 | -61.9536 | -0.041 | 2/6 | 74.2461 | -0.1045 | 0.393 | 0.9695158595554166 | fail |
| SOLUSDT | 1461 | -13.6493 | -0.0093 | 3/6 | 33.0791 | -0.023 | 0.407 | 0.6658864027479224 | fail |
| XRPUSDT | 1499 | -60.4842 | -0.0403 | 0/6 | 77.3182 | -0.1024 | 0.394 | 0.9685882573697908 | fail |
| ADAUSDT | 1474 | -34.5923 | -0.0235 | 2/6 | 57.2877 | -0.057 | 0.412 | 0.8664875013306599 | fail |
| AVAXUSDT | 1428 | 5.1038 | 0.0036 | 4/6 | 18.4565 | 0.0089 | 0.399 | 0.4370079469503744 | pass |
| **pooled** | 8923 | -221.5785 | -0.0248 | 0/6 | 260.3432 (seq) | - | - | 0.9973646883744939 | - |

Holm survivors (alpha 0.05): []

