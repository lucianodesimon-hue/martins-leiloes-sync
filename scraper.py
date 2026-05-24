#!/usr/bin/env python3
"""Scraper Martins Leilões — usa Cloudflare Worker como proxy."""
from __future__ import annotations
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup

FONTE = "https://martinsleiloes.com.br"
TIMEOUT = 20
WORKER_URL = os.environ.get("WORKER_URL", "").rstrip("/")
WORKER_KEY = os.environ.get("WORKER_KEY", "")

CATEGORIA_FALLBACK_IMG = {
    "veiculos":   "https://d3r4ngrkezrhn6.cloudfront.net/public/bomvalorjudicial/fotos/veiculos/327x244/suv_mmc_pajero_tr4_flex_preto-156124-1.jpg",
    "maquinas":   "https://d3r4ngrkezrhn6.cloudfront.net/public/cba/banner_leilao/34797_17785264021.jpg",
    "imoveis":    "https://d3r4ngrkezrhn6.cloudfront.net/public/resale/fotos/imoveis/327x244/casa-sao-paulo-131490-6.jpg",
    "industrial": "https://d3r4ngrkezrhn6.cloudfront.net/public/bomvalorjudicial/fotos/imoveis/327x244/galpao_industrial_tres_coracoes-591874-7.jpg",
    "sucatas":    "https://d3r4ngrkezrhn6.cloudfront.net/public/suzano/fotos/equipamentos/327x244/sucata_industrial-097536-9.jpg",
    "tecnologia": "",
    "animais":    "",
    "outros":     "",
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def is_logo_ou_vazio(src: str) -> bool:
    if not src:
        return True
    s = src.lower()
    return ("/logo" in s or "tipo-leilao" in s or "/layout/" in s
            or "logo-header" in s or s.endswith("/logo.png") or s.endswith("/logo.jpg"))


def fetch(url: str) -> Optional[str]:
    """GET via Cloudflare Worker (bypassa WAF da Martins)."""
    if WORKER_URL and WORKER_KEY:
        proxy = f"{WORKER_URL}/?url={requests.utils.quote(url, safe='')}&key={WORKER_KEY}"
        log(f"  → via worker: {url}")
        try:
            r = requests.get(proxy, timeout=30)
            if r.status_code == 200 and r.text:
                log(f"  ✓ {url} → HTTP 200 ({len(r.text)} bytes) [worker]")
                return r.text
            log(f"  ✗ {url} → worker HTTP {r.status_code}")
            return None
        except requests.RequestException as e:
            log(f"  ✗ {url} → worker erro: {e}")
            return None
    # Fallback direto (provavelmente bloqueado)
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 Chrome/120"}, timeout=TIMEOUT)
        if r.status_code == 200 and r.text:
            return r.text
        log(f"  ✗ {url} → HTTP {r.status_code}")
        return None
    except requests.RequestException as e:
        log(f"  ✗ {url} → erro: {e}")
        return None


def sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def categoria_da_url(href_e_titulo: str) -> str:
    t = sem_acento(href_e_titulo.lower())
    if "/imoveis/" in t:
        return "imoveis"
    if any(k in t for k in ("imovei", "apartamento", "galpao", "terreno", "hotel-fazenda", "fazenda", "sitio", "chacara")):
        return "imoveis"
    if re.search(r"\b(casa|casas)\b", t):
        return "imoveis"
    if any(k in t for k in ("sucata", "residuo", "reciclav")):
        return "sucatas"
    if any(k in t for k in ("industrial", "usina", "fabrica")):
        return "industrial"
    if any(k in t for k in ("tecnolog", "eletronic", "informatic", "servidor")):
        return "tecnologia"
    if any(k in t for k in ("animais", "animal", "gado", "bovino", "equino")):
        return "animais"
    if any(k in t for k in ("/bens-diversos/", "maquina", "caminh", "trator", "empilhad",
                            "linha-amarela", "linha amarela", "escavadeira", "retro")):
        return "maquinas"
    if any(k in t for k in ("/veiculos/", "veicul", "pajero", "carreta", "randon",
                            "mitsubishi", "fiduciar")):
        return "veiculos"
    return "outros"


def slug_from_href(href: str) -> str:
    path = href.split("?")[0].rstrip("/")
    return path.split("/")[-1] or href.replace("/", "-")


def rx_int(s: str, pattern: str) -> Optional[int]:
    m = re.search(pattern, s)
    return int(m.group(1)) if m else None


def extrair_imagem(card) -> str:
    """Extrai imagem: prioriza background-image do carrossel, depois <img banner_leilao>."""
    fotos = card.find_all(class_=re.compile(r"carousel-item.*fotos"))
    for f in fotos:
        style = f.get("style", "") or ""
        m = re.search(r"url\(([^)]+)\)", style)
        if m:
            url = m.group(1).strip().strip("'\"")
            if "banner_leilao" in url or "/fotos/" in url:
                return url
    inode = (card.find("img", src=re.compile("banner_leilao"))
             or card.find("img", src=re.compile(r"/fotos/"))
             or card.find("img"))
    return inode.get("src", "") if inode else ""


def extrair_titulo(card, texto: str) -> str:
    for cls in ("status-leilao", "titulo", "card-title"):
        node = card.find(class_=cls)
        if node:
            t = node.get_text(strip=True)
            if t:
                return t
    h = card.find(["h2", "h3"])
    if h:
        return h.get_text(strip=True)
    return texto[:80]


def parse_card(card, destaque: bool) -> Optional[dict[str, Any]]:
    href = card.get("href", "")
    if not href or not href.startswith("/"):
        return None

    texto = re.sub(r"\s+", " ", card.get_text(" ", strip=True))

    id_origem = rx_int(texto, r"ID:\s*(\d+)") or rx_int(href, r"-(\d{4,7})(?:/|$)")
    if not id_origem:
        return None

    titulo = extrair_titulo(card, texto)
    img = extrair_imagem(card)
    categoria = categoria_da_url(titulo + " " + href)

    if is_logo_ou_vazio(img):
        img = CATEGORIA_FALLBACK_IMG.get(categoria, "")

    status = "agendado"
    if re.search(r"ABERTO PARA LANCES|ABERTO|EM PREG", texto, re.I):
        status = "aberto"
    elif re.search(r"ENCERRADO", texto, re.I):
        status = "encerrado"

    fim_str = None
    m = re.search(r"Encerramento.*?(\d{2}/\d{2}/\d{4})\s*(\d{2}:\d{2})?", texto)
    if m:
        try:
            dt = datetime.strptime(f"{m.group(1)} {m.group(2) or '18:00'}", "%d/%m/%Y %H:%M")
            fim_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    lotes_qtd = rx_int(texto, r"(\d+)\s+Lotes?")
    agora = datetime.now()
    inicio = (agora - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
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
        "inicio": inicio,
        "fim": fim_str or fim_default,
        "tipo": "judicial" if "judic" in titulo.lower() else "extrajudicial",
        "imagem": img,
        "edital_url": "#",
        "lance_url": FONTE + href,
        "descricao": f"{titulo}. Leilão conduzido pela Martins Leilões." + (
            f" {lotes_qtd} lotes disponíveis." if lotes_qtd else ""),
        "destaque": destaque,
        "status_origem": status,
        "lotes_qtd": lotes_qtd,
        "origem_url": FONTE + href,
    }


def coletar_destaques() -> list[dict[str, Any]]:
    log(f"Coletando destaques: {FONTE}/")
    html = fetch(FONTE + "/")
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select(".destaques-row .destaque-item a.leilao-container")
    log(f"  {len(cards)} cards de destaque")
    eventos: list[dict[str, Any]] = []
    vistos: set[int] = set()
    for c in cards:
        e = parse_card(c, destaque=True)
        if e and e["id_origem"] not in vistos:
            eventos.append(e)
            vistos.add(e["id_origem"])
    return eventos


def coletar_proximos() -> list[dict[str, Any]]:
    log(f"Coletando próximos leilões: {FONTE}/")
    html = fetch(FONTE + "/")
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    cards = soup.find_all("a", class_=lambda c: c and "card-home" in c and "link-leilao" in c)
    log(f"  {len(cards)} cards 'próximos'")
    eventos: list[dict[str, Any]] = []
    vistos: set[int] = set()
    for c in cards:
        e = parse_card(c, destaque=False)
        if e and e["id_origem"] not in vistos:
            eventos.append(e)
            vistos.add(e["id_origem"])
    return eventos


def coletar_categoria(slug: str) -> list[dict[str, Any]]:
    url = f"{FONTE}/busca/categoriaProduto/{slug}"
    log(f"Coletando categoria '{slug}'")
    html = fetch(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("a.leilao-container, a.card-home.link-leilao")
    log(f"  {len(cards)} cards")
    eventos: list[dict[str, Any]] = []
    vistos: set[int] = set()
    for c in cards:
        e = parse_card(c, destaque=False)
        if e and e["id_origem"] not in vistos:
            e["categoria"] = slug
            eventos.append(e)
            vistos.add(e["id_origem"])
    return eventos


def sincronizar() -> list[dict[str, Any]]:
    eventos = coletar_destaques()
    vistos = {e["id_origem"] for e in eventos}
    for e in coletar_proximos():
        if e["id_origem"] not in vistos:
            eventos.append(e)
            vistos.add(e["id_origem"])
    for slug in ("industrial", "tecnologia", "animais", "sucatas"):
        try:
            for e in coletar_categoria(slug):
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
        log("ERRO: zero leilões coletados.")
        return 1
    from collections import Counter
    cats = Counter(e["categoria"] for e in eventos)
    log("\nDistribuição:")
    for c, n in cats.most_common():
        log(f"  {c}: {n}")
    print(json.dumps(eventos, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

