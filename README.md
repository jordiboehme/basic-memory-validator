# basic-memory-validator

[![Tests](https://github.com/jordiboehme/basic-memory-validator/actions/workflows/test.yml/badge.svg)](https://github.com/jordiboehme/basic-memory-validator/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![GitHub release](https://img.shields.io/github/v/release/jordiboehme/basic-memory-validator)](https://github.com/jordiboehme/basic-memory-validator/releases)

GitHub Action that validates [Basic Memory](https://github.com/basicmachines-co/basic-memory) markdown notes — checks YAML frontmatter, wikilinks, permalinks, and tags.

## Quick Start

```yaml
name: Validate Knowledge Base
on:
  pull_request:
    branches: [main]
    paths: ['memory/**/*.md']
  push:
    branches: [main]
    paths: ['memory/**/*.md']

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: jordiboehme/basic-memory-validator@v1
        with:
          paths: ['memory/']
```

### Multiple directories

```yaml
      - uses: jordiboehme/basic-memory-validator@v1
        with:
          paths: ['memory/', 'notes/']
```

### Custom config

```yaml
      - uses: jordiboehme/basic-memory-validator@v1
        with:
          paths: ['memory/']
          config: '.validation-config.json'
```

## Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `paths` | `['memory/']` | Directories to validate |
| `config` | | Path to a JSON config override file |

## Validation Rules

### Errors (block merge)

| Rule | Check |
|------|-------|
| F001 | YAML frontmatter is parseable (delimited by `---`) |
| F002 | Required fields present: `title`, `type`, `permalink`, `tags` |
| F003 | `type` is one of: `note`, `manifest` |
| F004 | `tags` is a non-empty list |
| F005 | `permalink` is lowercase slash-separated path |

### Warnings (informational)

| Rule | Check |
|------|-------|
| Q001 | Broken wikilinks — `[[Title]]` doesn't match any note's `title` |
| Q002 | Duplicate permalinks across files |
| Q003 | Tags not following `lowercase-with-hyphens` format |
| Q004 | File has no meaningful content beyond frontmatter |

## Config Override

Create a JSON file to override default validation settings:

```json
{
  "valid_types": ["note", "manifest", "registry"],
  "required_fields": ["title", "type", "permalink", "tags"],
  "permalink_pattern": "^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9/-]*$",
  "tag_pattern": "^[a-z0-9]+(-[a-z0-9]+)*$",
  "min_content_lines": 3
}
```

Only include the fields you want to override.

## Local Usage

Run the validator locally (requires Python 3.10+ and `pyyaml`):

```bash
pip install pyyaml
python validate_notes.py memory/
python validate_notes.py memory/ notes/              # multiple directories
python validate_notes.py memory/ --config config.json # custom config
```

## License

[MIT](LICENSE)
