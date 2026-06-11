"""Language-specific symbol extraction — Tree-sitter primary, regex fallback."""

from __future__ import annotations

import ast
import logging
import re

logger = logging.getLogger(__name__)


def extract_symbols(content: str, language: str) -> list[dict]:
    ts_result = _try_tree_sitter(content, language)
    if ts_result is not None:
        return ts_result

    logger.debug("Tree-sitter unavailable for %s, using regex fallback", language)
    return _extract_regex(content, language)


def _try_tree_sitter(content: str, language: str) -> list[dict] | None:
    try:
        from patchquest.memory.tree_sitter_extractors import extract_symbols_tree_sitter
        return extract_symbols_tree_sitter(content, language)
    except ImportError:
        return None
    except Exception as e:
        logger.debug("Tree-sitter extraction failed for %s: %s", language, e)
        return None


def _extract_regex(content: str, language: str) -> list[dict]:
    if language == "python":
        return _extract_python_ast(content)
    if language in ("typescript", "javascript"):
        return _extract_typescript_regex(content)
    if language == "rust":
        return _extract_rust_regex(content)
    if language in ("c", "cpp"):
        return _extract_c_regex(content)
    if language == "go":
        return _extract_go_regex(content)
    if language == "java":
        return _extract_java_regex(content)
    return []


def _extract_python_ast(content: str) -> list[dict]:
    symbols: list[dict] = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return symbols

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            symbols.append({
                "type": "class", "name": node.name,
                "line_start": node.lineno, "line_end": node.end_lineno,
                "parser_source": "ast",
            })
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    symbols.append({
                        "type": "method", "name": item.name,
                        "line_start": item.lineno, "line_end": item.end_lineno,
                        "parent": node.name, "parser_source": "ast",
                    })
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            symbols.append({
                "type": "function", "name": node.name,
                "line_start": node.lineno, "line_end": node.end_lineno,
                "parser_source": "ast",
            })
        elif isinstance(node, ast.Import):
            for alias in node.names:
                symbols.append({"type": "import", "name": alias.name, "line_start": node.lineno, "parser_source": "ast"})
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                symbols.append({"type": "import", "name": node.module, "line_start": node.lineno, "parser_source": "ast"})
    return symbols


_TS_FUNCTION = re.compile(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)")
_TS_CLASS = re.compile(r"(?:export\s+)?class\s+(\w+)")
_TS_CONST_FN = re.compile(r"(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(")
_TS_IMPORT = re.compile(r"import\s+.*?from\s+['\"]([^'\"]+)['\"]")
_TS_EXPORT = re.compile(r"export\s+(?:default\s+)?(?:class|function|const|let|var|interface|type|enum)\s+(\w+)")


def _extract_typescript_regex(content: str) -> list[dict]:
    symbols: list[dict] = []
    for i, line in enumerate(content.split("\n"), 1):
        if m := _TS_FUNCTION.match(line):
            symbols.append({"type": "function", "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
        elif m := _TS_CLASS.match(line):
            symbols.append({"type": "class", "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
        elif m := _TS_CONST_FN.match(line):
            symbols.append({"type": "function", "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
        elif m := _TS_IMPORT.match(line):
            symbols.append({"type": "import", "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
        elif m := _TS_EXPORT.match(line):
            symbols.append({"type": "export", "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
    return symbols


_RUST_FN = re.compile(r"(?:pub\s+)?(?:async\s+)?fn\s+(\w+)")
_RUST_STRUCT = re.compile(r"(?:pub\s+)?struct\s+(\w+)")
_RUST_ENUM = re.compile(r"(?:pub\s+)?enum\s+(\w+)")
_RUST_TRAIT = re.compile(r"(?:pub\s+)?trait\s+(\w+)")
_RUST_IMPL = re.compile(r"impl(?:<[^>]*>)?\s+(\w+)")
_RUST_MOD = re.compile(r"(?:pub\s+)?mod\s+(\w+)")
_RUST_USE = re.compile(r"use\s+([^;]+)")


def _extract_rust_regex(content: str) -> list[dict]:
    symbols: list[dict] = []
    for i, line in enumerate(content.split("\n"), 1):
        if m := _RUST_FN.match(line):
            symbols.append({"type": "function", "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
        elif m := _RUST_STRUCT.match(line):
            symbols.append({"type": "struct", "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
        elif m := _RUST_ENUM.match(line):
            symbols.append({"type": "enum", "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
        elif m := _RUST_TRAIT.match(line):
            symbols.append({"type": "trait", "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
        elif m := _RUST_IMPL.match(line):
            symbols.append({"type": "impl", "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
        elif m := _RUST_MOD.match(line):
            symbols.append({"type": "module", "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
        elif m := _RUST_USE.match(line):
            symbols.append({"type": "import", "name": m.group(1).strip(), "line_start": i, "parser_source": "regex_fallback"})
    return symbols


_C_FUNC = re.compile(r"^[\w\*\s]+\s+(\w+)\s*\([^)]*\)\s*\{", re.MULTILINE)
_C_CLASS = re.compile(r"class\s+(\w+)")
_C_INCLUDE = re.compile(r"#include\s+[<\"]([^>\"]+)[>\"]")


def _extract_c_regex(content: str) -> list[dict]:
    symbols: list[dict] = []
    for i, line in enumerate(content.split("\n"), 1):
        if m := _C_INCLUDE.match(line):
            symbols.append({"type": "include", "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
        elif m := _C_CLASS.match(line):
            symbols.append({"type": "class", "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
    for m in _C_FUNC.finditer(content):
        line_num = content[:m.start()].count("\n") + 1
        symbols.append({"type": "function", "name": m.group(1), "line_start": line_num, "parser_source": "regex_fallback"})
    return symbols


_GO_FUNC = re.compile(r"func\s+(\w+)\s*\(")
_GO_METHOD = re.compile(r"func\s+\([^)]+\)\s+(\w+)\s*\(")
_GO_TYPE = re.compile(r"type\s+(\w+)\s+(struct|interface)")
_GO_IMPORT = re.compile(r'"([^"]+)"')


def _extract_go_regex(content: str) -> list[dict]:
    symbols: list[dict] = []
    for i, line in enumerate(content.split("\n"), 1):
        if m := _GO_METHOD.match(line):
            symbols.append({"type": "method", "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
        elif m := _GO_FUNC.match(line):
            symbols.append({"type": "function", "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
        elif m := _GO_TYPE.match(line):
            symbols.append({"type": m.group(2), "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
    return symbols


_JAVA_CLASS = re.compile(r"(?:public\s+)?(?:abstract\s+)?class\s+(\w+)")
_JAVA_INTERFACE = re.compile(r"(?:public\s+)?interface\s+(\w+)")
_JAVA_METHOD = re.compile(r"(?:public|private|protected)\s+(?:static\s+)?[\w<>\[\]]+\s+(\w+)\s*\(")
_JAVA_IMPORT = re.compile(r"import\s+([\w.]+);")
_JAVA_PACKAGE = re.compile(r"package\s+([\w.]+);")


def _extract_java_regex(content: str) -> list[dict]:
    symbols: list[dict] = []
    for i, line in enumerate(content.split("\n"), 1):
        if m := _JAVA_PACKAGE.match(line):
            symbols.append({"type": "package", "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
        elif m := _JAVA_IMPORT.match(line):
            symbols.append({"type": "import", "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
        elif m := _JAVA_CLASS.match(line):
            symbols.append({"type": "class", "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
        elif m := _JAVA_INTERFACE.match(line):
            symbols.append({"type": "interface", "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
        elif m := _JAVA_METHOD.match(line):
            symbols.append({"type": "method", "name": m.group(1), "line_start": i, "parser_source": "regex_fallback"})
    return symbols
