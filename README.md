# SAKMLB: Speed-Aware K-Means Load Balancing with Admission Control in CloudAnalyst

Code and raw results for the paper "Speed-Aware K-Means Load Balancing with Admission Control for Heterogeneous Cloud
Workloads Using CloudAnalyst" (round 11).

## Contents
- `CloudAnalyst/`: the modified CloudAnalyst (CA) project (Eclipse, Java 8; libraries in `jars/`, sources in `source/`).
  `CA_H1.sim` is the configuration used for every run. Changes to the original CA code are listed in the `CHANGELOG_*.md`
  files and in Table 2 of the paper. SAKMLB is the speed-aware mode of `KMeanVmLoadBalancer.java`
  (launch options `-Dca.km2=true -Dca.tshare=90000`; the study driver sets them for the KM2 label). ETLBA and ReT-ELBa
  are `EtlbaVmLoadBalancer.java` and `RetElbaVmLoadBalancer.java`. Topologies B and C are in `study/Topologies.java`.
- `results/`: the raw result files behind every number in the paper, one row per simulation run:
  - `study_results.csv`: uniform workload (RR, capped and uncapped ESCEL and Throttled), seeds 1 to 10.
  - `study_round2.csv`: scenario analysis (longer requests, 1.5x users, heterogeneous lengths, DR) and instrumented mechanism runs.
  - `study_heavy.csv`: heavy heterogeneous load in topology A (RR, ESCEL, Throttled, SAKMLB; CDC, ORT, DR; 1.0x and 1.5x users).
  - `study_round3.csv`: capped ESCEL, ETLBA, ReT-ELBa, topologies B and C, scalability (3,000, 7,000, 10,000 VMs), smoothing factor, overhead and learning-phase metrics.
  - `study_achunk.csv`: instrumented topology A runs that give the mean completion time of all sub-cloudlets.
  The raw files can also hold rows for variants that the paper does not report. The paper uses only the labels listed in `analysis/cells.py`.
- `analysis/`: Python scripts (pandas, numpy, scipy, matplotlib) that read `results/` only.
  `python mktab9.py` regenerates the rows of Table 8 (`out/tab9.tex`); `figs.py`, `figlearn.py` and `diag.py` regenerate
  Figures 1 to 7 into `figures/`. `cells.py` maps each table cell to its CSV label and `expected.py` lists the values printed in the paper.

## Reproducing a run
Compile the project (Eclipse, or `javac -cp "CloudAnalyst/jars/*" -d bin $(find CloudAnalyst/source -name "*.java")`) and run
`cloudsim.ext.study.StudyDriver` with the program arguments
`CA_H1.sim <results.csv> <seeds|auto> <workers> <GROUP>`, for example
`CA_H1.sim study_round3.csv auto 4 ROUND3` (843 runs, about 4 hours with 4 workers). Groups: see `README_HEAVY.md`
(HEAVY, HTUNE), `README_ROUND2.md` and `README_ROUND3.md` (ROUND3, optional SCALE_ORT and SCALE_DR) and the ACHUNK group defined in `StudyDriver.java`
(topology A; ESCEL, Throttled and SAKMLB with per-sub-cloudlet instrumentation).
Each seed fixes the workload and routing draws (`-Dca.seed=N`). Replay noise is about 0.01 ms in RT and DCPT.

## Version
Tag this repository `v1.0` after upload; the paper's Data Availability statement cites release v1.0.

