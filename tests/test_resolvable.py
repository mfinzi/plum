import pytest

from plum.resolvable import Promise, ResolutionError


def test_promise():
    p = Promise()

    # Check delivery process.
    with pytest.raises(ResolutionError):
        p.resolve()
    p.deliver(1)
    assert p.resolve() == 1

    # Check that we cannot deliver twice.
    with pytest.raises(ResolutionError):
        p.deliver(1)


def test_promise_repr():
    p = Promise()

    assert repr(p) == "Promise()"

    class MockClass:
        pass

    for T in [int, MockClass]:
        p = Promise()
        p.deliver(T)
        assert repr(T) in repr(p)
