#!/usr/bin/env python3
"""
Coletor de precos do Monitor de Precos.

Le produtos.json, busca o preco atual de cada produto ativo e adiciona
uma linha em historico.csv. Roda diariamente via GitHub Actions.

Estrategias de extracao (em ordem):
  1. API do Mercado Livre (para links do mercadolivre.com.br)
  2. JSON-LD (schema.org/Product) embutido na pagina
  3. Meta tags Open Graph (product:price:amount) - usado por lojas VTEX como a Creamy
  4. Regex generica em atributos itemprop/price

Uso apenas de biblioteca padrao do Python (sem dependencias).
"""

import csv
import gzip
import json
import re
import sys
import traceback
import urllib.request
import urllib.error
from urllib.parse import urlsplit
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Sao_Paulo")
BASE = Path(__file__).parent
ARQ_PRODUTOS = BASE / "produtos.json"
ARQ_HISTORICO = BASE / "historico.csv"
CABECALHO = ["data", "hora", "produto_id", "preco", "disponivel", "fonte"]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def buscar(url: str, timeout: int = 40) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        dados = r.read()
    if dados[:2] == b"\x1f\x8b":  # resposta gzipada
        dados = gzip.decompress(dados)
    return dados.decode("utf-8", errors="replace")


def normalizar_preco(bruto) -> float | None:
    """Converte '1.299,90', '129.99', 129.99 etc. em float."""
    if bruto is None:
        return None
    if isinstance(bruto, (int, float)):
        v = float(bruto)
        return v if v > 0 else None
    s = re.sub(r"[^\d.,]", "", str(bruto))
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


# ---------------------------------------------------------------- estrategias

def imagem_de_html(html: str):
    padroes = [
        r'(?:property|name)=["\']og:image["\'][^>]*content=["\']([^"\']+)',
        r'content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']og:image["\']',
    ]
    for p in padroes:
        m = re.search(p, html, re.I)
        if m and m.group(1).startswith("http"):
            return m.group(1)
    return None


def preco_mercado_livre(url: str):
    m = re.search(r"(MLB)-?(\d+)", url, re.I)
    if not m:
        return None
    item_id = f"{m.group(1).upper()}{m.group(2)}"
    try:
        dados = json.loads(buscar(f"https://api.mercadolibre.com/items/{item_id}"))
        preco = normalizar_preco(dados.get("price"))
        if preco:
            disponivel = dados.get("status") == "active" and dados.get("available_quantity", 0) > 0
            fotos = dados.get("pictures") or []
            imagem = (fotos[0].get("secure_url") if fotos else None) or dados.get("secure_thumbnail") or dados.get("thumbnail")
            return preco, disponivel, "api-mercadolivre", imagem
    except Exception:
        pass
    return None  # cai para as estrategias genericas na pagina


def _iterar_objetos(dado):
    if isinstance(dado, dict):
        yield dado
        for v in dado.values():
            yield from _iterar_objetos(v)
    elif isinstance(dado, list):
        for item in dado:
            yield from _iterar_objetos(item)


def preco_jsonld(html: str):
    padrao = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
    for bloco in re.finditer(padrao, html, re.S | re.I):
        try:
            dado = json.loads(bloco.group(1).strip())
        except Exception:
            continue
        for obj in _iterar_objetos(dado):
            if "price" in obj or "lowPrice" in obj:
                preco = normalizar_preco(obj.get("price") or obj.get("lowPrice"))
                if preco:
                    disp = "instock" in str(obj.get("availability", "instock")).lower()
                    return preco, disp, "json-ld"
    return None


def preco_meta(html: str):
    padroes = [
        r'(?:property|name)=["\'](?:product:price:amount|og:price:amount)["\'][^>]*content=["\']([^"\']+)',
        r'content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\'](?:product:price:amount|og:price:amount)',
    ]
    for p in padroes:
        m = re.search(p, html, re.I)
        if m:
            preco = normalizar_preco(m.group(1))
            if preco:
                disp = bool(re.search(r'product:availability["\'][^>]*content=["\']\s*instock', html, re.I)) or \
                       bool(re.search(r'content=["\']\s*instock["\'][^>]*product:availability', html, re.I)) or \
                       "instock" in html.lower()
                return preco, disp, "meta-og"
    return None


def preco_generico(html: str):
    padroes = [
        r'itemprop=["\']price["\'][^>]*content=["\']([^"\']+)',
        r'"price"\s*:\s*"?([\d.,]+)"?',
        r'data-price=["\']([^"\']+)',
    ]
    for p in padroes:
        m = re.search(p, html, re.I)
        if m:
            preco = normalizar_preco(m.group(1))
            if preco:
                return preco, True, "regex"
    return None


def preco_vtex(url: str):
    """Lojas VTEX (URLs terminando em /p): API publica de catalogo."""
    partes = urlsplit(url)
    caminho = partes.path.rstrip("/")
    if not caminho.endswith("/p"):
        return None
    link_text = caminho[:-2].strip("/").split("/")[-1]
    api = f"{partes.scheme}://{partes.netloc}/api/catalog_system/pub/products/search/{link_text}/p"
    try:
        dados = json.loads(buscar(api, timeout=30))
    except Exception:
        return None
    if not isinstance(dados, list) or not dados:
        return None
    ofertas = []
    for item in dados[0].get("items", []):
        for seller in item.get("sellers", []):
            oferta = seller.get("commertialOffer") or {}
            preco = normalizar_preco(oferta.get("Price"))
            if preco:
                ofertas.append((preco, oferta.get("AvailableQuantity", 0) > 0))
    if not ofertas:
        return None
    disponiveis = [o for o in ofertas if o[1]]
    preco, disp = min(disponiveis or ofertas, key=lambda o: o[0])
    imagem = None
    for item in dados[0].get("items", []):
        imgs = item.get("images") or []
        if imgs and imgs[0].get("imageUrl"):
            imagem = imgs[0]["imageUrl"]
            break
    return preco, disp, "api-vtex", imagem


def coletar_preco(produto: dict):
    url = produto["url"]

    if "mercadolivre.com" in url or "mercadolibre.com" in url:
        r = preco_mercado_livre(url)
        if r:
            return r

    r = preco_vtex(url)
    if r:
        return r

    html = buscar(url)
    for estrategia in (preco_jsonld, preco_meta, preco_generico):
        r = estrategia(html)
        if r:
            preco, disp, fonte = r
            return preco, disp, fonte, imagem_de_html(html)
    amostra = re.sub(r"\s+", " ", html[:800])
    raise ValueError(
        f"nenhuma estrategia encontrou o preco (html {len(html)} chars; inicio: {amostra})"
    )


# ----------------------------------------------------------------------- main

def main() -> int:
    dados = json.loads(ARQ_PRODUTOS.read_text(encoding="utf-8"))
    produtos = dados["produtos"]
    ativos = [p for p in produtos if p.get("ativo", True)]
    if not ativos:
        print("Nenhum produto ativo para coletar.")
        return 0

    agora = datetime.now(TZ)
    linhas, falhas = [], []
    produtos_alterados = False

    for p in ativos:
        try:
            preco, disponivel, fonte, imagem = coletar_preco(p)
            if imagem and not p.get("imagem"):
                p["imagem"] = imagem
                produtos_alterados = True
                print(f"IMG  {p['id']}: imagem encontrada automaticamente")
            linhas.append([
                agora.strftime("%Y-%m-%d"),
                agora.strftime("%H:%M"),
                p["id"],
                f"{preco:.2f}",
                "sim" if disponivel else "nao",
                fonte,
            ])
            print(f"OK   {p['id']}: R$ {preco:.2f} ({fonte})")
        except Exception as e:
            falhas.append(p["id"])
            print(f"ERRO {p['id']}: {type(e).__name__}: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    if linhas:
        existe = ARQ_HISTORICO.exists()
        with ARQ_HISTORICO.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if not existe:
                w.writerow(CABECALHO)
            w.writerows(linhas)

    if produtos_alterados:
        ARQ_PRODUTOS.write_text(
            json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    print(f"\nColeta: {len(linhas)} ok, {len(falhas)} falha(s).")
    if falhas and not linhas:
        return 1  # tudo falhou: sinaliza erro no workflow
    return 0


if __name__ == "__main__":
    sys.exit(main())
