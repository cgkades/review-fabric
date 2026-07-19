#!/usr/bin/env python3
"""Opt-in live smoke helper; it never supplies, discovers, or prints credentials."""

from __future__ import annotations

import argparse
from pathlib import Path

from review_fabric.cli import main

parser = argparse.ArgumentParser(
    description="Run an explicitly configured tiny provider smoke review"
)
parser.add_argument("--config", type=Path, required=True, help="deliberately supplied JSON config")
parser.add_argument("repository", type=Path)
parser.add_argument("base")
parser.add_argument("head")
args = parser.parse_args()
if not args.config.is_file():
    parser.error("--config must name an existing explicit configuration")
raise SystemExit(main(["--config", str(args.config), str(args.repository), args.base, args.head]))
