"""Stage 3 — Save generated image to disk with metadata."""

from pathlib import Path

from PIL import ExifTags, Image


def run(
    image: Image.Image,
    output_path: str | Path,
) -> dict:
    """Save PIL Image as PNG with EXIF metadata.

    Returns dict with keys: output_path, width, height, file_size_bytes.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    exif_data = Image.Exif()
    exif_data[ExifTags.Base.Software] = "AI generated;zimage"
    exif_data[ExifTags.Base.Make] = "Alibaba Tongyi Lab"
    image.save(output_path, exif=exif_data, quality=95, subsampling=0)

    return {
        "output_path": str(output_path),
        "width": image.width,
        "height": image.height,
        "file_size_bytes": output_path.stat().st_size,
    }


def get_manifest_inputs(output_path: str) -> dict:
    return {"output_path": output_path}


def get_manifest_outputs(result: dict) -> dict:
    return {
        "output_path": result["output_path"],
        "width": result["width"],
        "height": result["height"],
        "file_size_bytes": result["file_size_bytes"],
    }


def get_manifest_debug(result: dict) -> dict:
    return {"image_format": "PNG"}
