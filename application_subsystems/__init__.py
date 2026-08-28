"""Trusted application subsystems hosted beside the generic Trade Engine API."""

from __future__ import annotations

from .basic import subsystem_definition as basic_subsystem_definition
from .registry import SubsystemContext, SubsystemPage, SubsystemRegistry


def build_builtin_subsystem_registry():
    """Build the explicit product-owned subsystem catalog.

    This is an application composition boundary, not filesystem or package
    discovery.  Adding or removing a trusted subsystem changes this catalog;
    the Engine HTTP host remains subsystem-neutral.
    """

    return SubsystemRegistry((basic_subsystem_definition(),))


__all__ = (
    "SubsystemContext",
    "SubsystemPage",
    "SubsystemRegistry",
    "build_builtin_subsystem_registry",
)
