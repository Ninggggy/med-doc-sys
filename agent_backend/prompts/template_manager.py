import json
from pathlib import Path
from typing import Any, Dict

from jinja2 import Environment, FileSystemLoader, StrictUndefined


class PromptTemplateManager:
    def __init__(self, subdir: str):
        root = Path(__file__).resolve().parent
        self.template_dir = root / subdir
        self.template_dir.mkdir(parents=True, exist_ok=True)
        self.env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
            undefined=StrictUndefined,
        )
        self.env.filters["tojson"] = self._tojson

    @staticmethod
    def _tojson(value: Any, indent: int | None = None, **kwargs: Any) -> str:
        options = {"ensure_ascii": False}
        options.update(kwargs or {})
        if indent is not None:
            options["indent"] = indent
        return json.dumps(value, **options)

    @staticmethod
    def _load_override_bundle(prompt_config: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(prompt_config, dict):
            return {}
        bundle = prompt_config.get("prompt_bundle", {})
        if isinstance(bundle, dict) and bundle:
            return bundle
        bundle_path = str(prompt_config.get("prompt_bundle_path", "") or "").strip()
        if not bundle_path:
            return {}
        path = Path(bundle_path)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def render(self, template_name: str, context: Dict[str, Any], prompt_config: Dict[str, Any] | None = None) -> str:
        template = self.env.get_template(template_name)
        rendered = template.render(**context).strip()
        bundle = self._load_override_bundle(prompt_config or {})
        template_overrides = bundle.get("template_overrides", {}) if isinstance(bundle.get("template_overrides", {}), dict) else {}
        template_suffixes = bundle.get("template_suffixes", {}) if isinstance(bundle.get("template_suffixes", {}), dict) else {}
        if template_name in template_overrides and str(template_overrides.get(template_name, "")).strip():
            override_template = self.env.from_string(str(template_overrides[template_name]))
            rendered = override_template.render(**context).strip()
        suffix = str(template_suffixes.get(template_name, "") or "").strip()
        if suffix:
            rendered = f"{rendered}\n\n[DynamicPromptPatch]\n{suffix}".strip()
        return rendered
