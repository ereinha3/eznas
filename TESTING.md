# Pipeline Testing Guide

This guide explains how to test the remux pipeline worker using existing media files.

## Quick Start

The easiest way to test the pipeline is using the `test_pipeline.py` script in **direct mode**, which bypasses qBittorrent and directly tests the remux worker:

```bash
# Test with a movie file
python test_pipeline.py --source /mnt/raid/data/media/movies/Some.Movie.2024.mkv --category movies --direct

# Test with a TV show file
python test_pipeline.py --source /mnt/raid/data/media/tv/Some.Show.S01E01.mkv --category tv --direct
```

## How It Works

### Direct Mode (Recommended)

The `--direct` flag tests the pipeline worker directly without involving qBittorrent:

1. **Copies** the source file to the download staging area (`/downloads/complete/{category}/`)
2. **Creates** a fake torrent record with the file
3. **Builds** a remux plan using the pipeline worker
4. **Runs** `ffmpeg` to remux the file (lossless, container change + language stripping)
5. **Moves** the processed file to the final library location
6. **Cleans up** the test file

This mode is perfect for testing the remux logic, language stripping, and file movement without needing qBittorrent running.

### qBittorrent Mode (Advanced)

Without `--direct`, the script attempts to add the file to qBittorrent as a completed torrent:

1. **Copies** the source file to the download staging area
2. **Creates** a minimal torrent file (may not work without proper bencode library)
3. **Adds** the torrent to qBittorrent via API
4. The **pipeline worker** (running in the background) will pick it up on its next poll

This mode requires:
- qBittorrent running and accessible
- Proper torrent file creation (may need `bencode` library)
- Pipeline worker running in the background

## Testing the Full Pipeline

To test the complete pipeline end-to-end:

1. **Start the stack** with `docker compose up`
2. **Wait** for services to be ready
3. **Run the test script** in direct mode:
   ```bash
   python test_pipeline.py --source /mnt/raid/data/media/movies/Your.Movie.mkv --category movies --direct
   ```
4. **Check** the output to see:
   - Remux plan details
   - FFmpeg command
   - Final file location
   - Success/failure status

## Simulating Downloads

If you want to simulate a real download scenario:

1. **Move** a file from your library to a temporary location:
   ```bash
   mv /mnt/raid/data/media/movies/Some.Movie.mkv /tmp/test_movie.mkv
   ```

2. **Run** the test script:
   ```bash
   python test_pipeline.py --source /tmp/test_movie.mkv --category movies --direct
   ```

3. **Verify** the processed file appears in the library at the expected location

4. **Move** the file back if needed:
   ```bash
   mv /mnt/raid/data/media/movies/Some.Movie.mkv /mnt/raid/data/media/movies/Some.Movie.mkv.backup
   ```

## What Gets Tested

- ✅ File copying to download staging area
- ✅ Torrent info creation
- ✅ Remux plan building
- ✅ Language track selection (audio/subtitle filtering)
- ✅ FFmpeg remux command execution
- ✅ File movement to final library location
- ✅ Container format conversion (e.g., to MKV)

## Troubleshooting

### "Source file does not exist"
- Check the file path is correct
- Ensure the file is accessible

### "FFmpeg failed"
- Check that `ffmpeg` is installed and in PATH
- Verify the source file is a valid video file
- Check FFmpeg error output for details

### "Config not found"
- Ensure you're running from the `nas_orchestrator` directory
- Or use `--root` to specify the config directory

### qBittorrent mode not working
- Use `--direct` mode instead (recommended)
- Or install `bencode` library for proper torrent file creation

## Next Steps

After testing, you can:
- Monitor the pipeline worker logs in Docker
- Check the processed files in the library
- Verify language tracks were correctly stripped
- Test with different file formats and categories

