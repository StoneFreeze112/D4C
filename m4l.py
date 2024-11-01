import re
import aiohttp
import asyncio
import aiofiles
from pathlib import Path
import argparse
import logging
import sys
from colorama import init, Fore, Style

# Initialize Colorama
init(autoreset=True)

# Set up logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]"
)

class MangaDownloader:
    def __init__(self, manga_name: str, uppercase: bool = False, edit: bool = False):
        """
        Initialize the manga downloader with a formatted manga name and create a folder for it.
        """
        if edit:
            self.manga_name = manga_name
        else:
            self.manga_name = manga_name.upper() if uppercase else manga_name.title()

        # Replace spaces with hyphens
        self.formatted_manga_name = re.sub(r'\s+', '-', self.manga_name)

        # Create a default MANGA folder
        self.manga_folder = Path("MANGA") / self.formatted_manga_name
        self.manga_folder.mkdir(parents=True, exist_ok=True)  # Ensure the folder exists
        self.history_file = Path("download_history.txt")

    def format_chapter_number(self, chapter_number: str) -> str:
        """
        Format the chapter number to a specific string format.
        """
        if '.' in chapter_number:
            integer_part, decimal_part = chapter_number.split('.')
            formatted_chapter_number = f"{int(integer_part):04d}.{decimal_part}"
        else:
            formatted_chapter_number = f"{int(chapter_number):04d}"
        return formatted_chapter_number

    async def generate_image_url(self, chapter_number: str, png_number: int, manga_address: str) -> str:
        """
        Generate the URL for a specific image in a chapter.
        """
        base_url = f"https://{manga_address}/manga/{{}}/{{}}-{{:03d}}.png"
        url = base_url.format(self.formatted_manga_name, chapter_number, png_number)
        return url

    async def download_image(self, session: aiohttp.ClientSession, url: str, path: Path) -> bool:
        """
        Download a single image from the specified URL and save it to the given path.
        """
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    async with aiofiles.open(path, 'wb') as file:
                        await file.write(await response.read())
                    return True
                else:
                    logging.warning(f"Failed to download {url}: Status {response.status}")
                    return False
        except aiohttp.ClientError as e:
            logging.error(f"Client error while downloading {url}: {e}")
            return False

    def extract_text_from_html(self, html_content: str) -> str:
        """
        Extract the manga address from the HTML content.
        """
        pattern = re.compile(r'vm\.CurPathName\s*=\s*"([^"]+)"')
        matches = pattern.findall(html_content)
        return matches[0] if matches else None

    async def extract_text_from_url(self, session: aiohttp.ClientSession, chapter_number: str) -> str:
        """
        Extract the manga address by requesting the chapter's HTML page.
        """
        formatted_chapter_number = self.format_chapter_number(chapter_number)
        url = f"https://manga4life.com/read-online/{self.formatted_manga_name}-chapter-{formatted_chapter_number}.html"
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    html_content = await response.text()
                    manga_address = self.extract_text_from_html(html_content)
                    if not manga_address:
                        # Try an alternative URL if the first one fails
                        url = f"https://manga4life.com/read-online/{self.formatted_manga_name}-chapter-{formatted_chapter_number}-index-2.html"
                        async with session.get(url) as alt_response:
                            if alt_response.status == 200:
                                html_content = await alt_response.text()
                                manga_address = self.extract_text_from_html(html_content)
                    return manga_address
                else:
                    logging.warning(f"Failed to extract URL for chapter {chapter_number}: Status {response.status}")
                    return None
        except aiohttp.ClientError as e:
            logging.error(f"Client error while extracting URL for chapter {chapter_number}: {e}")
            return None

    async def count_pages_in_chapter(self, session: aiohttp.ClientSession, chapter_number: str) -> int:
        """
        Count the number of pages in a chapter by generating image URLs and checking their existence.
        """
        formatted_chapter_number = self.format_chapter_number(chapter_number)
        manga_address = await self.extract_text_from_url(session, formatted_chapter_number)
        if manga_address:
            page_count = 0
            png_number = 1
            while True:
                url = await self.generate_image_url(formatted_chapter_number, png_number, manga_address)
                async with session.get(url) as response:
                    if response.status == 200:
                        page_count += 1
                    else:
                        break
                png_number += 1
            return page_count
        return 0

    async def colorful_progress_bar(self, current: int, total: int):
        """
        Display a colorful progress bar in the console.
        """
        current = min(current, total)
        percent = (current / total) * 100
        bar_length = 50  # Length of the progress bar
        filled_length = int(bar_length * current // total)

        bar = '█' * filled_length + '-' * (bar_length - filled_length)

        if percent < 50:
            color = Fore.RED
        elif percent < 80:
            color = Fore.YELLOW
        else:
            color = Fore.GREEN

        sys.stdout.write(f'\r{color}[{bar}] {percent:.2f}%{Style.RESET_ALL}')
        sys.stdout.flush()

    async def download_chapter_images(self, session: aiohttp.ClientSession, chapter_number: str, total_pages: int, chapter_index: int) -> bool:
        """
        Download all images for a specific chapter and update the progress bar.
        """
        formatted_chapter_number = self.format_chapter_number(chapter_number)
        manga_address = await self.extract_text_from_url(session, formatted_chapter_number)
        if manga_address:
            chapter_folder = self.manga_folder / f"Chapter-{formatted_chapter_number}"
            chapter_folder.mkdir(parents=True, exist_ok=True)

            for png_number in range(1, total_pages + 1):
                url = await self.generate_image_url(formatted_chapter_number, png_number, manga_address)
                image_filename = f"{png_number:03d}.png"
                image_path = chapter_folder / image_filename
                if await self.download_image(session, url, image_path):
                    overall_progress = (chapter_index * total_pages) + png_number
                    await self.colorful_progress_bar(overall_progress, total_chapters_pages)
            return True
        else:
            logging.warning(f"Failed to download images for chapter {chapter_number}. Manga address not found.")
            return False

    async def download_chapters(self, chapters_to_download: list, max_connections: int = 10):
        """
        Download the specified chapters and their images.
        """
        chapter_count = len(chapters_to_download)
        logging.info(f"There are {chapter_count} chapters to download.")

        global total_chapters_pages
        total_chapters_pages = 0
        pages_per_chapter = {}
        async with aiohttp.ClientSession() as session:
            for chapter_number in chapters_to_download:
                page_count = await self.count_pages_in_chapter(session, chapter_number)
                pages_per_chapter[chapter_number] = page_count
                total_chapters_pages += page_count

        for chapter_number, page_count in pages_per_chapter.items():
            logging.info(f"Chapter {chapter_number} has {page_count} pages.")

        user_input = input("Do you want to proceed with the download? (Y/N): ").strip().upper()

        if user_input != 'Y':
            logging.info("Download canceled by user.")
            return

        conn = aiohttp.TCPConnector(limit=max_connections)
        async with aiohttp.ClientSession(connector=conn) as session:
            for chapter_index, chapter_number in enumerate(chapters_to_download):
                total_pages = pages_per_chapter[chapter_number]
                await self.download_chapter_images(session, chapter_number, total_pages, chapter_index)

        # Force the progress bar to 100% at the end
        await self.colorful_progress_bar(total_chapters_pages, total_chapters_pages)
        print()  # Move to the next line after the progress bar is complete

        await self.save_history(self.manga_name)

    async def save_history(self, manga_name: str):
        """
        Save the downloaded manga name to the download history file.
        """
        if not self.history_file.exists():
            self.history_file.touch()
        async with aiofiles.open(self.history_file, 'a+') as file:
            await file.seek(0)
            history = await file.read()
            history_lines = history.splitlines()
            if manga_name not in history_lines:
                await file.write(manga_name + "\n")
                logging.info(f"Saved {manga_name} to history.")

    async def load_history(self):
        """
        Load and display the download history.
        """
        if self.history_file.exists():
            async with aiofiles.open(self.history_file, 'r') as file:
                history = await file.read()
                if history:
                    print("Download History:")
                    print(history)
                else:
                    print("No download history found.")
        else:
            print("No history file found.")

def main():
    """
    Main function to parse arguments and start the manga downloader.
    """
    parser = argparse.ArgumentParser(description="Manga Downloader")
    parser.add_argument('manga_name', type=str, help='The name of the manga to download')
    parser.add_argument('--edit', action='store_true', help='Edit manga name for previously downloaded manga')
    parser.add_argument('--uppercase', action='store_true', help='Use uppercase for manga name')
    parser.add_argument('--max-connections', type=int, default=10, help='Maximum concurrent downloads')
    parser.add_argument('--load-history', action='store_true', help='Load download history')

    args = parser.parse_args()
    
    downloader = MangaDownloader(args.manga_name, args.uppercase, args.edit)

    if args.load_history:
        asyncio.run(downloader.load_history())
        return

    chapters_to_download = input("Enter chapter numbers to download, separated by commas: ").split(',')
    chapters_to_download = [ch.strip() for ch in chapters_to_download if ch.strip()]

    # Start the download process
    asyncio.run(downloader.download_chapters(chapters_to_download, args.max_connections))

if __name__ == "__main__":
    main()
