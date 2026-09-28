#!/usr/bin/env python3
"""校验 Docker 三文件交付包，任何错误均以非零状态退出。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from source_digest import calculate_delivery_source_digests
except ImportError:  # pragma: no cover - 作为 deploy 包导入时使用
    from deploy.source_digest import calculate_delivery_source_digests


ENTRYPOINT_PATH = "usr/local/bin/backend-entrypoint.sh"
REQUIRED_SERVICES = {
    "mysql",
    "ocr-service",
    "etcd",
    "minio",
    "milvus",
    "agent-backend",
    "agent-frontend",
}
FORBIDDEN_IMAGE_FRAGMENTS = {
    "ocr-service-tesseract-ocr",
    "ocr_service-tesseract-ocr",
}
SOURCE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class SavedImage:
    tag: str
    os_name: str
    architecture: str
    config: dict[str, Any]
    layers: tuple[str, ...]


class Validator:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.notes: list[str] = []

    def ok(self, message: str) -> None:
        self.notes.append(f"[通过] {message}")

    def fail(self, message: str) -> None:
        self.errors.append(f"[失败] {message}")

    def require(self, condition: bool, ok_message: str, fail_message: str) -> None:
        if condition:
            self.ok(ok_message)
        else:
            self.fail(fail_message)

    def print_report(self) -> None:
        for message in self.notes:
            print(message)
        for message in self.errors:
            print(message, file=sys.stderr)
        if self.errors:
            print(f"\n校验未通过：{len(self.errors)} 项错误。", file=sys.stderr)
        else:
            print("\n校验通过：静态交付门禁全部满足。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="核对三文件交付包的版本、Compose 镜像、Docker save 清单、架构和入口权限。"
    )
    parser.add_argument("bundle_dir", type=Path, help="只包含三个交付文件的目录")
    parser.add_argument("--version", required=True, help="版本号，例如 v12")
    parser.add_argument(
        "--mode",
        required=True,
        choices=("upgrade", "offline", "online"),
        help="upgrade=增量升级，offline=全量离线，online=全量在线",
    )
    parser.add_argument(
        "--external-image",
        action="append",
        default=[],
        metavar="IMAGE",
        help="未装入两个 tar 的镜像；增量模式表示目标机预装，在线模式表示允许拉取",
    )
    parser.add_argument(
        "--allow-legacy-latest",
        action="append",
        default=[],
        metavar="IMAGE",
        help="仅为兼容既有增量环境而允许的完整 latest 镜像名",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="供 docker compose config 使用的环境文件；不提供时使用 Compose 默认行为",
    )
    parser.add_argument(
        "--skip-sha256",
        action="store_true",
        help="仅供开发阶段快速检查；正式交付禁止使用",
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="用于核对镜像 source-digest 的 agent 源码根目录",
    )
    parser.add_argument(
        "--expected-backend-source-digest",
        help="可选；核对历史冻结源码时显式传入，默认现场计算 --source-root",
    )
    parser.add_argument(
        "--expected-frontend-source-digest",
        help="可选；核对历史冻结源码时显式传入，默认现场计算 --source-root",
    )
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json_member(archive: tarfile.TarFile, name: str) -> Any:
    member = archive.getmember(name)
    stream = archive.extractfile(member)
    if stream is None:
        raise ValueError(f"归档成员不可读：{name}")
    return json.load(stream)


def inspect_docker_save(path: Path) -> tuple[dict[str, SavedImage], list[dict[str, Any]]]:
    images: dict[str, SavedImage] = {}
    with tarfile.open(path, mode="r:*") as archive:
        # getmembers 会遍历完整外层 tar，可发现被截断或目录损坏的交付文件。
        archive.getmembers()
        manifest = read_json_member(archive, "manifest.json")
        if not isinstance(manifest, list) or not manifest:
            raise ValueError("manifest.json 为空或格式错误")
        for item in manifest:
            config = read_json_member(archive, item["Config"])
            for tag in item.get("RepoTags") or []:
                if tag in images:
                    raise ValueError(f"RepoTag 重复：{tag}")
                images[tag] = SavedImage(
                    tag=tag,
                    os_name=str(config.get("os") or ""),
                    architecture=str(config.get("architecture") or ""),
                    config=dict(config.get("config") or {}),
                    layers=tuple(item.get("Layers") or ()),
                )
    return images, manifest


def normalize_tar_name(name: str) -> str:
    while name.startswith("./"):
        name = name[2:]
    return name.lstrip("/")


def inspect_entrypoint_in_layers(
    docker_save_path: Path,
    manifest: list[dict[str, Any]],
    backend_tag: str,
) -> tuple[int, bytes] | None:
    selected = next(
        (item for item in manifest if backend_tag in (item.get("RepoTags") or [])),
        None,
    )
    if selected is None:
        return None

    target_parent = str(Path(ENTRYPOINT_PATH).parent)
    target_name = Path(ENTRYPOINT_PATH).name
    whiteout = f"{target_parent}/.wh.{target_name}"
    opaque_whiteout = f"{target_parent}/.wh..wh..opq"

    with tarfile.open(docker_save_path, mode="r:*") as outer:
        for layer_name in reversed(selected.get("Layers") or []):
            layer_stream = outer.extractfile(layer_name)
            if layer_stream is None:
                raise ValueError(f"镜像层不可读：{layer_name}")
            found: tuple[int, bytes] | None = None
            target_removed = False
            with tarfile.open(fileobj=layer_stream, mode="r|*") as layer:
                for member in layer:
                    normalized = normalize_tar_name(member.name)
                    if normalized in {whiteout, opaque_whiteout}:
                        target_removed = True
                    if normalized != ENTRYPOINT_PATH:
                        continue
                    if not member.isfile():
                        target_removed = True
                        continue
                    content = layer.extractfile(member)
                    first_bytes = content.read(256) if content is not None else b""
                    found = (member.mode, first_bytes)
            if found is not None:
                return found
            if target_removed:
                return None
    return None


def inspect_final_regular_file(
    docker_save_path: Path,
    manifest: list[dict[str, Any]],
    image_tag: str,
    target_path: str,
    *,
    max_bytes: int = 128 * 1024,
) -> bytes | None:
    """从最上层向下查找镜像中最终生效的普通文件。"""
    selected = next(
        (item for item in manifest if image_tag in (item.get("RepoTags") or [])),
        None,
    )
    if selected is None:
        return None

    target_parent = str(Path(target_path).parent)
    target_name = Path(target_path).name
    whiteout = f"{target_parent}/.wh.{target_name}"
    opaque_whiteout = f"{target_parent}/.wh..wh..opq"
    with tarfile.open(docker_save_path, mode="r:*") as outer:
        for layer_name in reversed(selected.get("Layers") or []):
            layer_stream = outer.extractfile(layer_name)
            if layer_stream is None:
                raise ValueError(f"镜像层不可读：{layer_name}")
            with tarfile.open(fileobj=layer_stream, mode="r|*") as layer:
                for member in layer:
                    normalized = normalize_tar_name(member.name)
                    if normalized in {whiteout, opaque_whiteout}:
                        return None
                    if normalized != target_path:
                        continue
                    if not member.isfile():
                        return None
                    stream = layer.extractfile(member)
                    return stream.read(max_bytes) if stream is not None else b""
    return None


def inspect_final_image_paths(
    docker_save_path: Path,
    manifest: list[dict[str, Any]],
    image_tag: str,
) -> set[str]:
    """按 Docker overlay 规则重建镜像最终文件路径集。

    不解压文件内容，仅用于发现增量镜像中未被清理的旧静态资源。
    """
    selected = next(
        (item for item in manifest if image_tag in (item.get("RepoTags") or [])),
        None,
    )
    if selected is None:
        return set()

    paths: set[str] = set()
    with tarfile.open(docker_save_path, mode="r:*") as outer:
        for layer_name in selected.get("Layers") or []:
            layer_stream = outer.extractfile(layer_name)
            if layer_stream is None:
                raise ValueError(f"镜像层不可读：{layer_name}")
            with tarfile.open(fileobj=layer_stream, mode="r|*") as layer:
                for member in layer:
                    normalized = normalize_tar_name(member.name).rstrip("/")
                    if not normalized:
                        continue
                    path = Path(normalized)
                    basename = path.name
                    parent = path.parent.as_posix()
                    if basename == ".wh..wh..opq":
                        prefix = f"{parent}/" if parent != "." else ""
                        paths = {item for item in paths if not item.startswith(prefix)}
                        continue
                    if basename.startswith(".wh."):
                        target_name = basename[len(".wh.") :]
                        target = f"{parent}/{target_name}" if parent != "." else target_name
                        target_prefix = f"{target}/"
                        paths = {
                            item
                            for item in paths
                            if item != target and not item.startswith(target_prefix)
                        }
                        continue
                    if member.isfile() or member.issym() or member.islnk():
                        paths.add(normalized)
    return paths


def strip_yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_compose_services(text: str) -> dict[str, dict[str, Any]]:
    services: dict[str, dict[str, Any]] = {}
    in_services = False
    current: str | None = None
    current_section: str | None = None
    current_dependency: str | None = None

    for raw_line in text.splitlines():
        content = raw_line.lstrip(" ")
        if not content or content.startswith("#"):
            continue
        indent = len(raw_line) - len(content)
        if indent == 0:
            in_services = content.rstrip() == "services:"
            current = None
            current_section = None
            current_dependency = None
            continue
        if not in_services:
            continue
        service_match = re.fullmatch(r" {2}([A-Za-z0-9_.-]+):\s*", raw_line)
        if service_match:
            current = service_match.group(1)
            services[current] = {
                "images": [],
                "build": False,
                "entrypoint": "",
                "environment": {},
                "depends_on": {},
            }
            current_section = None
            current_dependency = None
            continue
        if current is None or indent < 4:
            continue
        if indent == 4:
            if content.rstrip() == "environment:":
                current_section = "environment"
            elif content.rstrip() == "depends_on:":
                current_section = "depends_on"
            else:
                current_section = None
            current_dependency = None
        image_match = re.match(r"^\s{4}image:\s*(\S.*?)\s*$", raw_line)
        if image_match:
            value = image_match.group(1).split(" #", 1)[0]
            services[current]["images"].append(strip_yaml_scalar(value))
        if re.match(r"^\s{4}build\s*:", raw_line):
            services[current]["build"] = True
        entrypoint_match = re.match(r"^\s{4}entrypoint:\s*(.*?)\s*$", raw_line)
        if entrypoint_match:
            services[current]["entrypoint"] = entrypoint_match.group(1)
        if current_section == "environment" and indent == 6:
            environment_match = re.match(r"^\s{6}([A-Za-z_][A-Za-z0-9_]*):\s*(.*?)\s*$", raw_line)
            if environment_match:
                services[current]["environment"][environment_match.group(1)] = strip_yaml_scalar(
                    environment_match.group(2).split(" #", 1)[0]
                )
        if current_section == "depends_on" and indent == 6:
            dependency_match = re.match(r"^\s{6}([A-Za-z0-9_.-]+):\s*$", raw_line)
            if dependency_match:
                current_dependency = dependency_match.group(1)
                services[current]["depends_on"][current_dependency] = ""
        if current_section == "depends_on" and current_dependency and indent == 8:
            condition_match = re.match(r"^\s{8}condition:\s*(.*?)\s*$", raw_line)
            if condition_match:
                services[current]["depends_on"][current_dependency] = strip_yaml_scalar(
                    condition_match.group(1).split(" #", 1)[0]
                )
    return services


def uses_latest(image: str) -> bool:
    if "@sha256:" in image:
        return False
    final_component = image.rsplit("/", 1)[-1]
    return ":" not in final_component or final_component.rsplit(":", 1)[-1] == "latest"


def find_compose_command() -> list[str] | None:
    if shutil.which("docker"):
        result = subprocess.run(
            ["docker", "compose", "version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    return None


def validate_compose_cli(
    validator: Validator,
    compose_path: Path,
    env_file: Path | None,
) -> None:
    command = find_compose_command()
    if command is None:
        validator.fail("未找到 docker compose/docker-compose，无法完成 Compose 官方解析")
        return
    args = [*command]
    if env_file is not None:
        if not env_file.is_file():
            validator.fail(f"指定的 env 文件不存在：{env_file}")
            return
        args.extend(["--env-file", str(env_file.resolve())])
    args.extend(["-f", str(compose_path.resolve()), "config", "--quiet"])
    result = subprocess.run(
        args,
        cwd=compose_path.parent,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode == 0:
        validator.ok("docker compose config --quiet 通过")
    else:
        details = (result.stderr or result.stdout).strip()
        validator.fail(f"docker compose config 失败：{details}")


def validate() -> int:
    args = parse_args()
    validator = Validator()
    bundle_dir = args.bundle_dir.resolve()
    version = args.version.strip()
    if not re.fullmatch(r"v[0-9]+", version):
        validator.fail(f"版本号必须形如 v12，当前为：{version!r}")

    backend_name = f"agent-backend-{version}.tar"
    frontend_name = f"agent-frontend-{version}.tar"
    expected_names = {backend_name, frontend_name, "docker-compose.yaml"}
    backend_path = bundle_dir / backend_name
    frontend_path = bundle_dir / frontend_name
    compose_path = bundle_dir / "docker-compose.yaml"

    validator.require(bundle_dir.is_dir(), f"交付目录存在：{bundle_dir}", f"交付目录不存在：{bundle_dir}")
    if not bundle_dir.is_dir():
        validator.print_report()
        return 1

    actual_names = {
        item.name
        for item in bundle_dir.iterdir()
        if item.is_file() and not item.name.startswith(".")
    }
    validator.require(
        actual_names == expected_names,
        "交付目录仅包含约定的三个文件",
        f"交付目录文件不符合三件套约定；期望 {sorted(expected_names)}，实际 {sorted(actual_names)}",
    )
    for path in (backend_path, frontend_path, compose_path):
        validator.require(
            path.is_file() and not path.is_symlink() and path.stat().st_size > 0,
            f"文件存在且非空：{path.name}",
            f"文件缺失、为空或为符号链接：{path}",
        )
    if any(not path.is_file() for path in (backend_path, frontend_path, compose_path)):
        validator.print_report()
        return 1

    try:
        compose_text = compose_path.read_text(encoding="utf-8-sig")
        services = parse_compose_services(compose_text)
    except Exception as exc:
        validator.fail(f"Compose 文件不可读：{exc}")
        services = {}

    missing_services = REQUIRED_SERVICES - set(services)
    validator.require(
        not missing_services,
        "Compose 包含全部七个必需服务",
        f"Compose 缺少服务：{sorted(missing_services)}",
    )
    build_services = sorted(name for name, data in services.items() if data["build"])
    validator.require(
        not build_services,
        "交付 Compose 不包含 build 字段",
        f"测试机没有源码，以下服务不得包含 build：{build_services}",
    )

    compose_images: dict[str, str] = {}
    for service, data in services.items():
        images = data["images"]
        if len(images) != 1:
            validator.fail(f"服务 {service} 必须且只能有一个生效 image，当前为 {images}")
            continue
        compose_images[service] = images[0]

    expected_core_images = {
        "agent-backend": f"deploy-agent-backend:{version}",
        "agent-frontend": f"deploy-agent-frontend:{version}",
        "ocr-service": "local/agent-ocr:linux-amd64",
    }
    for service, expected_image in expected_core_images.items():
        actual_image = compose_images.get(service)
        validator.require(
            actual_image == expected_image,
            f"{service} 镜像标签正确：{expected_image}",
            f"{service} 镜像应为 {expected_image}，实际为 {actual_image}",
        )

    backend_entrypoint = str(services.get("agent-backend", {}).get("entrypoint") or "")
    has_shell_defence = "/bin/sh" in backend_entrypoint and "/usr/local/bin/backend-entrypoint.sh" in backend_entrypoint
    validator.require(
        has_shell_defence,
        "Compose 使用 /bin/sh 启动后端入口脚本",
        "后端 entrypoint 必须显式包含 /bin/sh 和 /usr/local/bin/backend-entrypoint.sh",
    )

    backend_environment = services.get("agent-backend", {}).get("environment") or {}
    debug_value = str(backend_environment.get("DEBUG") or "").strip().lower()
    validator.require(
        debug_value == "false",
        "交付 Compose 已固定 DEBUG=false，不会启用 Flask reloader",
        "交付 Compose 必须将 agent-backend DEBUG 固定为 false；"
        f"当前为 {debug_value!r}，长任务可能被调试重载中断",
    )
    app_host = str(backend_environment.get("APP_HOST") or "").strip()
    app_port = str(backend_environment.get("APP_PORT") or "").strip()
    validator.require(
        app_host == "0.0.0.0" and app_port == "5002",
        "后端容器内监听契约固定为 0.0.0.0:5002",
        "agent-backend 的 APP_HOST/APP_PORT 必须固定为 0.0.0.0/5002，"
        "与 Nginx、容器端口和健康检查保持一致；"
        f"当前为 {app_host!r}/{app_port!r}",
    )
    frontend_dependencies = services.get("agent-frontend", {}).get("depends_on") or {}
    backend_dependency_condition = str(frontend_dependencies.get("agent-backend") or "")
    validator.require(
        backend_dependency_condition == "service_healthy",
        "前端仅在后端健康后启动",
        "agent-frontend 必须 depends_on agent-backend: service_healthy，"
        f"当前为 {backend_dependency_condition!r}",
    )

    for service, image in compose_images.items():
        for fragment in FORBIDDEN_IMAGE_FRAGMENTS:
            if fragment in image:
                validator.fail(f"服务 {service} 使用历史无效镜像名：{image}")

    allowed_latest = set(args.allow_legacy_latest)
    unknown_latest_exceptions = allowed_latest - set(compose_images.values())
    if unknown_latest_exceptions:
        validator.fail(f"latest 例外未被 Compose 使用：{sorted(unknown_latest_exceptions)}")
    for service, image in compose_images.items():
        if uses_latest(image) and image not in allowed_latest:
            validator.fail(f"服务 {service} 使用 latest 或隐式 latest：{image}")

    try:
        backend_images, backend_manifest = inspect_docker_save(backend_path)
        validator.ok(f"后端 tar 可完整读取，包含：{sorted(backend_images)}")
    except Exception as exc:
        validator.fail(f"后端 tar 无效或已损坏：{exc}")
        backend_images, backend_manifest = {}, []
    try:
        frontend_images, frontend_manifest = inspect_docker_save(frontend_path)
        validator.ok(f"前端 tar 可完整读取，包含：{sorted(frontend_images)}")
    except Exception as exc:
        validator.fail(f"前端 tar 无效或已损坏：{exc}")
        frontend_images = {}
        frontend_manifest = []

    bundled_images = set(backend_images) | set(frontend_images)
    for expected_image in expected_core_images.values():
        validator.require(
            expected_image in bundled_images,
            f"交付 tar 确实包含 {expected_image}",
            f"Compose 引用了 {expected_image}，但两个 tar 均未包含该 RepoTag",
        )

    old_version_tags = sorted(
        image
        for image in bundled_images
        if (
            image.startswith("deploy-agent-backend:")
            or image.startswith("deploy-agent-frontend:")
        )
        and not image.endswith(f":{version}")
    )
    validator.require(
        not old_version_tags,
        "tar 中没有混入旧版前后端标签",
        f"tar 中混入其他版本：{old_version_tags}",
    )

    wrong_arch = sorted(
        f"{image.tag}={image.os_name}/{image.architecture}"
        for image in [*backend_images.values(), *frontend_images.values()]
        if image.os_name != "linux" or image.architecture != "amd64"
    )
    validator.require(
        not wrong_arch,
        "tar 中全部镜像均为 linux/amd64",
        f"镜像架构错误：{wrong_arch}",
    )

    backend_tag = expected_core_images["agent-backend"]
    try:
        calculated_source_digests = calculate_delivery_source_digests(args.source_root)
    except Exception as exc:
        validator.fail(f"无法计算当前源码摘要：{exc}")
        calculated_source_digests = {"backend": "", "frontend": ""}
    expected_source_digests = {
        "agent-backend": args.expected_backend_source_digest
        or calculated_source_digests["backend"],
        "agent-frontend": args.expected_frontend_source_digest
        or calculated_source_digests["frontend"],
    }
    for role, expected_digest in expected_source_digests.items():
        validator.require(
            bool(SOURCE_DIGEST_PATTERN.fullmatch(expected_digest or "")),
            f"{role} 期望源码摘要有效：{expected_digest}",
            f"{role} 期望源码摘要无效：{expected_digest!r}",
        )
    release_images = {
        expected_core_images["agent-backend"]: (
            backend_images.get(expected_core_images["agent-backend"]),
            "agent-backend",
        ),
        expected_core_images["agent-frontend"]: (
            frontend_images.get(expected_core_images["agent-frontend"]),
            "agent-frontend",
        ),
    }
    for image_tag, (saved_image, expected_role) in release_images.items():
        if saved_image is None:
            continue
        labels = saved_image.config.get("Labels") or {}
        actual_version = str(labels.get("org.opencontainers.image.version") or "")
        actual_role = str(labels.get("com.ai4med.delivery.role") or "")
        source_digest = str(labels.get("com.ai4med.delivery.source-digest") or "")
        validator.require(
            actual_version == version,
            f"{image_tag} 内部发布版本标签正确：{version}",
            f"{image_tag} 内部版本标签应为 {version}，实际为 {actual_version!r}；禁止把旧镜像仅 retag 后交付",
        )
        validator.require(
            actual_role == expected_role,
            f"{image_tag} 镜像角色标签正确：{expected_role}",
            f"{image_tag} 镜像角色标签应为 {expected_role}，实际为 {actual_role!r}",
        )
        validator.require(
            bool(SOURCE_DIGEST_PATTERN.fullmatch(source_digest)),
            f"{image_tag} 已记录构建源码摘要：{source_digest}",
            f"{image_tag} 缺少有效 com.ai4med.delivery.source-digest；禁止仅改标签而不重建源码",
        )
        expected_digest = expected_source_digests[expected_role]
        validator.require(
            source_digest == expected_digest,
            f"{image_tag} 源码摘要与当前冻结源码一致",
            f"{image_tag} 源码摘要不一致；镜像={source_digest!r}，"
            f"期望={expected_digest!r}。必须重建镜像，不得仅 retag",
        )

    if backend_tag in backend_images:
        config_command = backend_images[backend_tag].config.get("Cmd") or []
        validator.require(
            "/usr/local/bin/backend-entrypoint.sh" in config_command,
            "后端镜像默认命令指向入口脚本",
            f"后端镜像默认 Cmd 异常：{config_command}",
        )
        try:
            entrypoint = inspect_entrypoint_in_layers(backend_path, backend_manifest, backend_tag)
            if entrypoint is None:
                validator.fail(f"后端最终镜像中不存在 /{ENTRYPOINT_PATH}")
            else:
                mode, first_bytes = entrypoint
                executable = bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
                validator.require(
                    executable,
                    f"后端入口脚本具备执行权限：{mode:04o}",
                    f"后端入口脚本无执行权限：{mode:04o}",
                )
                validator.require(
                    first_bytes.startswith(b"#!"),
                    "后端入口脚本包含有效 shebang",
                    "后端入口脚本缺少 shebang",
                )
        except Exception as exc:
            validator.fail(f"无法检查后端入口脚本：{exc}")

    frontend_tag = expected_core_images["agent-frontend"]
    if frontend_tag in frontend_images:
        try:
            final_paths = inspect_final_image_paths(frontend_path, frontend_manifest, frontend_tag)
            asset_pattern = re.compile(
                r"^usr/share/nginx/html/(js|css)/(app|chunk-vendors)\.([0-9a-f]{8,})\."
                r"(js|css)(?:\.map)?$"
            )
            asset_generations: dict[tuple[str, str, str], set[str]] = {}
            for path in final_paths:
                match = asset_pattern.fullmatch(path)
                if match is None:
                    continue
                directory, stem, digest, extension = match.groups()
                asset_generations.setdefault((directory, stem, extension), set()).add(digest)
            stale_groups = {
                "/".join(key): sorted(digests)
                for key, digests in sorted(asset_generations.items())
                if len(digests) > 1
            }
            validator.require(
                not stale_groups,
                "前端镜像不含多代 app/chunk-vendors 静态资源",
                "前端镜像残留多代带 hash 的旧资源；"
                f"增量构建必须在 COPY 前清空 /usr/share/nginx/html：{stale_groups}",
            )
            nginx_bytes = inspect_final_regular_file(
                frontend_path,
                frontend_manifest,
                frontend_tag,
                "etc/nginx/conf.d/default.conf",
            )
            nginx_text = (nginx_bytes or b"").decode("utf-8", errors="replace")
            index_no_cache = (
                "location = /index.html" in nginx_text
                and "Cache-Control" in nginx_text
                and "no-store" in nginx_text
            )
            validator.require(
                index_no_cache,
                "前端镜像已禁止缓存 index.html",
                "前端 Nginx 必须对 index.html 设置 no-store/no-cache，"
                "否则升级后浏览器可能继续请求旧 hash 资源",
            )
            timeout_values = {
                key: int(match.group(1)) if match else 0
                for key in ("proxy_send_timeout", "proxy_read_timeout", "send_timeout")
                for match in [re.search(rf"\b{key}\s+(\d+)s\s*;", nginx_text)]
            }
            timeout_ok = bool(timeout_values) and min(timeout_values.values()) >= 190
            validator.require(
                timeout_ok,
                f"前端 Nginx 超时不低于 190 秒：{timeout_values}",
                "前端 Nginx 的 proxy/send 超时必须不低于前端长读取请求的 190 秒；"
                f"当前为 {timeout_values}",
            )
        except Exception as exc:
            validator.fail(f"无法检查前端镜像最终静态资源：{exc}")

    referenced_images = set(compose_images.values())
    external_images = set(args.external_image)
    missing_from_tars = referenced_images - bundled_images
    if args.mode == "offline":
        validator.require(
            not external_images,
            "全量离线模式未声明外部镜像",
            "全量离线模式禁止 --external-image",
        )
        validator.require(
            not missing_from_tars,
            "全量离线模式的所有 Compose 镜像都位于 tar 中",
            f"全量离线包缺少镜像：{sorted(missing_from_tars)}",
        )
    else:
        validator.require(
            missing_from_tars == external_images,
            f"所有外部镜像均已显式声明：{sorted(external_images)}",
            "Compose 中未随包提供的镜像与 --external-image 不一致；"
            f"实际缺少 {sorted(missing_from_tars)}，声明为 {sorted(external_images)}",
        )

    validate_compose_cli(validator, compose_path, args.env_file)

    if args.skip_sha256:
        validator.fail("正式交付门禁禁止使用 --skip-sha256")
    else:
        for path in (backend_path, frontend_path, compose_path):
            try:
                digest = sha256_file(path)
                validator.ok(f"SHA-256 {path.name}: {digest}（{path.stat().st_size} bytes）")
            except Exception as exc:
                validator.fail(f"无法计算 {path.name} 的 SHA-256：{exc}")

    validator.print_report()
    return 1 if validator.errors else 0


if __name__ == "__main__":
    raise SystemExit(validate())
