# GitHub OpenAPI Spec Splitter

Two CLI tools that let you browse a large OpenAPI spec (like GitHub's 100k+ line YAML file), pick the API categories you need, and extract a custom spec containing only those endpoints.

## Requirements

- Python 3.8+
- PyYAML (only needed for `build_spec.py`)

```bash
pip install pyyaml
```

## Workflow

```
┌─────────────────────┐
│  Full GitHub Spec   │
│  (api.github.com    │
│   .yaml — huge)     │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐      ┌─────────────────────┐
│  extract_tasks.py   │ ───▶ │   task_menu.md      │
│                     │      │   (browsable menu    │
│  Streams the file   │      │    of all API        │
│  line-by-line,      │      │    categories)       │
│  no full parse      │      └─────────┬───────────┘
└─────────────────────┘                │
                                       │  You read the menu,
                                       │  pick your tags
                                       ▼
┌─────────────────────┐      ┌─────────────────────┐
│  build_spec.py      │ ───▶ │  custom_spec.yaml   │
│                     │      │  (small, standalone  │
│  Full YAML parse,   │      │   OpenAPI spec with  │
│  filters by tags,   │      │   only your stuff)   │
│  resolves all $refs │      └─────────────────────┘
└─────────────────────┘
     ▲
     │
     │  Feed it the SAME original
     │  spec + your chosen tags
```

## Step 1 — Generate the Menu

```bash
python3 extract_tasks.py api.github.com.yaml
```

This streams through the spec line-by-line (no full YAML parse, so it's fast even on huge files) and writes `task_menu.md`. Open it and you'll see something like:

```
| #  | Task Type              | Description                                    | Endpoints |
|----|------------------------|------------------------------------------------|----------:|
| 1  | actions                | Endpoints to manage GitHub Actions ...          |        42 |
| 2  | activity               | Activity APIs provide access to ...             |        12 |
| 3  | agent-tasks            | Endpoints to manage and interact with ...       |         5 |
| 4  | apps                   | Information for integrations ...                |        18 |
| 5  | gists                  | View, modify your gists.                        |        12 |
| ...                                                                                        |
```

Each row is a "task type" (an OpenAPI tag). The menu also includes a detailed section listing every endpoint per tag, and a copy-paste-ready comma-separated list of all tags at the bottom.

You can also specify a custom output path:

```bash
python3 extract_tasks.py api.github.com.yaml -o my_menu.md
```

## Step 2 — Build Your Custom Spec

Pick the tags you want from the menu, then pass them to `build_spec.py` along with the **same original spec file**:

```bash
python3 build_spec.py api.github.com.yaml -t agent-tasks,gists -o my_spec.yaml
```

This will:
1. Parse the full spec (uses C-accelerated YAML loader when available)
2. Keep only paths where at least one operation is tagged with your selections
3. Recursively resolve all `$ref` pointers so the output is self-contained
4. Write a clean, standalone OpenAPI YAML file

The output spec is ready to use with code generators, Swagger UI, Postman import, or as context for an LLM.

### Multiple ways to pass tags

```bash
# Comma-separated
python3 build_spec.py spec.yaml -t agent-tasks,gists,repos

# Multiple -t flags
python3 build_spec.py spec.yaml -t agent-tasks -t gists -t repos

# From a file (supports comments with #)
cat > my_tags.txt << EOF
# Core stuff I need
agent-tasks
gists
repos
# Maybe later:
# issues
# pulls
EOF

python3 build_spec.py spec.yaml --tags-file my_tags.txt -o my_spec.yaml
```

## Example End-to-End

```bash
# 1. Generate the menu
python3 extract_tasks.py api.github.com.yaml -o task_menu.md

# 2. Browse it
cat task_menu.md   # or open in any markdown viewer

# 3. Build a spec with just the agent-tasks and gists endpoints
python3 build_spec.py api.github.com.yaml -t agent-tasks,gists -o agent-and-gists.yaml

# 4. Check what you got
head -20 agent-and-gists.yaml
```

## How It Handles Large Files

- **`extract_tasks.py`** never loads the full YAML. It reads line-by-line with a simple state machine that recognizes the `tags:` and `paths:` sections, pulling out tag names, descriptions, HTTP methods, paths, and summaries. This makes it fast and memory-light even on 100k+ line specs.

- **`build_spec.py`** does need a full YAML parse (because it has to follow `$ref` pointers into `components/schemas`, `components/parameters`, etc. and include them in the output). It uses PyYAML's C loader for speed. On a typical machine, parsing the full GitHub spec takes a few seconds.
