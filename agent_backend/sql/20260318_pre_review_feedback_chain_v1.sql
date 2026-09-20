CREATE TABLE IF NOT EXISTS `pre_review_section_output` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `run_id` VARCHAR(64) NOT NULL,
  `section_id` VARCHAR(128) NOT NULL,
  `section_name` VARCHAR(256) NOT NULL,
  `schema_version` VARCHAR(64) NOT NULL DEFAULT 'chapter_review_v1',
  `output_json` LONGTEXT NOT NULL,
  `create_time` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_pre_review_section_output_run_id` (`run_id`),
  KEY `idx_pre_review_section_output_section_id` (`section_id`),
  CONSTRAINT `fk_pre_review_section_output_run_id`
    FOREIGN KEY (`run_id`) REFERENCES `pre_review_run` (`run_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS `pre_review_feedback_analysis_result` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `feedback_key` VARCHAR(128) NOT NULL,
  `run_id` VARCHAR(64) NOT NULL,
  `section_id` VARCHAR(128) NULL,
  `analysis_json` LONGTEXT NOT NULL,
  `create_time` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_pre_review_feedback_analysis_feedback_key` (`feedback_key`),
  KEY `idx_pre_review_feedback_analysis_run_id` (`run_id`),
  KEY `idx_pre_review_feedback_analysis_section_id` (`section_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS `pre_review_patch_registry` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `patch_id` VARCHAR(128) NOT NULL,
  `run_id` VARCHAR(64) NOT NULL,
  `section_id` VARCHAR(128) NULL,
  `patch_type` VARCHAR(64) NOT NULL,
  `target_agent` VARCHAR(64) NOT NULL,
  `target_scope` VARCHAR(256) NULL,
  `trigger_condition` LONGTEXT NULL,
  `patch_content` LONGTEXT NOT NULL,
  `source_feedback_key` VARCHAR(128) NOT NULL,
  `status` VARCHAR(32) NOT NULL DEFAULT 'candidate',
  `version` INT NOT NULL DEFAULT 1,
  `payload_json` LONGTEXT NULL,
  `create_time` DATETIME NOT NULL,
  `update_time` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_pre_review_patch_registry_patch_id` (`patch_id`),
  KEY `idx_pre_review_patch_registry_run_id` (`run_id`),
  KEY `idx_pre_review_patch_registry_section_id` (`section_id`),
  KEY `idx_pre_review_patch_registry_source_feedback_key` (`source_feedback_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS `pre_review_experience_memory` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `experience_id` VARCHAR(128) NOT NULL,
  `scope_type` VARCHAR(64) NOT NULL,
  `scope_key` VARCHAR(256) NOT NULL,
  `experience_type` VARCHAR(64) NOT NULL,
  `content` LONGTEXT NOT NULL,
  `source_feedback_ids` LONGTEXT NULL,
  `trigger_conditions` LONGTEXT NULL,
  `usage_count` INT NOT NULL DEFAULT 0,
  `success_count` INT NOT NULL DEFAULT 0,
  `status` VARCHAR(32) NOT NULL DEFAULT 'active',
  `payload_json` LONGTEXT NULL,
  `create_time` DATETIME NOT NULL,
  `update_time` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_pre_review_experience_memory_experience_id` (`experience_id`),
  KEY `idx_pre_review_experience_memory_scope_type` (`scope_type`),
  KEY `idx_pre_review_experience_memory_scope_key` (`scope_key`),
  KEY `idx_pre_review_experience_memory_experience_type` (`experience_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
