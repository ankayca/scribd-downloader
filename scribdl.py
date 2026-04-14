#!/usr/bin/env python3

from bs4 import BeautifulSoup
import img2pdf
import os
from PIL import Image
import requests
import shutil
import sys
import argparse
import re

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


def get_scribd_document(url, images=False):
    """Download Scribd document as images or text and convert images to PDF."""
    response = SESSION.get(url).text
    global TOTAL_PAGES
    TOTAL_PAGES = get_total_pages(url)
    soup = BeautifulSoup(response, "html.parser")

    title = sanitize_title(soup.find("title").get_text())

    page_num = 1
    if images:
        absimg = soup.find_all("img", {"class": "absimg"}, src=True)
        for img in absimg:
            save_image(img["src"], page_num)
            page_num += 1

    js_text = soup.find_all("script", type="text/javascript")
    for opening in js_text:
        script_content = opening.string
        if not script_content:
            continue
        matches = re.findall(r"https://.*?\.jsonp", script_content)
        for jsonp in matches:
            page_num = save_content(jsonp, images, page_num, title)

    if images:
        convert_to_pdf(title)


def command_line():
    args = get_arguments()
    setup_session(args.cookie)
    get_scribd_document(args.url, images=args.images)


if __name__ == "__main__":
    command_line()
