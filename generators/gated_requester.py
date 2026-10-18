"""Gated requester: request a fixed list only while the network holds more.

  constant combinator --red--> decider input
  decider: Each(green) > Each(red) -> Each, count from red
  decider output --red--> requester chest (set requests)

Wire the decider input to a roboport reading logistic network contents with
green; that network is the "stock" side of the comparison.
"""

from draftsman.blueprintable import Blueprint
from draftsman.constants import LogisticModeOfOperation
from draftsman.entity import ConstantCombinator, DeciderCombinator, LogisticRequestContainer


def build(item: str = "iron-plate", count: int = 100) -> Blueprint:
    bp = Blueprint()
    bp.label = "Gated requester"
    bp.description = (
        f"Requests {count} {item} only while the logistic network holds more than {count}. "
        "Wire a roboport (read logistic network contents) to the decider input with green."
    )
    bp.icons = ["requester-chest"]

    chest = LogisticRequestContainer(
        "requester-chest",
        id="chest",
        position=(0.5, -0.5),
        mode_of_operation=LogisticModeOfOperation.SET_REQUESTS,
    )
    decider = DeciderCombinator("decider-combinator", id="decider", position=(0.5, 1))
    decider.conditions = [
        DeciderCombinator.Condition(
            first_signal="signal-each",
            first_signal_networks={"green"},
            comparator=">",
            second_signal="signal-each",
            second_signal_networks={"red"},
        )
    ]
    decider.outputs = [
        DeciderCombinator.Output(signal="signal-each", copy_count_from_input=True, networks={"red"})
    ]
    requests = ConstantCombinator("constant-combinator", id="requests", position=(0.5, 2.5))
    requests.add_section().set_signal(0, item, count)

    # append() copies, so wire by id rather than by the local objects.
    for e in (chest, decider, requests):
        bp.entities.append(e)
    bp.add_circuit_connection("red", "requests", "decider", side_1="input", side_2="input")
    bp.add_circuit_connection("red", "decider", "chest", side_1="output", side_2="input")
    return bp


def circuit_tests(item: str = "iron-plate", count: int = 100):
    # Green on the decider input stands in for the roboport's network contents.
    from validator.circuit import Case, Expect, Inject

    other = "copper-plate" if item != "copper-plate" else "iron-plate"

    def stock(n, extra=None):
        signals = {item: n} if n else {}
        return [Inject("decider", "combinator_input_green", {**signals, **(extra or {})})]

    return [
        Case("network short: no request", stock(count // 2), [Expect("chest", requests={})]),
        Case("network has surplus: request", stock(count + 50),
             [Expect("chest", requests={item: count}),
              Expect("decider", "combinator_output_red", signals={item: count})]),
        Case("network exactly at request: no request (strict >)", stock(count), [Expect("chest", requests={})]),
        Case("unrelated items in network: no request", stock(0, {other: 10_000}),
             [Expect("chest", requests={})]),
        Case("surplus gone again: request drops", stock(count - 1), [Expect("chest", requests={})]),
    ]
