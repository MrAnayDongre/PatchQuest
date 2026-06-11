"""Tree-sitter-based symbol extraction for multiple languages."""

from __future__ import annotations

import logging
from typing import Any

from patchquest.memory.tree_sitter_registry import (
    get_parser,
    is_language_available,
    language_for_file,
)

logger = logging.getLogger(__name__)


def extract_symbols_tree_sitter(content: str, language: str) -> list[dict] | None:
    if not is_language_available(language):
        return None

    try:
        parser = get_parser(language)
        tree = parser.parse(content.encode("utf-8"))
    except Exception as e:
        logger.debug("Tree-sitter parse error for %s: %s", language, e)
        return None

    extractor = _EXTRACTORS.get(language)
    if not extractor:
        return None

    return extractor(tree.root_node, content)


def _node_text(node, source: str) -> str:
    return source[node.start_byte:node.end_byte]


def _extract_python(root, source: str) -> list[dict]:
    symbols: list[dict] = []
    for node in _walk(root):
        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                parent = _get_parent_class(node)
                symbols.append({
                    "type": "method" if parent else "function",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parent": parent,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": "class",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type in ("import_statement", "import_from_statement"):
            mod = node.child_by_field_name("module_name")
            if mod:
                symbols.append({
                    "type": "import",
                    "name": _node_text(mod, source),
                    "line_start": node.start_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
            else:
                name = node.child_by_field_name("name")
                if name:
                    symbols.append({
                        "type": "import",
                        "name": _node_text(name, source),
                        "line_start": node.start_point[0] + 1,
                        "parser_source": "tree_sitter",
                    })
    return symbols


def _extract_javascript(root, source: str) -> list[dict]:
    symbols: list[dict] = []
    for node in _walk(root):
        if node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": "function",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": "class",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "method_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": "method",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "lexical_declaration":
            for child in node.children:
                if child.type == "variable_declarator":
                    name_node = child.child_by_field_name("name")
                    value_node = child.child_by_field_name("value")
                    if name_node and value_node and value_node.type in ("arrow_function", "function"):
                        symbols.append({
                            "type": "function",
                            "name": _node_text(name_node, source),
                            "line_start": node.start_point[0] + 1,
                            "line_end": node.end_point[0] + 1,
                            "parser_source": "tree_sitter",
                        })
        elif node.type == "import_statement":
            src = node.child_by_field_name("source")
            if src:
                name = _node_text(src, source).strip("'\"")
                symbols.append({
                    "type": "import",
                    "name": name,
                    "line_start": node.start_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "export_statement":
            decl = node.child_by_field_name("declaration")
            if decl:
                name_node = decl.child_by_field_name("name")
                if name_node:
                    symbols.append({
                        "type": "export",
                        "name": _node_text(name_node, source),
                        "line_start": node.start_point[0] + 1,
                        "parser_source": "tree_sitter",
                    })
    return symbols


def _extract_typescript(root, source: str) -> list[dict]:
    symbols = _extract_javascript(root, source)
    for node in _walk(root):
        if node.type == "interface_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": "interface",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "type_alias_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": "type",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "enum_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": "enum",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
    return symbols


def _extract_rust(root, source: str) -> list[dict]:
    symbols: list[dict] = []
    for node in _walk(root):
        if node.type == "function_item":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": "function",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "struct_item":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": "struct",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "enum_item":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": "enum",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "trait_item":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": "trait",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "impl_item":
            type_node = node.child_by_field_name("type")
            if type_node:
                symbols.append({
                    "type": "impl",
                    "name": _node_text(type_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "mod_item":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": "module",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "use_declaration":
            arg = node.child_by_field_name("argument")
            if arg:
                symbols.append({
                    "type": "import",
                    "name": _node_text(arg, source),
                    "line_start": node.start_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
    return symbols


def _extract_c(root, source: str) -> list[dict]:
    symbols: list[dict] = []
    for node in _walk(root):
        if node.type == "function_definition":
            declarator = node.child_by_field_name("declarator")
            name_node = _find_identifier(declarator)
            if name_node:
                symbols.append({
                    "type": "function",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "struct_specifier":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": "struct",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "preproc_include":
            path_node = node.child_by_field_name("path")
            if path_node:
                symbols.append({
                    "type": "include",
                    "name": _node_text(path_node, source).strip('<>"'),
                    "line_start": node.start_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
    return symbols


def _extract_cpp(root, source: str) -> list[dict]:
    symbols = _extract_c(root, source)
    for node in _walk(root):
        if node.type == "class_specifier":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": "class",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "namespace_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": "namespace",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
    return symbols


def _extract_go(root, source: str) -> list[dict]:
    symbols: list[dict] = []
    for node in _walk(root):
        if node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": "function",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "method_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": "method",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "type_declaration":
            for child in node.children:
                if child.type == "type_spec":
                    name_node = child.child_by_field_name("name")
                    type_node = child.child_by_field_name("type")
                    if name_node:
                        sym_type = "struct"
                        if type_node and type_node.type == "interface_type":
                            sym_type = "interface"
                        symbols.append({
                            "type": sym_type,
                            "name": _node_text(name_node, source),
                            "line_start": child.start_point[0] + 1,
                            "line_end": child.end_point[0] + 1,
                            "parser_source": "tree_sitter",
                        })
        elif node.type == "import_declaration":
            for child in _walk(node):
                if child.type == "import_spec":
                    path_node = child.child_by_field_name("path")
                    if path_node:
                        symbols.append({
                            "type": "import",
                            "name": _node_text(path_node, source).strip('"'),
                            "line_start": child.start_point[0] + 1,
                            "parser_source": "tree_sitter",
                        })
    return symbols


def _extract_java(root, source: str) -> list[dict]:
    symbols: list[dict] = []
    for node in _walk(root):
        if node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": "class",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "interface_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": "interface",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "method_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": "method",
                    "name": _node_text(name_node, source),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "parser_source": "tree_sitter",
                })
        elif node.type == "import_declaration":
            for child in node.children:
                if child.type == "scoped_identifier":
                    symbols.append({
                        "type": "import",
                        "name": _node_text(child, source),
                        "line_start": node.start_point[0] + 1,
                        "parser_source": "tree_sitter",
                    })
                    break
        elif node.type == "package_declaration":
            for child in node.children:
                if child.type in ("scoped_identifier", "identifier"):
                    symbols.append({
                        "type": "package",
                        "name": _node_text(child, source),
                        "line_start": node.start_point[0] + 1,
                        "parser_source": "tree_sitter",
                    })
                    break
    return symbols


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _get_parent_class(node) -> str | None:
    parent = node.parent
    while parent:
        if parent.type == "class_definition":
            name_node = parent.child_by_field_name("name")
            if name_node:
                return name_node.text.decode("utf-8") if isinstance(name_node.text, bytes) else str(name_node.text)
        parent = parent.parent
    return None


def _find_identifier(node) -> Any | None:
    if node is None:
        return None
    if node.type == "identifier":
        return node
    for child in node.children:
        found = _find_identifier(child)
        if found:
            return found
    return None


_EXTRACTORS = {
    "python": _extract_python,
    "javascript": _extract_javascript,
    "typescript": _extract_typescript,
    "rust": _extract_rust,
    "c": _extract_c,
    "cpp": _extract_cpp,
    "go": _extract_go,
    "java": _extract_java,
}
