# 📉 Monitor de Preços

Monitor automático de preços de produtos. Roda sozinho na nuvem (GitHub Actions), de graça, uma vez por dia — não depende de nenhum computador ligado.

**Dashboard:** https://fcairo1.github.io/claude-price-monitor/

## Como funciona

1. `produtos.json` guarda a lista de produtos monitorados
2. Todo dia às ~6h (horário de Brasília), o GitHub Actions roda o `coletor.py`, que visita a página de cada produto, extrai o preço e adiciona uma linha no `historico.csv`
3. O dashboard (GitHub Pages) lê esses arquivos e mostra: preço atual, variação, mínimo histórico, média das últimas 30 coletas, gráfico de flutuação e o selo **🔥 BOM MOMENTO** quando vale a pena comprar

### Quando aparece o "bom momento"?

- Produto com **preço-alvo**: quando o preço atual fica igual ou abaixo do alvo
- Qualquer produto: quando bate o **mínimo histórico** (com 5+ dias de dados) ou fica **3%+ abaixo da média** das últimas 30 coletas (com 7+ dias de dados)

## Como adicionar/editar produtos

**Pelo dashboard (funciona no celular):** botão "+ Adicionar produto". No primeiro uso em cada aparelho, o painel pede um token do GitHub (instruções aparecem na tela). Ao salvar, a coleta do preço é disparada na hora.

**Pelo GitHub:** edite o `produtos.json` direto (site ou app do GitHub). Formato:

```json
{
  "id": "identificador-unico",
  "nome": "Nome que aparece no painel",
  "url": "https://www.loja.com.br/produto/p",
  "loja": "Nome da loja",
  "tipo": "recorrente",
  "preco_alvo": 99.90,
  "ativo": true
}
```

- `tipo`: `"recorrente"` (compra frequente) ou `"futura"` (compra planejada)
- `preco_alvo`: número ou `null`
- `ativo`: `false` pausa a coleta sem perder o histórico

## Lojas suportadas

- **Mercado Livre** — via API pública
- **Lojas VTEX** (Creamy, Época Cosméticos, muitas outras) — via metadados da página
- **Qualquer loja** que exponha preço em JSON-LD/meta tags (a maioria) — o coletor tenta 4 estratégias em ordem

Se alguma loja bloquear a coleta, a falha aparece no log da aba **Actions** sem travar os outros produtos.

## Rodar a coleta manualmente

Aba **Actions** → "Coleta diária de preços" → **Run workflow**.

---

*Criado com Claude 🤖*
