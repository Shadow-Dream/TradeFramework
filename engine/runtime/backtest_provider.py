#!/usr/bin/env python3
"""Backtest Sample Provider over one verified Dataset and Sampler runtime."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from itertools import islice
from typing import List, Optional

from engine.runtime.dataset import DatasetHandle
from engine.contracts.contract_expansion import (
    contract_root_paths,
    expand_contracts,
    resolve_contract_path,
)
from engine.contracts.data import compile_data_json_validator
from engine.contracts.data import compile_normalized_json_validator
from engine.contracts.data_compatibility import schemas_compatible
from engine.contracts.data_model import (
    normalize_data_key_schema,
    schema_label,
)
from engine.contracts.sampler import (
    DatasetSample,
    SampleFrame,
    parse_sampler_instant,
)
from engine.runtime.sampler import SamplerRuntime


class BacktestSampleProvider:
    """Hosts a Sampler iterator and validates its Sample frames.

    The Provider deliberately has no Dataset row/clock API.  Calling ``iter``
    delegates directly to the Sampler, so the same boundary can host a live
    brokerage Sampler later.
    """

    provider_type = "BacktestSampleProvider"

    def __init__(
        self,
        *,
        dataset: DatasetHandle,
        sampler,
        required_data_keys: Iterable[str] = (),
        max_frames: Optional[int] = None,
    ):
        if type(dataset) is not DatasetHandle:
            raise TypeError(
                "BacktestSampleProvider requires an Engine-verified DatasetHandle."
            )
        if not isinstance(sampler, SamplerRuntime):
            raise TypeError(
                "BacktestSampleProvider requires the SamplerRuntime protocol."
            )
        self._dataset = dataset
        self._dataset_id = dataset.dataset_id
        self._dataset_version_id = dataset.dataset_version_id
        self._sampler = sampler
        self._active_iterator = None
        self._closed = False
        if max_frames is not None and (
            isinstance(max_frames, bool)
            or not isinstance(max_frames, int)
            or max_frames < 1
        ):
            raise ValueError("Backtest Sample limit must be greater than zero.")
        self._max_frames = max_frames
        if isinstance(required_data_keys, Mapping):
            required_schema = {
                str(key): normalize_data_key_schema(value, path=str(key))
                for key, value in required_data_keys.items()
                if key
            }
        else:
            required_schema = {str(key): {} for key in required_data_keys if key}
        self._required_data_keys = tuple(required_schema)
        provided_schema = expand_contracts(self._sampler.output_schema)
        self._output_schema = dict(provided_schema)
        missing_contract = object()
        resolved = {
            key: resolve_contract_path(
                provided_schema, key, missing_contract
            )
            for key in self._required_data_keys
        }
        missing = sorted(
            key for key, schema in resolved.items()
            if schema is missing_contract
        )
        if missing:
            raise ValueError(
                "Backtest Sample does not satisfy required DataKey(s): " + ", ".join(missing)
            )
        mismatches = [
            f"{key} ({schema_label(resolved[key])} -> {schema_label(expected)})"
            for key, expected in required_schema.items()
            if resolved[key] is not missing_contract
            and not schemas_compatible(resolved[key], expected)
        ]
        if mismatches:
            raise ValueError("Backtest Sample has incompatible DataKey type(s): " + ", ".join(mismatches))
        self._validate_sample_data = compile_data_json_validator(
            self._output_schema,
            required_paths=(
                contract_root_paths(self._output_schema)
                | frozenset(self._required_data_keys)
            ),
            contracts_expanded=True,
        )
        self._validate_provenance = compile_normalized_json_validator(
            {},
            path="Sampler output provenance",
        )

    @property
    def output_data_keys(self) -> List[str]:
        return list(self._sampler.output_data_keys)

    @property
    def dataset_id(self) -> str:
        return self._dataset_id

    @property
    def dataset_version_id(self) -> str:
        return self._dataset_version_id

    def __len__(self) -> int:
        count = len(self._sampler)
        return min(count, self._max_frames) if self._max_frames is not None else count

    def build_frame(self, sample: DatasetSample, sequence: int) -> SampleFrame:
        if not isinstance(sample, DatasetSample):
            raise ValueError("Sampler must yield DatasetSample values.")
        if type(sample.data) is not dict:
            raise ValueError("Sampler output data must be a JSON object.")
        if not isinstance(sample.decision_time, str) or not sample.decision_time.strip():
            raise ValueError("Sampler output requires a non-empty string decisionTime.")
        if type(sample.provenance) is not dict:
            raise ValueError("Sampler output provenance must be a JSON object.")
        if any(
            not isinstance(key, str)
            or not key
            or type(value) is not dict
            for key, value in sample.provenance.items()
        ):
            raise ValueError(
                "Sampler output provenance must map non-empty string paths to objects."
            )
        if sample.sequence is not None and (
            isinstance(sample.sequence, bool)
            or not isinstance(sample.sequence, int)
            or sample.sequence != sequence
        ):
            raise ValueError(
                "Sampler output sequence must equal its zero-based emission order."
            )
        if not isinstance(sample.cycle_id, str):
            raise ValueError("Sampler output cycleId must be a string.")
        # A yielded DatasetSample transfers ownership of its parsed data tree to
        # the Provider.  Python Sampler messages are fresh strict-decoder objects;
        # RowMappingSampler copies borrowed Dataset fields before yielding.
        data = dict(sample.data)
        if not sample.contract_validated:
            self._validate_sample_data(data)
        # Provenance remains part of the Sampler protocol, but no downstream
        # runtime consumes it. Validate without retaining or copying its tree.
        self._validate_provenance(sample.provenance)
        return SampleFrame(
            cycle_id=sample.cycle_id or f"backtest:{self.dataset_version_id}:{sample.sequence if sample.sequence is not None else sequence}",
            decision_time=sample.decision_time,
            data=data,
        )

    def __iter__(self) -> Iterator[SampleFrame]:
        if self._closed:
            raise RuntimeError("Cannot iterate a closed BacktestSampleProvider.")
        if self._active_iterator is not None:
            raise RuntimeError("BacktestSampleProvider iteration is already running.")
        previous_time = None
        cycle_ids = set()
        iterator = iter(self._sampler)
        try:
            iterator_close = iterator.close
        except AttributeError as exc:
            self._sampler.close()
            raise TypeError(
                "Sampler iterator must implement the close lifecycle method."
            ) from exc
        if not callable(iterator_close):
            self._sampler.close()
            raise TypeError("Sampler iterator must implement the close lifecycle method.")
        self._active_iterator = iterator
        samples = islice(iterator, self._max_frames) if self._max_frames is not None else iterator
        try:
            for sequence, sample in enumerate(samples):
                current_time = parse_sampler_instant(sample.decision_time)
                if previous_time is not None and current_time < previous_time:
                    raise ValueError("Sampler decisionTime values must be non-decreasing.")
                previous_time = current_time
                frame = self.build_frame(sample, sequence)
                if not isinstance(frame.cycle_id, str) or not frame.cycle_id:
                    raise ValueError("Sampler output cycleId must be a non-empty string.")
                if frame.cycle_id in cycle_ids:
                    raise ValueError(f"Sampler output contains duplicate cycleId '{frame.cycle_id}'.")
                cycle_ids.add(frame.cycle_id)
                yield frame
        finally:
            try:
                iterator_close()
            finally:
                if self._active_iterator is iterator:
                    self._active_iterator = None

    def close(self):
        if self._closed:
            return
        first_error = None
        iterator = self._active_iterator
        if iterator is not None:
            try:
                iterator.close()
            except BaseException as exc:
                first_error = first_error or exc
            else:
                if self._active_iterator is iterator:
                    self._active_iterator = None
        try:
            self._sampler.close()
        except BaseException as exc:
            first_error = first_error or exc
        if first_error is not None:
            raise first_error
        self._active_iterator = None
        self._closed = True
