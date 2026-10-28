"""Bayes core unit tests (M3 step 3)."""

import pytest

from mmc.engine import bayes


def test_update_single_outcome_math():
    priors = {"a": 0.5, "b": 0.5}
    lik = {"a": {"pos": 0.8, "neg": 0.2}, "b": {"pos": 0.2, "neg": 0.8}}
    post = bayes.update(priors, "pos", lik)
    assert post["a"] == pytest.approx(0.8)
    assert post["b"] == pytest.approx(0.2)


def test_update_normalizes():
    priors = {"a": 0.7, "b": 0.2, "c": 0.1}
    lik = {
        "a": {"x": 0.5, "y": 0.5},
        "b": {"x": 0.9, "y": 0.1},
        "c": {"x": 0.1, "y": 0.9},
    }
    post = bayes.update(priors, "x", lik)
    assert sum(post.values()) == pytest.approx(1.0)
    # b gains the most: 0.2*0.9=0.18 vs a 0.35, c 0.01 -> b = 0.18/0.54
    assert post["b"] == pytest.approx(0.18 / 0.54)


def test_unlisted_hypothesis_gets_uniform_default():
    priors = {"a": 0.5, "b": 0.5}
    lik = {"a": {"x": 0.6, "y": 0.4}}  # b missing -> uniform 0.5/0.5
    post = bayes.update(priors, "x", lik)
    assert post["a"] == pytest.approx(0.6 / 1.1)
    assert post["b"] == pytest.approx(0.5 / 1.1)


def test_update_sequential_matches_joint():
    priors = {"a": 0.4, "b": 0.6}
    lik1 = {"a": {"x": 0.7, "y": 0.3}, "b": {"x": 0.2, "y": 0.8}}
    lik2 = {"a": {"x": 0.6, "y": 0.4}, "b": {"x": 0.5, "y": 0.5}}
    mid = bayes.update(priors, "x", lik1)
    final = bayes.update(mid, "y", lik2)
    joint_a = 0.4 * 0.7 * 0.4
    joint_b = 0.6 * 0.2 * 0.5
    assert final["a"] == pytest.approx(joint_a / (joint_a + joint_b))


def test_likelihood_ratio_above_one_for_supported_hypothesis():
    priors = {"a": 0.5, "b": 0.5}
    lik = {"a": {"pos": 0.8, "neg": 0.2}, "b": {"pos": 0.2, "neg": 0.8}}
    assert bayes.likelihood_ratio("a", "pos", lik, priors, 2) == pytest.approx(4.0)
    assert bayes.likelihood_ratio("b", "pos", lik, priors, 2) == pytest.approx(0.25)
