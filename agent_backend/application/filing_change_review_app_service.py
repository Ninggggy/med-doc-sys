from typing import Any, Dict, List, Tuple

from agent.agent_backend.services.filing_change_review_service import FilingChangeReviewService


class FilingChangeReviewAppService:
    def __init__(self) -> None:
        self.service = FilingChangeReviewService()

    def create_project(self, payload: Dict[str, Any]) -> Tuple[bool, str, Any]:
        return self.service.create_project(payload)

    def list_projects(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.service.list_projects(payload)

    def delete_project(self, project_id: str) -> Tuple[bool, str]:
        return self.service.delete_project(project_id)

    def get_project_detail(self, project_id: str) -> Tuple[bool, str, Any]:
        return self.service.get_project_detail(project_id)

    def save_application_form(self, project_id: str, payload: Dict[str, Any]) -> Tuple[bool, str, Any]:
        return self.service.save_application_form(project_id, payload)

    def get_application_form(self, project_id: str) -> Tuple[bool, str, Any]:
        return self.service.get_application_form(project_id)

    def import_application_form(self, project_id: str, upload_file: Any) -> Tuple[bool, str, Any]:
        return self.service.import_application_form(project_id, upload_file)

    def stage_application_form_import(self, project_id: str, upload_file: Any) -> Tuple[bool, str, Any]:
        return self.service.stage_application_form_import(project_id, upload_file)

    def import_staged_application_form(self, project_id: str, staging_file: str, original_file_name: str) -> Tuple[bool, str, Any]:
        return self.service.import_staged_application_form(project_id, staging_file, original_file_name)

    def cleanup_application_form_staging(self, project_id: str, staging_file: str) -> None:
        self.service.cleanup_application_form_staging(project_id, staging_file)

    def import_application_form_word(self, project_id: str, upload_file: Any) -> Tuple[bool, str, Any]:
        return self.import_application_form(project_id, upload_file)

    def parse_application_form(self, project_id: str) -> Tuple[bool, str, Any]:
        return self.service.parse_application_form(project_id)

    def upload_submission_files(self, project_id: str, files: List[Any], material_category: str, material_sub_category: str = "") -> Tuple[bool, str, Any]:
        return self.service.upload_submission_files(project_id, files, material_category, material_sub_category)

    def list_submissions(self, project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.service.list_submissions(project_id, payload)

    def delete_submission(self, project_id: str, doc_id: str) -> Tuple[bool, str]:
        return self.service.delete_submission(project_id, doc_id)

    def parse_submission(self, project_id: str, doc_id: str) -> Tuple[bool, str, Any]:
        return self.service.parse_submission(project_id, doc_id)

    def batch_parse_submissions(self, project_id: str, payload: Dict[str, Any]) -> Tuple[bool, str, Any]:
        return self.service.batch_parse_submissions(project_id, payload)

    def get_submission_parsed_markdown(self, project_id: str, doc_id: str) -> Tuple[bool, str, Any]:
        return self.service.get_submission_parsed_markdown(project_id, doc_id)

    def get_submission_original_file(self, project_id: str, doc_id: str):
        return self.service.get_submission_original_file(project_id, doc_id)

    def confirm_submission_numeric_cells(self, project_id: str, doc_id: str, payload: Dict[str, Any]):
        return self.service.confirm_submission_numeric_cells(project_id, doc_id, payload)

    def update_submission_metadata(self, project_id: str, doc_id: str, payload: Dict[str, Any]) -> Tuple[bool, str]:
        return self.service.update_submission_metadata(project_id, doc_id, payload)

    def set_category_not_applicable(self, project_id: str, payload: Dict[str, Any]) -> Tuple[bool, str]:
        return self.service.set_category_not_applicable(project_id, payload)

    def get_submission_catalog(self, project_id: str) -> Dict[str, Any]:
        return self.service.get_submission_catalog(project_id)

    def check_submission_completeness(self, project_id: str) -> Dict[str, Any]:
        return self.service.check_submission_completeness(project_id)

    def get_parse_readiness(self, project_id: str) -> Dict[str, Any]:
        return self.service.get_parse_readiness(project_id)

    def get_parse_review(self, project_id, kind, doc_id):
        return self.service.get_parse_review(project_id, kind, doc_id)

    def get_parse_review_page(self, project_id, kind, doc_id, page_no, payload):
        return self.service.get_parse_review_page(project_id, kind, doc_id, page_no, payload)

    def save_parse_review(self, project_id, kind, doc_id, payload):
        return self.service.save_parse_review(project_id, kind, doc_id, payload)

    def compare_submission_materials(self, project_id: str, payload: Dict[str, Any]) -> Tuple[bool, str, Any]:
        return self.service.compare_submission_materials(project_id, payload)

    def upload_reference_materials(self, files: List[Any], material_type: str, task_type: str, payload: Dict[str, Any]) -> Tuple[bool, str, Any]:
        return self.service.upload_reference_materials(files, material_type, task_type, payload)

    def list_reference_materials(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.service.list_reference_materials(payload)

    def delete_reference_material(self, doc_id: str) -> Tuple[bool, str]:
        return self.service.delete_reference_material(doc_id)

    def parse_reference_material(self, doc_id: str) -> Tuple[bool, str, Any]:
        return self.service.parse_reference_material(doc_id)

    def update_reference_material_metadata(self, doc_id: str, payload: Dict[str, Any]) -> Tuple[bool, str]:
        return self.service.update_reference_material_metadata(doc_id, payload)

    def get_reference_material_parsed_markdown(self, doc_id: str) -> Tuple[bool, str, Any]:
        return self.service.get_reference_material_parsed_markdown(doc_id)

    def get_reference_taxonomy(self) -> Dict[str, Any]:
        return self.service.get_reference_taxonomy()

    def create_rule(self, payload: Dict[str, Any]) -> Tuple[bool, str, Any]:
        return self.service.create_rule(payload)

    def list_rules(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.service.list_rules(payload)

    def update_rule(self, rule_id: str, payload: Dict[str, Any]) -> Tuple[bool, str, Any]:
        return self.service.update_rule(rule_id, payload)

    def delete_rule(self, rule_id: str) -> Tuple[bool, str]:
        return self.service.delete_rule(rule_id)

    def import_rules(self, upload_file: Any) -> Tuple[bool, str, Any]:
        return self.service.import_rules(upload_file)

    def start_review(self, project_id: str) -> Tuple[bool, str, Any]:
        return self.service.start_review(project_id)

    def start_ai_review(self, project_id: str) -> Tuple[bool, str, Any]:
        return self.service.start_review(project_id)

    def project_task_creation_lock(self, project_id: str):
        return self.service.project_task_creation_lock(project_id)

    def prepare_review_run(self, project_id: str) -> Tuple[bool, str, Any]:
        return self.service.prepare_review_run(project_id)

    def execute_review_run(self, project_id: str, run_id: str) -> Tuple[bool, str, Any]:
        return self.service.execute_review_run(project_id, run_id)

    def mark_review_run_interrupted(self, project_id: str, run_id: str, message: str) -> Tuple[bool, str, Any]:
        return self.service.mark_review_run_interrupted(project_id, run_id, message)

    def get_review_history(self, project_id: str) -> Dict[str, Any]:
        return self.service.get_review_history(project_id)

    def get_run_result(self, run_id: str) -> Tuple[bool, str, Any]:
        return self.service.get_run_result(run_id)

    def get_run_evidence(self, run_id: str) -> Tuple[bool, str, Any]:
        return self.service.get_run_evidence(run_id)

    def generate_correction_notice(self, run_id: str) -> Tuple[bool, str, Any]:
        return self.service.generate_correction_notice(run_id)

    def manual_confirm_run(self, run_id: str, payload: Dict[str, Any]) -> Tuple[bool, str, Any]:
        return self.service.manual_confirm_run(run_id, payload)

    def generate_report(self, run_id: str) -> Tuple[bool, str, Any]:
        return self.service.generate_report(run_id)

    def get_project_latest_report(self, project_id: str, run_id: str = "") -> Tuple[bool, str, Any]:
        return self.service.get_project_latest_report(project_id, run_id=run_id)

    def export_report_word(self, report_id: str) -> Tuple[bool, str, Any]:
        return self.service.export_report_word(report_id)
