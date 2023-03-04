from functools import wraps
from types import MethodType
from typing import Any

from .resolver import AmbiguousLookupError, NotFoundLookupError, Resolver
from .signature import Signature, append_default_args, extract_signature
from .type import resolve_type_hint
from .util import repr_short

__all__ = ["Function"]


_promised_convert = None
"""function or None: This will be set to :func:`.parametric.convert`."""


def _convert(obj, target_type):
    """Convert an object to a particular type. Only converts if `target_type` is set.

    Args:
        obj (object): Object to convert.
        target_type (type): Type to convert to.

    Returns:
        object: `object_to_covert` converted to type of `obj_from_target`.
    """
    if target_type is Any:
        return obj
    else:
        return _promised_convert(obj, target_type)


def _change_function_name(f, name):
    """It is not always the case that `f.__name__` is writable. To solve this, first
    create a temporary function that wraps `f` and then change the name.

    Args:
        f (function): Function to change the name of.
        name (str): New name.

    Returns:
        function: Function that wraps `f` and has name `name`.
    """

    @wraps(f)
    def f_renamed(*args, **kw_args):
        return f(*args, **kw_args)

    f_renamed.__name__ = name
    return f_renamed


_owner_transfer = {}
"""dict[type, type]: When the keys of this dictionary are detected as the owner of
a function (see :meth:`Function.owner`), make the corresponding value the owner."""


class Function:
    """A function.

    Args:
        f (function): Function that is wrapped.
        owner (str, optional): Name of the class that owns the function.
    """

    _instances = []

    def __init__(self, f, owner=None):
        Function._instances.append(self)

        self._f = f
        self._doc = ""
        wraps(f)(self)  # This will correctly populate `self._doc`.
        self._cache = {}

        # `owner` is the name of the owner. We will later attempt to resolve to
        # which class it actually points.
        self._owner_name = owner
        self._owner = None

        # Initialise pending and resolved methods.
        self._pending = []
        self._resolver = Resolver()
        self._resolved = []

    @property
    def owner(self):
        """object or None: Owner of the function. If `None`, then there is no owner."""
        if self._owner is None and self._owner_name is not None:
            name = self._owner_name.split(".")[-1]
            self._owner = self._f.__globals__[name]
            # Check if the ownership needs to be transferred to another class. This
            # can be very important for preventing infinite loops.
            while self._owner in _owner_transfer:
                self._owner = _owner_transfer[self._owner]
        return self._owner

    @property
    def __doc__(self):
        """str or None: Documentation of the function. This consists of the
        documentation of the function given at initialisation with the documentation
        of all other registered methods appended."""
        self._resolve_pending_registrations()

        # Derive the basis of the docstring from `self._f`.
        doc = self._doc

        # Append the docstrings of all other implementations to it. Exclude the
        # docstring from `self._f`, because that one forms the basis.
        resolver_doc = self._resolver.doc(exclude=self._f.__doc__)
        if resolver_doc:
            # Ensure a newline.
            while doc[-2:] != "\n\n":
                doc += "\n"
            doc += "This function has further implementations documented below.\n\n"
            doc += resolver_doc

        # If the docstring is empty, return `None`, which is consistent with omitting
        # the docstring.
        return doc if doc else None

    @__doc__.setter
    def __doc__(self, value):
        # Ensure that `self._doc` remains a string.
        self._doc = value if value else ""

    @property
    def methods(self):
        """list[:class:`.signature.Signature`]: All available methods."""
        self._resolve_pending_registrations()
        return self._resolver.signatures

    def dispatch(self, method=None, precedence=0):
        """Decorator to extend the function with another signature.

        Args:
            precedence (int, optional): Precedence of the signature. Defaults to `0`.

        Returns:
            function: Decorator.
        """
        if method is None:
            return lambda m: self.dispatch(m, precedence=precedence)

        self.register(method, precedence=precedence)
        return self

    def dispatch_multi(self, *signatures):
        """Decorator to extend the function with multiple signatures at once.

        Args:
            *signatures (tuple or :class:`.signature.Signature`): Signatures to
                register.

        Returns:
            function: Decorator.
        """
        resolved_signatures = []
        for signature in signatures:
            if isinstance(signature, Signature):
                resolved_signatures.append(signature)
            elif isinstance(signature, tuple):
                resolved_signatures.append(Signature(*signature))
            else:
                raise ValueError(
                    f"Signature `{signature}` must be a tuple or of type "
                    f"`plum.signature.Signature`."
                )

        def decorator(method):
            # The precedence will not be used, so we can safely set it to `None`.
            for signature in resolved_signatures:
                self.register(method, signature=signature, precedence=None)
            return self

        return decorator

    def clear_cache(self, reregister=True):
        """Clear cache.

        Args:
            reregister (bool, optional): Also reregister all methods. Defaults to
                `True`.
        """
        self._cache.clear()

        if reregister:
            # Add all resolved to pending.
            self._pending.extend(self._resolved)

            # Clear resolved.
            self._resolved = []
            self._resolver = Resolver()

    def register(self, f, signature=None, precedence=0):
        """Register a method.

        Either `signature` or `precedence` must be given.

        Args:
            f (function): Function that implements the method.
            signature (:class:`.signature.Signature`, optional): Signature. If it is
                not given, it will be derived from `f`.
            precedence (int, optional): Precedence of the function. If `signature` is
                given, then this argument will not be used. Defaults to `0`.
        """
        self._pending.append((f, signature, precedence))

    def _resolve_pending_registrations(self):
        # Keep track of whether anything registered.
        registered = False

        # Perform any pending registrations.
        for f, signature, precedence in self._pending:
            # Add to resolved registrations.
            self._resolved.append((f, signature, precedence))

            # Obtain the signature if it is not available.
            if signature is None:
                signature = extract_signature(f, precedence=precedence)
            else:
                # Ensure that the implementation is `f`, but make a copy before
                # mutating.
                signature = signature.__copy__()
                signature.implementation = f

            # Ensure that the implementation has the right name, because this name
            # will show up in the docstring.
            if getattr(signature.implementation, "__name__", None) != self.__name__:
                signature.implementation = _change_function_name(
                    signature.implementation,
                    self.__name__,
                )

            # Process default values.
            for subsignature in append_default_args(signature, f):
                self._resolver.register(subsignature)
                registered = True

        if registered:
            self._pending = []

            # Clear cache.
            self.clear_cache(reregister=False)

    def _enhance_exception(self, e):
        """Enchance an exception by prepending a prefix to the message of the exception
        which specifies that the message is for this function.

        Args:
            e (:class:`Exception`): Exception.

        Returns:
            :class:`Exception`: `e`, but with a prefix appended to the message.
        """
        # Specify to which function the message pertains.
        prefix = f"For function `{self.__name__}`"
        if self.owner:
            prefix += f" of `{repr_short(self.owner)}`"
        prefix = prefix + ", "
        # Return a new exception of the same type which incorporates the prefix.
        message = str(e)
        return type(e)(prefix + message[0].lower() + message[1:])

    def resolve_method(self, target, types):
        """Find the method and return type for arguments.

        Args:
            target (object): Target.
            types (tuple[type, ...]): Types of the arguments.

        Returns:
            function: Method.
            type: Return type.
        """
        self._resolve_pending_registrations()

        try:
            # Attempt to find the method using the resolver.
            signature = self._resolver.resolve(target)
            method = signature.implementation
            return_type = signature.return_type

        except AmbiguousLookupError as e:
            raise self._enhance_exception(e)  # Specify this function.

        except NotFoundLookupError as e:
            e = self._enhance_exception(e)  # Specify this function.

            if not self.owner:
                # Not in a class. Nothing we can do.
                raise e
            else:
                # In a class. Walk through the classes in the class's MRO, except for
                # this class, and try to get the method.
                method = None
                return_type = object

                for c in self.owner.__mro__[1:]:
                    # Skip the top of the type hierarchy given by `object` and `type`.
                    # We do not suddenly want to fall back to any unexpected default
                    # behaviour.
                    if c in {object, type}:
                        continue

                    # We need to check `c.__dict__` here instead of using `hasattr`
                    # since e.g. `c.__le__` will return  even if `c` does not implement
                    # `__le__`!
                    if self._f.__name__ in c.__dict__:
                        method = getattr(c, self._f.__name__)
                    else:
                        # For some reason, coverage fails to catch the `continue`
                        # below. Add the do-nothing `_ = None` fixes this.
                        # TODO: Remove this once coverage properly catches this.
                        _ = None
                        continue

                    # Ignore abstract methods.
                    if getattr(method, "__isabstractmethod__", False):
                        method = None
                        continue

                    # We found a good candidate. Break.
                    break

                if not method:
                    # If no method has been found after walking through the MRO, raise
                    # the original exception.
                    raise e

        # If the resolver is faithful, then we can perform caching using the types of
        # the arguments. If the resolver is not faithful, then we cannot.
        if self._resolver.is_faithful:
            self._cache[types] = method, return_type

        return method, return_type

    def __call__(self, *args, **kw_args):
        # Before attempting to use the cache, resolve any unresolved registrations. Use
        # an `if`-statement to speed up the common case.
        if self._pending:
            self._resolve_pending_registrations()

        # Attempt to use the cache based on the types of the arguments.
        types = tuple(map(type, args))
        try:
            method, return_type = self._cache[types]
        except KeyError:
            # Cache miss. Run the resolver based on the arguments.
            method, return_type = self.resolve_method(args, types)

        return _convert(method(*args, **kw_args), return_type)

    def invoke(self, *types):
        """Invoke a particular method.

        Args:
            *types: Types to resolve.

        Returns:
            function: Method.
        """
        # Need this before cache
        if len(self._pending) > 0:
            self._resolve_pending_registrations()

        # Attempt to use the cache based on the types.
        try:
            method, return_type = self._cache[types]
        except KeyError:
            # Cache miss. Run the resolver based on the types.
            sig_types = Signature(*(resolve_type_hint(t) for t in types))
            method, return_type = self.resolve_method(sig_types, types)

        @wraps(self._f)
        def wrapped_method(*args, **kw_args):
            return _convert(method(*args, **kw_args), return_type)

        return wrapped_method

    def __get__(self, instance, owner):
        if instance is not None:
            return MethodType(_BoundFunction(self, instance), instance)
        else:
            return self

    def __repr__(self):
        return (
            f"<function {self._f} with {len(self._resolver)} registered and"
            f" {len(self._pending)} pending method(s)>"
        )


class _BoundFunction:
    """A bound instance of `.function.Function`.

    Args:
        f (:class:`.function.Function`): Bound function.
        instance (object): Instance to which the function is bound.
    """

    def __init__(self, f, instance):
        self._f = f
        wraps(f._f)(self)  # This will call the setter for `__doc__`.
        self._instance = instance

    @property
    def __doc__(self):
        return self._f.__doc__

    @__doc__.setter
    def __doc__(self, value):
        # Don't need to do anything here. The docstring will be derived from `self._f`.
        # We, however, do need to implement this method, because :func:`wraps` calls
        # it.
        pass

    def __call__(self, _, *args, **kw_args):
        return self._f(self._instance, *args, **kw_args)

    def invoke(self, *types):
        """See :meth:`.Function.invoke`."""

        @wraps(self._f._f)
        def wrapped_method(*args, **kw_args):
            # TODO: Can we do this without `type` here?
            method = self._f.invoke(type(self._instance), *types)
            return method(self._instance, *args, **kw_args)

        return wrapped_method
