# CLAUDE.md — Monitor de Preços

Contexto completo do projeto para continuidade do desenvolvimento no Claude Code.
Última atualização: 30/08/2026.

---

## 1. O que é

Monitor automático de preços de produtos em lojas brasileiras. Roda sozinho na nuvem
(GitHub Actions), sem servidor e sem custo, e publica um dashboard estático que o
dono (Filipe, @Fcairo1) abre de qualquer lugar — inclusive do celular.

**Dois casos de uso que motivaram o projeto:**

1. **Compra futura** — produto caro que ele quer comprar quando cair de preço
   (`tipo: "futura"`, opcionalmente com `preco_alvo`).
2. **Compra recorrente** — produto que ele repõe sempre e quer comprar no melhor
   momento do ciclo (`tipo: "recorrente"`, comparado contra a média histórica).

**Links:**

| O quê | Onde |
|---|---|
| Dashboard (público, sem senha) | https://fcairo1.github.io/claude-price-monitor/ |
| Repositório | https://github.com/Fcairo1/claude-price-monitor |
| Execuções do robô | https://github.com/Fcairo1/claude-price-monitor/actions |
| Secrets | https://github.com/Fcairo1/claude-price-monitor/settings/secrets/actions |

**Idioma:** todo o projeto (código, comentários, commits, UI) está em **português**.
Identificadores no código Python evitam acentos por convenção adotada no início;
a UI e as mensagens ao usuário usam acentuação normal. Manter esse padrão.

---

## 2. Arquitetura

```
                     ┌───────────────────────────────────┐
  cron diário ──────►│  GitHub Actions (coleta.yml)      │
  push em            │  → python coletor.py              │
  produtos.json      │  → commita historico.csv +        │
  botão no dashboard │    coleta.log + produtos.json     │
                     └──────────────┬────────────────────┘
                                    │ git commit/push
                                    ▼
                     ┌───────────────────────────────────┐
   produtos.json ◄───┤  Repositório (branch main)        │
   historico.csv     │  é o banco de dados do projeto    │
   coleta.log        └──────────────┬────────────────────┘
                                    │ raw.githubusercontent / API
                                    ▼
                     ┌───────────────────────────────────┐
                     │  index.html (GitHub Pages)        │
                     │  lê os arquivos, desenha gráficos │
                     │  escreve via API do GitHub        │
                     └───────────────────────────────────┘
```

**Princípios de design que devem ser preservados:**

- **Zero infraestrutura.** Sem servidor, sem banco, sem chaves de API pagas. O
  repositório Git é o banco de dados; o histórico de commits é o log de auditoria.
- **Zero dependências Python.** `coletor.py` usa **apenas biblioteca padrão**
  (`urllib`, `re`, `json`, `csv`, `gzip`, `statistics`). Nada de `requests`,
  `beautifulsoup`, `pandas`. Isso mantém o workflow rápido (~15 s) e sem
  `pip install`. **Não adicionar dependências sem necessidade real.**
- **Dashboard é um arquivo só.** `index.html` tem HTML, CSS e JS inline. A única
  dependência externa é Chart.js via CDN. Sem build, sem npm, sem framework.
- **Melhor não registrar do que registrar errado.** Toda a lógica de sanidade
  (seção 6) existe porque preço errado polui o histórico e dispara alertas falsos.
  Em caso de dúvida, o coletor falha e explica no log.

---

## 3. Arquivos do repositório

```
claude-price-monitor/
├── coletor.py                    # 620 linhas — o robô que busca os preços
├── index.html                    # 698 linhas — dashboard completo (HTML+CSS+JS)
├── produtos.json                 # lista de produtos monitorados (entrada + estado)
├── historico.csv                 # banco de dados de preços (append-only)
├── coleta.log                    # saída da última execução (sobrescrito a cada run)
├── README.md                     # documentação para humanos
├── CLAUDE.md                     # este arquivo
├── .gitignore                    # __pycache__, *.pyc, .DS_Store
└── .github/workflows/
    ├── coleta.yml                # coleta diária + manual + on-push
    └── pages.yml                 # publica o dashboard no GitHub Pages
```

Não existe pasta `imagens/` versionada por padrão — ela é criada sob demanda quando
o usuário faz upload de uma foto pelo dashboard (ver seção 7.4).

---

## 4. Esquemas de dados

### 4.1 `produtos.json`

É simultaneamente **entrada** (o que monitorar) e **estado** (o coletor grava
`imagem` aqui quando descobre a foto). Editado por três caminhos: o dashboard, o
GitHub web/app, e o próprio `coletor.py`.

```jsonc
{
  "produtos": [
    {
      "id": "creamy-eye-cream",          // slug único; chave do histórico. NÃO mudar depois de criado
      "nome": "Creme Clareador para Olhos - Eye Cream (Creamy)",
      "tipo": "recorrente",              // "recorrente" | "futura"
      "preco_alvo": null,                // número ou null — dispara "bom momento" quando atingido
      "ativo": true,                     // false pausa a coleta sem perder histórico
      "imagem": "https://...jpg",        // preenchido automaticamente na 1ª coleta se estiver null
      "lojas": [                         // 1..N lojas do MESMO produto (comparação estilo Zoom)
        {
          "loja": "Creamy",              // rótulo; é a CHAVE da coluna `loja` no histórico
          "url": "https://www.creamy.com.br/eye-cream/p",
          "variante": null               // ex.: "100 ML" quando a página tem dropdown de tamanho
        }
      ],
      "compras": [                       // registro manual de compras do usuário (opcional)
        { "data": "2026-07-30", "preco": 122 }
      ]
    }
  ]
}
```

**Formato legado ainda suportado:** produtos antigos podiam ter `url` e `loja` no
nível raiz em vez de `lojas[]`. `lojas_de()` (coletor) e `lojasDe()` (dashboard)
normalizam os dois formatos. Nenhum produto usa mais o formato antigo, mas o
suporte é barato — só remova se for fazer uma migração explícita.

**Cuidado com `id`:** ele liga o produto ao `historico.csv`. Trocar o `id` órfã
todo o histórico. O dashboard gera o id por slug do nome na criação e **nunca**
o altera em edições (`editandoId` é reusado).

**Cuidado com `loja`:** o nome da loja também é chave no histórico (par
`produto_id` + `loja`). Renomear uma loja quebra a série daquele gráfico.

### 4.2 `historico.csv`

Append-only, uma linha por (produto, loja, execução). ~650 linhas hoje.

```csv
data,hora,produto_id,loja,preco,disponivel,fonte
2026-08-30,11:28,creamy-eye-cream,Creamy,116.31,sim,api-vtex
```

| Coluna | Descrição |
|---|---|
| `data` | `YYYY-MM-DD`, timezone `America/Sao_Paulo` |
| `hora` | `HH:MM` |
| `produto_id` | FK para `produtos.json` |
| `loja` | rótulo da loja (FK composta com produto_id) |
| `preco` | decimal com ponto, 2 casas |
| `disponivel` | `sim` / `nao` |
| `fonte` | **qual estratégia capturou o preço** — essencial para depuração |

**Sobre `fonte`:** foi adicionada depois de uma série de bugs de preço errado. Ao
investigar qualquer valor suspeito, essa coluna diz imediatamente qual caminho do
código produziu aquele número. Distribuição atual: `amazon-vis` 273, `variante-nuvem`
120, `api-vtex` 98, `amazon-json` 80, `variante-id` 44, `amazon-core` 12,
`amazon-1o` 18, `amazon` 6 (as duas últimas são de versões antigas do código).

**Múltiplas linhas no mesmo dia:** acontecem quando o usuário aciona coleta manual.
O dashboard deduplica por dia (último valor do dia vence) em `serieLoja()`.

**Se precisar limpar dados errados:** editar o CSV direto e commitar. Já foi feito
várias vezes (`grep -v` do valor errado). O dashboard se recupera sozinho.

### 4.3 `coleta.log`

Sobrescrito a cada execução, commitado junto. Formato:

```
IMG  parafusadeira-wap: imagem encontrada automaticamente
OK   creamy-eye-cream @ Creamy: R$ 116.31 (api-vtex)
ERRO refill-vanilla @ Ramim: ValueError: nao encontrei o preco da variacao '500ml'...
Coleta: 8 ok, 1 falha(s).
```

É a primeira coisa a olhar quando algo dá errado. Os erros incluem traceback e
diagnóstico rico (lista de preços/variações vistos na página).

---

## 5. `coletor.py` — mapa do código

Ordem das funções no arquivo e o que cada uma faz:

| Linha | Função | Papel |
|---|---|---|
| 53 | `buscar(url, timeout, ua)` | HTTP GET com UA de navegador, `Accept-Encoding: gzip` e descompressão manual (detecta magic bytes `\x1f\x8b`) |
| 70 | `normalizar_preco(bruto)` | `"1.299,90"` / `"129.99"` / `129.99` / `"R$ 89,90"` → `float`. Heurística: se tem `,` e `.`, o `.` é separador de milhar |
| 93 | `imagem_de_html(html)` | extrai `og:image` |
| 108 | `token_mercado_livre()` | OAuth client-credentials do ML, cacheado em `_ml_token` global |
| 133 | `_ml_api(caminho)` | GET autenticado na API do ML; converte HTTPError em ValueError com o corpo da resposta |
| 153 | `preco_mercado_livre(url)` | usa `/products/{id}` para URLs `/p/MLB...` (catálogo) e `/items/{id}` para anúncios. **Atualmente sempre falha — ver seção 8.3** |
| 181 | `_iterar_objetos(dado)` | walker recursivo de JSON |
| 191 | `preco_jsonld(html)` | schema.org/Product em `<script type="application/ld+json">` |
| 207 | `preco_meta(html)` | `product:price:amount` / `og:price:amount` |
| 224 | `preco_amazon(html)` | **retorna lista de candidatos** `[(valor, tag)]`, não um valor único |
| 273 | `escolher_preco(cands, referencia)` | decide qual candidato é confiável — o coração da lógica anti-erro |
| 306 | `preco_variante(html, variante, url_origem)` | preço de uma variação específica (dropdown de tamanho) |
| 385 | `url_amazon_canonica(url)` | reescreve para `/dp/{ASIN}?psc=1&th=1` |
| 399 | `imagem_amazon(html)` | `hiRes` / `data-old-hires` |
| 405 | `preco_generico(html)` | `itemprop=price`, `"price":`, `data-price` |
| 420 | `preco_vtex(url)` | API pública de catálogo VTEX |
| 454 | `lojas_de(produto)` | normaliza formato novo/legado → `[(loja, url, variante)]` |
| 466 | `coletar_preco(url, referencia, variante)` | orquestrador: escolhe estratégias, faz retries, valida |
| 552 | `main()` | lê produtos, calcula referências, itera, grava CSV/JSON, imprime log |

### 5.1 Ordem de decisão em `coletar_preco()`

```
1. URL é Mercado Livre?        → preco_mercado_livre()  [hoje sempre falha]
2. Sem variante e URL /p final? → preco_vtex()
3. É Amazon?                    → url_amazon_canonica()
4. Loop de tentativas (4 se Amazon, 3 caso contrário), alternando UA desktop/mobile:
   4a. Tem `variante`?  → preco_variante() APENAS. Nunca cai para o preço padrão
                          da página (seria de outra variação). Se não achar, tenta
                          de novo; esgotou → erro explicando quais variações existem
   4b. É Amazon?        → escolher_preco(preco_amazon(html), referencia)
   4c. Genérico         → preco_jsonld → preco_meta → preco_generico,
                          cada um validado contra `referencia` (faixa 0.25x–4x)
5. Esgotou → erro específico (anti-bot / variação não achada / preços incoerentes)
```

### 5.2 `main()`

- Lê `produtos.json`, filtra `ativo != false`.
- **Calcula `referencias`**: mediana dos **últimos 7 preços** de cada par
  `(produto_id, loja)` a partir do `historico.csv`. É a "memória" que permite
  detectar leituras absurdas.
- Itera produtos × lojas, chama `coletar_preco(url, ref, variante)`.
- Se veio `imagem` e o produto ainda não tem, grava em `produtos.json`
  (`produtos_alterados = True`) — por isso o workflow faz `git add -A`.
- Grava as linhas no CSV (cria cabeçalho se não existir).
- **Código de saída:** `1` apenas se **tudo** falhou; falhas parciais retornam `0`.
  Mas o workflow tem um passo `Sinalizar falha da coleta` que faz `exit 1` se o
  passo de coleta falhou — hoje isso só dispara no caso de falha total. Falhas
  parciais ficam visíveis só no `coleta.log`. **Possível melhoria: ver seção 11.**

---

## 6. A lógica anti-preço-errado (leia antes de mexer)

Esta é a parte mais cara do projeto em tempo de depuração. Foram ~6 rodadas de
correção. **Não simplifique sem entender o histórico.**

### 6.1 O problema

Uma página de produto contém dezenas de números que parecem preço:

- preço por unidade (`R$ 8,85/ml`, `R$ 0,19/g`) ← causou os bugs mais confusos
- preço riscado "de/por" (`a-text-price`)
- outras variações do mesmo produto (tamanhos, packs)
- ofertas de outros vendedores
- produtos patrocinados e "quem viu também comprou"
- valores de parcelamento

Bugs reais que aconteceram: whey de R$ 169,90 registrado como **R$ 0,19**; eye cream
de R$ 133 como **R$ 8,85**; fita de R$ 54,27 oscilando entre **24,99 / 58,99 / 135,00**
conforme a variação que a Amazon resolvia mostrar naquele acesso.

### 6.2 As cinco camadas de defesa

**Camada 1 — URL canônica (Amazon).** `url_amazon_canonica()` reescreve qualquer
link para `https://www.amazon.com.br/dp/{ASIN}?psc=1&th=1`. O `psc=1&th=1` **trava
a variação exata**. Sem isso a Amazon rotaciona qual variação exibe, e o preço
"pula" entre produtos diferentes. Essa foi a correção que resolveu a fita de silicone.

**Camada 2 — filtro por tipo de elemento.** Em `preco_amazon()`, a regex captura a
classe do container junto com o valor e descarta explicitamente:

```python
if "pricePerUnit" in cls or "a-text-price" in cls:
    continue
```

**Camada 3 — múltiplos candidatos, não um valor.** `preco_amazon()` devolve uma
lista `[(valor, tag)]` com tags de confiabilidade decrescente:

| Tag | Origem | Confiança |
|---|---|---|
| `amazon-p2p` | `<span class="a-price ... priceToPay">` | forte |
| `amazon-json` | `"desktop_buybox_group...priceAmount"` | forte |
| `amazon-apex` | `"apexPriceToPay"` | forte |
| `amazon-vis` | qualquer `a-offscreen` visível (fallback) | fraca |

`FORTES = {"amazon-p2p", "amazon-json", "amazon-apex"}` em `escolher_preco()`.

**Camada 4 — validação contra o histórico.** `escolher_preco(cands, referencia)`:

```
1. Existe candidato entre 0.5× e 2× da mediana dos últimos 7 preços? → aceita
2. Senão: existe valor confirmado por 2+ fontes E com ao menos uma fonte forte?
   → aceita (é o caso de promoção real de mais de 50%)
3. Senão → retorna None (não registra nada)
```

Sem `referencia` (produto novo, primeira coleta): aceita valor com consenso de 2+
fontes; senão o de maior prioridade.

**Camada 5 — falhar alto.** Quando nada é confiável, `coletar_preco` levanta
`ValueError` com diagnóstico (lista de preços vistos, mediana esperada). Buraco no
gráfico é aceitável; dado errado não é.

### 6.3 Fora da Amazon

A validação de sanidade também roda, com faixa mais frouxa (`0.25×`–`4×`), porque
lojas menores costumam ter uma fonte só e menos ruído na página.

---

## 7. `index.html` — dashboard

Arquivo único, tema escuro, responsivo. Chart.js 4.4.3 via jsDelivr é a única
dependência externa.

### 7.1 Leitura de dados

`lerArquivo(caminho)` tenta a API do GitHub (`Accept: application/vnd.github.raw+json`)
e cai para `raw.githubusercontent.com` com cache-buster. Ambos funcionam sem token
porque o repositório é público. **A escrita é que exige token.**

### 7.2 Análise (as funções que calculam o que aparece)

| Função | O que faz |
|---|---|
| `lojasDe(p)` | normaliza formato novo/legado |
| `serieLoja(id, loja)` | série temporal de uma loja; **deduplica por dia** (último vence) |
| `serieMelhor(id)` | para cada dia, o **menor preço entre todas as lojas** — é a série que manda no card |
| `statsDe(serie)` | `{atual, anterior, min, media30, dias}` |
| `bomMomento(p, st)` | regra do selo 🔥 |

**Regra do "bom momento"** (`bomMomento`), avaliada sobre a série do melhor preço:

```
produto indisponível          → false
preco_alvo definido e atingido → true
>= 5 dias de dados  e  preço <= mínimo histórico       → true
>= 7 dias de dados  e  preço <= média das últimas 30 × 0.97  → true
```

### 7.3 Render

Um card por produto contendo: miniatura, nome, tags (tipo / alvo / 🔥), preço grande
(melhor do dia) com variação % vs. dia anterior, "melhor preço hoje na **X**",
lista clicável de lojas (a mais barata com borda verde, variação exibida ao lado do
nome), três stats (mínimo histórico, média 30, dias de dados), gráfico multi-loja
(uma linha por loja, cores de `CORES`), bloco de compras registradas, e os botões
Registrar compra / Editar / Remover. Uma faixa amarela no topo lista os produtos em
bom momento.

### 7.4 Escrita (exige token)

Token pessoal do GitHub guardado em `localStorage` sob a chave **`pm_token`**.
Uma vez por navegador/aparelho. Pode ser o mesmo token em todos.

| Ação | Endpoint | Permissão necessária |
|---|---|---|
| Adicionar/editar/remover produto | `PUT /contents/produtos.json` | Contents: RW |
| Registrar/apagar compra | idem (grava em `produtos[].compras`) | Contents: RW |
| Upload de imagem | `PUT /contents/imagens/{id}.{ext}` (base64) | Contents: RW |
| Botão "🔄 Coletar agora" | `POST /actions/workflows/coleta.yml/dispatches` | Actions: RW |

`salvarProdutosJson()` lê o `sha` atual antes do PUT (obrigatório na API do GitHub).
**Não há resolução de conflito** — se o robô commitar entre o GET e o PUT, o PUT
falha com 409. Na prática nunca aconteceu (janela de milissegundos), mas é uma
fragilidade conhecida.

O botão "Coletar agora" dispara o workflow e agenda um `carregar()` após **75 s**.
É um número mágico baseado no tempo típico de execução (~15 s de coleta + commit +
propagação do CDN). Se a coleta ficar mais lenta, esse valor precisa subir.

### 7.5 Constantes no topo do JS

```js
const OWNER = 'Fcairo1', REPO = 'claude-price-monitor', BRANCH = 'main';
```

Se o repositório for renomeado ou movido, **é aqui que muda**.

---

## 8. Conhecimento por loja (ganho na marra)

### 8.1 Funcionam bem

| Loja / plataforma | Estratégia | Observações |
|---|---|---|
| **VTEX** (Creamy, Época, muitas farmácias) | `preco_vtex` → `/api/catalog_system/pub/products/search/{slug}/p` | API pública, sem auth, muito confiável. Detectada por URL terminando em `/p`. Pega o menor preço entre sellers disponíveis. **Retorna o preço cheio** — o site costuma exibir valor menor com desconto Pix (ex.: R$ 136,83 cheio vs. R$ 129,99 no Pix). Isso é proposital: mantém a série comparável |
| **Amazon BR** | `preco_amazon` + `escolher_preco` | Ver seção 6. Serve páginas sem bloco de preço de forma intermitente → 4 tentativas alternando UA desktop/mobile |
| **Nuvemshop / Tiendanube** (Maison Viegas) | `preco_variante` → `LS.variants` | Array JS com todas as variações e preços. Preços podem vir em centavos (`price` inteiro) ou string (`price_number`, `price_short`) |
| **Tray / lojas com `variant_id` na URL** (Ramim) | `preco_variante` → estratégia `variante-id` | Pega o `variant_id` da própria URL e procura preço perto dele no HTML. Retorna 403 esporádico → retries resolvem |

### 8.2 Estratégias de variação, em ordem

`preco_variante()` tenta, nesta ordem:

1. **`variante-nuvem`** — `LS.variants` (Nuvemshop).
2. **`variante-jsonld`** — oferta no JSON-LD cujo `name`/`sku` contém a variação.
3. **`variante-id`** — `variant_id` da URL; busca preço numa janela de ±600 chars
   ao redor de cada ocorrência do id. **Vem antes da busca textual** porque é mais
   preciso (a busca textual já pegou o preço da máquina em vez do refil uma vez).
4. **`variante-prox`** — busca textual: acha o texto da variação e pega o preço mais
   próximo. O padrão tolera espaços entre caracteres, então `"500ml"` casa com
   `"500 ML"`, `"500ml"`, `"500&nbsp;ml"`.

### 8.3 Mercado Livre — bloqueado (não insistir sem novidade)

**Situação:** impossível hoje, por decisão do ML.

- Scraping direto: retorna página anti-bot (`suspicious-traffic` /
  `account-verification`). O ML detecta o IP de datacenter do GitHub Actions.
- API oficial: foi criado um app em developers.mercadolivre.com.br, com
  `client_credentials`. **O OAuth funciona** (token obtido com sucesso), mas tanto
  `/products/{id}` quanto `/items/{id}` retornam:
  ```json
  {"blocked_by":"PolicyAgent","code":"PA_UNAUTHORIZED_RESULT_FROM_POLICIES","status":403}
  ```
  Ou seja: o ML restringe esses endpoints a apps de vendedores parceiros.

**O que ficou pronto no código:** `token_mercado_livre()`, `_ml_api()` e
`preco_mercado_livre()` estão implementados e corretos. Os secrets `ML_CLIENT_ID` e
`ML_CLIENT_SECRET` estão configurados no repositório e injetados no workflow. Se o
ML liberar acesso um dia, basta cadastrar uma URL do ML num produto — funciona sem
mexer no código.

**Caminhos não testados**, se um dia virar prioridade: pedir aprovação de app
comercial ao ML; rodar a coleta de um IP residencial (ex.: Raspberry Pi na casa
dele) em vez do GitHub Actions; ou serviço de scraping pago.

---

## 9. Autenticação e segredos

| Credencial | Onde vive | Para quê |
|---|---|---|
| Token do GitHub (dashboard) | `localStorage['pm_token']`, por navegador | escrever `produtos.json`, subir imagens, disparar workflow |
| `ML_CLIENT_ID` / `ML_CLIENT_SECRET` | GitHub Secrets do repositório | OAuth do Mercado Livre (hoje inútil, ver 8.3) |
| `GITHUB_TOKEN` | automático no workflow | commitar histórico (`permissions: contents: write`) |

**Token do dashboard — como criar:** github.com/settings/personal-access-tokens/new →
Repository access: **Only select repositories** → `claude-price-monitor` →
Permissions: **Contents: Read and write** + **Actions: Read and write** →
Expiration: **No expiration** (permitido para repos pessoais desde out/2024).

**Nunca commitar tokens.** O `index.html` é público no GitHub Pages; o token do
usuário só existe no navegador dele.

---

## 10. Workflows

### `coleta.yml`

- **Gatilhos:** cron `0 9 * * *` (09:00 UTC = 06:00 BRT), `workflow_dispatch`
  (botão do dashboard e aba Actions), e push em `main` que toque
  `produtos.json` ou `coletor.py` (coleta imediata ao adicionar produto).
- **Atraso do cron é normal.** A fila gratuita do GitHub costuma atrasar de minutos
  a ~3 h. Já foi observado às 08:53 em vez de 06:00. Não é bug.
- `concurrency: coleta` com `cancel-in-progress: false` — execuções enfileiram em
  vez de se cancelarem, evitando corrida no push do CSV.
- O passo de coleta tem `continue-on-error: true` para que o histórico seja
  commitado mesmo com falhas parciais; um passo final replica o status de falha.
- `git add -A` (não só o CSV) porque `produtos.json` pode ter sido alterado pelo
  coletor ao descobrir uma imagem.
- `git pull --rebase` antes do push, para o caso de o usuário ter editado pelo
  dashboard durante a execução.

### `pages.yml`

Publica a raiz do repositório no GitHub Pages a cada push em `index.html`.
`configure-pages@v5` com `enablement: true`.

**Nota histórica:** a primeira ativação do Pages **teve de ser feita à mão** pelo
dono em Settings → Pages → Source: **GitHub Actions**. Tokens de terceiros não
conseguem criar o site (403 `Resource not accessible by integration`). Se o Pages
for desativado, será preciso repetir esse clique manual.

---

## 11. Estado atual e pendências

### 11.1 Bug aberto — Refill Vanilla (Ramim)

**A coleta de 30/08 falhou.** O log diz:

```
ERRO refill-vanilla-para-maquina-aroma @ Ramim: nao encontrei o preco da variacao
'500ml' — variacoes que enxerguei no site: ['1000ml','110g','1562 L','160g','16l',
'19l','1L','1l','22G','240g','250g','2812 L'] (html 820018 chars)
```

Note que **`500ml` não aparece** na lista de variações vistas — só `1000ml`, `250g`
etc. Hipóteses, em ordem de probabilidade:

1. A loja **descontinuou o refil de 500 ml** ou renomeou a variação.
2. O `variant_id=4734` da URL cadastrada não existe mais, e a página caiu num
   estado diferente.
3. A loja mudou o layout e a lista de variações agora carrega via JS.

**Primeiro passo sugerido:** abrir a URL cadastrada no navegador e ver o que o
dropdown oferece hoje. Funcionou por ~44 coletas (fonte `variante-id`), então é
mudança recente do site.

### 11.2 Produtos monitorados hoje (8)

| Produto | Tipo | Lojas |
|---|---|---|
| Creme Clareador Eye Cream (Creamy) | recorrente | Creamy + Amazon |
| Fita de silicone para cicatriz | recorrente | Amazon |
| Whey Adaptogen Chocomaltine | recorrente | Amazon |
| Whey Concentrado Max Titanium Baunilha | recorrente | Amazon |
| Corsário Maison Viegas | futura | Maison Viegas (100 ML) |
| Spartan Elixir | recorrente | Maison Viegas (100 ML) |
| Refill Vanilla máquina aroma | recorrente | Ramim (500ml) — **falhando** |
| Parafusadeira WAP | futura | Amazon |

Produto removido pelo usuário em 09/08: Impressora 3D Bambu Lab A1 (67 registros
ficaram no histórico, órfãos — inofensivos, o dashboard ignora).

### 11.3 Dívidas técnicas conhecidas

- **`historico.csv` cresce para sempre.** 650 linhas hoje, ~9/dia. Em 2 anos serão
  ~7000 — ainda ok para o dashboard carregar, mas em algum momento vale arquivar
  por ano ou agregar dados antigos.
- **`compras` do Eye Cream/fita têm duplicatas** (duas entradas em 2026-07-09 para
  a fita). A remoção casa por `data` + `preco` exatos, então duplicatas idênticas
  seriam ambíguas. Baixo impacto.
- **Sem tratamento de conflito 409** na escrita do dashboard (ver 7.4).
- **Falha parcial não notifica.** Se 1 de 9 produtos falha, o workflow marca falha
  (e o GitHub manda email), mas o usuário não sabe qual sem abrir o log. Uma
  melhoria seria escrever um `status.json` que o dashboard exibe como aviso.
- **`preco_alvo` está `null` em todos os produtos.** A funcionalidade existe e
  funciona, só nunca foi usada.
- **Regra de "bom momento" não distingue `tipo`.** Hoje `futura` e `recorrente` são
  tratados igual (exceto pelo alvo). A ideia original era serem regras diferentes.
- **Warning do Node 20** nos workflows (`actions/checkout@v4` etc.). Inofensivo hoje;
  atualizar as actions quando conveniente.

### 11.4 Ideias discutidas e não implementadas

- Notificação real de queda de preço (email/Telegram/push) — hoje o usuário precisa
  abrir o dashboard. O conector de Gmail disponível só cria rascunhos, não envia.
- Segunda coleta diária (ex.: 6h e 18h) — trivial, é só outra linha no cron.
- Agregadores (Zoom/Buscapé) como fonte de comparação multi-loja automática.
- Supermercados online (foram cogitados no início, nunca testados).

---

## 12. Desenvolvimento

### Rodar a coleta localmente

```bash
python3 coletor.py          # usa produtos.json e ANEXA em historico.csv
```

Sem dependências para instalar. Para testar sem sujar o histórico, copie o repo
para /tmp ou faça o teste e reverta o CSV.

### Testar uma estratégia isolada

```python
import coletor
html = coletor.buscar("https://www.amazon.com.br/dp/B0G4WF68KJ?psc=1&th=1")
cands = coletor.preco_amazon(html)
print(cands)
print(coletor.escolher_preco(cands, referencia=58.99))
```

Foi assim que todas as correções de preço foram validadas. Vale escrever asserts
rápidos ao mexer em `escolher_preco` / `preco_variante` — os casos de regressão
importantes estão documentados na seção 6.1.

### Testar o dashboard localmente

`python3 -m http.server 8000` e abrir `localhost:8000`. Ele lê os dados do GitHub
(produção), não os arquivos locais — as constantes `OWNER`/`REPO` apontam para o
repositório remoto.

### Publicar

Push em `main`. Alterar `coletor.py` ou `produtos.json` dispara coleta imediata;
alterar `index.html` republica o Pages. Ambos levam ~40-60 s.

### Verificar o resultado

```bash
git pull && cat coleta.log && tail -5 historico.csv
```

### Convenções de commit

Mensagens em português, minúsculas, no imperativo ou descritivas:
`"fix preço Amazon (buy box) + registro de compras no dashboard"`,
`"suporte a variações de produto (dropdown de tamanho)"`.
Commits automáticos do robô usam o formato `coleta: YYYY-MM-DD HH:MM` e são
assinados por `price-monitor-bot`.

---

## 13. Como adicionar suporte a uma loja nova

Roteiro que funcionou nas quatro lojas suportadas:

1. **Cadastre o produto** pelo dashboard e veja o `coleta.log`. O erro já traz
   diagnóstico (tamanho do HTML, preços vistos, início da página).
2. **Baixe o HTML** e procure o preço: `curl -A "Mozilla/5.0 ..." URL > /tmp/p.html`.
   (Nota: no ambiente do Cowork o `curl` direto costuma ser bloqueado; use
   `mcp__workspace__web_fetch` ou teste dentro do próprio workflow.)
3. **Identifique a fonte mais estável**, nesta ordem de preferência:
   API pública da plataforma > JSON embutido na página > JSON-LD > meta tags >
   regex no HTML visível.
4. **Descubra a plataforma** — vale muito mais que resolver uma loja: procure por
   `vtex`, `LS.variants` (Nuvemshop), `tcdn.com.br` (Tray), `shopify` no HTML. Uma
   estratégia de plataforma resolve centenas de lojas de uma vez.
5. **Implemente** uma função `preco_xxx(html)` que retorna `(preco, disponivel, tag)`
   e plugue na cadeia de `coletar_preco`. Use uma tag nova na coluna `fonte`.
6. **Confirme com o usuário** que o valor bate com o que ele vê na tela antes de
   considerar pronto — foi assim que todos os bugs de preço foram pegos.

---

## 14. Sobre o usuário

Filipe (@Fcairo1, filipecairo@gmail.com) é product ops na SoundOn/ByteDance, não é
desenvolvedor. Ele opera o sistema pelo dashboard e pelo GitHub web/app, e prefere
respostas diretas e sem jargão. Decisões que ele tomou e que devem ser respeitadas:
dashboard **sem senha** (não há nada sigiloso), edição de produtos **pelo próprio
dashboard** (não editar JSON na mão), e acesso **remoto pelo celular** como
requisito de primeira classe.
