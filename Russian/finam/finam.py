import csv
import re
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    ElementClickInterceptedException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


BASE_URL = "https://www.finam.ru/"
NEWS_URL = "https://www.finam.ru/analysis/united/"


def parse_datetime_from_url(url: str) -> str:
    """
    从 Finam 新闻链接中解析日期时间。
    典型格式: ...-20240702-1943/
    返回格式: YYYY-MM-DD HH:MM，如果解析失败则返回空字符串。
    """
    m = re.search(r"-([0-9]{8})-([0-9]{4})(?:/|$)", url)
    if not m:
        return ""

    yyyymmdd, hhmm = m.groups()
    year = yyyymmdd[0:4]
    month = yyyymmdd[4:6]
    day = yyyymmdd[6:8]
    hour = hhmm[0:2]
    minute = hhmm[2:4]
    return f"{year}-{month}-{day} {hour}:{minute}"


def extract_news_from_page(html_content: str):
    """
    从单页 HTML 中提取新闻标题和日期。
    返回列表，每个元素为 {"date": ..., "title": ...}
    """
    soup = BeautifulSoup(html_content, "html.parser")

    # 匹配 Finam 主站和债券子站的新闻详情链接
    article_href_pattern = re.compile(
        r"/publications/item/|bonds\.finam\.ru/(news|comments)/item/",
        re.IGNORECASE,
    )

    articles = {}

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if not article_href_pattern.search(href):
            continue

        full_url = urljoin(BASE_URL, href)

        # 获取链接文本，清理空白字符
        text = a_tag.get_text(" ", strip=True)
        
        # 如果链接本身没有文本，尝试从父元素或子元素获取
        if not text or len(text) < 5:
            # 尝试从父元素获取文本
            parent = a_tag.parent
            if parent:
                parent_text = parent.get_text(" ", strip=True)
                # 如果父元素文本更长且合理，使用它
                if len(parent_text) > len(text) and len(parent_text) > 10:
                    text = parent_text
        
        # 如果还是没有文本，尝试从 title 属性获取
        if not text or len(text) < 5:
            title_attr = a_tag.get("title", "")
            if title_attr and len(title_attr) > 5:
                text = title_attr
        
        # 如果还是没有文本，跳过（但保留URL用于调试）
        if not text or len(text) < 5:
            # 仍然记录这个URL，但使用URL的一部分作为标题
            text = href.split("/")[-2] if "/" in href else href
        
        # 过滤明显的"читать далее"、"подробнее"等纯导航文本
        # 但如果文本较长（>20字符），即使包含这些词也保留（可能是标题的一部分）
        lower = text.lower()
        if len(text) < 20 and ("читать далее" in lower or "подробнее" in lower or "→" in text or ">" in text):
            # 短文本且包含导航词，跳过
            continue

        # 初始化该 URL 的记录
        info = articles.get(full_url)
        if info is None:
            date_str = parse_datetime_from_url(full_url)
            info = {"url": full_url, "date": date_str, "title": ""}
            articles[full_url] = info

        # 取同一 URL 中最长的文本作为标题（通常是正式标题）
        # 但优先选择看起来更像标题的文本（长度适中，不全是符号）
        if len(text) > len(info["title"]):
            # 如果新文本明显更长，或者旧文本太短，则更新
            if len(text) > len(info["title"]) * 1.2 or len(info["title"]) < 10:
                info["title"] = text

    # 返回所有记录，即使标题可能不完美
    news_list = []
    for info in articles.values():
        if info["title"]:
            news_list.append({"date": info["date"], "title": info["title"]})
        else:
            # 即使没有标题，也记录（使用URL的一部分）
            news_list.append({"date": info["date"], "title": info["url"].split("/")[-2] if "/" in info["url"] else info["url"]})

    return news_list


def scroll_and_load_all_news(driver, max_rounds: int = 60, target_count: int = None):
    """
    使用 Selenium 在动态页面中不断下拉、尝试点击“加载更多”，尽量把新闻都加载出来。
    
    Args:
        driver: Selenium WebDriver 实例
        max_rounds: 最大滚动轮数
        target_count: 目标新闻数量，达到此数量后停止（None表示不限制）
    """
    print("开始滚动页面并加载更多新闻...")
    if target_count:
        print(f"目标：抓取 {target_count} 条新闻")
    
    last_height = driver.execute_script("return document.body.scrollHeight")
    last_news_count = 0
    same_height_count = 0
    wait = WebDriverWait(driver, 10)

    for i in range(max_rounds):
        # 渐进式滚动：先滚动到中间，再到底部
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.7);")
        time.sleep(1)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)

        # 尝试多种方式查找"加载更多"按钮
        load_more_clicked = False
        
        # 方法1: 通过完整文本查找（精确匹配）
        load_more_texts = [
            "Загрузить ещё",
            "Загрузить еще", 
            "Показать ещё",
            "Показать еще",
        ]
        
        for text in load_more_texts:
            try:
                # 尝试通过完整链接文本查找
                btn = driver.find_element(By.LINK_TEXT, text)
                if btn.is_displayed() and btn.is_enabled():
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                    time.sleep(1)
                    # 使用JavaScript点击，避免被拦截
                    driver.execute_script("arguments[0].click();", btn)
                    print(f"  ✓ 通过LINK_TEXT点击了按钮: '{text}'")
                    load_more_clicked = True
                    time.sleep(4)  # 等待新内容加载
                    break
            except (NoSuchElementException, ElementClickInterceptedException):
                continue
        
        # 方法2: 通过部分文本查找
        if not load_more_clicked:
            for text in ["Загрузить", "Показать"]:
                try:
                    btn = driver.find_element(By.PARTIAL_LINK_TEXT, text)
                    btn_text = btn.text.strip()
                    # 确保文本包含关键词
                    if ("загрузить" in btn_text.lower() or "показать" in btn_text.lower()) and btn.is_displayed():
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                        time.sleep(1)
                        driver.execute_script("arguments[0].click();", btn)
                        print(f"  ✓ 通过PARTIAL_LINK_TEXT点击了按钮: '{btn_text}'")
                        load_more_clicked = True
                        time.sleep(4)
                        break
                except (NoSuchElementException, ElementClickInterceptedException):
                    continue
        
        # 方法3: 通过XPath查找包含这些文本的元素（更宽松）
        if not load_more_clicked:
            xpath_patterns = [
                "//a[contains(translate(text(), 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ', 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'), 'загрузить ещё')]",
                "//a[contains(translate(text(), 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ', 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'), 'загрузить еще')]",
                "//a[contains(translate(text(), 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ', 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'), 'показать ещё')]",
                "//a[contains(translate(text(), 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ', 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'), 'показать еще')]",
                "//a[contains(text(), 'Загрузить')]",
                "//a[contains(text(), 'Показать')]",
                "//button[contains(text(), 'Загрузить')]",
                "//button[contains(text(), 'Показать')]",
                "//*[contains(@class, 'load')]//a",
                "//*[contains(@class, 'more')]//a",
                "//*[contains(@id, 'load')]//a",
                "//*[contains(@id, 'more')]//a",
            ]
            for xpath in xpath_patterns:
                try:
                    btn = driver.find_element(By.XPATH, xpath)
                    btn_text = btn.text.strip()
                    if btn.is_displayed() and btn.is_enabled() and len(btn_text) > 0:
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                        time.sleep(1)
                        driver.execute_script("arguments[0].click();", btn)
                        print(f"  ✓ 通过XPath点击了按钮: '{btn_text}' (XPath: {xpath[:50]}...)")
                        load_more_clicked = True
                        time.sleep(4)
                        break
                except (NoSuchElementException, ElementClickInterceptedException):
                    continue
        
        # 方法4: 查找所有可见的链接，检查文本
        if not load_more_clicked:
            try:
                all_links = driver.find_elements(By.TAG_NAME, "a")
                for link in all_links:
                    try:
                        link_text = link.text.strip()
                        link_lower = link_text.lower()
                        if (("загрузить" in link_lower or "показать" in link_lower) and 
                            ("ещё" in link_lower or "еще" in link_lower) and
                            link.is_displayed() and link.is_enabled()):
                            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
                            time.sleep(1)
                            driver.execute_script("arguments[0].click();", link)
                            print(f"  ✓ 通过遍历链接点击了按钮: '{link_text}'")
                            load_more_clicked = True
                            time.sleep(4)
                            break
                    except:
                        continue
            except:
                pass
        
        if not load_more_clicked:
            print(f"  - 第 {i+1} 轮: 未找到'加载更多'按钮")

        # 如果点击了按钮，等待更长时间并检查是否有新内容
        if load_more_clicked:
            # 等待内容加载
            time.sleep(2)
            # 再次滚动到底部，触发可能的懒加载
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
        
        # 检查页面高度是否变化
        new_height = driver.execute_script("return document.body.scrollHeight")
        
        # 统计当前新闻数量（用于判断是否有新内容）
        try:
            current_news = driver.find_elements(By.XPATH, "//a[contains(@href, '/publications/item/')]")
            current_count = len(current_news)
            if current_count > last_news_count:
                print(f"  第 {i+1} 轮: 发现 {current_count} 条新闻链接（新增 {current_count - last_news_count} 条）")
                last_news_count = current_count
                same_height_count = 0
                
                # 检查是否达到目标数量
                if target_count and current_count >= target_count:
                    print(f"  ✓ 已达到目标数量 {target_count} 条，停止加载")
                    break
            elif load_more_clicked:
                # 如果点击了按钮但新闻数量没增加，可能还在加载，再等一会
                time.sleep(2)
                current_news2 = driver.find_elements(By.XPATH, "//a[contains(@href, '/publications/item/')]")
                current_count2 = len(current_news2)
                if current_count2 > current_count:
                    print(f"  第 {i+1} 轮（延迟检测）: 发现 {current_count2} 条新闻链接（新增 {current_count2 - current_count} 条）")
                    last_news_count = current_count2
                    same_height_count = 0
                    
                    # 检查是否达到目标数量
                    if target_count and current_count2 >= target_count:
                        print(f"  ✓ 已达到目标数量 {target_count} 条，停止加载")
                        break
        except Exception as e:
            pass

        if new_height == last_height:
            same_height_count += 1
            if same_height_count >= 5:  # 连续5次高度不变才停止
                print(f"  连续 {same_height_count} 次页面高度未变化，停止加载")
                break
        else:
            same_height_count = 0
            last_height = new_height

    print(f"滚动完成，共进行了 {i+1} 轮")


def save_news_to_csv(news_list, filename: str = "finam.csv"):
    """
    将新闻列表保存到 CSV 文件。
    字段：date, title
    """
    if not news_list:
        print("没有可保存的新闻数据。")
        return

    # 使用 utf-8-sig，方便在 Excel 中直接打开显示中文/俄文
    with open(filename, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "title"])
        writer.writeheader()
        for item in news_list:
            writer.writerow(item)

    print(f"已将 {len(news_list)} 条新闻保存到 {filename}")


def main():
    """
    使用 Selenium 打开 https://www.finam.ru/analysis/united/，
    模拟浏览器不断下拉/点击“Загрузить ещё”，
    把“Новости”栏目下加载出来的所有新闻标题和日期抓取到 finam.csv。
    """
    chrome_options = Options()
    # 如需可视化调试，把下面这行注释掉即可
    # chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--window-size=1400,900")

    # 这里假设已安装 Chrome 浏览器，并且 ChromeDriver 在 PATH 中
    driver = webdriver.Chrome(options=chrome_options)

    try:
        print(f"正在打开页面: {NEWS_URL}")
        driver.get(NEWS_URL)
        
        # 等待页面初始加载完成
        print("等待页面初始加载...")
        time.sleep(8)
        
        # 等待主要内容区域出现
        try:
            wait = WebDriverWait(driver, 15)
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        except:
            pass
        
        # 统计初始新闻数量
        try:
            initial_news = driver.find_elements(By.XPATH, "//a[contains(@href, '/publications/item/')]")
            print(f"初始页面发现 {len(initial_news)} 条新闻链接")
        except:
            pass

        # 尝试查找"加载更多"按钮，看看是否存在
        print("检查是否存在'加载更多'按钮...")
        btn_found = False
        try:
            test_btn = driver.find_element(By.PARTIAL_LINK_TEXT, "Загрузить")
            print(f"  ✓ 找到包含'Загрузить'的按钮/链接: '{test_btn.text.strip()}'")
            btn_found = True
        except:
            try:
                test_btn = driver.find_element(By.PARTIAL_LINK_TEXT, "Показать")
                print(f"  ✓ 找到包含'Показать'的按钮/链接: '{test_btn.text.strip()}'")
                btn_found = True
            except:
                print("  - 未找到明显的'加载更多'按钮，将尝试滚动触发懒加载")
        
        # 不断滚动+尝试点击“加载更多”，目标3000条
        scroll_and_load_all_news(driver, target_count=3000)
        
        # 最后再等待一下，确保所有动态内容都加载完成
        print("等待最终内容加载完成...")
        time.sleep(3)

        html_content = driver.page_source
        
        # 调试：统计页面中的链接数量
        soup_debug = BeautifulSoup(html_content, "html.parser")
        all_links = soup_debug.find_all("a", href=re.compile(r"/publications/item/"))
        print(f"页面中共找到 {len(all_links)} 个 /publications/item/ 链接")
        
        news_list = extract_news_from_page(html_content)
        print(f"从HTML中解析到 {len(news_list)} 条新闻")

        if not news_list:
            print("未从页面中解析到任何新闻，请检查页面结构是否有变化。")
            return

        # 去重（以 date + title 为键）
        seen = set()
        uniq_news = []
        for item in news_list:
            key = (item.get("date", ""), item["title"])
            if key not in seen:
                seen.add(key)
                uniq_news.append(item)

        save_news_to_csv(uniq_news, "finam.csv")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()

