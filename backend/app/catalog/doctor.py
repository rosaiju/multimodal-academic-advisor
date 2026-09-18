"""Catalog doctor - validate and inspect a degree catalog from the command line.

    python -m app.catalog.doctor ../data/catalog/demo_university_cs.yaml
    python -m app.catalog.doctor ../data/catalog            # every file in a directory
    python -m app.catalog.doctor <file> --course COSC490    # one prerequisite tree

Exits non-zero if a catalog fails to load or a check fires, so it works in CI and
in a pre-commit hook.

This exists for whoever encodes the real Morgan State COSC requirements: a 200-line
YAML file is easy to get subtly wrong, and "the server starts" is a weak signal. The
checks below catch the mistakes that produce a plausible-looking but wrong catalog.
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

from app.catalog.explain import describe_program, prerequisite_depth, prerequisite_tree
from app.catalog.loader import CatalogError, load_program
from app.catalog.schema import Program

# ASCII only: the Windows console defaults to cp1252 and raises
# UnicodeEncodeError on box-drawing characters. The whole team runs Windows.
DIM = "-" * 72


def _fmt_credits(value: Decimal) -> str:
    return f"{value.normalize():f}" if value == value.to_integral_value() else f"{value}"


def check_program(program: Program) -> list[str]:
    """Structural warnings a schema check cannot catch.

    These are warnings, not errors: each one is legitimate in some catalog. They
    exist to make a human look twice.
    """
    warnings: list[str] = []
    summary = describe_program(program)

    demanded = summary.course_requirement_credits
    if demanded > program.total_credits_required:
        warnings.append(
            f"requirement blocks demand {_fmt_credits(demanded)} credits but the degree "
            f"totals {_fmt_credits(program.total_credits_required)} - over-constrained?"
        )

    referenced: set[str] = set()
    for block in program.requirement_blocks:
        referenced.update(c.code for c in program.courses_for_block(block))
    orphans = sorted({c.code for c in program.courses} - referenced)
    if orphans:
        warnings.append(
            f"{len(orphans)} course(s) satisfy no requirement block: {', '.join(orphans)}"
        )

    for course in program.courses:
        tree = prerequisite_tree(program, course.code)

        def has_cycle(node: dict) -> bool:
            return node.get("cycle", False) or any(
                has_cycle(k) for k in node.get("prerequisites", [])
            )

        if has_cycle(tree):
            warnings.append(f"{course.code}: prerequisite cycle detected")

    for block in program.requirement_blocks:
        if not program.courses_for_block(block) and block.type not in {"gpa", "residency"}:
            warnings.append(f"block {block.id!r}: no course can satisfy it")

    return warnings


def print_program(program: Program, *, verbose: bool) -> None:
    summary = describe_program(program)

    print(DIM)
    print(f"  {summary.program}")
    print(f"  {summary.institution} | catalog {summary.catalog_year} | {summary.program_id}")
    print(DIM)
    print(
        f"  {_fmt_credits(summary.total_credits_required)} credits required"
        f"  |  min GPA {summary.min_gpa}"
        f"  |  min major GPA {summary.min_major_gpa}"
    )
    print(f"  {len(program.courses)} courses  |  {len(summary.blocks)} requirement blocks")
    print(f"  blocks demand {_fmt_credits(summary.course_requirement_credits)} credits explicitly")
    print()

    for block in summary.blocks:
        flag = "  [needs advisor]" if block.advisor_approval_required else ""
        print(f"  * {block.name}{flag}")
        print(f"      {block.requirement}")
        if block.min_grade and block.min_grade != "D":
            print(f"      minimum grade: {block.min_grade}")
        if verbose and block.eligible_courses:
            for c in block.eligible_courses:
                print(f"        {c.code:<10} {c.title[:44]:<44} {_fmt_credits(c.credits)} cr")
        elif block.eligible_courses:
            print(f"      {len(block.eligible_courses)} eligible course(s)")
        if block.note:
            note = " ".join(block.note.split())
            print(f"      note: {note}")
        print()


def print_prerequisites(program: Program, code: str) -> int:
    code = code.upper()
    if program.course(code) is None:
        print(f"  unknown course {code!r}", file=sys.stderr)
        return 1

    def render(node: dict, indent: int = 0) -> None:
        marker = "  " * indent + ("`- " if indent else "")
        cycle = "   <-- CYCLE" if node.get("cycle") else ""
        print(f"  {marker}{node['code']}  {node['title']}{cycle}")
        for child in node.get("prerequisites", []):
            render(child, indent + 1)

    print(DIM)
    print(f"  Prerequisite chain for {code}")
    print(DIM)
    render(prerequisite_tree(program, code))
    print()
    print(f"  chain depth: {prerequisite_depth(program, code)} course(s)")
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.catalog.doctor",
        description="Validate and inspect degree catalog files.",
    )
    parser.add_argument("path", type=Path, help="catalog .yaml file, or a directory of them")
    parser.add_argument(
        "--course", metavar="CODE", help="show the prerequisite tree for one course"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="list every eligible course per block"
    )
    parser.add_argument(
        "--strict", action="store_true", help="treat warnings as failures (use in CI)"
    )
    args = parser.parse_args(argv)

    if args.path.is_dir():
        files = sorted([*args.path.glob("*.yaml"), *args.path.glob("*.yml")])
        if not files:
            print(f"  no catalog files in {args.path}", file=sys.stderr)
            return 1
    else:
        files = [args.path]

    failed = False
    warned = False

    for path in files:
        try:
            program = load_program(path)
        except CatalogError as exc:
            print(f"\n  FAILED  {path.name}\n", file=sys.stderr)
            print(f"{exc}\n", file=sys.stderr)
            failed = True
            continue

        if args.course:
            if print_prerequisites(program, args.course):
                failed = True
            continue

        print_program(program, verbose=args.verbose)

        warnings = check_program(program)
        if warnings:
            warned = True
            print("  WARNINGS")
            for w in warnings:
                print(f"    ! {w}")
            print()
        else:
            print("  No warnings.\n")

    if failed:
        return 1
    if warned and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
