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
