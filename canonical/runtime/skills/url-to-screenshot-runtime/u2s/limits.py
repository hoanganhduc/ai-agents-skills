"""Shared screenshot artifact resource limits."""

# Ten megapixels permits long full-page captures while keeping worst-case
# standard-library filter reversal and exact visual analysis bounded.
MAX_CAPTURE_PIXELS = 10_000_000
MAX_PNG_DIMENSION = 100_000
MAX_PNG_FILE_BYTES = 64 * 1024 * 1024
MAX_PNG_DECOMPRESSED_BYTES = MAX_CAPTURE_PIXELS * 4 + MAX_PNG_DIMENSION
MAX_PNG_CHUNKS = 10_000
MAX_PNG_DECODE_SECONDS = 15.0
PNG_COLOR_BITS = 5
