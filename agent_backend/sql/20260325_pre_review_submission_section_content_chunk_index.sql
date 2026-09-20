ALTER TABLE `pre_review_submission_section_content`
  ADD COLUMN `chunk_index` INT NOT NULL DEFAULT 1 AFTER `section_name`;

UPDATE `pre_review_submission_section_content`
SET `chunk_index` = 1
WHERE `chunk_index` IS NULL OR `chunk_index` <= 0;

CREATE INDEX `idx_pre_review_submission_section_content_chunk_index`
  ON `pre_review_submission_section_content` (`chunk_index`);
