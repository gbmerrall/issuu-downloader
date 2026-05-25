# Issuu PDF Downloader

Confirmed still works - May 2026

A command-line tool to download and convert Issuu publications to PDF format with preserved text layout. Vibe coded using Claude Code. 

## Key Features

- **Searchable PDFs**: Converts publications while preserving selectable, searchable text
- **Smart Resume**: Automatically resumes interrupted downloads without losing progress
- **File Size Optimization**: Compress PDFs and optimize images to reduce file size
- **Reliable Downloads**: Built-in retry logic with exponential backoff for network errors
- **Rate Limiting**: Control request speed to be respectful to servers
- **Progress Tracking**: Clear progress indication with batch-by-batch status updates

## Requirements

### Python Version
- Python 3.7 or higher

### System Dependencies

The tool requires the **Cairo library** for SVG to PDF conversion. This must be installed on your system before installing the Python packages.

#### Installing Cairo

**macOS:**
```bash
brew install cairo
```

**Ubuntu/Debian:**
```bash
sudo apt-get install libcairo2-dev
```

**Fedora/RHEL:**
```bash
sudo dnf install cairo-devel
```

**Windows:**
Download and install GTK+ runtime from:
https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer

## Installation

1. Install Cairo (see above)
2. Install Python dependencies:

```bash
pip install -r requirements.txt
```

## Usage

### Basic Usage

```bash
python download_issuu.py <issuu_url> <output.pdf>
```

**Example:**
```bash
python download_issuu.py "https://issuu.com/alliance12/docs/annual_report_2023" annual_report.pdf
```

### Command-Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `url` | Issuu publication URL (required) | - |
| `output` | Output PDF file path (required) | - |
| `--cache-dir <path>` | Directory for temporary files | `.issuu_cache` |
| `--batch-size <n>` | Number of pages to process per batch | `10` |
| `--keep-cache` | Don't delete cache after completion | `False` |
| `--clean` | Remove cache and start fresh | `False` |
| `--compress` | Enable PDF compression | `False` |
| `--jpeg-quality <1-100>` | JPEG quality for images (lower = smaller) | Original quality |
| `--image-scale <factor>` | Scale images (0.5 = 50%, 2.0 = 200%) | `1.0` |
| `--delay <seconds>` | Random delay between requests (0-N seconds) | `0` |

## Examples

### Basic Download
```bash
python download_issuu.py "https://issuu.com/user/docs/document" output.pdf
```

### Optimized for Small File Size
Reduce file size by compressing the PDF, reducing JPEG quality, and scaling down images:
```bash
python download_issuu.py "https://issuu.com/user/docs/document" output.pdf \
  --compress \
  --jpeg-quality 75 \
  --image-scale 0.8
```

### Rate-Limited Download
Add a random delay (0-5 seconds) between page downloads to be respectful to servers:
```bash
python download_issuu.py "https://issuu.com/user/docs/document" output.pdf \
  --delay 5
```

### Resume Interrupted Download
Simply re-run the same command - it will automatically resume from the last completed batch:
```bash
python download_issuu.py "https://issuu.com/user/docs/document" output.pdf
```

### Force Fresh Start
Remove all cached files and start over:
```bash
python download_issuu.py "https://issuu.com/user/docs/document" output.pdf --clean
```

### Custom Batch Size
Process more pages at once (useful for faster downloads with stable internet):
```bash
python download_issuu.py "https://issuu.com/user/docs/document" output.pdf \
  --batch-size 20
```

### Keep Cache for Inspection
Preserve temporary files after completion (useful for troubleshooting):
```bash
python download_issuu.py "https://issuu.com/user/docs/document" output.pdf \
  --keep-cache
```

## Features in Detail

### File Size Optimization

The tool offers several options to reduce output file size:

- **`--compress`**: Applies PDF compression to reduce file size without quality loss
- **`--jpeg-quality <1-100>`**: Re-encodes embedded JPEG images at lower quality
  - 90-100: High quality, minimal compression
  - 75-89: Good quality, moderate compression (recommended)
  - 50-74: Acceptable quality, high compression
  - 1-49: Lower quality, maximum compression
- **`--image-scale <factor>`**: Resizes images before embedding
  - 0.5 = 50% size (quarter the pixels)
  - 0.75 = 75% size
  - 1.0 = original size (no scaling)

**Recommended for smaller files:**
```bash
python download_issuu.py <url> output.pdf --compress --jpeg-quality 80 --image-scale 0.75
```

### Smart Resume

Progress is automatically saved after each batch. If the download is interrupted (network error, Ctrl+C, computer sleep, etc.), simply re-run the same command to resume.

**How it works:**
- Progress is saved in `.issuu_cache/<publication>/progress.json`
- Each completed batch is recorded
- Already-downloaded files are detected and skipped
- Failed pages are tracked and retried on the next run

**To force a fresh start:**
```bash
python download_issuu.py <url> output.pdf --clean
```

### Rate Limiting

Use `--delay` to add a random pause between page downloads. This helps avoid overwhelming the server and is more respectful to the service.

**Example:** Add a random 0-5 second delay between pages:
```bash
python download_issuu.py <url> output.pdf --delay 5
```

### Error Handling

The tool includes robust error handling:

- **Automatic Retries**: Failed downloads are automatically retried up to 3 times with exponential backoff (1s, 2s, 4s delays)
- **Batch Isolation**: If one page fails, other pages in the batch continue processing
- **Failed Page Tracking**: Failed pages are logged and can be retried by re-running the command
- **Progress Preservation**: Progress is saved after each batch, not just at the end

## Troubleshooting

### Cairo Library Not Found

**Error message:** `OSError: no library called "cairo" was found`

**Solution:** Install the Cairo library for your system (see Requirements section above).

After installing Cairo, you may need to restart your terminal or reinstall the Python packages:
```bash
pip install --force-reinstall cairosvg
```

### Text Not Selectable in Output PDF

**Possible causes:**
1. Cairo library is not properly installed
2. Source SVG files don't contain text elements (they might be rasterized)
3. Old version of Cairo

**Solutions:**
- Verify Cairo installation: On macOS, try `brew list cairo` to confirm it's installed
- Update Cairo: `brew upgrade cairo` (macOS) or equivalent for your system
- Try reinstalling cairosvg: `pip install --force-reinstall cairosvg`

### Network Errors or Timeouts

**The tool automatically retries failed downloads**, but if you're experiencing persistent issues:

**Solutions:**
- Check your internet connection
- Reduce batch size: `--batch-size 5`
- Add delays between requests: `--delay 2`
- Try again later if Issuu's servers are experiencing issues
- Check if you can access the publication URL in your browser

### Resume Not Working

**Possible causes:**
- Cache directory was deleted
- Different cache directory is being used
- `--clean` flag was used

**Solutions:**
- Check if `.issuu_cache` directory exists in your current folder
- Make sure you're running the command from the same directory
- Don't use `--clean` flag if you want to resume
- Verify progress file exists: `.issuu_cache/<publication>/progress.json`

### Out of Disk Space

Large publications can require significant temporary disk space (2-3x the final PDF size).

**Solutions:**
- Check available disk space before starting
- Use `--cache-dir /path/to/larger/drive` to store temporary files elsewhere
- Clean up cache manually: `rm -rf .issuu_cache`
- Use file size optimization options to reduce space requirements

### PDF File Size Too Large

**Solutions:**

For significant file size reduction, combine these options:
```bash
python download_issuu.py <url> output.pdf \
  --compress \
  --jpeg-quality 70 \
  --image-scale 0.7
```

**Trade-offs:**
- `--compress`: No visible quality loss, moderate size reduction
- `--jpeg-quality 70-85`: Slight quality loss, good size reduction
- `--image-scale 0.7`: Reduces resolution, significant size reduction

### Permission Errors

**Error message:** `PermissionError: [Errno 13] Permission denied`

**Solutions:**
- Ensure you have write permissions for the output directory
- Check that the output file isn't open in another program
- Try saving to a different location
- Change cache directory: `--cache-dir /path/with/permissions`

### Some Pages Failed to Convert

**The tool will continue processing other pages** and report failed pages at the end.

**Solutions:**
- Re-run the command to retry failed pages (they will be automatically retried)
- Check the failed page numbers reported in the summary
- Some complex SVGs may genuinely fail to convert - this is rare but possible
- Try using `--clean` to start fresh if pages consistently fail

## Tips and Recommendations

### Choosing Batch Size

- **Default (10)**: Good balance for most use cases
- **Smaller (5)**: Better for unstable internet connections
- **Larger (20-50)**: Faster downloads if you have stable, fast internet
- **Very large publications (100+ pages)**: Keep at 10-20 to avoid memory issues

### Balancing Quality vs File Size

| Use Case | Recommended Settings |
|----------|---------------------|
| **Archive/Print** | No optimization flags (original quality) |
| **Screen Reading** | `--compress --jpeg-quality 85 --image-scale 0.9` |
| **Quick Preview** | `--compress --jpeg-quality 70 --image-scale 0.7` |
| **Minimum Size** | `--compress --jpeg-quality 60 --image-scale 0.6` |

### Estimated Download Times

Times vary based on page complexity, image count, and internet speed:

- **Small publication (10-20 pages)**: 2-5 minutes
- **Medium publication (50-100 pages)**: 10-20 minutes
- **Large publication (200+ pages)**: 30-60 minutes

### Best Practices

1. **Test first**: Try downloading a few pages with a small batch size before committing to a large publication
2. **Check disk space**: Ensure you have at least 2-3x the expected PDF size available
3. **Use resume**: Don't worry about interruptions - just re-run the command
4. **Be respectful**: Use `--delay` for large downloads to avoid overwhelming servers
5. **Optimize when needed**: Only use quality reduction options if file size is a concern

## How It Works

1. **Fetch Metadata**: Retrieves publication information from Issuu's API to discover all pages
2. **Download SVG Pages**: Downloads each page as an SVG file in batches
3. **Process Images** (optional): Optimizes embedded JPEG images if quality/scale options are specified
4. **Convert to PDF**: Converts each SVG to a PDF page using Cairo (preserves text)
5. **Incremental Merge**: Combines PDFs after each batch into the final document
6. **Track Progress**: Saves progress after each batch for resume capability
7. **Cleanup**: Removes temporary files after successful completion (unless `--keep-cache` is used)

## License

This tool is provided as-is for educational and personal use.
