#!/usr/bin/env python3
"""Re-derive the interface-shape figures CLAUDE.md's accepted items quote.

Those figures go stale on the next commit, which is why the doc tells a reader
to re-derive rather than to trust them. This is what re-derives them, so the
instruction names a command instead of describing one.

It reports four things, and the last is the one a written figure cannot carry:

* how much source there is, as modules and functions;
* which parameter-NAME groups repeat across signatures, which is the first limb
  of the doc's own test for introducing a type;
* anonymous multi-value returns bucketed by their annotated shape, which is the
  second limb - a pair of the SAME types is a swap the type checker cannot
  catch, a pair of different ones is caught;
* the widest signatures WITH whether each is a framework boundary, because the
  doc's claim is about which ones are, and a count alone cannot say.

Usage:
    python3 scripts/interface_census.py [--json]

System Role:
    Development tooling. Reads the tree and prints; changes nothing.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rich.console import Console

#: Printing goes through rich rather than ``print``, which the lint rules refuse.
SAY = Console()

SRC = Path(__file__).resolve().parent.parent / "src" / "lsdsk"

#: A signature this wide is worth listing, whoever owns it.
WIDE = 6

#: The smallest return that can carry a swap: one value cannot be swapped.
SMALLEST_MULTI_VALUE = 2

#: How many of the repeated groups the report lists, loudest first.
REPORTED = 12

#: A name group repeated in at least this many signatures is worth reporting,
#: which is the threshold the doc's own test states.
REPEATED = 3

#: What marks a signature as a framework's rather than this project's. Click
#: passes a context, and the Windows bindings take a library handle.
FRAMEWORK_MARKERS = {"ctx": "click", "kernel32": "win32", "setupapi": "win32", "handle": "win32"}


@dataclass
class Signature:
    """One function's parameter list, with where it is and who owns its shape."""

    module: str
    name: str
    line: int
    parameters: tuple[str, ...]
    owner: str

    @property
    def width(self) -> int:
        """How many parameters it declares."""
        return len(self.parameters)


@dataclass
class Census:
    """Everything one pass over the tree found."""

    modules: int = 0
    functions: int = 0
    signatures: list[Signature] = field(default_factory=list["Signature"])
    returns: Counter[str] = field(default_factory=Counter[str])
    return_sites: dict[str, list[str]] = field(default_factory=dict[str, list[str]])


def owner_of(parameters: tuple[str, ...]) -> str:
    """Whose shape this signature is: a framework's, or this project's."""
    for parameter in parameters:
        marker = FRAMEWORK_MARKERS.get(parameter)
        if marker is not None:
            return marker
    return "lsdsk"


def parameters_of(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
    """Every declared parameter, minus the instance argument."""
    args = node.args
    named = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    return tuple(arg.arg for arg in named if arg.arg not in {"self", "cls"})


def return_shape(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """The annotated shape of a multi-value return, or ``None`` for anything else.

    A NAMED tuple type is not anonymous, so only a subscripted ``tuple[...]``
    with two or more elements counts - which is exactly the construct a swap of
    two same-typed members passes through unnoticed.
    """
    annotation = node.returns
    if not isinstance(annotation, ast.Subscript):
        return None
    root = annotation.value
    if getattr(root, "id", getattr(root, "attr", "")) != "tuple":
        return None
    inner = annotation.slice
    if not isinstance(inner, ast.Tuple) or len(inner.elts) < SMALLEST_MULTI_VALUE:
        return None
    if any(isinstance(element, ast.Constant) and element.value is Ellipsis for element in inner.elts):
        return None
    return f"tuple[{', '.join(ast.unparse(element) for element in inner.elts)}]"


def walk(root: Path) -> Census:
    """Read every module under `root` and count what is in it."""
    census = Census()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        census.modules += 1
        module = str(path.relative_to(root.parent.parent))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            census.functions += 1
            parameters = parameters_of(node)
            census.signatures.append(Signature(module, node.name, node.lineno, parameters, owner_of(parameters)))
            shape = return_shape(node)
            if shape is not None:
                census.returns[shape] += 1
                census.return_sites.setdefault(shape, []).append(f"{module}:{node.lineno} {node.name}")
    return census


def name_groups(signatures: list[Signature]) -> list[tuple[tuple[str, ...], int, str]]:
    """Parameter-name PAIRS and TRIPLES that repeat, with whose shape they are.

    Pairs and triples rather than every subset: the doc's recorded groups are
    of that size, and every larger group contains a smaller one, so a wider
    sweep reports the same finding several times over.
    """
    counts: Counter[tuple[str, ...]] = Counter()
    owners: dict[tuple[str, ...], set[str]] = {}
    for signature in signatures:
        names = sorted(set(signature.parameters))
        groups = [
            (names[first], names[second]) for first in range(len(names)) for second in range(first + 1, len(names))
        ]
        groups += [
            (names[first], names[second], names[third])
            for first in range(len(names))
            for second in range(first + 1, len(names))
            for third in range(second + 1, len(names))
        ]
        for group in groups:
            counts[group] += 1
            owners.setdefault(group, set()).add(signature.owner)
    found = [(group, count, "/".join(sorted(owners[group]))) for group, count in counts.items() if count >= REPEATED]
    return sorted(found, key=lambda entry: (-entry[1], entry[0]))


@dataclass(frozen=True)
class WideSignature:
    """One signature at or above the width worth listing."""

    where: str
    name: str
    width: int
    owner: str


@dataclass(frozen=True)
class RepeatedGroup:
    """One parameter-name group and how many signatures carry it."""

    group: tuple[str, ...]
    signatures: int
    owner: str


@dataclass(frozen=True)
class Figures:
    """Everything one census found, typed so a reader cannot misread a field."""

    modules: int
    functions: int
    wide_signatures: tuple[WideSignature, ...]
    wide_by_owner: dict[str, int]
    repeated_name_groups: tuple[RepeatedGroup, ...]
    anonymous_multi_value_returns: int
    largest_return_shape: tuple[str, int]
    same_typed_return_shapes: dict[str, int]


def report(census: Census) -> Figures:
    """Everything the doc quotes, as data."""
    wide = sorted(
        (s for s in census.signatures if s.width >= WIDE),
        key=lambda s: (-s.width, s.module, s.line),
    )
    same_typed = {
        shape: count
        for shape, count in census.returns.items()
        if len({element.strip() for element in shape[len("tuple[") : -1].split(",")}) == 1
    }
    return Figures(
        modules=census.modules,
        functions=census.functions,
        wide_signatures=tuple(WideSignature(f"{s.module}:{s.line}", s.name, s.width, s.owner) for s in wide),
        wide_by_owner=dict(Counter(s.owner for s in wide)),
        repeated_name_groups=tuple(
            RepeatedGroup(group, count, owner) for group, count, owner in name_groups(census.signatures)[:REPORTED]
        ),
        anonymous_multi_value_returns=sum(census.returns.values()),
        largest_return_shape=max(census.returns.items(), key=lambda entry: entry[1], default=("none", 0)),
        same_typed_return_shapes=same_typed,
    )


def main() -> int:
    """Print the census."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit the figures as JSON.")
    arguments = parser.parse_args()

    figures = report(walk(SRC))
    if arguments.json:
        SAY.print(json.dumps(asdict(figures), indent=2, default=str))
        return 0

    SAY.print(f"modules {figures.modules}   functions {figures.functions}")
    SAY.print(f"\nsignatures at {WIDE}+ parameters, by whose shape they are: {figures.wide_by_owner}")
    for signature in figures.wide_signatures:
        SAY.print(f"  {signature.width:>2}  {signature.owner:<6} {signature.where} {signature.name}")
    SAY.print(f"\nparameter-name groups in {REPEATED}+ signatures (top {REPORTED}):")
    for repeated in figures.repeated_name_groups:
        SAY.print(f"  {repeated.signatures:>3}  {repeated.owner:<6} {', '.join(repeated.group)}")
    SAY.print(f"\nanonymous multi-value returns: {figures.anonymous_multi_value_returns}")
    SAY.print(f"  largest shape: {figures.largest_return_shape}")
    SAY.print(f"  same-typed shapes: {figures.same_typed_return_shapes or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
