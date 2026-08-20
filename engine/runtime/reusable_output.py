"""Engine-owned proof reuse for one Python Module output generation."""

from __future__ import annotations

from engine.authority.module_invocation import (
    require_module_invocation_authority,
)
from engine.contracts import strict_json

try:
    import orjson as _orjson
except ImportError:  # The strict stdlib codec is the portable baseline.
    _orjson = None


__all__ = (
    "ReusableOutputOwner",
    "issue_reusable_output_receipt",
    "is_reusable_output_receipt",
    "reusable_output_receipt_material",
)


_REUSABLE_OUTPUT_RECEIPT_TOKEN = object()


class _ReusableOutputReceipt:
    """Nominal adapter receipt; it is not a proof until its first validation."""

    __slots__ = (
        "_adapter",
        "_candidate",
        "_epoch",
        "_generation",
        "_handle",
        "_registered",
        "_slot",
    )

    def __init__(
        self,
        adapter,
        handle,
        slot,
        epoch,
        generation,
        candidate,
        registered,
        *,
        _token,
    ):
        if _token is not _REUSABLE_OUTPUT_RECEIPT_TOKEN:
            raise TypeError("Reusable output receipt is Engine-owned.")
        self._adapter = adapter
        self._handle = handle
        self._slot = slot
        self._epoch = epoch
        self._generation = generation
        self._candidate = candidate
        self._registered = registered


def issue_reusable_output_receipt(adapter, handle, material):
    """Convert exact SDK material into an adapter-bound nominal receipt."""

    if (
        type(material) is not tuple
        or len(material) != 5
    ):
        raise TypeError("Reusable Module output material is invalid.")
    slot, epoch, generation, candidate, registered = material
    if not isinstance(slot, str) or not slot:
        raise TypeError("Reusable Module output slot is invalid.")
    if (
        isinstance(epoch, bool)
        or not isinstance(epoch, int)
        or epoch < 0
        or isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
        or type(registered) is not bool
    ):
        raise TypeError("Reusable Module output generation is invalid.")
    return _ReusableOutputReceipt(
        adapter,
        handle,
        slot,
        epoch,
        generation,
        candidate,
        registered,
        _token=_REUSABLE_OUTPUT_RECEIPT_TOKEN,
    )


def is_reusable_output_receipt(value):
    return type(value) is _ReusableOutputReceipt


def reusable_output_receipt_material(receipt, *, adapter):
    """Read one receipt only through the adapter which issued it."""

    if type(receipt) is not _ReusableOutputReceipt:
        raise TypeError("Reusable output receipt is Engine-owned.")
    if receipt._adapter is not adapter:
        raise TypeError("Reusable output receipt belongs to another adapter.")
    return (
        receipt._handle,
        receipt._slot,
        receipt._epoch,
        receipt._generation,
        receipt._candidate,
        receipt._registered,
    )


def _encode_snapshot(value):
    if _orjson is not None:
        try:
            return "orjson", _orjson.dumps(value)
        except (TypeError, RecursionError):
            pass
    return "strict-json", strict_json.dumps(
        value,
        separators=(",", ":"),
    )


def _decode_snapshot(codec, payload):
    if codec not in {"orjson", "strict-json"}:
        raise RuntimeError("Reusable output snapshot codec is invalid.")
    try:
        if codec == "orjson":
            value = _orjson.loads(payload)
        else:
            value = strict_json.loads(payload)
    except Exception as exc:
        raise RuntimeError(
            "Engine-owned reusable output snapshot cannot be decoded."
        ) from exc
    if type(value) is not dict:
        raise RuntimeError(
            "Engine-owned reusable output snapshot is not an object."
        )
    return value


class ReusableOutputOwner:
    """Per-ModuleInvoker owner of validated immutable output snapshots."""

    __slots__ = ("_adapter", "_entries", "_invocation_authority")

    def __init__(self, invocation_authority, adapter):
        require_module_invocation_authority(invocation_authority)
        self._invocation_authority = invocation_authority
        self._adapter = adapter
        self._entries = {}

    def material(self, receipt):
        return reusable_output_receipt_material(
            receipt,
            adapter=self._adapter,
        )

    def register_validated(self, receipt, isolated_outputs):
        """Store bytes only after ModuleInvoker completed normal validation."""

        (
            _handle,
            slot,
            epoch,
            generation,
            _candidate,
            registered,
        ) = self.material(receipt)
        if registered:
            raise RuntimeError(
                "Registered reusable Module outputs cannot be registered again."
            )
        if type(isolated_outputs) is not dict:
            raise TypeError("Validated reusable Module outputs must be an object.")
        codec, payload = _encode_snapshot(isolated_outputs)
        self._entries[slot] = (epoch, generation, codec, payload)

    def resolve(self, receipt):
        (
            _handle,
            slot,
            epoch,
            generation,
            _candidate,
            registered,
        ) = self.material(receipt)
        if not registered:
            raise RuntimeError("Reusable Module outputs have not been registered.")
        entry = self._entries.get(slot)
        if entry is None or entry[:2] != (epoch, generation):
            raise RuntimeError(
                "Reusable Module output receipt is stale for this invocation."
            )
        return _decode_snapshot(entry[2], entry[3])

    def discard(self, receipt):
        (
            _handle,
            slot,
            epoch,
            generation,
            _candidate,
            _registered,
        ) = self.material(receipt)
        entry = self._entries.get(slot)
        if entry is not None and entry[:2] == (epoch, generation):
            self._entries.pop(slot, None)

    def invalidate(self):
        self._entries.clear()
