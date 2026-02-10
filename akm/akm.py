from bs4 import BeautifulSoup
import csv
import re
import requests
from urllib.parse import urljoin


BASE_URL = "https://www.akm.ru"
START_URL = "https://www.akm.ru/news/"


def get_akm_news(html_content):
    """
    从 AKM 新闻列表 HTML 内容中提取新闻日期和标题。
    返回列表，每个元素为字典：{"date": ..., "title": ...}
    """
    soup = BeautifulSoup(html_content, "html.parser")
    news_list = []

    # 所有新闻块都使用 b-section-item 类（包括主列表和右侧“热门”）
    items = soup.find_all("div", class_=re.compile(r"\bb-section-item\b"))

    for item in items:
        # 标题在 h3.b-section-item__title > a 中
        title_tag = item.find("h3", class_="b-section-item__title")
        if not title_tag:
            continue
        link = title_tag.find("a")
        if not link:
            continue
        title = link.get_text(strip=True)
        if not title:
            continue

        # 日期在 meta 区块中：div.b-section-item__meta 里的最后一个 span
        date_text = ""
        meta = item.find("div", class_="b-section-item__meta")
        if meta:
            spans = meta.find_all("span")
            if spans:
                # 最后一个 span 是完整的日期时间，例如 “05 февраля 2026 12:18”
                date_text = spans[-1].get_text(strip=True)

        # 只保存有日期和标题的记录（如果你希望无日期也保留，可去掉 date_text 判断）
        if title and date_text:
            news_list.append({"date": date_text, "title": title})

    return news_list


def find_next_page_url(html_content, current_url):
    """
    从 HTML 中找到“加载更多（Загрузить еще）”按钮的链接，返回下一页的绝对 URL。
    如果找不到，则返回 None。
    """
    soup = BeautifulSoup(html_content, "html.parser")

    # 按 ID 包含 "_loadmore" 来找按钮（例如 section_681133c_loadmore）
    load_more_link = soup.find("a", id=re.compile(r"_loadmore"))
    if not load_more_link:
        # 兜底：根据按钮文字中的 “Загрузить еще” 文字查找
        load_more_link = soup.find("a", string=re.compile(r"Загрузить еще", re.IGNORECASE))

    if not load_more_link:
        return None

    href = load_more_link.get("href")
    if not href:
        return None

    # 转成绝对 URL
    return urljoin(current_url, href)


def save_news_to_csv(news_list, filename="akm.csv"):
    """
    将新闻列表保存到 CSV 文件。
    字段：date, title
    """
    if not news_list:
        print("没有可保存的新闻数据。")
        return

    # 使用 utf-8-sig 方便在 Excel 中打开时正常显示俄文
    with open(filename, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "title"])
        writer.writeheader()
        for item in news_list:
            writer.writerow(item)

    print(f"已将 {len(news_list)} 条新闻保存到 {filename}")


def main():
    """
    在线从 https://www.akm.ru/news/ 连续翻页抓取新闻，并保存到 akm.csv。
    """
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        }
    )

    all_news = []
    seen = set()  # 用于去重 (date, title)

    current_url = START_URL
    page_num = 1
    max_pages = 50  # 安全上限，避免死循环

    while current_url and page_num <= max_pages:
        print(f"正在抓取第 {page_num} 页: {current_url}")

        try:
            resp = session.get(current_url, timeout=15)
            resp.raise_for_status()
            resp.encoding = "utf-8"
        except Exception as e:
            print(f"请求 {current_url} 失败：{e}")
            break

        html_content = resp.text

        page_news = get_akm_news(html_content)
        if not page_news:
            print("本页未解析到任何新闻，停止。")
            break

        added_this_page = 0
        for item in page_news:
            key = (item["date"], item["title"])
            if key not in seen:
                seen.add(key)
                all_news.append(item)
                added_this_page += 1

        print(f"本页新增 {added_this_page} 条新闻，累计 {len(all_news)} 条。")

        # 查找下一页链接（“加载更多”按钮）
        next_url = find_next_page_url(html_content, current_url)
        if not next_url:
            print("未找到更多页链接，抓取结束。")
            break

        current_url = next_url
        page_num += 1

    if not all_news:
        print("未抓取到任何新闻。")
        return

    save_news_to_csv(all_news, "akm.csv")


if __name__ == "__main__":
    main()

