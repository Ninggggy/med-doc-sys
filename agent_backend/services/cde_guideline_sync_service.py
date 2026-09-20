"""CDE 指导原则同步服务。

功能：
1. 批量同步指导原则到本地知识库
2. 记录同步历史
3. 提供进度回调
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from agent.agent_backend.config.settings import settings
from agent.agent_backend.services.cde_guideline_crawler import CDEGuidelineCrawler
from agent.agent_backend.services.knowledge_service import KnowledgeService
from agent.agent_backend.utils.agent_logging import log_agent_flow


logger = logging.getLogger("agent.cde_sync")


class CDEGuidelineSyncService:
    """CDE 指导原则同步服务。

    集成爬虫和知识库上传，提供完整的同步流程。
    """

    def __init__(
        self,
        min_interval: float = 2.0,
        proxy: Optional[str] = None,
    ):
        self.crawler = CDEGuidelineCrawler(min_interval=min_interval, proxy=proxy)
        self.knowledge_service = KnowledgeService()
        self._sync_history: List[Dict[str, Any]] = []

    def sync_guidelines(
        self,
        classify1: str = "all",
        classify2: str = "all",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        keyword: Optional[str] = None,
        auto_download: bool = True,
        dry_run: bool = False,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """执行同步任务。

        Args:
            classify1: 适用范围筛选
            classify2: 专业分类筛选
            start_date: 发布日期开始
            end_date: 发布日期结束
            keyword: 搜索关键词
            auto_download: 是否自动下载新增的指导原则
            dry_run: 是否仅对比不下载（测试模式）
            progress_callback: 进度回调函数

        Returns:
            同步结果统计
        """
        start_time = time.time()

        # 回调函数
        def _notify(phase: str, **kwargs):
            if progress_callback:
                try:
                    progress_callback({
                        "phase": phase,
                        "timestamp": datetime.now().isoformat(),
                        **kwargs,
                    })
                except Exception:
                    pass

        _notify("fetch_list_start", classify1=classify1, classify2=classify2)

        try:
            # 1. 获取 CDE 指导原则列表
            guidelines = self.crawler.fetch_all_guidelines(
                classify1=classify1,
                classify2=classify2,
                start_date=start_date,
                end_date=end_date,
                keyword=keyword,
            )

            _notify("fetch_list_done", total_fetched=len(guidelines))

            log_agent_flow(
                "cde_sync",
                "fetch_complete",
                classify1=classify1,
                classify2=classify2,
                total_guidelines=len(guidelines),
            )

            # 2. 对比本地知识库
            _notify("compare_start")
            comparison = self.crawler.compare_with_local(guidelines)
            _notify("compare_done", **{k: len(v) for k, v in comparison.items()})

            result = {
                "success": True,
                "total_fetched": len(guidelines),
                "new_count": len(comparison["new"]),
                "existing_count": len(comparison["existing"]),
                "updated_count": len(comparison["updated"]),
                "downloaded_count": 0,
                "failed_count": 0,
                "dry_run": dry_run,
                "guidelines": guidelines,
                "comparison": {
                    "new": comparison["new"],
                    "existing": [
                        {
                            "title": item.get("title", ""),
                            "issueDate": item.get("issueDate", ""),
                            "local_doc_id": item.get("local_doc_id", ""),
                        }
                        for item in comparison["existing"]
                    ],
                    "updated": [
                        {
                            "title": item.get("title", ""),
                            "issueDate": item.get("issueDate", ""),
                            "local_doc_id": item.get("local_doc_id", ""),
                            "local_issue_date": item.get("local_issue_date", ""),
                        }
                        for item in comparison["updated"]
                    ],
                },
            }

            if dry_run:
                result["message"] = "Dry run completed, no files downloaded"
                return result

            # 3. 下载并上传新增的指导原则
            if auto_download and comparison["new"]:
                _notify("download_start", count=len(comparison["new"]))

                # 下载目录
                download_dir = os.path.join(
                    settings.upload_dir,
                    "cde_guidelines",
                    datetime.now().strftime("%Y%m%d_%H%M%S"),
                )

                downloaded_items = []
                failed_items = []

                for idx, guideline in enumerate(comparison["new"]):
                    _notify(
                        "downloading",
                        current=idx + 1,
                        total=len(comparison["new"]),
                        title=guideline.get("title", ""),
                    )

                    try:
                        # 下载 PDF
                        file_path, file_name = self.crawler.download_guideline_pdf(
                            zdyz_id=guideline.get("zdyzIdCODE", ""),
                            output_dir=download_dir,
                            simulate_click=True,
                        )

                        # 上传到知识库
                        _notify(
                            "uploading",
                            current=idx + 1,
                            total=len(comparison["new"]),
                            title=guideline.get("title", ""),
                        )

                        # 使用查询时的筛选条件，如果筛选条件为 all 则使用默认值
                        affect_range = guideline.get("classify1", "")
                        profession_classification = guideline.get("classify2", "")

                        # 如果筛选条件为空（all），使用默认分类
                        if not affect_range or affect_range == "all":
                            affect_range = "other"
                        if not profession_classification or profession_classification == "all":
                            profession_classification = "other"

                        ok, msg, data = self.knowledge_service.upload_local_knowledge(
                            file_path=file_path,
                            file_name=file_name,
                            classification="指导原则",
                            affect_range=affect_range,
                            profession_classification=profession_classification,
                            registration_scope=guideline.get("issueDate", ""),
                            registration_path=guideline.get("issueDate", ""),
                        )

                        if ok and data:
                            downloaded_items.append({
                                "title": guideline.get("title", ""),
                                "issueDate": guideline.get("issueDate", ""),
                                "doc_id": data.get("doc_id", ""),
                                "file_path": file_path,
                            })
                        else:
                            failed_items.append({
                                "title": guideline.get("title", ""),
                                "error": msg,
                            })

                    except Exception as e:
                        logger.error(f"Failed to sync guideline {guideline.get('title', '')}: {e}")
                        failed_items.append({
                            "title": guideline.get("title", ""),
                            "error": str(e),
                        })

                _notify(
                    "download_complete",
                    downloaded=len(downloaded_items),
                    failed=len(failed_items),
                )

                result["downloaded_count"] = len(downloaded_items)
                result["failed_count"] = len(failed_items)
                result["downloaded_items"] = downloaded_items
                result["failed_items"] = failed_items

            # 记录同步历史
            history_entry = {
                "timestamp": datetime.now().isoformat(),
                "classify1": classify1,
                "classify2": classify2,
                "total_fetched": len(guidelines),
                "new_count": len(comparison["new"]),
                "existing_count": len(comparison["existing"]),
                "updated_count": len(comparison["updated"]),
                "downloaded_count": result.get("downloaded_count", 0),
                "duration": time.time() - start_time,
            }
            self._sync_history.append(history_entry)

            _notify(
                "sync_complete",
                duration=time.time() - start_time,
                **history_entry,
            )

            return result

        except Exception as e:
            logger.error(f"Sync failed: {e}")
            _notify("sync_failed", error=str(e))
            return {
                "success": False,
                "error": str(e),
            }
        finally:
            self.crawler.close()

    def get_sync_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取同步历史。"""
        return self._sync_history[-limit:]

    def fetch_only(
        self,
        classify1: str = "all",
        classify2: str = "all",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> Dict[str, Any]:
        """仅获取 CDE 列表，不下载不上传。

        用于前端预览和对比。
        """
        try:
            guidelines = self.crawler.fetch_all_guidelines(
                classify1=classify1,
                classify2=classify2,
                start_date=start_date,
                end_date=end_date,
                keyword=keyword,
            )

            comparison = self.crawler.compare_with_local(guidelines)

            return {
                "success": True,
                "total_fetched": len(guidelines),
                "new_count": len(comparison["new"]),
                "existing_count": len(comparison["existing"]),
                "updated_count": len(comparison["updated"]),
                "guidelines": guidelines,
                "comparison": {
                    "new": comparison["new"],
                    "existing": [
                        {
                            "title": item.get("title", ""),
                            "issueDate": item.get("issueDate", ""),
                            "local_doc_id": item.get("local_doc_id", ""),
                        }
                        for item in comparison["existing"]
                    ],
                    "updated": [
                        {
                            "title": item.get("title", ""),
                            "issueDate": item.get("issueDate", ""),
                            "local_doc_id": item.get("local_doc_id", ""),
                            "local_issue_date": item.get("local_issue_date", ""),
                        }
                        for item in comparison["updated"]
                    ],
                },
            }
        except Exception as e:
            logger.error(f"Fetch failed: {e}")
            return {
                "success": False,
                "error": str(e),
            }
        finally:
            self.crawler.close()
