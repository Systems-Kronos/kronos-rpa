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
from webdriver_manager.chrome import ChromeDriverManager

#Criando o objeto para a FastAPI
app = FastAPI()

@app.get("/noticias")
def principal_root():
    lista_informacoes = []
    url_site = "https://noticias.portaldaindustria.com.br/busca/?q=f%C3%A1brica"
    #Inicializando o objeto para remover o sinalizador de teste automatizado
    options = Options()

    #Iniciando o Chrome sem a interface gráfica -> modo headless
    options.add_argument("--headless")
    #Habilitando modo sem sandbox -> requerido em ambientes linux/containers
    options.add_argument("--no-sandbox")
    #Desabilitando a GPU ->  necessário em modo headless
    options.add_argument("--disable-gpu")

    #Removendo o detector de teste automatizado
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument("--disable-blink-features=AutomationControlled")

    #Remoção dos sinalizadores
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36")
    
    #Trazendo como parâmetro o service do driver
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    #Disfarçando o teste "automatizado" para o navegador
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

    #Espera para identificar todas as classes feed-post-link com a tag <a> (título)
    #Encontra cada um dos títulos 
      #Utilizando o CSS_SELECTOR para melhor funcionamento já que estamos tratando seletores compostos  
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.col-md-4"))
    )

    #Garante termos o scroll de toda a página -> registra a altura da página
    primeiro_scroll = driver.execute_script("return document.body.scrollHeight")
    while True:
        #Continua a parte de scrolling até que o scroll atinja a ltura máxima
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")

        time.sleep(2)

        #Registra o tamanho alcançado pelo scroll
        ultimo_scroll = driver.execute_script("return document.body.scrollHeight")

        if ultimo_scroll == primeiro_scroll:
            break
        primeiro_scroll = ultimo_scroll

    time.sleep(2)
    pagina = driver.page_source
    conteudo_pagina = BeautifulSoup(pagina, "html.parser")
    driver.quit()

    #Acessando a div com as informações do título, lide, imagem, data de publicação
    div_noticia = conteudo_pagina.select('div.col-md-4')

    for div in div_noticia:

        #Inicializando as variáveis em que suas classes serão encontradas dentro do link da notícia
        subtitulo = None
        data_publicacao = None
        autor = None
        imagem_perfil_autor = None

        #Selecionando título
        titulo_tag_classe = div.select_one('a.post-meta--link h3.post-meta--title')

        #Selecionando a imagem
        imagem_tag_classe = div.select_one('img.multimedia--element')

        #Selecionando o link da notícia
        link_tag = div.select_one('a.post-meta--link')

        titulo = titulo_tag_classe.get_text(strip=True) if titulo_tag_classe else "Título não encontrado"
        imagem = imagem_tag_classe['src'] if imagem_tag_classe and 'src' in imagem_tag_classe.attrs else "Imagem não identificada"

        #Lógica para selecionar a url da notícia e poder acessar o subtítulo completo
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

                #Extraindo dados dos autores
                div_autor = link_requisicao.select_one("div.article--author") or link_requisicao.select_one("div.article-author")

                if div_autor:

                    #Selecionando classes dos autores
                    nome_autor_tag = div_autor.select_one("span.author-name")
                    imagem_autor_tag = div_autor.select_one("img")

                    autor = nome_autor_tag.get_text(strip=True) if nome_autor_tag else "Nome do autor da publicação não disponível"
                    imagem_perfil_autor = imagem_autor_tag["src"] if imagem_autor_tag and imagem_autor_tag.has_attr("src") else "Imagem de perfil do autor não encontrado"

            except Exception as e:
                print(f"Não foi possível acessar a notícia: {e}")
        
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