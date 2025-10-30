import time
from bs4 import BeautifulSoup
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

origins = [
    "http://localhost:5173",  # dev
    "https://kronos-plataforma-react.onrender.com"  # produção
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CHROME_DRIVER_PATH = '/usr/bin/chromedriver'
CHROME_BIN_PATH = '/usr/bin/chromium'

@app.get("/noticias")
def principal_root():
    lista_informacoes = []
    url_site = "https://noticias.portaldaindustria.com.br/busca/?q=f%C3%A1brica"
    
    options = Options()
    options.binary_location = CHROME_BIN_PATH 
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36")
    
    try:
        service = Service(executable_path=CHROME_DRIVER_PATH)
        driver = webdriver.Chrome(service=service, options=options)
    except Exception as e:
        print(f"Erro ao inicializar o Chrome Driver: {e}")
        return {"error": "Falha ao inicializar o navegador automatizado.", "details": str(e)}


    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """
        }
    )

    driver.get(url_site)

    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.col-md-4"))
    )

    primeiro_scroll = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")

        time.sleep(2)

        ultimo_scroll = driver.execute_script("return document.body.scrollHeight")

        if ultimo_scroll == primeiro_scroll:
            break
        primeiro_scroll = ultimo_scroll

    time.sleep(2)
    pagina = driver.page_source
    conteudo_pagina = BeautifulSoup(pagina, "html.parser")
    driver.quit()

    div_noticia = conteudo_pagina.select('div.col-md-4')

    for div in div_noticia:
        subtitulo = None
        data_publicacao = None
        autor = None
        imagem_perfil_autor = None
        titulo_tag_classe = div.select_one('a.post-meta--link h3.post-meta--title')
        imagem_tag_classe = div.select_one('img.multimedia--element')
        link_tag = div.select_one('a.post-meta--link')

        titulo = titulo_tag_classe.get_text(strip=True) if titulo_tag_classe else "Título não encontrado"
        imagem = imagem_tag_classe['src'] if imagem_tag_classe and 'src' in imagem_tag_classe.attrs else "Imagem não identificada"
        link_noticia = "https://noticias.portaldaindustria.com.br" + link_tag['href'] if link_tag and 'href' in link_tag.attrs else None

        if link_noticia:
            try:
                resposta_requisicao = requests.get(link_noticia, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                link_requisicao = BeautifulSoup(resposta_requisicao.text, "html.parser")

                subtitulo_tag_classe = link_requisicao.select_one("h2.article--subtitle")
                data_publicacao_tag_classe = link_requisicao.select_one("time.article--time")
            
                if subtitulo_tag_classe:
                    subtitulo = subtitulo_tag_classe.get_text(strip=True)
                
                if data_publicacao_tag_classe:
                    data_publicacao = data_publicacao_tag_classe.get_text(strip=True)

                div_autor = link_requisicao.select_one("div.article-author")

                if div_autor:
                    imagem_autor_tag = div_autor.select_one("img")
                    imagem_perfil_autor = (
                        imagem_autor_tag["src"]
                        if imagem_autor_tag and imagem_autor_tag.has_attr("src")
                        else "Imagem de perfil do autor não encontrado aaaaaaa"
                    )

                    nome_autor_tag = div_autor.select_one("div.article-author-info a")
                    autor = (
                        nome_autor_tag.get_text(strip=True)
                        if nome_autor_tag
                        else "Nome do autor da publicação não disponível"
                    )

            except Exception as e:
                print(f"Não foi possível acessar a notícia {link_noticia}: {e}")
        
        lista_informacoes.append({
            'titulo': titulo,
            'imagem': imagem,
            'subtitulo': subtitulo,
            'data_publicacao': data_publicacao,
            'nome_autor': autor,
            'imagem_perfil_autor': imagem_perfil_autor,
            'link_noticia': link_noticia
        })

    return lista_informacoes
