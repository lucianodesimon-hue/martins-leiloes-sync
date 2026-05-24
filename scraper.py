#!/usr/bin/env python3
"""
Scraper Martins Leilões / Bomvalor — equivalente Python do scraper.php

Roda no GitHub Actions (IPs Azure, normalmente não bloqueados pelo WAF da Martins).
Gera data/leiloes_bomvalor.json que o site PHP em produção consome via raw.githubusercontent.com.

Dependências: requests, beautifulsoup4, lxml.
Saída: JSON no stdout (workflow GH Action redireciona pra arquivo + commita).
"""

from __future__ import annotations
import json
import re
import sys
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup

FONTE = "https://martinsleiloes.com.br"
TIMEOUT = 20

# Headers de Chrome real — replicam o que browser envia. Necessário pra bypassar WAF.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def log(msg: str) -> None:
    """Loga em stderr pra não atrapalhar o JSON no stdout."""
    print(msg, file=sys.stderr, flush=True)


def fetch(url: str) -> Optional[str]:
    """GET com headers de browser. Retorna HTML ou None."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code == 200 and r.text:
            log(f"  ✓ {url} → HTTP 200 ({len(r.text)} bytes)")
            return r.text
        log(f"  ✗ {url} → HTTP {r.status_code} (tamanho {len(r.text)} bytes)")
        return None
    except requests.RequestException as e:
        log(f"  ✗ {url} → erro: {e}")
        return None


def sem_acento(s: str) -> str:
    """Remove acentos pra comparações sem ambiguidade."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def categoria_da_url(href_e_titulo: str) -> str:
    """Mesma lógica do scraper.php — categoria a partir do texto."""
    t = sem_acento(href_e_titulo.lower())

    if "/imoveis/" in t:
        return "imoveis"
    if (
        "imovei" in t
        or "apartamento" in t
        or re.search(r"\b(casa|casas)\b", t)
        or "galpao" in t
        or "terreno" in t
        or "hotel-fazenda" in t
        or "fazenda" in t
        or "sitio" in t
        or "chacara" in t
    ):
        return "imoveis"
    if "sucata" in t or "residuo" in t or "reciclav" in t:
        return "sucatas"
    if "industrial" in t or "usina" in t or "fabrica" in t:
        return "industrial"
    if (
        "tecnolog" in t
        or "eletronic" in t
        or "informatic" in t
        or "servidor" in t
    ):
        return "tecnologia"
    if (
        "animais" in t
        or "animal" in t
        or "gado" in t
        or "bovino" in t
        or "equino" in t
    ):
        return "animais"
    if (
        "/bens-diversos/" in t
        or "maquina" in t
        or "caminh" in t
        or "trator" in t
        or "empilhad" in t
        or "linha-amarela" in t
        or "linha amarela" in t
        or "escavadeira" in t
        or "retro" in t
    ):
        return "maquinas"
    if (
        "/veiculos/" in t
        or "veicul" in t
        or "pajero" in t
        or "carreta" in t
        or "randon" in t
        or "mitsubishi" in t
        or "fiduciar" in t
    ):
        return "veiculos"
    return "outros"


def slug_from_href(href: str) -> str:
    """Último segmento do path como slug."""
    path = href.split("?")[0].rstrip("/")
    return path.split("/")[-1] or href.replace("/", "-")


def rx_int(s: str, pattern: str) -> Optional[int]:
    m = re.search(pattern, s)
    return int(m.group(1)) if m else None


def parse_evento_card(card, source_for_cat: str = "") -> Optional[dict[str, Any]]:
    """Extrai um leilão de um <a class='card-home link-leilao'> (ou similar)."""
    href = card.get("href", "")
    if not href:
        return None
    if not href.startswith("/"):
        if not href.startswith(FONTE):
            return None
        href = href[len(FONTE):]

    texto = re.sub(r"\s+", " ", card.get_text(" ", strip=True))

    id_origem = rx_int(texto, r"ID:\s*(\d+)")
    if not id_origem:
        # tenta extrair do final da URL (-12345)
        id_origem = rx_int(href, r"-(\d{4,7})(?:/|$)")
    if not id_origem:
        return None

    titulo = ""
    titulo_node = card.find(class_=re.compile("titulo|card-title"))
    if titulo_node:
        titulo = titulo_node.get_text(strip=True)
    if not titulo:
        titulo_node = card.find(["h2", "h3"])
        if titulo_node:
            titulo = titulo_node.get_text(strip=True)
    if not titulo:
        titulo = texto[:80]

    img = ""
    img_node = card.find("img", src=re.compile("banner_leilao"))
    if not img_node:
        img_node = card.find("img", src=re.compile("cloudfront"))
    if not img_node:
        img_node = card.find("img")
    if img_node:
        img = img_node.get("src", "")

    lotes_qtd = rx_int(texto, r"(\d+)\s+Lotes?")
    status = "agendado"
    if re.search(r"ABERTO|EM PREG", texto, re.I):
        status = "aberto"
    elif re.search(r"ENCERRADO", texto, re.I):
        status = "encerrado"

    fim_str = None
    m = re.search(r"Encerramento.*?(\d{2}/\d{2}/\d{4})\s*(\d{2}:\d{2})?", texto)
    if m:
        try:
            fim_dt = datetime.strptime(
                f"{m.group(1)} {m.group(2) or '18:00'}", "%d/%m/%Y %H:%M"
            )
            fim_str = fim_dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    cat_source = source_for_cat or (titulo + " " + href)
    categoria = categoria_da_url(cat_source)

    agora = datetime.now()
    fim_default = (agora + timedelta(days=15)).strftime("%Y-%m-%d %H:%M:%S")

    return {
        "id_origem": id_origem,
        "slug": slug_from_href(href),
        "titulo": titulo,
        "categoria": categoria,
        "subcategoria": "",
        "cidade": "",
        "uf": "",
        "lance_inicial": 0,
        "lance_atual": 0,
        "avaliacao": 0,
        "desconto": 0,
        "inicio": agora.strftime("%Y-%m-%d %H:%M:%S"),
        "fim": fim_str or fim_default,
        "tipo": "judicial" if "judic" in titulo.lower() else "extrajudicial",
        "imagem": img,
        "edital_url": "#",
        "lance_url": FONTE + href,
        "descricao": f"{titulo}. Leilão conduzido pela Martins Leilões. "
        + (f"{lotes_qtd} lotes disponíveis." if lotes_qtd else ""),
        "destaque": True,
        "lotes_qtd": lotes_qtd,
        "origem_url": FONTE + href,
    }


def coletar_eventos_home() -> list[dict[str, Any]]:
    log(f"Coletando home: {FONTE}/")
    html = fetch(FONTE + "/")
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")

    cards = soup.find_all("a", class_=lambda c: c and "card-home" in c and "link-leilao" in c)
    log(f"  {len(cards)} cards encontrados na home")

    eventos = []
    vistos = set()
    for card in cards:
        evt = parse_evento_card(card)
        if evt and evt["id_origem"] not in vistos:
            eventos.append(evt)
            vistos.add(evt["id_origem"])
    return eventos


def coletar_busca_categoria(slug: str) -> list[dict[str, Any]]:
    url = f"{FONTE}/busca/categoriaProduto/{slug}"
    log(f"Coletando categoria '{slug}': {url}")
    html = fetch(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")

    # tenta seletores em ordem de especificidade
    selectors = [
        {"name": "a", "class_": lambda c: c and "card-home" in c and "link-leilao" in c},
        {"name": "a", "class_": lambda c: c and "card" in c, "href": re.compile(r"/leilao-|/evento")},
        {"name": "a", "href": re.compile(r"/leilao-")},
    ]
    cards = []
    for sel in selectors:
        cards = soup.find_all(**sel)
        if cards:
            break
    log(f"  {len(cards)} cards encontrados")

    eventos = []
    vistos = set()
    for card in cards:
        # força categoria pelo slug do path
        evt = parse_evento_card(card)
        if evt and evt["id_origem"] not in vistos:
            evt["categoria"] = slug
            evt["destaque"] = False
            eventos.append(evt)
            vistos.add(evt["id_origem"])
    return eventos


def sincronizar() -> list[dict[str, Any]]:
    """Coleta home + 4 categorias extras, dedup por id_origem."""
    eventos = coletar_eventos_home()
    vistos = {e["id_origem"] for e in eventos}

    for slug in ("industrial", "tecnologia", "animais", "sucatas"):
        try:
            extras = coletar_busca_categoria(slug)
            for e in extras:
                if e["id_origem"] not in vistos:
                    eventos.append(e)
                    vistos.add(e["id_origem"])
        except Exception as err:
            log(f"  ✗ falha em {slug}: {err}")

    return eventos


def main() -> int:
    log(f"=== Sync Bomvalor — {datetime.now().isoformat()} ===")
    eventos = sincronizar()
    log(f"\nTotal: {len(eventos)} leilões")

    if not eventos:
        log("ERRO: zero leilões coletados — WAF da Martins pode estar bloqueando o IP do runner.")
        return 1

    # Resumo por categoria pra log
    from collections import Counter
    cats = Counter(e["categoria"] for e in eventos)
    log("\nDistribuição:")
    for c, n in cats.most_common():
        log(f"  {c}: {n}")

    # Saída: JSON no stdout (workflow redireciona pra arquivo)
    print(json.dumps(eventos, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
