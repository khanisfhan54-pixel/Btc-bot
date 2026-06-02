# SHPE target definition

Version: `shpe-target.v1.0.0-temporary`

This document fixes the offline ML target used by the SHPE training workflow.
The repository explicitly defines SHPE as a probability layer that estimates the
likelihood of a stop-hunt sweep at each 5-minute bar and never places orders.
No existing exact label generator, triple-barrier target, or future-return target
was found in the repository. Therefore this target is a documented temporary
assumption derived from the existing stop-hunt / liquidity-sweep semantics.

## Event being predicted

A future BTCUSDT stop-hunt sweep: price trades through a recent swing-liquidity
pool above or below the market, then rejects back inside that pool within the
prediction horizon.

## Bar interval

5-minute bars only. Feature rows are actionable only after the current 5-minute
bar has closed.

## Prediction horizon

The next 3 completed 5-minute bars after the feature bar, i.e. 15 minutes.

## Positive label condition

For a feature row at bar `t`, label `1` when any bar in `(t, t + 3]` satisfies
one of these conditions using only pools known at `t`:

- High-side sweep: future `high >= prior_high_pool * (1 + sweep_buffer_bps / 10000)`
  and future `close <= prior_high_pool`.
- Low-side sweep: future `low <= prior_low_pool * (1 - sweep_buffer_bps / 10000)`
  and future `close >= prior_low_pool`.

The default `prior_high_pool` is the maximum high over the 20 completed bars up
to and including bar `t`; the default `prior_low_pool` is the minimum low over
the same past-only window. The default `sweep_buffer_bps` is `1.0`.

## Negative label condition

Label `0` when the full 3-bar future horizon is available and no positive
high-side or low-side sweep condition occurs.

## Neutral / ignore zone

Rows without enough past bars to form pools, rows without the full future
horizon, rows with non-positive prices, and rows whose pool range is not finite
are excluded from training/evaluation. There is no separate neutral class in
this version.

## Regime / context conditioning

The SHPE model uses the existing regime-conditional classifier. Offline datasets
store the row's `regime` value when present; otherwise they use `unknown`.
Regime labels are used for model routing only and are never used to create the
future target.

## No-lookahead rule

Features and liquidity pools for bar `t` may use only data available at or before
the close/feature-available timestamp of bar `t`. Labels may inspect future bars
only after the feature vector has already been fixed. Rows are split strictly in
time order for walk-forward training and validation; random splits are forbidden.
