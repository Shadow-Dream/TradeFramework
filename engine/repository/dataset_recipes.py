#!/usr/bin/env python3
"""Immutable Dataset Recipe archive repository."""

from __future__ import annotations

from pathlib import Path

from engine.archive import version as version_archive
from engine.archive import version_transaction
from engine.contracts import strict_json
from engine.contracts.dataset_workspace import (
    normalize_script_arguments,
    recipe_row,
    require_request_fields,
    script_digest,
)
from engine.control import database as engine_database
from engine.core import resource_ids
def save_recipe(config, request):
    require_request_fields(
        request,
        allowed={"recipeId", "name", "scriptText"},
        required={"scriptText"},
        label="Dataset Recipe save request",
    )
    requested_id = str(request.get("recipeId") or "").strip()
    if not requested_id and not str(request.get("name") or "").strip():
        raise ValueError("Script name is required.")
    recipe_id = (
        resource_ids.normalize_resource_id(requested_id)
        if requested_id
        else resource_ids.new_resource_id("script")
    )
    script_text = str(request.get("scriptText") or "")
    if not script_text.strip():
        raise ValueError("Dataset Recipe requires scriptText.")
    with engine_database.connect_database(config) as conn:
        rows = conn.execute(
            "SELECT * FROM dataset_recipes WHERE recipe_id = ?", (recipe_id,)
        ).fetchall()
    records = [recipe_row(row) for row in rows]
    version_archive.verify_record_collection(records, ("recipeId",))
    for record in records:
        version_archive.verify_record_location(
            record,
            managed_root=config["releaseRoot"],
            expected_root=(
                Path(config["releaseRoot"])
                / "_dataset_recipes"
                / record["recipeId"]
                / record["version"]
            ),
        )
    name = str(request.get("name") or recipe_id)
    digest = script_digest(script_text)

    def destination_for_version(version):
        return Path(config["releaseRoot"]) / "_dataset_recipes" / recipe_id / version

    def prepare_staging(staging, _version, _destination):
        (staging / "script.py").write_text(script_text, encoding="utf-8")
        return {
            "recipeId": recipe_id,
            "name": name,
            "scriptDigest": digest,
            "scriptText": script_text,
        }, None

    def create_record(_version, _context):
        return {
            "recipeId": recipe_id,
            "name": name,
            "scriptDigest": digest,
        }

    def write_record(staging, record, _context):
        (staging / "recipe.json").write_text(
            strict_json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def commit_record(record, _context):
        with engine_database.connect_database(config) as conn:
            conn.execute(
            """
            INSERT INTO dataset_recipes
            (recipe_id, version, name, script_digest, content_digest, archive_root,
             archive_manifest_digest, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'archived')
            """,
            (
                record["recipeId"],
                record["version"],
                record["name"],
                record["scriptDigest"],
                record["contentDigest"],
                record["archive"]["root"],
                record["archive"]["manifestDigest"],
                record["createdAt"],
            ),
            )
            conn.commit()

    def read_committed_record(record, _context):
        with engine_database.connect_database(config) as conn:
            row = conn.execute(
                "SELECT * FROM dataset_recipes WHERE recipe_id = ? AND version = ?",
                (record["recipeId"], record["version"]),
            ).fetchone()
        return recipe_row(row) if row is not None else None

    result = version_transaction.archive_if_changed(
        records=records,
        identity_key="recipeId",
        identity=recipe_id,
        resource_type="dataset-recipe",
        resource_id=recipe_id,
        managed_root=config["releaseRoot"],
        destination_for_version=destination_for_version,
        prepare_staging=prepare_staging,
        create_record=create_record,
        record_fields={
            "recipeId", "name", "scriptDigest", "version", "status",
            "contentDigest", "createdAt", "archive",
        },
        write_record=write_record,
        commit_record=commit_record,
        read_committed_record=read_committed_record,
        immutable_fields=(),
    )
    return result["record"]


def get_recipe(config, recipe_id, version, include_script=True):
    if not str(version or "").strip():
        raise ValueError("Dataset Script version is required.")
    with engine_database.connect_database(config) as conn:
        rows = conn.execute(
            "SELECT * FROM dataset_recipes WHERE recipe_id = ? ORDER BY CAST(version AS INTEGER)",
            (recipe_id,),
        ).fetchall()
    recipes = [recipe_row(row) for row in rows]
    version_archive.verify_record_collection(recipes, ("recipeId",))
    for recipe in recipes:
        version_archive.verify_record_location(
            recipe,
            managed_root=config["releaseRoot"],
            expected_root=(
                Path(config["releaseRoot"])
                / "_dataset_recipes"
                / recipe["recipeId"]
                / recipe["version"]
            ),
        )
    recipe = next(
        (item for item in recipes if str(item["version"]) == str(version)),
        None,
    )
    if not recipe:
        raise ValueError(f"Unknown Dataset Recipe: {recipe_id}@{version}")
    if include_script:
        script_text = (Path(recipe["archive"]["root"]) / "script.py").read_text(encoding="utf-8")
        if script_digest(script_text) != recipe["scriptDigest"]:
            raise ValueError(f"Dataset Script digest mismatch: {recipe_id}@{version}")
        recipe["scriptText"] = script_text
    return recipe


def list_recipes(config):
    with engine_database.connect_database(config) as conn:
        rows = conn.execute("SELECT * FROM dataset_recipes ORDER BY recipe_id, version").fetchall()
    recipes = [recipe_row(row) for row in rows]
    version_archive.verify_record_collection(recipes, ("recipeId",))
    for recipe in recipes:
        version_archive.verify_record_location(
            recipe,
            managed_root=config["releaseRoot"],
            expected_root=(
                Path(config["releaseRoot"])
                / "_dataset_recipes"
                / recipe["recipeId"]
                / recipe["version"]
            ),
        )
    return recipes
def resolve_archived_recipe(config, request):
    recipe_id = str(request.get("recipeId") or "").strip()
    recipe_version = str(request.get("recipeVersion") or "").strip()
    if not recipe_id or not recipe_version:
        raise ValueError("Dataset Script execution requires recipeId and recipeVersion.")
    recipe = get_recipe(config, recipe_id, recipe_version, include_script=True)
    return (
        recipe["scriptText"],
        normalize_script_arguments(request.get("arguments")),
        recipe["recipeId"],
        recipe["version"],
    )
