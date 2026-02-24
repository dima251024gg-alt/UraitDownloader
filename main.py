import os
import re
import shutil
import logging
import asyncio
from asyncio import Semaphore

from aiohttp import ClientSession, ClientTimeout
from pypdf import PdfWriter
from svglib.svglib import svg2rlg
from reportlab.graphics import renderPDF
from win11toast import toast
import environ


# ---------------- CONFIG ----------------

env = environ.Env()
environ.Env.read_env("account.txt")

EMAIL = env("URAIT_EMAIL")
PASSWORD = env("URAIT_PASSWORD")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://urait.ru/",
}


# ---------------- HELPERS ----------------

def svg_to_pdf(svg_path: str, pdf_path: str):
    drawing = svg2rlg(svg_path)
    renderPDF.drawToFile(drawing, pdf_path)


async def fetch(session, url, sem):
    async with sem:
        async with session.get(url) as r:
            return r.status, await r.text(), r.headers.get("Content-Type", "")


# ---------------- CORE ----------------

async def login(session, sem):
    async with sem:
        r = await session.post(
            "https://urait.ru/login",
            json={"email": EMAIL, "password": PASSWORD},
        )
        text = await r.text()
        if "Неверный пароль" in text:
            raise RuntimeError("Неверный пароль")
        if "не зарегистрирован" in text:
            raise RuntimeError("Пользователь не найден")


async def get_book_info(url, session, sem):
    async with sem:
        r = await session.get(url)
        html = await r.text()

    pages = int(re.search(r'book-about-produce__info">(\d+)<', html).group(1))
    title = re.search(r'book_title">(.+?)<', html).group(1)

    r = await session.get(url.replace("/book/", "/viewer/"))
    viewer = await r.text()
    code = re.search(r"Viewer\('(.+?)'", viewer).group(1)

    return pages, title, code


async def load_page(page, code, session, net_sem, cpu_sem, ok, fail):
    url = f"https://urait.ru/viewer/page/{code}/{page}"

    try:
        status, text, ctype = await fetch(session, url, net_sem)
        if status != 200 or "<svg" not in text.lower():
            raise RuntimeError("Not SVG")

        os.makedirs("temp", exist_ok=True)
        svg = f"temp/{page}.svg"
        pdf = f"temp/{page}.pdf"

        with open(svg, "w", encoding="utf-8") as f:
            f.write(text)

        async with cpu_sem:
            await asyncio.to_thread(svg_to_pdf, svg, pdf)

        ok.append(page)

    except Exception as e:
        fail.append(page)
        logging.warning(f"Page {page} failed: {e}")


# ---------------- MAIN ----------------

async def main():
    logging.basicConfig(level=logging.INFO)

    url = input("Вставь ссылку на книгу:\n").strip()

    ok_pages = []
    fail_pages = []

    net_sem = Semaphore(4)
    cpu_sem = Semaphore(2)

    timeout = ClientTimeout(total=None)

    try:
        async with ClientSession(headers=DEFAULT_HEADERS, timeout=timeout) as session:
            await login(session, net_sem)

            pages, title, code = await get_book_info(url, session, net_sem)

            tasks = [
                load_page(p, code, session, net_sem, cpu_sem, ok_pages, fail_pages)
                for p in range(1, pages)
            ]

            done = 0
            for coro in asyncio.as_completed(tasks):
                await coro
                done += 1
                print(
                    f"\rГотово: {done}/{pages-1} | OK: {len(ok_pages)} | FAIL: {len(fail_pages)}",
                    end=""
                )
            print()

            if not ok_pages:
                raise RuntimeError("Ни одна страница не скачалась")

            ok_pages.sort()
            pdf = PdfWriter()
            for p in ok_pages:
                pdf.append(f"temp/{p}.pdf")

            pdf.write(f"{title}.pdf")
            pdf.close()

            toast("Книга скачана")

    finally:
        shutil.rmtree("temp", ignore_errors=True)
        input("\nНажми Enter для выхода")


if __name__ == "__main__":
    asyncio.run(main())