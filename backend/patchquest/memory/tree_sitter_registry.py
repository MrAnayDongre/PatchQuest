"""Tree-sitter parser registry — maps file extensions to language parsers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".go": "go",
    ".java": "java",
}

_LANGUAGE_MODULES: dict[str, str] = {
    "python": "tree_sitter_python",
    "javascript": "tree_sitter_javascript",
    "typescript": "tree_sitter_typescript",
    "rust": "tree_sitter_rust",
    "c": "tree_sitter_c",
    "cpp": "tree_sitter_cpp",
    "go": "tree_sitter_go",
    "java": "tree_sitter_java",
}

_parser_cache: dict[str, Any] = {}
_availability_cache: dict[str, bool] = {}


def is_tree_sitter_available() -> bool:
    try:
        import tree_sitter  # noqa: F401
        return True
    except ImportError:
        return False


def language_for_extension(ext: str) -> str | None:
    return _EXTENSION_MAP.get(ext)


def language_for_file(path: str) -> str | None:
    return _EXTENSION_MAP.get(Path(path).suffix)


def supported_languages() -> list[str]:
    if not is_tree_sitter_available():
        return []
    available = []
    for lang in _LANGUAGE_MODULES:
        if is_language_available(lang):
            available.append(lang)
    return available


def is_language_available(language: str) -> bool:
    if language in _availability_cache:
        return _availability_cache[language]

    if not is_tree_sitter_available():
        _availability_cache[language] = False
        return False

    module_name = _LANGUAGE_MODULES.get(language)
    if not module_name:
        _availability_cache[language] = False
        return False

    try:
        _get_language(language)
        _availability_cache[language] = True
        return True
    except Exception:
        _availability_cache[language] = False
        return False


def _get_language(language: str):
    import tree_sitter
    module_name = _LANGUAGE_MODULES[language]

    if language == "typescript":
        import tree_sitter_typescript
        return tree_sitter.Language(tree_sitter_typescript.language_typescript())
    else:
        import importlib
        mod = importlib.import_module(module_name)
        lang_func = getattr(mod, f"language")
        return tree_sitter.Language(lang_func())


def get_parser(language: str):
    if language in _parser_cache:
        return _parser_cache[language]

    import tree_sitter
    lang = _get_language(language)
    parser = tree_sitter.Parser(lang)
    _parser_cache[language] = parser
    return parser


def parse_file(path: str) -> tuple[Any | None, str | None]:
    language = language_for_file(path)
    if not language:
        return None, None

    if not is_language_available(language):
        return None, language

    try:
        content = Path(path).read_bytes()
        parser = get_parser(language)
        tree = parser.parse(content)
        return tree, language
    except Exception as e:
        logger.debug("Tree-sitter parse failed for %s: %s", path, e)
        return None, language


def get_status() -> dict:
    ts_available = is_tree_sitter_available()
    langs = {}
    for lang in _LANGUAGE_MODULES:
        if ts_available:
            langs[lang] = is_language_available(lang)
        else:
            langs[lang] = False
    return {
        "tree_sitter_available": ts_available,
        "languages": langs,
        "supported_extensions": list(_EXTENSION_MAP.keys()),
    }
