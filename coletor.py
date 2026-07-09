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
import statistics
import sys
import time
import traceback
from collections import Counter
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
CABECALHO = ["data", "hora", "produto_id", "loja", "preco", "disponivel", "fonte"]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)


def buscar(url: str, timeout: int = 40, ua: str = None) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": ua or UA,
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


def preco_amazon(html: str):
    """
    Amazon usa formato proprio. A pagina traz varios precos (variacoes,
    preco por unidade/ml, ofertas de terceiros, patrocinados), entao a
    ordem de busca mira no elemento exato do "preco a pagar" do buy box.
    """
    def offscreen(trecho):
        m = re.search(r'class="a-offscreen">\s*R\$(?:&nbsp;|\s)*([\d.,]+)', trecho)
        return normalizar_preco(m.group(1)) if m else None

    # Coleta VARIOS candidatos e depois escolhe o mais confiavel.
    cands = []

    # precos visiveis, com a classe do container (que revela o tipo de preco)
    for m in re.finditer(
        r'<span class="a-price([^"]*)"[^>]*>.{0,150}?class="a-offscreen">\s*R\$(?:&nbsp;|\s)*([\d.,]+)',
        html, re.S,
    ):
        cls, v = m.group(1), normalizar_preco(m.group(2))
        if not v:
            continue
        # descarta preco por unidade (R$/ml, R$/g) e preco riscado "de:"
        if "pricePerUnit" in cls or "a-text-price" in cls:
            continue
        cands.append((v, "amazon-p2p" if "priceToPay" in cls else "amazon-vis"))

    # JSONs internos do buybox
    m = re.search(r'"desktop_buybox_group.{0,600}?"priceAmount"\s*:\s*([\d.]+)', html, re.S)
    if m and normalizar_preco(m.group(1)):
        cands.append((normalizar_preco(m.group(1)), "amazon-json"))
    m = re.search(r'"apexPriceToPay".{0,120}?R\$\D{0,12}([\d.,]+)', html, re.S)
    if m and normalizar_preco(m.group(1)):
        cands.append((normalizar_preco(m.group(1)), "amazon-apex"))

    prioridade = {"amazon-p2p": 0, "amazon-json": 1, "amazon-apex": 2, "amazon-vis": 3}
    cands.sort(key=lambda c: prioridade[c[1]])
    return cands


def escolher_preco(cands, referencia=None):
    """
    Escolhe o candidato mais confiavel:
      1. coerente com o historico do produto (entre 25% e 400% da mediana);
      2. senao, valor confirmado por 2+ fontes da pagina (consenso);
      3. senao, o de maior prioridade.
    """
    if not cands:
        return None
    contagem = Counter(round(v, 2) for v, _ in cands)
    if referencia:
        # 1) valor coerente com o historico (metade ao dobro da mediana)
        for v, tag in cands:
            if referencia * 0.5 <= v <= referencia * 2:
                return v, tag
        # 2) fora da faixa, so aceita se 2+ fontes da pagina confirmarem
        #    (ex.: promocao real de mais de 50%)
        for v, tag in cands:
            if contagem[round(v, 2)] >= 2:
                return v, tag
        # 3) nada confiavel: melhor falhar do que registrar preco errado
        return None
    for v, tag in cands:
        if contagem[round(v, 2)] >= 2:
            return v, tag
    return cands[0]


def imagem_amazon(html: str):
    m = re.search(r'"hiRes"\s*:\s*"(https://[^"]+)"', html) or \
        re.search(r'data-old-hires="(https://[^"]+)"', html)
    return m.group(1) if m else None


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


def lojas_de(produto: dict):
    """Retorna [(nome_da_loja, url), ...] — aceita formato novo (lojas) e antigo (url)."""
    def nome_padrao(url):
        return urlsplit(url).netloc.replace("www.", "")
    if produto.get("lojas"):
        return [(l.get("loja") or nome_padrao(l["url"]), l["url"]) for l in produto["lojas"] if l.get("url")]
    if produto.get("url"):
        return [(produto.get("loja") or nome_padrao(produto["url"]), produto["url"])]
    return []


def coletar_preco(url: str, referencia=None):
    """referencia = mediana dos ultimos precos conhecidos deste produto/loja,
    usada como validacao de sanidade contra leituras erradas."""
    if "mercadolivre.com" in url or "mercadolibre.com" in url:
        r = preco_mercado_livre(url)
        if r:
            return r

    r = preco_vtex(url)
    if r:
        return r

    eh_amazon = "amazon." in urlsplit(url).netloc

    # Amazon as vezes serve uma versao da pagina sem o bloco de preco;
    # tentar mais de uma vez (alternando desktop/celular) resolve na maioria.
    tentativas = 4 if eh_amazon else 1
    html = ""
    for t in range(tentativas):
        if t:
            time.sleep(6)
        html = buscar(url, ua=(UA_MOBILE if (eh_amazon and t % 2) else None))

        if eh_amazon:
            escolha = escolher_preco(preco_amazon(html), referencia)
            if escolha:
                preco, tag = escolha
                disp = ("add-to-cart-button" in html) or ("Em estoque" in html)
                return preco, disp, tag, imagem_amazon(html) or imagem_de_html(html)
            continue

        for estrategia in (preco_jsonld, preco_meta, preco_generico):
            r = estrategia(html)
            if r:
                preco, disp, fonte = r
                # validacao de sanidade tambem fora da Amazon
                if referencia and not (referencia * 0.25 <= preco <= referencia * 4):
                    continue
                return preco, disp, fonte, imagem_de_html(html)

    if "suspicious-traffic" in html or "account-verification" in html:
        raise ValueError("a loja bloqueou o acesso do robo (pagina anti-bot)")
    candidatos = re.findall(r'class="a-offscreen">([^<]{0,25})', html)[:8]
    if eh_amazon and candidatos and referencia:
        raise ValueError(
            f"precos na pagina nao batem com o historico (mediana R$ {referencia:.2f}) "
            f"e nao houve confirmacao — nada registrado. Vistos: {candidatos}"
        )
    amostra = re.sub(r"\s+", " ", html[:500])
    raise ValueError(
        f"nenhuma estrategia encontrou o preco (html {len(html)} chars; "
        f"precos visiveis: {candidatos}; inicio: {amostra})"
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

    # mediana dos ultimos precos por (produto, loja) — referencia de sanidade
    referencias = {}
    if ARQ_HISTORICO.exists():
        grupos = {}
        with ARQ_HISTORICO.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    grupos.setdefault((row["produto_id"], row.get("loja", "")), []).append(float(row["preco"]))
                except (ValueError, KeyError):
                    pass
        referencias = {k: statistics.median(v[-7:]) for k, v in grupos.items()}

    for p in ativos:
        for nome_loja, url in lojas_de(p):
            try:
                ref = referencias.get((p["id"], nome_loja))
                preco, disponivel, fonte, imagem = coletar_preco(url, ref)
                if imagem and not p.get("imagem"):
                    p["imagem"] = imagem
                    produtos_alterados = True
                    print(f"IMG  {p['id']}: imagem encontrada automaticamente")
                linhas.append([
                    agora.strftime("%Y-%m-%d"),
                    agora.strftime("%H:%M"),
                    p["id"],
                    nome_loja,
                    f"{preco:.2f}",
                    "sim" if disponivel else "nao",
                    fonte,
                ])
                print(f"OK   {p['id']} @ {nome_loja}: R$ {preco:.2f} ({fonte})")
            except Exception as e:
                falhas.append(f"{p['id']} @ {nome_loja}")
                print(f"ERRO {p['id']} @ {nome_loja}: {type(e).__name__}: {e}", file=sys.stderr)
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
