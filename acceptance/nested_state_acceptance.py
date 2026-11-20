# -*- coding: utf-8 -*-
"""Nested pipeline state propagation acceptance readings (zero external dependencies).

One minimal governance event carries both a chained field mapping and processing
identifiers. It is driven through three paths:

1. preprocessing nested transformation   (``NestedProcessingTransformation``)
2. query postprocessing nested transformation (``NestedQueryPostprocessingTransformation``)
3. direct use of the child pipeline without nesting

Collected readings:

* per-container key counts and per-key content of the five state containers after the
  public method absorbed the nested state (``applied``, ``applied_ids``,
  ``field_name_applied_ids``, ``field_mappings``, ``state``);
* the bookkeeping trail left behind by the direct-use path (ledger entry count);
* how many times each nested transformation calls ``ProcessingPipeline.absorb_nested_state``.

The script imports pySigma from this repository only and prints a deterministic report.
Set ``PYSIGMA_REPO_ROOT`` to point at another checkout (e.g. a pristine baseline snapshot)
to collect comparable readings from that tree.

Run: python acceptance/nested_state_acceptance.py
"""

import os
import sys

REPO_ROOT = os.environ.get("PYSIGMA_REPO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)
sys.path.insert(0, REPO_ROOT)

from sigma.processing.pipeline import ProcessingPipeline, ProcessingItem, QueryPostprocessingItem
from sigma.processing.postprocessing import (
    EmbedQueryTransformation,
    NestedQueryPostprocessingTransformation,
    ReplaceQueryTransformation,
)
from sigma.processing.transformations import (
    FieldMappingTransformation,
    NestedProcessingTransformation,
    SetStateTransformation,
)
from sigma.rule import SigmaRule

CONTAINERS = (
    "applied",
    "applied_ids",
    "field_name_applied_ids",
    "field_mappings",
    "state",
)

MINIMAL_EVENT = {
    "title": "GovernanceEvent",
    "logsource": {"category": "test"},
    "detection": {"sel": [{"EventID": 1, "User": "alice"}], "condition": "sel"},
    "fields": ["EventID", "User"],
}


def new_rule():
    return SigmaRule.from_dict(MINIMAL_EVENT)


def new_nested_items():
    """Fresh child items; processing items can only be bound to one pipeline."""
    return [
        ProcessingItem(
            transformation=FieldMappingTransformation({"EventID": "event.id"}),
            identifier="map_event_id",
        ),
        ProcessingItem(
            transformation=FieldMappingTransformation({"event.id": "event.code"}),
            identifier="map_event_code",
        ),
        ProcessingItem(
            transformation=SetStateTransformation("index", "governance-index"),
            identifier="set_index",
        ),
    ]


def new_nested_postprocessing_items():
    return [
        QueryPostprocessingItem(ReplaceQueryTransformation("foo", "bar"), identifier="replace_foo"),
        QueryPostprocessingItem(EmbedQueryTransformation("[", "]"), identifier="wrap_query"),
    ]


def snapshot(pipeline):
    """Per-container key count and per-key content of the five bookkeeping containers."""
    readings = {}
    for name in CONTAINERS:
        value = getattr(pipeline, name)
        if name == "applied":
            content = list(value)
        elif name == "applied_ids":
            content = sorted(value)
        elif name in ("field_name_applied_ids", "field_mappings"):
            content = {
                key: sorted(inner)
                for key, inner in sorted(value.items(), key=lambda kv: str(kv[0]))
            }
        else:
            content = {key: value[key] for key in sorted(value)}
        readings[name] = {"keys": len(value), "content": content}
    return readings


def print_snapshot(label, pipeline):
    print(f"[{label}]")
    for name, reading in snapshot(pipeline).items():
        print(f"  {name}: keys={reading['keys']} content={reading['content']}")


class AbsorbCallCounter:
    """Wrap ProcessingPipeline.absorb_nested_state and record each invocation."""

    def __init__(self):
        self.calls = []
        self.available = hasattr(ProcessingPipeline, "absorb_nested_state")
        self._original = getattr(ProcessingPipeline, "absorb_nested_state", None)

    def __enter__(self):
        if not self.available:
            return self
        original = self._original

        def counting(inner_self, nested):
            self.calls.append(nested)
            return original(inner_self, nested)

        ProcessingPipeline.absorb_nested_state = counting
        return self

    def __exit__(self, *exc_info):
        if self.available:
            ProcessingPipeline.absorb_nested_state = self._original
        return False

    def reading(self):
        return len(self.calls) if self.available else "n/a (public method absent)"


def run_preprocessing_path():
    transformation = NestedProcessingTransformation(items=new_nested_items())
    outer = ProcessingPipeline(items=[ProcessingItem(transformation, identifier="outer_nest")])
    rule = new_rule()
    outer.apply(rule)
    return outer, transformation, rule


def run_postprocessing_path():
    transformation = NestedQueryPostprocessingTransformation(
        items=new_nested_postprocessing_items()
    )
    outer = ProcessingPipeline(
        postprocessing_items=[QueryPostprocessingItem(transformation, identifier="outer_nest")]
    )
    rule = new_rule()
    outer.apply(rule)
    query = outer.postprocess_query(rule, 'User="foobar"')
    return outer, transformation, rule, query


def run_direct_path():
    direct = ProcessingPipeline(items=new_nested_items())
    rule = new_rule()
    direct.apply(rule)
    return direct, rule


def main():
    print("== path 1: preprocessing nested transformation ==")
    outer, transformation, rule = run_preprocessing_path()
    print(f"rule.fields={rule.fields}")
    print_snapshot("outer after absorb", outer)
    print_snapshot("nested pipeline", transformation._nested_pipeline)

    print("== path 2: query postprocessing nested transformation ==")
    outer_pp, transformation_pp, rule_pp, query = run_postprocessing_path()
    print(f"postprocessed query={query!r}")
    print_snapshot("outer after absorb", outer_pp)
    print_snapshot("nested pipeline", transformation_pp._nested_pipeline)

    print("== path 3: direct use of the child pipeline (no nesting) ==")
    direct, rule_direct = run_direct_path()
    print(f"rule.fields={rule_direct.fields}")
    print_snapshot("direct pipeline", direct)
    print(f"ledger_trail_entries={len(direct.field_mappings)}")

    print("== public method call counts ==")
    with AbsorbCallCounter() as counter_pre:
        run_preprocessing_path()
    print(f"preprocessing_transform_calls={counter_pre.reading()}")

    with AbsorbCallCounter() as counter_pp:
        run_postprocessing_path()
    print(f"postprocessing_transform_calls={counter_pp.reading()}")

    with AbsorbCallCounter() as counter_direct:
        run_direct_path()
    print(f"direct_path_calls={counter_direct.reading()}")


if __name__ == "__main__":
    main()
