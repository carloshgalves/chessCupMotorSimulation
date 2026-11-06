"""Import the 1772 Philidor engraving through The Met's CC0 Open Access API."""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from io import BytesIO
import json
from urllib.parse import urlsplit
from urllib.request import urlopen, Request
from urllib.error import HTTPError

from materialize_history_portraits import DATA, manifest_rows

USER_AGENT = "ChessSimulationOpenAccess/1.0 (https://github.com/carloshgalves/chessCupMotorSimulation)"
OBJECT = "https://collectionapi.metmuseum.org/public/collection/v1/objects/423124"


def get(url: str, *, limit: int) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {
            "collectionapi.metmuseum.org", "images.metmuseum.org"}:
        raise ValueError("Unapproved Met source URL")
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=20) as response:
        if urlsplit(response.url).hostname not in {
                "collectionapi.metmuseum.org", "images.metmuseum.org"}:
            raise ValueError("Met redirected outside of official image infrastructure")
        content = response.read(limit + 1)
    if len(content) > limit:
        raise ValueError("Image larger than budget")
    return content


def main() -> None:
    from PIL import Image, ImageOps

    row = next(r for r in manifest_rows() if r["slug"] == "philidor")
    if row["source_url"] != "https://www.metmuseum.org/art/collection/search/423124":
        raise ValueError("Philidor source does not match audited museum page")
    photo = DATA / "portraits/philidor.jpg"
    packpath = DATA / "philidor.json"
    profile = json.loads(packpath.read_text(encoding="utf-8"))
    if photo.exists() or profile["portrait"] is not None:
        if photo.exists() and profile["portrait"] is not None:
            if sha256(photo.read_bytes()).hexdigest() == profile["portrait"]["sha256"]:
                print("MET_PRESERVED philidor",flush=True)
                return
        raise ValueError("Partial Met import detected; no overwrite")
    obj = json.loads(get(OBJECT, limit=20000))
    if not (obj.get("isPublicDomain") is True and obj.get("objectID") == 423124
            and obj.get("objectDate") == "1772"
            and "Philidor" in obj.get("title","")
            and "Saint-Aubin" in obj.get("artistDisplayName","")):
        raise ValueError("Met object is not verified public-domain Philidor")
    imgurl = obj.get("primaryImageSmall") or obj.get("primaryImage")
    if not isinstance(imgurl, str) or urlsplit(imgurl).hostname != "images.metmuseum.org":
        raise ValueError("Met did not return an allowed original image")
    image_data = get(imgurl, limit=5_000_000)
    with Image.open(BytesIO(image_data)) as img:
        if img.format not in {"JPEG", "PNG"} or min(img.size) < 280:
            raise ValueError("Met image invalid or too small")
        square = ImageOps.fit(img.convert("RGB"), (480,480),
                              method=Image.Resampling.LANCZOS,
                              centering=tuple(row["centering"]))
        out = BytesIO()
        square.save(out,format="JPEG",quality=82,progressive=True,optimize=True)
        jpeg=out.getvalue()
    if len(jpeg)>500_000:
        raise ValueError("Met derivative above size budget")
    profile["portrait"]={
        "local_asset":"history/portraits/philidor.jpg",
        "alt":"Gravura de François-André Danican Philidor, feita em 1772 por Augustin de Saint-Aubin após Cochin II; não é fotografia",
        "author":row["author"],
        "license":"CC0 1.0",
        "source_url":row["source_url"],
        "verified_at":date.today().isoformat(),
        "event":row["event"],
        "event_date":"1772",
        "rights_holder":"Metropolitan Museum of Art — Open Access",
        "license_url":"https://creativecommons.org/publicdomain/zero/1.0/",
        "sha256":sha256(jpeg).hexdigest(),
        "derivative_note":"Recorte JPEG 480×480 px de gravura de 1772, domínio público (The Met Open Access); arte histórica, não fotografia do jogador.",
        "approval_status":"approved",
    }
    photo.parent.mkdir(parents=True,exist_ok=True)
    photo.write_bytes(jpeg)
    packpath.write_text(json.dumps(profile,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("MET_IMPORTED philidor",len(jpeg),"bytes",flush=True)


if __name__=="__main__":
    main()
