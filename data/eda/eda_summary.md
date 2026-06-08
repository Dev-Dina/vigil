# Vigil EDA — real AACT reference cohort

- Source: real hosted AACT snapshot (data/clean/ref_*.parquet); build-time only
- Studies after population filter: **73,073**
- Trials with participant-flow arms: 72,842
- Arms: 182,240 | Withdrawal rows: 384,802

## Overall dropout rate
- trial mean: 0.2018
- trial median: 0.0933
- participant-weighted: 0.151

## Dropout by phase
- EARLY_PHASE1: 0.1389 (n=464)
- NA: 0.1418 (n=21918)
- PHASE1: 0.1929 (n=5512)
- PHASE1/PHASE2: 0.2945 (n=3935)
- PHASE2: 0.2524 (n=18807)
- PHASE2/PHASE3: 0.2156 (n=1319)
- PHASE3: 0.2323 (n=13102)
- PHASE4: 0.158 (n=7785)

## Dropout by therapeutic area
- ONCOLOGY: 0.3597 (n=14339)
- MUSCULOSKELETAL: 0.2421 (n=2613)
- PSYCHIATRY: 0.2048 (n=6244)
- IMMUNOLOGY_RHEUMATOLOGY: 0.1996 (n=3366)
- GASTROENTEROLOGY: 0.1892 (n=1819)
- CARDIOVASCULAR: 0.1845 (n=6716)
- RENAL_UROLOGY: 0.1704 (n=1643)
- NEUROLOGY: 0.1661 (n=4682)
- DERMATOLOGY: 0.1599 (n=2104)
- ENDOCRINE_METABOLIC: 0.156 (n=4399)
- RESPIRATORY: 0.142 (n=3212)
- OTHER: 0.1351 (n=12086)
- INFECTIOUS_DISEASE: 0.1289 (n=7282)
- OPHTHALMOLOGY: 0.1136 (n=2337)

## Dropout by enrollment size
- 1-50: 0.2035 (n=32249)
- 51-100: 0.1899 (n=12971)
- 101-250: 0.2038 (n=13114)
- 251-500: 0.2051 (n=7027)
- 501-1000: 0.2251 (n=4263)
- 1000+: 0.1857 (n=3218)

## Dropout by sponsor class
- ACADEMIC_OTHER: 0.1618 (n=35544)
- INDUSTRY: 0.2445 (n=33218)
- NIH: 0.2343 (n=2319)
- OTHER_GOV: 0.161 (n=1761)

## Dropout by site count (single vs multi-site)
- MULTI_SITE: 0.2558 (n=34065)
- SINGLE_SITE: 0.1544 (n=38777)

## Withdrawal-reason mix (controlled vocab)
- OTHER: 0.1754
- WITHDRAWAL_BY_SUBJECT: 0.1477
- STUDY_TERMINATED: 0.1217
- LOST_TO_FOLLOWUP: 0.1071
- PROTOCOL_VIOLATION: 0.0959
- DEATH: 0.0909
- ADMINISTRATIVE: 0.0818
- ADVERSE_EVENT: 0.0817
- LACK_OF_EFFICACY: 0.0762
- PHYSICIAN_DECISION: 0.0142
- NONCOMPLIANCE: 0.0053
- PREGNANCY: 0.002

## Covariate -> dropout associations (sign + effect size)
- {'covariate': 'enrollment', 'pearson_r': -0.0057, 'sign': 'negative', 'high_minus_low_tercile_dropout_gap': -0.0055, 'n': 72842}
- {'covariate': 'n_arms', 'pearson_r': -0.004, 'sign': 'negative', 'high_minus_low_tercile_dropout_gap': nan, 'n': 72842}
- {'covariate': 'n_sites', 'pearson_r': 0.1353, 'sign': 'positive', 'high_minus_low_tercile_dropout_gap': 0.1332, 'n': 72842}
- {'covariate': 'n_countries', 'pearson_r': 0.1844, 'sign': 'positive', 'high_minus_low_tercile_dropout_gap': nan, 'n': 72842}
- {'covariate': 'planned_duration_days', 'pearson_r': 0.1277, 'sign': 'positive', 'high_minus_low_tercile_dropout_gap': 0.1126, 'n': 72842}
- {'covariate': 'min_age_years', 'pearson_r': -0.0277, 'sign': 'negative', 'high_minus_low_tercile_dropout_gap': nan, 'n': 70391}
- {'covariate': 'max_age_years', 'pearson_r': 0.0692, 'sign': 'positive', 'high_minus_low_tercile_dropout_gap': 0.0432, 'n': 35256}
- {'covariate': 'blinded(vs open label)', 'blinded_mean_dropout': 0.1618, 'open_mean_dropout': 0.2344, 'n': 72842}

## Figures
- dropout_rate_hist: `C:\Users\LEGION\Desktop\ThisIsIt\Codebase\Vigil\data\eda\figures\dropout_rate_hist.png`
- dropout_by_phase: `C:\Users\LEGION\Desktop\ThisIsIt\Codebase\Vigil\data\eda\figures\dropout_by_phase.png`
- dropout_by_therapeutic_area: `C:\Users\LEGION\Desktop\ThisIsIt\Codebase\Vigil\data\eda\figures\dropout_by_therapeutic_area.png`
- dropout_by_enrollment_size: `C:\Users\LEGION\Desktop\ThisIsIt\Codebase\Vigil\data\eda\figures\dropout_by_enrollment_size.png`
- enrollment_hist: `C:\Users\LEGION\Desktop\ThisIsIt\Codebase\Vigil\data\eda\figures\enrollment_hist.png`
- withdrawal_reason_mix: `C:\Users\LEGION\Desktop\ThisIsIt\Codebase\Vigil\data\eda\figures\withdrawal_reason_mix.png`
- missingness: `C:\Users\LEGION\Desktop\ThisIsIt\Codebase\Vigil\data\eda\figures\missingness.png`
