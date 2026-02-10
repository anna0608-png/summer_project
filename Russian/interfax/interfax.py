# -*- coding: utf-8 -*-
"""
Interfax 经济新闻爬虫：爬取 https://www.interfax.ru/business/ 上的新闻标题及日期。
通过自动滚动并点击「Загрузить еще новости」加载更多，最多 10000 条，保存为 interfax.csv。
"""

import csv
import re
import time
import html

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, ElementClickInterceptedException
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


BASE_URL = "https://www.interfax.ru"
NEWS_URL = "https://www.interfax.ru/business/"
MAX_ITEMS = 10000
OUTPUT_CSV = "interfax.csv"

# 新闻链接路径前缀（相对路径或完整 URL，该页面 timeline 中出现的栏目）
NEWS_HREF_PATTERN = re.compile(
    r"^(?:https?://[^/]+)?/(business|world|russia|moscow|digital|news|culture)/\d+",
    re.I,
)


def extract_news_from_page(html_content: str):
    """
    从单页 HTML 中提取新闻日期和标题。
    页面结构：.timeline 内每个 <time datetime="..."> 与同块内的 <a href="/section/id"><h3>标题</h3></a> 成对。
    「加载更多」可能把新内容插入为多个 div.timeline，故需遍历所有 timeline 块。
    返回列表 [{"date": "YYYY-MM-DD HH:MM", "title": "..."}, ...]
    """
    soup = BeautifulSoup(html_content, "html.parser")
    # 页面上可能有多个 div.timeline（首屏一块，「加载更多」再插入多块），全部解析
    timelines = soup.find_all("div", class_=re.compile(r"^timeline$"))
    if not timelines:
        return []

    result = []
    for timeline in timelines:
        for time_el in timeline.find_all("time", datetime=True):
            parent = time_el.parent
            if not parent:
                continue
            a = parent.find("a", href=NEWS_HREF_PATTERN)
            if not a:
                continue
            datetime_val = time_el.get("datetime", "").strip()
            if not datetime_val:
                continue
            date_str = datetime_val.replace("T", " ")[:16]
            title = a.get("title") or (a.find("h3") and a.find("h3").get_text(" ", strip=True)) or ""
            title = html.unescape(title).strip()
            if not title:
                continue
            result.append({"date": date_str, "title": title})

    return result


def scroll_and_click_load_more(driver, max_clicks: int = 500, target_count: int = MAX_ITEMS):
    """
    滚动到「Загрузить еще новости」并反复点击，每轮加载后立即从当前 DOM 解析并合并新闻，
    直到已收集条数达到 target_count 或无法再加载。返回去重后的新闻列表。
    （页面可能对旧内容做虚拟化/回收，最终 page_source 里只有少量条目，故必须在每轮增量解析。）
    """
    print("开始滚动并点击「Загрузить еще новости」加载更多...")
    if target_count:
        print(f"目标：最多抓取 {target_count} 条新闻")

    collected = []  # 按出现顺序
    seen = set()    # (date, title) 用于去重
    no_new_count = 0

    for round_num in range(max_clicks):
        # 先滚动到底部，使按钮进入视口
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.5)

        load_more_clicked = False

        # 方法1：通过 class 查找按钮（div.timeline__more）
        try:
            btn = driver.find_element(By.CSS_SELECTOR, "div.timeline__more")
            if btn.is_displayed() and btn.is_enabled():
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                time.sleep(0.8)
                driver.execute_script("arguments[0].click();", btn)
                load_more_clicked = True
                print(f"  第 {round_num + 1} 轮: 已点击「Загрузить еще новости」")
        except NoSuchElementException:
            pass

        # 方法2：通过文本查找
        if not load_more_clicked:
            try:
                btn = driver.find_element(By.XPATH, "//div[contains(., 'Загрузить еще новости')]")
                if "timeline__more" in (btn.get_attribute("class") or ""):
                    if btn.is_displayed():
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                        time.sleep(0.8)
                        driver.execute_script("arguments[0].click();", btn)
                        load_more_clicked = True
                        print(f"  第 {round_num + 1} 轮: 已点击加载更多（按文本）")
            except NoSuchElementException:
                pass

        if load_more_clicked:
            time.sleep(2.5)
        else:
            no_new_count += 1
            if no_new_count >= 3:
                print("  连续多轮未找到加载按钮，结束加载")
                break

        # 每轮都从当前 DOM 解析并合并（避免虚拟滚动导致最终只剩少量条目）
        try:
            html_content = driver.page_source
            chunk = extract_news_from_page(html_content)
            added = 0
            for item in chunk:
                key = (item["date"], item["title"])
                if key not in seen:
                    seen.add(key)
                    collected.append(item)
                    added += 1
            if chunk:
                print(f"  当前已收集 {len(collected)} 条（本轮解析 {len(chunk)} 条，新增 {added} 条）")
            if added > 0:
                no_new_count = 0
        except Exception as e:
            print(f"  本轮解析异常: {e}")

        if target_count and len(collected) >= target_count:
            print(f"  已收集 {len(collected)} 条，达到目标，停止加载")
            break

    print("加载阶段结束")
    return collected


def save_to_csv(news_list: list, filename: str = OUTPUT_CSV):
    """将新闻列表保存为 CSV，表头 date, title，UTF-8-BOM 便于 Excel 打开。"""
    if not news_list:
        print("没有可保存的新闻。")
        return
    with open(filename, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "title"])
        writer.writeheader()
        for row in news_list:
            writer.writerow(row)
    print(f"已保存 {len(news_list)} 条到 {filename}")


def main():
    options = Options()
    options.add_argument("--window-size=1400,900")
    # 如需无头运行，取消下一行注释
    # options.add_argument("--headless=new")

    driver = webdriver.Chrome(options=options)

    try:
        print(f"打开: {NEWS_URL}")
        driver.get(NEWS_URL)

        print("等待页面加载...")
        time.sleep(5)

        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.timeline"))
            )
        except Exception:
            pass

        collected = scroll_and_click_load_more(driver, max_clicks=500, target_count=MAX_ITEMS)

        # 已在循环中去重；超过上限时只保留前 MAX_ITEMS 条（按出现顺序）
        unique = collected[:MAX_ITEMS] if len(collected) > MAX_ITEMS else collected
        if len(collected) > MAX_ITEMS:
            print(f"已截断为前 {MAX_ITEMS} 条")

        print(f"共收集 {len(unique)} 条新闻")
        save_to_csv(unique, OUTPUT_CSV)

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
