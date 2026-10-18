# Stage-0 (synthetic leg) -- trend 1d -- pass

PASS: arm LONG passes (pooled net_r 109.7931); 1 of 4 arms pass. Thresholds: n_floor 20, pooled_n_floor 39, folds 6 majority 4, breadth 0.5 over >= 2 powered cells, holm alpha 0.05, baseline B with materiality 0.1 R/trade. Crypto candles are Binance USD-M (PROXY for Bybit linear); the cost stack is the harness's venue-aware fee + slippage + funding. A pass is an input to a re-entry case, never a roster act.

## arm `B` (baseline) -- pass -- pooled net_r 68.4395 > 0 with 4 folds positive and 4 of 6 powered cells pass (breadth floor 3)

| cell | n | net R | R/trade | folds+ | maxDD R | R/cap-day | hold d | p (1-sided) | grade |
|---|--:|--:|--:|--:|--:|--:|--:|--:|---|
| BTCUSDT | 45 | 18.4636 | 0.4103 | 4/6 | 7.3646 | 0.0102 | 40.267 | 0.10554328916297728 | pass |
| ETHUSDT | 46 | 12.7891 | 0.278 | 3/6 | 10.7864 | 0.0072 | 38.717 | 0.16298372694754648 | fail |
| SOLUSDT | 41 | 3.575 | 0.0872 | 3/6 | 8.9755 | 0.0023 | 38.683 | 0.3549037570285778 | fail |
| XRPUSDT | 43 | 7.9747 | 0.1855 | 4/6 | 12.199 | 0.0054 | 34.488 | 0.3055604398788302 | pass |
| ADAUSDT | 44 | 7.5845 | 0.1724 | 4/6 | 7.7063 | 0.0045 | 38.318 | 0.21257525556795098 | pass |
| AVAXUSDT | 37 | 18.0532 | 0.4879 | 4/6 | 5.5615 | 0.0114 | 42.946 | 0.09680278476694565 | pass |
| **pooled** | 256 | 68.4395 | 0.2673 | 4/6 | 25.2343 (seq) | - | - | 0.014467231449741672 | - |

Holm survivors (alpha 0.05): []

## arm `ADX20` (candidate) -- fail -- net_r/trade 0.3007 beats the baseline's 0.2673 by 0.0334 < the 0.1 materiality bar

| cell | n | net R | R/trade | folds+ | maxDD R | R/cap-day | hold d | p (1-sided) | grade |
|---|--:|--:|--:|--:|--:|--:|--:|--:|---|
| BTCUSDT | 40 | 14.5426 | 0.3636 | 4/6 | 8.4261 | 0.0087 | 41.825 | 0.135328753412288 | pass |
| ETHUSDT | 42 | 6.6169 | 0.1575 | 2/6 | 8.6908 | 0.0041 | 38.167 | 0.28047021116728865 | fail |
| SOLUSDT | 34 | 3.7803 | 0.1112 | 3/6 | 8.7596 | 0.0026 | 43.235 | 0.3405423072200684 | fail |
| XRPUSDT | 31 | 17.1352 | 0.5527 | 4/6 | 5.5847 | 0.0155 | 35.613 | 0.13870418963720432 | pass |
| ADAUSDT | 40 | 6.1687 | 0.1542 | 4/6 | 8.9407 | 0.004 | 38.8 | 0.26190134739720294 | pass |
| AVAXUSDT | 32 | 17.6183 | 0.5506 | 4/6 | 5.5507 | 0.0129 | 42.688 | 0.09692619531239688 | pass |
| **pooled** | 219 | 65.8614 | 0.3007 | 5/6 | 21.9346 (seq) | - | - | 0.013438772647779455 | - |

Holm survivors (alpha 0.05): []

## arm `VOL50` (candidate) -- indeterminate -- pooled n 25 < floor 39: underpowered, never FAIL on thin n

| cell | n | net R | R/trade | folds+ | maxDD R | R/cap-day | hold d | p (1-sided) | grade |
|---|--:|--:|--:|--:|--:|--:|--:|--:|---|
| BTCUSDT | 3 | 3.0842 | 1.0281 | 1/6 | 1.0714 | 0.0176 | 58.333 | 0.21533971323968404 | indeterminate |
| ETHUSDT | 3 | 7.7424 | 2.5808 | 1/6 | 0.0 | 0.0578 | 44.667 | 0.14759151495129608 | indeterminate |
| SOLUSDT | 5 | 2.9859 | 0.5972 | 1/6 | 1.8778 | 0.0267 | 22.4 | 0.2635767092132641 | indeterminate |
| XRPUSDT | 4 | -2.4568 | -0.6142 | 0/6 | 2.4568 | -0.0319 | 19.25 | 0.9178343827587733 | indeterminate |
| ADAUSDT | 4 | 3.7333 | 0.9333 | 1/6 | 1.0531 | 0.0381 | 24.5 | 0.1292817950079557 | indeterminate |
| AVAXUSDT | 6 | 11.5211 | 1.9202 | 1/6 | 3.0758 | 0.1646 | 11.667 | 0.17539241399453648 | indeterminate |
| **pooled** | 25 | 26.61 | 1.0644 | 2/6 | 4.9536 (seq) | - | - | 0.030790943772670883 | - |

Holm survivors (alpha 0.05): []

## arm `LONG` (candidate) -- pass -- pooled net_r 109.7931 > 0 with 4 folds positive and 3 of 6 powered cells pass (breadth floor 3); beats the baseline by 0.241 R/trade

| cell | n | net R | R/trade | folds+ | maxDD R | R/cap-day | hold d | p (1-sided) | grade |
|---|--:|--:|--:|--:|--:|--:|--:|--:|---|
| BTCUSDT | 36 | 19.0082 | 0.528 | 5/6 | 7.9951 | 0.0147 | 35.917 | 0.09935976558339543 | pass |
| ETHUSDT | 37 | 17.4244 | 0.4709 | 3/6 | 6.3174 | 0.016 | 29.514 | 0.08309143421101933 | fail |
| SOLUSDT | 32 | 25.2031 | 0.7876 | 3/6 | 4.6388 | 0.0313 | 25.188 | 0.03847056973262257 | fail |
| XRPUSDT | 38 | 16.76 | 0.4411 | 5/6 | 8.7593 | 0.0289 | 15.263 | 0.1330749854173608 | pass |
| ADAUSDT | 39 | 8.9262 | 0.2289 | 3/6 | 6.9969 | 0.0109 | 21.026 | 0.17578971283821848 | fail |
| AVAXUSDT | 34 | 22.4716 | 0.6609 | 4/6 | 7.7065 | 0.0322 | 20.5 | 0.12415504414376244 | pass |
| **pooled** | 216 | 109.7931 | 0.5083 | 4/6 | 22.7181 (seq) | - | - | 0.000894995498114975 | - |

Holm survivors (alpha 0.05): []

