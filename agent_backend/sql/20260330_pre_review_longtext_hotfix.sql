ALTER TABLE `pre_review_execution_audit`
  MODIFY COLUMN `input_digest_json` LONGTEXT NOT NULL,
  MODIFY COLUMN `output_digest_json` LONGTEXT NOT NULL,
  MODIFY COLUMN `version_snapshot_json` LONGTEXT NOT NULL;

ALTER TABLE `pre_review_section_output`
  MODIFY COLUMN `output_json` LONGTEXT NOT NULL;

ALTER TABLE `pre_review_feedback_analysis_result`
  MODIFY COLUMN `analysis_json` LONGTEXT NOT NULL;

ALTER TABLE `pre_review_patch_registry`
  MODIFY COLUMN `trigger_condition` LONGTEXT NULL,
  MODIFY COLUMN `patch_content` LONGTEXT NOT NULL,
  MODIFY COLUMN `payload_json` LONGTEXT NULL;

ALTER TABLE `pre_review_experience_memory`
  MODIFY COLUMN `content` LONGTEXT NOT NULL,
  MODIFY COLUMN `source_feedback_ids` LONGTEXT NULL,
  MODIFY COLUMN `trigger_conditions` LONGTEXT NULL,
  MODIFY COLUMN `payload_json` LONGTEXT NULL;

ALTER TABLE `pre_review_upload_task`
  MODIFY COLUMN `payload_json` LONGTEXT NULL;

ALTER TABLE `pre_review_submission_section_content`
  MODIFY COLUMN `content` LONGTEXT NOT NULL;

ALTER TABLE `pre_review_section_rule`
  MODIFY COLUMN `payload_json` LONGTEXT NULL;

ALTER TABLE `pre_review_prompt_rule`
  MODIFY COLUMN `payload_json` LONGTEXT NULL;

ALTER TABLE `pre_review_section_example`
  MODIFY COLUMN `content` LONGTEXT NOT NULL,
  MODIFY COLUMN `payload_json` LONGTEXT NULL;
