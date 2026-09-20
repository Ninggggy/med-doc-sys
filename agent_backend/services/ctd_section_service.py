import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional


class CTDSectionService:
    """Provide chapter metadata with both tree and leaf-node views.

    The service uses the canonical CTD catalog snapshots under
    `data/base_data/` as the primary source. Existing field names are
    preserved so current upload, preview and pre-review flows remain stable.
    """

    BASE_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "base_data"
    TREE_CATALOG_FILE = BASE_DATA_DIR / "ctd_tree_catalog.json"
    LAYER_CATALOG_FILE = BASE_DATA_DIR / "ctd_layer_catalog.json"
    LAYER_CATALOG_FALLBACK_FILE = BASE_DATA_DIR / "ctd_layer_catalog copy.json"
    VERSION2_FILE = Path(__file__).resolve().parents[1] / "markdown" / "version2.md"
    CHEMISTRY_BRANCH_ROOTS = {"3.2.s", "3.2.p", "3.2.a", "3.2.r"}
    CANONICAL_SECTION_NAME_OVERRIDES = {
        "3.2.p": "制剂",
        "3.2.p.6": "对照品/标准品",
        "3.2.s": "原料药",
        "3.2.s.5": "对照品/标准品",
        "3.2.r": "区域性信息",
    }
    MODULE_ROOTS = {
        "1": "模块一 行政文件和药品信息",
        "2": "模块二 通用技术文档总结",
        "3": "模块三 质量（药学）",
        "4": "模块四 非临床试验报告",
        "5": "模块五 临床研究报告",
    }
    SUPPLEMENT_ROOT = {
        "section_id": "supplement",
        "section_name": "药品补充申请",
        "children_sections": [
            {"section_id": "supplement.application_info", "section_name": "申请信息", "children_sections": []},
            {"section_id": "supplement.catalog", "section_name": "目录", "children_sections": []},
            {"section_id": "supplement.approval_certificate", "section_name": "1 药品批准证明文件", "children_sections": []},
            {"section_id": "supplement.supporting_documents", "section_name": "2 证明性文件", "children_sections": []},
            {"section_id": "supplement.inspection", "section_name": "3 检查检验相关信息", "children_sections": []},
            {"section_id": "supplement.quality", "section_name": "4 质量标准及说明书等", "children_sections": []},
            {"section_id": "supplement.pharmacy", "section_name": "5 药学研究资料", "children_sections": []},
            {"section_id": "supplement.nonclinical", "section_name": "6 药理毒理研究资料", "children_sections": []},
            {"section_id": "supplement.clinical", "section_name": "7 临床研究资料", "children_sections": []},
            {"section_id": "supplement.other", "section_name": "8 其他", "children_sections": []},
            {"section_id": "supplement.generic_name", "section_name": "通用名称核准资料", "children_sections": []},
        ],
    }
    CHEMISTRY_NODE_PRIORITY = {
        "3.2.s": 0,
        "3.2.p": 1,
        "3.2.a": 2,
        "3.2.r": 3,
    }

    def __init__(self, raw_data_dir: str):
        self.raw_data_dir = Path(raw_data_dir)
        self._catalog_cache: Optional[Dict[str, Any]] = None
        self._catalog_cache_signature: str = ""

    @staticmethod
    def normalize_section_id(section_id: Any) -> str:
        raw = str(section_id or "").strip().strip(".")
        if not raw:
            return ""
        parts = [part.strip() for part in raw.split(".") if str(part).strip()]
        normalized: List[str] = []
        for part in parts:
            normalized.append(part.lower() if re.fullmatch(r"[A-Za-z]+", part) else part)
        return ".".join(normalized)

    @classmethod
    def _canonical_section_name(cls, section_id: str, section_name: Any) -> str:
        sid = cls.normalize_section_id(section_id)
        override = cls.CANONICAL_SECTION_NAME_OVERRIDES.get(sid, "")
        if override:
            return override
        return str(section_name or "").strip() or sid

    @staticmethod
    def _merge_string_lists(*values: List[Any]) -> List[str]:
        out: List[str] = []
        seen = set()
        for items in values:
            for item in items or []:
                text = str(item or "").strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                out.append(text)
        return out

    def _merge_outline_children(
        self,
        base_children: List[Dict[str, Any]],
        incoming_children: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        ordered_ids: List[str] = []
        for child in list(base_children or []) + list(incoming_children or []):
            if not isinstance(child, dict):
                continue
            sid = self.normalize_section_id(child.get("section_id"))
            if not sid:
                continue
            child = {
                **child,
                "section_id": sid,
                "section_name": self._canonical_section_name(sid, child.get("section_name")),
                "children_sections": list(child.get("children_sections") or []),
                "concern_points": list(child.get("concern_points") or child.get("points") or []),
            }
            if sid not in merged:
                merged[sid] = child
                ordered_ids.append(sid)
                continue
            existing = merged[sid]
            merged[sid] = {
                **existing,
                "section_id": sid,
                "section_name": self._canonical_section_name(
                    sid,
                    child.get("section_name") or existing.get("section_name"),
                ),
                "concern_points": self._merge_string_lists(
                    existing.get("concern_points") or existing.get("points") or [],
                    child.get("concern_points") or child.get("points") or [],
                ),
                "children_sections": self._merge_outline_children(
                    existing.get("children_sections") or [],
                    child.get("children_sections") or [],
                ),
            }
        return self._sort_outline_nodes([merged[sid] for sid in ordered_ids])

    def _canonicalize_outline_nodes(self, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        ordered_ids: List[str] = []
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            sid = self.normalize_section_id(node.get("section_id"))
            if not sid:
                continue
            normalized_node = {
                **node,
                "section_id": sid,
                "section_name": self._canonical_section_name(sid, node.get("section_name")),
                "children_sections": self._canonicalize_outline_nodes(node.get("children_sections") or []),
                "concern_points": list(node.get("concern_points") or node.get("points") or []),
            }
            if sid not in merged:
                merged[sid] = normalized_node
                ordered_ids.append(sid)
                continue
            existing = merged[sid]
            merged[sid] = {
                **existing,
                "section_id": sid,
                "section_name": self._canonical_section_name(
                    sid,
                    normalized_node.get("section_name") or existing.get("section_name"),
                ),
                "concern_points": self._merge_string_lists(
                    existing.get("concern_points") or existing.get("points") or [],
                    normalized_node.get("concern_points") or normalized_node.get("points") or [],
                ),
                "children_sections": self._merge_outline_children(
                    existing.get("children_sections") or [],
                    normalized_node.get("children_sections") or [],
                ),
            }
        return self._sort_outline_nodes([merged[sid] for sid in ordered_ids])

    @classmethod
    def _outline_sort_key(cls, node: Dict[str, Any]) -> tuple:
        sid = cls.normalize_section_id((node or {}).get("section_id"))
        if sid in cls.CHEMISTRY_NODE_PRIORITY:
            return (0, cls.CHEMISTRY_NODE_PRIORITY[sid], sid)
        return (1, sid)

    @classmethod
    def _sort_outline_nodes(cls, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not isinstance(nodes, list) or len(nodes) < 2:
            return list(nodes or [])
        ids = {
            cls.normalize_section_id((item or {}).get("section_id"))
            for item in nodes
            if isinstance(item, dict)
        }
        chemistry_ids = set(cls.CHEMISTRY_NODE_PRIORITY.keys())
        if not ids.intersection(chemistry_ids):
            return list(nodes)
        return sorted(list(nodes), key=cls._outline_sort_key)

    def _load_version2_outline(self) -> List[Dict[str, Any]]:
        path = self.VERSION2_FILE
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        match = re.search(r"(\[\s*\{.*\}\s*\])", text, flags=re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(1))
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def _load_base_data_outline(self) -> List[Dict[str, Any]]:
        path = self.TREE_CATALOG_FILE
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def _load_single_layer_catalog(self, path: Path) -> List[List[Dict[str, Any]]]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def _load_layer_catalog(self) -> List[List[Dict[str, Any]]]:
        primary = self._load_single_layer_catalog(self.LAYER_CATALOG_FILE)
        if primary:
            return primary
        fallback = self._load_single_layer_catalog(self.LAYER_CATALOG_FALLBACK_FILE)
        return fallback

    @staticmethod
    def _file_signature(path: Path) -> str:
        try:
            if not path.exists():
                return f"{path.name}:missing"
            stat = path.stat()
            return f"{path.name}:{int(stat.st_mtime_ns)}:{int(stat.st_size)}"
        except Exception:
            return f"{path.name}:unknown"

    def _catalog_source_signature(self) -> str:
        return "|".join(
            [
                self._file_signature(self.TREE_CATALOG_FILE),
                self._file_signature(self.LAYER_CATALOG_FILE),
                self._file_signature(self.LAYER_CATALOG_FALLBACK_FILE),
                self._file_signature(self.VERSION2_FILE),
            ]
        )

    def _resolve_catalog_sources(self) -> Dict[str, Any]:
        tree_exists = self.TREE_CATALOG_FILE.exists()
        primary_exists = self.LAYER_CATALOG_FILE.exists()
        fallback_exists = self.LAYER_CATALOG_FALLBACK_FILE.exists()
        primary = self._load_single_layer_catalog(self.LAYER_CATALOG_FILE) if primary_exists else []
        fallback = self._load_single_layer_catalog(self.LAYER_CATALOG_FALLBACK_FILE) if fallback_exists else []
        layer_source = "primary"
        layer_source_path = str(self.LAYER_CATALOG_FILE)
        if not primary and fallback:
            layer_source = "fallback"
            layer_source_path = str(self.LAYER_CATALOG_FALLBACK_FILE)
        return {
            "tree_source": str(self.TREE_CATALOG_FILE if tree_exists else self.VERSION2_FILE),
            "tree_source_mode": "primary" if tree_exists else "version2_fallback",
            "layer_source": layer_source_path,
            "layer_source_mode": layer_source,
            "primary_layer_available": bool(primary),
            "fallback_layer_available": bool(fallback),
        }

    def _flatten_layer_catalog_rows(self, layer_catalog: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        for layer in layer_catalog or []:
            if not isinstance(layer, list):
                continue
            for item in layer:
                if not isinstance(item, dict):
                    continue
                section_id = self.normalize_section_id(item.get("section_id"))
                if not section_id:
                    continue
                section_name = self._canonical_section_name(section_id, item.get("section_name"))
                current = merged.get(section_id, {})
                merged[section_id] = {
                    **current,
                    **item,
                    "section_id": section_id,
                    "section_name": section_name or str(current.get("section_name", "") or section_id).strip() or section_id,
                }
        return sorted(
            merged.values(),
            key=lambda item: (len(str(item.get("section_id", "") or "").split(".")), str(item.get("section_id", "") or "")),
        )

    def _build_outline_from_layer_rows(self, layer_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not layer_rows:
            return []

        node_map: Dict[str, Dict[str, Any]] = {}
        roots: List[str] = []

        def ensure_node(section_id: str, section_name: str = "") -> Dict[str, Any]:
            normalized_section_id = self.normalize_section_id(section_id)
            if not normalized_section_id:
                return {}
            node = node_map.get(normalized_section_id)
            if node is None:
                node = {
                    "section_id": normalized_section_id,
                    "section_name": self._canonical_section_name(normalized_section_id, section_name or normalized_section_id),
                    "children_sections": [],
                }
                node_map[normalized_section_id] = node
            elif section_name and (
                not str(node.get("section_name", "") or "").strip()
                or str(node.get("section_name", "") or "").strip() == normalized_section_id
            ):
                node["section_name"] = self._canonical_section_name(normalized_section_id, section_name)
            return node

        for item in layer_rows:
            if not isinstance(item, dict):
                continue
            section_id = self.normalize_section_id(item.get("section_id"))
            if not section_id:
                continue
            ensure_node(section_id, str(item.get("section_name", "") or "").strip())
            parts = [part for part in section_id.split(".") if part]
            for size in range(1, len(parts)):
                ancestor_id = ".".join(parts[:size])
                ensure_node(ancestor_id, "")

        for section_id in sorted(node_map.keys(), key=lambda value: (len(value.split(".")), value)):
            node = node_map[section_id]
            parts = [part for part in section_id.split(".") if part]
            parent_id = ".".join(parts[:-1]) if len(parts) > 1 else ""
            if parent_id and parent_id in node_map:
                parent = node_map[parent_id]
                existing_ids = {
                    self.normalize_section_id(child.get("section_id"))
                    for child in parent.get("children_sections", [])
                    if isinstance(child, dict)
                }
                if section_id not in existing_ids:
                    parent.setdefault("children_sections", []).append(node)
                continue
            if section_id not in roots:
                roots.append(section_id)

        return [node_map[section_id] for section_id in roots if section_id in node_map]

    @classmethod
    def _group_outline_by_module(cls, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        groups: Dict[str, List[Dict[str, Any]]] = {key: [] for key in cls.MODULE_ROOTS.keys()}
        passthrough: List[Dict[str, Any]] = []
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            section_id = str(node.get("section_id", "") or "").strip()
            module_id = section_id.split(".", 1)[0] if section_id else ""
            if module_id in groups:
                groups[module_id].append(node)
            else:
                passthrough.append(node)
        out: List[Dict[str, Any]] = []
        for module_id, module_name in cls.MODULE_ROOTS.items():
            children = groups.get(module_id) or []
            if not children:
                continue
            out.append(
                {
                    "section_id": module_id,
                    "section_name": module_name,
                    "children_sections": children,
                }
            )
        out.extend(passthrough)
        return out

    @classmethod
    def _resolve_node_root(cls, section_id: str, inherited_root: str) -> str:
        value = cls.normalize_section_id(section_id)
        root = cls.normalize_section_id(inherited_root)
        if not value:
            return root
        if value in cls.CHEMISTRY_BRANCH_ROOTS:
            return value
        if root in cls.CHEMISTRY_BRANCH_ROOTS:
            return root
        return root or value

    def _normalize_nodes(
        self,
        nodes: List[Dict[str, Any]],
        *,
        parent_path: Optional[List[str]] = None,
        inherited_root: str = "",
        parent_section_id: str = "",
    ) -> List[Dict[str, Any]]:
        parent_path = list(parent_path or [])
        out: List[Dict[str, Any]] = []
        for order, node in enumerate(nodes or [], start=1):
            if not isinstance(node, dict):
                continue
            section_id = self.normalize_section_id(node.get("section_id"))
            if not section_id:
                continue
            section_name = self._canonical_section_name(section_id, node.get("section_name"))
            concern_points = [
                str(item).strip()
                for item in (node.get("concern_points") or node.get("points") or [])
                if str(item).strip()
            ]
            title_path = parent_path + [section_name]
            root_section_id = self._resolve_node_root(section_id, inherited_root)
            children = self._normalize_nodes(
                node.get("children_sections") or [],
                parent_path=title_path,
                inherited_root=root_section_id,
                parent_section_id=section_id,
            )
            out.append(
                {
                    "section_id": section_id,
                    "section_code": section_id,
                    "section_name": section_name,
                    "title_path": title_path,
                    "concern_points": concern_points,
                    "root_section_id": root_section_id,
                    "parent_section_id": parent_section_id,
                    "node_level": len(section_id.split(".")) - 1,
                    "sort_order": order,
                    "is_leaf": not bool(children),
                    "children_sections": children,
                }
            )
        return out

    def flatten_nodes(self, nodes: List[Dict[str, Any]], leaf_only: bool = False) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            children = [child for child in (node.get("children_sections") or []) if isinstance(child, dict)]
            item = {
                "section_id": str(node.get("section_id", "") or "").strip(),
                "section_code": str(node.get("section_code", "") or node.get("section_id", "") or "").strip(),
                "section_name": str(node.get("section_name", "") or node.get("section_id", "") or "").strip(),
                "title_path": list(node.get("title_path") or []),
                "concern_points": list(node.get("concern_points") or []),
                "root_section_id": str(node.get("root_section_id", "") or "").strip(),
                "parent_section_id": str(node.get("parent_section_id", "") or "").strip(),
                "node_level": int(node.get("node_level", 0) or 0),
                "sort_order": int(node.get("sort_order", 0) or 0),
                "is_leaf": bool(node.get("is_leaf", False)),
                "children_sections": [],
            }
            if not leaf_only or item["is_leaf"]:
                out.append(item)
            out.extend(self.flatten_nodes(children, leaf_only=leaf_only))
        return out

    def _build_roots(self) -> List[Dict[str, Any]]:
        base_data_outline = self._load_base_data_outline()
        outline = base_data_outline or self._load_version2_outline()
        layer_outline = self._build_outline_from_layer_rows(
            self._flatten_layer_catalog_rows(self._load_layer_catalog())
        )
        if layer_outline:
            outline = self._merge_outline_children(outline, layer_outline) if outline else layer_outline
        if outline:
            if not base_data_outline:
                outline = self._group_outline_by_module(outline)
            if not any(self.normalize_section_id(item.get("section_id")) == "supplement" for item in outline if isinstance(item, dict)):
                outline.append(deepcopy(self.SUPPLEMENT_ROOT))
            outline = self._canonicalize_outline_nodes(outline)
        return self._normalize_nodes(outline, parent_path=[], inherited_root="", parent_section_id="")

    def _build_catalog(self) -> Dict[str, Any]:
        chapter_structure = self._build_roots()
        layer_catalog = self._load_layer_catalog()
        source_meta = self._resolve_catalog_sources()
        all_sections = self.flatten_nodes(chapter_structure, leaf_only=False)
        flat_sections = self.flatten_nodes(chapter_structure, leaf_only=True)
        return {
            "chapter_structure": chapter_structure,
            "layer_catalog": layer_catalog,
            "all_sections": all_sections,
            "flat_sections": flat_sections,
            "section_map": {item["section_id"]: item for item in all_sections if item.get("section_id")},
            "leaf_section_map": {item["section_id"]: item for item in flat_sections if item.get("section_id")},
            "catalog_meta": source_meta,
        }

    def refresh_catalog(self) -> Dict[str, Any]:
        self._catalog_cache_signature = self._catalog_source_signature()
        self._catalog_cache = self._build_catalog()
        return deepcopy(self._catalog_cache)

    def get_catalog(self, force_refresh: bool = False) -> Dict[str, Any]:
        current_signature = self._catalog_source_signature()
        if force_refresh or self._catalog_cache is None or self._catalog_cache_signature != current_signature:
            self._catalog_cache = self._build_catalog()
            self._catalog_cache_signature = current_signature
        return deepcopy(self._catalog_cache)

    def get_section(self, section_id: str, leaf_only: bool = False) -> Optional[Dict[str, Any]]:
        section_key = self.normalize_section_id(section_id)
        if not section_key:
            return None
        catalog = self.get_catalog()
        if leaf_only:
            return catalog.get("leaf_section_map", {}).get(section_key)
        return catalog.get("section_map", {}).get(section_key)

    def list_section_ids(self, leaf_only: bool = False) -> List[str]:
        catalog = self.get_catalog()
        source = catalog.get("flat_sections" if leaf_only else "all_sections", [])
        return [str(item.get("section_id", "")).strip() for item in source if str(item.get("section_id", "")).strip()]

    def list_root_sections(self) -> List[Dict[str, Any]]:
        catalog = self.get_catalog()
        return deepcopy(catalog.get("chapter_structure", []))

    def default_branch_root(self) -> str:
        catalog = self.get_catalog()
        section_map = catalog.get("section_map", {}) if isinstance(catalog, dict) else {}
        if "3" in section_map:
            return "3"
        roots = catalog.get("chapter_structure", []) if isinstance(catalog, dict) else []
        for root in roots:
            root_id = str((root or {}).get("section_id", "") or "").strip()
            if root_id and root_id != "0":
                return root_id
        if roots:
            return str((roots[0] or {}).get("section_id", "") or "").strip()
        return ""

    def build_tree_from_leaf_sections(self, leaf_sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        catalog = self.get_catalog()
        section_map = catalog.get("section_map", {})
        include_ids = set()
        leaf_map = {}
        for item in leaf_sections or []:
            if not isinstance(item, dict):
                continue
            sid = self.normalize_section_id(item.get("section_id"))
            if not sid:
                continue
            leaf_map[sid] = item
            current = sid
            while current:
                include_ids.add(current)
                parent = str(section_map.get(current, {}).get("parent_section_id", "") or "").strip()
                current = parent

        def clone(node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            sid = str(node.get("section_id", "") or "").strip()
            if sid not in include_ids:
                return None
            children = []
            for child in node.get("children_sections") or []:
                copied = clone(child)
                if copied is not None:
                    children.append(copied)
            merged_leaf = leaf_map.get(sid, {})
            return {
                "section_id": sid,
                "section_code": str(node.get("section_code", "") or sid).strip() or sid,
                "section_name": str(node.get("section_name", "") or sid).strip() or sid,
                "title_path": list(node.get("title_path") or []),
                "concern_points": list(merged_leaf.get("concern_points") or node.get("concern_points") or []),
                "root_section_id": str(merged_leaf.get("root_section_id") or node.get("root_section_id", "") or "").strip(),
                "parent_section_id": str(node.get("parent_section_id", "") or "").strip(),
                "node_level": int(node.get("node_level", 0) or 0),
                "sort_order": int(node.get("sort_order", 0) or 0),
                "is_leaf": bool(node.get("is_leaf", False)),
                "children_sections": children,
            }

        out: List[Dict[str, Any]] = []
        for root in catalog.get("chapter_structure", []):
            copied = clone(root)
            if copied is not None:
                out.append(copied)
        return out

    @staticmethod
    def _contains_section_token(text: str, section_id: str) -> bool:
        if not text or not section_id:
            return False
        pattern = re.compile(rf"(?<![A-Za-z0-9.]){re.escape(section_id)}(?![A-Za-z0-9.])", flags=re.IGNORECASE)
        return bool(pattern.search(text))

    def infer_section_id_from_path(self, raw_path: str, leaf_only: bool = False) -> str:
        path = str(raw_path or "").replace("\\", "/").strip()
        if not path:
            return ""
        catalog = self.get_catalog()
        section_map = catalog.get("leaf_section_map" if leaf_only else "section_map", {})
        normalized = path.lower()

        candidates = sorted(
            section_map.values(),
            key=lambda item: len(str(item.get("section_id", "") or "")),
            reverse=True,
        )
        for item in candidates:
            section_id = str(item.get("section_id", "") or "").strip()
            if section_id and self._contains_section_token(path, section_id):
                return section_id

        candidates = sorted(
            section_map.values(),
            key=lambda item: len(str(item.get("section_name", "") or "")),
            reverse=True,
        )
        for item in candidates:
            section_id = str(item.get("section_id", "") or "").strip()
            section_name = str(item.get("section_name", "") or "").strip()
            if section_id and section_name and section_name.lower() in normalized:
                return section_id
        return ""
