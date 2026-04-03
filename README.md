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

You can also specify a custom output path, filter to specific tags, and exclude paths:

```bash
python3 extract_tasks.py api.github.com.yaml -o my_menu.md

# Only show specific tags
python3 extract_tasks.py api.github.com.yaml --tags repos,actions,issues,pulls

# Exclude enterprise/admin endpoints from the menu
python3 extract_tasks.py api.github.com.yaml --tags repos,actions \
    --exclude-paths '*/hosted-runners/*' '*/cache/*' '*/rulesets/*'
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

### Path-level filtering

Tags like `actions` (184 endpoints) and `repos` (201 endpoints) include a lot of enterprise/admin endpoints you probably don't need. Use `--exclude-paths` to trim them:

```bash
# Exclude runner management, cache settings, and rulesets
python3 build_spec.py spec.yaml -t actions,repos \
    --exclude-paths '*/hosted-runners/*' '*/cache/*' '*/rulesets/*' \
        '*/self-hosted-runners/*' '*/runner-groups/*'

# Only include repo-level paths (skip org/enterprise admin)
python3 build_spec.py spec.yaml -t actions \
    --include-paths '/repos/*'

# Load exclude patterns from a file
cat > exclude.txt << EOF
# Enterprise/admin bloat
*/hosted-runners/*
*/self-hosted-runners/*
*/runner-groups/*
*/cache/*
*/rulesets/*
*/pages/*
*/attestations/*
*/autolinks/*
EOF

python3 build_spec.py spec.yaml -t repos,actions,issues,pulls \
    --exclude-paths-file exclude.txt -o my_spec.yaml
```

Both `--exclude-paths` and `--include-paths` use glob patterns matched against the API path (e.g. `/repos/{owner}/{repo}/actions/workflows`). Exclude is applied first, then include.

## Why Filter? The Noise Problem

GitHub's full OpenAPI spec contains **900+ endpoints across 46 tags**. The vast majority are enterprise administration, org-level policy management, and niche features that an AI agent (or most developers) will never touch. Without filtering, you're feeding thousands of lines of irrelevant schema into your LLM context window or code generator.

Here's what the noise looks like in practice:

| Category | Examples | Why it's noise |
|----------|----------|----------------|
| **Enterprise/org admin** | Runner management, cache retention policies, fork PR approval settings, OIDC customization, workflow permission controls | Infrastructure ops — not something a dev or AI agent configuring a repo needs |
| **Self-hosted runners** | Create/delete runners, manage labels, registration tokens, JIT configs | Only relevant if you manage your own CI infrastructure |
| **Branch protection & rulesets** | Ruleset CRUD, status check contexts, admin enforcement, deployment branch policies | Policy governance — set once by a human, not by an agent writing code |
| **GitHub Pages** | Pages builds, deployments, health checks, custom domains | Static site hosting — separate concern from repo/code management |
| **Security & compliance** | Attestations, vulnerability alerts, code scanning, secret scanning, security advisories | Important but not part of a prototyping workflow |
| **Niche repo features** | Autolinks, deploy keys, import/export, traffic stats, community profile, CODEOWNERS validation | Rarely needed for everyday dev work |
| **User account management** | GPG keys, SSH signing keys, email settings, social accounts, followers, blocking | Profile administration — not relevant to repo operations |
| **Activity & notifications** | Event streams, notification threads, feed subscriptions, starring/watching management | Consumption features — an agent creating code doesn't need to manage notifications |

### The included `glean-github-spec.yaml`

This repo ships a pre-built spec (`glean-github-spec.yaml`) tailored for an **AI agent that prototypes web apps**. It was generated using `exclude_defaults.txt` and contains **189 endpoints** across 8 tags:

| Tag | Endpoints | What's included |
|-----|-----------|-----------------|
| `repos` | 55 | Repo CRUD, branches, commits, file contents, compare, forks, releases, tags, collaborators |
| `actions` | 34 | Workflow runs, jobs, logs, artifacts, repo-level secrets and variables |
| `issues` | 37 | Issue CRUD, comments, labels, assignees, milestones, timeline |
| `pulls` | 26 | PR CRUD, review comments, reviews, merge, requested reviewers |
| `reactions` | 15 | Reactions on issues, PRs, comments, and releases |
| `git` | 13 | Low-level git: blobs, commits, refs (branches), tags, trees |
| `search` | 6 | Search code, commits, issues, labels, repos, users |
| `users` | 4 | Get/update authenticated user, look up users by name or ID |

To regenerate it or customize further:

```bash
python3 build_spec.py api.github.com.yaml \
    -t repos,actions,issues,pulls,git,search,reactions,users \
    --exclude-paths-file exclude_defaults.txt \
    -o glean-github-spec.yaml
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
