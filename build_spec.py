#!/usr/bin/env python3
"""
build_spec.py - Extract selected task types from a large GitHub OpenAPI YAML
spec and produce a minimal custom spec containing only those endpoints.

Supports both tag-level and path-level filtering so you can exclude
enterprise/admin endpoints you don't need.

Usage:
    python3 build_spec.py <spec_file> -t tag1,tag2,tag3 [-o output_file]
    python3 build_spec.py <spec_file> -t tag1 -t tag2 [-o output_file]
    python3 build_spec.py <spec_file> --tags-file my_tags.txt [-o output_file]
    python3 build_spec.py <spec_file> -t actions --exclude-paths '*/hosted-runners/*' '*/cache/*'

Examples:
    python3 build_spec.py github-api-spec.yaml -t agent-tasks,repos -o my_spec.yaml
    python3 build_spec.py github-api-spec.yaml -t gists -t issues -o my_spec.yaml
    python3 build_spec.py github-api-spec.yaml -t actions,repos \\
        --exclude-paths '*/hosted-runners/*' '*/rulesets/*' '*/cache/*'
    python3 build_spec.py github-api-spec.yaml -t repos \\
        --exclude-paths-file exclude_patterns.txt
"""

import argparse
import fnmatch
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
        content = f.read()
    # Some specs have leading whitespace before '---' which breaks parsers
    content = content.lstrip()
    try:
        spec = yaml.load(content, Loader=yaml.CSafeLoader)
    except (AttributeError, yaml.YAMLError):
        spec = yaml.load(content, Loader=yaml.SafeLoader)
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


def path_matches_any(path_key, patterns):
    """Check if a path matches any of the given glob patterns.

    Supports two match modes:
    - Pattern starting with '/' is anchored and matched against the full path.
    - Pattern without leading '/' matches if any substring of the path between
      slashes contains the pattern segments (substring/component match).

    Uses fnmatch with a flag to make '*' match '/' for convenience.
    """
    for pattern in patterns:
        # Try direct fnmatch first (with * matching /)
        if _glob_match(path_key, pattern):
            return True
        # Also check if the pattern appears as a substring match on path segments
        if not pattern.startswith("/") and _glob_match(path_key, "*/" + pattern) :
            return True
        if not pattern.startswith("/") and _glob_match(path_key, "*/" + pattern + "/*"):
            return True
    return False


def _glob_match(path, pattern):
    """fnmatch but with * also matching /."""
    # Convert * to match everything including /
    # We do this by translating to regex ourselves
    regex = fnmatch.translate(pattern)
    # fnmatch.translate turns * into [^/]* equivalent via (?s:.*),
    # but we want * to match / too, which (?s:.*) already does.
    # However fnmatch uses re.IGNORECASE on some platforms, so be explicit.
    return re.match(regex, path, re.DOTALL) is not None


def build_custom_spec(spec, selected_tags, exclude_patterns=None, include_patterns=None, strip_examples=False):
    """Build a new spec containing only paths matching selected tags and path filters."""
    selected = set(selected_tags)
    exclude_patterns = exclude_patterns or []
    include_patterns = include_patterns or []

    # ── Filter paths ──
    filtered_paths = OrderedDict()
    all_refs = set()
    excluded_count = 0

    paths = spec.get("paths", {})
    for path_key, path_val in paths.items():
        if not isinstance(path_val, dict):
            continue

        # Path-level filtering: exclude first, then include
        if exclude_patterns and path_matches_any(path_key, exclude_patterns):
            excluded_count += 1
            continue
        if include_patterns and not path_matches_any(path_key, include_patterns):
            excluded_count += 1
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

    if excluded_count:
        print(f"  Path filter excluded {excluded_count} paths")

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

    # ── Remove additionalProperties: true (some validators reject it) ──
    strip_additional_properties(output)

    # ── Sanitize example values ──
    # GitHub's spec has many example values with embedded quotes like
    # example: '"2007-10-29T02:42:39.000-07:00"' which fail date-time
    # format validation. Strip the wrapping double quotes.
    sanitize_examples(output)

    # ── Strip response examples if requested ──
    # GitHub's response examples are often incomplete (missing required
    # fields like has_discussions) and add thousands of lines of bloat.
    if strip_examples:
        print(f"  Stripping response examples...")
        strip_response_examples(output)

    return output


def strip_additional_properties(obj):
    """Recursively remove additionalProperties: true entries.

    Some validators (e.g. Glean) reject additionalProperties or require
    it to be false.
    """
    if isinstance(obj, dict):
        if "additionalProperties" in obj and obj["additionalProperties"] is True:
            del obj["additionalProperties"]
        for val in obj.values():
            strip_additional_properties(val)
    elif isinstance(obj, list):
        for item in obj:
            strip_additional_properties(item)


def sanitize_examples(obj):
    """Recursively strip embedded double quotes from example values.

    Fixes strings like '"some value"' -> 'some value' which appear
    throughout GitHub's spec and break format validators.
    """
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key == "example" and isinstance(val, str):
                if len(val) >= 2 and val.startswith('"') and val.endswith('"'):
                    obj[key] = val[1:-1]
            else:
                sanitize_examples(val)
    elif isinstance(obj, list):
        for item in obj:
            sanitize_examples(item)


def strip_response_examples(spec):
    """Remove operation-level examples that bloat the spec and often have
    missing required fields or null values that fail validation.

    Removes:
    - 'examples' and 'example' keys from response media type objects
    - 'examples' and 'example' keys from request body media type objects
    - The entire components/examples section

    Preserves:
    - Per-property 'example' values in schemas (e.g. example: true)
    """
    for path_val in spec.get("paths", {}).values():
        if not isinstance(path_val, dict):
            continue
        for method in ("get", "post", "put", "patch", "delete", "head", "options", "parameters"):
            op = path_val.get(method)
            if not isinstance(op, dict):
                continue
            # Strip from responses
            for resp_val in op.get("responses", {}).values():
                if not isinstance(resp_val, dict):
                    continue
                for media in resp_val.get("content", {}).values():
                    if isinstance(media, dict):
                        media.pop("examples", None)
                        media.pop("example", None)
            # Strip from request bodies
            req_body = op.get("requestBody")
            if isinstance(req_body, dict):
                for media in req_body.get("content", {}).values():
                    if isinstance(media, dict):
                        media.pop("examples", None)
                        media.pop("example", None)

    # Strip components/examples entirely
    if "components" in spec and isinstance(spec["components"], dict):
        spec["components"].pop("examples", None)


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
    parser.add_argument(
        "--exclude-paths",
        nargs="+",
        default=[],
        help="Glob patterns for paths to exclude (e.g. '**/hosted-runners/**' '**/rulesets/**')"
    )
    parser.add_argument(
        "--include-paths",
        nargs="+",
        default=[],
        help="Glob patterns for paths to include (only matching paths kept)"
    )
    parser.add_argument(
        "--exclude-paths-file",
        help="File with exclude patterns (one per line, # for comments)"
    )
    parser.add_argument(
        "--strip-examples",
        action="store_true",
        help="Remove response examples (they bloat the spec and often have validation errors)"
    )
    args = parser.parse_args()

    tags = parse_tags(args)
    if not tags:
        print("Error: No tags specified. Use -t or --tags-file.")
        print("Run extract_tasks.py first to see available task types.")
        sys.exit(1)

    # Collect path filter patterns
    exclude_patterns = list(args.exclude_paths)
    if args.exclude_paths_file:
        with open(args.exclude_paths_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    exclude_patterns.append(line)

    include_patterns = list(args.include_paths)

    print(f"Selected task types: {', '.join(tags)}")
    if exclude_patterns:
        print(f"Excluding paths: {', '.join(exclude_patterns)}")
    if include_patterns:
        print(f"Including only paths: {', '.join(include_patterns)}")
    print(f"Source: {args.spec_file}")

    spec = chunked_yaml_parse(args.spec_file)

    custom_spec = build_custom_spec(spec, tags, exclude_patterns, include_patterns, args.strip_examples)

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
