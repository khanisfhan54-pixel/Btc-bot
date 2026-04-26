def apply_conflict(signal_conf, alpha_dir, regime_label):
    regime_direction_map={"TREND":"LONG","BEAR":"SHORT"}
    expected=regime_direction_map.get(regime_label)
    if expected and alpha_dir not in ("NEUTRAL",expected):
        return signal_conf*0.7
    return signal_conf

def test_alpha_alignment_penalty():
    orig=0.8
    final=apply_conflict(orig,"SHORT","TREND")
    assert final < orig
