#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


def _bootstrap_repo_root() -> Path:
    script_path = Path(__file__).resolve()
    repo_root = script_path.parents[3]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    return repo_root


REPO_ROOT = _bootstrap_repo_root()

from agent.agent_backend.services.ctd_section_service import CTDSectionService  # noqa: E402


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
TEST_DIR = BACKEND_DIR / "test"
RAW_DATA_DIR = BACKEND_DIR / "data" / "raw_data"

INVALID_PATH_CHARS_RE = re.compile(r'[<>:"/\\|?*]+')
SECTION_HEADING_RE = re.compile(
    r"^\s{0,3}(#{1,6})\s*(3\.2\.[a-z](?:\.\d+){1,3})\s*(.*?)\s*$",
    flags=re.IGNORECASE,
)


def _safe_name(value: str) -> str:
    text = str(value or "").strip()
    text = INVALID_PATH_CHARS_RE.sub("_", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or "unknown"


def _node_slug(section_id: str, section_name: str) -> str:
    sid = str(section_id or "").strip()
    name = _safe_name(section_name)
    return f"{sid}_{name}" if sid else name


def _normalize_text(text: str) -> str:
    return str(text or "").replace("\ufeff", "", 1).replace("\r\n", "\n").replace("\r", "\n")


def _normalize_body_lines(lines: Iterable[str]) -> str:
    normalized: List[str] = []
    blank_streak = 0
    for raw_line in lines:
        line = str(raw_line or "").rstrip()
        if not line.strip():
            blank_streak += 1
            if blank_streak <= 1:
                normalized.append("")
            continue
        blank_streak = 0
        normalized.append(line)
    return "\n".join(normalized).strip()


def _load_ctd_catalog() -> Tuple[CTDSectionService, Dict[str, Any], Dict[str, Dict[str, Any]]]:
    service = CTDSectionService(raw_data_dir=str(RAW_DATA_DIR))
    catalog = service.get_catalog()
    section_map = catalog.get("section_map", {}) if isinstance(catalog.get("section_map", {}), dict) else {}
    return service, catalog, section_map


def _parse_markdown_sections(
    markdown_path: Path,
    section_service: CTDSectionService,
    section_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    text = _normalize_text(markdown_path.read_text(encoding="utf-8", errors="ignore"))
    lines = text.split("\n")

    headings: List[Tuple[int, str, str]] = []
    for idx, raw_line in enumerate(lines):
        match = SECTION_HEADING_RE.match(raw_line)
        if not match:
            continue
        _, raw_section_id, raw_heading_title = match.groups()
        section_id = section_service.normalize_section_id(raw_section_id)
        if not section_id:
            continue
        headings.append((idx, section_id, str(raw_heading_title or "").strip()))

    if not headings:
        raise ValueError(f"no CTD headings found in markdown: {markdown_path}")

    parsed: Dict[str, Dict[str, Any]] = {}
    for pos, (line_index, section_id, heading_title) in enumerate(headings):
        next_index = headings[pos + 1][0] if pos + 1 < len(headings) else len(lines)
        content = _normalize_body_lines(lines[line_index + 1 : next_index])
        meta = section_map.get(section_id, {})
        section_name = str(meta.get("section_name", "") or heading_title or section_id).strip() or section_id
        parsed[section_id] = {
            "section_id": section_id,
            "section_name": section_name,
            "title_path": list(meta.get("title_path") or [section_name]),
            "parent_section_id": str(meta.get("parent_section_id", "") or "").strip(),
            "root_section_id": str(meta.get("root_section_id", "") or "").strip(),
            "content": content,
            "children_ids": [],
        }
    return parsed


def _collect_included_nodes(
    parsed_nodes: Dict[str, Dict[str, Any]],
    section_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    included_ids: Set[str] = set()
    branch_roots: Set[str] = set()

    for section_id, node in parsed_nodes.items():
        meta = section_map.get(section_id, {})
        branch_root = str(meta.get("root_section_id", "") or node.get("root_section_id", "") or "").strip()
        if branch_root:
            branch_roots.add(branch_root)
        current = section_id
        while current:
            included_ids.add(current)
            current_meta = section_map.get(current, {})
            parent_id = str(current_meta.get("parent_section_id", "") or "").strip()
            if not parent_id or (branch_root and current == branch_root):
                break
            current = parent_id
        if branch_root:
            included_ids.add(branch_root)

    nodes: Dict[str, Dict[str, Any]] = {}
    for section_id in included_ids:
        meta = section_map.get(section_id, {})
        section_name = str(meta.get("section_name", "") or section_id).strip() or section_id
        nodes[section_id] = {
            "section_id": section_id,
            "section_name": section_name,
            "title_path": list(meta.get("title_path") or [section_name]),
            "parent_section_id": str(meta.get("parent_section_id", "") or "").strip(),
            "root_section_id": str(meta.get("root_section_id", "") or "").strip() or section_id,
            "content": "",
            "children_ids": [],
        }

    for section_id, parsed_node in parsed_nodes.items():
        if section_id not in nodes:
            nodes[section_id] = dict(parsed_node)
        else:
            nodes[section_id]["content"] = str(parsed_node.get("content", "") or "").strip()

    for section_id, node in nodes.items():
        child_ids: List[str] = []
        meta = section_map.get(section_id, {})
        for child in meta.get("children_sections") or []:
            if not isinstance(child, dict):
                continue
            child_id = str(child.get("section_id", "") or "").strip()
            if child_id and child_id in nodes:
                child_ids.append(child_id)
        node["children_ids"] = child_ids

    return nodes


def _root_section_ids(node_index: Dict[str, Dict[str, Any]]) -> Set[str]:
    roots: Set[str] = set()
    for node in node_index.values():
        root_id = str(node.get("root_section_id", "") or "").strip()
        section_id = str(node.get("section_id", "") or "").strip()
        if root_id:
            roots.add(root_id)
        elif section_id:
            roots.add(section_id)
    return roots


def _find_catalog_node(nodes: Iterable[Dict[str, Any]], target_section_id: str) -> Optional[Dict[str, Any]]:
    target = str(target_section_id or "").strip()
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        section_id = str(node.get("section_id", "") or "").strip()
        if section_id == target:
            return node
        matched = _find_catalog_node(node.get("children_sections") or [], target)
        if matched is not None:
            return matched
    return None


def _collect_branch_nodes(
    branch_root: str,
    catalog: Dict[str, Any],
    section_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    normalized_branch_root = str(branch_root or "").strip()
    if not normalized_branch_root or normalized_branch_root not in section_map:
        raise ValueError(f"unknown branch root: {branch_root}")
    branch_node = _find_catalog_node(catalog.get("chapter_structure", []) or [], normalized_branch_root)
    if not isinstance(branch_node, dict):
        raise ValueError(f"branch root not found in catalog tree: {branch_root}")

    nodes: Dict[str, Dict[str, Any]] = {}

    def walk(node: Dict[str, Any]) -> None:
        section_id = str(node.get("section_id", "") or "").strip()
        meta = section_map.get(section_id, {})
        section_name = str(meta.get("section_name", "") or section_id).strip() or section_id
        children = [child for child in (node.get("children_sections") or []) if isinstance(child, dict)]
        nodes[section_id] = {
            "section_id": section_id,
            "section_name": section_name,
            "title_path": list(meta.get("title_path") or [section_name]),
            "parent_section_id": str(meta.get("parent_section_id", "") or "").strip(),
            "root_section_id": str(meta.get("root_section_id", "") or "").strip() or normalized_branch_root,
            "content": "",
            "children_ids": [str(child.get("section_id", "") or "").strip() for child in children if str(child.get("section_id", "") or "").strip()],
        }
        for child in children:
            child_id = str(child.get("section_id", "") or "").strip()
            if child_id:
                walk(child)

    walk(branch_node)
    return nodes


def _build_path_chain(section_id: str, node_index: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    chain: List[Dict[str, Any]] = []
    current = node_index.get(section_id)
    branch_root = str((current or {}).get("root_section_id", "") or "").strip()
    while isinstance(current, dict):
        chain.append(current)
        current_id = str(current.get("section_id", "") or "").strip()
        if branch_root and current_id == branch_root:
            break
        parent_id = str(current.get("parent_section_id", "") or "").strip()
        current = node_index.get(parent_id) if parent_id else None
    chain.reverse()
    return chain


def _directory_segment(node: Dict[str, Any], is_root: bool = False) -> str:
    section_name = str(node.get("section_name", "") or node.get("section_id", "") or "").strip()
    if is_root:
        return _safe_name(section_name)
    return _node_slug(str(node.get("section_id", "") or ""), section_name)


def _render_section_markdown(node: Dict[str, Any], node_index: Dict[str, Dict[str, Any]]) -> str:
    section_id = str(node.get("section_id", "") or "").strip()
    section_name = str(node.get("section_name", "") or section_id).strip() or section_id
    title_path = [str(x or "").strip() for x in (node.get("title_path") or []) if str(x or "").strip()]
    content = str(node.get("content", "") or "").strip()
    child_ids = [cid for cid in (node.get("children_ids") or []) if cid in node_index]

    lines: List[str] = [f"# {section_id} {section_name}", ""]
    if title_path:
        lines.append(f"> CTD 路径: {' / '.join(title_path)}")
        lines.append("")

    if content:
        lines.append(content)
        lines.append("")
    elif child_ids:
        lines.append("本章节无直接正文，内容主要分布于下列子章节。")
        lines.append("")

    if child_ids:
        lines.append("## 子章节")
        lines.append("")
        for child_id in child_ids:
            child = node_index[child_id]
            lines.append(f"- {child['section_id']} {child['section_name']}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def build_ectd_from_markdown(*, markdown_path: Path, output_dir: Path, zip_output_path: Optional[Path] = None) -> Dict[str, Any]:
    section_service, _, section_map = _load_ctd_catalog()
    parsed_nodes = _parse_markdown_sections(markdown_path, section_service, section_map)
    node_index = _collect_included_nodes(parsed_nodes, section_map)

    output_dir.mkdir(parents=True, exist_ok=True)
    written_files: List[str] = []
    written_dirs: Set[str] = set()

    for section_id in sorted(node_index.keys(), key=lambda item: (item.count("."), item)):
        node = node_index[section_id]
        chain = _build_path_chain(section_id, node_index=node_index)
        dir_path = output_dir
        for idx, item in enumerate(chain):
            dir_path = dir_path / _directory_segment(item, is_root=(idx == 0))
            dir_path.mkdir(parents=True, exist_ok=True)
            written_dirs.add(str(dir_path))
        file_path = dir_path / f"{_node_slug(node['section_id'], node['section_name'])}.md"
        file_path.write_text(_render_section_markdown(node, node_index=node_index), encoding="utf-8")
        written_files.append(str(file_path))

    manifest = {
        "source_md": str(markdown_path),
        "output_dir": str(output_dir),
        "branch_roots": sorted(_root_section_ids(node_index)),
        "section_count": len(node_index),
        "written_file_count": len(written_files),
        "written_dir_count": len(written_dirs),
        "files": written_files,
    }
    manifest_path = output_dir / "_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if zip_output_path is not None:
        zip_output_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in output_dir.rglob("*"):
                if path.is_dir():
                    continue
                archive.write(path, path.relative_to(output_dir))
        manifest["zip_output"] = str(zip_output_path)

    return manifest


def build_ectd_directories(*, output_dir: Path, branch_roots: Iterable[str]) -> Dict[str, Any]:
    section_service, catalog, section_map = _load_ctd_catalog()
    normalized_roots: List[str] = []
    for root in branch_roots or []:
        normalized = section_service.normalize_section_id(root)
        if normalized:
            normalized_roots.append(normalized)
    if not normalized_roots:
        raise ValueError("at least one valid branch_root is required")

    output_dir.mkdir(parents=True, exist_ok=True)
    written_dirs: Set[str] = set()
    all_nodes: Dict[str, Dict[str, Any]] = {}
    for branch_root in normalized_roots:
        branch_nodes = _collect_branch_nodes(branch_root, catalog, section_map)
        all_nodes.update(branch_nodes)
        for section_id in sorted(branch_nodes.keys(), key=lambda item: (item.count("."), item)):
            node = branch_nodes[section_id]
            chain = _build_path_chain(section_id, node_index=branch_nodes)
            dir_path = output_dir
            for idx, item in enumerate(chain):
                dir_path = dir_path / _directory_segment(item, is_root=(idx == 0))
                dir_path.mkdir(parents=True, exist_ok=True)
                written_dirs.add(str(dir_path))

    manifest = {
        "output_dir": str(output_dir),
        "branch_roots": normalized_roots,
        "section_count": len(all_nodes),
        "written_dir_count": len(written_dirs),
        "directories": sorted(written_dirs),
    }
    manifest_path = output_dir / "_directory_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Split a CTD markdown file into an eCTD-style directory tree.")
    parser.add_argument(
        "--input-md",
        default=str(TEST_DIR / "sub_e018f27f52ab4bba_CTD11_parsed.md"),
        help="Source markdown file for the CTD document.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(TEST_DIR / "ectd"),
        help="Target eCTD directory.",
    )
    parser.add_argument(
        "--zip-output",
        default=str(TEST_DIR / "sub_e018f27f52ab4bba_CTD11_ectd.zip"),
        help="Optional zip output path.",
    )
    args = parser.parse_args()

    manifest = build_ectd_from_markdown(
        markdown_path=Path(args.input_md),
        output_dir=Path(args.output_dir),
        zip_output_path=Path(args.zip_output) if str(args.zip_output or "").strip() else None,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
