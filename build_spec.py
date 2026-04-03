#!/usr/bin/env python3
"""
build_spec.py - Extract selected task types from a large GitHub OpenAPI YAML
spec and produce a minimal custom spec containing only those endpoints.

Uses streaming to read the source file and only fully parses the sections
that match your selected tags.

Usage:
    python3 build_spec.py <spec_file> -t tag1,tag2,tag3 [-o output_file]
    python3 build_spec.py <spec_file> -t tag1 -t tag2 [-o output_file]
    python3 build_spec.py <spec_file> --tags-file my_tags.txt [-o output_file]

Examples:
    python3 build_spec.py github-api-spec.yaml -t agent-tasks,repos -o my_spec.yaml
    python3 build_spec.py github-api-spec.yaml -t gists -t issues -o my_spec.yaml
    echo "agent-tasks,apps" > tags.txt && python3 build_spec.py github-api-spec.yaml --tags-file tags.txt
"""

import argparse
import sys
import yaml
import re
from collections import OrderedDict


# Preserve YAML key ordering
def represent_ordereddict(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())


yaml.add_representer(OrderedDict, represent_ordereddict)


# Use a custom string representer that handles multiline and special chars well
def represent_str(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.add_representer(str, represent_str)


def chunked_yaml_parse(filepath: str):
    """
    Parse the full YAML file. For very large files we rely on PyYAML's C
    loader when available. This is the reliable approach since we need to
    resolve $ref pointers and extract nested schemas.
    """
    print(f"  Loading spec (this may take a moment for large files)...")
    with open(filepath, "r", encoding="utf-8") as f:
        # Try C loader for speed
        try:
            spec = yaml.load(f, Loader=yaml.CSafeLoader)
        except AttributeError:
            f.seek(0)
            spec = yaml.load(f, Loader=yaml.SafeLoader)
    return spec


def collect_refs(obj, refs=None):
    """Recursively collect all $ref strings from a nested dict/list."""
    if refs is None:
        refs = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "$ref" and isinstance(v, str):
                refs.add(v)
            else:
                collect_refs(v, refs)
    elif isinstance(obj, list):
        for item in obj:
            collect_refs(item, refs)
    return refs


def resolve_ref_path(ref_str: str):
    """Convert '#/components/schemas/foo' to ('components', 'schemas', 'foo')."""
    if not ref_str.startswith("#/"):
        return None
    parts = ref_str[2:].split("/")
    return tuple(parts)


def get_nested(spec, path_tuple):
    """Safely get a nested value from spec by tuple of keys."""
    current = spec
    for key in path_tuple:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
    return current


def collect_all_refs_deep(spec, initial_refs):
    """
    Given a set of $ref strings, recursively resolve them and collect
    any refs they themselves contain, until no new refs are found.
    Returns a set of all ref path tuples needed.
    """
    all_refs = set()
    queue = list(initial_refs)
    visited = set()

    while queue:
        ref_str = queue.pop()
        if ref_str in visited:
            continue
        visited.add(ref_str)

        path = resolve_ref_path(ref_str)
        if path is None:
            continue
        all_refs.add(path)

        obj = get_nested(spec, path)
        if obj is None:
            continue

        nested = collect_refs(obj)
        for r in nested:
            if r not in visited:
                queue.append(r)

    return all_refs


def build_custom_spec(spec, selected_tags):
    """Build a new spec containing only paths matching selected tags."""
    selected = set(selected_tags)

    # ── Filter paths ──
    filtered_paths = OrderedDict()
    all_refs = set()

    paths = spec.get("paths", {})
    for path_key, path_val in paths.items():
        if not isinstance(path_val, dict):
            continue

        matching_methods = OrderedDict()
        for method in ("get", "post", "put", "patch", "delete", "head", "options"):
            if method not in path_val:
                continue
            op = path_val[method]
            if not isinstance(op, dict):
                continue
            op_tags = op.get("tags", [])
            if any(t in selected for t in op_tags):
                matching_methods[method] = op

        if matching_methods:
            # Also include path-level parameters
            path_entry = OrderedDict()
            if "parameters" in path_val:
                path_entry["parameters"] = path_val["parameters"]
            path_entry.update(matching_methods)
            filtered_paths[path_key] = path_entry

            # Collect refs from these operations
            all_refs |= collect_refs(matching_methods)

    if not filtered_paths:
        print(f"\n  WARNING: No endpoints found for tags: {', '.join(selected_tags)}")
        print(f"  Run extract_tasks.py first to see available task types.")
        sys.exit(1)

    # ── Filter tag definitions ──
    filtered_tags = []
    for tag_def in spec.get("tags", []):
        if isinstance(tag_def, dict) and tag_def.get("name") in selected:
            filtered_tags.append(tag_def)

    # ── Resolve all referenced components recursively ──
    print(f"  Resolving component references...")
    all_ref_paths = collect_all_refs_deep(spec, all_refs)

    # ── Build components section ──
    components = OrderedDict()
    for ref_path in sorted(all_ref_paths):
        if len(ref_path) < 2 or ref_path[0] != "components":
            continue
        section = ref_path[1]  # schemas, parameters, responses, examples, headers
        name = "/".join(ref_path[2:])  # handle nested

        if section not in components:
            components[section] = OrderedDict()

        obj = get_nested(spec, ref_path)
        if obj is not None:
            components[section][name] = obj

    # ── Assemble output spec ──
    output = OrderedDict()
    output["openapi"] = spec.get("openapi", "3.0.3")

    info = spec.get("info", {})
    output["info"] = OrderedDict()
    output["info"]["title"] = f"GitHub API - Custom Subset ({', '.join(selected_tags)})"
    output["info"]["version"] = info.get("version", "1.0.0")
    output["info"]["description"] = (
        f"Custom OpenAPI spec containing only these task types: {', '.join(selected_tags)}.\n"
        f"Generated from the full GitHub v3 REST API spec."
    )
    if "license" in info:
        output["info"]["license"] = info["license"]

    if filtered_tags:
        output["tags"] = filtered_tags

    output["servers"] = spec.get("servers", [{"url": "https://api.github.com"}])
    output["paths"] = filtered_paths

    if components:
        output["components"] = components

    return output


def parse_tags(args):
    """Parse tags from CLI arguments and/or file."""
    tags = []

    if args.tags:
        for t in args.tags:
            tags.extend([x.strip() for x in t.split(",") if x.strip()])

    if args.tags_file:
        with open(args.tags_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    tags.extend([x.strip() for x in line.split(",") if x.strip()])

    return list(dict.fromkeys(tags))  # dedupe preserving order


def main():
    parser = argparse.ArgumentParser(
        description="Build a custom OpenAPI spec from selected task types."
    )
    parser.add_argument("spec_file", help="Path to the full OpenAPI YAML spec file")
    parser.add_argument(
        "-t", "--tags",
        action="append",
        help="Comma-separated task type tags (can be repeated: -t gists -t repos)"
    )
    parser.add_argument(
        "--tags-file",
        help="File containing tags (one per line or comma-separated, # for comments)"
    )
    parser.add_argument(
        "-o", "--output",
        default="custom_spec.yaml",
        help="Output YAML file (default: custom_spec.yaml)"
    )
    args = parser.parse_args()

    tags = parse_tags(args)
    if not tags:
        print("Error: No tags specified. Use -t or --tags-file.")
        print("Run extract_tasks.py first to see available task types.")
        sys.exit(1)

    print(f"Selected task types: {', '.join(tags)}")
    print(f"Source: {args.spec_file}")

    spec = chunked_yaml_parse(args.spec_file)

    custom_spec = build_custom_spec(spec, tags)

    path_count = len(custom_spec.get("paths", {}))
    op_count = sum(
        1 for p in custom_spec.get("paths", {}).values()
        for k in p if k in ("get", "post", "put", "patch", "delete", "head", "options")
    )
    comp_sections = custom_spec.get("components", {})
    comp_count = sum(len(v) for v in comp_sections.values() if isinstance(v, dict))

    print(f"\n  Writing {args.output} ...")
    with open(args.output, "w", encoding="utf-8") as f:
        yaml.dump(dict(custom_spec), f, default_flow_style=False, allow_unicode=True, sort_keys=False, width=120)

    print(f"\n  Done!")
    print(f"  Paths:      {path_count}")
    print(f"  Operations: {op_count}")
    print(f"  Components: {comp_count}")
    print(f"  Output:     {args.output}")


if __name__ == "__main__":
    main()
