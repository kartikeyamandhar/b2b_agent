"""Vision tools (01-backend.md section 6, tools 2 and 7). One metered model call each, structured output.

The prompts describe real-world labels and destruction in general terms; nothing here knows how the synthetic
photos are drawn. Photos are sent as stored when they are small enough, otherwise downscaled; either way the
recording key is the stored photo's sha256 (llm.client.ImageInput.key), so replay never depends on re-encoding.
"""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass

from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from recallops.llm.client import Completion, ImageInput, LlmClient, ModelRequest
from recallops.llm.meter import RunBudget

VISION_VERSION = "1.0.0"
MAX_SIDE_PX = 1568  # larger images are downscaled to this long side before sending
DEFAULT_SIZE = (768, 576)  # synthetic photo size, used when replaying without the photo file


class VisionError(RuntimeError):
    """The model answered, but not with a valid result (counted and logged; the agent sees an error)."""


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LabelReading(_M):
    legible: bool = Field(description="false when the label text cannot be read with confidence")
    model_text: str | None = Field(description="the model number exactly as printed, or null if not visible")
    serial_text: str | None = Field(description="the serial number exactly as printed, or null")
    date_code_text: str | None = Field(description="the date code exactly as printed, or null")
    confidence: float = Field(ge=0, le=1, description="confidence that model_text is read correctly")


class DestructionReading(_M):
    shows_product: bool = Field(description="false when the photo does not show the product (label, receipt, other)")
    complies: bool = Field(description="true only if the instructed destruction is clearly visible")
    observed: str = Field(description="one sentence on what the photo shows")
    confidence: float = Field(ge=0, le=1)


LABEL_SYSTEM = """You read the product label in a photo for a consumer product recall program.
Report the model number, serial number, and date code exactly as printed on the label (model numbers are often
marked MODEL, MOD., M/N, or similar; serial numbers SER, S/N; date codes DATE, MFD, or a printed date).
Copy characters exactly, including letters, digits, hyphens, and spaces. If a field is absent, cut off, too blurry,
or otherwise not readable with confidence, return null for it. Never guess, complete, or correct partial text.
Set legible to false when the label as a whole cannot be read."""

DESTRUCTION_SYSTEM = """You check whether a photo shows a recalled product disabled the way the recall instructs.
Set complies to true only if the photo clearly shows the instructed destruction, for example a power cord cut
through, material cut apart, the word RECALLED written on the product, or the product taken apart, as the
instruction requires. If the product looks intact, the destruction is not visible, or the photo shows something
other than the product (such as a label close-up or a receipt), set complies to false. Describe what you see in
one sentence."""


@dataclass(frozen=True)
class VisionResult[T: BaseModel]:
    reading: T
    completion: Completion


def prepare_image(data: bytes | None, sha256: str, size: tuple[int, int] | None = None) -> ImageInput:
    """data None is allowed only for replay (the recording key needs the hash and size, not the bytes)."""
    if data is None:
        width, height = size or DEFAULT_SIZE
        return ImageInput("image/jpeg", "", width, height, source_sha256=sha256)
    with Image.open(io.BytesIO(data)) as opened:
        fmt = (opened.format or "JPEG").upper()
        image = ImageOps.exif_transpose(opened)
        rotated = image.size != opened.size or opened.getexif().get(0x0112, 1) != 1
        if max(image.size) <= MAX_SIDE_PX and fmt in ("JPEG", "PNG") and not rotated:
            media = "image/png" if fmt == "PNG" else "image/jpeg"
            return ImageInput(media, base64.b64encode(data).decode(), image.width, image.height, source_sha256=sha256)
        image.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX))
        out = io.BytesIO()
        image.convert("RGB").save(out, format="JPEG", quality=85)
        return ImageInput(
            "image/jpeg", base64.b64encode(out.getvalue()).decode(), image.width, image.height, source_sha256=sha256
        )


def _call[T: BaseModel](
    llm: LlmClient, run: RunBudget, system: str, text: str, image: ImageInput, schema: type[T], purpose: str
) -> VisionResult[T]:
    request = ModelRequest(
        system=system,
        user_text=text,
        output_schema=schema.model_json_schema(),
        images=(image,),
        max_tokens=400,
        purpose=purpose,
    )
    done = llm.complete(request, run)
    try:
        reading = schema.model_validate(json.loads(done.response.text))
    except (ValueError, ValidationError) as exc:
        raise VisionError(f"{purpose}: invalid model output: {str(exc)[:200]}") from exc
    return VisionResult(reading=reading, completion=done)


def read_label_photo(llm: LlmClient, run: RunBudget, image: ImageInput) -> VisionResult[LabelReading]:
    return _call(
        llm,
        run,
        LABEL_SYSTEM,
        "Read the label in this photo.",
        image,
        LabelReading,
        "read_label_photo",
    )


def check_destruction_photo(
    llm: LlmClient, run: RunBudget, image: ImageInput, instruction: str
) -> VisionResult[DestructionReading]:
    return _call(
        llm,
        run,
        DESTRUCTION_SYSTEM,
        f"The recall instructs: {instruction}\nDoes this photo show that instruction carried out?",
        image,
        DestructionReading,
        "check_destruction_photo",
    )
