#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path


PROPERTY_PATTERN = re.compile(
    r"public\s+(?P<type>[A-Za-z0-9_<>\[\]\.?]+)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\{\s*get;\s*set;\s*\}(?:\s*=\s*(?P<default>[^;]+))?;"
)
CLASS_PATTERN = re.compile(r"public\s+sealed\s+class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*Config)\b")
CONFIG_FIELD_PATTERN = re.compile(r"\[ConfigField\((?P<args>.*?)\)\]", re.DOTALL)


def camel_case(value):
    return value[:1].lower() + value[1:] if value else value


def module_suffix(class_name):
    name = class_name[:-len("Config")] if class_name.endswith("Config") else class_name
    aliases = {
        "Risk": "constraint",
        "Market": "marketrule",
        "MarketRule": "marketrule",
    }
    for suffix in ("Signal", "Target", "Input", "Universe", "Risk", "Constraint", "Execution", "MarketRule", "Market", "Analyzer"):
        if name.endswith(suffix):
            return aliases.get(suffix, suffix.lower())
    return camel_case(name)


def clean_type(type_name):
    value = type_name.strip()
    while value.endswith("?"):
        value = value[:-1]
    return value


def array_item_type(type_name):
    value = clean_type(type_name)
    if value.endswith("[]"):
        return value[:-2]
    match = re.match(r"(?:List|IList|IReadOnlyList|IEnumerable|HashSet)<(.+)>", value)
    if match:
        return match.group(1).strip()
    return None


def json_schema_for_type(type_name):
    item_type = array_item_type(type_name)
    if item_type:
        return {
            "type": "array",
            "items": json_schema_for_type(item_type),
        }

    value = clean_type(type_name)
    if value in {"string", "String"}:
        return {"type": "string"}
    if value in {"bool", "Boolean"}:
        return {"type": "boolean"}
    if value in {"byte", "sbyte", "short", "ushort", "int", "uint", "long", "ulong", "Int16", "Int32", "Int64"}:
        return {"type": "integer"}
    if value in {"decimal", "double", "float", "Decimal", "Double", "Single"}:
        return {"type": "number"}
    if value in {"DateTime", "DateTimeOffset"}:
        return {"type": "string", "format": "date-time"}
    if value in {"TimeSpan"}:
        return {"type": "string"}
    return {"type": "string"}


def parse_default(raw):
    if raw is None:
        return None
    value = raw.strip()
    array_match = re.search(r"(?:new\s+(?:[A-Za-z0-9_<>.?]+\s*)?\[\]\s*)?\{(?P<items>[^}]*)\}", value)
    if array_match:
        items = []
        for item in array_match.group("items").split(","):
            parsed = parse_default(item.strip())
            if parsed is not None:
                items.append(parsed)
        return items
    if value.endswith("m") or value.endswith("M") or value.endswith("d") or value.endswith("D") or value.endswith("f") or value.endswith("F"):
        value = value[:-1]
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value in {"true", "false"}:
        return value == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return None


def parse_string_array(value):
    match = re.search(r"\{(?P<items>[^}]*)\}", value, re.DOTALL)
    if not match:
        return []
    result = []
    for raw in match.group("items").split(","):
        item = raw.strip()
        if item.startswith('"') and item.endswith('"'):
            result.append(item[1:-1])
    return result


def split_attribute_args(args):
    result = []
    current = []
    depth = 0
    in_string = False
    escape = False
    for char in args:
        if in_string:
            current.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "({[":
            depth += 1
        elif char in ")}]":
            depth -= 1
        elif char == "," and depth == 0:
            result.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if current:
        result.append("".join(current).strip())
    return result


def parse_attribute_literal(value):
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value in {"true", "false"}:
        return value == "true"
    array = parse_string_array(value)
    if array:
        return array
    return parse_default(value)


def attributes_before(body, position):
    prefix = body[:position].rstrip()
    lines = prefix.splitlines()
    selected = []
    for line in reversed(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            selected.append(stripped)
            continue
        if not stripped:
            continue
        break
    return "\n".join(reversed(selected))


def parse_config_field_metadata(attribute_text):
    metadata = {}
    match = CONFIG_FIELD_PATTERN.search(attribute_text or "")
    if not match:
        return metadata
    args = split_attribute_args(match.group("args"))
    positional = []
    for arg in args:
        if "=" in arg:
            key, raw_value = arg.split("=", 1)
            key = key.strip()
            value = parse_attribute_literal(raw_value)
            if key in {"Description", "description"} and value:
                metadata["description"] = value
            elif key in {"Minimum", "Min", "minimum", "min"} and value is not None:
                metadata["minimum"] = value
            elif key in {"Maximum", "Max", "maximum", "max"} and value is not None:
                metadata["maximum"] = value
            elif key in {"Options", "Enum", "enum"} and value:
                metadata["enum"] = value
            elif key in {"Group", "group"} and value:
                metadata["x-group"] = value
            elif key in {"Unit", "unit"} and value:
                metadata["x-unit"] = value
        else:
            positional.append(parse_attribute_literal(arg))
    if positional and positional[0]:
        metadata.setdefault("description", positional[0])
    return metadata


def parse_config_classes(source):
    result = {}
    for class_match in CLASS_PATTERN.finditer(source):
        class_name = class_match.group("name")
        start = source.find("{", class_match.end())
        if start < 0:
            continue
        depth = 0
        end = start
        for index in range(start, len(source)):
            char = source[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        body = source[start + 1:end]
        properties = {}
        required = []
        for prop in PROPERTY_PATTERN.finditer(body):
            prop_name = camel_case(prop.group("name"))
            prop_schema = json_schema_for_type(prop.group("type"))
            prop_schema.update(parse_config_field_metadata(attributes_before(body, prop.start())))
            default = parse_default(prop.group("default"))
            if default is not None:
                prop_schema["default"] = default
            properties[prop_name] = prop_schema
            if prop.group("default") is None and not prop.group("type").endswith("?"):
                required.append(prop_name)
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
        }
        if required:
            schema["required"] = required
        result[module_suffix(class_name)] = schema
    return result


def discover_schemas(root):
    schemas = {}
    for source_path in sorted((root / "src").glob("*.cs")):
        schemas.update(parse_config_classes(source_path.read_text(encoding="utf-8")))
    return schemas


def write_schema_files(root, schemas):
    payload_dir = root / "payloads"
    payload_dir.mkdir(parents=True, exist_ok=True)
    for suffix, schema in schemas.items():
        (payload_dir / f"{suffix}.schema.json").write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")


def update_package(root, schemas):
    package_path = root / "payloads" / "package.json"
    if not package_path.exists():
        return False
    package = json.loads(package_path.read_text(encoding="utf-8"))
    changed = False
    for module in package.get("modules") or []:
        suffix = str(module.get("kind", "")).lower()
        schema = schemas.get(suffix)
        if schema:
            module["configSchema"] = schema
            changed = True
    if changed:
        package_path.write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
    return changed


def generate_project_schemas(root, write=False):
    root = Path(root).resolve()
    schemas = discover_schemas(root)
    if write:
        write_schema_files(root, schemas)
        update_package(root, schemas)
    return schemas


def main():
    parser = argparse.ArgumentParser(description="Generate JSON schemas from scaffold C# config classes.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--write", action="store_true", help="Write payloads/*.schema.json and update payloads/package.json.")
    args = parser.parse_args()

    schemas = generate_project_schemas(args.root, write=args.write)
    print(json.dumps(schemas, indent=2))


if __name__ == "__main__":
    main()
