# Backtest report: atlas-m2-rolling-origin-v1-delay1

**HISTORICAL BACKTEST: rolling-origin re-forecasts of past days from a frozen data snapshot, refitted at every origin with data through that origin only and scored against the recorded actuals. Not prospective validation.**

The legacy 4.93 % MAPE is a one-step-ahead (h = 1) score with observed lags on one 90-day block (2026-04-02..2026-06-30), from a single fit. It is a different procedure from the recursive forecasts evaluated here; only the legacy-holdout comparison window puts the two side by side on the same target days.

Result categories used in ATLAS:

- `legacy_one_step_holdout`: Legacy one-step holdout: single fit, horizon 1, observed lags.
- `historical_backtest`: Historical recursive multi-horizon backtest: refit at every origin with data through that origin only; horizons 1-30 from the model's own recursion.
- `future_projection`: Future projection: beyond the data, no observed outcome.

- config fingerprint: `26ef670d6da2bff677235a45dbb6ab04a6a30169aa9a372bfa936a98be56ceb4`
- data fingerprint: `5ab8b3a59e107158da9a3a285ea58a22c566f5b0da067e0f3fae6ee5e3d73a4d`
- horizons 1..30; training window expanding; data delay 1 day(s); reference `seasonal_naive`
- provenance (git commit, versions, run time): see `manifest.json`

## Periods

| period | role | origins | first origin | last origin | targets |
|---|---|---|---|---|---|
| development | selection | 486 | 2024-02-01 | 2025-05-31 | 2024-02-02..2025-06-30 |

## Forecast status

| period | model | ok |
|---|---|---|
| development | atlas_linear_regression | 14,580 |
| development | legacy_linear_regression | 14,580 |
| development | naive | 14,580 |
| development | same_weekday_mean_4w | 14,580 |
| development | same_weekday_median_5w | 14,580 |
| development | seasonal_exponential_smoothing | 14,580 |
| development | seasonal_naive | 14,580 |

## Selection (development period, pre-registered rule)

Pre-registered: candidates ['naive', 'seasonal_naive', 'same_weekday_mean_4w', 'same_weekday_median_5w', 'seasonal_exponential_smoothing', 'atlas_linear_regression']; selection period development; MAE over horizons 1..30 on forecasts common to all eligible candidates; failure rate above 0 makes a candidate ineligible; the best regression is selected only if its MAE is at least 3 % below the best baseline's and the DM test (conservative p) is below 0.05; otherwise the best baseline.

| candidate | scheduled | failed | eligible |
|---|---|---|---|
| naive | 14,580 | 0 | yes |
| seasonal_naive | 14,580 | 0 | yes |
| same_weekday_mean_4w | 14,580 | 0 | yes |
| same_weekday_median_5w | 14,580 | 0 | yes |
| seasonal_exponential_smoothing | 14,580 | 0 | yes |
| atlas_linear_regression | 14,580 | 0 | yes |

Ranking on 14,580 forecasts common to every eligible candidate (486 origins):

| rank | model | family | MAE |
|---|---|---|---|
| 1 | atlas_linear_regression | regression | 823.0 |
| 2 | same_weekday_median_5w | baseline | 956.1 |
| 3 | same_weekday_mean_4w | baseline | 996.1 |
| 4 | seasonal_exponential_smoothing | baseline | 1,000.9 |
| 5 | seasonal_naive | baseline | 1,135.0 |
| 6 | naive | baseline | 1,291.3 |

Best regression `atlas_linear_regression` vs best baseline `same_weekday_median_5w`: MAE difference -133.2 (95 % HAC CI -265.3 to -1.1), improvement 13.9 %, DM p (conservative) 0.048; margin met: yes; significant: yes.

**Selected: `atlas_linear_regression`.** atlas_linear_regression beats the best baseline same_weekday_median_5w by at least 3 % in MAE and the selection-period DM test is significant at 0.05.

## development: accuracy by horizon bucket

MAE, RMSE and bias in requests per day (bias = mean of prediction minus actual, with a 95 % HAC interval); WAPE and MAPE in %. `vs SN` is the MAE improvement over seasonal naive on common forecasts, `vs B*` over the best development baseline (`same_weekday_median_5w`); positive = better. `p` = DM p-value (conservative; n/a when too few origins).

| bucket | model | origins | forecasts | failed | MAE | RMSE | bias [95 % CI] | WAPE % | MASE | MAPE % | vs SN % | p | vs B* % | p |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| day_1 | naive | 486 | 486 | 0 | 1,233.2 | 1,664.1 | 0.9 [-147.6, 149.4] | 12.97 | 1.428 | 12.91 | -17.1 | 0.009 | -35.2 | <0.001 |
| day_1 | seasonal_naive | 486 | 486 | 0 | 1,052.8 | 1,551.3 | -6.3 [-144.7, 132.1] | 11.07 | 1.212 | 10.89 | ref |  | -15.4 | 0.005 |
| day_1 | same_weekday_mean_4w | 486 | 486 | 0 | 917.7 | 1,313.6 | -8.0 [-125.2, 109.2] | 9.65 | 1.055 | 9.50 | 12.8 | 0.007 | -0.6 | 0.747 |
| day_1 | same_weekday_median_5w | 486 | 486 | 0 | 912.1 | 1,288.1 | -67.0 [-181.8, 47.7] | 9.59 | 1.047 | 9.41 | 13.4 | 0.005 | B* |  |
| day_1 | seasonal_exponential_smoothing | 486 | 486 | 0 | 877.4 | 1,255.7 | -12.1 [-124.1, 99.9] | 9.23 | 1.011 | 9.06 | 16.7 | <0.001 | 3.8 | 0.399 |
| day_1 | legacy_linear_regression | 486 | 486 | 0 | 875.5 | 1,204.8 | 222.7 [117.1, 328.4] | 9.21 | 1.005 | 9.21 | 16.8 | <0.001 | 4.0 | 0.354 |
| day_1 | atlas_linear_regression | 486 | 486 | 0 | 776.1 | 1,129.5 | -1.3 [-102.1, 99.4] | 8.16 | 0.895 | 7.89 | 26.3 | <0.001 | 14.9 | <0.001 |
| days_2_7 | naive | 486 | 2,916 | 0 | 1,218.6 | 1,700.8 | -12.0 [-164.4, 140.4] | 12.80 | 1.409 | 12.81 | -14.8 | <0.001 | -32.6 | <0.001 |
| days_2_7 | seasonal_naive | 486 | 2,916 | 0 | 1,061.6 | 1,564.8 | -14.7 [-225.5, 196.0] | 11.15 | 1.224 | 10.97 | ref |  | -15.5 | 0.008 |
| days_2_7 | same_weekday_mean_4w | 486 | 2,916 | 0 | 929.1 | 1,325.1 | -16.3 [-255.0, 222.4] | 9.76 | 1.069 | 9.61 | 12.5 | 0.022 | -1.1 | 0.610 |
| days_2_7 | same_weekday_median_5w | 486 | 2,916 | 0 | 919.3 | 1,300.1 | -73.6 [-300.5, 153.3] | 9.65 | 1.057 | 9.47 | 13.4 | 0.008 | B* |  |
| days_2_7 | seasonal_exponential_smoothing | 486 | 2,916 | 0 | 939.6 | 1,354.1 | -20.5 [-208.9, 167.9] | 9.87 | 1.082 | 9.74 | 11.5 | <0.001 | -2.2 | 0.669 |
| days_2_7 | legacy_linear_regression | 486 | 2,916 | 0 | 977.8 | 1,329.7 | 295.7 [49.2, 542.3] | 10.27 | 1.117 | 10.30 | 7.9 | 0.188 | -6.4 | 0.220 |
| days_2_7 | atlas_linear_regression | 486 | 2,916 | 0 | 812.9 | 1,196.1 | 2.7 [-220.4, 225.7] | 8.54 | 0.936 | 8.24 | 23.4 | <0.001 | 11.6 | 0.016 |
| days_8_14 | naive | 486 | 3,402 | 0 | 1,258.0 | 1,776.9 | -30.5 [-211.3, 150.4] | 13.18 | 1.454 | 13.18 | -13.7 | <0.001 | -31.8 | <0.001 |
| days_8_14 | seasonal_naive | 486 | 3,402 | 0 | 1,106.8 | 1,631.6 | -33.9 [-292.3, 224.6] | 11.60 | 1.278 | 11.40 | ref |  | -16.0 | 0.006 |
| days_8_14 | same_weekday_mean_4w | 486 | 3,402 | 0 | 988.6 | 1,384.6 | -35.5 [-355.4, 284.5] | 10.36 | 1.138 | 10.23 | 10.7 | 0.077 | -3.6 | 0.171 |
| days_8_14 | same_weekday_median_5w | 486 | 3,402 | 0 | 954.2 | 1,353.0 | -93.0 [-382.9, 197.0] | 10.00 | 1.099 | 9.82 | 13.8 | 0.006 | B* |  |
| days_8_14 | seasonal_exponential_smoothing | 486 | 3,402 | 0 | 950.9 | 1,403.3 | -39.6 [-267.1, 187.8] | 9.97 | 1.096 | 9.82 | 14.1 | <0.001 | 0.4 | 0.943 |
| days_8_14 | legacy_linear_regression | 486 | 3,402 | 0 | 1,062.5 | 1,437.9 | 339.3 [-23.6, 702.2] | 11.14 | 1.212 | 11.15 | 4.0 | 0.559 | -11.3 | 0.055 |
| days_8_14 | atlas_linear_regression | 486 | 3,402 | 0 | 820.0 | 1,209.7 | 11.3 [-284.1, 306.7] | 8.59 | 0.947 | 8.30 | 25.9 | <0.001 | 14.1 | 0.020 |
| days_15_30 | naive | 486 | 7,776 | 0 | 1,336.7 | 1,845.7 | -80.2 [-333.6, 173.1] | 13.94 | 1.547 | 13.88 | -13.3 | <0.001 | -37.3 | <0.001 |
| days_15_30 | seasonal_naive | 486 | 7,776 | 0 | 1,180.0 | 1,697.5 | -84.1 [-375.7, 207.5] | 12.30 | 1.363 | 12.12 | ref |  | -21.2 | <0.001 |
| days_15_30 | same_weekday_mean_4w | 486 | 7,776 | 0 | 1,029.4 | 1,427.3 | -85.7 [-480.6, 309.2] | 10.73 | 1.186 | 10.53 | 12.8 | 0.001 | -5.7 | 0.018 |
| days_15_30 | same_weekday_median_5w | 486 | 7,776 | 0 | 973.6 | 1,360.2 | -143.3 [-494.0, 207.4] | 10.15 | 1.122 | 9.90 | 17.5 | <0.001 | B* |  |
| days_15_30 | seasonal_exponential_smoothing | 486 | 7,776 | 0 | 1,053.4 | 1,489.3 | -89.8 [-352.1, 172.4] | 10.98 | 1.215 | 10.81 | 10.7 | <0.001 | -8.2 | 0.066 |
| days_15_30 | legacy_linear_regression | 486 | 7,776 | 0 | 1,088.6 | 1,450.6 | 307.2 [-162.9, 777.4] | 11.35 | 1.244 | 11.29 | 7.7 | 0.274 | -11.8 | 0.099 |
| days_15_30 | atlas_linear_regression | 486 | 7,776 | 0 | 831.0 | 1,225.8 | 2.4 [-344.3, 349.0] | 8.66 | 0.962 | 8.35 | 29.6 | 0.001 | 14.6 | 0.086 |
| days_1_30 | naive | 486 | 14,580 | 0 | 1,291.3 | 1,795.6 | -52.3 [-238.8, 134.3] | 13.50 | 1.494 | 13.47 | -13.8 | <0.001 | -35.0 | <0.001 |
| days_1_30 | seasonal_naive | 486 | 14,580 | 0 | 1,135.0 | 1,651.6 | -55.9 [-271.8, 160.0] | 11.87 | 1.310 | 11.68 | ref |  | -18.7 | <0.001 |
| days_1_30 | same_weekday_mean_4w | 486 | 14,580 | 0 | 996.1 | 1,393.7 | -57.5 [-383.0, 268.1] | 10.42 | 1.147 | 10.24 | 12.2 | 0.003 | -4.2 | 0.046 |
| days_1_30 | same_weekday_median_5w | 486 | 14,580 | 0 | 956.1 | 1,344.3 | -115.1 [-403.9, 173.7] | 10.00 | 1.101 | 9.78 | 15.8 | <0.001 | B* |  |
| days_1_30 | seasonal_exponential_smoothing | 486 | 14,580 | 0 | 1,000.9 | 1,435.9 | -61.7 [-251.2, 127.9] | 10.47 | 1.154 | 10.31 | 11.8 | <0.001 | -4.7 | 0.218 |
| days_1_30 | legacy_linear_regression | 486 | 14,580 | 0 | 1,053.3 | 1,416.6 | 309.6 [-109.2, 728.4] | 11.01 | 1.203 | 10.99 | 7.2 | 0.322 | -10.2 | 0.105 |
| days_1_30 | atlas_linear_regression | 486 | 14,580 | 0 | 823.0 | 1,213.1 | 4.4 [-313.2, 322.0] | 8.61 | 0.951 | 8.30 | 27.5 | <0.001 | 13.9 | 0.048 |

### development: MAE by horizon

| h | naive | seasonal_naive | same_weekday_mean_4w | same_weekday_median_5w | seasonal_exponential_smoothing | legacy_linear_regression | atlas_linear_regression |
|---|---|---|---|---|---|---|---|
| 1 | 1,233.2 | 1,052.8 | 917.7 | 912.1 | 877.4 | 875.5 | 776.1 |
| 2 | 1,276.6 | 1,052.8 | 916.4 | 910.9 | 925.1 | 929.8 | 805.2 |
| 3 | 1,313.1 | 1,052.0 | 915.8 | 910.2 | 939.7 | 957.3 | 813.9 |
| 4 | 1,280.0 | 1,054.4 | 917.2 | 912.6 | 932.7 | 970.0 | 814.5 |
| 5 | 1,161.1 | 1,055.5 | 918.2 | 913.3 | 925.1 | 985.3 | 814.6 |
| 6 | 1,055.4 | 1,055.4 | 919.8 | 913.9 | 942.4 | 1,001.8 | 812.3 |
| 7 | 1,225.3 | 1,099.3 | 987.0 | 954.9 | 972.8 | 1,022.5 | 816.9 |
| 8 | 1,356.0 | 1,099.0 | 984.0 | 954.4 | 972.2 | 1,038.0 | 815.8 |
| 9 | 1,318.1 | 1,098.2 | 982.4 | 953.7 | 947.6 | 1,052.5 | 818.7 |
| 10 | 1,324.0 | 1,098.8 | 982.1 | 953.3 | 927.6 | 1,063.5 | 819.7 |
| 11 | 1,308.1 | 1,102.1 | 984.6 | 956.6 | 932.8 | 1,072.1 | 821.4 |
| 12 | 1,195.3 | 1,101.0 | 983.4 | 955.1 | 939.6 | 1,072.5 | 821.6 |
| 13 | 1,098.0 | 1,098.0 | 982.0 | 952.8 | 952.9 | 1,064.0 | 819.0 |
| 14 | 1,206.2 | 1,150.7 | 1,022.1 | 953.7 | 983.5 | 1,074.8 | 823.6 |
| 15 | 1,338.1 | 1,148.8 | 1,019.5 | 951.6 | 1,013.1 | 1,085.2 | 828.0 |
| 16 | 1,369.6 | 1,148.4 | 1,017.8 | 950.4 | 1,014.1 | 1,088.0 | 830.5 |
| 17 | 1,383.4 | 1,146.7 | 1,015.6 | 948.7 | 1,022.0 | 1,091.0 | 830.4 |
| 18 | 1,379.6 | 1,147.5 | 1,015.4 | 949.5 | 1,021.0 | 1,086.3 | 830.6 |
| 19 | 1,247.1 | 1,143.1 | 1,011.3 | 945.8 | 1,007.0 | 1,090.9 | 829.7 |
| 20 | 1,140.9 | 1,140.9 | 1,010.7 | 944.2 | 997.3 | 1,095.9 | 827.8 |
| 21 | 1,220.9 | 1,173.7 | 1,018.2 | 968.2 | 1,015.5 | 1,098.0 | 827.7 |
| 22 | 1,365.5 | 1,173.5 | 1,015.4 | 967.9 | 1,057.0 | 1,100.7 | 827.0 |
| 23 | 1,394.7 | 1,179.1 | 1,019.7 | 971.6 | 1,073.6 | 1,099.4 | 826.5 |
| 24 | 1,419.2 | 1,185.3 | 1,027.2 | 978.8 | 1,071.6 | 1,097.4 | 829.7 |
| 25 | 1,442.0 | 1,193.1 | 1,034.1 | 986.6 | 1,060.3 | 1,095.3 | 832.4 |
| 26 | 1,265.3 | 1,194.2 | 1,035.1 | 987.4 | 1,054.2 | 1,090.2 | 834.6 |
| 27 | 1,192.1 | 1,192.1 | 1,034.6 | 985.9 | 1,068.6 | 1,083.7 | 835.4 |
| 28 | 1,320.0 | 1,236.8 | 1,066.3 | 1,013.0 | 1,120.8 | 1,076.2 | 835.1 |
| 29 | 1,437.0 | 1,237.3 | 1,064.1 | 1,013.4 | 1,132.4 | 1,072.0 | 835.0 |
| 30 | 1,471.7 | 1,239.5 | 1,065.1 | 1,013.8 | 1,126.0 | 1,068.3 | 834.8 |

## Where a regression does worse than a baseline

Every bucket and period in which a regression's MAE on common forecasts is higher than a baseline's (exploratory, unadjusted p-values).

| period | bucket | regression | baseline | forecasts | MAE | baseline MAE | improvement % | p |
|---|---|---|---|---|---|---|---|---|
| development | days_2_7 | legacy_linear_regression | same_weekday_mean_4w | 2,916 | 977.8 | 929.1 | -5.2 | 0.335 |
| development | days_8_14 | legacy_linear_regression | same_weekday_mean_4w | 3,402 | 1,062.5 | 988.6 | -7.5 | 0.209 |
| development | days_15_30 | legacy_linear_regression | same_weekday_mean_4w | 7,776 | 1,088.6 | 1,029.4 | -5.8 | 0.372 |
| development | days_1_30 | legacy_linear_regression | same_weekday_mean_4w | 14,580 | 1,053.3 | 996.1 | -5.7 | 0.325 |
| development | days_2_7 | legacy_linear_regression | same_weekday_median_5w | 2,916 | 977.8 | 919.3 | -6.4 | 0.220 |
| development | days_8_14 | legacy_linear_regression | same_weekday_median_5w | 3,402 | 1,062.5 | 954.2 | -11.3 | 0.055 |
| development | days_15_30 | legacy_linear_regression | same_weekday_median_5w | 7,776 | 1,088.6 | 973.6 | -11.8 | 0.099 |
| development | days_1_30 | legacy_linear_regression | same_weekday_median_5w | 14,580 | 1,053.3 | 956.1 | -10.2 | 0.105 |
| development | days_2_7 | legacy_linear_regression | seasonal_exponential_smoothing | 2,916 | 977.8 | 939.6 | -4.1 | 0.524 |
| development | days_8_14 | legacy_linear_regression | seasonal_exponential_smoothing | 3,402 | 1,062.5 | 950.9 | -11.7 | 0.092 |
| development | days_15_30 | legacy_linear_regression | seasonal_exponential_smoothing | 7,776 | 1,088.6 | 1,053.4 | -3.3 | 0.587 |
| development | days_1_30 | legacy_linear_regression | seasonal_exponential_smoothing | 14,580 | 1,053.3 | 1,000.9 | -5.2 | 0.424 |

## Sensitivity analyses (pre-registered, descriptive)

### target_aligned

Only targets reachable at every horizon, so all horizons share one set of days.

| period | bucket | model | forecasts | MAE | vs best baseline % | p |
|---|---|---|---|---|---|---|
| development | day_1 | naive | 457 | 1,245.5 | -34.4 | <0.001 |
| development | days_2_7 | naive | 2,742 | 1,241.1 | -32.9 | <0.001 |
| development | days_8_14 | naive | 3,199 | 1,276.4 | -31.9 | <0.001 |
| development | days_15_30 | naive | 7,312 | 1,358.5 | -37.9 | <0.001 |
| development | days_1_30 | naive | 13,710 | 1,312.1 | -35.5 | <0.001 |
| development | day_1 | seasonal_naive | 457 | 1,081.6 | -16.7 | 0.003 |
| development | days_2_7 | seasonal_naive | 2,742 | 1,088.2 | -16.6 | 0.006 |
| development | days_8_14 | seasonal_naive | 3,199 | 1,128.4 | -16.6 | 0.005 |
| development | days_15_30 | seasonal_naive | 7,312 | 1,194.4 | -21.3 | <0.001 |
| development | days_1_30 | seasonal_naive | 13,710 | 1,154.0 | -19.1 | <0.001 |
| development | day_1 | same_weekday_mean_4w | 457 | 931.2 | -0.5 | 0.805 |
| development | days_2_7 | same_weekday_mean_4w | 2,742 | 941.6 | -0.9 | 0.640 |
| development | days_8_14 | same_weekday_mean_4w | 3,199 | 998.8 | -3.2 | 0.190 |
| development | days_15_30 | same_weekday_mean_4w | 7,312 | 1,041.9 | -5.8 | 0.013 |
| development | days_1_30 | same_weekday_mean_4w | 13,710 | 1,008.1 | -4.1 | 0.035 |
| development | day_1 | seasonal_exponential_smoothing | 457 | 903.8 | 2.5 | 0.596 |
| development | days_2_7 | seasonal_exponential_smoothing | 2,742 | 965.2 | -3.4 | 0.538 |
| development | days_8_14 | seasonal_exponential_smoothing | 3,199 | 967.9 | -0.1 | 0.988 |
| development | days_15_30 | seasonal_exponential_smoothing | 7,312 | 1,072.7 | -8.9 | 0.051 |
| development | days_1_30 | seasonal_exponential_smoothing | 13,710 | 1,021.1 | -5.4 | 0.216 |
| development | day_1 | legacy_linear_regression | 457 | 885.5 | 4.4 | 0.322 |
| development | days_2_7 | legacy_linear_regression | 2,742 | 982.3 | -5.2 | 0.316 |
| development | days_8_14 | legacy_linear_regression | 3,199 | 1,065.0 | -10.1 | 0.091 |
| development | days_15_30 | legacy_linear_regression | 7,312 | 1,099.5 | -11.6 | 0.117 |
| development | days_1_30 | legacy_linear_regression | 13,710 | 1,060.9 | -9.5 | 0.146 |
| development | day_1 | atlas_linear_regression | 457 | 801.9 | 13.5 | <0.001 |
| development | days_2_7 | atlas_linear_regression | 2,742 | 835.3 | 10.5 | 0.034 |
| development | days_8_14 | atlas_linear_regression | 3,199 | 839.6 | 13.2 | 0.039 |
| development | days_15_30 | atlas_linear_regression | 7,312 | 841.5 | 14.6 | 0.113 |
| development | days_1_30 | atlas_linear_regression | 13,710 | 838.5 | 13.4 | 0.083 |


## Fit diagnostics

| model | period | fits | failed | train rows | rank | rank-deficient fits | restricted cond. (median / max) | max train leverage | non-estimable | high leverage | clipped |
|---|---|---|---|---|---|---|---|---|---|---|---|
| atlas_linear_regression | development | 486 | 0 | 368..853 | 18..18 of 18 | 0 | 6.7 / 7.3 | 0.259 | 0 | 0 | 0 |
| legacy_linear_regression | development | 486 | 0 | 368..853 | 28..29 of 32 | 486 | 40.4 / 19,722.9 | 1.000 | 496 | 89 | 0 |

Regression MAE with and without non-estimable or high-leverage forecasts:

| period | model | forecasts | MAE | non-estimable | high leverage | kept | MAE kept |
|---|---|---|---|---|---|---|---|
| development | atlas_linear_regression | 14,580 | 823.0 | 0 | 0 | 14,580 | 823.0 |
| development | legacy_linear_regression | 14,580 | 1,053.3 | 495 | 87 | 13,998 | 995.5 |

## Caveats

- All numbers are historical re-forecasts from one frozen snapshot (`legacy-v0`, a single final data vintage). Values of recent days may have been revised after each origin, which favours methods that lean on the latest days; the size of this effect is unknown.
- Neither period is an untouched prospective sample: the whole series was examined by the original author and in M0/M1 before any model was specified, and the confirmation period contains the legacy holdout.
- Forecasts from neighbouring origins share target days. Counts of forecasts overstate the information; p-values use HAC variances and are indicative. Selection-period p-values are optimistic because the selection was made on the same data.
- MASE below 1 is not skill against seasonal naive (its scale is the in-sample one-step error); use the improvement columns, which compare the same forecasts.
- MAPE is secondary and not comparable with the legacy 4.93 % except in the legacy-holdout window table, where target days and formula are the same.
