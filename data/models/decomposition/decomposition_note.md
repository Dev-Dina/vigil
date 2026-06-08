# Pan-indication GBT PR-AUC Decomposition

**Pan-indication (pooled) TEST GBT PR-AUC: 0.6968**

## Per-indication PR-AUC (top 8 by arm count)

| Indication | n_trials | n_arms | test_pr_auc | split_type | n_train_arms | n_test_arms |
|------------|----------|--------|-------------|------------|--------------|-------------|
| T2D      |      863 |   2666 |       0.3809 |           temporal |         1669 |        516 |
| PSO      |      352 |   1407 |       0.6474 |           temporal |          899 |        240 |
| IBD      |      354 |   1207 |       0.4975 |           temporal |          646 |        229 |
| ALZ      |      332 |    927 |       0.7747 |           temporal |          581 |        172 |
| MDD      |      310 |    791 |       0.3206 |           temporal |          481 |        152 |
| MS       |      259 |    719 |       0.6201 |           temporal |          452 |        142 |
| HF       |      235 |    548 |       0.5829 |           temporal |          320 |        107 |
| RA       |        4 |      9 |          N/A |     small-N random |            8 |          1 |

## Interpretation: Between- vs Within-Indication Signal

The pan-indication pooled PR-AUC (0.6968) summarises how well the GBT separates high- from
low-dropout arms across the full modelling cohort (Phase 1/2 through Phase 3, all therapeutic
areas, all sponsor classes).

**If within-indication PR-AUC ≈ pan-indication PR-AUC** (or higher), the model captures
participant- and arm-level structure inside each indication — the predictive signal does not
merely reflect indication-level base rates. This is the preferred scenario for a retention
intelligence platform: the model generalises within a disease domain, not only across them.

**If within-indication PR-AUC is substantially below the pooled value**, much of the pooled
gain comes from the model learning which indications carry higher baseline dropout (an
indication-level intercept effect). The model would then perform well pooled but poorly when
deployed within a single indication — a critical operational limitation.

**Small-N caution**: indications with fewer than 9 trials use a random 2-fold split. With small
test sets the PR-AUC estimate is high-variance; treat those numbers as directional only.
A null result ("N/A") means the test fold contained only one class after thresholding.
