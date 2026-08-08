#!/usr/bin/env python3
import os
import shutil
from pathlib import Path
import sys

def main():
    root = Path(__file__).resolve().parent.parent
    dist_dir = root / "dist"

    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    
    dist_dir.mkdir(parents=True)

    allowlist_files = [
        "index.html",
        "styles.css",
    ]

    allowlist_dirs = [
        "src",
        "data"
    ]

    print("Building /dist...")
    
    for filename in allowlist_files:
        src = root / filename
        if src.exists():
            shutil.copy2(src, dist_dir / filename)
            print(f"Copied {filename}")
        else:
            print(f"Warning: {filename} not found.")

    for dirname in allowlist_dirs:
        src = root / dirname
        if src.exists():
            shutil.copytree(src, dist_dir / dirname)
            print(f"Copied directory {dirname}")
        else:
            print(f"Warning: directory {dirname} not found.")

    # Count files and size
    total_files = 0
    total_size = 0
    max_size = 0
    max_file = None

    for filepath in dist_dir.rglob("*"):
        if filepath.is_file():
            total_files += 1
            size = filepath.stat().st_size
            total_size += size
            if size > max_size:
                max_size = size
                max_file = filepath

    print("\n--- Build Summary ---")
    print(f"Total files in /dist: {total_files}")
    print(f"Total size: {total_size / (1024 * 1024):.2f} MB")
    if max_file:
        print(f"Largest file: {max_file.relative_to(dist_dir)} ({max_size / (1024 * 1024):.2f} MB)")
    
    # Cloudflare Pages limits check
    # 20,000 files per deployment
    # 25 MB per file limit (Cloudflare Pages free tier limit)
    if total_files > 20000:
        print("WARNING: File count exceeds Cloudflare limit (20,000)!")
        sys.exit(1)
    if max_size > 25 * 1024 * 1024:
        print("WARNING: Largest file exceeds Cloudflare limit (25 MB)!")
        sys.exit(1)
        
    print("\nSuccess: /dist is ready for deployment and within Cloudflare limits.")

if __name__ == "__main__":
    main()
