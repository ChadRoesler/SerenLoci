"""
How a hybrid hit is scored (25 Nov 2026): the meaning gets a real say.

A hybrid hit's score is a noisy-OR of two lanes, the vector and the words. The
vector lane was 1/(1+d), and real distances run about 0.9 to 1.25, so it only
ever said 0.44 to 0.53 while the words lane ran 0.05 to 1.0: the words decided,
and a store's first row was whichever fact shared most words with the question.
On Vellmarch's fat-fact pack the right fact came first 25 times in 100 while
the store's own vectors alone would have put it first 61 times. Pinned here:

- the vector lane is the cosine similarity, and it spans what distances span
- a near fact that shares no word with the question outranks a far one that
  shares some (the case that was backwards)
- two lanes agreeing still beat either alone; nothing reaches an exact hit's 1.0
- storage.words_weight turns the words lane down, is full strength by default,
  and the store hands it to the score
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from seren_loci import store as store_mod  # noqa: E402
from seren_loci.config import LociConfig, StorageConfig  # noqa: E402

NEAR, FAR = 0.85, 1.25          # a fact that answers the question; an unrelated one


def test_the_vector_lane_is_cosine_and_spans_what_distances_span():
    v = store_mod._vector_score
    assert v(0.0) == 1.0 and abs(v(1.0) - 0.5) < 1e-9 and v(2.0) == 0.0, "unit vectors: cosine is 1 - d*d/2"
    assert v(NEAR) > v(1.0) > v(FAR)
    assert v(NEAR) - v(FAR) > 0.35, "the old 1/(1+d) moved nine points over the same span"
    assert 1.0 / (1.0 + NEAR) - 1.0 / (1.0 + FAR) < 0.10
    assert v(5.0) == 0.0, "never below nothing"


def test_a_near_fact_outranks_a_far_one_that_only_shares_words():
    near_no_words = store_mod._hybrid_score(NEAR, None)
    far_with_words = store_mod._hybrid_score(FAR, 0.5)
    assert near_no_words > far_with_words

    def old(distance, bm25_rel):
        miss = 1.0
        for s in ([1.0 / (1.0 + distance)] if distance is not None else []) + \
                 ([0.05 + 0.95 * bm25_rel / (1.0 + bm25_rel)] if bm25_rel is not None else []):
            miss *= 1.0 - s
        return 1.0 - miss
    assert old(NEAR, None) < old(FAR, 0.5), "which is the order the old score gave"


def test_two_lanes_agreeing_beat_either_alone_and_nothing_reaches_exact():
    h = store_mod._hybrid_score
    assert h(NEAR, 2.0) > h(NEAR, None) and h(NEAR, 2.0) > h(None, 2.0)
    assert h(0.0, 1000.0) == store_mod._MAX_NONEXACT < 1.0
    assert h(None, None) == 0.0
    assert h(FAR, None) == store_mod._vector_score(FAR), "one lane: its own score, no lift"


def test_words_weight_turns_the_words_lane_down():
    h = store_mod._hybrid_score
    assert h(FAR, 2.0, 1.0) > h(FAR, 2.0, 0.5) > h(FAR, 2.0, 0.0) > h(FAR, None)
    assert h(NEAR, None, 0.0) == h(NEAR, None, 1.0), "it touches the words lane only"
    assert h(FAR, 2.0) == h(FAR, 2.0, 1.0), "full strength unless told"
    assert h(FAR, 2.0, 7.0) == h(FAR, 2.0, 1.0) and h(FAR, 2.0, -1.0) == h(FAR, 2.0, 0.0), "held to 0..1"
    assert StorageConfig().words_weight == 1.0
    assert LociConfig(storage=StorageConfig(words_weight=0.5)).storage.words_weight == 0.5
