# 🤖 kronos-rpa

## Índice

- [📓 Sobre](#-sobre)
- [🚀 Tecnologias](#-tecnologias)
- [✨ Funcionalidades](#-funcionalidades)
  - [Sincronização de Bancos (CDC) - `rpa-banco`](#-sincronização-de-bancos-cdc---rpa-banco)
  - [Web Scraping API - `WEB-Raspagem`](#-web-scraping-api---web-raspagem)
  - [Modelo CDC Genérico - `Modelo-RPA`](#-modelo-cdc-genérico---modelo-rpa)
- [⚙️ Instalação](#-instalação)
- [⏰ Processamento Agendado (GitHub Actions)](#-processamento-agendado-github-actions)
- [📄 Licença](#-licença)
- [💻 Autores](#-autores)

</br>

## 📓 Sobre

Este repositório contém uma coleção de scripts de RPA (Robotic Process Automation) e APIs desenvolvidos em Python, focados na automação de integração e extração de dados para o sistema Kronos.

O projeto é dividido em três componentes principais:
1.  Um sistema robusto de **sincronização de banco de dados** bi-direcional (Change Data Capture) para manter a consistência entre um banco de dados primário (legado) e um secundário (novo), que possuem esquemas (schemas) diferentes.
2.  Uma **API de Web Scraping** que extrai notícias do Portal da Indústria e as serve através de um endpoint FastAPI.
3.  Um **Modelo de CDC Genérico** que utiliza replicação lógica (`wal2json`) e serve como prova de conceito para futuras sincronizações.

</br>

## 🚀 Tecnologias

As principais tecnologias e bibliotecas utilizadas neste projeto são:

* **Python 3.11**
* **API & Servidor:** FastAPI, Uvicorn
* **Web Scraping:** Selenium, BeautifulSoup4, Requests
* **Banco de Dados:** PostgreSQL (via `psycopg2-binary`)
* **Data Matching & Hashing:** TheFuzz (Fuzzy Matching), Bcrypt (Hashing de Senha), Gender Guesser (Inferência de Gênero)
* **Containerização:** Docker
* **CI/CD & Automação:** GitHub Actions
* **Ambiente (Web Scraping):** Chromium, Chromium-Driver

</br>

## ✨ Funcionalidades

### Sincronização de Bancos (CDC) - `rpa-banco`

Este é o principal RPA de sincronização, projetado para manter dois bancos de dados (Primário e Secundário) com esquemas diferentes em consistência. Ele é executado em duas partes:

**1. `cdc_app.py` (Primário -> Secundário)**
Este script implementa uma lógica de CDC customizada que lê o banco primário e aplica as mudanças no secundário, seguindo 6 regras de negócio complexas:

* **Regra 1 (Inserção):** Registros novos no primário são inseridos no secundário.
* **Regra 2 (Atualização):** Alterações em registros no primário são refletidas no secundário.
* **Regra 3 (Remoção):** Registros deletados no primário são deletados no secundário.
* **Regra 4 (Respeito ao Secundário):** Alterações feitas *apenas* no secundário são mantidas (se o primário não mudou).
* **Regra 5 (Merge):** Se colunas diferentes do *mesmo registro* forem alteradas no primário e no secundário, as mudanças são mescladas (ambas são mantidas).
* **Regra 6 (Resolução de Conflito):** Se a *mesma coluna* for alterada em ambos, a alteração do **primário vence** e o conflito é registrado em log.

Para lidar com as inconsistências e transformações de dados entre os esquemas, o script utiliza ferramentas avançadas:
* **`TheFuzz`:** Empregado na função `processar_cargo`, utiliza *fuzzy matching* (`fuzz.token_set_ratio`) para comparar strings de cargos (ex: "DesenvolvedOr" vs "DesenvolvedorA"). Isso impede a criação de cargos duplicados no banco secundário.
* **`Bcrypt`:** Usado nas funções `pre_processar_administracao` e `pre_processar_usuario` para aplicar hashing de senhas. Senhas em texto plano do banco primário são transformadas em hash bcrypt antes de serem inseridas no secundário, garantindo a segurança.

**2. `cdc_crud.py` (Secundário -> Primário)**
Este script faz o caminho inverso, sincronizando *algumas* alterações do banco secundário de volta para o primário:

* **Deleções:** Lê um log de deleções no secundário e apaga os usuários correspondentes no primário (`sincronizar_deletes`).
* **Atualizações:** Verifica os usuários mapeados e atualiza dados como status, cargo, setor e gestor no banco primário (`sincronizar_updates_usuarios`).
* **Links:** Sincroniza a tabela de associação `HabilidadeUsuario` (Secundário) com a `usuario_habilidade` (Primário) (`sincronizar_habilidades`).
* **`Gender Guesser`:** O script inclui a função `descobrir_genero`, que utiliza a biblioteca `gender_guesser` para inferir o gênero (M/F/O) com base no primeiro nome do usuário, auxiliando no pré-processamento de dados.

### Web Scraping API - `WEB-Raspagem`

Este componente (`rpaNoticias.py`) é uma API FastAPI que expõe um endpoint `/noticias` para extração de dados do Portal da Indústria.

O processo de scraping é robusto e feito em múltiplas etapas:
1.  **Inicialização:** Utiliza `Selenium` para controlar um navegador `Chromium` em modo *headless* (sem interface gráfica), com opções para se parecer com um usuário comum.
2.  **Scroll Infinito:** O RPA acessa a página de busca e lida com o "infinite scroll". Ele executa um script JavaScript (`window.scrollTo`) para rolar até o fim da página. Em seguida, compara a altura da página (`document.body.scrollHeight`) antes e depois do scroll. O processo se repete até que a altura não mude mais, garantindo que todas as notícias sejam carregadas.
3.  **Coleta Primária:** Após o scroll, `BeautifulSoup` é usado para parsear o HTML da página completa e extrair a lista de todas as notícias (título, imagem e link).
4.  **Coleta Secundária (Detalhes):** O script então itera por cada notícia e usa `Requests` (uma biblioteca mais leve) para acessar o link individual de cada artigo. Desta página de detalhe, ele extrai informações adicionais, como subtítulo, data de publicação e o nome do autor.

### Modelo CDC Genérico - `Modelo-RPA`

A pasta `Modelo-RPA` contém o script (`rpaConexao.py`) que demonstra uma **fórmula genérica** de Change Data Capture.

* Diferente do RPA do `rpa-banco`, este modelo utiliza a **replicação lógica** nativa do PostgreSQL (`wal2json`).
* Ele captura alterações do WAL (Write-Ahead Log) em tempo real e é genérico porque pode replicar *qualquer* tabela, criando o schema e as tabelas de destino (inclusive uma tabela de `staging`) automaticamente se elas não existirem.
* Em uma V2, este modelo será usado para sincronizar os bancos de QA e DEV, que são idênticos, mas hoje não têm consistência de dados.

</br>

## ⚙️ Instalação

É necessário ter o Python (versão 3.10+), Docker e acesso e credenciais para as instâncias de PostgreSQL.

```bash
# clonar o repositório
git clone [https://github.com/Systems-Kronos/kronos-rpa.git](https://github.com/Systems-Kronos/kronos-rpa.git)

# entrar no diretório
cd kronos-rpa

# instalar dependências
pip install -r requirements.txt
````

**Configuração do Ambiente (CDC):**

Crie um arquivo `.env` na raiz do projeto com as variáveis de ambiente para os bancos:

```
DATABASE_URL_PRIMARIO="postgresql://user:pass@host:port/db1"
DATABASE_URL_SECUNDARIO="postgresql://user:pass@host:port/db2"
```

**Execução (CDC):**

Execute os scripts (a ordem importa se for a primeira execução):

```bash
python rpa-banco/cdc_app.py
python rpa-banco/cdc_crud.py
```

**Execução (Web Scraping API):**

  * **Modo Desenvolvimento (Uvicorn):**

    ```bash
    uvicorn WEB-Raspagem.rpaNoticias:app --host 0.0.0.0 --port 8080
    ```

  * **Modo Produção (Docker):**

    ```bash
    # Build da imagem
    docker build -t kronos-rpa:latest .

    # Run do contêiner
    docker run -p 8080:8080 kronos-rpa:latest
    ```

</br>

## ⏰ Processamento Agendado (GitHub Actions)

O repositório utiliza GitHub Actions para automação:

**1. Sincronização de Bancos (`sync_dbs.yml`)**

  * **Frequência:** Executa a cada hora (`cron: '0 */1 * * *'`) e por dispatch manual.
  * **Ação:** Instala as dependências Python e executa os dois scripts de CDC em sequência: `cdc_app.py` (Primário -\> Secundário) e `cdc_crud.py` (Secundário -\> Primário).

**2. Deploy da API de Web Scraping (`deploy_webscraping.yaml`)**

  * **Gatilho:** Executa em `push` ou `pull_request` para os branches `main` e `dev`.
  * **Ação:** Faz o build da imagem Docker (`docker build -t kronos-rpa:latest .`) e aciona um deploy no Render (serviço de-hospedagem) via API.

</br>

## 📄 Licença

Este projeto está licenciado sob a licença MIT — veja o arquivo [LICENSE](https://www.google.com/search?q=LICENSE) para mais detalhes.

</br>

## 💻 Autores

  - [Theo Martins](https://github.com/TheoMGtech)
  - [Júlia Penna](https://github.com/juliaPnMt1304)
  - [Yasmin Barbosa](https://github.com/yassbarbosa)
