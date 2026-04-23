from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from replay_engine import ReplayEngine


@dataclass
class CyclicNode:
    name: str
    child: object | None = None


class MutableBox:
    def __init__(self, value):
        self.value = value
        self.meta = {"items": [1, 2, 3]}

    def __hash__(self):
        return id(self)


def test_safe_payload_handles_nested_object_state_without_aliasing():
    engine = ReplayEngine()
    box = MutableBox(7)

    frozen = engine._safe_payload(box, depth=2)
    box.meta["items"].append(99)

    assert frozen["state"]["meta"]["items"] == [1, 2, 3]


def test_safe_payload_handles_cyclic_dataclass_graph():
    engine = ReplayEngine()
    node = CyclicNode("root")
    node.child = node

    frozen = engine._safe_payload({"node": node}, depth=2)

    assert frozen["node"]["__dataclass__"].endswith("CyclicNode")
    assert frozen["node"]["fields"]["child"] == "__CYCLE__"


def test_safe_payload_handles_set_and_frozenset_stably():
    engine = ReplayEngine()
    payload = {
        "items": {MutableBox(1), MutableBox(2)},
        "freeze": frozenset({MutableBox(3), MutableBox(4)}),
    }

    frozen = engine._safe_payload(payload, depth=2)

    assert isinstance(frozen["items"], tuple)
    assert isinstance(frozen["freeze"], tuple)
    assert len(frozen["items"]) == 2
    assert len(frozen["freeze"]) == 2


def test_safe_payload_copies_object_ndarray():
    engine = ReplayEngine()
    arr = np.array([{"x": [1, 2]}], dtype=object)

    frozen = engine._safe_payload(arr, depth=2)
    frozen_again = engine._safe_payload(arr, depth=2)
    arr[0]["x"].append(3)

    assert isinstance(frozen, np.ndarray)
    assert frozen.dtype == object
    assert frozen.shape == arr.shape
    assert frozen_again.shape == arr.shape
