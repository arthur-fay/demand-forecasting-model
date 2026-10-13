# Backtest report: atlas-m2-rolling-origin-v1

**HISTORICAL BACKTEST: rolling-origin re-forecasts of past days from a frozen data snapshot, refitted at every origin with data through that origin only and scored against the recorded actuals. Not prospective validation.**

The legacy 4.93 % MAPE is a one-step-ahead (h = 1) score with observed lags on one 90-day block (2026-04-02..2026-06-30), from a single fit. It is a different procedure from the recursive forecasts evaluated here; only the legacy-holdout comparison window puts the two side by side on the same target days.

Result categories used in ATLAS:

- `legacy_one_step_holdout`: Legacy one-step holdout: single fit, horizon 1, observed lags.
- `historical_backtest`: Historical recursive multi-horizon backtest: refit at every origin with data through that origin only; horizons 1-30 from the model's own recursion.
- `future_projection`: Future projection: beyond the data, no observed outcome.

- config fingerprint: `78da64e8c2ce75d9318cf855137d015e9692dbd50517a70d00243d7f731b1753`
- data fingerprint: `5ab8b3a59e107158da9a3a285ea58a22c566f5b0da067e0f3fae6ee5e3d73a4d`
- horizons 1..30; training window expanding; data delay 0 day(s); reference `seasonal_naive`
- provenance (git commit, versions, run time): see `manifest.json`

## Periods

| period | role | origins | first origin | last origin | targets |
|---|---|---|---|---|---|
| development | selection | 486 | 2024-02-01 | 2025-05-31 | 2024-02-02..2025-06-30 |
| confirmation | confirmation | 336 | 2025-06-30 | 2026-05-31 | 2025-07-01..2026-06-30 |
| legacy_window | descriptive | 119 | 2026-03-03 | 2026-06-29 | 2026-03-04..2026-07-29 |

## Forecast status

| period | model | beyond_snapshot | ok |
|---|---|---|---|
| confirmation | atlas_linear_regression | 0 | 10,080 |
| confirmation | legacy_linear_regression | 0 | 10,080 |
| confirmation | naive | 0 | 10,080 |
| confirmation | same_weekday_mean_4w | 0 | 10,080 |
| confirmation | same_weekday_median_5w | 0 | 10,080 |
| confirmation | seasonal_exponential_smoothing | 0 | 10,080 |
| confirmation | seasonal_naive | 0 | 10,080 |
| development | atlas_linear_regression | 0 | 14,580 |
| development | legacy_linear_regression | 0 | 14,580 |
| development | naive | 0 | 14,580 |
| development | same_weekday_mean_4w | 0 | 14,580 |
| development | same_weekday_median_5w | 0 | 14,580 |
| development | seasonal_exponential_smoothing | 0 | 14,580 |
| development | seasonal_naive | 0 | 14,580 |
| legacy_window | atlas_linear_regression | 435 | 3,135 |
| legacy_window | legacy_linear_regression | 435 | 3,135 |
| legacy_window | naive | 435 | 3,135 |
| legacy_window | same_weekday_mean_4w | 435 | 3,135 |
| legacy_window | same_weekday_median_5w | 435 | 3,135 |
| legacy_window | seasonal_exponential_smoothing | 435 | 3,135 |
| legacy_window | seasonal_naive | 435 | 3,135 |

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
| 1 | atlas_linear_regression | regression | 817.9 |
| 2 | same_weekday_median_5w | baseline | 953.0 |
| 3 | seasonal_exponential_smoothing | baseline | 989.1 |
| 4 | same_weekday_mean_4w | baseline | 990.9 |
| 5 | seasonal_naive | baseline | 1,129.1 |
| 6 | naive | baseline | 1,272.3 |

Best regression `atlas_linear_regression` vs best baseline `same_weekday_median_5w`: MAE difference -135.1 (95 % HAC CI, conservative, -266.4 to -3.8), improvement 14.2 %, DM p (conservative) 0.044; margin met: yes; significant: yes.

**Selected: `atlas_linear_regression`.** atlas_linear_regression beats the best baseline same_weekday_median_5w by at least 3 % in MAE and the selection-period DM test is significant at 0.05.

The selection was sealed before this run; recomputing it from this run's development records reproduces it in every decision field: yes.

## Recommendation (confirmation period `confirmation`)

**Recommended: `atlas_linear_regression`.** Confirmed: atlas_linear_regression is not worse than same_weekday_median_5w on the confirmation period.

Holm adjustment over the primary family; every other test is exploratory and unadjusted.

| hypothesis | model | vs | MAE diff | 95 % CI (conservative) | improvement % | DM p (conservative) | Holm p |
|---|---|---|---|---|---|---|---|
| H1: best development regression vs best development baseline | atlas_linear_regression | same_weekday_median_5w | -127.3 | -256.4 to 1.9 | 11.4 | 0.054 | 0.054 |
| H2: selected model vs the seasonal-naive reference | atlas_linear_regression | seasonal_naive | -386.6 | -645.0 to -128.2 | 28.2 | 0.003 | 0.007 |

## development: accuracy by horizon bucket

MAE, RMSE and bias in requests per day (bias = mean of prediction minus actual, with a 95 % HAC interval from the larger of the HLN and Bartlett-2h standard errors); WAPE and MAPE in %. `vs SN` is the MAE improvement over seasonal naive on common forecasts, `vs B*` over the best development baseline (`same_weekday_median_5w`); positive = better. `p` = DM p-value (conservative; n/a when too few origins).

| bucket | model | origins | forecasts | failed | MAE | RMSE | bias [95 % CI] | WAPE % | MASE | MAPE % | vs SN % | p | vs B* % | p |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| day_1 | naive | 486 | 486 | 0 | 900.7 | 1,300.8 | 0.2 [-115.8, 116.3] | 9.47 | 1.045 | 9.41 | 14.4 | 0.014 | 1.2 | 0.816 |
| day_1 | seasonal_naive | 486 | 486 | 0 | 1,052.8 | 1,551.3 | -6.3 [-193.9, 181.3] | 11.07 | 1.211 | 10.89 | ref |  | -15.4 | 0.005 |
| day_1 | same_weekday_mean_4w | 486 | 486 | 0 | 917.7 | 1,313.6 | -8.0 [-169.8, 153.8] | 9.65 | 1.054 | 9.50 | 12.8 | 0.007 | -0.6 | 0.747 |
| day_1 | same_weekday_median_5w | 486 | 486 | 0 | 912.1 | 1,288.1 | -67.0 [-224.8, 90.7] | 9.59 | 1.046 | 9.41 | 13.4 | 0.005 | B* |  |
| day_1 | seasonal_exponential_smoothing | 486 | 486 | 0 | 768.0 | 1,116.1 | -11.3 [-123.9, 101.4] | 8.08 | 0.886 | 7.95 | 27.1 | <0.001 | 15.8 | <0.001 |
| day_1 | legacy_linear_regression | 486 | 486 | 0 | 736.0 | 1,044.5 | 151.7 [52.2, 251.2] | 7.74 | 0.848 | 7.72 | 30.1 | <0.001 | 19.3 | <0.001 |
| day_1 | atlas_linear_regression | 486 | 486 | 0 | 663.9 | 974.1 | -0.5 [-95.4, 94.3] | 6.98 | 0.768 | 6.80 | 36.9 | <0.001 | 27.2 | <0.001 |
| days_2_7 | naive | 486 | 2,916 | 0 | 1,219.9 | 1,683.2 | -12.7 [-129.9, 104.6] | 12.81 | 1.410 | 12.80 | -15.7 | <0.001 | -33.7 | <0.001 |
| days_2_7 | seasonal_naive | 486 | 2,916 | 0 | 1,054.2 | 1,552.0 | -13.9 [-204.5, 176.8] | 11.07 | 1.214 | 10.90 | ref |  | -15.5 | 0.012 |
| days_2_7 | same_weekday_mean_4w | 486 | 2,916 | 0 | 917.6 | 1,313.9 | -15.9 [-246.2, 214.4] | 9.64 | 1.055 | 9.49 | 13.0 | 0.022 | -0.6 | 0.797 |
| days_2_7 | same_weekday_median_5w | 486 | 2,916 | 0 | 912.4 | 1,288.6 | -72.2 [-292.1, 147.8] | 9.58 | 1.048 | 9.40 | 13.4 | 0.012 | B* |  |
| days_2_7 | seasonal_exponential_smoothing | 486 | 2,916 | 0 | 923.9 | 1,326.9 | -19.4 [-184.1, 145.4] | 9.70 | 1.063 | 9.55 | 12.4 | <0.001 | -1.3 | 0.806 |
| days_2_7 | legacy_linear_regression | 486 | 2,916 | 0 | 953.5 | 1,298.2 | 279.4 [49.9, 508.8] | 10.01 | 1.091 | 10.04 | 9.5 | 0.114 | -4.5 | 0.386 |
| days_2_7 | atlas_linear_regression | 486 | 2,916 | 0 | 806.3 | 1,183.9 | 2.6 [-211.3, 216.4] | 8.47 | 0.929 | 8.18 | 23.5 | <0.001 | 11.6 | 0.015 |
| days_8_14 | naive | 486 | 3,402 | 0 | 1,260.1 | 1,783.3 | -31.1 [-211.0, 148.7] | 13.21 | 1.457 | 13.23 | -14.6 | <0.001 | -32.1 | <0.001 |
| days_8_14 | seasonal_naive | 486 | 3,402 | 0 | 1,099.2 | 1,627.1 | -33.1 [-271.4, 205.1] | 11.52 | 1.269 | 11.30 | ref |  | -15.2 | 0.011 |
| days_8_14 | same_weekday_mean_4w | 486 | 3,402 | 0 | 982.7 | 1,377.6 | -35.1 [-347.6, 277.4] | 10.30 | 1.131 | 10.17 | 10.6 | 0.102 | -3.0 | 0.276 |
| days_8_14 | same_weekday_median_5w | 486 | 3,402 | 0 | 954.1 | 1,356.5 | -91.8 [-375.7, 192.1] | 10.00 | 1.098 | 9.82 | 13.2 | 0.011 | B* |  |
| days_8_14 | seasonal_exponential_smoothing | 486 | 3,402 | 0 | 948.8 | 1,404.4 | -38.5 [-242.8, 165.7] | 9.94 | 1.093 | 9.81 | 13.7 | <0.001 | 0.6 | 0.907 |
| days_8_14 | legacy_linear_regression | 486 | 3,402 | 0 | 1,055.3 | 1,431.0 | 337.0 [-16.0, 690.1] | 11.06 | 1.204 | 11.08 | 4.0 | 0.567 | -10.6 | 0.064 |
| days_8_14 | atlas_linear_regression | 486 | 3,402 | 0 | 819.5 | 1,208.4 | 10.9 [-282.0, 303.8] | 8.59 | 0.946 | 8.29 | 25.4 | <0.001 | 14.1 | 0.017 |
| days_15_30 | naive | 486 | 7,776 | 0 | 1,320.5 | 1,830.9 | -80.9 [-320.9, 159.1] | 13.77 | 1.528 | 13.70 | -12.4 | 0.001 | -36.1 | <0.001 |
| days_15_30 | seasonal_naive | 486 | 7,776 | 0 | 1,175.1 | 1,691.3 | -83.5 [-362.5, 195.6] | 12.25 | 1.357 | 12.06 | ref |  | -21.1 | <0.001 |
| days_15_30 | same_weekday_mean_4w | 486 | 7,776 | 0 | 1,026.6 | 1,425.6 | -85.3 [-472.7, 302.0] | 10.70 | 1.182 | 10.50 | 12.6 | 0.002 | -5.8 | 0.017 |
| days_15_30 | same_weekday_median_5w | 486 | 7,776 | 0 | 970.2 | 1,357.2 | -142.3 [-486.2, 201.7] | 10.12 | 1.118 | 9.87 | 17.4 | <0.001 | B* |  |
| days_15_30 | seasonal_exponential_smoothing | 486 | 7,776 | 0 | 1,045.1 | 1,480.9 | -88.8 [-336.2, 158.6] | 10.90 | 1.205 | 10.73 | 11.1 | <0.001 | -7.7 | 0.085 |
| days_15_30 | legacy_linear_regression | 486 | 7,776 | 0 | 1,088.4 | 1,451.7 | 310.2 [-158.8, 779.3] | 11.35 | 1.243 | 11.29 | 7.4 | 0.302 | -12.2 | 0.094 |
| days_15_30 | atlas_linear_regression | 486 | 7,776 | 0 | 831.1 | 1,225.8 | 2.3 [-342.7, 347.3] | 8.67 | 0.962 | 8.35 | 29.3 | <0.001 | 14.3 | 0.086 |
| days_1_30 | naive | 486 | 14,580 | 0 | 1,272.3 | 1,775.6 | -52.9 [-225.9, 120.1] | 13.30 | 1.472 | 13.27 | -12.7 | 0.001 | -33.5 | <0.001 |
| days_1_30 | seasonal_naive | 486 | 14,580 | 0 | 1,129.1 | 1,644.8 | -55.2 [-259.1, 148.7] | 11.81 | 1.303 | 11.61 | ref |  | -18.5 | <0.001 |
| days_1_30 | same_weekday_mean_4w | 486 | 14,580 | 0 | 990.9 | 1,389.1 | -57.2 [-373.3, 259.0] | 10.36 | 1.141 | 10.19 | 12.2 | 0.004 | -4.0 | 0.053 |
| days_1_30 | same_weekday_median_5w | 486 | 14,580 | 0 | 953.0 | 1,341.3 | -113.9 [-394.4, 166.5] | 9.96 | 1.097 | 9.75 | 15.6 | <0.001 | B* |  |
| days_1_30 | seasonal_exponential_smoothing | 486 | 14,580 | 0 | 989.1 | 1,422.5 | -60.6 [-236.7, 115.5] | 10.34 | 1.140 | 10.19 | 12.4 | <0.001 | -3.8 | 0.308 |
| days_1_30 | legacy_linear_regression | 486 | 14,580 | 0 | 1,041.9 | 1,405.4 | 305.0 [-104.2, 714.3] | 10.90 | 1.190 | 10.87 | 7.7 | 0.286 | -9.3 | 0.125 |
| days_1_30 | atlas_linear_regression | 486 | 14,580 | 0 | 817.9 | 1,205.8 | 4.3 [-306.9, 315.4] | 8.55 | 0.945 | 8.25 | 27.6 | <0.001 | 14.2 | 0.044 |

### development: MAE by horizon

| h | naive | seasonal_naive | same_weekday_mean_4w | same_weekday_median_5w | seasonal_exponential_smoothing | legacy_linear_regression | atlas_linear_regression |
|---|---|---|---|---|---|---|---|
| 1 | 900.7 | 1,052.8 | 917.7 | 912.1 | 768.0 | 736.0 | 663.9 |
| 2 | 1,233.1 | 1,052.8 | 916.4 | 910.9 | 876.6 | 876.5 | 777.7 |
| 3 | 1,273.8 | 1,052.0 | 915.8 | 910.2 | 924.7 | 931.2 | 806.6 |
| 4 | 1,310.9 | 1,054.4 | 917.2 | 912.6 | 940.4 | 957.0 | 813.9 |
| 5 | 1,282.5 | 1,055.5 | 918.2 | 913.3 | 934.0 | 968.9 | 813.5 |
| 6 | 1,164.1 | 1,055.4 | 919.8 | 913.9 | 926.2 | 984.9 | 814.1 |
| 7 | 1,054.8 | 1,054.8 | 917.9 | 913.4 | 941.3 | 1,002.4 | 811.8 |
| 8 | 1,225.0 | 1,099.0 | 984.0 | 954.4 | 972.7 | 1,022.7 | 816.4 |
| 9 | 1,355.1 | 1,098.2 | 982.4 | 953.7 | 971.3 | 1,039.6 | 818.5 |
| 10 | 1,316.6 | 1,098.8 | 982.1 | 953.3 | 947.1 | 1,054.7 | 819.1 |
| 11 | 1,322.9 | 1,102.1 | 984.6 | 956.6 | 929.3 | 1,062.7 | 820.0 |
| 12 | 1,308.6 | 1,101.0 | 983.4 | 955.1 | 931.9 | 1,072.9 | 822.2 |
| 13 | 1,195.4 | 1,098.0 | 982.0 | 952.8 | 937.7 | 1,069.6 | 820.4 |
| 14 | 1,097.4 | 1,097.4 | 980.3 | 952.5 | 951.2 | 1,064.7 | 820.2 |
| 15 | 1,205.0 | 1,148.8 | 1,019.5 | 951.6 | 981.5 | 1,075.5 | 824.9 |
| 16 | 1,337.6 | 1,148.4 | 1,017.8 | 950.4 | 1,011.9 | 1,085.4 | 829.7 |
| 17 | 1,363.8 | 1,146.7 | 1,015.6 | 948.7 | 1,012.8 | 1,087.4 | 831.2 |
| 18 | 1,379.7 | 1,147.5 | 1,015.4 | 949.5 | 1,021.1 | 1,090.6 | 830.5 |
| 19 | 1,376.3 | 1,143.1 | 1,011.3 | 945.8 | 1,017.9 | 1,084.4 | 831.2 |
| 20 | 1,248.0 | 1,140.9 | 1,010.7 | 944.2 | 1,005.9 | 1,088.3 | 829.1 |
| 21 | 1,142.0 | 1,142.0 | 1,009.3 | 944.5 | 997.9 | 1,094.3 | 828.7 |
| 22 | 1,220.7 | 1,173.5 | 1,015.4 | 967.9 | 1,015.6 | 1,095.3 | 827.3 |
| 23 | 1,370.9 | 1,179.1 | 1,019.7 | 971.6 | 1,061.8 | 1,100.9 | 829.2 |
| 24 | 1,400.1 | 1,185.3 | 1,027.2 | 978.8 | 1,080.9 | 1,102.0 | 831.5 |
| 25 | 1,422.5 | 1,193.1 | 1,034.1 | 986.6 | 1,077.7 | 1,098.7 | 833.2 |
| 26 | 1,444.7 | 1,194.2 | 1,035.1 | 987.4 | 1,061.7 | 1,093.9 | 832.8 |
| 27 | 1,266.3 | 1,192.1 | 1,034.6 | 985.9 | 1,053.2 | 1,088.9 | 833.9 |
| 28 | 1,190.6 | 1,190.6 | 1,031.0 | 983.7 | 1,066.7 | 1,081.2 | 833.6 |
| 29 | 1,320.4 | 1,237.3 | 1,064.1 | 1,013.4 | 1,121.5 | 1,075.1 | 835.1 |
| 30 | 1,439.1 | 1,239.5 | 1,065.1 | 1,013.8 | 1,133.9 | 1,072.7 | 835.7 |

## confirmation: accuracy by horizon bucket

MAE, RMSE and bias in requests per day (bias = mean of prediction minus actual, with a 95 % HAC interval from the larger of the HLN and Bartlett-2h standard errors); WAPE and MAPE in %. `vs SN` is the MAE improvement over seasonal naive on common forecasts, `vs B*` over the best development baseline (`same_weekday_median_5w`); positive = better. `p` = DM p-value (conservative; n/a when too few origins).

| bucket | model | origins | forecasts | failed | MAE | RMSE | bias [95 % CI] | WAPE % | MASE | MAPE % | vs SN % | p | vs B* % | p |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| day_1 | naive | 336 | 336 | 0 | 969.7 | 1,434.5 | -3.5 [-157.7, 150.6] | 9.13 | 1.005 | 8.87 | 20.9 | 0.011 | 8.6 | 0.303 |
| day_1 | seasonal_naive | 336 | 336 | 0 | 1,225.7 | 2,001.7 | -6.9 [-316.3, 302.5] | 11.54 | 1.268 | 11.16 | ref |  | -15.6 | 0.039 |
| day_1 | same_weekday_mean_4w | 336 | 336 | 0 | 1,101.7 | 1,688.0 | -28.0 [-299.5, 243.5] | 10.38 | 1.143 | 10.08 | 10.1 | 0.127 | -3.9 | 0.222 |
| day_1 | same_weekday_median_5w | 336 | 336 | 0 | 1,060.6 | 1,715.0 | -174.8 [-449.3, 99.8] | 9.99 | 1.099 | 9.58 | 13.5 | 0.039 | B* |  |
| day_1 | seasonal_exponential_smoothing | 336 | 336 | 0 | 831.5 | 1,230.3 | -3.2 [-153.1, 146.7] | 7.83 | 0.862 | 7.60 | 32.2 | <0.001 | 21.6 | 0.004 |
| day_1 | legacy_linear_regression | 336 | 336 | 0 | 724.5 | 1,177.4 | -80.3 [-221.8, 61.2] | 6.82 | 0.754 | 6.55 | 40.9 | <0.001 | 31.7 | <0.001 |
| day_1 | atlas_linear_regression | 336 | 336 | 0 | 694.6 | 1,145.6 | -74.5 [-209.0, 60.1] | 6.54 | 0.724 | 6.23 | 43.3 | <0.001 | 34.5 | <0.001 |
| days_2_7 | naive | 336 | 2,016 | 0 | 1,351.5 | 2,034.1 | -15.1 [-247.4, 217.2] | 12.71 | 1.398 | 12.52 | -12.1 | 0.014 | -27.0 | 0.002 |
| days_2_7 | seasonal_naive | 336 | 2,016 | 0 | 1,205.4 | 1,983.3 | -22.5 [-375.8, 330.7] | 11.34 | 1.250 | 10.96 | ref |  | -13.3 | 0.025 |
| days_2_7 | same_weekday_mean_4w | 336 | 2,016 | 0 | 1,099.7 | 1,687.1 | -32.3 [-463.7, 399.1] | 10.35 | 1.143 | 10.05 | 8.8 | 0.129 | -3.4 | 0.411 |
| days_2_7 | same_weekday_median_5w | 336 | 2,016 | 0 | 1,063.9 | 1,716.3 | -178.2 [-622.6, 266.1] | 10.01 | 1.106 | 9.61 | 11.7 | 0.025 | B* |  |
| days_2_7 | seasonal_exponential_smoothing | 336 | 2,016 | 0 | 1,083.4 | 1,688.1 | -12.9 [-306.2, 280.3] | 10.19 | 1.121 | 9.93 | 10.1 | 0.001 | -1.8 | 0.793 |
| days_2_7 | legacy_linear_regression | 336 | 2,016 | 0 | 970.7 | 1,579.9 | -188.6 [-552.8, 175.5] | 9.13 | 1.010 | 8.69 | 19.5 | <0.001 | 8.8 | 0.166 |
| days_2_7 | atlas_linear_regression | 336 | 2,016 | 0 | 929.8 | 1,544.4 | -177.8 [-542.4, 186.8] | 8.75 | 0.969 | 8.26 | 22.9 | <0.001 | 12.6 | 0.023 |
| days_8_14 | naive | 336 | 2,352 | 0 | 1,512.1 | 2,275.1 | -40.5 [-417.0, 335.9] | 14.19 | 1.569 | 13.97 | -11.6 | 0.016 | -35.3 | <0.001 |
| days_8_14 | seasonal_naive | 336 | 2,352 | 0 | 1,354.8 | 2,126.2 | -47.3 [-536.0, 441.3] | 12.72 | 1.413 | 12.45 | ref |  | -21.2 | 0.005 |
| days_8_14 | same_weekday_mean_4w | 336 | 2,352 | 0 | 1,161.2 | 1,770.0 | -58.8 [-636.0, 518.4] | 10.90 | 1.211 | 10.54 | 14.3 | 0.015 | -3.9 | 0.148 |
| days_8_14 | same_weekday_median_5w | 336 | 2,352 | 0 | 1,117.8 | 1,746.6 | -204.8 [-764.3, 354.7] | 10.49 | 1.165 | 10.06 | 17.5 | 0.005 | B* |  |
| days_8_14 | seasonal_exponential_smoothing | 336 | 2,352 | 0 | 1,256.9 | 1,930.1 | -38.6 [-458.1, 380.9] | 11.80 | 1.307 | 11.52 | 7.2 | 0.006 | -12.4 | 0.067 |
| days_8_14 | legacy_linear_regression | 336 | 2,352 | 0 | 1,052.3 | 1,694.9 | -289.7 [-846.9, 267.4] | 9.88 | 1.099 | 9.36 | 22.3 | 0.002 | 5.9 | 0.278 |
| days_8_14 | atlas_linear_regression | 336 | 2,352 | 0 | 984.0 | 1,626.4 | -253.1 [-779.1, 272.9] | 9.23 | 1.029 | 8.69 | 27.4 | <0.001 | 12.0 | 0.017 |
| days_15_30 | naive | 336 | 5,376 | 0 | 1,627.0 | 2,382.0 | -67.5 [-467.1, 332.1] | 15.23 | 1.692 | 15.06 | -12.2 | 0.002 | -43.9 | 0.002 |
| days_15_30 | seasonal_naive | 336 | 5,376 | 0 | 1,450.3 | 2,192.5 | -74.2 [-507.0, 358.6] | 13.58 | 1.511 | 13.23 | ref |  | -28.2 | 0.017 |
| days_15_30 | same_weekday_mean_4w | 336 | 5,376 | 0 | 1,198.5 | 1,756.8 | -86.3 [-695.4, 522.7] | 11.22 | 1.248 | 10.82 | 17.4 | 0.021 | -6.0 | 0.076 |
| days_15_30 | same_weekday_median_5w | 336 | 5,376 | 0 | 1,131.0 | 1,725.1 | -232.3 [-815.0, 350.4] | 10.59 | 1.179 | 10.12 | 22.0 | 0.017 | B* |  |
| days_15_30 | seasonal_exponential_smoothing | 336 | 5,376 | 0 | 1,391.4 | 2,034.2 | -65.6 [-470.0, 338.8] | 13.02 | 1.449 | 12.75 | 4.1 | 0.052 | -23.0 | 0.061 |
| days_15_30 | legacy_linear_regression | 336 | 5,376 | 0 | 1,094.1 | 1,727.4 | -349.8 [-1,024.5, 324.9] | 10.24 | 1.144 | 9.69 | 24.6 | 0.002 | 3.3 | 0.661 |
| days_15_30 | atlas_linear_regression | 336 | 5,376 | 0 | 1,024.2 | 1,657.4 | -297.8 [-934.8, 339.2] | 9.59 | 1.073 | 9.03 | 29.4 | 0.003 | 9.4 | 0.123 |
| days_1_30 | naive | 336 | 10,080 | 0 | 1,523.2 | 2,264.9 | -48.6 [-332.4, 235.2] | 14.28 | 1.582 | 14.09 | -11.1 | 0.009 | -37.0 | <0.001 |
| days_1_30 | seasonal_naive | 336 | 10,080 | 0 | 1,371.5 | 2,130.4 | -55.4 [-379.3, 268.6] | 12.86 | 1.428 | 12.53 | ref |  | -23.3 | 0.006 |
| days_1_30 | same_weekday_mean_4w | 336 | 10,080 | 0 | 1,166.8 | 1,743.9 | -67.1 [-556.0, 421.7] | 10.94 | 1.215 | 10.58 | 14.9 | 0.005 | -4.9 | 0.070 |
| days_1_30 | same_weekday_median_5w | 336 | 10,080 | 0 | 1,112.2 | 1,728.0 | -213.2 [-712.8, 286.4] | 10.43 | 1.158 | 9.99 | 18.9 | 0.006 | B* |  |
| days_1_30 | seasonal_exponential_smoothing | 336 | 10,080 | 0 | 1,279.8 | 1,922.6 | -46.7 [-338.7, 245.3] | 12.00 | 1.331 | 11.73 | 6.7 | 0.001 | -15.1 | 0.056 |
| days_1_30 | legacy_linear_regression | 336 | 10,080 | 0 | 1,047.3 | 1,675.5 | -294.5 [-885.1, 296.0] | 9.82 | 1.093 | 9.31 | 23.6 | 0.002 | 5.8 | 0.256 |
| days_1_30 | atlas_linear_regression | 336 | 10,080 | 0 | 984.9 | 1,613.4 | -255.9 [-821.6, 309.7] | 9.24 | 1.030 | 8.70 | 28.2 | 0.003 | 11.4 | 0.054 |

### confirmation: MAE by horizon

| h | naive | seasonal_naive | same_weekday_mean_4w | same_weekday_median_5w | seasonal_exponential_smoothing | legacy_linear_regression | atlas_linear_regression |
|---|---|---|---|---|---|---|---|
| 1 | 969.7 | 1,225.7 | 1,101.7 | 1,060.6 | 831.5 | 724.5 | 694.6 |
| 2 | 1,335.1 | 1,216.0 | 1,100.2 | 1,060.3 | 988.7 | 876.8 | 855.3 |
| 3 | 1,372.2 | 1,208.1 | 1,098.9 | 1,062.4 | 1,061.4 | 950.0 | 913.9 |
| 4 | 1,400.1 | 1,206.1 | 1,099.6 | 1,064.4 | 1,097.7 | 980.0 | 941.5 |
| 5 | 1,470.8 | 1,203.0 | 1,099.8 | 1,065.1 | 1,103.6 | 997.5 | 952.4 |
| 6 | 1,330.0 | 1,198.3 | 1,096.4 | 1,061.0 | 1,113.9 | 1,003.7 | 954.4 |
| 7 | 1,200.7 | 1,200.7 | 1,103.4 | 1,070.4 | 1,135.4 | 1,016.0 | 961.0 |
| 8 | 1,431.6 | 1,369.8 | 1,165.0 | 1,115.6 | 1,213.1 | 1,030.0 | 967.9 |
| 9 | 1,619.9 | 1,359.3 | 1,162.9 | 1,115.9 | 1,241.3 | 1,039.0 | 976.6 |
| 10 | 1,577.2 | 1,351.1 | 1,160.3 | 1,116.4 | 1,266.3 | 1,042.9 | 978.9 |
| 11 | 1,528.3 | 1,349.3 | 1,160.4 | 1,117.8 | 1,272.5 | 1,056.0 | 984.0 |
| 12 | 1,592.5 | 1,350.9 | 1,159.0 | 1,118.1 | 1,273.6 | 1,061.4 | 989.0 |
| 13 | 1,482.8 | 1,350.8 | 1,160.2 | 1,118.6 | 1,268.8 | 1,066.5 | 994.5 |
| 14 | 1,352.7 | 1,352.7 | 1,160.8 | 1,122.1 | 1,263.0 | 1,070.4 | 996.9 |
| 15 | 1,506.6 | 1,491.2 | 1,196.8 | 1,133.1 | 1,319.4 | 1,079.6 | 1,000.3 |
| 16 | 1,683.5 | 1,484.7 | 1,198.5 | 1,131.8 | 1,372.7 | 1,085.4 | 1,006.7 |
| 17 | 1,686.9 | 1,480.6 | 1,200.1 | 1,132.3 | 1,419.6 | 1,089.6 | 1,011.4 |
| 18 | 1,701.0 | 1,479.7 | 1,200.2 | 1,132.8 | 1,438.1 | 1,094.9 | 1,014.0 |
| 19 | 1,753.4 | 1,483.6 | 1,201.2 | 1,133.3 | 1,434.4 | 1,099.2 | 1,016.9 |
| 20 | 1,619.2 | 1,484.4 | 1,203.3 | 1,134.8 | 1,397.1 | 1,097.9 | 1,018.6 |
| 21 | 1,484.6 | 1,484.6 | 1,207.6 | 1,141.9 | 1,384.1 | 1,102.6 | 1,022.7 |
| 22 | 1,610.7 | 1,425.7 | 1,189.7 | 1,121.5 | 1,391.3 | 1,102.6 | 1,026.9 |
| 23 | 1,707.0 | 1,420.6 | 1,192.7 | 1,123.5 | 1,394.2 | 1,103.7 | 1,031.5 |
| 24 | 1,660.0 | 1,412.5 | 1,190.5 | 1,124.7 | 1,412.9 | 1,100.3 | 1,033.9 |
| 25 | 1,668.4 | 1,410.7 | 1,190.4 | 1,126.1 | 1,398.7 | 1,097.3 | 1,033.3 |
| 26 | 1,672.3 | 1,412.2 | 1,189.1 | 1,126.5 | 1,374.0 | 1,093.1 | 1,035.7 |
| 27 | 1,548.0 | 1,411.7 | 1,188.7 | 1,125.4 | 1,356.9 | 1,091.4 | 1,034.5 |
| 28 | 1,412.7 | 1,412.7 | 1,190.1 | 1,129.8 | 1,343.2 | 1,089.8 | 1,032.8 |
| 29 | 1,576.9 | 1,457.3 | 1,217.7 | 1,140.7 | 1,385.9 | 1,091.4 | 1,034.7 |
| 30 | 1,740.9 | 1,451.8 | 1,218.5 | 1,138.0 | 1,439.2 | 1,086.1 | 1,033.1 |

## legacy_window: accuracy by horizon bucket

MAE, RMSE and bias in requests per day (bias = mean of prediction minus actual, with a 95 % HAC interval from the larger of the HLN and Bartlett-2h standard errors); WAPE and MAPE in %. `vs SN` is the MAE improvement over seasonal naive on common forecasts, `vs B*` over the best development baseline (`same_weekday_median_5w`); positive = better. `p` = DM p-value (conservative; n/a when too few origins).

| bucket | model | origins | forecasts | failed | MAE | RMSE | bias [95 % CI] | WAPE % | MASE | MAPE % | vs SN % | p | vs B* % | p |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| day_1 | naive | 119 | 119 | 0 | 827.2 | 1,067.1 | -7.7 [-202.3, 186.8] | 7.69 | 0.807 | 7.78 | 2.0 | 0.840 | -22.0 | 0.089 |
| day_1 | seasonal_naive | 119 | 119 | 0 | 844.0 | 1,132.4 | -4.7 [-291.9, 282.5] | 7.84 | 0.824 | 7.96 | ref |  | -24.4 | 0.042 |
| day_1 | same_weekday_mean_4w | 119 | 119 | 0 | 697.2 | 947.4 | 58.3 [-181.2, 297.7] | 6.48 | 0.681 | 6.51 | 17.4 | 0.075 | -2.8 | 0.695 |
| day_1 | same_weekday_median_5w | 119 | 119 | 0 | 678.3 | 935.1 | 1.7 [-236.4, 239.7] | 6.30 | 0.662 | 6.39 | 19.6 | 0.042 | B* |  |
| day_1 | seasonal_exponential_smoothing | 119 | 119 | 0 | 659.7 | 836.0 | -22.6 [-186.1, 140.9] | 6.13 | 0.644 | 6.16 | 21.8 | 0.017 | 2.8 | 0.776 |
| day_1 | legacy_linear_regression | 119 | 119 | 0 | 514.7 | 687.1 | -44.1 [-169.1, 80.9] | 4.78 | 0.503 | 4.80 | 39.0 | <0.001 | 24.1 | 0.010 |
| day_1 | atlas_linear_regression | 119 | 119 | 0 | 492.4 | 662.3 | -45.2 [-165.7, 75.3] | 4.58 | 0.481 | 4.60 | 41.7 | <0.001 | 27.4 | 0.003 |
| days_2_7 | naive | 118 | 693 | 0 | 1,088.7 | 1,411.2 | -0.8 [-163.3, 172.3] | 10.13 | 1.062 | 10.48 | -33.0 | <0.001 | -61.9 | <0.001 |
| days_2_7 | seasonal_naive | 118 | 693 | 0 | 818.6 | 1,105.8 | -15.3 [-301.2, 283.6] | 7.62 | 0.799 | 7.76 | ref |  | -21.8 | 0.067 |
| days_2_7 | same_weekday_mean_4w | 118 | 693 | 0 | 695.6 | 948.8 | 66.8 [-305.2, 436.6] | 6.47 | 0.679 | 6.52 | 15.0 | 0.243 | -3.5 | 0.642 |
| days_2_7 | same_weekday_median_5w | 118 | 693 | 0 | 672.2 | 931.9 | 7.9 [-391.2, 402.3] | 6.26 | 0.656 | 6.35 | 17.9 | 0.067 | B* |  |
| days_2_7 | seasonal_exponential_smoothing | 118 | 693 | 0 | 791.6 | 1,024.8 | -1.7 [-222.7, 222.5] | 7.37 | 0.773 | 7.47 | 3.3 | 0.619 | -17.8 | 0.057 |
| days_2_7 | legacy_linear_regression | 118 | 693 | 0 | 597.2 | 806.8 | -67.6 [-302.4, 159.2] | 5.56 | 0.583 | 5.62 | 27.0 | 0.001 | 11.2 | 0.252 |
| days_2_7 | atlas_linear_regression | 118 | 693 | 0 | 549.3 | 767.4 | -74.3 [-326.2, 170.8] | 5.11 | 0.536 | 5.16 | 32.9 | <0.001 | 18.3 | 0.068 |
| days_8_14 | naive | 112 | 763 | 0 | 1,122.1 | 1,423.5 | 49.6 [-306.7, 438.0] | 10.50 | 1.094 | 10.77 | -43.2 | <0.001 | -46.0 | <0.001 |
| days_8_14 | seasonal_naive | 112 | 763 | 0 | 783.5 | 1,053.1 | 46.5 [-368.8, 471.9] | 7.33 | 0.764 | 7.36 | ref |  | -1.9 | 0.567 |
| days_8_14 | same_weekday_mean_4w | 112 | 763 | 0 | 739.9 | 997.1 | 103.5 [-588.3, 802.6] | 6.92 | 0.722 | 6.93 | 5.6 | 0.513 | 3.7 | 0.721 |
| days_8_14 | same_weekday_median_5w | 112 | 763 | 0 | 768.6 | 1,027.5 | 40.1 [-623.7, 693.8] | 7.19 | 0.750 | 7.24 | 1.9 | 0.567 | B* |  |
| days_8_14 | seasonal_exponential_smoothing | 112 | 763 | 0 | 833.6 | 1,071.0 | 47.7 [-320.5, 432.5] | 7.80 | 0.813 | 7.83 | -6.4 | 0.175 | -8.5 | 0.326 |
| days_8_14 | legacy_linear_regression | 112 | 763 | 0 | 588.5 | 764.1 | -41.1 [-289.4, 192.6] | 5.51 | 0.574 | 5.59 | 24.9 | <0.001 | 23.4 | 0.005 |
| days_8_14 | atlas_linear_regression | 112 | 763 | 0 | 528.8 | 717.1 | -32.3 [-314.8, 241.5] | 4.95 | 0.516 | 5.05 | 32.5 | <0.001 | 31.2 | 0.002 |
| days_15_30 | naive | 105 | 1,560 | 0 | 1,198.6 | 1,519.8 | 32.2 [-1,058.0, 1,044.1] | 11.24 | 1.168 | 11.54 | -25.5 | 0.073 | -36.7 | 0.002 |
| days_15_30 | seasonal_naive | 105 | 1,560 | 0 | 954.7 | 1,215.4 | 11.0 [-1,137.7, 1,079.5] | 8.95 | 0.931 | 9.04 | ref |  | -8.9 | 0.108 |
| days_15_30 | same_weekday_mean_4w | 105 | 1,560 | 0 | 917.1 | 1,184.4 | 86.2 [-1,474.2, 1,661.8] | 8.60 | 0.894 | 8.62 | 3.9 | 0.725 | -4.6 | 0.653 |
| days_15_30 | same_weekday_median_5w | 105 | 1,560 | 0 | 876.6 | 1,116.0 | 8.5 [-1,365.0, 1,409.9] | 8.22 | 0.855 | 8.24 | 8.2 | 0.108 | B* |  |
| days_15_30 | seasonal_exponential_smoothing | 105 | 1,560 | 0 | 945.8 | 1,180.9 | 24.7 [-1,020.6, 994.2] | 8.87 | 0.922 | 8.94 | 0.9 | 0.660 | -7.9 | 0.279 |
| days_15_30 | legacy_linear_regression | 105 | 1,560 | 0 | 597.4 | 773.3 | -10.8 [-277.3, 192.8] | 5.60 | 0.582 | 5.71 | 37.4 | <0.001 | 31.9 | <0.001 |
| days_15_30 | atlas_linear_regression | 105 | 1,560 | 0 | 550.6 | 735.0 | -6.7 [-470.3, 426.5] | 5.16 | 0.537 | 5.29 | 42.3 | 0.001 | 37.2 | <0.001 |
| days_1_30 | naive | 119 | 3,135 | 0 | 1,141.6 | 1,458.1 | 27.6 [-777.4, 757.2] | 10.68 | 1.113 | 10.98 | -29.9 | 0.026 | -43.1 | <0.001 |
| days_1_30 | seasonal_naive | 119 | 3,135 | 0 | 878.7 | 1,150.6 | 13.2 [-850.3, 786.4] | 8.22 | 0.857 | 8.31 | ref |  | -10.2 | 0.042 |
| days_1_30 | same_weekday_mean_4w | 119 | 3,135 | 0 | 816.7 | 1,083.1 | 85.1 [-1,220.8, 1,402.3] | 7.64 | 0.796 | 7.66 | 7.1 | 0.500 | -2.4 | 0.828 |
| days_1_30 | same_weekday_median_5w | 119 | 3,135 | 0 | 797.6 | 1,049.7 | 15.8 [-1,162.4, 1,199.7] | 7.46 | 0.778 | 7.51 | 9.2 | 0.042 | B* |  |
| days_1_30 | seasonal_exponential_smoothing | 119 | 3,135 | 0 | 873.6 | 1,109.9 | 22.7 [-729.4, 698.8] | 8.17 | 0.852 | 8.24 | 0.6 | 0.571 | -9.5 | 0.262 |
| days_1_30 | legacy_linear_regression | 119 | 3,135 | 0 | 592.0 | 775.6 | -32.0 [-236.6, 74.6] | 5.54 | 0.578 | 5.63 | 32.6 | <0.001 | 25.8 | <0.001 |
| days_1_30 | atlas_linear_regression | 119 | 3,135 | 0 | 542.8 | 735.4 | -29.3 [-401.7, 298.2] | 5.08 | 0.530 | 5.17 | 38.2 | <0.001 | 31.9 | 0.001 |

### legacy_window: MAE by horizon

| h | naive | seasonal_naive | same_weekday_mean_4w | same_weekday_median_5w | seasonal_exponential_smoothing | legacy_linear_regression | atlas_linear_regression |
|---|---|---|---|---|---|---|---|
| 1 | 827.2 | 844.0 | 697.2 | 678.3 | 659.7 | 514.7 | 492.4 |
| 2 | 1,150.1 | 831.3 | 699.7 | 680.0 | 786.8 | 575.2 | 540.3 |
| 3 | 1,208.5 | 826.1 | 700.4 | 678.7 | 824.8 | 596.5 | 548.2 |
| 4 | 1,185.2 | 822.0 | 692.4 | 671.5 | 833.0 | 603.5 | 553.3 |
| 5 | 1,198.9 | 823.7 | 696.2 | 673.3 | 806.8 | 616.9 | 562.3 |
| 6 | 984.6 | 813.6 | 695.8 | 661.9 | 761.8 | 609.6 | 558.4 |
| 7 | 794.1 | 794.1 | 688.6 | 667.5 | 734.5 | 581.9 | 533.3 |
| 8 | 1,022.4 | 789.5 | 730.7 | 761.4 | 857.2 | 584.3 | 529.0 |
| 9 | 1,285.7 | 780.2 | 736.3 | 767.9 | 894.4 | 584.6 | 528.5 |
| 10 | 1,278.3 | 778.6 | 741.8 | 771.7 | 884.3 | 587.2 | 531.0 |
| 11 | 1,248.6 | 778.9 | 738.7 | 769.9 | 867.4 | 584.5 | 524.0 |
| 12 | 1,186.9 | 782.8 | 745.2 | 775.2 | 826.5 | 592.3 | 526.7 |
| 13 | 1,030.7 | 784.5 | 746.4 | 776.9 | 765.4 | 593.8 | 531.3 |
| 14 | 790.2 | 790.2 | 740.8 | 757.1 | 733.9 | 593.5 | 531.3 |
| 15 | 1,038.1 | 895.5 | 847.5 | 821.2 | 852.4 | 599.5 | 534.1 |
| 16 | 1,299.7 | 884.3 | 854.5 | 827.4 | 899.7 | 597.6 | 534.4 |
| 17 | 1,292.8 | 885.1 | 862.7 | 833.4 | 900.6 | 588.4 | 531.5 |
| 18 | 1,246.6 | 889.1 | 869.7 | 839.0 | 937.3 | 594.4 | 538.2 |
| 19 | 1,207.0 | 893.9 | 878.0 | 844.9 | 898.2 | 595.0 | 539.9 |
| 20 | 997.8 | 898.2 | 879.2 | 848.7 | 823.3 | 596.7 | 550.1 |
| 21 | 905.6 | 905.6 | 874.4 | 828.1 | 861.5 | 597.4 | 555.1 |
| 22 | 1,129.7 | 1,008.3 | 922.6 | 893.4 | 942.1 | 601.8 | 558.2 |
| 23 | 1,353.4 | 989.1 | 922.5 | 892.3 | 987.4 | 607.4 | 561.4 |
| 24 | 1,319.5 | 991.5 | 931.5 | 899.9 | 1,014.9 | 603.4 | 557.2 |
| 25 | 1,346.4 | 998.4 | 941.2 | 908.0 | 1,008.1 | 604.0 | 562.9 |
| 26 | 1,284.1 | 1,006.3 | 949.8 | 916.8 | 999.4 | 603.8 | 566.4 |
| 27 | 1,179.2 | 1,010.6 | 953.4 | 920.1 | 978.9 | 604.5 | 568.6 |
| 28 | 1,015.8 | 1,015.8 | 953.1 | 902.8 | 961.0 | 597.3 | 559.6 |
| 29 | 1,189.7 | 1,030.2 | 1,035.5 | 938.9 | 1,031.7 | 589.8 | 551.5 |
| 30 | 1,392.6 | 1,012.5 | 1,039.3 | 940.9 | 1,078.8 | 576.3 | 546.5 |

## Where a regression does worse than a baseline

Every bucket and period in which a regression's MAE on common forecasts is higher than a baseline's (exploratory, unadjusted p-values).

| period | bucket | regression | baseline | forecasts | MAE | baseline MAE | improvement % | p |
|---|---|---|---|---|---|---|---|---|
| development | days_2_7 | legacy_linear_regression | same_weekday_mean_4w | 2,916 | 953.5 | 917.6 | -3.9 | 0.474 |
| development | days_8_14 | legacy_linear_regression | same_weekday_mean_4w | 3,402 | 1,055.3 | 982.7 | -7.4 | 0.208 |
| development | days_15_30 | legacy_linear_regression | same_weekday_mean_4w | 7,776 | 1,088.4 | 1,026.6 | -6.0 | 0.357 |
| development | days_1_30 | legacy_linear_regression | same_weekday_mean_4w | 14,580 | 1,041.9 | 990.9 | -5.1 | 0.371 |
| development | days_2_7 | legacy_linear_regression | same_weekday_median_5w | 2,916 | 953.5 | 912.4 | -4.5 | 0.386 |
| development | days_8_14 | legacy_linear_regression | same_weekday_median_5w | 3,402 | 1,055.3 | 954.1 | -10.6 | 0.064 |
| development | days_15_30 | legacy_linear_regression | same_weekday_median_5w | 7,776 | 1,088.4 | 970.2 | -12.2 | 0.094 |
| development | days_1_30 | legacy_linear_regression | same_weekday_median_5w | 14,580 | 1,041.9 | 953.0 | -9.3 | 0.125 |
| development | days_2_7 | legacy_linear_regression | seasonal_exponential_smoothing | 2,916 | 953.5 | 923.9 | -3.2 | 0.601 |
| development | days_8_14 | legacy_linear_regression | seasonal_exponential_smoothing | 3,402 | 1,055.3 | 948.8 | -11.2 | 0.103 |
| development | days_15_30 | legacy_linear_regression | seasonal_exponential_smoothing | 7,776 | 1,088.4 | 1,045.1 | -4.1 | 0.519 |
| development | days_1_30 | legacy_linear_regression | seasonal_exponential_smoothing | 14,580 | 1,041.9 | 989.1 | -5.3 | 0.416 |

## Comparison window: legacy_holdout_window (legacy_window)

The 90 target days of the legacy one-step holdout, scored by every model at every horizon from the descriptive legacy_window origins (one refit per origin). Targets 2026-04-02..2026-06-30 (90 days); every horizon scored on all of them: yes. Here MAPE uses the same formula and the same target days as the legacy number; the procedures differ as labelled.

| procedure | h | forecasts | MAE | RMSE | MAPE % |
|---|---|---|---|---|---|
| published legacy one-step holdout (single fit, observed lags) | 1 | 90 | 514.30 | 676.70 | 4.93 |
| M2 re-computation of the legacy one-step holdout | 1 | 90 | 514.30 | 676.70 | 4.93 |
| published 7-day seasonal baseline y(t-7) | 1 | 90 | 851.16 | 1,107.24 | 8.23 |
| backtest `atlas_linear_regression` (refit per origin) | 1 | 90 | 493.57 | 653.69 | 4.72 |
| backtest `atlas_linear_regression` (refit per origin) | 2 | 90 | 551.46 | 744.96 | 5.34 |
| backtest `atlas_linear_regression` (refit per origin) | 7 | 90 | 549.98 | 754.24 | 5.32 |
| backtest `atlas_linear_regression` (refit per origin) | 14 | 90 | 550.30 | 743.23 | 5.31 |
| backtest `atlas_linear_regression` (refit per origin) | 21 | 90 | 559.32 | 748.02 | 5.40 |
| backtest `atlas_linear_regression` (refit per origin) | 30 | 90 | 546.46 | 733.70 | 5.27 |
| backtest `legacy_linear_regression` (refit per origin) | 1 | 90 | 514.33 | 678.07 | 4.91 |
| backtest `legacy_linear_regression` (refit per origin) | 2 | 90 | 582.40 | 784.77 | 5.62 |
| backtest `legacy_linear_regression` (refit per origin) | 7 | 90 | 596.99 | 793.17 | 5.76 |
| backtest `legacy_linear_regression` (refit per origin) | 14 | 90 | 586.40 | 766.57 | 5.64 |
| backtest `legacy_linear_regression` (refit per origin) | 21 | 90 | 587.89 | 771.93 | 5.65 |
| backtest `legacy_linear_regression` (refit per origin) | 30 | 90 | 576.31 | 762.49 | 5.54 |
| backtest `naive` (refit per origin) | 1 | 90 | 781.66 | 1,018.88 | 7.52 |
| backtest `naive` (refit per origin) | 2 | 90 | 1,099.40 | 1,399.19 | 10.64 |
| backtest `naive` (refit per origin) | 7 | 90 | 851.16 | 1,107.24 | 8.23 |
| backtest `naive` (refit per origin) | 14 | 90 | 785.57 | 1,052.08 | 7.42 |
| backtest `naive` (refit per origin) | 21 | 90 | 883.81 | 1,098.06 | 8.42 |
| backtest `naive` (refit per origin) | 30 | 90 | 1,392.64 | 1,740.84 | 13.39 |
| backtest `same_weekday_mean_4w` (refit per origin) | 1 | 90 | 697.43 | 935.78 | 6.67 |
| backtest `same_weekday_mean_4w` (refit per origin) | 2 | 90 | 697.43 | 935.78 | 6.67 |
| backtest `same_weekday_mean_4w` (refit per origin) | 7 | 90 | 697.43 | 935.78 | 6.67 |
| backtest `same_weekday_mean_4w` (refit per origin) | 14 | 90 | 720.64 | 961.73 | 6.85 |
| backtest `same_weekday_mean_4w` (refit per origin) | 21 | 90 | 875.15 | 1,152.34 | 8.30 |
| backtest `same_weekday_mean_4w` (refit per origin) | 30 | 90 | 1,039.32 | 1,290.36 | 9.82 |
| backtest `same_weekday_median_5w` (refit per origin) | 1 | 90 | 719.26 | 967.67 | 6.86 |
| backtest `same_weekday_median_5w` (refit per origin) | 2 | 90 | 719.26 | 967.67 | 6.86 |
| backtest `same_weekday_median_5w` (refit per origin) | 7 | 90 | 719.26 | 967.67 | 6.86 |
| backtest `same_weekday_median_5w` (refit per origin) | 14 | 90 | 785.70 | 1,026.94 | 7.46 |
| backtest `same_weekday_median_5w` (refit per origin) | 21 | 90 | 834.21 | 1,056.00 | 7.86 |
| backtest `same_weekday_median_5w` (refit per origin) | 30 | 90 | 940.93 | 1,190.17 | 8.91 |
| backtest `seasonal_exponential_smoothing` (refit per origin) | 1 | 90 | 641.59 | 813.83 | 6.10 |
| backtest `seasonal_exponential_smoothing` (refit per origin) | 2 | 90 | 783.31 | 981.36 | 7.47 |
| backtest `seasonal_exponential_smoothing` (refit per origin) | 7 | 90 | 765.50 | 1,017.27 | 7.38 |
| backtest `seasonal_exponential_smoothing` (refit per origin) | 14 | 90 | 721.13 | 975.92 | 6.82 |
| backtest `seasonal_exponential_smoothing` (refit per origin) | 21 | 90 | 847.40 | 1,049.78 | 8.08 |
| backtest `seasonal_exponential_smoothing` (refit per origin) | 30 | 90 | 1,078.84 | 1,285.27 | 10.28 |
| backtest `seasonal_naive` (refit per origin) | 1 | 90 | 851.16 | 1,107.24 | 8.23 |
| backtest `seasonal_naive` (refit per origin) | 2 | 90 | 851.16 | 1,107.24 | 8.23 |
| backtest `seasonal_naive` (refit per origin) | 7 | 90 | 851.16 | 1,107.24 | 8.23 |
| backtest `seasonal_naive` (refit per origin) | 14 | 90 | 785.57 | 1,052.08 | 7.42 |
| backtest `seasonal_naive` (refit per origin) | 21 | 90 | 883.81 | 1,098.06 | 8.42 |
| backtest `seasonal_naive` (refit per origin) | 30 | 90 | 1,012.49 | 1,235.00 | 9.61 |

## What recursion costs (observed-input diagnostic)

Each origin's fitted regression applied to the *observed* previous days instead of its own predictions (the legacy holdout's procedure, with coefficients h - 1 days old). It reads actuals after the origin, so it is diagnostic only. At h = 1 both are the same.

| period | model | h | forecasts | MAE recursive | MAE observed inputs | MAPE % recursive | MAPE % observed inputs |
|---|---|---|---|---|---|---|---|
| confirmation | atlas_linear_regression | 1 | 336 | 694.6 | 694.6 | 6.23 | 6.23 |
| confirmation | atlas_linear_regression | 2 | 336 | 855.3 | 693.0 | 7.63 | 6.21 |
| confirmation | atlas_linear_regression | 7 | 336 | 961.0 | 687.6 | 8.50 | 6.14 |
| confirmation | atlas_linear_regression | 14 | 336 | 996.9 | 691.5 | 8.80 | 6.16 |
| confirmation | atlas_linear_regression | 21 | 336 | 1,022.7 | 699.7 | 9.02 | 6.23 |
| confirmation | atlas_linear_regression | 30 | 336 | 1,033.1 | 709.7 | 9.11 | 6.31 |
| confirmation | legacy_linear_regression | 1 | 336 | 724.5 | 724.5 | 6.55 | 6.55 |
| confirmation | legacy_linear_regression | 2 | 336 | 876.8 | 724.5 | 7.88 | 6.55 |
| confirmation | legacy_linear_regression | 7 | 336 | 1,016.0 | 721.5 | 9.05 | 6.50 |
| confirmation | legacy_linear_regression | 14 | 336 | 1,070.4 | 728.5 | 9.53 | 6.55 |
| confirmation | legacy_linear_regression | 21 | 336 | 1,102.6 | 736.7 | 9.78 | 6.62 |
| confirmation | legacy_linear_regression | 30 | 336 | 1,086.1 | 748.9 | 9.59 | 6.71 |
| development | atlas_linear_regression | 1 | 486 | 663.9 | 663.9 | 6.80 | 6.80 |
| development | atlas_linear_regression | 2 | 486 | 777.7 | 668.1 | 7.91 | 6.83 |
| development | atlas_linear_regression | 7 | 486 | 811.8 | 678.6 | 8.23 | 6.92 |
| development | atlas_linear_regression | 14 | 486 | 820.2 | 693.0 | 8.29 | 7.04 |
| development | atlas_linear_regression | 21 | 486 | 828.7 | 705.2 | 8.35 | 7.13 |
| development | atlas_linear_regression | 30 | 486 | 835.7 | 720.2 | 8.36 | 7.23 |
| development | legacy_linear_regression | 1 | 486 | 736.0 | 736.0 | 7.72 | 7.72 |
| development | legacy_linear_regression | 2 | 486 | 876.5 | 746.2 | 9.22 | 7.84 |
| development | legacy_linear_regression | 7 | 486 | 1,002.4 | 798.8 | 10.56 | 8.39 |
| development | legacy_linear_regression | 14 | 486 | 1,064.7 | 864.4 | 11.15 | 9.06 |
| development | legacy_linear_regression | 21 | 486 | 1,094.3 | 899.6 | 11.37 | 9.37 |
| development | legacy_linear_regression | 30 | 486 | 1,072.7 | 926.2 | 11.05 | 9.57 |
| legacy_window | atlas_linear_regression | 1 | 119 | 492.4 | 492.4 | 4.60 | 4.60 |
| legacy_window | atlas_linear_regression | 2 | 118 | 540.3 | 487.0 | 5.08 | 4.55 |
| legacy_window | atlas_linear_regression | 7 | 113 | 533.3 | 457.9 | 5.06 | 4.34 |
| legacy_window | atlas_linear_regression | 14 | 106 | 531.3 | 472.1 | 5.07 | 4.48 |
| legacy_window | atlas_linear_regression | 21 | 99 | 555.1 | 480.2 | 5.32 | 4.57 |
| legacy_window | atlas_linear_regression | 30 | 90 | 546.5 | 490.6 | 5.27 | 4.69 |
| legacy_window | legacy_linear_regression | 1 | 119 | 514.7 | 514.8 | 4.80 | 4.80 |
| legacy_window | legacy_linear_regression | 2 | 118 | 575.2 | 507.4 | 5.40 | 4.74 |
| legacy_window | legacy_linear_regression | 7 | 113 | 581.9 | 482.8 | 5.52 | 4.57 |
| legacy_window | legacy_linear_regression | 14 | 106 | 593.5 | 494.1 | 5.64 | 4.69 |
| legacy_window | legacy_linear_regression | 21 | 99 | 597.4 | 500.9 | 5.70 | 4.77 |
| legacy_window | legacy_linear_regression | 30 | 90 | 576.3 | 511.6 | 5.54 | 4.89 |

## Sensitivity analyses (pre-registered, descriptive)

### exclude_flagged_targets

Target days covered by a warning-level M1 data-quality flag removed.

| period | bucket | model | forecasts | MAE | vs best baseline % | p |
|---|---|---|---|---|---|---|
| confirmation | day_1 | naive | 318 | 935.2 | 1.2 | n/a |
| confirmation | days_2_7 | naive | 1,908 | 1,265.0 | -33.2 | n/a |
| confirmation | days_8_14 | naive | 2,226 | 1,403.4 | -42.5 | n/a |
| confirmation | days_15_30 | naive | 5,088 | 1,527.8 | -53.6 | n/a |
| confirmation | days_1_30 | naive | 9,540 | 1,426.5 | -45.3 | n/a |
| development | day_1 | naive | 475 | 868.1 | 0.3 | n/a |
| development | days_2_7 | naive | 2,850 | 1,187.5 | -36.3 | n/a |
| development | days_8_14 | naive | 3,325 | 1,228.1 | -34.4 | n/a |
| development | days_15_30 | naive | 7,585 | 1,278.5 | -38.0 | n/a |
| development | days_1_30 | naive | 14,235 | 1,234.8 | -35.6 | n/a |
| confirmation | day_1 | seasonal_naive | 318 | 1,126.9 | -19.0 | n/a |
| confirmation | days_2_7 | seasonal_naive | 1,908 | 1,105.4 | -16.4 | n/a |
| confirmation | days_8_14 | seasonal_naive | 2,226 | 1,233.2 | -25.2 | n/a |
| confirmation | days_15_30 | seasonal_naive | 5,088 | 1,343.5 | -35.1 | n/a |
| confirmation | days_1_30 | seasonal_naive | 9,540 | 1,262.9 | -28.6 | n/a |
| development | day_1 | seasonal_naive | 475 | 1,011.4 | -16.1 | n/a |
| development | days_2_7 | seasonal_naive | 2,850 | 1,012.8 | -16.3 | n/a |
| development | days_8_14 | seasonal_naive | 3,325 | 1,062.0 | -16.2 | n/a |
| development | days_15_30 | seasonal_naive | 7,585 | 1,133.5 | -22.4 | n/a |
| development | days_1_30 | seasonal_naive | 14,235 | 1,088.6 | -19.6 | n/a |
| confirmation | day_1 | same_weekday_mean_4w | 318 | 1,022.7 | -8.0 | n/a |
| confirmation | days_2_7 | same_weekday_mean_4w | 1,908 | 1,020.6 | -7.4 | n/a |
| confirmation | days_8_14 | same_weekday_mean_4w | 2,226 | 1,052.1 | -6.8 | n/a |
| confirmation | days_15_30 | same_weekday_mean_4w | 5,088 | 1,062.4 | -6.8 | n/a |
| confirmation | days_1_30 | same_weekday_mean_4w | 9,540 | 1,050.3 | -7.0 | n/a |
| development | day_1 | same_weekday_mean_4w | 475 | 874.9 | -0.5 | n/a |
| development | days_2_7 | same_weekday_mean_4w | 2,850 | 874.7 | -0.4 | n/a |
| development | days_8_14 | same_weekday_mean_4w | 3,325 | 942.9 | -3.2 | n/a |
| development | days_15_30 | same_weekday_mean_4w | 7,585 | 983.6 | -6.2 | n/a |
| development | days_1_30 | same_weekday_mean_4w | 14,235 | 948.7 | -4.2 | n/a |
| confirmation | day_1 | seasonal_exponential_smoothing | 318 | 766.7 | 19.0 | n/a |
| confirmation | days_2_7 | seasonal_exponential_smoothing | 1,908 | 996.0 | -4.8 | n/a |
| confirmation | days_8_14 | seasonal_exponential_smoothing | 2,226 | 1,145.6 | -16.3 | n/a |
| confirmation | days_15_30 | seasonal_exponential_smoothing | 5,088 | 1,285.6 | -29.3 | n/a |
| confirmation | days_1_30 | seasonal_exponential_smoothing | 9,540 | 1,177.7 | -20.0 | n/a |
| development | day_1 | seasonal_exponential_smoothing | 475 | 734.0 | 15.7 | n/a |
| development | days_2_7 | seasonal_exponential_smoothing | 2,850 | 883.4 | -1.4 | n/a |
| development | days_8_14 | seasonal_exponential_smoothing | 3,325 | 910.5 | 0.3 | n/a |
| development | days_15_30 | seasonal_exponential_smoothing | 7,585 | 999.5 | -7.9 | n/a |
| development | days_1_30 | seasonal_exponential_smoothing | 14,235 | 946.6 | -4.0 | n/a |
| confirmation | day_1 | legacy_linear_regression | 318 | 671.2 | 29.1 | n/a |
| confirmation | days_2_7 | legacy_linear_regression | 1,908 | 872.3 | 8.2 | n/a |
| confirmation | days_8_14 | legacy_linear_regression | 2,226 | 919.1 | 6.7 | n/a |
| confirmation | days_15_30 | legacy_linear_regression | 5,088 | 958.1 | 3.7 | n/a |
| confirmation | days_1_30 | legacy_linear_regression | 9,540 | 922.3 | 6.1 | n/a |
| development | day_1 | legacy_linear_regression | 475 | 706.4 | 18.9 | n/a |
| development | days_2_7 | legacy_linear_regression | 2,850 | 915.7 | -5.1 | n/a |
| development | days_8_14 | legacy_linear_regression | 3,325 | 1,019.9 | -11.6 | n/a |
| development | days_15_30 | legacy_linear_regression | 7,585 | 1,052.1 | -13.6 | n/a |
| development | days_1_30 | legacy_linear_regression | 14,235 | 1,005.7 | -10.5 | n/a |
| confirmation | day_1 | atlas_linear_regression | 318 | 643.5 | 32.0 | n/a |
| confirmation | days_2_7 | atlas_linear_regression | 1,908 | 828.8 | 12.8 | n/a |
| confirmation | days_8_14 | atlas_linear_regression | 2,226 | 851.7 | 13.5 | n/a |
| confirmation | days_15_30 | atlas_linear_regression | 5,088 | 884.3 | 11.1 | n/a |
| confirmation | days_1_30 | atlas_linear_regression | 9,540 | 857.6 | 12.6 | n/a |
| development | day_1 | atlas_linear_regression | 475 | 631.3 | 27.5 | n/a |
| development | days_2_7 | atlas_linear_regression | 2,850 | 763.2 | 12.4 | n/a |
| development | days_8_14 | atlas_linear_regression | 3,325 | 776.7 | 15.0 | n/a |
| development | days_15_30 | atlas_linear_regression | 7,585 | 785.8 | 15.2 | n/a |
| development | days_1_30 | atlas_linear_regression | 14,235 | 774.0 | 15.0 | n/a |

### legacy_identification_boundary

Origins whose legacy design is affected by the leap-day step: trend, year and day of year are exactly collinear until 2025-01-01 (2025 targets not estimable from December 2024 origins), and afterwards the step is identified from very few 2025 rows (high leverage).

| period | bucket | model | forecasts | MAE | vs best baseline % | p |
|---|---|---|---|---|---|---|
| confirmation | day_1 | naive | 336 | 969.7 | 8.6 | 0.303 |
| confirmation | days_2_7 | naive | 2,016 | 1,351.5 | -27.0 | 0.002 |
| confirmation | days_8_14 | naive | 2,352 | 1,512.1 | -35.3 | <0.001 |
| confirmation | days_15_30 | naive | 5,376 | 1,627.0 | -43.9 | 0.002 |
| confirmation | days_1_30 | naive | 10,080 | 1,523.2 | -37.0 | <0.001 |
| development | day_1 | naive | 425 | 839.3 | -10.3 | n/a |
| development | days_2_7 | naive | 2,550 | 1,082.2 | -44.7 | n/a |
| development | days_8_14 | naive | 2,975 | 1,097.7 | -39.4 | n/a |
| development | days_15_30 | naive | 6,800 | 1,150.5 | -40.1 | n/a |
| development | days_1_30 | naive | 12,750 | 1,114.2 | -39.9 | n/a |
| confirmation | day_1 | seasonal_naive | 336 | 1,225.7 | -15.6 | 0.039 |
| confirmation | days_2_7 | seasonal_naive | 2,016 | 1,205.4 | -13.3 | 0.025 |
| confirmation | days_8_14 | seasonal_naive | 2,352 | 1,354.8 | -21.2 | 0.005 |
| confirmation | days_15_30 | seasonal_naive | 5,376 | 1,450.3 | -28.2 | 0.017 |
| confirmation | days_1_30 | seasonal_naive | 10,080 | 1,371.5 | -23.3 | 0.006 |
| development | day_1 | seasonal_naive | 425 | 867.9 | -14.0 | n/a |
| development | days_2_7 | seasonal_naive | 2,550 | 883.9 | -18.2 | n/a |
| development | days_8_14 | seasonal_naive | 2,975 | 926.6 | -17.7 | n/a |
| development | days_15_30 | seasonal_naive | 6,800 | 990.1 | -20.6 | n/a |
| development | days_1_30 | seasonal_naive | 12,750 | 950.0 | -19.3 | n/a |
| confirmation | day_1 | same_weekday_mean_4w | 336 | 1,101.7 | -3.9 | 0.222 |
| confirmation | days_2_7 | same_weekday_mean_4w | 2,016 | 1,099.7 | -3.4 | 0.411 |
| confirmation | days_8_14 | same_weekday_mean_4w | 2,352 | 1,161.2 | -3.9 | 0.148 |
| confirmation | days_15_30 | same_weekday_mean_4w | 5,376 | 1,198.5 | -6.0 | 0.076 |
| confirmation | days_1_30 | same_weekday_mean_4w | 10,080 | 1,166.8 | -4.9 | 0.070 |
| development | day_1 | same_weekday_mean_4w | 425 | 775.1 | -1.8 | n/a |
| development | days_2_7 | same_weekday_mean_4w | 2,550 | 763.4 | -2.1 | n/a |
| development | days_8_14 | same_weekday_mean_4w | 2,975 | 810.7 | -2.9 | n/a |
| development | days_15_30 | same_weekday_mean_4w | 6,800 | 860.6 | -4.8 | n/a |
| development | days_1_30 | same_weekday_mean_4w | 12,750 | 826.6 | -3.8 | n/a |
| confirmation | day_1 | seasonal_exponential_smoothing | 336 | 831.5 | 21.6 | 0.004 |
| confirmation | days_2_7 | seasonal_exponential_smoothing | 2,016 | 1,083.4 | -1.8 | 0.793 |
| confirmation | days_8_14 | seasonal_exponential_smoothing | 2,352 | 1,256.9 | -12.4 | 0.067 |
| confirmation | days_15_30 | seasonal_exponential_smoothing | 5,376 | 1,391.4 | -23.0 | 0.061 |
| confirmation | days_1_30 | seasonal_exponential_smoothing | 10,080 | 1,279.8 | -15.1 | 0.056 |
| development | day_1 | seasonal_exponential_smoothing | 425 | 679.6 | 10.7 | n/a |
| development | days_2_7 | seasonal_exponential_smoothing | 2,550 | 769.0 | -2.8 | n/a |
| development | days_8_14 | seasonal_exponential_smoothing | 2,975 | 792.7 | -0.7 | n/a |
| development | days_15_30 | seasonal_exponential_smoothing | 6,800 | 878.6 | -7.0 | n/a |
| development | days_1_30 | seasonal_exponential_smoothing | 12,750 | 830.0 | -4.2 | n/a |
| confirmation | day_1 | legacy_linear_regression | 336 | 724.5 | 31.7 | <0.001 |
| confirmation | days_2_7 | legacy_linear_regression | 2,016 | 970.7 | 8.8 | 0.166 |
| confirmation | days_8_14 | legacy_linear_regression | 2,352 | 1,052.3 | 5.9 | 0.278 |
| confirmation | days_15_30 | legacy_linear_regression | 5,376 | 1,094.1 | 3.3 | 0.661 |
| confirmation | days_1_30 | legacy_linear_regression | 10,080 | 1,047.3 | 5.8 | 0.256 |
| development | day_1 | legacy_linear_regression | 425 | 666.9 | 12.4 | n/a |
| development | days_2_7 | legacy_linear_regression | 2,550 | 832.3 | -11.3 | n/a |
| development | days_8_14 | legacy_linear_regression | 2,975 | 890.2 | -13.0 | n/a |
| development | days_15_30 | legacy_linear_regression | 6,800 | 930.6 | -13.4 | n/a |
| development | days_1_30 | legacy_linear_regression | 12,750 | 892.7 | -12.1 | n/a |
| confirmation | day_1 | atlas_linear_regression | 336 | 694.6 | 34.5 | <0.001 |
| confirmation | days_2_7 | atlas_linear_regression | 2,016 | 929.8 | 12.6 | 0.023 |
| confirmation | days_8_14 | atlas_linear_regression | 2,352 | 984.0 | 12.0 | 0.017 |
| confirmation | days_15_30 | atlas_linear_regression | 5,376 | 1,024.2 | 9.4 | 0.123 |
| confirmation | days_1_30 | atlas_linear_regression | 10,080 | 984.9 | 11.4 | 0.054 |
| development | day_1 | atlas_linear_regression | 425 | 592.8 | 22.1 | n/a |
| development | days_2_7 | atlas_linear_regression | 2,550 | 669.1 | 10.5 | n/a |
| development | days_8_14 | atlas_linear_regression | 2,975 | 683.6 | 13.2 | n/a |
| development | days_15_30 | atlas_linear_regression | 6,800 | 724.7 | 11.7 | n/a |
| development | days_1_30 | atlas_linear_regression | 12,750 | 699.6 | 12.2 | n/a |

### holiday_related_targets

Targets on a federal holiday, its observed day, or the day before or after.

| period | bucket | model | forecasts | MAE | vs best baseline % | p |
|---|---|---|---|---|---|---|
| confirmation | day_1 | naive | 30 | 1,147.3 | 23.4 | n/a |
| confirmation | days_2_7 | naive | 171 | 1,578.2 | -3.2 | n/a |
| confirmation | days_8_14 | naive | 189 | 1,949.8 | -30.1 | n/a |
| confirmation | days_15_30 | naive | 468 | 2,086.1 | -57.0 | n/a |
| confirmation | days_1_30 | naive | 858 | 1,922.1 | -36.2 | n/a |
| development | day_1 | naive | 39 | 1,337.5 | 6.2 | n/a |
| development | days_2_7 | naive | 234 | 1,512.7 | -6.1 | n/a |
| development | days_8_14 | naive | 273 | 1,737.2 | -23.6 | n/a |
| development | days_15_30 | naive | 624 | 1,438.4 | -4.2 | n/a |
| development | days_1_30 | naive | 1,170 | 1,519.6 | -8.8 | n/a |
| confirmation | day_1 | seasonal_naive | 30 | 1,725.0 | -15.2 | n/a |
| confirmation | days_2_7 | seasonal_naive | 171 | 1,755.3 | -14.8 | n/a |
| confirmation | days_8_14 | seasonal_naive | 189 | 2,054.6 | -37.1 | n/a |
| confirmation | days_15_30 | seasonal_naive | 468 | 1,866.8 | -40.5 | n/a |
| confirmation | days_1_30 | seasonal_naive | 858 | 1,880.9 | -33.2 | n/a |
| development | day_1 | seasonal_naive | 39 | 1,821.9 | -27.7 | n/a |
| development | days_2_7 | seasonal_naive | 234 | 1,821.9 | -27.7 | n/a |
| development | days_8_14 | seasonal_naive | 273 | 1,716.0 | -22.1 | n/a |
| development | days_15_30 | seasonal_naive | 624 | 1,544.5 | -11.9 | n/a |
| development | days_1_30 | seasonal_naive | 1,170 | 1,649.3 | -18.1 | n/a |
| confirmation | day_1 | same_weekday_mean_4w | 30 | 1,684.2 | -12.5 | n/a |
| confirmation | days_2_7 | same_weekday_mean_4w | 171 | 1,734.9 | -13.5 | n/a |
| confirmation | days_8_14 | same_weekday_mean_4w | 189 | 1,743.9 | -16.4 | n/a |
| confirmation | days_15_30 | same_weekday_mean_4w | 468 | 1,484.5 | -11.7 | n/a |
| confirmation | days_1_30 | same_weekday_mean_4w | 858 | 1,598.5 | -13.2 | n/a |
| development | day_1 | same_weekday_mean_4w | 39 | 1,443.8 | -1.2 | n/a |
| development | days_2_7 | same_weekday_mean_4w | 234 | 1,443.8 | -1.2 | n/a |
| development | days_8_14 | same_weekday_mean_4w | 273 | 1,470.5 | -4.6 | n/a |
| development | days_15_30 | same_weekday_mean_4w | 624 | 1,485.5 | -7.6 | n/a |
| development | days_1_30 | same_weekday_mean_4w | 1,170 | 1,472.3 | -5.4 | n/a |
| confirmation | day_1 | seasonal_exponential_smoothing | 30 | 1,075.0 | 28.2 | n/a |
| confirmation | days_2_7 | seasonal_exponential_smoothing | 171 | 1,526.2 | 0.2 | n/a |
| confirmation | days_8_14 | seasonal_exponential_smoothing | 189 | 1,854.1 | -23.7 | n/a |
| confirmation | days_15_30 | seasonal_exponential_smoothing | 468 | 1,855.1 | -39.6 | n/a |
| confirmation | days_1_30 | seasonal_exponential_smoothing | 858 | 1,762.0 | -24.8 | n/a |
| development | day_1 | seasonal_exponential_smoothing | 39 | 1,313.4 | 7.9 | n/a |
| development | days_2_7 | seasonal_exponential_smoothing | 234 | 1,497.5 | -5.0 | n/a |
| development | days_8_14 | seasonal_exponential_smoothing | 273 | 1,507.7 | -7.3 | n/a |
| development | days_15_30 | seasonal_exponential_smoothing | 624 | 1,330.9 | 3.6 | n/a |
| development | days_1_30 | seasonal_exponential_smoothing | 1,170 | 1,404.9 | -0.6 | n/a |
| confirmation | day_1 | legacy_linear_regression | 30 | 1,025.9 | 31.5 | n/a |
| confirmation | days_2_7 | legacy_linear_regression | 171 | 1,333.1 | 12.8 | n/a |
| confirmation | days_8_14 | legacy_linear_regression | 189 | 1,333.9 | 11.0 | n/a |
| confirmation | days_15_30 | legacy_linear_regression | 468 | 1,244.7 | 6.3 | n/a |
| confirmation | days_1_30 | legacy_linear_regression | 858 | 1,274.3 | 9.7 | n/a |
| development | day_1 | legacy_linear_regression | 39 | 1,228.9 | 13.8 | n/a |
| development | days_2_7 | legacy_linear_regression | 234 | 1,404.3 | 1.5 | n/a |
| development | days_8_14 | legacy_linear_regression | 273 | 1,448.1 | -3.0 | n/a |
| development | days_15_30 | legacy_linear_regression | 624 | 1,325.2 | 4.0 | n/a |
| development | days_1_30 | legacy_linear_regression | 1,170 | 1,366.5 | 2.2 | n/a |
| confirmation | day_1 | atlas_linear_regression | 30 | 848.4 | 43.3 | n/a |
| confirmation | days_2_7 | atlas_linear_regression | 171 | 1,181.1 | 22.7 | n/a |
| confirmation | days_8_14 | atlas_linear_regression | 189 | 1,154.3 | 23.0 | n/a |
| confirmation | days_15_30 | atlas_linear_regression | 468 | 1,069.5 | 19.5 | n/a |
| confirmation | days_1_30 | atlas_linear_regression | 858 | 1,102.7 | 21.9 | n/a |
| development | day_1 | atlas_linear_regression | 39 | 934.0 | 34.5 | n/a |
| development | days_2_7 | atlas_linear_regression | 234 | 1,050.6 | 26.3 | n/a |
| development | days_8_14 | atlas_linear_regression | 273 | 1,034.1 | 26.4 | n/a |
| development | days_15_30 | atlas_linear_regression | 624 | 1,038.2 | 24.8 | n/a |
| development | days_1_30 | atlas_linear_regression | 1,170 | 1,036.2 | 25.8 | n/a |

### other_targets

Every other target day.

| period | bucket | model | forecasts | MAE | vs best baseline % | p |
|---|---|---|---|---|---|---|
| confirmation | day_1 | naive | 306 | 952.3 | 6.4 | n/a |
| confirmation | days_2_7 | naive | 1,845 | 1,330.5 | -30.3 | n/a |
| confirmation | days_8_14 | naive | 2,163 | 1,473.9 | -35.9 | n/a |
| confirmation | days_15_30 | naive | 4,908 | 1,583.2 | -42.4 | n/a |
| confirmation | days_1_30 | naive | 9,222 | 1,486.1 | -37.1 | n/a |
| development | day_1 | naive | 447 | 862.6 | 0.5 | n/a |
| development | days_2_7 | naive | 2,682 | 1,194.3 | -37.7 | n/a |
| development | days_8_14 | naive | 3,129 | 1,218.5 | -33.2 | n/a |
| development | days_15_30 | naive | 7,152 | 1,310.2 | -40.2 | n/a |
| development | days_1_30 | naive | 13,410 | 1,250.7 | -36.8 | n/a |
| confirmation | day_1 | seasonal_naive | 306 | 1,176.7 | -15.6 | n/a |
| confirmation | days_2_7 | seasonal_naive | 1,845 | 1,154.4 | -13.1 | n/a |
| confirmation | days_8_14 | seasonal_naive | 2,163 | 1,293.7 | -19.3 | n/a |
| confirmation | days_15_30 | seasonal_naive | 4,908 | 1,410.5 | -26.8 | n/a |
| confirmation | days_1_30 | seasonal_naive | 9,222 | 1,324.1 | -22.1 | n/a |
| development | day_1 | seasonal_naive | 447 | 985.7 | -13.7 | n/a |
| development | days_2_7 | seasonal_naive | 2,682 | 987.2 | -13.8 | n/a |
| development | days_8_14 | seasonal_naive | 3,129 | 1,045.4 | -14.3 | n/a |
| development | days_15_30 | seasonal_naive | 7,152 | 1,142.9 | -22.3 | n/a |
| development | days_1_30 | seasonal_naive | 13,410 | 1,083.8 | -18.5 | n/a |
| confirmation | day_1 | same_weekday_mean_4w | 306 | 1,044.6 | -2.6 | n/a |
| confirmation | days_2_7 | same_weekday_mean_4w | 1,845 | 1,040.9 | -2.0 | n/a |
| confirmation | days_8_14 | same_weekday_mean_4w | 2,163 | 1,110.3 | -2.4 | n/a |
| confirmation | days_15_30 | same_weekday_mean_4w | 4,908 | 1,171.2 | -5.3 | n/a |
| confirmation | days_1_30 | same_weekday_mean_4w | 9,222 | 1,126.6 | -3.9 | n/a |
| development | day_1 | same_weekday_mean_4w | 447 | 871.8 | -0.5 | n/a |
| development | days_2_7 | same_weekday_mean_4w | 2,682 | 871.6 | -0.5 | n/a |
| development | days_8_14 | same_weekday_mean_4w | 3,129 | 940.1 | -2.8 | n/a |
| development | days_15_30 | same_weekday_mean_4w | 7,152 | 986.6 | -5.6 | n/a |
| development | days_1_30 | same_weekday_mean_4w | 13,410 | 948.9 | -3.8 | n/a |
| confirmation | day_1 | seasonal_exponential_smoothing | 306 | 807.6 | 20.7 | n/a |
| confirmation | days_2_7 | seasonal_exponential_smoothing | 1,845 | 1,042.4 | -2.1 | n/a |
| confirmation | days_8_14 | seasonal_exponential_smoothing | 2,163 | 1,204.8 | -11.1 | n/a |
| confirmation | days_15_30 | seasonal_exponential_smoothing | 4,908 | 1,347.1 | -21.1 | n/a |
| confirmation | days_1_30 | seasonal_exponential_smoothing | 9,222 | 1,234.9 | -13.9 | n/a |
| development | day_1 | seasonal_exponential_smoothing | 447 | 720.4 | 16.9 | n/a |
| development | days_2_7 | seasonal_exponential_smoothing | 2,682 | 873.8 | -0.7 | n/a |
| development | days_8_14 | seasonal_exponential_smoothing | 3,129 | 900.0 | 1.6 | n/a |
| development | days_15_30 | seasonal_exponential_smoothing | 7,152 | 1,020.2 | -9.2 | n/a |
| development | days_1_30 | seasonal_exponential_smoothing | 13,410 | 952.9 | -4.2 | n/a |
| confirmation | day_1 | legacy_linear_regression | 306 | 695.0 | 31.7 | n/a |
| confirmation | days_2_7 | legacy_linear_regression | 1,845 | 937.1 | 8.2 | n/a |
| confirmation | days_8_14 | legacy_linear_regression | 2,163 | 1,027.7 | 5.2 | n/a |
| confirmation | days_15_30 | legacy_linear_regression | 4,908 | 1,079.7 | 2.9 | n/a |
| confirmation | days_1_30 | legacy_linear_regression | 9,222 | 1,026.2 | 5.4 | n/a |
| development | day_1 | legacy_linear_regression | 447 | 693.0 | 20.1 | n/a |
| development | days_2_7 | legacy_linear_regression | 2,682 | 914.2 | -5.4 | n/a |
| development | days_8_14 | legacy_linear_regression | 3,129 | 1,021.0 | -11.6 | n/a |
| development | days_15_30 | legacy_linear_regression | 7,152 | 1,067.8 | -14.3 | n/a |
| development | days_1_30 | legacy_linear_regression | 13,410 | 1,013.6 | -10.9 | n/a |
| confirmation | day_1 | atlas_linear_regression | 306 | 679.5 | 33.2 | n/a |
| confirmation | days_2_7 | atlas_linear_regression | 1,845 | 906.5 | 11.2 | n/a |
| confirmation | days_8_14 | atlas_linear_regression | 2,163 | 969.1 | 10.6 | n/a |
| confirmation | days_15_30 | atlas_linear_regression | 4,908 | 1,019.9 | 8.3 | n/a |
| confirmation | days_1_30 | atlas_linear_regression | 9,222 | 974.0 | 10.2 | n/a |
| development | day_1 | atlas_linear_regression | 447 | 640.4 | 26.2 | n/a |
| development | days_2_7 | atlas_linear_regression | 2,682 | 784.9 | 9.5 | n/a |
| development | days_8_14 | atlas_linear_regression | 3,129 | 800.8 | 12.5 | n/a |
| development | days_15_30 | atlas_linear_regression | 7,152 | 813.0 | 13.0 | n/a |
| development | days_1_30 | atlas_linear_regression | 13,410 | 798.8 | 12.6 | n/a |

### target_aligned

Only targets reachable at every horizon, so all horizons share one set of days.

| period | bucket | model | forecasts | MAE | vs best baseline % | p |
|---|---|---|---|---|---|---|
| confirmation | day_1 | naive | 307 | 1,023.8 | 9.4 | 0.270 |
| confirmation | days_2_7 | naive | 1,842 | 1,427.1 | -26.3 | 0.004 |
| confirmation | days_8_14 | naive | 2,149 | 1,581.1 | -35.5 | <0.001 |
| confirmation | days_15_30 | naive | 4,912 | 1,687.6 | -45.8 | 0.001 |
| confirmation | days_1_30 | naive | 9,210 | 1,588.5 | -37.7 | <0.001 |
| development | day_1 | naive | 457 | 913.0 | 1.5 | 0.788 |
| development | days_2_7 | naive | 2,742 | 1,240.5 | -33.9 | <0.001 |
| development | days_8_14 | naive | 3,199 | 1,279.5 | -32.2 | <0.001 |
| development | days_15_30 | naive | 7,312 | 1,342.1 | -36.7 | <0.001 |
| development | days_1_30 | naive | 13,710 | 1,292.8 | -33.9 | <0.001 |
| confirmation | day_1 | seasonal_naive | 307 | 1,276.1 | -12.9 | 0.086 |
| confirmation | days_2_7 | seasonal_naive | 1,842 | 1,276.1 | -12.9 | 0.042 |
| confirmation | days_8_14 | seasonal_naive | 2,149 | 1,409.0 | -20.7 | 0.006 |
| confirmation | days_15_30 | seasonal_naive | 4,912 | 1,494.7 | -29.1 | 0.017 |
| confirmation | days_1_30 | seasonal_naive | 9,210 | 1,423.7 | -23.4 | 0.005 |
| development | day_1 | seasonal_naive | 457 | 1,081.6 | -16.7 | 0.003 |
| development | days_2_7 | seasonal_naive | 2,742 | 1,081.6 | -16.7 | 0.009 |
| development | days_8_14 | seasonal_naive | 3,199 | 1,121.1 | -15.8 | 0.009 |
| development | days_15_30 | seasonal_naive | 7,312 | 1,189.6 | -21.2 | <0.001 |
| development | days_1_30 | seasonal_naive | 13,710 | 1,148.4 | -18.9 | <0.001 |
| confirmation | day_1 | same_weekday_mean_4w | 307 | 1,158.4 | -2.5 | 0.431 |
| confirmation | days_2_7 | same_weekday_mean_4w | 1,842 | 1,158.4 | -2.5 | 0.520 |
| confirmation | days_8_14 | same_weekday_mean_4w | 2,149 | 1,225.5 | -5.0 | 0.039 |
| confirmation | days_15_30 | same_weekday_mean_4w | 4,912 | 1,243.0 | -7.4 | 0.025 |
| confirmation | days_1_30 | same_weekday_mean_4w | 9,210 | 1,219.2 | -5.7 | 0.018 |
| development | day_1 | same_weekday_mean_4w | 457 | 931.2 | -0.5 | 0.805 |
| development | days_2_7 | same_weekday_mean_4w | 2,742 | 931.2 | -0.5 | 0.806 |
| development | days_8_14 | same_weekday_mean_4w | 3,199 | 993.5 | -2.7 | 0.298 |
| development | days_15_30 | same_weekday_mean_4w | 7,312 | 1,039.3 | -5.9 | 0.011 |
| development | days_1_30 | same_weekday_mean_4w | 13,710 | 1,003.4 | -3.9 | 0.039 |
| confirmation | day_1 | seasonal_exponential_smoothing | 307 | 872.7 | 22.8 | 0.003 |
| confirmation | days_2_7 | seasonal_exponential_smoothing | 1,842 | 1,147.1 | -1.5 | 0.815 |
| confirmation | days_8_14 | seasonal_exponential_smoothing | 2,149 | 1,319.9 | -13.1 | 0.053 |
| confirmation | days_15_30 | seasonal_exponential_smoothing | 4,912 | 1,448.2 | -25.1 | 0.045 |
| confirmation | days_1_30 | seasonal_exponential_smoothing | 9,210 | 1,338.9 | -16.1 | 0.042 |
| development | day_1 | seasonal_exponential_smoothing | 457 | 789.8 | 14.8 | 0.001 |
| development | days_2_7 | seasonal_exponential_smoothing | 2,742 | 950.1 | -2.5 | 0.654 |
| development | days_8_14 | seasonal_exponential_smoothing | 3,199 | 966.6 | 0.1 | 0.977 |
| development | days_15_30 | seasonal_exponential_smoothing | 7,312 | 1,064.7 | -8.5 | 0.067 |
| development | days_1_30 | seasonal_exponential_smoothing | 13,710 | 1,009.7 | -4.6 | 0.297 |
| confirmation | day_1 | legacy_linear_regression | 307 | 758.7 | 32.9 | <0.001 |
| confirmation | days_2_7 | legacy_linear_regression | 1,842 | 1,025.9 | 9.2 | 0.157 |
| confirmation | days_8_14 | legacy_linear_regression | 2,149 | 1,117.2 | 4.3 | 0.418 |
| confirmation | days_15_30 | legacy_linear_regression | 4,912 | 1,153.1 | 0.4 | 0.996 |
| confirmation | days_1_30 | legacy_linear_regression | 9,210 | 1,106.1 | 4.1 | 0.396 |
| development | day_1 | legacy_linear_regression | 457 | 748.8 | 19.2 | <0.001 |
| development | days_2_7 | legacy_linear_regression | 2,742 | 959.0 | -3.5 | 0.512 |
| development | days_8_14 | legacy_linear_regression | 3,199 | 1,057.4 | -9.3 | 0.107 |
| development | days_15_30 | legacy_linear_regression | 7,312 | 1,099.1 | -12.0 | 0.112 |
| development | days_1_30 | legacy_linear_regression | 13,710 | 1,049.7 | -8.7 | 0.168 |
| confirmation | day_1 | atlas_linear_regression | 307 | 724.0 | 35.9 | <0.001 |
| confirmation | days_2_7 | atlas_linear_regression | 1,842 | 976.1 | 13.6 | 0.014 |
| confirmation | days_8_14 | atlas_linear_regression | 2,149 | 1,038.6 | 11.0 | 0.019 |
| confirmation | days_15_30 | atlas_linear_regression | 4,912 | 1,079.2 | 6.8 | 0.215 |
| confirmation | days_1_30 | atlas_linear_regression | 9,210 | 1,037.3 | 10.1 | 0.030 |
| development | day_1 | atlas_linear_regression | 457 | 685.8 | 26.0 | <0.001 |
| development | days_2_7 | atlas_linear_regression | 2,742 | 829.1 | 10.5 | 0.032 |
| development | days_8_14 | atlas_linear_regression | 3,199 | 839.1 | 13.3 | 0.033 |
| development | days_15_30 | atlas_linear_regression | 7,312 | 841.6 | 14.3 | 0.113 |
| development | days_1_30 | atlas_linear_regression | 13,710 | 833.3 | 13.7 | 0.076 |


## Bandwidth sensitivity of the selection and primary tests (post hoc)

Added after the independent review; descriptive only (the selection and the recommendation use the pre-registered test). The primary HAC variance truncates at lag h - 1; wider bandwidths allow for dependence beyond shared target days. HLN factor and t(T - 1) throughout; eff. n = T * gamma_0 / long-run variance.

| test | period | model | vs | mean diff | kernel | bandwidth | p | CI ± | eff. n |
|---|---|---|---|---|---|---|---|---|---|
| selection: best regression vs best baseline | development | atlas_linear_regression | same_weekday_median_5w | -135.1 | rectangular | 29 | 0.044 | 131.3 | 19.6 |
| selection: best regression vs best baseline | development | atlas_linear_regression | same_weekday_median_5w | -135.1 | rectangular | 59 | 0.002 | 85.5 | 46.2 |
| selection: best regression vs best baseline | development | atlas_linear_regression | same_weekday_median_5w | -135.1 | bartlett | 60 | 0.018 | 111.4 | 27.2 |
| selection: best regression vs best baseline | development | atlas_linear_regression | same_weekday_median_5w | -135.1 | bartlett | 90 | 0.006 | 96.7 | 36.1 |
| selection: best regression vs best baseline | development | atlas_linear_regression | same_weekday_median_5w | -135.1 | bartlett | 120 | 0.002 | 86.1 | 45.6 |
| H1: best development regression vs best development baseline | confirmation | atlas_linear_regression | same_weekday_median_5w | -127.3 | rectangular | 29 | 0.054 | 129.2 | 13.0 |
| H1: best development regression vs best development baseline | confirmation | atlas_linear_regression | same_weekday_median_5w | -127.3 | rectangular | 59 | 0.096 | 150.1 | 9.6 |
| H1: best development regression vs best development baseline | confirmation | atlas_linear_regression | same_weekday_median_5w | -127.3 | bartlett | 60 | 0.047 | 125.3 | 13.8 |
| H1: best development regression vs best development baseline | confirmation | atlas_linear_regression | same_weekday_median_5w | -127.3 | bartlett | 90 | 0.071 | 138.1 | 11.4 |
| H1: best development regression vs best development baseline | confirmation | atlas_linear_regression | same_weekday_median_5w | -127.3 | bartlett | 120 | 0.091 | 147.8 | 9.9 |
| H2: selected model vs the seasonal-naive reference | confirmation | atlas_linear_regression | seasonal_naive | -386.6 | rectangular | 29 | 0.003 | 258.4 | 20.8 |
| H2: selected model vs the seasonal-naive reference | confirmation | atlas_linear_regression | seasonal_naive | -386.6 | rectangular | 59 | 0.018 | 319.1 | 13.6 |
| H2: selected model vs the seasonal-naive reference | confirmation | atlas_linear_regression | seasonal_naive | -386.6 | bartlett | 60 | 0.003 | 255.9 | 21.2 |
| H2: selected model vs the seasonal-naive reference | confirmation | atlas_linear_regression | seasonal_naive | -386.6 | bartlett | 90 | 0.007 | 282.0 | 17.4 |
| H2: selected model vs the seasonal-naive reference | confirmation | atlas_linear_regression | seasonal_naive | -386.6 | bartlett | 120 | 0.010 | 293.6 | 16.1 |

## Fit diagnostics

| model | period | fits | failed | train rows | rank | rank-deficient fits | restricted cond. (median / max) | max train leverage | non-estimable | high leverage | clipped |
|---|---|---|---|---|---|---|---|---|---|---|---|
| atlas_linear_regression | confirmation | 336 | 0 | 884..1,219 | 18..18 of 18 | 0 | 7.0 / 7.2 | 0.114 | 0 | 0 | 0 |
| atlas_linear_regression | development | 486 | 0 | 369..854 | 18..18 of 18 | 0 | 6.7 / 7.3 | 0.259 | 0 | 0 | 0 |
| atlas_linear_regression | legacy_window | 119 | 0 | 1,130..1,248 | 18..18 of 18 | 0 | 7.2 / 7.4 | 0.110 | 0 | 0 | 0 |
| legacy_linear_regression | confirmation | 336 | 0 | 884..1,219 | 29..29 of 32 | 336 | 4,612.5 / 5,276.4 | 0.141 | 0 | 0 | 0 |
| legacy_linear_regression | development | 486 | 0 | 369..854 | 28..29 of 32 | 486 | 40.4 / 19,722.9 | 1.000 | 465 | 69 | 0 |
| legacy_linear_regression | legacy_window | 119 | 0 | 1,130..1,248 | 29..29 of 32 | 119 | 5,178.0 / 5,397.6 | 0.136 | 0 | 0 | 0 |

Regression MAE with and without non-estimable or high-leverage forecasts; `B*` (`same_weekday_median_5w`) on exactly the same kept and removed forecasts:

| period | model | forecasts | MAE | non-estimable | high leverage | kept | MAE kept | B* kept | removed | MAE removed | B* removed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| confirmation | atlas_linear_regression | 10,080 | 984.9 | 0 | 0 | 10,080 | 984.9 | 1,112.2 | 0 | n/a | n/a |
| confirmation | legacy_linear_regression | 10,080 | 1,047.3 | 0 | 0 | 10,080 | 1,047.3 | 1,112.2 | 0 | n/a | n/a |
| development | atlas_linear_regression | 14,580 | 817.9 | 0 | 0 | 14,580 | 817.9 | 953.0 | 0 | n/a | n/a |
| development | legacy_linear_regression | 14,580 | 1,041.9 | 465 | 69 | 14,046 | 986.2 | 895.2 | 534 | 2,509.5 | 2,472.6 |
| legacy_window | atlas_linear_regression | 3,135 | 542.8 | 0 | 0 | 3,135 | 542.8 | 797.6 | 0 | n/a | n/a |
| legacy_window | legacy_linear_regression | 3,135 | 592.0 | 0 | 0 | 3,135 | 592.0 | 797.6 | 0 | n/a | n/a |

## Caveats

- All numbers are historical re-forecasts from one frozen snapshot (`legacy-v0`, a single final data vintage). Values of recent days may have been revised after each origin, which favours methods that lean on the latest days; the size of this effect is unknown.
- Neither period is an untouched prospective sample: the whole series was examined by the original author and in M0/M1 before any model was specified, and the confirmation period contains the legacy holdout.
- Forecasts from neighbouring origins share target days. Counts of forecasts overstate the information; p-values use HAC variances and are indicative. Selection-period p-values are optimistic because the selection was made on the same data.
- MASE below 1 is not skill against seasonal naive (its scale is the in-sample one-step error); use the improvement columns, which compare the same forecasts.
- MAPE is secondary and not comparable with the legacy 4.93 % except in the legacy-holdout window table, where target days and formula are the same.
