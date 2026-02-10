# -*- coding: utf-8 -*-
"""
Ведомости 经济栏目爬虫：爬取 https://www.vedomosti.ru/economics 上
「Также в рубрике」下方的所有新闻标题及日期。通过自动滚动并点击「Показать еще」
加载更多，最多 10000 条，保存为 vedomosti_economics.csv。
"""

import csv
import re
import time
import html as html_module

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, ElementClickInterceptedException
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


NEWS_URL = "https://www.vedomosti.ru/economics"
MAX_ITEMS = 10000
OUTPUT_CSV = "vedomosti_economics.csv"

# 页面日期格式 DD.MM.YYYY，转换为 YYYY-MM-DD
DATE_DOT_PATTERN = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")


def dot_date_to_iso(dot_date: str) -> str:
    """将 DD.MM.YYYY 转为 YYYY-MM-DD。"""
    m = DATE_DOT_PATTERN.match(dot_date.strip())
    if not m:
        return dot_date
    d, mon, y = m.groups()
    return f"{y}-{mon}-{d}"


def extract_news_from_page(html_content: str, debug: bool = False):
    """
    从当前页 HTML 中提取新闻日期和标题。
    使用两种策略：
    1. 从 time 元素找链接
    2. 从链接找最近的 time 元素（用于处理新加载的内容）
    返回列表 [{"date": "YYYY-MM-DD", "title": "..."}, ...]
    """
    soup = BeautifulSoup(html_content, "html.parser")
    result = []
    seen_keys = set()  # 用于去重

    # 策略1：从 time 元素找链接
    all_times = soup.find_all("time")
    skipped_no_date = 0
    skipped_no_parent = 0
    skipped_no_link = 0
    skipped_bad_href = 0
    skipped_no_title = 0

    for time_el in all_times:
        date_text = (time_el.get("datetime") or time_el.get_text(strip=True) or "").strip()
        if not date_text:
            skipped_no_date += 1
            continue
        # 兼容 datetime 格式 YYYY-MM-DD 或文本 DD.MM.YYYY
        if re.match(r"^\d{4}-\d{2}-\d{2}", date_text):
            date_iso = date_text[:10]
        elif DATE_DOT_PATTERN.match(date_text):
            date_iso = dot_date_to_iso(date_text)
        else:
            skipped_no_date += 1
            continue

        parent = time_el.parent
        if not parent:
            skipped_no_parent += 1
            continue

        # 向上查找包含 card-news-item 或 card-mobile-news 的父元素
        card = parent.find_parent(class_=re.compile(r"card-news-item|card-mobile-news|article-preview-item"))
        if not card:
            card = parent.find_parent(class_=re.compile(r"card"))
            if not card:
                card = parent

        # 尝试多种方式查找标题链接
        a = None
        # 方法1：title 容器
        title_div = card.find("div", class_=re.compile(r"card-news-item__title|article-preview-item__title"))
        if title_div:
            a = title_div.find("a", href=True)
        # 方法2：card 内任意链接
        if not a:
            a = card.find("a", href=True)
        # 方法3：在上层祖先中找链接
        if not a:
            for ancestor in [parent, getattr(parent, "parent", None), getattr(getattr(parent, "parent", None), "parent", None)]:
                if not ancestor:
                    continue
                a = ancestor.find("a", href=True)
                if a:
                    break

        if not a:
            skipped_no_link += 1
            continue

        href = a.get("href", "") or ""
        if not href:
            skipped_bad_href += 1
            continue

        # 经济栏目：优先 economics，其次 generic 新闻路径
        is_news_link = (
            "/economics/" in href
            or "/news/" in href
            or "/articles/" in href
            or (href.startswith("/") and len(href) > 10 and not any(x in href for x in ["#", "javascript:", "mailto:", "tel:"]))
        )
        if not is_news_link:
            skipped_bad_href += 1
            continue

        title = a.get_text(" ", strip=True) or (a.get("title") or "")
        title = html_module.unescape(title).strip()
        if not title or len(title) < 2:
            skipped_no_title += 1
            continue

        key = (date_iso, title)
        if key not in seen_keys:
            seen_keys.add(key)
            result.append({"date": date_iso, "title": title})

    # 策略2：从链接找最近的 time 元素（适配「Также в рубрике」块）
    econ_links = soup.find_all("a", href=re.compile(r"/economics/"))
    strategy2_count = 0

    for a in econ_links:
        href = a.get("href", "") or ""
        if not href or any(x in href for x in ["#", "javascript:", "mailto:", "tel:"]):
            continue

        title = a.get_text(" ", strip=True) or (a.get("title") or "")
        title = html_module.unescape(title).strip()
        if not title or len(title) < 2:
            continue

        # 向上查找最近的 time
        date_iso = None
        current = a.parent
        max_levels = 10
        level = 0

        while current is not None and level < max_levels:
            time_el = current.find("time")
            if time_el:
                date_text = (time_el.get("datetime") or time_el.get_text(strip=True) or "").strip()
                if date_text:
                    if re.match(r"^\d{4}-\d{2}-\d{2}", date_text):
                        date_iso = date_text[:10]
                    elif DATE_DOT_PATTERN.match(date_text):
                        date_iso = dot_date_to_iso(date_text)
                    if date_iso:
                        break
            current = current.parent
            level += 1

        if date_iso:
            key = (date_iso, title)
            if key not in seen_keys:
                seen_keys.add(key)
                result.append({"date": date_iso, "title": title})
                strategy2_count += 1

    if debug:
        print(f"    解析调试: 共 {len(all_times)} 个time元素, 解析出 {len(result)} 条")
        print(
            f"    策略1跳过: 无日期={skipped_no_date}, 无父级={skipped_no_parent}, "
            f"无链接={skipped_no_link}, 链接不符合={skipped_bad_href}, 无标题={skipped_no_title}"
        )
        print(f"    策略2（从链接找日期）: 找到 {strategy2_count} 条")

    return result


def scroll_and_click_load_more(driver, max_clicks: int = 500, target_count: int = MAX_ITEMS):
    """
    滚动到「Показать еще」并反复点击，每轮从当前 DOM 解析并合并新闻，
    直到已收集条数达到 target_count 或无法再加载。返回去重后的新闻列表。
    """
    print("开始滚动并点击「Показать еще」加载更多...")
    if target_count:
        print(f"目标：最多抓取 {target_count} 条新闻")

    collected = []
    seen = set()
    no_click_count = 0

    for round_num in range(max_clicks):
        # 渐进式滚动：先滚动到中间，再到底部
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.7);")
        time.sleep(1)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)

        load_more_clicked = False

        # 方法0：通过 class 查找按钮
        try:
            btn = driver.find_element(By.CSS_SELECTOR, "button.articles-preview-list__button")
            if btn.is_displayed() and btn.is_enabled():
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                time.sleep(0.8)
                driver.execute_script("arguments[0].click();", btn)
                load_more_clicked = True
                print(f"  第 {round_num + 1} 轮: 已点击「Показать еще」（通过 class 找到）")
        except NoSuchElementException:
            pass

        # 方法1：链接文本
        for link_text in ["Показать еще", "Показать ещё"]:
            try:
                btn = driver.find_element(By.LINK_TEXT, link_text)
                if btn.is_displayed() and btn.is_enabled():
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                    time.sleep(0.8)
                    driver.execute_script("arguments[0].click();", btn)
                    load_more_clicked = True
                    print(f"  第 {round_num + 1} 轮: 已点击「{link_text}」")
                    break
            except NoSuchElementException:
                pass
            if load_more_clicked:
                break

        # 方法2：部分链接文本
        if not load_more_clicked:
            try:
                btn = driver.find_element(By.PARTIAL_LINK_TEXT, "Показать")
                t = (btn.text or "").strip()
                if ("еще" in t.lower() or "ещё" in t.lower()) and btn.is_displayed():
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                    time.sleep(0.8)
                    driver.execute_script("arguments[0].click();", btn)
                    load_more_clicked = True
                    print(f"  第 {round_num + 1} 轮: 已点击「Показать...」")
            except NoSuchElementException:
                pass

        # 方法3：XPath
        if not load_more_clicked:
            for xpath in [
                "//button[contains(@class, 'articles-preview-list__button')]",
                "//button[contains(@class, 'articles-preview-list__button')]//span[contains(text(), 'Показать еще')]",
                "//button[contains(@class, 'articles-preview-list__button')]//span[contains(text(), 'Показать ещё')]",
                "//button[contains(translate(., 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ', 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'), 'показать еще')]",
                "//a[contains(translate(text(), 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ', 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'), 'показать еще')]",
                "//a[contains(translate(text(), 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ', 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'), 'показать ещё')]",
                "//*[contains(@class, 'show-more')]//a",
                "//*[contains(@class, 'card-list__show-more')]",
            ]:
                try:
                    btn = driver.find_element(By.XPATH, xpath)
                    if btn.is_displayed() and btn.is_enabled():
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                        time.sleep(0.8)
                        driver.execute_script("arguments[0].click();", btn)
                        load_more_clicked = True
                        print(f"  第 {round_num + 1} 轮: 已点击「Показать еще」（XPath）")
                        break
                except NoSuchElementException:
                    pass

        # 方法4：遍历所有 button 和 a
        if not load_more_clicked:
            try:
                all_buttons = driver.find_elements(By.TAG_NAME, "button")
                for btn in all_buttons:
                    try:
                        t = (btn.text or "").strip()
                        t_lower = t.lower()
                        if ("показать" in t_lower and ("еще" in t_lower or "ещё" in t_lower)) and btn.is_displayed():
                            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                            time.sleep(0.8)
                            driver.execute_script("arguments[0].click();", btn)
                            load_more_clicked = True
                            print(f"  第 {round_num + 1} 轮: 已点击「{t}」（遍历按钮）")
                            break
                    except Exception:
                        continue

                if not load_more_clicked:
                    all_links = driver.find_elements(By.TAG_NAME, "a")
                    for a in all_links:
                        try:
                            t = (a.text or "").strip()
                            t_lower = t.lower()
                            if ("показать" in t_lower and ("еще" in t_lower or "ещё" in t_lower)) and a.is_displayed():
                                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", a)
                                time.sleep(0.8)
                                driver.execute_script("arguments[0].click();", a)
                                load_more_clicked = True
                                print(f"  第 {round_num + 1} 轮: 已点击「{t}」（遍历链接）")
                                break
                        except Exception:
                            continue
            except Exception:
                pass

        if load_more_clicked:
            before_height = driver.execute_script("return document.body.scrollHeight")
            time.sleep(2)
            after_height = driver.execute_script("return document.body.scrollHeight")
            if after_height < before_height * 0.5:
                print(f"  警告：页面高度从 {before_height} 降到 {after_height}，可能页面被重置，重新滚动到底部")
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)

            # 触发虚拟滚动渲染
            for _ in range(3):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1)
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.9);")
                time.sleep(0.5)
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1)

            try:
                WebDriverWait(driver, 5).until(lambda d: len(d.find_elements(By.CSS_SELECTOR, "time")) > 0)
            except Exception:
                pass
            time.sleep(1.5)
            no_click_count = 0
        else:
            no_click_count += 1
            if no_click_count >= 3:
                print("  连续多轮未找到「Показать еще」，结束加载")
                break

        # 每轮从当前 DOM 解析并合并
        try:
            time_elements = driver.find_elements(By.CSS_SELECTOR, "time")
            time_count = len(time_elements)

            html_content = driver.page_source
            debug_parse = (round_num == 0)
            chunk = extract_news_from_page(html_content, debug=debug_parse)
            added = 0
            for item in chunk:
                key = (item["date"], item["title"])
                if key not in seen:
                    seen.add(key)
                    collected.append(item)
                    added += 1

            if chunk:
                print(
                    f"  当前已收集 {len(collected)} 条（页面有 {time_count} 个 time 元素，"
                    f"解析出 {len(chunk)} 条，新增 {added} 条）"
                )

            if len(collected) > 50 and time_count < len(collected) * 0.3 and len(chunk) < len(collected) * 0.3:
                print(
                    f"  检测到页面可能被重置：time元素({time_count})和解析数量({len(chunk)})"
                    f"都远少于已收集数量({len(collected)})，停止继续加载"
                )
                break

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
    # options.add_argument("--headless=new")

    driver = webdriver.Chrome(options=options)

    try:
        print(f"打开: {NEWS_URL}")
        driver.get(NEWS_URL)

        print("等待页面加载...")
        time.sleep(5)

        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "time.card-news-item__date"))
            )
        except Exception:
            pass

        collected = scroll_and_click_load_more(driver, max_clicks=500, target_count=MAX_ITEMS)

        unique = collected[:MAX_ITEMS] if len(collected) > MAX_ITEMS else collected
        if len(collected) > MAX_ITEMS:
            print(f"已截断为前 {MAX_ITEMS} 条")

        print(f"共收集 {len(unique)} 条新闻")
        save_to_csv(unique, OUTPUT_CSV)

    finally:
        driver.quit()


if __name__ == "__main__":
    main()

