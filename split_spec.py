#!/usr/bin/env python3
"""
split_spec.py - Split a multi-path OpenAPI spec into individual single-path
spec files, one per operation. Designed for platforms like Glean that require
exactly one path per API spec.

Usage:
    python3 split_spec.py <spec_file> [-o output_dir]

Examples:
    python3 split_spec.py glean-github-spec.yaml -o actions/
    python3 split_spec.py glean-github-spec.yaml -o actions/ --one-per-path
"""

import argparse
import os
import re
import sys
import yaml
from collections import OrderedDict


# Preserve YAML key ordering
def represent_ordereddict(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())


yaml.add_representer(OrderedDict, represent_ordereddict)


def represent_str(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.add_representer(str, represent_str)


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


def resolve_ref_path(ref_str):
    if not ref_str.startswith("#/"):
        return None
    return tuple(ref_str[2:].split("/"))


def get_nested(spec, path_tuple):
    current = spec
    for key in path_tuple:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
    return current


def collect_all_refs_deep(spec, initial_refs):
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
        for r in collect_refs(obj):
            if r not in visited:
                queue.append(r)
    return all_refs


def slugify(path, method):
    """Create a filename-safe slug from a path and method."""
    # /repos/{owner}/{repo}/actions/runs/{run_id} -> repos-owner-repo-actions-runs-run_id
    slug = path.strip("/")
    slug = re.sub(r'\{([^}]+)\}', r'\1', slug)  # remove braces
    slug = slug.replace("/", "-")
    slug = re.sub(r'[^a-zA-Z0-9_-]', '-', slug)
    return f"{method}-{slug}"


def build_single_op_spec(spec, path_key, path_val, method, op, output_dir):
    """Build a standalone single-path spec for one operation."""
    # Build the path entry with just this method
    path_entry = OrderedDict()
    if "parameters" in path_val:
        path_entry["parameters"] = path_val["parameters"]
    path_entry[method] = op

    # Collect and resolve refs
    refs = collect_refs(path_entry)
    all_ref_paths = collect_all_refs_deep(spec, refs)

    # Build components
    components = OrderedDict()
    for ref_path in sorted(all_ref_paths):
        if len(ref_path) < 2 or ref_path[0] != "components":
            continue
        section = ref_path[1]
        name = "/".join(ref_path[2:])
        if section not in components:
            components[section] = OrderedDict()
        obj = get_nested(spec, ref_path)
        if obj is not None:
            components[section][name] = obj

    # Get operation summary for the title
    summary = op.get("summary", f"{method.upper()} {path_key}")
    op_tags = op.get("tags", [])
    tag_str = op_tags[0] if op_tags else "github"

    # Assemble
    output = OrderedDict()
    output["openapi"] = spec.get("openapi", "3.0.3")
    output["info"] = OrderedDict()
    output["info"]["title"] = summary
    output["info"]["version"] = spec.get("info", {}).get("version", "1.0.0")
    output["info"]["description"] = summary
    output["servers"] = spec.get("servers", [{"url": "https://api.github.com"}])

    if op_tags:
        output["tags"] = [{"name": t} for t in op_tags]

    output["paths"] = OrderedDict()
    output["paths"][path_key] = path_entry

    if components:
        output["components"] = components

    # Write
    slug = slugify(path_key, method)
    filepath = os.path.join(output_dir, f"{slug}.yaml")
    with open(filepath, "w", encoding="utf-8") as f:
        yaml.dump(dict(output), f, default_flow_style=False, allow_unicode=True, sort_keys=False, width=120)

    return filepath, summary


def main():
    parser = argparse.ArgumentParser(
        description="Split a multi-path OpenAPI spec into individual single-path specs."
    )
    parser.add_argument("spec_file", help="Path to the OpenAPI spec to split")
    parser.add_argument(
        "-o", "--output-dir",
        default="actions",
        help="Output directory for individual specs (default: actions/)"
    )
    parser.add_argument(
        "--one-per-path",
        action="store_true",
        help="One file per path (all methods) instead of one per operation"
    )
    args = parser.parse_args()

    print(f"Loading {args.spec_file}...")
    with open(args.spec_file, "r", encoding="utf-8") as f:
        content = f.read().lstrip()
    try:
        spec = yaml.load(content, Loader=yaml.CSafeLoader)
    except (AttributeError, yaml.YAMLError):
        spec = yaml.load(content, Loader=yaml.SafeLoader)

    os.makedirs(args.output_dir, exist_ok=True)

    paths = spec.get("paths", {})
    methods = ("get", "post", "put", "patch", "delete", "head", "options")
    count = 0

    if args.one_per_path:
        for path_key, path_val in paths.items():
            if not isinstance(path_val, dict):
                continue
            # Include all methods in one file
            path_methods = OrderedDict()
            if "parameters" in path_val:
                path_methods["parameters"] = path_val["parameters"]
            for m in methods:
                if m in path_val:
                    path_methods[m] = path_val[m]

            if not any(m in path_methods for m in methods):
                continue

            # Use the first method's info for naming
            first_method = next(m for m in methods if m in path_val)
            filepath, summary = build_single_op_spec(
                spec, path_key, path_val, first_method, path_val[first_method], args.output_dir
            )
            # Rewrite with all methods
            with open(filepath, "r") as f:
                single = yaml.load(f.read(), Loader=yaml.SafeLoader)
            single["paths"][path_key] = dict(path_methods)
            # Re-resolve refs for all methods
            refs = collect_refs(path_methods)
            all_ref_paths = collect_all_refs_deep(spec, refs)
            components = OrderedDict()
            for ref_path in sorted(all_ref_paths):
                if len(ref_path) < 2 or ref_path[0] != "components":
                    continue
                section = ref_path[1]
                name = "/".join(ref_path[2:])
                if section not in components:
                    components[section] = OrderedDict()
                obj = get_nested(spec, ref_path)
                if obj is not None:
                    components[section][name] = obj
            if components:
                single["components"] = dict(components)
            with open(filepath, "w", encoding="utf-8") as f:
                yaml.dump(single, f, default_flow_style=False, allow_unicode=True, sort_keys=False, width=120)

            count += 1
            print(f"  {filepath}")
    else:
        for path_key, path_val in paths.items():
            if not isinstance(path_val, dict):
                continue
            for method in methods:
                if method not in path_val:
                    continue
                op = path_val[method]
                if not isinstance(op, dict):
                    continue
                filepath, summary = build_single_op_spec(
                    spec, path_key, path_val, method, op, args.output_dir
                )
                count += 1
                print(f"  {filepath}")

    print(f"\nDone! Generated {count} spec files in {args.output_dir}/")


if __name__ == "__main__":
    main()
