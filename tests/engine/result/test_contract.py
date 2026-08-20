#!/usr/bin/env python3

import copy
import json
import unittest
from unittest import mock

from engine.archive import backtest_result as backtest_result_archive
from engine.contracts.backtest import backtest_evidence_digest
from engine.contracts import contract_expansion as expansion_contracts
from engine.contracts import result as result_contracts
from engine.repository import backtest_results as result_repository
from tests.support.backtest_runtime import BacktestRuntimeFixture


class ResultValidatorCompilationTests(unittest.TestCase):
    @staticmethod
    def declaration(path, schema, *, required):
        return {
            "label": path,
            "schema": schema,
            "required": required,
            "source": {"path": f"cycles.data.{path}"},
            "encoding": {
                "time": "decisionTime",
                "value": f"data.{path}",
            },
        }

    def test_result_presence_checks_reuse_one_expanded_contract_snapshot(self):
        number = {"type": "number"}
        string = {"type": "string"}
        root = {
            "type": "object",
            "properties": {
                "requiredValue": number,
                "optionalValue": string,
            },
            "required": ["requiredValue"],
            "additionalProperties": False,
        }
        data_keys = {
            "root": self.declaration("root", root, required=True),
            "root.requiredValue": self.declaration(
                "root.requiredValue", number, required=True
            ),
            "root.optionalValue": self.declaration(
                "root.optionalValue", string, required=False
            ),
        }
        real_expand = expansion_contracts.expand_contracts
        with (
            mock.patch.object(
                result_contracts, "expand_contracts", wraps=real_expand
            ) as compiler_expand,
            mock.patch.object(
                expansion_contracts, "expand_contracts", wraps=real_expand
            ) as nested_expand,
        ):
            validate = result_contracts.compile_cycle_validator(data_keys)

        self.assertEqual(compiler_expand.call_count, 1)
        self.assertEqual(nested_expand.call_count, 0)
        validate({"root": {"requiredValue": 1.0}})

    def test_cycle_contains_only_identity_time_and_data(self):
        cycle = {
            "schemaVersion": 3,
            "cycleId": "cycle-0",
            "decisionTime": "2026-01-01T00:00:00Z",
            "data": {},
        }
        validate_data = mock.Mock()
        result_contracts.require_cycle(cycle, 0, validate_data, set())
        validate_data.assert_called_once_with(cycle["data"])

        invalid = copy.deepcopy(cycle)
        invalid["cycleId"] = "cycle-1"
        invalid["provenance"] = {}
        with self.assertRaisesRegex(ValueError, "unsupported field.*provenance"):
            result_contracts.require_cycle(
                invalid,
                1,
                lambda _data: None,
                set(),
            )


class CurrentResultContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = BacktestRuntimeFixture().open()

    def tearDown(self):
        self.fixture.close()

    def test_current_contract_is_strict_not_a_legacy_loader(self):
        with self.assertRaisesRegex(ValueError, "archived"):
            result_contracts.require_result({"schemaVersion": 4, "cycles": []})
        completed = self.fixture.run_minimal_backtest("strict-result-contract")
        result_path = (
            backtest_result_archive.archive_root(
                self.fixture.config["releaseRoot"]
            )
            / completed["backtestId"]
            / "result.json"
        )
        current = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertIs(result_contracts.require_result(current), current)
        manifest = json.loads(
            (result_path.parent / "result-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        execution_snapshot = manifest["catalog"]["request"]["executionSnapshot"]
        self.assertIs(
            result_contracts.require_result(
                current,
                execution_snapshot=execution_snapshot,
            ),
            current,
        )
        fabricated_projection = copy.deepcopy(current)
        fabricated_projection["executionChain"]["pipeline"][
            "observationInput"
        ] = {"whitelist": ["fabricated.path"], "blacklist": []}
        with self.assertRaisesRegex(ValueError, "Pipeline identity"):
            result_contracts.require_result(
                fabricated_projection,
                execution_snapshot=execution_snapshot,
            )
        numeric_snapshot = copy.deepcopy(execution_snapshot)
        artifact = numeric_snapshot["compositionArtifact"]
        artifact["pipelinePlan"]["outputContracts"]["strictNumber"] = {
            "const": 10
        }
        artifact["artifactHash"] = backtest_evidence_digest({
            key: value for key, value in artifact.items() if key != "artifactHash"
        })
        numeric_snapshot["snapshotHash"] = backtest_evidence_digest({
            key: value
            for key, value in numeric_snapshot.items()
            if key != "snapshotHash"
        })
        numeric_tamper = copy.deepcopy(current)
        numeric_tamper["executionChain"]["snapshotHash"] = numeric_snapshot[
            "snapshotHash"
        ]
        numeric_tamper["executionChain"]["pipeline"]["dataKeyContract"][
            "outputs"
        ]["strictNumber"] = {"const": 10.0}
        with self.assertRaisesRegex(ValueError, "Pipeline identity"):
            result_contracts.require_result(
                numeric_tamper,
                execution_snapshot=numeric_snapshot,
            )
        for field in (
            "dataKeys",
            "metrics",
            "executionChain",
            "sampleFrameContract",
        ):
            incomplete = copy.deepcopy(current)
            incomplete.pop(field)
            with self.assertRaisesRegex(ValueError, field):
                result_contracts.require_result(incomplete)
        array_contract = copy.deepcopy(current)
        array_contract["dataKeys"] = {
            "state.values": {
                "label": "state.values",
                "schema": {"type": "array", "items": {"type": "number"}},
                "required": True,
                "source": {"path": "cycles.data.state.values"},
                "encoding": {
                    "time": "decisionTime",
                    "value": "data.state.values",
                },
            }
        }
        with self.assertRaisesRegex(ValueError, "array runtime type"):
            result_contracts.require_result(array_contract)


if __name__ == "__main__":
    unittest.main()
