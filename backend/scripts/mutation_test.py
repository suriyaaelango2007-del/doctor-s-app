"""Tiny mutation tester (mutmut doesn't run on Windows).

For each mutation point in the target modules, rewrite the module with one
small change, run the test suite, and record whether the tests caught it.
Survivors = behaviour the tests don't pin down.

Usage (from backend/, with the test Postgres running):
  uv run python -m scripts.mutation_test                       # all targets
  uv run python -m scripts.mutation_test app/time_rules.py     # one file
"""

import ast
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = [
    "app/time_rules.py",
    "app/services/booking.py",
    "app/routers/public.py",
    "app/auth.py",
]
TEST_TIMEOUT = 120

CMP_SWAP = {
    ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.In: ast.NotIn, ast.NotIn: ast.In,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
}
BINOP_SWAP = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.FloorDiv}
SKIP_CALL_PREFIXES = ("log.", "print(")


def _src(node: ast.AST) -> str:
    return ast.unparse(node).replace("\n", " ")[:70]


def mutation_points(tree: ast.Module):
    """Yield (node_path_index, description, apply_fn) for every mutation."""
    points = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                if type(op) in CMP_SWAP:
                    new = CMP_SWAP[type(op)]
                    points.append((node, f"{type(op).__name__} -> {new.__name__} in `{_src(node)}`",
                                   lambda n, i=i, new=new: n.ops.__setitem__(i, new())))
        elif isinstance(node, ast.BoolOp):
            new = ast.Or if isinstance(node.op, ast.And) else ast.And
            points.append((node, f"{type(node.op).__name__} -> {new.__name__} in `{_src(node)}`",
                           lambda n, new=new: setattr(n, "op", new())))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            points.append((node, f"drop `not` in `{_src(node)}`", "unwrap"))
        elif isinstance(node, ast.BinOp) and type(node.op) in BINOP_SWAP:
            new = BINOP_SWAP[type(node.op)]
            points.append((node, f"{type(node.op).__name__} -> {new.__name__} in `{_src(node)}`",
                           lambda n, new=new: setattr(n, "op", new())))
        elif isinstance(node, ast.Constant) and not isinstance(node.value, str):
            if isinstance(node.value, bool):
                points.append((node, f"{node.value} -> {not node.value}",
                               lambda n: setattr(n, "value", not n.value)))
            elif isinstance(node.value, int) and node.value not in (0,):
                points.append((node, f"{node.value} -> {node.value + 1}",
                               lambda n: setattr(n, "value", n.value + 1)))
        elif isinstance(node, ast.Raise):
            points.append((node, f"remove `{_src(node)}`", "pass"))
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            s = _src(node)
            if not s.startswith(SKIP_CALL_PREFIXES):
                points.append((node, f"remove call `{s}`", "pass"))
        elif isinstance(node, ast.Return) and node.value is not None:
            points.append((node, f"`{_src(node)}` -> return None", "return_none"))
    return points


class _Replace(ast.NodeTransformer):
    def __init__(self, target, mode):
        self.target, self.mode = target, mode

    def visit(self, node):
        if node is self.target:
            if self.mode == "pass":
                return ast.copy_location(ast.Pass(), node)
            if self.mode == "unwrap":
                return node.operand
            if self.mode == "return_none":
                return ast.copy_location(ast.Return(value=ast.Constant(None)), node)
        return self.generic_visit(node)


def build_mutant(source: str, index: int) -> tuple[str, int, str]:
    tree = ast.parse(source)
    node, desc, action = mutation_points(tree)[index]
    line = node.lineno
    if callable(action):
        action(node)
    else:
        tree = _Replace(node, action).visit(tree)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree), line, desc


def run_tests() -> str:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", "--no-header"],
            cwd=ROOT, capture_output=True, text=True, timeout=TEST_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "killed (timeout)"
    return "survived" if r.returncode == 0 else "killed"


def restore_backups() -> None:
    """Undo a previous run that was killed mid-mutation."""
    for bak in ROOT.glob("app/**/*.py.mutbak"):
        bak.replace(bak.with_suffix(""))
        print(f"Restored {bak.with_suffix('').relative_to(ROOT)} from an interrupted run")


def main(targets: list[str]) -> None:
    restore_backups()
    print("Baseline test run ...", flush=True)
    if run_tests() != "survived":
        sys.exit("Tests fail without mutations — fix them first (is the test Postgres running?).")

    results = []
    for rel in targets:
        path = ROOT / rel
        original = path.read_text(encoding="utf-8")
        original_bytes = path.read_bytes()
        backup = path.with_name(path.name + ".mutbak")
        backup.write_bytes(original_bytes)
        n = len(mutation_points(ast.parse(original)))
        print(f"\n{rel}: {n} mutants", flush=True)
        try:
            for i in range(n):
                mutated, line, desc = build_mutant(original, i)
                path.write_text(mutated, encoding="utf-8")
                t = time.time()
                outcome = run_tests()
                mark = "." if outcome.startswith("killed") else "S"
                print(f"  [{mark}] {rel}:{line}  {desc}  ({time.time() - t:.0f}s)", flush=True)
                results.append({"file": rel, "line": line, "mutation": desc, "outcome": outcome})
        finally:
            path.write_bytes(original_bytes)  # always restore, byte-for-byte
            backup.unlink(missing_ok=True)

    killed = sum(r["outcome"].startswith("killed") for r in results)
    survivors = [r for r in results if r["outcome"] == "survived"]
    print(f"\nMutation score: {killed}/{len(results)} killed ({100 * killed / max(len(results), 1):.0f}%)")
    if survivors:
        print("\nSurvivors (tests did not notice):")
        for r in survivors:
            print(f"  {r['file']}:{r['line']}  {r['mutation']}")
    (ROOT / ".mutation-report.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:] or DEFAULT_TARGETS)
