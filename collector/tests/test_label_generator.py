import numpy as np
import pandas as pd

from collector.pipeline.label_generator import _generate_label_columns


def test_generate_label_columns_drops_nan_labels_for_one_second_horizon():
    df = pd.DataFrame({"mid_price": np.arange(100.0, 200.0)})

    labeled_df, _ = _generate_label_columns(df, grid_ms=100, threshold=0.00005, horizons_s=[1])

    assert len(labeled_df) <= 90
    assert labeled_df["return_1s"].isna().sum() == 0
    assert labeled_df["direction_1s"].isna().sum() == 0


def test_generate_label_columns_drops_exactly_longest_horizon_shift_rows(capsys):
    df = pd.DataFrame({"mid_price": np.arange(100.0, 200.0)})

    labeled_df, rows_dropped = _generate_label_columns(df, grid_ms=100, threshold=0.00005, horizons_s=[1, 2])

    assert rows_dropped == 20
    assert len(labeled_df) == 80
    label_cols = ["return_1s", "return_2s", "direction_1s", "direction_2s"]
    assert labeled_df[label_cols].isna().sum().sum() == 0
    assert "Dropped 20 rows with NaN labels (20.00%)" in capsys.readouterr().out
