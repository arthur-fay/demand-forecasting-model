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
| 1 | atlas_linear_regression | regression | 817.9 |
| 2 | same_weekday_median_5w | baseline | 953.0 |
| 3 | seasonal_exponential_smoothing | baseline | 989.1 |
| 4 | same_weekday_mean_4w | baseline | 990.9 |
| 5 | seasonal_naive | baseline | 1,129.1 |
| 6 | naive | baseline | 1,272.3 |

Best regression `atlas_linear_regression` vs best baseline `same_weekday_median_5w`: MAE difference -135.1 (95 % HAC CI -266.4 to -3.8), improvement 14.2 %, DM p (conservative) 0.044; margin met: yes; significant: yes.

**Selected: `atlas_linear_regression`.** atlas_linear_regression beats the best baseline same_weekday_median_5w by at least 3 % in MAE and the selection-period DM test is significant at 0.05.

## development: accuracy by horizon bucket

MAE, RMSE and bias in requests per day (bias = mean of prediction minus actual, with a 95 % HAC interval); WAPE and MAPE in %. `vs SN` is the MAE improvement over seasonal naive on common forecasts, `vs B*` over the best development baseline (`same_weekday_median_5w`); positive = better. `p` = DM p-value (conservative; n/a when too few origins).

| bucket | model | origins | forecasts | failed | MAE | RMSE | bias [95 % CI] | WAPE % | MASE | MAPE % | vs SN % | p | vs B* % | p |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| day_1 | naive | 486 | 486 | 0 | 900.7 | 1,300.8 | 0.2 [-115.8, 116.3] | 9.47 | 1.045 | 9.41 | 14.4 | 0.014 | 1.2 | 0.816 |
| day_1 | seasonal_naive | 486 | 486 | 0 | 1,052.8 | 1,551.3 | -6.3 [-144.7, 132.1] | 11.07 | 1.211 | 10.89 | ref |  | -15.4 | 0.005 |
| day_1 | same_weekday_mean_4w | 486 | 486 | 0 | 917.7 | 1,313.6 | -8.0 [-125.2, 109.2] | 9.65 | 1.054 | 9.50 | 12.8 | 0.007 | -0.6 | 0.747 |
| day_1 | same_weekday_median_5w | 486 | 486 | 0 | 912.1 | 1,288.1 | -67.0 [-181.8, 47.7] | 9.59 | 1.046 | 9.41 | 13.4 | 0.005 | B* |  |
| day_1 | seasonal_exponential_smoothing | 486 | 486 | 0 | 768.0 | 1,116.1 | -11.3 [-110.8, 88.3] | 8.08 | 0.886 | 7.95 | 27.1 | <0.001 | 15.8 | <0.001 |
| day_1 | legacy_linear_regression | 486 | 486 | 0 | 736.0 | 1,044.5 | 151.7 [59.5, 243.9] | 7.74 | 0.848 | 7.72 | 30.1 | <0.001 | 19.3 | <0.001 |
| day_1 | atlas_linear_regression | 486 | 486 | 0 | 663.9 | 974.1 | -0.5 [-87.4, 86.4] | 6.98 | 0.768 | 6.80 | 36.9 | <0.001 | 27.2 | <0.001 |
| days_2_7 | naive | 486 | 2,916 | 0 | 1,219.9 | 1,683.2 | -12.7 [-129.9, 104.6] | 12.81 | 1.410 | 12.80 | -15.7 | <0.001 | -33.7 | <0.001 |
| days_2_7 | seasonal_naive | 486 | 2,916 | 0 | 1,054.2 | 1,552.0 | -13.9 [-204.5, 176.8] | 11.07 | 1.214 | 10.90 | ref |  | -15.5 | 0.012 |
| days_2_7 | same_weekday_mean_4w | 486 | 2,916 | 0 | 917.6 | 1,313.9 | -15.9 [-246.2, 214.4] | 9.64 | 1.055 | 9.49 | 13.0 | 0.022 | -0.6 | 0.797 |
| days_2_7 | same_weekday_median_5w | 486 | 2,916 | 0 | 912.4 | 1,288.6 | -72.2 [-292.1, 147.8] | 9.58 | 1.048 | 9.40 | 13.4 | 0.012 | B* |  |
| days_2_7 | seasonal_exponential_smoothing | 486 | 2,916 | 0 | 923.9 | 1,326.9 | -19.4 [-184.1, 145.4] | 9.70 | 1.063 | 9.55 | 12.4 | <0.001 | -1.3 | 0.806 |
| days_2_7 | legacy_linear_regression | 486 | 2,916 | 0 | 953.5 | 1,298.2 | 279.4 [49.9, 508.8] | 10.01 | 1.091 | 10.04 | 9.5 | 0.114 | -4.5 | 0.386 |
| days_2_7 | atlas_linear_regression | 486 | 2,916 | 0 | 806.3 | 1,183.9 | 2.6 [-211.3, 216.4] | 8.47 | 0.929 | 8.18 | 23.5 | <0.001 | 11.6 | 0.015 |
| days_8_14 | naive | 486 | 3,402 | 0 | 1,260.1 | 1,783.3 | -31.1 [-182.9, 120.6] | 13.21 | 1.457 | 13.23 | -14.6 | <0.001 | -32.1 | <0.001 |
| days_8_14 | seasonal_naive | 486 | 3,402 | 0 | 1,099.2 | 1,627.1 | -33.1 [-271.4, 205.1] | 11.52 | 1.269 | 11.30 | ref |  | -15.2 | 0.011 |
| days_8_14 | same_weekday_mean_4w | 486 | 3,402 | 0 | 982.7 | 1,377.6 | -35.1 [-347.6, 277.4] | 10.30 | 1.131 | 10.17 | 10.6 | 0.102 | -3.0 | 0.276 |
| days_8_14 | same_weekday_median_5w | 486 | 3,402 | 0 | 954.1 | 1,356.5 | -91.8 [-375.7, 192.1] | 10.00 | 1.098 | 9.82 | 13.2 | 0.011 | B* |  |
| days_8_14 | seasonal_exponential_smoothing | 486 | 3,402 | 0 | 948.8 | 1,404.4 | -38.5 [-242.8, 165.7] | 9.94 | 1.093 | 9.81 | 13.7 | <0.001 | 0.6 | 0.907 |
| days_8_14 | legacy_linear_regression | 486 | 3,402 | 0 | 1,055.3 | 1,431.0 | 337.0 [-16.0, 690.1] | 11.06 | 1.204 | 11.08 | 4.0 | 0.567 | -10.6 | 0.064 |
| days_8_14 | atlas_linear_regression | 486 | 3,402 | 0 | 819.5 | 1,208.4 | 10.9 [-282.0, 303.8] | 8.59 | 0.946 | 8.29 | 25.4 | <0.001 | 14.1 | 0.017 |
| days_15_30 | naive | 486 | 7,776 | 0 | 1,320.5 | 1,830.9 | -80.9 [-319.4, 157.5] | 13.77 | 1.528 | 13.70 | -12.4 | 0.001 | -36.1 | <0.001 |
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

## What recursion costs (observed-input diagnostic)

Each origin's fitted regression applied to the *observed* previous days instead of its own predictions (the legacy holdout's procedure, with coefficients h - 1 days old). It reads actuals after the origin, so it is diagnostic only. At h = 1 both are the same.

| period | model | h | forecasts | MAE recursive | MAE observed inputs | MAPE % recursive | MAPE % observed inputs |
|---|---|---|---|---|---|---|---|
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

## Sensitivity analyses (pre-registered, descriptive)

### exclude_flagged_targets

Target days covered by a warning-level M1 data-quality flag removed.

| period | bucket | model | forecasts | MAE | vs best baseline % | p |
|---|---|---|---|---|---|---|
| development | day_1 | naive | 475 | 868.1 | 0.3 | n/a |
| development | days_2_7 | naive | 2,850 | 1,187.5 | -36.3 | n/a |
| development | days_8_14 | naive | 3,325 | 1,228.1 | -34.4 | n/a |
| development | days_15_30 | naive | 7,585 | 1,278.5 | -38.0 | n/a |
| development | days_1_30 | naive | 14,235 | 1,234.8 | -35.6 | n/a |
| development | day_1 | seasonal_naive | 475 | 1,011.4 | -16.1 | n/a |
| development | days_2_7 | seasonal_naive | 2,850 | 1,012.8 | -16.3 | n/a |
| development | days_8_14 | seasonal_naive | 3,325 | 1,062.0 | -16.2 | n/a |
| development | days_15_30 | seasonal_naive | 7,585 | 1,133.5 | -22.4 | n/a |
| development | days_1_30 | seasonal_naive | 14,235 | 1,088.6 | -19.6 | n/a |
| development | day_1 | same_weekday_mean_4w | 475 | 874.9 | -0.5 | n/a |
| development | days_2_7 | same_weekday_mean_4w | 2,850 | 874.7 | -0.4 | n/a |
| development | days_8_14 | same_weekday_mean_4w | 3,325 | 942.9 | -3.2 | n/a |
| development | days_15_30 | same_weekday_mean_4w | 7,585 | 983.6 | -6.2 | n/a |
| development | days_1_30 | same_weekday_mean_4w | 14,235 | 948.7 | -4.2 | n/a |
| development | day_1 | seasonal_exponential_smoothing | 475 | 734.0 | 15.7 | n/a |
| development | days_2_7 | seasonal_exponential_smoothing | 2,850 | 883.4 | -1.4 | n/a |
| development | days_8_14 | seasonal_exponential_smoothing | 3,325 | 910.5 | 0.3 | n/a |
| development | days_15_30 | seasonal_exponential_smoothing | 7,585 | 999.5 | -7.9 | n/a |
| development | days_1_30 | seasonal_exponential_smoothing | 14,235 | 946.6 | -4.0 | n/a |
| development | day_1 | legacy_linear_regression | 475 | 706.4 | 18.9 | n/a |
| development | days_2_7 | legacy_linear_regression | 2,850 | 915.7 | -5.1 | n/a |
| development | days_8_14 | legacy_linear_regression | 3,325 | 1,019.9 | -11.6 | n/a |
| development | days_15_30 | legacy_linear_regression | 7,585 | 1,052.1 | -13.6 | n/a |
| development | days_1_30 | legacy_linear_regression | 14,235 | 1,005.7 | -10.5 | n/a |
| development | day_1 | atlas_linear_regression | 475 | 631.3 | 27.5 | n/a |
| development | days_2_7 | atlas_linear_regression | 2,850 | 763.2 | 12.4 | n/a |
| development | days_8_14 | atlas_linear_regression | 3,325 | 776.7 | 15.0 | n/a |
| development | days_15_30 | atlas_linear_regression | 7,585 | 785.8 | 15.2 | n/a |
| development | days_1_30 | atlas_linear_regression | 14,235 | 774.0 | 15.0 | n/a |

### legacy_identification_boundary

Origins whose legacy design is affected by the leap-day step: trend, year and day of year are exactly collinear until 2025-01-01 (2025 targets not estimable from December 2024 origins), and afterwards the step is identified from very few 2025 rows (high leverage).

| period | bucket | model | forecasts | MAE | vs best baseline % | p |
|---|---|---|---|---|---|---|
| development | day_1 | naive | 425 | 839.3 | -10.3 | n/a |
| development | days_2_7 | naive | 2,550 | 1,082.2 | -44.7 | n/a |
| development | days_8_14 | naive | 2,975 | 1,097.7 | -39.4 | n/a |
| development | days_15_30 | naive | 6,800 | 1,150.5 | -40.1 | n/a |
| development | days_1_30 | naive | 12,750 | 1,114.2 | -39.9 | n/a |
| development | day_1 | seasonal_naive | 425 | 867.9 | -14.0 | n/a |
| development | days_2_7 | seasonal_naive | 2,550 | 883.9 | -18.2 | n/a |
| development | days_8_14 | seasonal_naive | 2,975 | 926.6 | -17.7 | n/a |
| development | days_15_30 | seasonal_naive | 6,800 | 990.1 | -20.6 | n/a |
| development | days_1_30 | seasonal_naive | 12,750 | 950.0 | -19.3 | n/a |
| development | day_1 | same_weekday_mean_4w | 425 | 775.1 | -1.8 | n/a |
| development | days_2_7 | same_weekday_mean_4w | 2,550 | 763.4 | -2.1 | n/a |
| development | days_8_14 | same_weekday_mean_4w | 2,975 | 810.7 | -2.9 | n/a |
| development | days_15_30 | same_weekday_mean_4w | 6,800 | 860.6 | -4.8 | n/a |
| development | days_1_30 | same_weekday_mean_4w | 12,750 | 826.6 | -3.8 | n/a |
| development | day_1 | seasonal_exponential_smoothing | 425 | 679.6 | 10.7 | n/a |
| development | days_2_7 | seasonal_exponential_smoothing | 2,550 | 769.0 | -2.8 | n/a |
| development | days_8_14 | seasonal_exponential_smoothing | 2,975 | 792.7 | -0.7 | n/a |
| development | days_15_30 | seasonal_exponential_smoothing | 6,800 | 878.6 | -7.0 | n/a |
| development | days_1_30 | seasonal_exponential_smoothing | 12,750 | 830.0 | -4.2 | n/a |
| development | day_1 | legacy_linear_regression | 425 | 666.9 | 12.4 | n/a |
| development | days_2_7 | legacy_linear_regression | 2,550 | 832.3 | -11.3 | n/a |
| development | days_8_14 | legacy_linear_regression | 2,975 | 890.2 | -13.0 | n/a |
| development | days_15_30 | legacy_linear_regression | 6,800 | 930.6 | -13.4 | n/a |
| development | days_1_30 | legacy_linear_regression | 12,750 | 892.7 | -12.1 | n/a |
| development | day_1 | atlas_linear_regression | 425 | 592.8 | 22.1 | n/a |
| development | days_2_7 | atlas_linear_regression | 2,550 | 669.1 | 10.5 | n/a |
| development | days_8_14 | atlas_linear_regression | 2,975 | 683.6 | 13.2 | n/a |
| development | days_15_30 | atlas_linear_regression | 6,800 | 724.7 | 11.7 | n/a |
| development | days_1_30 | atlas_linear_regression | 12,750 | 699.6 | 12.2 | n/a |

### holiday_related_targets

Targets on a federal holiday, its observed day, or the day before or after.

| period | bucket | model | forecasts | MAE | vs best baseline % | p |
|---|---|---|---|---|---|---|
| development | day_1 | naive | 39 | 1,337.5 | 6.2 | n/a |
| development | days_2_7 | naive | 234 | 1,512.7 | -6.1 | n/a |
| development | days_8_14 | naive | 273 | 1,737.2 | -23.6 | n/a |
| development | days_15_30 | naive | 624 | 1,438.4 | -4.2 | n/a |
| development | days_1_30 | naive | 1,170 | 1,519.6 | -8.8 | n/a |
| development | day_1 | seasonal_naive | 39 | 1,821.9 | -27.7 | n/a |
| development | days_2_7 | seasonal_naive | 234 | 1,821.9 | -27.7 | n/a |
| development | days_8_14 | seasonal_naive | 273 | 1,716.0 | -22.1 | n/a |
| development | days_15_30 | seasonal_naive | 624 | 1,544.5 | -11.9 | n/a |
| development | days_1_30 | seasonal_naive | 1,170 | 1,649.3 | -18.1 | n/a |
| development | day_1 | same_weekday_mean_4w | 39 | 1,443.8 | -1.2 | n/a |
| development | days_2_7 | same_weekday_mean_4w | 234 | 1,443.8 | -1.2 | n/a |
| development | days_8_14 | same_weekday_mean_4w | 273 | 1,470.5 | -4.6 | n/a |
| development | days_15_30 | same_weekday_mean_4w | 624 | 1,485.5 | -7.6 | n/a |
| development | days_1_30 | same_weekday_mean_4w | 1,170 | 1,472.3 | -5.4 | n/a |
| development | day_1 | seasonal_exponential_smoothing | 39 | 1,313.4 | 7.9 | n/a |
| development | days_2_7 | seasonal_exponential_smoothing | 234 | 1,497.5 | -5.0 | n/a |
| development | days_8_14 | seasonal_exponential_smoothing | 273 | 1,507.7 | -7.3 | n/a |
| development | days_15_30 | seasonal_exponential_smoothing | 624 | 1,330.9 | 3.6 | n/a |
| development | days_1_30 | seasonal_exponential_smoothing | 1,170 | 1,404.9 | -0.6 | n/a |
| development | day_1 | legacy_linear_regression | 39 | 1,228.9 | 13.8 | n/a |
| development | days_2_7 | legacy_linear_regression | 234 | 1,404.3 | 1.5 | n/a |
| development | days_8_14 | legacy_linear_regression | 273 | 1,448.1 | -3.0 | n/a |
| development | days_15_30 | legacy_linear_regression | 624 | 1,325.2 | 4.0 | n/a |
| development | days_1_30 | legacy_linear_regression | 1,170 | 1,366.5 | 2.2 | n/a |
| development | day_1 | atlas_linear_regression | 39 | 934.0 | 34.5 | n/a |
| development | days_2_7 | atlas_linear_regression | 234 | 1,050.6 | 26.3 | n/a |
| development | days_8_14 | atlas_linear_regression | 273 | 1,034.1 | 26.4 | n/a |
| development | days_15_30 | atlas_linear_regression | 624 | 1,038.2 | 24.8 | n/a |
| development | days_1_30 | atlas_linear_regression | 1,170 | 1,036.2 | 25.8 | n/a |

### other_targets

Every other target day.

| period | bucket | model | forecasts | MAE | vs best baseline % | p |
|---|---|---|---|---|---|---|
| development | day_1 | naive | 447 | 862.6 | 0.5 | n/a |
| development | days_2_7 | naive | 2,682 | 1,194.3 | -37.7 | n/a |
| development | days_8_14 | naive | 3,129 | 1,218.5 | -33.2 | n/a |
| development | days_15_30 | naive | 7,152 | 1,310.2 | -40.2 | n/a |
| development | days_1_30 | naive | 13,410 | 1,250.7 | -36.8 | n/a |
| development | day_1 | seasonal_naive | 447 | 985.7 | -13.7 | n/a |
| development | days_2_7 | seasonal_naive | 2,682 | 987.2 | -13.8 | n/a |
| development | days_8_14 | seasonal_naive | 3,129 | 1,045.4 | -14.3 | n/a |
| development | days_15_30 | seasonal_naive | 7,152 | 1,142.9 | -22.3 | n/a |
| development | days_1_30 | seasonal_naive | 13,410 | 1,083.8 | -18.5 | n/a |
| development | day_1 | same_weekday_mean_4w | 447 | 871.8 | -0.5 | n/a |
| development | days_2_7 | same_weekday_mean_4w | 2,682 | 871.6 | -0.5 | n/a |
| development | days_8_14 | same_weekday_mean_4w | 3,129 | 940.1 | -2.8 | n/a |
| development | days_15_30 | same_weekday_mean_4w | 7,152 | 986.6 | -5.6 | n/a |
| development | days_1_30 | same_weekday_mean_4w | 13,410 | 948.9 | -3.8 | n/a |
| development | day_1 | seasonal_exponential_smoothing | 447 | 720.4 | 16.9 | n/a |
| development | days_2_7 | seasonal_exponential_smoothing | 2,682 | 873.8 | -0.7 | n/a |
| development | days_8_14 | seasonal_exponential_smoothing | 3,129 | 900.0 | 1.6 | n/a |
| development | days_15_30 | seasonal_exponential_smoothing | 7,152 | 1,020.2 | -9.2 | n/a |
| development | days_1_30 | seasonal_exponential_smoothing | 13,410 | 952.9 | -4.2 | n/a |
| development | day_1 | legacy_linear_regression | 447 | 693.0 | 20.1 | n/a |
| development | days_2_7 | legacy_linear_regression | 2,682 | 914.2 | -5.4 | n/a |
| development | days_8_14 | legacy_linear_regression | 3,129 | 1,021.0 | -11.6 | n/a |
| development | days_15_30 | legacy_linear_regression | 7,152 | 1,067.8 | -14.3 | n/a |
| development | days_1_30 | legacy_linear_regression | 13,410 | 1,013.6 | -10.9 | n/a |
| development | day_1 | atlas_linear_regression | 447 | 640.4 | 26.2 | n/a |
| development | days_2_7 | atlas_linear_regression | 2,682 | 784.9 | 9.5 | n/a |
| development | days_8_14 | atlas_linear_regression | 3,129 | 800.8 | 12.5 | n/a |
| development | days_15_30 | atlas_linear_regression | 7,152 | 813.0 | 13.0 | n/a |
| development | days_1_30 | atlas_linear_regression | 13,410 | 798.8 | 12.6 | n/a |

### target_aligned

Only targets reachable at every horizon, so all horizons share one set of days.

| period | bucket | model | forecasts | MAE | vs best baseline % | p |
|---|---|---|---|---|---|---|
| development | day_1 | naive | 457 | 913.0 | 1.5 | 0.788 |
| development | days_2_7 | naive | 2,742 | 1,240.5 | -33.9 | <0.001 |
| development | days_8_14 | naive | 3,199 | 1,279.5 | -32.2 | <0.001 |
| development | days_15_30 | naive | 7,312 | 1,342.1 | -36.7 | <0.001 |
| development | days_1_30 | naive | 13,710 | 1,292.8 | -33.9 | <0.001 |
| development | day_1 | seasonal_naive | 457 | 1,081.6 | -16.7 | 0.003 |
| development | days_2_7 | seasonal_naive | 2,742 | 1,081.6 | -16.7 | 0.009 |
| development | days_8_14 | seasonal_naive | 3,199 | 1,121.1 | -15.8 | 0.009 |
| development | days_15_30 | seasonal_naive | 7,312 | 1,189.6 | -21.2 | <0.001 |
| development | days_1_30 | seasonal_naive | 13,710 | 1,148.4 | -18.9 | <0.001 |
| development | day_1 | same_weekday_mean_4w | 457 | 931.2 | -0.5 | 0.805 |
| development | days_2_7 | same_weekday_mean_4w | 2,742 | 931.2 | -0.5 | 0.806 |
| development | days_8_14 | same_weekday_mean_4w | 3,199 | 993.5 | -2.7 | 0.298 |
| development | days_15_30 | same_weekday_mean_4w | 7,312 | 1,039.3 | -5.9 | 0.011 |
| development | days_1_30 | same_weekday_mean_4w | 13,710 | 1,003.4 | -3.9 | 0.039 |
| development | day_1 | seasonal_exponential_smoothing | 457 | 789.8 | 14.8 | 0.001 |
| development | days_2_7 | seasonal_exponential_smoothing | 2,742 | 950.1 | -2.5 | 0.654 |
| development | days_8_14 | seasonal_exponential_smoothing | 3,199 | 966.6 | 0.1 | 0.977 |
| development | days_15_30 | seasonal_exponential_smoothing | 7,312 | 1,064.7 | -8.5 | 0.067 |
| development | days_1_30 | seasonal_exponential_smoothing | 13,710 | 1,009.7 | -4.6 | 0.297 |
| development | day_1 | legacy_linear_regression | 457 | 748.8 | 19.2 | <0.001 |
| development | days_2_7 | legacy_linear_regression | 2,742 | 959.0 | -3.5 | 0.512 |
| development | days_8_14 | legacy_linear_regression | 3,199 | 1,057.4 | -9.3 | 0.107 |
| development | days_15_30 | legacy_linear_regression | 7,312 | 1,099.1 | -12.0 | 0.112 |
| development | days_1_30 | legacy_linear_regression | 13,710 | 1,049.7 | -8.7 | 0.168 |
| development | day_1 | atlas_linear_regression | 457 | 685.8 | 26.0 | <0.001 |
| development | days_2_7 | atlas_linear_regression | 2,742 | 829.1 | 10.5 | 0.032 |
| development | days_8_14 | atlas_linear_regression | 3,199 | 839.1 | 13.3 | 0.033 |
| development | days_15_30 | atlas_linear_regression | 7,312 | 841.6 | 14.3 | 0.113 |
| development | days_1_30 | atlas_linear_regression | 13,710 | 833.3 | 13.7 | 0.076 |


## Fit diagnostics

| model | period | fits | failed | train rows | rank | rank-deficient fits | restricted cond. (median / max) | max train leverage | non-estimable | high leverage | clipped |
|---|---|---|---|---|---|---|---|---|---|---|---|
| atlas_linear_regression | development | 486 | 0 | 369..854 | 18..18 of 18 | 0 | 6.7 / 7.3 | 0.259 | 0 | 0 | 0 |
| legacy_linear_regression | development | 486 | 0 | 369..854 | 28..29 of 32 | 486 | 40.4 / 19,722.9 | 1.000 | 465 | 69 | 0 |

Regression MAE with and without non-estimable or high-leverage forecasts:

| period | model | forecasts | MAE | non-estimable | high leverage | kept | MAE kept |
|---|---|---|---|---|---|---|---|
| development | atlas_linear_regression | 14,580 | 817.9 | 0 | 0 | 14,580 | 817.9 |
| development | legacy_linear_regression | 14,580 | 1,041.9 | 465 | 69 | 14,046 | 986.2 |

## Caveats

- All numbers are historical re-forecasts from one frozen snapshot (`legacy-v0`, a single final data vintage). Values of recent days may have been revised after each origin, which favours methods that lean on the latest days; the size of this effect is unknown.
- Neither period is an untouched prospective sample: the whole series was examined by the original author and in M0/M1 before any model was specified, and the confirmation period contains the legacy holdout.
- Forecasts from neighbouring origins share target days. Counts of forecasts overstate the information; p-values use HAC variances and are indicative. Selection-period p-values are optimistic because the selection was made on the same data.
- MASE below 1 is not skill against seasonal naive (its scale is the in-sample one-step error); use the improvement columns, which compare the same forecasts.
- MAPE is secondary and not comparable with the legacy 4.93 % except in the legacy-holdout window table, where target days and formula are the same.
