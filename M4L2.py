import re
import aiohttp
import asyncio
import aiofiles
from pathlib import Path
import argparse
import logging
from rich.progress import Progress  # Import the rich progress bar

# Set up logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]"
)

class MangaDownloader:
    def __init__(self, manga_name: str, uppercase: bool = False, edit: bool = False):
        if edit:
            self.manga_name = manga_name
        else:
            self.manga_name = manga_name.upper() if uppercase else manga_name.title()
        self.formatted_manga_name = re.sub(r'\s+', '-', self.manga_name)

        # Create a default MANGA folder
        self.manga_folder = Path("MANGA") / self.formatted_manga_name
        self.manga_folder.mkdir(parents=True, exist_ok=True)  # Ensure the folder exists
        self.history_file = Path("download_history.txt")

    def format_chapter_number(self, chapter_number: str) -> str:
        if '.' in chapter_number:
            integer_part, decimal_part = chapter_number.split('.')
            formatted_chapter_number = f"{int(integer_part):04d}.{decimal_part}"
        else:
            formatted_chapter_number = f"{int(chapter_number):04d}"
        return formatted_chapter_number

    async def generate_image_url(self, chapter_number: str, png_number: int, manga_address: str) -> str:
        base_url = f"https://{manga_address}/manga/{{}}/{{}}-{{:03d}}.png"
        url = base_url.format(self.formatted_manga_name, chapter_number, png_number)
        return url

    async def download_image(self, session: aiohttp.ClientSession, url: str, path: Path) -> bool:
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    async with aiofiles.open(path, 'wb') as file:
                        await file.write(await response.read())
                    logging.info(f"Downloaded: {url}")
                    return True
                else:
                    logging.warning(f"Failed to download {url}: {response.status}")
                    return False
        except aiohttp.ClientError as e:
            logging.error(f"Error downloading {url}: {e}")
            return False

    def extract_text_from_html(self, html_content: str) -> str:
        pattern = re.compile(r'vm\.CurPathName\s*=\s*"([^"]+)"')
        matches = pattern.findall(html_content)
        return matches[0] if matches else None

    async def extract_text_from_url(self, session: aiohttp.ClientSession, chapter_number: str) -> str:
        formatted_chapter_number = self.format_chapter_number(chapter_number)
        url = f"https://manga4life.com/read-online/{self.formatted_manga_name}-chapter-{formatted_chapter_number}.html"
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    html_content = await response.text()
                    manga_address = self.extract_text_from_html(html_content)
                    if not manga_address:
                        logging.warning(f"Could not find 'vm.CurPathName' for manga '{self.manga_name}', chapter '{formatted_chapter_number}'.")
                        url = f"https://manga4life.com/read-online/{self.formatted_manga_name}-chapter-{formatted_chapter_number}-index-2.html"
                        async with session.get(url) as alt_response:
                            if alt_response.status == 200:
                                html_content = await alt_response.text()
                                manga_address = self.extract_text_from_html(html_content)
                                if not manga_address:
                                    logging.warning(f"Alternative URL also failed for '{self.manga_name}', chapter '{formatted_chapter_number}'.")
                    return manga_address
                else:
                    logging.error(f"Error accessing {url}: HTTP {response.status}")
                    return None
        except aiohttp.ClientError as e:
            logging.error(f"Error accessing {url}: {e}")
            return None

    async def count_pages_in_chapter(self, session: aiohttp.ClientSession, chapter_number: str) -> int:
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

    async def download_chapter_images(self, session: aiohttp.ClientSession, chapter_number: str) -> bool:
        formatted_chapter_number = self.format_chapter_number(chapter_number)
        manga_address = await self.extract_text_from_url(session, formatted_chapter_number)
        if manga_address:
            chapter_folder = self.manga_folder / f"Chapter-{formatted_chapter_number}"
            chapter_folder.mkdir(parents=True, exist_ok=True)  # Ensure the chapter folder exists

            # Count the total number of pages for the progress bar
            png_number = 1
            total_pages = 0
            while True:
                url = await self.generate_image_url(formatted_chapter_number, png_number, manga_address)
                async with session.get(url) as response:
                    if response.status == 200:
                        total_pages += 1
                    else:
                        break
                png_number += 1
            
            # Download images with a rich progress bar
            with Progress() as progress:
                task = progress.add_task(f"[blue]Downloading Chapter {formatted_chapter_number}", total=total_pages)
                png_number = 1
                while png_number <= total_pages:
                    url = await self.generate_image_url(formatted_chapter_number, png_number, manga_address)
                    image_filename = f"{png_number:03d}.png"
                    image_path = chapter_folder / image_filename
                    if await self.download_image(session, url, image_path):
                        progress.update(task, advance=1)  # Update the progress bar
                    png_number += 1
            return total_pages > 0
        else:
            return False

    async def download_chapters(self, chapters_to_download: list):
        # New: Ask for confirmation before starting downloads
        chapter_count = len(chapters_to_download)
        logging.info(f"There are {chapter_count} chapters to download.")
        
        # Count pages per chapter
        pages_per_chapter = {}
        async with aiohttp.ClientSession() as session:
            for chapter_number in chapters_to_download:
                page_count = await self.count_pages_in_chapter(session, chapter_number)
                pages_per_chapter[chapter_number] = page_count

        for chapter_number, page_count in pages_per_chapter.items():
            logging.info(f"Chapter {chapter_number} has {page_count} pages.")

        user_input = input("Do you want to proceed with the download? (Y/N): ").strip().upper()

        if user_input != 'Y':
            logging.info("Download canceled by user.")
            return  # Exit the method if the user does not confirm

        conn = aiohttp.TCPConnector(limit=10)
        async with aiohttp.ClientSession(connector=conn) as session:
            tasks = []
            for chapter_number in chapters_to_download:
                tasks.append(self.download_chapter_images(session, chapter_number))
                if len(tasks) >= 5:  # Limit concurrent tasks to avoid overloading
                    await asyncio.gather(*tasks)
                    tasks = []
            if tasks:
                await asyncio.gather(*tasks)
        await self.save_history(self.manga_name)

    async def save_history(self, manga_name: str):
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
        if self.history_file.exists():
            async with aiofiles.open(self.history_file, 'r') as file:
                history = await file.read()
                history_lines = history.splitlines()
                logging.info("Download History:")
                for manga in history_lines:
                    logging.info(manga)
        else:
            logging.info("No download history found.")

def parse_chapters(chapters_str):
    chapters = []
    for part in chapters_str.split(','):
        if '-' in part:
            start, end = map(int, part.split('-'))
            chapters.extend(range(start, end + 1))
        else:
            chapters.append(int(part))
    return [str(chapter) for chapter in chapters]

def parse_args():
    parser = argparse.ArgumentParser(description="Manga Downloader")
    parser.add_argument('-d', '--download', metavar='MANGA_NAME', type=str, help="Download manga chapters")
    parser.add_argument('-c', '--chapters', metavar='CHAPTERS', type=str, help="Chapters to download, e.g., 1,2-5")
    parser.add_argument('--history', action='store_true', help="Show download history")  # Updated line
    parser.add_argument('-u', '--uppercase', action='store_true', help="Format manga name to uppercase")
    parser.add_argument('-e', '--edit', action='store_true', help="Edit manga name instead of formatting")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.download and args.chapters:
        manga_name = args.download
        input_chapters = args.chapters
        chapters_to_download = parse_chapters(input_chapters)
        downloader = MangaDownloader(manga_name, uppercase=args.uppercase, edit=args.edit)
        asyncio.run(downloader.download_chapters(chapters_to_download))
    elif args.history:
        downloader = MangaDownloader("SampleManga", edit=True)  # Dummy manga name to instantiate
        asyncio.run(downloader.load_history())
    else:
        print("Invalid arguments. Use -h for help.")

if __name__ == "__main__":
    main()
