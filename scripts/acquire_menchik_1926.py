"""Import a 1926 Menchik photograph from the British Chess Magazine frontispiece.

The image is a historical published work, not a Creative Commons licensed photo.
The editorial archive is credited; the extract is marked as public domain by
expiry of its original periodical copyright, not by a CC0 dedication.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import json

from materialize_history_portraits import DATA, manifest_rows
HOST="www.chesshistory.com"
PAGE="https://www.chesshistory.com/winter/extra/menchikclub.html"
JPEG="https://www.chesshistory.com/winter/pics/cn8774_menchik.jpg"


def read(url: str, limit: int) -> bytes:
    from urllib.parse import urlsplit
    if urlsplit(url).hostname != HOST or urlsplit(url).scheme != "https":
        raise ValueError("Unapproved photo host")
    req=Request(url,headers={"User-Agent":"ChessSimulationHistoricalCurator/1.0 (+https://github.com/carloshgalves/chessCupMotorSimulation)"})
    with urlopen(req,timeout=18) as res:
        if urlsplit(res.url).hostname!=HOST:
            raise ValueError("Off-host redirect")
        blob=res.read(limit+1)
    if len(blob)>limit:
        raise ValueError("Source exceeded size limit")
    return blob


def main():
    from PIL import Image,ImageOps
    row=next(x for x in manifest_rows() if x["slug"]=="menchik")
    assert row["source_url"]==PAGE
    picture=DATA/"portraits/menchik.jpg"
    packfile=DATA/"menchik.json"
    pack=json.loads(packfile.read_text(encoding="utf-8"))
    if picture.is_file() and pack["portrait"] is not None:
        assert sha256(picture.read_bytes()).hexdigest()==pack["portrait"]["sha256"]
        print("MENCHIK_PRESERVED",flush=True)
        return
    if picture.exists() or pack["portrait"] is not None:
        raise ValueError("Incomplete prior Menchik import")
    html=read(PAGE,150_000).decode("utf-8","replace")
    if ("The Vera Menchik Club" not in html or
            "February 1926" not in html or "cn8774_menchik" not in html):
        print("MENCHIK_SOURCE_DIAGNOSTIC", len(html),
              "club", "The Vera Menchik Club" in html,
              "year", "February 1926" in html,
              "asset", "cn8774_menchik" in html, flush=True)
        raise ValueError("British Chess Magazine 1926 source no longer matches archive")
    original=read(JPEG,1_500_000)
    with Image.open(BytesIO(original)) as im:
        if im.format!="JPEG" or im.width<450 or im.height<650:
            raise ValueError("Unexpected reproduction dimensions")
        # The original plate includes multiple portraits and a trophy.
        # The top-left oval is marked MISS VERA MENCHIK WINNER.
        region=im.crop((int(.03*im.width),int(.025*im.height),
                        int(.33*im.width),int(.24*im.height)))
        if min(region.size)<150:
            raise ValueError("Menchik crop insufficient")
        square=ImageOps.fit(region.convert("RGB"),(480,480),
                            method=Image.Resampling.LANCZOS)
        out=BytesIO()
        square.save(out,format="JPEG",quality=85,optimize=True,progressive=True)
        binary=out.getvalue()
    pack["portrait"]={
        "local_asset":"history/portraits/menchik.jpg",
        "alt":"Retrato de Vera Menchik, vencedora do torneio feminino; fotografia publicada no British Chess Magazine de fevereiro de 1926",
        "author":row["author"],"license":"Public Domain Mark 1.0",
        "source_url":PAGE,"verified_at":date.today().isoformat(),
        "event":row["event"],"event_date":row["event_date"],
        "rights_holder":row["rights_holder"],"license_url":row["license_url"],
        "sha256":sha256(binary).hexdigest(),
        "derivative_note":"Recorte da fotografia no oval superior esquerdo do frontispício de fevereiro de 1926; reprodução histórica, não licença CC0. Fonte editorial: Edward Winter / British Chess Magazine.",
        "approval_status":"approved"
    }
    picture.parent.mkdir(exist_ok=True)
    picture.write_bytes(binary)
    packfile.write_text(json.dumps(pack,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("MENCHIK_IMPORTED",len(binary),flush=True)


if __name__=="__main__":
    main()
