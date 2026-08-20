"""Backtest Result recovery and catalog validation use cases."""

from __future__ import annotations

from engine.contracts import result as result_contracts
from engine.contracts import strict_json
from engine.contracts.contract_expansion import contract_expansion_cache_scope
from engine.core import resource_ids
from engine.repository import backtest_results as result_repository
from engine.runtime import result_runtime


def _strict_json_equal(left, right):
    return strict_json.exact_equal(left, right)


def _recover_backtest_result_catalog(config, backtest_id, expected_request):
    """Commit one sealed Result catalog left by an interrupted Runtime."""
    if not isinstance(backtest_id, str) or not resource_ids.is_resource_id(
        backtest_id
    ):
        raise ValueError("Backtest Result recovery ID is invalid.")
    if not isinstance(expected_request, dict) or not isinstance(
        expected_request.get("executionSnapshot"), dict
    ):
        raise ValueError("Backtest Result recovery request is invalid.")
    if result_repository.result_catalog_exists(config, backtest_id):
        evidence = result_repository.load_result_archive_evidence(
            config, backtest_id, verify_digest=True
        )
        if not _strict_json_equal(evidence["request"], expected_request):
            raise ValueError(
                "Recovered Backtest Result request does not match its Job."
            )
        return result_repository.get_backtest_meta(config, backtest_id)

    recovery_evidence = result_repository.load_unindexed_result_archive(
        config,
        backtest_id,
    )
    if recovery_evidence is None:
        return None
    manifest = recovery_evidence["manifest"]
    metadata = result_repository.require_result_manifest_metadata(
        manifest["resultMetadata"],
        expected_schema_version=8,
    )
    catalog = result_contracts.require_catalog(
        manifest["catalog"],
        backtest_id=backtest_id,
    )
    if (
        not _strict_json_equal(catalog["request"], expected_request)
        or not _strict_json_equal(metadata.get("metrics"), catalog["metrics"])
        or not isinstance(metadata.get("dataKeys"), dict)
        or not isinstance(metadata.get("executionChain"), dict)
    ):
        raise ValueError(
            "Recovered Backtest Result request does not match its Job."
        )
    recovery_evidence.update({
        "request": expected_request,
        "metrics": metadata["metrics"],
        "dataKeys": metadata["dataKeys"],
        "executionChain": metadata["executionChain"],
    })
    verified_archive = result_runtime.verify_result_archive_in_runtimes(
        recovery_evidence
    )
    result_repository.commit_recovered_result_catalog(
        config,
        backtest_id,
        catalog,
        metadata,
        content_digest=recovery_evidence["contentDigest"],
        result_size=recovery_evidence["resultSize"],
    )
    # Bind the pre-commit strict proof to every durable column and the same
    # sealed archive inodes.  Recompiling the same Result contracts here would
    # be a duplicate boundary, not independent evidence.
    return result_repository.require_recovered_result_commit(
        config,
        backtest_id,
        catalog,
        metadata,
        content_digest=recovery_evidence["contentDigest"],
        result_size=recovery_evidence["resultSize"],
        archive_identity=verified_archive["archiveIdentity"],
    )


def recover_backtest_result_catalog(config, backtest_id, expected_request):
    """Recover one Result within a bounded pure-contract compilation scope."""
    with contract_expansion_cache_scope():
        return _recover_backtest_result_catalog(
            config,
            backtest_id,
            expected_request,
        )


__all__ = ("recover_backtest_result_catalog",)
