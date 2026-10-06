# Pre-specified hypotheses and analysis for the confirmatory runs (seeds 21 to 30)

Written on 7 October 2026, before any run on seeds 21 to 30. No analysis so far has used these seeds.
Commit this file to the repository before running the batch, so that its date is verifiable.

## Why
The mean completion time of all sub-cloudlets was added to the paper after a first analysis on seeds 1 to 10, because
CloudAnalyst's RT and DCPT stop at the first sub-cloudlet of a request group. To show that the result does not depend on
having looked at those seeds, we fix the hypotheses here and test them once on new seeds.

## Configurations (fixed)
Heavy heterogeneous lengths (1200, 20000, 1200, 6000, 20000, 6000), 1.5x users, topologies A, B and C, brokers CDC and
ORT, seeds 21 to 30. SAKMLB (alpha 0.3, K = 2, re-cluster every 25 completions), NKMLB (K = 2, nominal MIPS and RAM)
and capped ESCEL all with T_share = 90,000. 180 runs, no tuning of any setting on these seeds.

## Metric (fixed)
Mean completion time over all sub-cloudlets = mean of (waiting time + execution time), computed from the per-class
columns DC{1..5}_{v1L,v1H,v2L,v2H}_{n,wait,exec} of each run. For each run the mean is weighted by the number of
sub-cloudlets n. The number of sub-cloudlets is identical across algorithms on a given seed.

## Hypotheses (fixed)
H1. SAKMLB has a lower mean completion time than NKMLB in each of topologies A, B and C, under CDC and under ORT
    (6 tests).
H2. SAKMLB has a lower mean completion time than capped ESCEL in each of topologies A and B, under CDC and under ORT
    (4 tests). We make no claim for topology C, where the earlier result was a tie.

## Decision rule (fixed)
For each test, take the paired difference (SAKMLB minus the comparison algorithm) over seeds 21 to 30. The hypothesis is
confirmed if the Holm-adjusted two-sided p-value of a paired t-test, adjusted over all 10 tests, is below 0.05 and the mean
difference is negative. We also report the 95% confidence interval of the difference and the number of seeds (of 10) in
which SAKMLB is lower. A hypothesis that fails is reported as failed.

## What we do not claim
No superiority of SAKMLB over NKMLB on RT or DCPT. RT, DCPT and the completion time of every algorithm are reported for
all settings, including those where SAKMLB does not win.
