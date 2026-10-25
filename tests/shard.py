"""Run one shard of the test suite: CI runs the shards on separate runners at once.

    python -m tests.shard INDEX COUNT     # shard INDEX (from 0) of COUNT

The shards partition the suite as `python -m unittest discover tests` finds it:
each test runs in exactly one. They're balanced on the times CI recorded
(durations.json, tests of a second or more), longest first onto the least
loaded shard; a test it doesn't list counts as DEFAULT seconds. Stale times only
unbalance the shards; every test still runs. Each shard prints its slowest
tests, to refresh the file from.
"""

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT = 0.1  # about the mean of the tests durations.json doesn't list, in seconds


def durations() -> dict[str, float]:
    return json.loads((HERE / "durations.json").read_text())


def _flat(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from _flat(t)
        else:
            yield t


def tests(index=0, count=1) -> list[unittest.TestCase]:
    """Shard `index` of `count`, in the suite's order."""
    everything = list(_flat(unittest.TestLoader().discover(str(HERE), top_level_dir=str(HERE))))
    times = durations()
    load, mine = [0.0] * count, set()
    for i, t in sorted(enumerate(everything), key=lambda p: -times.get(p[1].id(), DEFAULT)):
        to = load.index(min(load))
        load[to] += times.get(t.id(), DEFAULT)
        if to == index:
            mine.add(i)
    return [t for i, t in enumerate(everything) if i in mine]


def main(argv=None):
    index, count = map(int, (argv or sys.argv[1:])[:2])
    shard = tests(index, count)
    print(f"shard {index} of {count}: {len(shard)} tests", file=sys.stderr, flush=True)
    result = unittest.TextTestRunner(verbosity=2, durations=15).run(unittest.TestSuite(shard))
    sys.exit(not result.wasSuccessful())


if __name__ == "__main__":
    main()
