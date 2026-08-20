#!/usr/bin/env python3

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine.archive import dataset as dataset_archive
from engine.archive.sampler import sampler_runtime_bundle
from engine.authority.dataset import verify_dataset_storage_authority
from engine.authority.sampler import verify_sampler_runtime_bundle_authority
from engine.contracts import dataset as dataset_contracts
from engine.contracts.sampler import DatasetSample
from engine.runtime import sampler as sampler_runtime
from engine.runtime.backtest_provider import BacktestSampleProvider
from engine.runtime.dataset import DatasetHandle
from engine.runtime.sampler import SamplerRuntime, create_verified_sampler_runtime

class BacktestSampleProviderOwnershipTests(unittest.TestCase):
    @staticmethod
    def dataset():
        storage = {"type": "directory", "uri": "/unused"}
        content_hash = "sha256:" + ("0" * 64)
        manifest = {
            "datasetId": "prices",
            "datasetVersionId": "v1",
            "storage": storage,
            "contentHash": content_hash,
            "capabilities": {},
        }
        with mock.patch.object(dataset_archive, "verify_sealed_container"):
            authority = verify_dataset_storage_authority(
                dataset_id="prices",
                dataset_version_id="v1",
                storage=storage,
                content_hash=content_hash,
                capabilities={},
                manifest=manifest,
            )
        return DatasetHandle.from_verified_storage(authority)

    @classmethod
    def provider(cls):
        sampler = mock.MagicMock(spec=SamplerRuntime)
        sampler.declared_output_contracts = {
            "price.value": {"type": "number"}
        }
        sampler.output_schema = {"price.value": {"type": "number"}}
        sampler.output_data_keys = ["price.value"]
        sampler.__len__.return_value = 1
        return BacktestSampleProvider(
            dataset=cls.dataset(),
            sampler=sampler,
            required_data_keys={"price.value": {"type": "number"}},
        )

    @classmethod
    def python_sampler(cls, archive, *, execution_root=None):
        archive = Path(archive)
        runtime_root = archive / "runtime"
        runtime_root.mkdir()
        runtime, sources = sampler_runtime_bundle("python-script")
        for name, source in sources.items():
            shutil.copy2(source, runtime_root / name)
        definition = {
            "type": "python-script",
            "config": {},
            "parameterSchema": {"type": "object"},
            "source": "def sample(dataset, parameters):\n    return iter(())\n",
            "entryPoint": "sample",
            "outputSchema": {},
            "archive": {"root": str(archive)},
            "runtime": runtime,
        }
        authority = verify_sampler_runtime_bundle_authority(
            definition
        )
        return create_verified_sampler_runtime(
            authority,
            cls.dataset(),
            execution_root=execution_root,
        )

    def test_verified_dataset_authority_is_the_only_handle_construction_path(self):
        with self.assertRaisesRegex(TypeError, "Engine-owned"):
            DatasetHandle(object())

    def test_dataset_handle_reuses_the_authority_storage_proof(self):
        storage = {"type": "directory", "uri": "/unused"}
        content_hash = "sha256:" + ("0" * 64)
        manifest = {
            "datasetId": "prices",
            "datasetVersionId": "v1",
            "storage": storage,
            "contentHash": content_hash,
            "capabilities": {},
        }
        with mock.patch.object(
            dataset_archive, "verify_sealed_container"
        ) as verify_container:
            authority = verify_dataset_storage_authority(
                dataset_id="prices",
                dataset_version_id="v1",
                storage=storage,
                content_hash=content_hash,
                capabilities={},
                manifest=manifest,
            )
            handle = DatasetHandle.from_verified_storage(authority)
            self.assertEqual(handle.root, Path("/unused").resolve())
            self.assertEqual(handle.descriptor()["root"], str(handle.root))
        verify_container.assert_called_once_with(
            Path("/unused").resolve(),
            manifest,
            content_hash,
            semantic_capabilities=None,
        )

    def test_records_cannot_use_a_custom_loader_without_semantic_proof(self):
        storage = {"type": "directory", "uri": "/unused"}
        content_hash = "sha256:" + ("0" * 64)
        capabilities = {
            dataset_contracts.RECORDS_CAPABILITY: {
                "protocol": dataset_contracts.RECORDS_PROTOCOL,
                "descriptor": {},
            },
        }
        manifest = {
            "datasetId": "prices",
            "datasetVersionId": "v1",
            "storage": storage,
            "contentHash": content_hash,
            "capabilities": capabilities,
        }
        loader = mock.Mock(return_value=())
        with mock.patch.object(dataset_archive, "verify_sealed_container"):
            authority = verify_dataset_storage_authority(
                dataset_id="prices",
                dataset_version_id="v1",
                storage=storage,
                content_hash=content_hash,
                capabilities=capabilities,
                manifest=manifest,
                semantic_capabilities=frozenset(),
            )
        handle = DatasetHandle.from_verified_storage(
            authority,
            record_loader=loader,
        )

        with self.assertRaisesRegex(ValueError, "not semantically verified"):
            handle.records()
        loader.assert_not_called()

    def test_sampler_required_capability_must_have_semantic_proof(self):
        storage = {"type": "directory", "uri": "/unused"}
        content_hash = "sha256:" + ("0" * 64)
        capabilities = {
            dataset_contracts.RECORDS_CAPABILITY: {
                "protocol": dataset_contracts.RECORDS_PROTOCOL,
                "descriptor": {},
            },
        }
        manifest = {
            "datasetId": "prices",
            "datasetVersionId": "v1",
            "storage": storage,
            "contentHash": content_hash,
            "capabilities": capabilities,
        }
        with mock.patch.object(dataset_archive, "verify_sealed_container"):
            dataset_authority = verify_dataset_storage_authority(
                dataset_id="prices",
                dataset_version_id="v1",
                storage=storage,
                content_hash=content_hash,
                capabilities=capabilities,
                manifest=manifest,
                semantic_capabilities=frozenset(),
            )
        dataset = DatasetHandle.from_verified_storage(dataset_authority)
        definition = {
            "config": {},
            "parameterSchema": {"type": "object"},
        }
        runtime = {"protocol": "row-map-in-process-v1"}
        spec = {"requiredCapabilities": (dataset_contracts.RECORDS_CAPABILITY,)}
        with mock.patch.object(
            sampler_runtime,
            "sampler_runtime_bundle_material",
            return_value=(definition, runtime, {}, spec),
        ):
            with self.assertRaisesRegex(ValueError, "not semantically verified"):
                create_verified_sampler_runtime(
                    object(),
                    dataset,
                    source_schema={},
                )

    def test_provider_keeps_runtime_authorities_private_and_closes_strictly(self):
        provider = self.provider()
        self.assertFalse(hasattr(provider, "dataset"))
        self.assertFalse(hasattr(provider, "sampler"))
        provider.close()
        provider._sampler.close.assert_called_once_with()

    def test_provider_rejects_sampler_outside_runtime_protocol(self):
        class UnverifiedSampler:
            output_schema = {}

        with self.assertRaisesRegex(TypeError, "SamplerRuntime protocol"):
            BacktestSampleProvider(
                dataset=self.dataset(),
                sampler=UnverifiedSampler(),
            )

    def test_runtime_protocol_default_length_uses_and_closes_isolated_fork(self):
        forks = []

        class DefaultLengthSampler(SamplerRuntime):
            declared_output_contracts = {}
            output_schema = {}
            output_data_keys = []

            def __init__(self):
                self.position = 0
                self.closed = False

            def fork_for_counting(self):
                fork = type(self)()
                forks.append(fork)
                return fork

            def __iter__(self):
                while self.position < 3:
                    value = self.position
                    self.position += 1
                    yield value

            def close(self):
                self.closed = True

        sampler = DefaultLengthSampler()
        self.assertEqual(len(sampler), 3)
        self.assertEqual(sampler.position, 0)
        self.assertFalse(sampler.closed)
        self.assertEqual(len(forks), 1)
        self.assertEqual(forks[0].position, 3)
        self.assertTrue(forks[0].closed)
        self.assertEqual(list(iter(sampler)), [0, 1, 2])

    def test_runtime_protocol_rejects_formal_runtime_as_counting_fork(self):
        class AliasedSampler(SamplerRuntime):
            declared_output_contracts = {}
            output_schema = {}
            output_data_keys = []

            def fork_for_counting(self):
                return self

            def __iter__(self):
                yield 1

            def close(self):
                raise AssertionError("Formal Runtime must not be closed.")

        sampler = AliasedSampler()
        with self.assertRaisesRegex(RuntimeError, "must not be the formal Runtime"):
            len(sampler)

    def test_runtime_protocol_closes_counting_fork_after_iteration_failure(self):
        forks = []

        class FailingSampler(SamplerRuntime):
            declared_output_contracts = {}
            output_schema = {}
            output_data_keys = []

            def __init__(self, *, counting=False):
                self.counting = counting
                self.closed = False

            def fork_for_counting(self):
                fork = type(self)(counting=True)
                forks.append(fork)
                return fork

            def __iter__(self):
                if self.counting:
                    raise RuntimeError("counting failed")
                yield 1

            def close(self):
                self.closed = True

        sampler = FailingSampler()
        with self.assertRaisesRegex(RuntimeError, "counting failed"):
            len(sampler)
        self.assertFalse(sampler.closed)
        self.assertEqual(len(forks), 1)
        self.assertTrue(forks[0].closed)

    def test_python_sampler_cleans_owned_root_when_preparation_is_interrupted(self):
        owned_root = mock.Mock()
        owned_root.name = "/tmp/trade-sampler-owned-fixture"
        with tempfile.TemporaryDirectory() as archive:
            sampler = self.python_sampler(archive)
            with (
                mock.patch.object(sampler_runtime.shutil, "which", return_value="/usr/bin/bwrap"),
                mock.patch.object(
                    sampler_runtime.tempfile,
                    "TemporaryDirectory",
                    return_value=owned_root,
                ),
                mock.patch.object(
                    sampler_runtime.shutil,
                    "copy2",
                    side_effect=SystemExit("interrupted"),
                ),
            ):
                with self.assertRaisesRegex(SystemExit, "interrupted"):
                    next(iter(sampler))
        owned_root.cleanup.assert_called_once_with()
        self.assertEqual(sampler._state, "ready")
        with self.assertRaisesRegex(TypeError, "verified Sampler runtime bundle"):
            sampler_runtime.PythonScriptSampler()

    def test_python_sampler_uses_protocol_default_length_without_consuming_run(self):
        emitted = (
            DatasetSample(
                data={},
                provenance={},
                decision_time="2026-01-01T00:00:00Z",
                sequence=0,
            ),
            DatasetSample(
                data={},
                provenance={},
                decision_time="2026-01-02T00:00:00Z",
                sequence=1,
            ),
        )
        iterations = []

        def iterate(_sampler):
            iterations.append("started")
            _sampler._parameters["iterationTouched"] = True
            yield from emitted

        with tempfile.TemporaryDirectory() as archive:
            sampler = self.python_sampler(archive)
            with mock.patch.object(
                sampler_runtime.PythonScriptSampler,
                "_iterate",
                iterate,
            ):
                self.assertEqual(len(sampler), 2)
                self.assertNotIn("iterationTouched", sampler._parameters)
                self.assertEqual(list(iter(sampler)), list(emitted))
                self.assertTrue(sampler._parameters["iterationTouched"])
        self.assertEqual(iterations, ["started", "started"])
        self.assertEqual(sampler._state, "ready")

    def test_python_sampler_owns_a_unique_leaf_and_cleans_it_after_close_failure(self):
        class FailingCloseTransport:
            runtime_roots = []

            def __init__(self, command, _request):
                runtime_target = command.index("/runtime")
                self.runtime_roots.append(Path(command[runtime_target - 1]))
                self.process = mock.Mock()
                self.messages = iter((b'{"type":"complete"}', None))

            def read_line(self):
                return next(self.messages)

            def wait(self):
                return 0

            def close(self):
                raise RuntimeError("transport cleanup failed")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "archive"
            execution_root = root / "execution"
            archive.mkdir()
            sampler = self.python_sampler(
                archive, execution_root=str(execution_root)
            )
            with (
                mock.patch.object(
                    sampler_runtime.shutil,
                    "which",
                    return_value="/usr/bin/bwrap",
                ),
                mock.patch.object(
                    sampler_runtime.sampler_process,
                    "SamplerProcessTransport",
                    FailingCloseTransport,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "transport cleanup failed"):
                    list(sampler)
            runtime_root = FailingCloseTransport.runtime_roots[-1]
            self.assertFalse(runtime_root.exists())
            self.assertTrue((execution_root / "sampler").is_dir())
            self.assertEqual(list((execution_root / "sampler").iterdir()), [])

    def test_provider_closes_the_active_iterator_before_the_sampler(self):
        events = []

        class Sampler(SamplerRuntime):
            declared_output_contracts = {
                "price.value": {"type": "number"}
            }
            output_schema = {"price.value": {"type": "number"}}
            output_data_keys = ["price.value"]

            def __len__(self):
                return 2

            def fork_for_counting(self):
                raise AssertionError("Optimized length must not fork.")

            def __iter__(self):
                try:
                    yield DatasetSample(
                        data={"price": {"value": 1}},
                        provenance={},
                        decision_time="2026-01-01T00:00:00Z",
                        sequence=0,
                    )
                    yield DatasetSample(
                        data={"price": {"value": 2}},
                        provenance={},
                        decision_time="2026-01-02T00:00:00Z",
                        sequence=1,
                    )
                finally:
                    events.append("iterator")

            def close(self):
                events.append("sampler")

        provider = BacktestSampleProvider(
            dataset=self.dataset(),
            sampler=Sampler(),
            required_data_keys={"price.value": {"type": "number"}},
        )
        frames = iter(provider)
        next(frames)
        provider.close()
        frames.close()
        self.assertEqual(events, ["iterator", "sampler"])

    def test_provider_takes_ownership_without_deepcopying_sample_data(self):
        provider = self.provider()
        price = {"value": 12.0}
        frame = provider.build_frame(DatasetSample(
            data={"price": price},
            provenance={},
            decision_time="2026-01-01T00:00:00Z",
        ), 0)
        self.assertIs(frame.data["price"], price)

    def test_sample_frame_does_not_retain_sampler_provenance(self):
        provider = self.provider()
        source = {"dataset": {"row": 7}}
        frame = provider.build_frame(DatasetSample(
            data={"price": {"value": 12.0}},
            provenance={"price.value": source},
            decision_time="2026-01-01T00:00:00Z",
        ), 0)
        self.assertFalse(hasattr(frame, "provenance"))

    def test_provider_still_validates_sampler_provenance(self):
        provider = self.provider()
        with self.assertRaisesRegex(ValueError, "Sampler output provenance"):
            provider.build_frame(DatasetSample(
                data={"price": {"value": 12.0}},
                provenance={"price.value": {"row": float("nan")}},
                decision_time="2026-01-01T00:00:00Z",
            ), 0)

    def test_provider_still_validates_untrusted_sampler_values(self):
        provider = self.provider()
        with self.assertRaisesRegex(ValueError, "price.value"):
            provider.build_frame(DatasetSample(
                data={"price": {"value": "wrong"}},
                provenance={},
                decision_time="2026-01-01T00:00:00Z",
            ), 0)

    def test_provider_resolves_required_typed_map_children(self):
        sampler = mock.MagicMock(spec=SamplerRuntime)
        sampler.declared_output_contracts = {
            "dynamic": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": {"type": "number"},
            }
        }
        sampler.output_schema = sampler.declared_output_contracts
        sampler.output_data_keys = ["dynamic"]
        sampler.__len__.return_value = 1
        provider = BacktestSampleProvider(
            dataset=self.dataset(),
            sampler=sampler,
            required_data_keys={"dynamic.a": {"type": "number"}},
        )
        provider.build_frame(DatasetSample(
            data={"dynamic": {"a": 1.0}},
            provenance={},
            decision_time="2026-01-01T00:00:00Z",
        ), 0)
        with self.assertRaisesRegex(ValueError, "dynamic.a"):
            provider.build_frame(DatasetSample(
                data={"dynamic": {}},
                provenance={},
                decision_time="2026-01-01T00:00:00Z",
            ), 0)

    def test_provider_limit_does_not_pull_an_extra_sampler_item(self):
        def samples():
            for sequence in range(2):
                yield DatasetSample(
                    data={"price": {"value": float(sequence)}},
                    provenance={},
                    decision_time=f"2026-01-0{sequence + 1}T00:00:00Z",
                    sequence=sequence,
                )
            raise AssertionError("Provider requested an N+1 sampler item.")

        class Sampler(SamplerRuntime):
            declared_output_contracts = {
                "price.value": {"type": "number"}
            }
            output_schema = {"price.value": {"type": "number"}}
            output_data_keys = ["price.value"]

            def __len__(self):
                return 3

            def fork_for_counting(self):
                raise AssertionError("Optimized length must not fork.")

            def __iter__(self):
                return samples()

            def close(self):
                pass

        provider = BacktestSampleProvider(
            dataset=self.dataset(),
            sampler=Sampler(),
            required_data_keys={"price.value": {"type": "number"}},
            max_frames=2,
        )
        self.assertEqual(len(provider), 2)
        self.assertEqual(len(list(provider)), 2)
