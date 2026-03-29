# -*- coding: utf-8 -*-
"""
Отладка Orgeo через Playwright: перехватываем ВСЕ HTTP-ответы
и сохраняем те, в которых уже есть таблица с результатами
(ищем по словам 'Фамилия' или '<table').
"""

from pathlib import Path
from playwright.sync_api import sync_playwright

# СЮДА подставь свою ссылку Orgeo (ту же, что открываешь в браузере)
URL = "https://orgeo.ru/online/49864"


def main() -> None:
    out_dir = Path(r"C:\Users\andre\PycharmProjects\DjangoProject\orgeo_responses")
    out_dir.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            page = browser.new_page()

            def handle_response(response):
                try:
                    url = response.url
                    ctype = response.headers.get("content-type", "")
                    # Нас интересуют только текстовые/HTML-ответы с orgeo
                    if "orgeo.ru" not in url:
                        return
                    if "text/html" not in ctype and "text/plain" not in ctype:
                        return
                    body = response.text()
                    # Ищем признаки таблицы протокола
                    if "Фамилия" in body or "<table" in body:
                        safe_name = (
                            url.replace("https://", "")
                            .replace("http://", "")
                            .replace("/", "_")
                            .replace("?", "_")
                            .replace("&", "_")
                            .replace("=", "-")
                        )
                        path = out_dir / f"{safe_name}.html"
                        path.write_text(body, encoding="utf-8")
                        print(f"Сохранил ответ с таблицей: {url} -> {path}")
                except Exception:
                    # Для отладки нам достаточно того, что хоть что-то сохранится
                    pass

            page.on("response", handle_response)
            page.goto(URL, wait_until="domcontentloaded")
            # Немного подождём, чтобы все запросы успели отработать
            page.wait_for_timeout(8000)

            print(f"Готово. Посмотри файлы в папке: {out_dir}")
        finally:
            browser.close()


if __name__ == "__main__":
    main()