#!/usr/bin/env python3
"""
extract_tasks.py - Stream-parse a large GitHub OpenAPI YAML spec and extract
all unique task types (tags) with their descriptions and associated endpoints.

Outputs a markdown "menu" file you can browse to pick the task types you need.
Supports path-level and tag-level filtering so the menu reflects only what
you'd actually include in a custom spec.

Usage:
    python3 extract_tasks.py <spec_file> [-o output_file]
    python3 extract_tasks.py <spec_file> --tags repos,issues,pulls
    python3 extract_tasks.py <spec_file> --exclude-paths '*/hosted-runners/*'

Examples:
    python3 extract_tasks.py github-api-spec.yaml -o task_menu.md
    python3 extract_tasks.py github-api-spec.yaml --tags repos,actions,issues \\
        --exclude-paths '*/hosted-runners/*' '*/cache/*' '*/rulesets/*'
"""

import argparse
import fnmatch
import sys
import re
from collections import defaultdict


def stream_extract_tags_and_paths(filepath: str):
    """
    Stream-parse the YAML file line-by-line to extract:
      1. Top-level tag definitions (name + description)
      2. Path -> method -> tags mappings

    This avoids loading the entire spec into memory as a parsed YAML tree.
    """
    tag_definitions = {}  # tag_name -> description
    path_operations = defaultdict(list)  # tag_name -> [(method, path, summary, operationId)]

    # State machine for top-level tags block
    in_tags_block = False
    current_tag_name = None
    current_tag_desc = None

    # State machine for paths block
    in_paths_block = False
    current_path = None
    current_method = None
    current_summary = None
    current_operation_id = None
    current_op_tags = []
    in_op_tags = False

    with open(filepath, "r", encoding="utf-8") as f:
        prev_line = ""
        for line_num, raw_line in enumerate(f, 1):
            line = raw_line.rstrip("\n")
            stripped = line.strip()

            # ── Detect top-level blocks ──
            # Top-level "tags:" (no indent)
            if re.match(r'^tags:\s*$', line):
                in_tags_block = True
                in_paths_block = False
                continue

            if re.match(r'^paths:\s*$', line):
                # Flush last tag if any
                if in_tags_block and current_tag_name:
                    tag_definitions[current_tag_name] = current_tag_desc or ""
                in_tags_block = False
                in_paths_block = True
                continue

            # Other top-level keys end the current block
            if re.match(r'^[a-zA-Z]', line) and not line.startswith(' ') and not line.startswith('-'):
                if in_tags_block and current_tag_name:
                    tag_definitions[current_tag_name] = current_tag_desc or ""
                in_tags_block = False
                in_paths_block = False
                continue

            # ── Parse top-level tags block ──
            if in_tags_block:
                # New tag entry: "- name: <value>"
                m = re.match(r'^- name:\s*(.+)', stripped)
                if m:
                    # Save previous tag
                    if current_tag_name:
                        tag_definitions[current_tag_name] = current_tag_desc or ""
                    current_tag_name = m.group(1).strip()
                    current_tag_desc = None
                    continue

                m = re.match(r'^description:\s*(.+)', stripped)
                if m and current_tag_name:
                    current_tag_desc = m.group(1).strip()
                    continue

            # ── Parse paths block ──
            if in_paths_block:
                # New path: exactly 2-space indent with quoted key
                m = re.match(r'^  "([^"]+)":\s*$', line) or re.match(r"^  '([^']+)':\s*$", line)
                if m:
                    # Flush previous operation
                    if current_method and current_path:
                        for t in current_op_tags:
                            path_operations[t].append((
                                current_method.upper(),
                                current_path,
                                current_summary or "",
                                current_operation_id or ""
                            ))
                    current_path = m.group(1)
                    current_method = None
                    current_summary = None
                    current_operation_id = None
                    current_op_tags = []
                    in_op_tags = False
                    continue

                # HTTP method: exactly 4-space indent
                m = re.match(r'^    (get|post|put|patch|delete|head|options):\s*$', line)
                if m:
                    # Flush previous operation
                    if current_method and current_path:
                        for t in current_op_tags:
                            path_operations[t].append((
                                current_method.upper(),
                                current_path,
                                current_summary or "",
                                current_operation_id or ""
                            ))
                    current_method = m.group(1)
                    current_summary = None
                    current_operation_id = None
                    current_op_tags = []
                    in_op_tags = False
                    continue

                # Summary (6-space indent)
                m = re.match(r'^      summary:\s*(.+)', line)
                if m:
                    current_summary = m.group(1).strip().strip("'\"")
                    continue

                # operationId
                m = re.match(r'^      operationId:\s*(.+)', line)
                if m:
                    current_operation_id = m.group(1).strip().strip("'\"")
                    continue

                # Tags list start
                if re.match(r'^      tags:\s*$', line):
                    in_op_tags = True
                    continue

                # Tag list item
                if in_op_tags:
                    m = re.match(r'^      - (.+)', line)
                    if m:
                        current_op_tags.append(m.group(1).strip().strip("'\""))
                        continue
                    else:
                        in_op_tags = False

        # Flush final operation
        if in_paths_block and current_method and current_path:
            for t in current_op_tags:
                path_operations[t].append((
                    current_method.upper(),
                    current_path,
                    current_summary or "",
                    current_operation_id or ""
                ))

        # Flush final tag definition
        if in_tags_block and current_tag_name:
            tag_definitions[current_tag_name] = current_tag_desc or ""

    return tag_definitions, path_operations


def build_menu(tag_definitions, path_operations, output_path):
    """Write a markdown menu file."""
    # Merge: some tags might appear in paths but not in definitions
    all_tags = sorted(set(list(tag_definitions.keys()) + list(path_operations.keys())))

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# GitHub API Task Types Menu\n\n")
        f.write(f"**Total task types found: {len(all_tags)}**\n\n")
        f.write("Use this menu to pick task types, then run `build_spec.py` with your selections.\n\n")
        f.write("---\n\n")

        # Quick reference table
        f.write("## Quick Reference\n\n")
        f.write("| # | Task Type | Description | Endpoints |\n")
        f.write("|---|-----------|-------------|----------:|\n")
        for i, tag in enumerate(all_tags, 1):
            desc = tag_definitions.get(tag, "").replace("|", "\\|")
            count = len(path_operations.get(tag, []))
            f.write(f"| {i} | `{tag}` | {desc} | {count} |\n")

        f.write("\n---\n\n")

        # Detailed listing
        f.write("## Detailed Endpoints by Task Type\n\n")
        for tag in all_tags:
            desc = tag_definitions.get(tag, "No description")
            ops = path_operations.get(tag, [])
            f.write(f"### `{tag}`\n\n")
            f.write(f"> {desc}\n\n")
            if ops:
                f.write("| Method | Path | Summary |\n")
                f.write("|--------|------|---------|\n")
                for method, path, summary, op_id in ops:
                    f.write(f"| {method} | `{path}` | {summary} |\n")
            else:
                f.write("_No endpoints found in spec._\n")
            f.write("\n")

        # Copy-paste helper
        f.write("---\n\n")
        f.write("## Copy-Paste Task List\n\n")
        f.write("```\n")
        f.write(",".join(all_tags))
        f.write("\n```\n")

    return all_tags


def _glob_match(path, pattern):
    """fnmatch but with * also matching /."""
    regex = fnmatch.translate(pattern)
    return re.match(regex, path, re.DOTALL) is not None


def _path_matches_any(path_key, patterns):
    """Check if a path matches any of the given glob patterns."""
    for pattern in patterns:
        if _glob_match(path_key, pattern):
            return True
        if not pattern.startswith("/") and _glob_match(path_key, "*/" + pattern):
            return True
        if not pattern.startswith("/") and _glob_match(path_key, "*/" + pattern + "/*"):
            return True
    return False


def filter_path_operations(path_operations, exclude_patterns=None, include_patterns=None):
    """Filter path operations by glob patterns on the path string."""
    if not exclude_patterns and not include_patterns:
        return path_operations

    filtered = defaultdict(list)
    excluded_count = 0
    for tag, ops in path_operations.items():
        for method, path, summary, op_id in ops:
            skip = False
            if exclude_patterns and _path_matches_any(path, exclude_patterns):
                skip = True
            if not skip and include_patterns and not _path_matches_any(path, include_patterns):
                skip = True
            if skip:
                excluded_count += 1
            else:
                filtered[tag].append((method, path, summary, op_id))

    if excluded_count:
        print(f"  Path filter excluded {excluded_count} endpoints")
    return filtered


def main():
    parser = argparse.ArgumentParser(
        description="Extract task types from a GitHub OpenAPI spec into a browsable menu."
    )
    parser.add_argument("spec_file", help="Path to the OpenAPI YAML spec file")
    parser.add_argument(
        "-o", "--output",
        default="task_menu.md",
        help="Output markdown file (default: task_menu.md)"
    )
    parser.add_argument(
        "--exclude-paths",
        nargs="+",
        default=[],
        help="Glob patterns for paths to exclude (e.g. '**/hosted-runners/**')"
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
        "--tags",
        nargs="+",
        default=[],
        help="Only show these tags in the menu (comma-separated or repeated)"
    )
    args = parser.parse_args()

    # Collect path filter patterns
    exclude_patterns = list(args.exclude_paths)
    if args.exclude_paths_file:
        with open(args.exclude_paths_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    exclude_patterns.append(line)
    include_patterns = list(args.include_paths)

    # Collect tag filters
    selected_tags = set()
    for t in args.tags:
        selected_tags.update(x.strip() for x in t.split(",") if x.strip())

    print(f"Scanning {args.spec_file} ...")
    tag_defs, path_ops = stream_extract_tags_and_paths(args.spec_file)
    print(f"  Found {len(tag_defs)} tag definitions")
    print(f"  Found {len(path_ops)} tags with endpoints")

    # Apply path filters
    path_ops = filter_path_operations(path_ops, exclude_patterns, include_patterns)

    # Apply tag filter
    if selected_tags:
        tag_defs = {k: v for k, v in tag_defs.items() if k in selected_tags}
        path_ops = {k: v for k, v in path_ops.items() if k in selected_tags}
        print(f"  Filtered to {len(selected_tags)} selected tags")

    all_tags = build_menu(tag_defs, path_ops, args.output)
    print(f"\nMenu written to: {args.output}")
    print(f"Total task types: {len(all_tags)}")
    print(f"\nTask types: {', '.join(all_tags)}")


if __name__ == "__main__":
    main()
