"""Required lifecycle aggregation for Engine-owned runtime resources."""

from __future__ import annotations


def invoke_all(resources, method, *, suppress_errors=False):
    """Invoke one required lifecycle method on all resources.

    Cleanup continues after a failure, while the first failure remains the
    authoritative outcome unless the caller is already handling another error.
    """

    first_error = None
    for resource in resources:
        try:
            getattr(resource, method)()
        except BaseException as exc:
            first_error = first_error or exc
    if first_error and not suppress_errors:
        raise first_error


__all__ = ("invoke_all",)
