import unittest

from tests import shard


class Shards(unittest.TestCase):
    def test_every_test_runs_in_exactly_one_shard(self):
        # CI runs the suite as shards on separate runners: together they must be the
        # whole suite, each test once, whatever the count.
        everything = [t.id() for t in shard.tests()]
        self.assertGreater(len(everything), 300)
        for count in (1, 2, 3, 4, 6):
            with self.subTest(count=count):
                ran = [t.id() for i in range(count) for t in shard.tests(i, count)]
                self.assertCountEqual(ran, everything)

    def test_a_shard_keeps_the_suite_order(self):
        # Tests of a class stay together and in order, so a class's setUpClass runs
        # once a shard (a server per shard, not per test).
        everything = [t.id() for t in shard.tests()]
        for i in range(4):
            ids = [t.id() for t in shard.tests(i, 4)]
            self.assertEqual(ids, sorted(ids, key=everything.index))

    def test_the_slow_tests_spread_over_the_shards(self):
        # Longest first onto the least loaded shard: on the recorded times no shard
        # takes more than a sixth over an even split, plus its longest test.
        times = shard.durations()
        load = [sum(times.get(t.id(), shard.DEFAULT) for t in shard.tests(i, 4)) for i in range(4)]
        self.assertLess(max(load), sum(load) / 4 * 1.17 + max(times.values()))
