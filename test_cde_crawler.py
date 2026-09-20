"""
CDE 爬虫测试脚本

用于验证：
1. 获取 CDE 列表页面
2. 解析列表数据
3. 获取详情页 PDF 链接
4. 下载 PDF
"""

import requests
from bs4 import BeautifulSoup
import re
import time
from urllib.parse import urljoin, urlparse
import sys
import io

# 设置输出编码为 UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

CDE_BASE_URL = "https://www.cde.org.cn"
CDE_LIST_PAGE_URL = f"{CDE_BASE_URL}/zdyz/listpage/9cd8db3b7530c6fa0c86485e563f93c7"


headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def test_fetch_list_page():
    """测试获取列表页"""
    print("=" * 60)
    print("测试 1: 获取列表页")
    print("=" * 60)

    params = {
        "pageNum": 1,
        "pageSize": 10,
    }

    try:
        response = requests.get(CDE_LIST_PAGE_URL, params=params, headers=headers, timeout=30)
        print(f"URL: {response.url}")
        print(f"状态码: {response.status_code}")
        print(f"内容长度: {len(response.text)} 字符")

        # 检查是否包含指导原则内容
        if "指导原则" in response.text or "指导原則" in response.text:
            print("[OK] 列表页返回了指导原则内容")
        else:
            print("[WARN] 列表页没有返回预期的指导原则内容")

        # 解析列表
        soup = BeautifulSoup(response.text, "html.parser")

        # 查找表格
        table = soup.find("table", {"class": "layui-table"})
        if not table:
            table = soup.find("table")

        if table:
            print(f"[OK] 找到表格")
            tbody = table.find("tbody")
            rows = tbody.find_all("tr") if tbody else table.find_all("tr")[1:]
            print(f"   表格行数: {len(rows)}")

            # 解析第一行
            if rows:
                first_row = rows[0]
                cells = first_row.find_all(["td", "th"])
                print(f"   第一行单元格数: {len(cells)}")

                if len(cells) > 1:
                    title_cell = cells[1]
                    link_elem = title_cell.find("a")
                    if link_elem:
                        print(f"   第一条指导原则: {link_elem.get_text(strip=True)[:50]}...")
                        print(f"   链接: {link_elem.get('href', 'N/A')[:80]}...")
        else:
            print("[WARN] 未找到表格")

        # 检查分页信息
        page_count = soup.find("span", {"class": "layui-laypage-count"})
        if page_count:
            print(f"   分页信息: {page_count.get_text(strip=True)}")

        return response.text

    except Exception as e:
        print(f"[ERROR] 获取列表页失败: {e}")
        return None


def test_parse_list(html_content):
    """测试解析列表数据"""
    print()
    print("=" * 60)
    print("测试 2: 解析列表数据")
    print("=" * 60)

    if not html_content:
        print("[WARN] 无内容可解析")
        return []

    soup = BeautifulSoup(html_content, "html.parser")
    guidelines = []

    # 查找表格
    table = soup.find("table", {"class": "layui-table"}) or soup.find("table")

    if table:
        tbody = table.find("tbody")
        rows = tbody.find_all("tr") if tbody else table.find_all("tr")[1:]

        for i, row in enumerate(rows[:3]):  # 只取前3行
            cells = row.find_all(["td", "th"])
            if len(cells) < 3:
                continue

            title_cell = cells[1] if len(cells) > 1 else None
            status_cell = cells[2] if len(cells) > 2 else None
            date_cell = cells[3] if len(cells) > 3 else None

            if title_cell:
                link_elem = title_cell.find("a")
                if link_elem:
                    title = link_elem.get_text(strip=True)
                    href = link_elem.get("href", "")

                    # 提取 zdyzIdCODE
                    match = re.search(r"zdyzIdCODE=([a-f0-9]+)", href)
                    zdyz_id = match.group(1) if match else "N/A"

                    issue_date = date_cell.get_text(strip=True) if date_cell else "N/A"
                    status = status_cell.get_text(strip=True) if status_cell else "N/A"

                    print(f"\n指导原则 {i+1}:")
                    print(f"  标题: {title[:50]}...")
                    print(f"  zdyzIdCODE: {zdyz_id}")
                    print(f"  发布日期: {issue_date}")
                    print(f"  版本状态: {status}")
                    print(f"  链接: {href[:60]}...")

                    guidelines.append({
                        "title": title,
                        "zdyzIdCODE": zdyz_id,
                        "issueDate": issue_date,
                        "href": href,
                    })

    print(f"\n[OK] 共解析到 {len(guidelines)} 条指导原则")
    return guidelines


def test_fetch_detail(zdyz_id):
    """测试获取详情页"""
    print()
    print("=" * 60)
    print(f"测试 3: 获取详情页 (zdyzIdCODE={zdyz_id})")
    print("=" * 60)

    detail_url = f"{CDE_BASE_URL}/zdyz/domesticinfopage?zdyzIdCODE={zdyz_id}"

    try:
        response = requests.get(detail_url, headers=headers, timeout=30)
        print(f"URL: {response.url}")
        print(f"状态码: {response.status_code}")
        print(f"内容长度: {len(response.text)} 字符")

        soup = BeautifulSoup(response.text, "html.parser")

        # 查找标题
        title_elem = soup.find("h1") or soup.find("h2")
        if title_elem:
            print(f"[OK] 标题: {title_elem.get_text(strip=True)[:60]}...")

        # 查找 PDF 链接
        pdf_links = []
        for link in soup.find_all("a"):
            href = link.get("href", "")
            text = link.get_text(strip=True)
            if href.endswith(".pdf") or ".pdf" in href.lower() or "pdf" in text.lower():
                if not href.startswith("http"):
                    href = urljoin(CDE_BASE_URL, href)
                pdf_links.append({
                    "href": href,
                    "text": text[:30] if text else "N/A",
                })

        # 查找 onclick 事件
        for elem in soup.find_all(onclick=True):
            onclick = elem.get("onclick", "")
            if "pdf" in onclick.lower() or "download" in onclick.lower():
                print(f"   发现 onclick: {onclick[:80]}...")

        if pdf_links:
            print(f"[OK] 发现 {len(pdf_links)} 个 PDF 链接:")
            for i, pdf in enumerate(pdf_links[:3]):
                print(f"   {i+1}. {pdf['text']}: {pdf['href'][:60]}...")
            return pdf_links[0]["href"] if pdf_links else None
        else:
            print("[WARN] 未找到 PDF 链接")
            # 打印部分 HTML 用于调试
            print("\n   查找 a 标签示例:")
            for link in soup.find_all("a")[:5]:
                href = link.get("href", "")
                text = link.get_text(strip=True)
                print(f"     - {text[:30]} | {href[:60]}")
            return None

    except Exception as e:
        print(f"[ERROR] 获取详情页失败: {e}")
        return None


def test_download_pdf(pdf_url, output_path="test_download.pdf"):
    """测试下载 PDF"""
    print()
    print("=" * 60)
    print(f"测试 4: 下载 PDF")
    print("=" * 60)

    if not pdf_url:
        print("[WARN] 没有 PDF URL")
        return False

    try:
        print(f"下载 URL: {pdf_url[:60]}...")
        response = requests.get(pdf_url, headers=headers, stream=True, timeout=30)

        print(f"状态码: {response.status_code}")

        if response.status_code == 200:
            content_type = response.headers.get("Content-Type", "unknown")
            content_length = len(response.content)

            print(f"Content-Type: {content_type}")
            print(f"内容大小: {content_length / 1024:.1f} KB")

            # 保存文件
            with open(output_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            print(f"[OK] PDF 已保存到: {output_path}")
            return True
        else:
            print(f"[ERROR] 下载失败: HTTP {response.status_code}")
            return False

    except Exception as e:
        print(f"[ERROR] 下载失败: {e}")
        return False


if __name__ == "__main__":
    print("")
    print("*" * 60)
    print("* CDE 爬虫测试")
    print("*" * 60)

    # 测试1: 获取列表页
    html_content = test_fetch_list_page()

    if html_content:
        # 测试2: 解析列表
        guidelines = test_parse_list(html_content)

        if guidelines:
            # 测试3: 获取第一条的详情页
            first_id = guidelines[0]["zdyzIdCODE"]
            pdf_url = test_fetch_detail(first_id)

            # 测试4: 下载 PDF
            if pdf_url:
                test_download_pdf(pdf_url)

    print()
    print("*" * 60)
    print("* 测试完成")
    print("*" * 60)
