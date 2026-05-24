# Sync Bomvalor — Martins Leilões

Scraper rodando no GitHub Actions a cada 15 min. Substitui o scraper PHP da DreamHost (bloqueado pelo WAF da Martins).

## Setup rápido

1. Repo já criado (este).
2. Workflow roda a cada 15min automaticamente.
3. O resultado vai em `data/leiloes_bomvalor.json` neste repositório.
4. O site PHP consome via raw URL:
   `https://raw.githubusercontent.com/lucianodesimon-hue/martins-leiloes-sync/main/data/leiloes_bomvalor.json`
5. URL deve ser colada em `site/lib/config.php` no campo `integracao_bomvalor.fonte_externa`.

## Rodar manualmente

Actions → Sync Leiloes Bomvalor → Run workflow.

## Limites

- GitHub Actions free: ilimitado em repos públicos
- Workflow leva ~30s por run

## Plano D (se WAF da Martins bloquear IPs do Azure também)

- Cloudflare Workers (100k req/dia grátis)
- BrightData Web Unlocker (~US$0.001/req)
- Render.com cron job
