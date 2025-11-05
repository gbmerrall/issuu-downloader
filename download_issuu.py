#!/usr/bin/env python3
"""
Issuu PDF Downloader

A command-line tool to download and convert Issuu publications to PDF format
with preserved text layout. Supports batched processing, smart resume on
interruption, and automatic retry of failed downloads.

This tool fetches publication metadata from Issuu's reader4.json API, downloads
SVG files for each page, converts them to PDF using cairosvg, and merges them
incrementally into a single output PDF. Progress is tracked after each batch,
allowing interrupted downloads to resume seamlessly.

Key Features:
    - Smart resume capability with progress tracking
    - Automatic retry logic with exponential backoff
    - Text-preserving SVG to PDF conversion
    - Memory-efficient batch processing
    - Comprehensive error handling and reporting

Example:
    Basic usage to download a publication:

        $ python download_issuu.py "https://issuu.com/user/docs/document" output.pdf

    Resume an interrupted download:

        $ python download_issuu.py "https://issuu.com/user/docs/document" output.pdf

    Force a fresh start with custom batch size:

        $ python download_issuu.py --clean --batch-size 20 \\
            "https://issuu.com/user/docs/document" output.pdf

For more information, see README.md
"""
import argparse
import json
import time
import random
import base64
from io import BytesIO
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import httpx
from pypdf import PdfWriter, PdfReader
from pypdf.errors import PdfReadError
import cairosvg
from PIL import Image
from PIL import UnidentifiedImageError
from lxml import etree

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
    'sec-ch-ua': '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
}


# Utility Functions
def ensure_directory(path: Path) -> None:
    """Create directory if it doesn't exist."""
    path.mkdir(parents=True, exist_ok=True)


def file_exists_with_content(path: Path) -> bool:
    """Check if file exists and has content."""
    return path.exists() and path.stat().st_size > 0


def sanitize_filename(name: str) -> str:
    """Sanitize filename by removing path separators and dangerous characters."""
    # Remove path separators and null bytes
    dangerous_chars = ['/', '\\', '\0', '..']
    sanitized = name
    for char in dangerous_chars:
        sanitized = sanitized.replace(char, '_')
    # Also remove any leading/trailing dots or spaces
    sanitized = sanitized.strip('. ')
    # Ensure it's not empty
    if not sanitized:
        sanitized = "document"
    return sanitized


def parse_issuu_url(url: str) -> Tuple[str, str]:
    """
    Extract username and document name from Issuu URL.

    Args:
        url: Issuu publication URL

    Returns:
        Tuple of (username, document_name)

    Raises:
        ValueError: If URL is not a valid Issuu URL

    Example:
        >>> parse_issuu_url("https://issuu.com/alliance12/docs/report_2023")
        ('alliance12', 'report_2023')
    """
    import re

    # Match pattern: https://issuu.com/{username}/docs/{document_name}
    pattern = r'https?://issuu\.com/([^/]+)/docs/([^/?]+)'
    match = re.match(pattern, url)

    if not match:
        raise ValueError(f"Invalid Issuu URL format: {url}")

    return match.group(1), match.group(2)


def fetch_reader_json(username: str, doc_name: str, client: httpx.Client, timeout: int = 30) -> Dict:
    """
    Fetch reader4.json metadata from Issuu.

    Args:
        username: Issuu username
        doc_name: Document name
        client: httpx.Client instance for connection pooling
        timeout: Request timeout in seconds

    Returns:
        Parsed JSON metadata

    Raises:
        Exception: If request fails or metadata cannot be fetched
    """
    url = f"https://publication.issuu.com/{username}/{doc_name}/reader4.json"

    try:
        response = client.get(url, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        raise Exception(f"Failed to fetch metadata: {e}")
    except Exception as e:
        raise Exception(f"Unexpected error fetching metadata: {e}")


def extract_svg_urls(metadata: Dict) -> List[Tuple[int, str]]:
    """
    Extract SVG URLs from reader JSON metadata.

    Args:
        metadata: Parsed reader4.json

    Returns:
        List of (page_number, svg_url) tuples, sorted by page number

    Raises:
        KeyError: If expected structure is missing
    """
    pages = metadata["document"]["pages"]
    svg_urls = []

    for idx, page in enumerate(pages):
        # Page number is 1-indexed (idx + 1)
        page_num = idx + 1
        # Based on Task 3 info: the field is svgUrl
        svg_url = page.get("svgUrl", "")

        if svg_url:
            svg_urls.append((page_num, svg_url))

    return sorted(svg_urls, key=lambda x: x[0])


def load_progress(cache_dir: Path) -> Dict:
    """
    Load progress state from cache directory.

    Args:
        cache_dir: Cache directory path

    Returns:
        Progress dictionary with completed_batches, failed_pages, etc.
    """
    progress_file = cache_dir / "progress.json"

    if progress_file.exists():
        with open(progress_file, 'r') as f:
            return json.load(f)

    # Default progress structure
    return {
        "total_pages": 0,
        "completed_batches": [],
        "failed_pages": [],
        "last_successful_batch": 0
    }


def save_progress(cache_dir: Path, progress: Dict) -> None:
    """
    Save progress state to cache directory.

    Args:
        cache_dir: Cache directory path
        progress: Progress dictionary to save
    """
    ensure_directory(cache_dir)
    progress_file = cache_dir / "progress.json"

    with open(progress_file, 'w') as f:
        json.dump(progress, indent=2, fp=f)


def download_svg_with_retry(url: str, output_path: Path, client: httpx.Client, max_attempts: int = 3) -> bool:
    """
    Download SVG file with retry logic and validation.

    Args:
        url: SVG URL to download
        output_path: Where to save the SVG file
        client: httpx.Client instance for connection pooling
        max_attempts: Maximum retry attempts

    Returns:
        True if successful, False otherwise
    """
    ensure_directory(output_path.parent)

    for attempt in range(1, max_attempts + 1):
        try:
            response = client.get(url, timeout=30)
            response.raise_for_status()

            # Validate content length
            expected_size = int(response.headers.get('content-length', 0))

            # Write to disk
            with open(output_path, 'wb') as f:
                f.write(response.content)

            # Verify file size (only check for empty files, not exact match
            # because content-length may be compressed size)
            actual_size = output_path.stat().st_size

            if actual_size == 0:
                raise ValueError("Downloaded file is empty")

            return True

        except httpx.TimeoutException as e:
            print(f"  Attempt {attempt}/{max_attempts} failed (timeout): {e}")
            print(f"  URL: {url}")

            if attempt < max_attempts:
                delay = 2 ** (attempt - 1)  # Exponential backoff: 1s, 2s, 4s
                time.sleep(delay)
            else:
                return False

        except httpx.HTTPError as e:
            print(f"  Attempt {attempt}/{max_attempts} failed (network): {e}")
            print(f"  URL: {url}")

            if attempt < max_attempts:
                delay = 2 ** (attempt - 1)  # Exponential backoff: 1s, 2s, 4s
                time.sleep(delay)
            else:
                return False

        except Exception as e:
            print(f"  Attempt {attempt}/{max_attempts} failed: {e}")
            print(f"  URL: {url}")

            if attempt < max_attempts:
                delay = 2 ** (attempt - 1)  # Exponential backoff: 1s, 2s, 4s
                time.sleep(delay)
            else:
                return False

    return False


def process_svg_images(
    svg_path: Path,
    jpeg_quality: Optional[int] = None,
    image_scale: float = 1.0
) -> bool:
    """
    Process JPEG images embedded in SVG file.

    Decodes base64-encoded JPEG images, resizes them if needed, re-encodes
    with specified quality, and updates the SVG file.

    Args:
        svg_path: Path to SVG file to process
        jpeg_quality: JPEG quality (1-100), None to preserve original
        image_scale: Scale factor for images (1.0 = original size)

    Returns:
        True if successful, False otherwise
    """
    try:
        # Parse SVG with lxml
        tree = etree.parse(str(svg_path))
        root = tree.getroot()

        # Define namespaces
        namespaces = {
            'svg': 'http://www.w3.org/2000/svg',
            'xlink': 'http://www.w3.org/1999/xlink'
        }

        # Find all image elements using XPath
        # Try both with and without namespace prefix
        image_elements = root.xpath('//svg:image', namespaces=namespaces)
        if not image_elements:
            # Fallback: try without namespace
            image_elements = root.xpath('//image')

        if not image_elements:
            # No images found, nothing to process
            return True

        processed_count = 0

        # Process each image element
        for img_elem in image_elements:
            try:
                # Check both href attributes (with and without xlink namespace)
                href_value = None
                href_attr = None

                # Try xlink:href first (more common in SVG)
                xlink_href = img_elem.get('{http://www.w3.org/1999/xlink}href')
                if xlink_href:
                    href_value = xlink_href
                    href_attr = '{http://www.w3.org/1999/xlink}href'
                else:
                    # Try plain href
                    plain_href = img_elem.get('href')
                    if plain_href:
                        href_value = plain_href
                        href_attr = 'href'

                if not href_value:
                    # No href found, skip this image
                    continue

                # Check if this is a base64 JPEG data URI
                # Support both with and without charset parameter
                if not (href_value.startswith('data:image/jpeg;charset=utf-8;base64,') or
                        href_value.startswith('data:image/jpeg;base64,')):
                    # Not a JPEG data URI, skip
                    continue

                # Extract base64 data (everything after "base64,")
                base64_start = href_value.index('base64,') + 7
                old_base64 = href_value[base64_start:]

                # Decode from base64
                jpeg_data = base64.b64decode(old_base64)

                # Load image with PIL
                img = Image.open(BytesIO(jpeg_data))

                # Resize if needed
                if image_scale != 1.0:
                    new_width = int(img.width * image_scale)
                    new_height = int(img.height * image_scale)

                    # Use LANCZOS for high-quality downsampling/upsampling
                    img = img.resize((new_width, new_height), Image.LANCZOS)

                # Re-encode as JPEG
                output_buffer = BytesIO()

                if jpeg_quality is not None:
                    img.save(output_buffer, format='JPEG', quality=jpeg_quality, optimize=True)
                else:
                    # Preserve original quality
                    img.save(output_buffer, format='JPEG')

                # Re-encode to base64
                new_jpeg_data = output_buffer.getvalue()
                new_base64 = base64.b64encode(new_jpeg_data).decode('ascii')

                # Update the href attribute with new base64 data
                new_href = f'data:image/jpeg;charset=utf-8;base64,{new_base64}'
                img_elem.set(href_attr, new_href)

                processed_count += 1

            except Exception as e:
                print(f"    Warning: Failed to process one image: {e}")
                # Continue with other images
                continue

        # Write modified SVG back to file
        tree.write(
            str(svg_path),
            encoding='utf-8',
            xml_declaration=True,
            pretty_print=False
        )

        return True

    except (UnidentifiedImageError, ValueError) as e:
        print(f"  Warning: Failed to process image in {svg_path.name}: {e}")
        return False
    except Exception as e:
        print(f"  Warning: Unexpected error processing {svg_path.name}: {e}")
        return False


def convert_svg_to_pdf(svg_path: Path, pdf_path: Path) -> bool:
    """
    Convert SVG file to PDF.

    Args:
        svg_path: Path to input SVG file
        pdf_path: Path to output PDF file

    Returns:
        True if successful, False otherwise
    """
    ensure_directory(pdf_path.parent)

    try:
        cairosvg.svg2pdf(url=str(svg_path), write_to=str(pdf_path))

        # Verify PDF was created and has content
        if not file_exists_with_content(pdf_path):
            raise ValueError("PDF file is empty or not created")

        return True

    except Exception as e:
        print(f"  Conversion failed for {svg_path.name}: {e}")
        return False


def merge_pdfs(pdf_paths: List[Path], output_path: Path, compress: bool = False) -> bool:
    """
    Merge multiple PDF files into one.

    Args:
        pdf_paths: List of PDF files to merge (in order)
        output_path: Path to output merged PDF
        compress: Whether to apply PDF compression

    Returns:
        True if successful, False otherwise
    """
    try:
        writer = PdfWriter()

        for pdf_path in pdf_paths:
            if not pdf_path.exists():
                print(f"  Warning: {pdf_path} does not exist, skipping")
                continue

            reader = PdfReader(str(pdf_path))
            for page in reader.pages:
                writer.add_page(page)

        # Apply compression if requested
        if compress:
            for page in writer.pages:
                page.compress_content_streams()

        with open(output_path, 'wb') as f:
            writer.write(f)

        return True

    except PdfReadError as e:
        print(f"  Merge failed (corrupt PDF): {e}")
        return False
    except Exception as e:
        print(f"  Merge failed: {e}")
        return False


def append_pdfs_to_output(pdf_paths: List[Path], output_path: Path, compress: bool = False) -> bool:
    """
    Append PDFs to existing output file (for incremental merge).

    Args:
        pdf_paths: List of PDF files to append
        output_path: Path to output merged PDF (may or may not exist)
        compress: Whether to apply PDF compression

    Returns:
        True if successful, False otherwise
    """
    try:
        writer = PdfWriter()

        # If output exists, read existing pages first
        if output_path.exists():
            existing_reader = PdfReader(str(output_path))
            for page in existing_reader.pages:
                writer.add_page(page)

        # Add new pages
        for pdf_path in pdf_paths:
            if not pdf_path.exists():
                print(f"  Warning: {pdf_path} does not exist, skipping")
                continue

            reader = PdfReader(str(pdf_path))
            for page in reader.pages:
                writer.add_page(page)

        # Apply compression if requested
        if compress:
            for page in writer.pages:
                page.compress_content_streams()

        # Write back to output
        with open(output_path, 'wb') as f:
            writer.write(f)

        return True

    except PdfReadError as e:
        print(f"  Append failed (corrupt PDF): {e}")
        return False
    except Exception as e:
        print(f"  Append failed: {e}")
        return False


def download_batch_svgs(
    pages: List[Tuple[int, str]],
    svgs_dir: Path,
    client: httpx.Client,
    delay: float
) -> List[int]:
    """Download SVG files for a batch of pages.

    Returns:
        List of failed page numbers
    """
    failed_pages = []

    for page_num, svg_url in pages:
        svg_path = svgs_dir / f"page_{page_num:04d}.svg"

        # Skip if already downloaded
        if file_exists_with_content(svg_path):
            print(f"  Page {page_num}: already downloaded")
        else:
            print(f"  Page {page_num}: downloading...", end=" ")
            if download_svg_with_retry(svg_url, svg_path, client):
                print("✓")

                # Add random delay after successful download
                if delay > 0:
                    sleep_time = random.uniform(0, delay)
                    time.sleep(sleep_time)
            else:
                print("✗ (download failed)")
                failed_pages.append(page_num)
                continue

    return failed_pages


def process_batch_images(
    pages: List[Tuple[int, str]],
    svgs_dir: Path,
    jpeg_quality: Optional[int],
    image_scale: float,
    failed_pages: List[int]
) -> None:
    """Process SVG images (resize/compress) if needed."""
    # Only process if jpeg_quality or image_scale != 1.0
    should_process = jpeg_quality is not None or image_scale != 1.0

    if not should_process:
        return

    for page_num, _ in pages:
        if page_num in failed_pages:
            continue

        svg_path = svgs_dir / f"page_{page_num:04d}.svg"

        if not svg_path.exists():
            continue

        print(f"  Page {page_num}: processing images...", end=" ")
        if process_svg_images(svg_path, jpeg_quality, image_scale):
            print("✓")
        else:
            print("✗ (image processing failed)")
            # Don't mark as failed, just warn - conversion might still work
            continue


def convert_batch_to_pdfs(
    pages: List[Tuple[int, str]],
    svgs_dir: Path,
    pdfs_dir: Path,
    failed_pages: List[int]
) -> Tuple[List[Path], List[int]]:
    """Convert SVG files to PDFs.

    Returns:
        Tuple of (batch_pdfs list, updated failed_pages list)
    """
    batch_pdfs = []

    for page_num, _ in pages:
        if page_num in failed_pages:
            continue

        svg_path = svgs_dir / f"page_{page_num:04d}.svg"
        pdf_path = pdfs_dir / f"page_{page_num:04d}.pdf"

        # Skip if already converted
        if file_exists_with_content(pdf_path):
            print(f"  Page {page_num}: already converted")
            batch_pdfs.append(pdf_path)
        else:
            print(f"  Page {page_num}: converting...", end=" ")
            if convert_svg_to_pdf(svg_path, pdf_path):
                print("✓")
                batch_pdfs.append(pdf_path)
            else:
                print("✗ (conversion failed)")
                failed_pages.append(page_num)

    return batch_pdfs, failed_pages


def merge_batch_pdfs(
    batch_pdfs: List[Path],
    partial_pdf: Path,
    compress: bool
) -> bool:
    """Merge batch PDFs into partial output PDF.

    Returns:
        True if merge succeeded, False otherwise
    """
    if not batch_pdfs:
        return True

    print("  Merging batch PDFs...", end=" ")
    if append_pdfs_to_output(batch_pdfs, partial_pdf, compress=compress):
        print("✓")
        return True
    else:
        print("✗ (merge failed)")
        return False


def process_batch(
    batch_number: int,
    pages: List[Tuple[int, str]],
    cache_dir: Path,
    partial_pdf: Path,
    client: httpx.Client,
    compress: bool = False,
    jpeg_quality: Optional[int] = None,
    image_scale: float = 1.0,
    delay: float = 0
) -> Tuple[bool, List[int]]:
    """
    Process a batch of pages: download, convert, merge.

    Args:
        batch_number: Current batch number
        pages: List of (page_num, svg_url) tuples to process
        cache_dir: Cache directory for intermediate files
        partial_pdf: Path to incrementally built PDF
        client: httpx.Client instance for connection pooling
        compress: Whether to apply PDF compression
        jpeg_quality: JPEG quality (1-100), None to preserve original
        image_scale: Scale factor for images (1.0 = original size)
        delay: Maximum random delay between requests in seconds

    Returns:
        Tuple of (success, failed_page_numbers)
    """
    svgs_dir = cache_dir / "svgs"
    pdfs_dir = cache_dir / "temp_pdfs"

    ensure_directory(svgs_dir)
    ensure_directory(pdfs_dir)

    print(f"Processing batch {batch_number} ({len(pages)} pages)...")

    # Phase 1: Download SVGs
    failed_pages = download_batch_svgs(pages, svgs_dir, client, delay)

    # Phase 1.5: Process images (resize/compress) if needed
    process_batch_images(pages, svgs_dir, jpeg_quality, image_scale, failed_pages)

    # Phase 2: Convert SVGs to PDFs
    batch_pdfs, failed_pages = convert_batch_to_pdfs(
        pages, svgs_dir, pdfs_dir, failed_pages
    )

    # Phase 3: Merge batch PDFs into partial output
    merge_success = merge_batch_pdfs(batch_pdfs, partial_pdf, compress)

    if not merge_success:
        return False, failed_pages

    return len(failed_pages) == 0, failed_pages


def run_pipeline(args):
    """
    Execute the full download and conversion pipeline.

    Args:
        args: Parsed command-line arguments
    """
    output_pdf = Path(args.output).resolve()
    cache_base = Path(args.cache_dir)

    # Check if output file already exists
    if output_pdf.exists():
        print(f"   Warning: {output_pdf} already exists and will be overwritten")

    print("Issuu PDF Downloader")
    print("=" * 60)

    # Step 1: Parse URL
    print("\n1. Parsing URL...")
    username, doc_name = parse_issuu_url(args.url)
    print("   Publication: {username}/{doc_name}")

    # Sanitize username and doc_name
    username = sanitize_filename(username)
    doc_name = sanitize_filename(doc_name)
    print("   Sanitized: {username}/{doc_name}")

    # Set up cache directory
    pub_cache = cache_base / f"{username}_{doc_name}"

    if args.clean and pub_cache.exists():
        import shutil
        print(f"   Cleaning cache: {pub_cache}")
        shutil.rmtree(pub_cache)

    ensure_directory(pub_cache)

    # Create httpx.Client with context manager for connection pooling
    with httpx.Client(headers=HEADERS) as client:
        # Step 2: Fetch metadata
        print("\n2. Fetching metadata...")
        metadata = fetch_reader_json(username, doc_name, client)
        svg_urls = extract_svg_urls(metadata)
        total_pages = len(svg_urls)
        print(f"   Found {total_pages} pages")

        # Step 3: Load progress
        print("\n3. Loading progress...")
        progress = load_progress(pub_cache)
        progress["total_pages"] = total_pages

        completed = set(progress.get("completed_batches", []))
        print("   Completed batches: {len(completed)}")

        # Step 4: Process in batches
        print("\n4. Processing batches (batch size: {args.batch_size})...")

        partial_pdf = pub_cache / "partial.pdf"
        all_failed = []

        # Split into batches
        batches = []
        for i in range(0, total_pages, args.batch_size):
            batch_pages = svg_urls[i:i + args.batch_size]
            batch_num = (i // args.batch_size) + 1
            batches.append((batch_num, batch_pages))

        total_batches = len(batches)

        for batch_num, batch_pages in batches:
            # Skip completed batches
            if batch_num in completed:
                print(f"   Batch {batch_num}/{total_batches}: already completed ✓")
                continue

            success, failed = process_batch(
                batch_num,
                batch_pages,
                pub_cache,
                partial_pdf,
                client,
                compress=args.compress,
                jpeg_quality=args.jpeg_quality,
                image_scale=args.image_scale,
                delay=args.delay
            )

            # Check if merge failed - stop processing if it did
            if not success:
                print(f"\n✗ Error: PDF merge failed for batch {batch_num}")
                print("   Cannot continue processing. The partial PDF may be corrupted.")
                print("   Try running with --clean to start fresh, or investigate the error above.")
                raise Exception(f"PDF merge failed at batch {batch_num}/{total_batches}")

            if failed:
                all_failed.extend(failed)
                print(f"   Batch {batch_num}/{total_batches}: completed with {len(failed)} failures ⚠")
            else:
                print(f"   Batch {batch_num}/{total_batches}: completed ✓")

            # Save progress
            progress["completed_batches"].append(batch_num)
            progress["last_successful_batch"] = batch_num
            if failed:
                progress["failed_pages"].extend([
                    {"page": p, "reason": "download or conversion failed"}
                    for p in failed
                ])
            save_progress(pub_cache, progress)

    # Step 5: Copy final PDF
    print("\n5. Finalizing...")
    if partial_pdf.exists():
        import shutil
        shutil.copy(partial_pdf, output_pdf)

        # Count pages in final PDF
        reader = PdfReader(str(output_pdf))
        final_page_count = len(reader.pages)
        print(f"   Created: {output_pdf} ({final_page_count} pages)")
    else:
        raise Exception("No PDF was created")

    # Step 6: Summary
    print(f"\n{'=' * 60}")
    print("Summary:")
    print(f"  Total pages: {total_pages}")
    print(f"  Successful: {total_pages - len(all_failed)}")
    print(f"  Failed: {len(all_failed)}")

    if all_failed:
        print(f"\nFailed pages: {sorted(all_failed)}")
        print("Re-run the command to retry failed pages.")

    # Step 7: Cleanup
    if not args.keep_cache and not all_failed:
        print()
        import shutil
        shutil.rmtree(pub_cache)

    if all_failed:
        raise Exception(f"{len(all_failed)} pages failed to process")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Download and convert Issuu publications to PDF"
    )
    parser.add_argument("url", help="Issuu publication URL")
    parser.add_argument("output", help="Output PDF file path")
    parser.add_argument(
        "--cache-dir",
        default=".issuu_cache",
        help="Cache directory (default: .issuu_cache)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Pages per batch (default: 10)"
    )
    parser.add_argument(
        "--keep-cache",
        action="store_true",
        help="Keep cache after successful completion"
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove cache and start fresh"
    )
    parser.add_argument(
        "--compress",
        action="store_true",
        help="Enable PDF compression to reduce file size (default: False)"
    )
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=None,
        metavar="QUALITY",
        help="JPEG quality for embedded images (1-100, lower = smaller file, default: preserve original)"
    )
    parser.add_argument(
        "--image-scale",
        type=float,
        default=1.0,
        metavar="SCALE",
        help="Scale images (e.g., 0.5 = 50%% size, 2.0 = 200%% size, default: 1.0)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0,
        metavar="SECONDS",
        help="Maximum random delay between requests in seconds (e.g., 5 = 0-5s random pause)"
    )

    args = parser.parse_args()

    # Validate jpeg_quality range
    if args.jpeg_quality is not None and not (1 <= args.jpeg_quality <= 100):
        parser.error("--jpeg-quality must be between 1 and 100")

    # Validate image_scale is positive
    if args.image_scale <= 0:
        parser.error("--image-scale must be greater than 0")

    # Validate delay is non-negative
    if args.delay < 0:
        parser.error("--delay must be >= 0")

    # Run the pipeline
    try:
        run_pipeline(args)
    except KeyboardInterrupt:
        print("\n\nInterrupted! Progress has been saved. Re-run to resume.")
        return 1
    except Exception as e:
        print(f"\n✗ Error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
