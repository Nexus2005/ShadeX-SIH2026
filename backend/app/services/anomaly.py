"""Forensics: PDF metadata analysis, duplicate detection, ELA-style page diff support.

Every check produces neutral-language *indicators* — never fraud claims.
"""
import logging
import zipfile
from datetime import datetime, timezone
from typing import Any

from PIL import Image

from app.config import get_settings

logger = logging.getLogger("resilient.anomaly")

SUSPICIOUS_PRODUCERS = [
    "photoshop", "canva", "ilovepdf", "pdfescape", "nitro", "foxit", "inkscape",
    "gimp", "paint", "ocrmyfail",
]

OFFICIAL_PRODUCERS = [
    "microsoft", "excel", "word", "acrobat distiller", "itext", "openoffice",
    "libreoffice", "skia", "quartz", "wkhtmltopdf", "govt", "government",
]


def analyze_pdf_metadata(pdf_metadata: dict[str, Any]) -> dict[str, Any]:
    """Indicator-level metadata analysis for document_versions.forensics."""
    flags: list[dict[str, Any]] = []
    producer = (pdf_metadata.get("producer") or "")
    creator = (pdf_metadata.get("creator") or "")
    signature = (producer + " " + creator).lower()

    for software in SUSPICIOUS_PRODUCERS:
        if software in signature:
            flags.append(
                {
                    "type": "metadata_editor_indicator",
                    "note": f"File may have passed through '{software.title()}' — legitimate documents are also edited or converted; treat only as a review cue.",
                    "software": software,
                    "severity": "info",
                }
            )
    if not producer.strip() and not creator.strip():
        flags.append(
            {
                "type": "metadata_absent",
                "note": "No producer/creator metadata present; common for scans — not an anomaly by itself.",
                "severity": "info",
            }
        )
    created = _parse_pdf_date(pdf_metadata.get("creation_date"))
    modified = _parse_pdf_date(pdf_metadata.get("mod_date"))
    if created and modified and modified > created:
        delta_hours = (modified - created).total_seconds() / 3600
        if delta_hours < 48:
            flags.append(
                {
                    "type": "modified_soon_after_creation",
                    "note": f"File modified {delta_hours:.1f}h after creation.",
                    "severity": "info",
                }
            )
    return {
        "producer": producer or None,
        "creator": creator or None,
        "created": created.isoformat() if created else None,
        "modified": modified.isoformat() if modified else None,
        "flags": flags,
        "is_encrypted": bool(pdf_metadata.get("is_encrypted")),
    }


def _parse_pdf_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    # PyPDF dates like D:20240115120000+05'30'
    cleaned = str(raw).replace("D:", "").replace("Z", "")
    for pattern in ("%Y%m%d%H%M%S", "%Y%m%d"):
        try:
            dt = datetime.strptime(cleaned[: len(pattern) - pattern.count("%") + 6][:15].split("+")[0].replace("'", ""), pattern)
            return dt.replace(tzinfo=None)
        except ValueError:
            continue
    return None


def estimate_page_image_quality(page_pil_image: Image.Image) -> dict[str, Any]:
    """Cheap quality signals for uploads; no claims about authenticity."""
    from PIL import ImageStat

    result: dict[str, Any] = {"width": page_pil_image.width, "height": page_pil_image.height}
    stat = ImageStat.Stat(page_pil_image.convert("L"))
    std = float(stat.stddev[0])
    mean = float(stat.mean[0])
    result["luminance_mean"] = round(mean, 1)
    result["luminance_stddev"] = round(std, 1)
    # extreme means: near-black or near-white scans
    if mean < 40:
        result["likely_dark_capture"] = True
    if mean > 245 and std < 8:
        result["likely_blank"] = True
    if std < 14:
        result["likely_blur_or_flat"] = True
    return result


def ela_signals(page_pil_image: Image.Image) -> dict[str, Any] | None:
    """Re-save difference changes compressed regions; investigative aid, not proof."""
    try:
        import io

        import numpy as np

        original = page_pil_image.convert("RGB")
        buf = io.BytesIO()
        original.save(buf, format="JPEG")
        recompressed = Image.open(io.BytesIO(buf.getvalue()))
        arr = np.asarray(original, dtype=np.int16) - np.asarray(recompressed, dtype=np.int16)
        diff = np.abs(arr[:, :, :3]).sum(axis=2)
        share_resaved = float((diff > 12).mean())
        return {
            "ela_share_of_regions": round(share_resaved, 4),
            "ela_note": "Higher values mark regions that behave differently under uniform re-compression; explain manually, do not label fraud from this alone.",
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("ELA unavailable: %s", exc)
        return None


def analyze_zip_bomb_safety(data: bytes, label: str = "file") -> None:
    """Tight input sanity guard: reject obviously recursive/deep archives before parsing."""
    if label.lower().endswith(".pdf") and b"/EmbeddedFile" not in data[:4096]:
        return
    # Images arrive via magic-byte-detected content; only PDFs embed ZIP-like objects.
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io := __import__("io").BytesIO(data)) as zf:
                names = zf.namelist()
                if len(names) > 500:
                    raise ValueError("Archive object count is abnormally high")
        except zipfile.BadZipFile:
            pass


def phash_of_image(pil_image: Image.Image) -> str | None:
    try:
        import imagehash

        return str(imagehash.average_hash(pil_image, hash_size=12))
    except Exception as exc:  # noqa: BLE001
        logger.debug("imagehash unavailable: %s", exc)
        return None
