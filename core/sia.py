import urllib3
from datetime import date
import logging

from .piso import Piso
from .util import safe_int, tmap
from .web import Driver, get_text, get_query
from .retry import retry, RetryException
from .imgur import ImgUr
from selenium.common.exceptions import JavascriptException

urllib3.disable_warnings()

logger = logging.getLogger(__name__)


class BadSiaFicha(RetryException):
    pass


class CaptchaError(ValueError):
    pass


def get_val(n):
    txt = get_text(n)
    if txt is None:
        return None
    num = safe_int(txt)
    if num is not None:
        return num
    lw = txt.lower()
    if lw in ("si", "no"):
        return lw == "si"
    if len(txt) > 1 and txt.upper() == txt:
        return txt.title()
    return txt


class SiaDriver(Driver):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def waitLoaded(self):
        try:
            self.waitjs('window.document.readyState === "complete" && !jQuery("#ctl00_UpdateProg1").is(":visible")')
        except JavascriptException:
            title = get_text(self.get_soup().select_one("title"))
            if set((title or "").lower().split()).intersection("captcha", "blocked"):
                raise CaptchaError(title)
            raise

    def iter_pages(self):
        page = 1
        logger.info(f"Página {page}")
        self.click("//p/input[@type='submit']")
        while True:
            self.waitLoaded()
            yield page
            page += 1
            nxt = self.safe_wait("//td/a[text()='%s']" % (page))
            if nxt is None:
                break
            logger.info(f"Página {page}")
            nxt.click()

    def iter_pisos(self):
        page = self.execute_script(
            'return Number(jQuery("tr.paginador span.general").text())'
        )
        ids = []
        for tr in self.get_soup().select("tr"):
            tds = tr.find_all("td")
            txt = tmap(get_val, tds)
            if len(tds) != 7:
                continue
            ids.append(txt[0])
        for id in ids:
            logger.info(f"Piso {id}")
            self.click_and_wait(f"//td[text()='{id}']")
            yield id
            self.click_and_wait("//div/input[@type='submit']")
            if page > 1:
                self.click_and_wait(f"//td/a[text()='{page}']")

    def click_and_wait(self, id: str):
        while True:
            self.waitjs('window.document.readyState === "complete"')
            self.click(id)
            self.waitLoaded()
            if self.safe_wait(id) is None:
                return


class Sia:
    URL = "https://www3.emvs.es/SMAWeb/"

    def __init__(self, old: dict[int, Piso] = None):
        self.old = old or {}
        self.today = date.today().strftime("%Y-%m-%d")

    def get_pisos(self):
        r: list[Piso] = []
        with SiaDriver(wait=10) as w:
            w.get(Sia.URL)
            for page in w.iter_pages():
                for piso in w.iter_pisos():
                    ps = self.get_piso(w, piso)
                    r.append(ps)
        r = sorted(r, key=lambda x: x.id)
        return r

    def get_piso(self, w: Driver, id: int) -> Piso:
        @retry(times=3, sleep=3)
        def get_soup_vals():
            soup = w.get_soup()
            vals = tmap(get_val, soup.select(".form-group input"))
            if len(vals) < 12:
                raise BadSiaFicha()
            return soup, vals

        soup, vals = get_soup_vals()

        old = self.old.get(id) or Piso(id=-1, imgs=[], publicado=self.today)
        ps = Piso(
            id=id,
            publicado=old.publicado or self.today,
            direccion=vals[1],
            precio=vals[2],
            municipio="Madrid",
            distrito=vals[3],
            barrio=vals[4],
            dormitorios=vals[5],
            aseos=vals[6],
            planta=vals[7],
            cee=vals[8],
            orientacion=vals[9],
            adaptada=vals[10],
            ascensor=vals[11],
            reservada=soup.select_one(
                "img[src$='imagenes/RESERVADA.png']") is not None
        )
        for img in soup.select("div.rectanguloBusquedas input[src]"):
            img = get_text(img)
            img = img.rsplit("&", 1)[0]
            ps.imgs.append(img)
        ps.imgs = self.__parse_imgs(w, ps.imgs, old.imgs)

        ps.modificado = self.__get_update(ps)
        return ps

    def __get_update(self, ps: Piso):
        old = self.old.get(ps.id)
        if old is None:
            return None
        if ps.askey() == old.askey():
            return old.modificado
        return self.today

    def __parse_imgs(self, w: Driver, imgs: list[str], old: list[str]):
        if len(imgs) == 0 or ImgUr.get_client_id() is None:
            return imgs
        iup = ImgUr(
            session=w.pass_cookies()
        )

        imgur = {}
        for o in old:
            if "i.imgur.com" in o:
                imgur[o.split("?", 1)[-1]] = o

        def get_url(img: str):
            qry = get_query(img).get('idDoc')
            if qry is None:
                return img
            if qry in imgur:
                return imgur[qry]
            lnk = iup.safe_upload(img)
            if lnk is None:
                return img
            return lnk + '?' + qry

        return list(map(get_url, imgs))


if __name__ == "__main__":
    s = Sia()
    ps = s.get_pisos()
    import json

    print(json.dumps(ps, indent=2))
