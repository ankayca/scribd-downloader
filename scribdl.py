#!/usr/bin/env python3

from bs4 import BeautifulSoup
import img2pdf
import os
from PIL import Image
from playwright.sync_api import sync_playwright
import requests
import shutil
import sys
import argparse
import re
import time

IMAGES = []
SESSION = requests.Session()

IMAGES_DIR = "scribd/images"
PDF_DIR = "scribd"


def ensure_dirs():
    os.makedirs(IMAGES_DIR, exist_ok=True)
    os.makedirs(PDF_DIR, exist_ok=True)


def get_arguments():
    parser = argparse.ArgumentParser(
        description="Download documents or text from Scribd"
    )

    parser.add_argument(
        "url", metavar="URL", type=str, help="Scribd document URL to download"
    )
    parser.add_argument(
        "-i",
        "--images",
        help="Download document as images",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "-c",
        "--cookie",
        help="Browser cookie string for authenticated access",
        type=str,
        default=None,
    )

    parser.add_argument(
        "--from-page",
        help="Start screenshotting from this page number (inclusive)",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--to-page",
        help="Stop screenshotting at this page number (inclusive)",
        type=int,
        default=None,
    )

    return parser.parse_args()


def fix_encoding(query):
    if sys.version_info > (3, 0):
        return query
    else:
        return query.encode("utf-8")


def setup_session(cookie=None):
    """Configure the shared session with browser headers and optional cookies."""
    SESSION.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.scribd.com/",
    })
    if cookie:
        SESSION.headers["Cookie"] = cookie


def get_total_pages(url):
    response = SESSION.get(url).text
    soup = BeautifulSoup(response, "html.parser")
    span = soup.find("span", {"data-e2e": "total-pages"})
    if span:
        total_pages = span.get_text().replace("/", "").strip()
        return int(total_pages)
    return None


def extract_image_url_from_jsonp(jsonp_url):
    """Fetch a jsonp file and extract the absimg src URL from it."""
    response = SESSION.get(jsonp_url).text
    # Strip the jsonp callback wrapper: window.pageN_callback(["..."])
    match = re.search(r'_callback\(\["(.*)"\]\)', response, re.DOTALL)
    if not match:
        return None
    html_content = match.group(1).replace("\\n", "\n").replace('\\"', '"').replace("\\/", "/")
    soup = BeautifulSoup(html_content, "html.parser")
    # Images use "orig" attribute in jsonp, not "src"
    img = soup.find("img", {"class": "absimg"})
    if img:
        url = img.get("orig") or img.get("src")
        if url:
            # Convert http to https
            return url.replace("http://html.scribd.com", "https://html.scribdassets.com")
    return None


def save_image(content, page_num, found=False):
    """
    Download an image and save it in IMAGES_DIR.
    Overwrites existing images if present.
    """
    global IMAGES
    ensure_dirs()
    image_path = os.path.join(IMAGES_DIR, f"{page_num}.jpg")

    if os.path.exists(image_path):
        os.remove(image_path)

    if content.endswith(".jsonp"):
        # Parse jsonp to find the real image URL from the absimg tag
        replacement = extract_image_url_from_jsonp(content)
        if not replacement:
            # Fallback to old URL transformation
            replacement = content.replace("/pages/", "/images/")
            if found:
                replacement = replacement.replace(".jsonp", "/000.jpg")
            else:
                replacement = replacement.replace(".jsonp", ".jpg")
    else:
        replacement = content

    response = SESSION.get(replacement, stream=True)
    with open(image_path, "wb") as out_file:
        shutil.copyfileobj(response.raw, out_file)

    try:
        with Image.open(image_path) as img:
            img.verify()
        IMAGES.append(image_path)
        print(f"Downloaded image {page_num}/{TOTAL_PAGES}")
    except Exception:
        os.remove(image_path)
        print(f"Skipped image {page_num}/{TOTAL_PAGES} (invalid/access denied)")


def save_text(jsonp, filename):
    """Extract text from .jsonp and append to a text file."""
    response = SESSION.get(jsonp).text
    page_no = response[11:12]
    response_head = (
        response.replace(f'window.page{page_no}_callback(["', "")
        .replace("\\n", "")
        .replace("\\", "")
        .replace('"]);', "")
    )
    soup_content = BeautifulSoup(response_head, "html.parser")

    with open(filename, "a", encoding="utf-8") as feed:
        for x in soup_content.find_all("span", {"class": "a"}):
            feed.write(f"{fix_encoding(x.get_text())}\n")


def save_content(content, images, page_num, title, found=False):
    """Save either image or text content."""
    if content:
        if images:
            save_image(content, page_num, found)
        else:
            save_text(content, f"{title}.txt")
        page_num += 1
    return page_num


def sanitize_title(title):
    forbidden_chars = r" *\"/\\<>:|(),"
    for ch in forbidden_chars:
        title = title.replace(ch, "_")
    return title


def convert_to_pdf(title):
    """Convert images in IMAGES_DIR to PDF in PDF_DIR."""
    ensure_dirs()
    if not IMAGES:
        return

    sorted_images = sorted(
        IMAGES, key=lambda x: int(os.path.splitext(os.path.basename(x))[0])
    )
    pdf_path = os.path.join(PDF_DIR, f"{title}.pdf")
    with open(pdf_path, "wb") as f:
        f.write(img2pdf.convert(sorted_images))
    print(f"Saved PDF: {pdf_path}")


def screenshot_pages(url, title, from_page=None, to_page=None):
    """Use Playwright to screenshot each page with text and images rendered."""
    global IMAGES
    ensure_dirs()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 1920, "height": 1080},
            device_scale_factor=2,
        )
        print("Loading document in browser...")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)

        # Scroll through to load all pages
        outer_pages = page.query_selector_all(".outer_page")
        total = len(outer_pages)
        print(f"Found {total} pages, scrolling to load all...")

        for i in range(total * 2):
            page.evaluate("window.scrollBy(0, 800)")
            time.sleep(0.3)

        # Wait for images to finish loading
        time.sleep(2)

        # Hide fixed/sticky UI overlays (navbar, reader toolbar, ads) so they
        # don't visually bleed into element screenshots. Scribd's JS re-shows
        # these overlays (e.g. the Download/Find toolbar) as the page scrolls,
        # so a one-shot hide isn't enough: we install a stylesheet rule with
        # !important (which beats Scribd's inline styles) and re-tag overlays
        # before every screenshot via hide_overlays().
        page.add_style_tag(content="""
            [data-scribd-hide="1"] { visibility: hidden !important; }
        """)

        def hide_overlays():
            page.evaluate("""
                () => {
                    document.querySelectorAll('*').forEach(el => {
                        const pos = window.getComputedStyle(el).position;
                        if (pos === 'fixed' || pos === 'sticky') {
                            el.setAttribute('data-scribd-hide', '1');
                        }
                    });
                }
            """)

        hide_overlays()

        # Screenshot each page
        newpages = page.query_selector_all(".newpage")
        print(f"Screenshotting {len(newpages)} pages...")

        for i, pg in enumerate(newpages, 1):
            if from_page and i < from_page:
                continue
            if to_page and i > to_page:
                break

            image_path = os.path.join(IMAGES_DIR, f"{i}.jpg")
            png_path = os.path.join(IMAGES_DIR, f"{i}.png")

            try:
                # Use a short timeout — some .newpage elements are hidden placeholders
                pg.scroll_into_view_if_needed(timeout=5000)
            except Exception:
                print(f"Skipped page {i}/{len(newpages)} (not visible)")
                continue
            time.sleep(0.3)

            # Re-hide overlays at the current scroll position: Scribd re-shows
            # its reader toolbar after scrolling, so this must run per page.
            hide_overlays()

            try:
                # Use pg.screenshot() directly — it captures the exact element bounding
                # box correctly accounting for CSS transforms, without the
                # viewport-relative coordinate mismatch that page.screenshot(clip=bbox)
                # suffers from (bounding_box() returns viewport-relative y, but
                # page.screenshot clip expects page-absolute y, causing height clipping).
                pg.screenshot(path=png_path)
            except Exception as e:
                print(f"Skipped page {i}/{len(newpages)} (screenshot failed: {e})")
                continue

            # Convert PNG to JPEG for img2pdf compatibility
            with Image.open(png_path) as img:
                img.convert("RGB").save(image_path, "JPEG", quality=95)
            os.remove(png_path)

            IMAGES.append(image_path)
            print(f"Captured page {i}/{len(newpages)}")

        browser.close()

    convert_to_pdf(title)


def get_scribd_document(url, images=False, args=None):
    """Download Scribd document as images or text and convert images to PDF."""
    response = SESSION.get(url).text
    global TOTAL_PAGES
    TOTAL_PAGES = get_total_pages(url)
    soup = BeautifulSoup(response, "html.parser")

    # Use the URL slug as the file name
    title = sanitize_title(url.rstrip("/").split("/")[-1])

    if images:
        screenshot_pages(url, title, from_page=args.from_page, to_page=args.to_page)
        return

    page_num = 1
    js_text = soup.find_all("script", type="text/javascript")
    for opening in js_text:
        script_content = opening.string
        if not script_content:
            continue
        matches = re.findall(r"https://.*?\.jsonp", script_content)
        for jsonp in matches:
            page_num = save_content(jsonp, False, page_num, title)


def command_line():
    args = get_arguments()
    setup_session(args.cookie)
    get_scribd_document(args.url, images=args.images, args=args)


if __name__ == "__main__":
    command_line()
