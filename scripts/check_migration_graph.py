#!/usr/bin/env python3
"""
Static migration graph guard.

Fails when an app has multiple live leaf migrations after merge.

This intentionally avoids importing Django so it can run early in CI.
It is deliberately tolerant of legacy historical quirks such as duplicate
numeric prefixes in already-merged migration history, since rewriting old
applied migrations is not production-safe.
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_DIRS = sorted(list((ROOT / "core").glob("migrations")) + list((ROOT / "apps").glob("*/migrations")))
@dataclass(frozen=True)
class MigrationFile:
    app: str
    path: Path
    name: str


def parse_dependencies(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Migration":
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name) and target.id == "dependencies":
                            return extract_dependency_names(item.value)
    return []


def extract_dependency_names(node: ast.AST) -> list[str]:
    names: list[str] = []
    if not isinstance(node, (ast.List, ast.Tuple)):
        return names

    for elt in node.elts:
        if isinstance(elt, ast.Tuple) and len(elt.elts) >= 2:
            dep_name = literal_str(elt.elts[1])
            if dep_name:
                names.append(dep_name)
        elif isinstance(elt, ast.List) and len(elt.elts) >= 2:
            dep_name = literal_str(elt.elts[1])
            if dep_name:
                names.append(dep_name)
    return names


def literal_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def load_app_migrations(migration_dir: Path) -> list[MigrationFile]:
    files: list[MigrationFile] = []
    app = str(migration_dir.parent.relative_to(ROOT))

    for path in sorted(migration_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        files.append(
            MigrationFile(
                app=app,
                path=path,
                name=path.stem,
            )
        )

    return files


def main() -> int:
    all_errors: list[str] = []

    for migration_dir in MIGRATION_DIRS:
        files = load_app_migrations(migration_dir)
        if not files:
            continue

        by_name = {file.name: file for file in files}
        parents_to_children: dict[str, set[str]] = defaultdict(set)

        for file in files:
            for dep_name in parse_dependencies(file.path):
                if dep_name in by_name:
                    parents_to_children[dep_name].add(file.name)

        leaves = sorted(file.name for file in files if not parents_to_children.get(file.name))
        if len(leaves) > 1:
            all_errors.append(
                f"{files[0].app}: multiple migration leaves detected [{', '.join(leaves)}]"
            )

    if all_errors:
        print("Migration graph check failed:\n")
        for error in all_errors:
            print(f"- {error}")
        print(
            "\nResolve by rebasing onto the latest base branch and leaving each app"
            " with a single migration leaf before merge."
        )
        return 1

    print("Migration graph check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
