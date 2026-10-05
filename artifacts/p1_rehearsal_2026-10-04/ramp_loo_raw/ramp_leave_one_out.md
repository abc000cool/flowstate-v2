# Ramp estimation: leave-one-out validation

- **Inputs**: data/mndot/mndot_i94_wb_stpaul/detectors.csv
- **Data hash (sha256 of the loaded rows)**: ce44356d7a26cd24
- **Data-quality masking**: not applied
- **Code**: 75a54b7ca45a
- **Created**: 2026-10-05T03:16:11Z

Each ramp that has a detector was, in turn, treated as if it had none: its flow was estimated from the other counts and compared with what its own detector measured. Counts were assumed accurate to ±5% (linear combination); the measured ramp's own error is not in the interval, so coverage below the nominal level can come from either side.

## Method

Between two neighbouring mainline stations every vehicle is conserved: the flow counted downstream (one travel time later) equals the flow counted upstream plus the entrances minus the exits in between. Travel time comes from the station spacing and the speeds the two stations measure; all counts are read at the unmeasured ramp's position by shifting them by their travel time, then averaged over each period. With one ramp without a count in a segment, its flow is what the counts leave over; with more, the counts fix only their net flow, and a stated split assumption per extra ramp is needed. Negative results are set to zero and reported. The interval propagates a stated per-detector count error; it does not include the change in the number of vehicles queued between the stations, so periods in congestion are flagged.

## Per ramp

| Ramp | Kind | Segment | Periods | Mean measured | Bias | MAE | Relative error | Coverage | Clipped |
|---|---|---|---|---|---|---|---|---|---|
| rnd_88835 | off_ramp | S1063→S1064 | 558 | 0 | +239 | 239 | — | 54% | 2 |
| rnd_88833 | off_ramp | S1064→S1065 | 787 | 192 | +303 | 303 | 157.5% | 6% | 0 |
| rnd_88831 | on_ramp | S1064→S1065 | 787 | 286 | -280 | 280 | 97.9% | 7% | 545 |
| rnd_88827 | on_ramp | S1065→S1066 | 807 | 1 | +297 | 297 | 21428.0% | 5% | 15 |
| rnd_88823 | on_ramp | S1066→S1067 | 827 | 0 | +281 | 281 | — | 22% | 57 |
| rnd_88819 | on_ramp | S1067→S1068 | 821 | 244 | -7 | 77 | 31.4% | 87% | 17 |
| rnd_88817 | off_ramp | S1067→S1068 | 821 | 230 | +18 | 69 | 29.9% | 87% | 118 |
| rnd_88811 | off_ramp | S1947→S1069 | 841 | 211 | +106 | 114 | 54.1% | 92% | 0 |
| rnd_88807 | on_ramp | S1069→S1070 | 828 | 804 | -91 | 107 | 13.3% | 87% | 0 |
| rnd_87205 | off_ramp | S1948→S792 | 827 | 2,701 | -2,430 | 2,456 | 91.0% | 17% | 33 |
| rnd_91040 | on_ramp | S790→S97 | 838 | 910 | -872 | 873 | 96.0% | 0% | 667 |
| rnd_87221 | off_ramp | S790→S97 | 838 | 209 | +992 | 992 | 475.1% | 0% | 0 |

## Pooled over tested ramps

| Periods | Bias | MAE | RMSE | Relative error | Coverage |
|---|---|---|---|---|---|
| all: 9580 | -132 | 518 | 1,062 | 103.8% | 39% |
| free flow: 9029 | -104 | 485 | 1,006 | 108.4% | 39% |
| congested: 551 | -592 | 1,055 | 1,739 | 78.6% | 36% |

Median relative error over ramps: 93.5%.
