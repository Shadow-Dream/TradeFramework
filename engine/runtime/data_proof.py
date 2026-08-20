#!/usr/bin/env python3
"""One-shot runtime transport for an Environment Observation."""

from __future__ import annotations

from engine.authority.runtime_data import (
    require_observation_projection_authority,
)


__all__ = ()


_VALIDATED_OBSERVATION_TOKEN = object()


class _ValidatedObservation:
    """Synchronously carry one exact Environment Observation to its Pipeline."""

    __slots__ = ("_authority", "_consumed", "_data")

    def __init__(self, authority, data, *, _token):
        if _token is not _VALIDATED_OBSERVATION_TOKEN:
            raise TypeError("Validated Observation is Engine-owned.")
        self._authority = authority
        self._data = data
        self._consumed = False

    def _consume(self, authority):
        if self._authority is not authority:
            raise TypeError(
                "Validated Observation does not match this Pipeline authority."
            )
        if self._consumed:
            raise RuntimeError("Validated Observation has already been consumed.")
        self._consumed = True
        data = self._data
        self._data = None
        return data

    def __copy__(self):
        raise TypeError("Validated Observation cannot be copied.")

    def __deepcopy__(self, _memo):
        raise TypeError("Validated Observation cannot be copied.")

    def __reduce__(self):
        raise TypeError("Validated Observation cannot be serialized.")


def seal_validated_observation(authority, observation):
    """Seal a successfully completed Environment Observation exactly once."""

    require_observation_projection_authority(authority)
    if type(observation) is not dict:
        raise TypeError("Validated Observation must be an exact object.")
    return _ValidatedObservation(
        authority,
        observation,
        _token=_VALIDATED_OBSERVATION_TOKEN,
    )


def consume_validated_observation(authority, proof):
    """Consume one exact-authority proof without rescanning its JSON tree."""

    require_observation_projection_authority(authority)
    if type(proof) is not _ValidatedObservation:
        raise TypeError("Pipeline execution requires a validated Observation.")
    return proof._consume(authority)
