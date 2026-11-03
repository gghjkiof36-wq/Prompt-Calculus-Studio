"""Small, non-executing Python source index; declarations are not call edges."""
import ast


def symbols(text):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return []
    result = []

    def visit(node, parents=()):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            parents = (*parents, node.name)
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            result.append((start, node.end_lineno, '.'.join(parents)))
        for child in ast.iter_child_nodes(node):
            visit(child, parents)

    try:
        visit(tree)
    except RecursionError:
        return []
    return result


def enclosing(index, line):
    matches = [s for s in index if s[0] <= line <= s[1]]
    return min(matches, key=lambda s: s[1] - s[0]) if matches else None
