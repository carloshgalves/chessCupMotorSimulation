"""Curate an already-downloaded, freely licensed tournament photo without network calls.

The operator must verify the source license and player/event identity manually
before --apply. The script validates declarations but cannot grant legal rights.
"""
from __future__ import annotations

import argparse
from datetime import date
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile

from materialize_history_portraits import DATA, manifest_rows


def make_portrait(slug: str, input_path: Path, *, source_url: str,
                  confirmed_license: str, confirmed_event: str, apply: bool) -> dict:
    from PIL import Image, ImageOps, UnidentifiedImageError

    row = next((item for item in manifest_rows() if item["slug"] == slug), None)
    if row is None:
        raise ValueError("Jogador sem documento de origem no manifesto")
    if (source_url != row["source_url"] or
            confirmed_license != row["license"] or
            confirmed_event != row["event"]):
        raise ValueError("Confirmações não correspondem à fonte previamente catalogada")
    if input_path.is_symlink() or not input_path.is_file():
        raise ValueError("Arquivo local ausente ou symlink")
    if input_path.stat().st_size > 8_000_000:
        raise ValueError("Imagem acima do limite de 8 MB")
    original = input_path.read_bytes()
    with Image.open(BytesIO(original)) as photo:
        if photo.format not in {"JPEG", "PNG"}:
            raise ValueError("Apenas imagens JPEG/PNG")
        if photo.width * photo.height > 36_000_000:
            raise ValueError("Imagem com dimensão excessiva")
        square = ImageOps.fit(ImageOps.exif_transpose(photo).convert("RGB"),
                              (480, 480), method=Image.Resampling.LANCZOS,
                              centering=tuple(row["centering"]))
        out = BytesIO()
        square.save(out, "JPEG", quality=82, optimize=True, progressive=True)
        binary = out.getvalue()
    if len(binary) > 500_000:
        raise ValueError("Arquivo derivado ultrapassa o limite de pacote")
    pack_path = DATA / f"{slug}.json"
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    destination = DATA / "portraits" / f"{slug}.jpg"
    if destination.exists() or pack.get("portrait") is not None:
        raise ValueError("Imagem já incorporada; sem sobrescrita automática")
    portrait = {
        "local_asset": f"history/portraits/{slug}.jpg",
        "alt": f"Retrato documental de {slug} no torneio {row['event']}",
        "author": row["author"], "license": row["license"],
        "source_url": row["source_url"],
        "verified_at": date.today().isoformat(),
        "event": row["event"], "event_date": row["event_date"],
        "rights_holder": row["rights_holder"],
        "license_url": row["license_url"],
        "sha256": sha256(binary).hexdigest(),
        "derivative_note": "Recorte quadrado e redução a 480×480 px.",
        "approval_status": "approved",
    }
    if not apply:
        return {"slug": slug, "status": "DRY_RUN", "sha256": portrait["sha256"],
                "bytes": len(binary), "source": source_url}
    # Failure between the two renames leaves an orphan JPEG; rerun must fail,
    # allowing explicit rollback rather than silently overwriting evidence.
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_jpg = tmp_json = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".tmp",
                                         delete=False) as output:
            tmp_jpg = Path(output.name)
            output.write(binary)
            output.flush()
            os.fsync(output.fileno())
        pack["portrait"] = portrait
        with tempfile.NamedTemporaryFile(mode="w", dir=DATA, suffix=".tmp",
                                         encoding="utf-8", delete=False) as output:
            tmp_json = Path(output.name)
            json.dump(pack, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp_jpg, destination)
        tmp_jpg = None
        os.replace(tmp_json, pack_path)
        tmp_json = None
    finally:
        for temp_path in (tmp_jpg, tmp_json):
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
    return {"slug": slug, "status": "IMPORTED", "sha256": portrait["sha256"],
            "bytes": len(binary), "source": source_url}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--confirmed-license", required=True)
    parser.add_argument("--confirmed-event", required=True)
    parser.add_argument("--rights-and-identity-checked", action="store_true",
                        help="Confirm personal review of rights, player identity and competition")
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    if arguments.apply and not arguments.rights_and_identity_checked:
        parser.error("--apply requires --rights-and-identity-checked")
    result = make_portrait(arguments.slug, arguments.input,
                           source_url=arguments.source_url,
                           confirmed_license=arguments.confirmed_license,
                           confirmed_event=arguments.confirmed_event,
                           apply=arguments.apply)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
