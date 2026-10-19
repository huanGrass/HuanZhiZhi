"""Download the exact corresponding-source attachments listed in the release notices."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import urllib.request


def fetch_sources(output):
    manifest = Path(__file__).resolve().parents[1] / "licenses/source-archives.json"
    output.mkdir(parents=True, exist_ok=True)
    for item in json.loads(manifest.read_text(encoding="utf-8")):
        target = output / item["file"]
        if target.name != item["file"]:
            raise ValueError("Source archive must use a plain filename")
        if target.exists():
            with target.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != item["sha256"]:
                    raise ValueError(f"Existing archive checksum mismatch: {target}")
        else:
            with tempfile.TemporaryDirectory(dir=output) as temporary:
                downloaded = Path(temporary) / target.name
                with urllib.request.urlopen(item["url"], timeout=60) as response, downloaded.open("wb") as stream:
                    shutil.copyfileobj(response, stream)
                with downloaded.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != item["sha256"]:
                        raise ValueError(f"Downloaded archive checksum mismatch: {target}")
                downloaded.rename(target)
        print(target)
    shutil.copy2(manifest, output / manifest.name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("build/third-party-sources"))
    fetch_sources(parser.parse_args().output)
