# Influencer Pro

Influencer Pro is a project for building tools and workflows for influencer marketing.

## Overview

This repository provides a lightweight starting point for influencer marketing tooling, including:
- campaign planning helpers
- content creator tracking utilities
- quick reporting summaries

## Project structure

```text
.
├── README.md
├── pyproject.toml
├── pytest.ini
├── .gitignore
├── src/
│   └── influencer_pro/
│       ├── __init__.py
│       └── cli.py
└── tests/
    └── test_cli.py
```

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
influencer-pro --campaign "summer-launch" --influencers "@creatorone" "@creatortwo"
```

## Contributing

Contributions and suggestions are welcome. Please open an issue to discuss a change before submitting a pull request.
