import os
from typing import Any, Callable, Dict, List, Optional


class ParserManager:
    _registry: Dict[str, Callable[[str], Any]] = {}

    @classmethod
    def register_parser(cls, ext: str, parser_callable: Callable[[str], Any]) -> None:
        normalized_ext = ext.lower().lstrip(".")
        cls._registry[normalized_ext] = parser_callable

    @classmethod
    def list_supported_extensions(cls) -> List[str]:
        return sorted(cls._registry.keys())

    @classmethod
    def is_supported(cls, ext: str) -> bool:
        normalized = ext.lower().lstrip(".")
        return normalized in cls._registry

    @classmethod
    def get_parser(cls, ext: str) -> Callable[[str], Any]:
        ext = ext.lower().lstrip(".")
        if ext not in cls._registry:
            supported = ", ".join(cls.list_supported_extensions())
            raise ValueError(f"No parser registered for extension: {ext} (supported: {supported})")
        return cls._registry[ext]

    @classmethod
    def parse(cls, file_path: str, ext_hint: Optional[str] = None) -> List[dict]:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"file not found: {file_path}")
        ext = os.path.splitext(file_path)[1].lstrip(".").lower()
        if not ext and ext_hint:
            ext = str(ext_hint).lstrip(".").lower()
        parser = cls.get_parser(ext)
        result = parser(file_path)
        if not isinstance(result, list):
            raise TypeError("Parser must return list[dict]")
        return result
