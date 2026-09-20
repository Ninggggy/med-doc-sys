"""CDE 指导原则爬虫服务。

功能：
1. 使用 Selenium 模拟浏览器获取 CDE 指导原则列表
2. 下载 PDF 文件
3. 对比本地知识库，返回需同步的文件
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup

from agent.agent_backend.database.mysql.db_model import FileInfo
from agent.agent_backend.database.mysql.mysql_conn import MysqlConnection
from agent.agent_backend.utils.agent_logging import log_agent_flow


logger = logging.getLogger("agent.cde_crawler")


# CDE 网站配置
CDE_BASE_URL = "https://www.cde.org.cn"
CDE_LIST_PAGE_URL = f"{CDE_BASE_URL}/zdyz/listpage/9cd8db3b7530c6fa0c86485e563f93c7"


# 分类映射
CLASSIFY1_MAP = {
    "all": "全部",
    "traditional_chinese_medicine": "中药",
    "chemical_drug": "化学药",
    "biological_product": "生物制品",
}

CLASSIFY2_MAP = {
    "all": "全部",
    "pharmacy": "药学",
    "clinical": "临床",
    "non_clinical": "非临床",
    "clinical_pharmacology": "临床药理",
    "biostatistics": "生物统计",
    "multidisciplinary": "多学科",
}


class CDEGuidelineCrawler:
    """CDE 指导原则爬虫（使用 Selenium）。

    特性：
    - 使用 Selenium 模拟浏览器获取动态加载的页面
    - 支持分类筛选和分页获取
    - 自动下载 PDF 附件
    """

    def __init__(
        self,
        min_interval: float = 2.0,
        max_retries: int = 3,
        timeout: int = 30,
        proxy: Optional[str] = None,
        headless: bool = True,
    ):
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self.proxy = proxy
        self.headless = headless
        self._last_request_time: Optional[float] = None
        self._db_conn = MysqlConnection()
        self._driver: Optional[webdriver.Chrome] = None

    @staticmethod
    def _first_existing_path(candidates: List[str]) -> str:
        for item in candidates:
            candidate = str(item or "").strip()
            if candidate and os.path.exists(candidate):
                return candidate
        return ""

    def _init_driver(self) -> webdriver.Chrome:
        """初始化 Chrome WebDriver。"""
        if self._driver is not None:
            return self._driver

        chrome_options = Options()
        if self.headless:
            chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

        if self.proxy:
            chrome_options.add_argument(f"--proxy-server={self.proxy}")

        chrome_binary = self._first_existing_path([
            os.environ.get("CHROME_BIN", ""),
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/google-chrome",
        ])
        if chrome_binary:
            chrome_options.binary_location = chrome_binary

        chromedriver_path = self._first_existing_path([
            os.environ.get("CHROMEDRIVER_PATH", ""),
            "/usr/bin/chromedriver",
            "/usr/local/bin/chromedriver",
        ])
        if chromedriver_path:
            logger.info(f"Using system chromedriver: {chromedriver_path}")
            service = Service(executable_path=chromedriver_path)
        else:
            logger.info("System chromedriver not found, fallback to webdriver-manager")
            service = Service(ChromeDriverManager().install())
        self._driver = webdriver.Chrome(service=service, options=chrome_options)
        self._driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    })
                """
            }
        )
        return self._driver

    def _close_driver(self):
        """关闭 WebDriver。"""
        if self._driver:
            self._driver.quit()
            self._driver = None

    def _rate_limit(self):
        """执行限速控制。"""
        now = time.time()
        if self._last_request_time is not None:
            elapsed = now - self._last_request_time
            if elapsed < self.min_interval:
                sleep_time = self.min_interval - elapsed
                logger.debug(f"Rate limiting: sleeping {sleep_time:.1f}s")
                time.sleep(sleep_time)
        self._last_request_time = time.time()

    def _select_radio_option(self, driver: webdriver.Chrome, container_class: str, option_text: str):
        """选择单选按钮选项。

        Args:
            driver: WebDriver 实例
            container_class: 包含选项的容器 class 名
            option_text: 要选择的选项文本
        """
        try:
            # 查找容器
            container = driver.find_element(By.CLASS_NAME, container_class)
            # 查找所有选项
            options = container.find_elements(By.TAG_NAME, "span")
            for option in options:
                text = option.text.strip()
                if option_text in text or text in option_text:
                    # 检查是否已选中
                    if "radioActive" not in option.get_attribute("class"):
                        option.click()
                        time.sleep(1)  # 等待页面响应
                    return True
            return False
        except Exception as e:
            logger.warning(f"Failed to select option '{option_text}' in {container_class}: {e}")
            return False

    def fetch_guideline_list(
        self,
        classify1: str = "all",
        classify2: str = "all",
        page: int = 1,
        page_size: int = 10,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """获取指导原则列表。

        使用 Selenium 模拟浏览器操作，点击筛选条件后获取列表数据。

        Args:
            classify1: 适用范围筛选
            classify2: 专业分类筛选
            page: 页码，从1开始
            page_size: 每页数量（通过下拉框选择）
            start_date: 发布日期开始 (YYYY-MM-DD)
            end_date: 发布日期结束 (YYYY-MM-DD)
            keyword: 搜索关键词

        Returns:
            (guideline_list, total_count)
        """
        self._rate_limit()
        driver = self._init_driver()

        try:
            # 访问列表页
            logger.info(f"Navigating to list page: {CDE_LIST_PAGE_URL}")
            driver.get(CDE_LIST_PAGE_URL)

            # 等待页面加载
            WebDriverWait(driver, self.timeout).until(
                EC.presence_of_element_located((By.CLASS_NAME, "listWrapper"))
            )
            time.sleep(2)  # 等待 JavaScript 初始化

            # 应用筛选条件
            # 1. 适用范围
            if classify1 != "all":
                classify1_text = CLASSIFY1_MAP.get(classify1, classify1)
                self._select_radio_option(driver, "classify1Items", classify1_text)
                time.sleep(1)

            # 2. 专业分类
            if classify2 != "all":
                classify2_text = CLASSIFY2_MAP.get(classify2, classify2)
                self._select_radio_option(driver, "classify2Items", classify2_text)
                time.sleep(1)

            # 3. 搜索关键词
            if keyword:
                try:
                    search_input = driver.find_element(By.ID, "searchTitle")
                    search_input.clear()
                    search_input.send_keys(keyword)
                    # 点击搜索按钮
                    search_btn = driver.find_element(By.ID, "searcheBtn")
                    search_btn.click()
                    time.sleep(2)
                except Exception as e:
                    logger.warning(f"Failed to set search keyword: {e}")

            # 4. 日期范围
            if start_date:
                try:
                    date_input = driver.find_element(By.ID, "publishDate1")
                    date_input.clear()
                    date_input.send_keys(start_date)
                except Exception as e:
                    logger.warning(f"Failed to set start date: {e}")

            if end_date:
                try:
                    date_input = driver.find_element(By.ID, "publishDate2")
                    date_input.clear()
                    date_input.send_keys(end_date)
                except Exception as e:
                    logger.warning(f"Failed to set end date: {e}")

            # 等待表格数据加载
            time.sleep(2)
            WebDriverWait(driver, self.timeout).until(
                EC.presence_of_element_located((By.CLASS_NAME, "layui-table"))
            )

            # 如果需要切换到其他页
            if page > 1:
                try:
                    # 查找分页组件
                    pagination = driver.find_element(By.CLASS_NAME, "layui-laypage")
                    # 点击指定页码
                    page_links = pagination.find_elements(By.TAG_NAME, "a")
                    for link in page_links:
                        if link.get_attribute("data-page") == str(page):
                            link.click()
                            time.sleep(2)
                            break
                except Exception as e:
                    logger.warning(f"Failed to navigate to page {page}: {e}")

            # 获取页面 HTML 并解析
            html_content = driver.page_source
            guidelines, total = self._parse_list_page(html_content)

            # 保存分类信息到每个结果
            classify1_mapped = CLASSIFY1_MAP.get(classify1, classify1) if classify1 != "all" else ""
            classify2_mapped = CLASSIFY2_MAP.get(classify2, classify2) if classify2 != "all" else ""
            for guideline in guidelines:
                guideline["_query_classify1"] = classify1
                guideline["_query_classify2"] = classify2
                guideline["classify1"] = classify1_mapped
                guideline["classify2"] = classify2_mapped

            logger.info(f"Fetch list done: {len(guidelines)} guidelines, total: {total}")

            return guidelines, total

        except Exception as e:
            logger.error(f"Failed to fetch guideline list: {e}")
            raise

    def _parse_list_page(self, html_content: str) -> Tuple[List[Dict[str, Any]], int]:
        """解析列表页 HTML，提取指导原则数据。"""
        soup = BeautifulSoup(html_content, "html.parser")
        guidelines = []

        # 查找表格
        table = soup.find("table", {"class": "layui-table"})
        if not table:
            logger.warning("Table not found in page")
            return [], 0

        # 提取总条数
        total = 0
        count_elem = soup.find("span", {"class": "layui-laypage-count"})
        if count_elem:
            match = re.search(r"(\d+)", count_elem.get_text())
            if match:
                total = int(match.group(1))

        # 遍历表格行
        tbody = table.find("tbody")
        if tbody:
            rows = tbody.find_all("tr")
        else:
            rows = table.find_all("tr")[1:]  # 跳过表头

        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) < 3:
                continue

            title_cell = cells[1] if len(cells) > 1 else None
            status_cell = cells[2] if len(cells) > 2 else None
            date_cell = cells[3] if len(cells) > 3 else None

            if not title_cell:
                continue

            link_elem = title_cell.find("a")
            if not link_elem:
                continue

            title = link_elem.get_text(strip=True)
            href = link_elem.get("href", "")

            # 提取 zdyzIdCODE
            zdyz_id = ""
            match = re.search(r"zdyzIdCODE=([a-f0-9]+)", href)
            if match:
                zdyz_id = match.group(1)

            issue_date = date_cell.get_text(strip=True) if date_cell else ""
            nowstate = status_cell.get_text(strip=True) if status_cell else ""

            if title and zdyz_id:
                guidelines.append({
                    "title": title,
                    "zdyzIdCODE": zdyz_id,
                    "issueDate": issue_date,
                    "nowstate": nowstate,
                    "detailUrl": urljoin(CDE_BASE_URL, href),
                })

        return guidelines, total

    def get_guideline_detail(self, zdyz_id: str) -> Dict[str, Any]:
        """获取指导原则详情页，提取 PDF 下载链接。"""
        self._rate_limit()
        driver = self._init_driver()

        detail_url = f"{CDE_BASE_URL}/zdyz/domesticinfopage?zdyzIdCODE={zdyz_id}"

        try:
            logger.info(f"Navigating to detail page: {detail_url}")
            driver.get(detail_url)

            # 等待页面加载
            WebDriverWait(driver, self.timeout).until(
                EC.presence_of_element_located((By.CLASS_NAME, "layui-table"))
            )
            time.sleep(2)

            # 获取页面 HTML
            html_content = driver.page_source
            result = self._parse_detail_page(html_content, zdyz_id)

            return result

        except Exception as e:
            logger.error(f"Failed to fetch detail for {zdyz_id}: {e}")
            raise

    def _parse_detail_page(self, html_content: str, zdyz_id: str) -> Dict[str, Any]:
        """解析详情页 HTML，提取 PDF 链接和元数据。"""
        soup = BeautifulSoup(html_content, "html.parser")
        result = {
            "zdyzIdCODE": zdyz_id,
            "title": "",
            "pdfUrl": "",
            "pdfFileName": "",
            "issueDate": "",
            "fclass": "",
            "zyfl": "",
            "content": "",
            "attachments": [],
        }

        # 提取标题
        title_elem = soup.find("td", {"id": "title"})
        if title_elem:
            result["title"] = title_elem.get_text(strip=True)

        # 提取发布日期
        issue_date_elem = soup.find("td", {"id": "issueDate"})
        if issue_date_elem:
            result["issueDate"] = issue_date_elem.get_text(strip=True)

        # 提取适用范围和专业分类
        fclass_elem = soup.find("td", {"id": "fclass"})
        if fclass_elem:
            result["fclass"] = fclass_elem.get_text(strip=True)

        zyfl_elem = soup.find("td", {"id": "zyfl"})
        if zyfl_elem:
            result["zyfl"] = zyfl_elem.get_text(strip=True)

        # 查找附件链接
        for tr in soup.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) >= 2:
                first_td_text = tds[0].get_text(strip=True)
                if "附件" in first_td_text:
                    link = tds[1].find("a", href=True)
                    if link:
                        href = link.get("href", "")
                        file_name_span = link.find("span", {"class": "fileLink"})
                        file_name = file_name_span.get_text(strip=True) if file_name_span else ""

                        if href:
                            if not href.startswith("http"):
                                href = urljoin(CDE_BASE_URL, href)
                            result["pdfUrl"] = href
                            result["pdfFileName"] = file_name or f"{zdyz_id}.pdf"
                            result["attachments"].append({
                                "url": href,
                                "fileName": file_name,
                            })
                    break

        return result

    def download_guideline_pdf(
        self,
        zdyz_id: str,
        output_dir: str,
        use_selenium_download: bool = True,
    ) -> Tuple[str, str]:
        """下载指导原则 PDF 文件。

        Args:
            zdyz_id: 指导原则 ID
            output_dir: 下载文件保存目录
            use_selenium_download: 是否使用 Selenium 下载（False 则使用 requests）

        Returns:
            (file_path, file_name)
        """
        os.makedirs(output_dir, exist_ok=True)

        # 获取详情和 PDF 链接
        detail = self.get_guideline_detail(zdyz_id)
        pdf_url = detail.get("pdfUrl")
        file_name = detail.get("pdfFileName") or f"{zdyz_id}.pdf"

        if not pdf_url:
            raise RuntimeError(f"PDF URL not found for {zdyz_id}")

        # 清理文件名
        safe_title = re.sub(r'[\\/*?"<>|:]', "_", detail.get("title", zdyz_id))[:100]
        if safe_title and not file_name.startswith(safe_title):
            file_name = f"{safe_title}_{file_name}"

        file_path = os.path.join(output_dir, file_name)

        if use_selenium_download:
            return self._download_with_selenium(zdyz_id, pdf_url, file_path, file_name)
        else:
            return self._download_with_requests(zdyz_id, pdf_url, file_path, file_name)

    def _download_with_requests(
        self,
        zdyz_id: str,
        pdf_url: str,
        file_path: str,
        file_name: str,
    ) -> Tuple[str, str]:
        """使用 requests 下载 PDF。"""
        import requests

        log_agent_flow(
            "cde_crawler",
            "download_pdf_start",
            zdyz_id=zdyz_id,
            pdf_url=pdf_url,
            method="requests",
        )

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": f"{CDE_BASE_URL}/zdyz/domesticinfopage?zdyzIdCODE={zdyz_id}",
            }
            response = requests.get(pdf_url, headers=headers, stream=True, timeout=60)
            response.raise_for_status()

            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            log_agent_flow(
                "cde_crawler",
                "download_pdf_done",
                zdyz_id=zdyz_id,
                file_path=file_path,
                file_size=os.path.getsize(file_path),
            )

            return file_path, file_name

        except Exception as e:
            logger.error(f"Failed to download PDF with requests for {zdyz_id}: {e}")
            raise

    def _download_with_selenium(
        self,
        zdyz_id: str,
        pdf_url: str,
        file_path: str,
        file_name: str,
    ) -> Tuple[str, str]:
        """使用 Selenium 下载 PDF。"""
        driver = self._init_driver()

        log_agent_flow(
            "cde_crawler",
            "download_pdf_start",
            zdyz_id=zdyz_id,
            pdf_url=pdf_url,
            method="selenium",
        )

        try:
            # 访问详情页
            detail_url = f"{CDE_BASE_URL}/zdyz/domesticinfopage?zdyzIdCODE={zdyz_id}"
            driver.get(detail_url)
            time.sleep(2)

            # 查找并点击 PDF 链接
            try:
                pdf_link = driver.find_element(By.CLASS_NAME, "fileLink")
                pdf_link.click()
                time.sleep(5)  # 等待下载
            except Exception as e:
                logger.warning(f"Could not click PDF link: {e}")
                # 直接访问 PDF URL
                driver.get(pdf_url)
                time.sleep(3)

            # 检查下载是否成功
            if os.path.exists(file_path):
                return file_path, file_name

            # 检查下载目录中的文件
            download_dir = os.path.dirname(file_path)
            files = [f for f in os.listdir(download_dir) if f.endswith(".pdf")]
            if files:
                latest_file = max(files, key=lambda f: os.path.getctime(os.path.join(download_dir, f)))
                latest_path = os.path.join(download_dir, latest_file)
                os.rename(latest_path, file_path)
                return file_path, file_name

            raise RuntimeError("PDF download failed - file not found")

        except Exception as e:
            logger.error(f"Failed to download PDF with selenium for {zdyz_id}: {e}")
            raise

    def fetch_all_guidelines(
        self,
        classify1: str = "all",
        classify2: str = "all",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        keyword: Optional[str] = None,
        max_pages: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """获取所有指导原则（自动翻页）。"""
        all_guidelines = []
        page = 1

        while True:
            guidelines, total = self.fetch_guideline_list(
                classify1=classify1,
                classify2=classify2,
                page=page,
                start_date=start_date,
                end_date=end_date,
                keyword=keyword,
            )

            if not guidelines:
                break

            all_guidelines.extend(guidelines)

            if len(all_guidelines) >= total:
                break

            if max_pages and page >= max_pages:
                break

            page += 1

        return all_guidelines

    def compare_with_local(
        self,
        guidelines: List[Dict[str, Any]],
        classification: str = "指导原则",
    ) -> Dict[str, List[Dict[str, Any]]]:
        """对比 CDE 列表与本地知识库，返回差异。"""
        session = self._db_conn.get_session()
        try:
            query = session.query(FileInfo).filter(
                FileInfo.classification == classification,
                FileInfo.is_deleted == 0,
            )
            local_files = query.all()

            local_index: Dict[str, Any] = {}
            for file in local_files:
                key = str(file.file_name or "").rsplit(".", 1)[0].strip().lower()
                if key:
                    local_index[key] = file

            new_items = []
            existing_items = []
            updated_items = []

            for guideline in guidelines:
                title = guideline.get("title", "").strip()
                issue_date = guideline.get("issueDate", "").strip()

                if not title:
                    continue

                lookup_key = title.lower()
                existing = None
                for local_key in local_index:
                    if lookup_key in local_key or local_key in lookup_key:
                        existing = local_index[local_key]
                        break

                if not existing:
                    new_items.append(guideline)
                else:
                    local_date = str(existing.registration_path or "").strip()
                    if issue_date and local_date and issue_date != local_date:
                        guideline["local_doc_id"] = existing.doc_id
                        guideline["local_issue_date"] = local_date
                        updated_items.append(guideline)
                    else:
                        guideline["local_doc_id"] = existing.doc_id
                        existing_items.append(guideline)

            return {
                "new": new_items,
                "existing": existing_items,
                "updated": updated_items,
                "total_cde": len(guidelines),
                "total_local": len(local_files),
            }

        finally:
            session.close()

    def close(self):
        """关闭爬虫会话。"""
        self._close_driver()
