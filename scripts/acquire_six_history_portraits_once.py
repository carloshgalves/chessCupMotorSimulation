"""One-shot, opt-in acquisition of six already-reviewed Commons file records.

Uses the official Wikimedia upload endpoint once per source, no proxies,
alternate hosts, captcha workarounds, rate-limit retries, or silent fallback.
A provider denial stops automated acquisition. A source candidate is never
marked approved unless the local image actually passed decoding, cropping
and SHA-256 recording.
"""
from __future__ import annotations

from datetime import date
from hashlib import md5, sha1, sha256
from io import BytesIO
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

BASE = Path(__file__).resolve().parents[1] / "src/chesssim/data/history"
SLUGS = ("alekhine", "fischer", "marshall", "nimzowitsch", "reti", "kamsky")
# SHA-1 as shown in the corresponding Wikimedia Commons file record.
# Fischer's Commons record does not publish SHA-1 in the current page.
KNOWN_SOURCE_SHA1 = {
    "alekhine": "228403dff2c4e1a1d12f5de08d6c9b87ce6dd108",
    "marshall": "a8514329fa8da47589c9947dfb45d4b5730d6cff",
    "nimzowitsch": "e1f237ca140bc59534a27cf388540c4c4624c383",
    "reti": "d0e10e2f9ad348b0beb69cc90e1f48463c65946c",
    "kamsky": "c5ee57fc85a8d505077c089e6bfe82a7e9a66907",
}
LABELS = {
    "alekhine": "Fotografia histórica de Alexander Alekhine, por volta de 1918",
    "fischer": "Bobby Fischer ao tabuleiro, fotografado pela Anefo em 1971",
    "marshall": "Fotografia de Frank Marshall publicada em livro de 1904",
    "nimzowitsch": "Retrato histórico de Aron Nimzowitsch em publicação anterior a 1930",
    "reti": "Retrato fotográfico de Richard Réti, acervo da Biblioteca Nacional Austríaca",
    "kamsky": "Gata Kamsky no Grenke Chess Open de Karlsruhe, 2016",
}


class ProviderDenied(RuntimeError):
    pass


def original_url(filename: str) -> str:
    name = filename.replace(" ", "_")
    digest = md5(name.encode("utf-8"), usedforsecurity=False).hexdigest()
    return ("https://upload.wikimedia.org/wikipedia/commons/"
            + digest[0] + "/" + digest[:2] + "/" + quote(name))


def acquire(row: dict) -> tuple[bytes, str]:
    if row["source_url"] != "https://commons.wikimedia.org/wiki/File:" + row["file"].replace(" ", "_"):
        raise ValueError("Fonte divergente do arquivo auditado para " + row["slug"])
    url = original_url(row["file"])
    req = Request(url, headers={
        "User-Agent": "ChessCupMotorSimulation/0.1 (public attribution on GitHub; single acquisition)",
        "Accept": "image/jpeg, image/png",
    })
    try:
        with urlopen(req, timeout=30) as response:
            if response.url.split("/")[2] != "upload.wikimedia.org":
                raise ValueError("Redirecionamento fora do acervo autorizado")
            original = response.read(8_000_001)
    except HTTPError as exc:
        if exc.code in (403, 429, 451):
            raise ProviderDenied("Provedor recusou download HTTP " + str(exc.code)) from exc
        raise
    if len(original) > 8_000_000:
        raise ValueError("Imagem acima de 8 MB")
    actual_sha1 = sha1(original).hexdigest()
    expected_sha1 = KNOWN_SOURCE_SHA1.get(row["slug"])
    if expected_sha1 is not None and actual_sha1 != expected_sha1:
        raise ValueError("Hash SHA-1 do original difere da ficha Commons: " + row["slug"])
    with Image.open(BytesIO(original)) as photo:
        if photo.format not in ("JPEG", "PNG"):
            raise ValueError("Formato não aceito: " + row["slug"])
        if photo.width * photo.height > 36_000_000:
            raise ValueError("Dimensão excessiva")
        square = ImageOps.fit(
            ImageOps.exif_transpose(photo).convert("RGB"), (480, 480),
            method=Image.Resampling.LANCZOS, centering=tuple(row["centering"]),
        )
        out = BytesIO()
        square.save(out, "JPEG", quality=82, optimize=True, progressive=True)
        derived = out.getvalue()
    if not derived.startswith(b"\\xff\\xd8") or len(derived) > 500_000:
        raise ValueError("Derivado JPEG inválido ou grande demais")
    return derived, actual_sha1


def main() -> None:
    manifest = json.loads((BASE / "portrait_candidates.json").read_text(encoding="utf-8"))
    candidates = {item["slug"]: item for item in manifest["candidates"]}
    imported = []
    errors = []
    for slug in SLUGS:
        row = candidates[slug]
        profile_path = BASE / (slug + ".json")
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        dest = BASE / "portraits" / (slug + ".jpg")
        if profile["portrait"] is not None or dest.exists():
            raise ValueError("Inconsistência: retrato já presente ou parcialmente importado: " + slug)
        try:
            derived, original_sha1 = acquire(row)
        except ProviderDenied as exc:
            print(f"STOP_NO_RETRY {slug}: {exc}", flush=True)
            errors.append({"slug": slug, "reason": str(exc)})
            break
        except (OSError, URLError, ValueError) as exc:
            print(f"UNAVAILABLE {slug}: {type(exc).__name__}: {exc}", flush=True)
            errors.append({"slug": slug, "reason": type(exc).__name__ + ": " + str(exc)})
            continue
        profile["portrait"] = {
            "local_asset": "history/portraits/" + slug + ".jpg",
            "alt": LABELS[slug],
            "author": row["author"],
            "license": row["license"],
            "source_url": row["source_url"],
            "verified_at": date.today().isoformat(),
            "event": row["event"],
            "event_date": row["event_date"],
            "rights_holder": row["rights_holder"],
            "license_url": row["license_url"],
            "sha256": sha256(derived).hexdigest(),
            "derivative_note": (
                "Recorte de 480 × 480 px da imagem Commons, original SHA-1 "
                + original_sha1 + ". "
                + ("Public Domain Mark é indicação de status no acervo, não licença mundial."
                   if row["license"] == "Public Domain Mark 1.0"
                   else "Derivado sob " + row["license"] + "; sem endosso do retratado.")
            ),
            "approval_status": "approved",
        }
        dest.write_bytes(derived)
        profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\\n",
                                encoding="utf-8")
        imported.append(slug)
        print(f"IMPORTED {slug}: {len(derived)} bytes; sha256={sha256(derived).hexdigest()}", flush=True)
    report = {"imported": imported, "unavailable": errors,
              "remaining": [x for x in SLUGS if x not in imported]}
    print("ACQUISITION_REPORT " + json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
