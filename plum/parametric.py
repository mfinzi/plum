# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, print_function

import logging

from .dispatcher import Dispatcher

__all__ = ['parametric', 'type_parameter', 'kind', 'Kind']
log = logging.getLogger(__name__)

dispatch = Dispatcher()


@dispatch(object)
def get_id(x):
    return id(x)


@dispatch({int, float, str})
def get_id(x):
    return x


def parametric(Class):
    """A decorator for parametric classes."""
    subclasses = {}

    if not issubclass(Class, object):
        raise RuntimeError('To let {} be a parametric class, it must be a '
                           'new-style class.')

    class ParametricClass(Class):
        def __new__(cls, *ps):
            # Convert type parameters.
            ps = tuple(get_id(p) for p in ps)

            if ps not in subclasses:
                def __new__(cls, *args, **kw_args):
                    return Class.__new__(cls)

                name = Class.__name__ + '{' + ','.join(str(p) for p in ps) + '}'
                SubClass = type(name, (ParametricClass,), {'__new__': __new__})
                SubClass._type_parameter = ps[0] if len(ps) == 1 else ps
                subclasses[ps] = SubClass
            return subclasses[ps]

    return ParametricClass


def type_parameter(x):
    """Get the type parameter of an instance of a parametric type.

    Args:
        x (instance): Instance of a parametric type.
    """
    return x._type_parameter


def kind():
    """Create a parametric wrapper type for dispatch purposes."""

    @parametric
    class Kind(object):
        def __init__(self, *xs):
            self.xs = xs

        def get(self):
            return self.xs[0] if len(self.xs) == 1 else self.xs

    return Kind


Kind = kind()  #: A default kind provided for convenience.
