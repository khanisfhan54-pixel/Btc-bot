import threading
import pytest
from replay_engine import ReplayEngine

class E:
    def __init__(self, strict=True):
        self._strict_replay = strict
        self._fsm_error = None
        self.calls=[]
        self._equity=0.0
        self._confirmed_regime=''
        self._smoothed_garch_prob=[0.5,0.5]
        self.nhhmm_prior=[0.3,0.3,0.4]
        self._regime_state_probs=[0.25]*4
        self.range_ticks=0.0
        self.last_signed_position_size=0.0
        self._last_valid_vol=0.0
    def update(self,p): self.calls.append(("update",p)); self._equity += float(p.get('price',0)); self._confirmed_regime=p.get('regime','')
    def _trigger_circuit_breaker(self,p): self.calls.append(("cb",p))
    def _self_heal(self,p): self.calls.append(("heal",p))
    def serialize_state(self): return {"schema_version":"2.4","eq":self._equity,"reg":self._confirmed_regime}
    def load_state(self,s): self._equity=s.get('eq',0); self._confirmed_regime=s.get('reg','')

def test_baseline_record_event_fields():
    r=ReplayEngine(); r.record_event('update_start',{})
    e=r.last_events(1)[0]
    for k in ["id","type","ts_ns","ts_monotonic_ns","source","dropped_events_before","payload"]: assert k in e

def test_issue_10_record_event_custom_source():
    r=ReplayEngine(); r.record_event('update_start',{},source='signal_engine')
    assert r.last_events(1)[0]['source']=='signal_engine'

def test_issue_11_monotonic_timestamp_nondecreasing():
    r=ReplayEngine(); r.record_event('update_start',{}); r.record_event('update_end',{})
    ev=r.last_events(2); assert ev[1]['ts_monotonic_ns']>=ev[0]['ts_monotonic_ns']

def test_issue_13_replay_gap_marker_emitted():
    r=ReplayEngine(max_events=1); r.record_event('update_start',{}); r.record_event('update_end',{})
    assert list(r.replay())[0]['type']=='__REPLAY_GAP__'

def test_issue_20_circuit_breaker_full_payload_forwarded():
    r=ReplayEngine(); payload={"reason":"x","market_state":{"a":1}}; r.record_event('update_start',{}); r.record_event('circuit_breaker',payload)
    seen={}
    orig = ReplayEngine._run_replay_callback_with_timeout
    def _spy(self, engine, callback_name, callback_arg, timeout_seconds):
        if callback_name == "_trigger_circuit_breaker":
            seen["arg"] = callback_arg
        return orig(self, engine, callback_name, callback_arg, timeout_seconds)
    ReplayEngine._run_replay_callback_with_timeout = _spy
    try:
        e=E(strict=False); r.apply_events(e)
    finally:
        ReplayEngine._run_replay_callback_with_timeout = orig
    assert isinstance(seen.get("arg"), dict) and "market_state" in seen["arg"]

def test_issue_4_copy_any_logs_on_deepcopy_failure():
    r=ReplayEngine(); out=r._copy_any(threading.Lock()); assert isinstance(out,dict); assert r.copy_fidelity_failures()>=1

def test_issue_15_clear_reset_counters_true():
    r=ReplayEngine(max_events=1); r.record_event('update_start',{}); r.record_event('update_end',{}); r.clear(reset_counters=True)
    assert r._event_id==0 and r._dropped_events==0 and r._dropped_snapshot_count==0

def test_issue_12_snapshot_contains_timestamp_and_regime():
    r=ReplayEngine(); r.snapshot({"_confirmed_regime":"bear","equity":1})
    s=r._snapshots[-1]; assert 'ts_ns' in s and 'ts_monotonic_ns' in s and s['regime_marker']=='bear'
