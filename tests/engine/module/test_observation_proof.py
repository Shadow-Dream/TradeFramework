#!/usr/bin/env python3

import copy
import pickle
import unittest
from unittest import mock

from engine.runtime.data_proof import (
    consume_validated_observation,
    seal_validated_observation,
)


class ObservationProofTests(unittest.TestCase):
    class Authority:
        pass

    def test_proof_is_exact_one_shot_and_not_transportable(self):
        first = self.Authority()
        second = self.Authority()
        data = {"required": 1}
        with mock.patch(
            "engine.runtime.data_proof.require_observation_projection_authority",
            side_effect=lambda authority: authority,
        ):
            proof = seal_validated_observation(first, data)
            for operation in (
                lambda: copy.copy(proof),
                lambda: copy.deepcopy(proof),
                lambda: pickle.dumps(proof),
            ):
                with self.assertRaisesRegex(TypeError, "cannot be"):
                    operation()
            with self.assertRaisesRegex(TypeError, "does not match"):
                consume_validated_observation(second, proof)
            self.assertIs(
                consume_validated_observation(first, proof),
                data,
            )
            with self.assertRaisesRegex(RuntimeError, "already been consumed"):
                consume_validated_observation(first, proof)

    def test_seal_requires_an_exact_observation(self):
        with mock.patch(
            "engine.runtime.data_proof.require_observation_projection_authority",
            side_effect=lambda authority: authority,
        ), self.assertRaisesRegex(TypeError, "exact object"):
            seal_validated_observation(self.Authority(), object())


if __name__ == "__main__":
    unittest.main()
