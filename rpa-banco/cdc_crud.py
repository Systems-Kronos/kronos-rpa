import psycopg2
import psycopg2.extras
import json
from dotenv import load_dotenv
import os
import sys
import gender_guesser.detector as gender

load_dotenv()

DB_PRIMARIO_CONFIG = os.getenv("DATABASE_URL_PRIMARIO")
DB_SECUNDARIO_CONFIG = os.getenv("DATABASE_URL_SECUNDARIO")

if not DB_PRIMARIO_CONFIG or not DB_SECUNDARIO_CONFIG:
    print(" Erro: As credenciais dos bancos de dados não estão definidas nas variáveis de ambiente.")
    sys.exit(1)

# Objeto para detectar gênero
gd = gender.Detector(case_sensitive=False)


def conectar_banco(config, nome_banco):
    try:
        conn = psycopg2.connect(config)
        print(f"Conectado no {nome_banco}")
        return conn
    except Exception as e:
        print(f"    Erro: Conexão com banco {nome_banco} falhou: {e}")
        sys.exit(1)

def carregar_mapas_traducao(cur_s):
    print("Carregando mapas de tradução (S -> P)...")
    mapas = {}
    tabelas_para_mapear = ['usuario', 'Habilidade', 'Setor']
    
    for tabela in tabelas_para_mapear:
        mapa_invertido = {}
        try:
            cur_s.execute(
                """
                SELECT nCdOrigem
                     , nCdDestino
                  FROM table_log.rpa_mapa_ids
                 WHERE cNmTabela = %s
                """,
                (tabela,)
            )
            for linha in cur_s:
                mapa_invertido[linha['ncddestino']] = linha['ncdorigem']
                
            mapas[tabela] = mapa_invertido
            print(f"   -> Mapa '{tabela}' carregado. {len(mapa_invertido)} itens encontrados.")
            
        except Exception as e:
            print(f" [ERRO] Falha ao carregar mapa para '{tabela}': {e}")
            mapas[tabela] = {} 
            
    return mapas


def buscar_nome_cargo(cur_s, id_cargo_s):
    if not id_cargo_s:
        return None
    try:
        cur_s.execute("SELECT cNmCargo FROM public.Cargo WHERE nCdCargo = %s", (id_cargo_s,))
        resultado = cur_s.fetchone()
        return resultado['cnmcargo'] if resultado else None
    except Exception as e:
        print(f"    ERRO: Não foi possível buscar o nome do Cargo ID {id_cargo_s}: {e}")
        return None

def descobrir_genero(nome_completo):
    if not nome_completo:
        return None
        
    try:
        primeiro_nome = nome_completo.split(' ')[0]
        
        resultado = gd.get_gender(primeiro_nome) # Não tem 'brazil' mas 'portugal' é próximo suficiente
        
        if resultado == 'female' or resultado == 'mostly_female':
            return 'F'
        if resultado == 'male' or resultado == 'mostly_male':
            return 'M'
            
        return 'O' # unknown, androgynous, etc.
        
    except Exception as e:
        print(f"    Erro: Falha ao descobrir gênero de '{nome_completo}': {e}")
        return None

# --- Funções Principais de Sincronização ---

def sincronizar_deletes(cur_p, cur_s, mapa_usuarios_s_para_p):
    print("\n--- 1. Procurando Deletes (S -> P) ---")
    
    ids_p_para_deletar = set()
    try:
        cur_s.execute(
            """
            SELECT nCdUsuario 
              FROM table_log.Usuario
             WHERE cOperacao = 'DELETE' 
               AND dOperacao >= CURRENT_DATE - INTERVAL '2 days'
            """
        )
        
        deletados_recentes_s = cur_s.fetchall()
        
        if not deletados_recentes_s:
            print("   -> Nenhum usuário deletado nos últimos 2 dias no Secundário.")
            return

        print(f"   -> Foi encontrado {len(deletados_recentes_s)} deletes recentes no Secundário.")

        for delecao in deletados_recentes_s:
            id_s_deletado = delecao['ncdusuario']
            id_p_mapeado = mapa_usuarios_s_para_p.get(id_s_deletado)
            
            if id_p_mapeado:
                ids_p_para_deletar.add(id_p_mapeado)
        
        if not ids_p_para_deletar:
            print("   -> Nenhum dos deletes recentes estava mapeado. Nada a fazer.")
            return

        print(f"   -> Encontrados {len(ids_p_para_deletar)} usuários para apagar do Primário.")

        # Apaga do Primário
        for id_p in ids_p_para_deletar:
            try:
                cur_p.execute("DELETE FROM public.usuario WHERE id = %s", (id_p,))
                print(f"   - Usuário Apagado (ID P: {id_p})")
            except Exception as e_del:
                print(f"   Erro: Falha ao apagar Usuário (ID P: {id_p}): {e_del}")
                
    except psycopg2.Error as e_query:
        print(f"   -> Erro: {e_query}")

def sincronizar_updates_usuarios(cur_p, cur_s, mapas):
    print("\n--- 2. Procurando Updates [usuario] (S -> P) ---")
    
    mapa_usuarios_s_para_p = mapas['usuario']
    mapa_setores_s_para_p = mapas['Setor']
    
    if not mapa_usuarios_s_para_p:
        print("   -> Mapa de usuários está vazio. Pulando updates.")
        return
        
    total_usuarios = len(mapa_usuarios_s_para_p)
    total_atualizado = 0
    
    print(f"   -> Verificando {total_usuarios} usuários mapeados...")
    
    for id_s, id_p in mapa_usuarios_s_para_p.items():
        try:
            cur_s.execute("SELECT * FROM public.Usuario WHERE nCdUsuario = %s", (id_s,))
            linha_s = cur_s.fetchone()
            
            cur_p.execute("SELECT * FROM public.usuario WHERE id = %s", (id_p,))
            linha_p = cur_p.fetchone()

            if not linha_s or not linha_p:
                continue 

            dados_p_desejado = {}
            dados_p_desejado['nome'] = linha_s['cnmusuario']
            dados_p_desejado['cpf'] = linha_s['ccpf']
            dados_p_desejado['status'] = 'Ativo' if bool(linha_s['bativo']) else 'Inativo'
            dados_p_desejado['cargo'] = buscar_nome_cargo(cur_s, linha_s['ncdcargo'])
            
            id_s_setor = linha_s.get('ncdsetor')
            dados_p_desejado['fk_setor_id'] = mapa_setores_s_para_p.get(id_s_setor)
            
            id_s_gestor = linha_s.get('ncdgestor')
            dados_p_desejado['fk_supervisor_id'] = mapa_usuarios_s_para_p.get(id_s_gestor) if id_s_gestor else id_p

            dados_para_update = {}
            for coluna, valor_desejado in dados_p_desejado.items():
                valor_atual = linha_p.get(coluna)
                
                if str(valor_atual) != str(valor_desejado):
                    dados_para_update[coluna] = valor_desejado

            if dados_para_update:
                set_clause = ", ".join([f"{col} = %s" for col in dados_para_update.keys()])
                valores = list(dados_para_update.values()) + [id_p]
                
                sql_update = f"UPDATE public.usuario SET {set_clause} WHERE id = %s;"
                cur_p.execute(sql_update, valores)
                total_atualizado += 1
                print(f"   Usuário Atualizado (ID P: {id_p}). Colunas: {list(dados_para_update.keys())}")

        except Exception as e_item:
            print(f"   Erro: Falha ao processar S:{id_s} -> P:{id_p}: {e_item}")
            try: cur_p.connection.rollback()
            except: pass
    
    print(f"   -> Verificação concluída. {total_atualizado} usuários foram atualizados.")

def sincronizar_novos_usuarios(cur_p, cur_s, mapas):
    print("\n--- 3. Procurando INSERTS [usuario] (S -> P) ---")
    
    mapa_usuarios_s_para_p = mapas['usuario']
    mapa_setores_s_para_p = mapas['Setor']
        
    total_usuarios = len(mapa_usuarios_s_para_p)
    total_inseridos = 0
    
    print(f"   -> Verificando existência de usuários para inserção...")
    
    cur_s.execute("SELECT nCdUsuario FROM public.Usuario")
    todos_ids_s = {linha['ncdusuario'] for linha in cur_s.fetchall()}

    for id_s in todos_ids_s:
        if id_s not in mapa_usuarios_s_para_p:
            try:
                cur_s.execute("SELECT * FROM public.Usuario WHERE nCdUsuario = %s", (id_s,))
                linha_s = cur_s.fetchone()
                
                if not linha_s:
                    continue 

                dados_p_novo = {}
                dados_p_novo['nome'] = linha_s['cnmusuario']
                dados_p_novo['cpf'] = linha_s['ccpf']
                dados_p_novo['status'] = 'Ativo' if bool(linha_s['bativo']) else 'Inativo'
                dados_p_novo['cargo'] = buscar_nome_cargo(cur_s, linha_s['ncdcargo'])
                dados_p_novo['genero'] = descobrir_genero(linha_s['cnmusuario'])
                dados_p_novo['senha'] = linha_s['csenha'] if not str(linha_s['csenha']).startswith('$2') else '***hashed***'
                
                id_s_setor = linha_s.get('ncdsetor')
                dados_p_novo['fk_setor_id'] = mapa_setores_s_para_p.get(id_s_setor)
                
                id_s_gestor = linha_s.get('ncdgestor')
                dados_p_novo['fk_supervisor_id'] = mapa_usuarios_s_para_p.get(id_s_gestor) if id_s_gestor else 'lastval()'                

                if dados_p_novo['fk_supervisor_id'] == 'lastval()':
                    del dados_p_novo['fk_supervisor_id']
                    colunas = ", ".join(dados_p_novo.keys()) + ", fk_supervisor_id"
                    valores_placeholder = ", ".join(["%s"] * len(dados_p_novo)) + ", lastval()"
                else:
                    colunas = ", ".join(dados_p_novo.keys())
                    valores_placeholder = ", ".join(["%s"] * len(dados_p_novo))

                valores = list(dados_p_novo.values())
                
                sql_insert = f"INSERT INTO public.usuario ({colunas}) VALUES ({valores_placeholder}) RETURNING id;"
                cur_p.execute(sql_insert, valores)
                novo_id_p = cur_p.fetchone()['id']
                
                mapa_usuarios_s_para_p[id_s] = novo_id_p
                total_inseridos += 1
                print(f"   Usuário Inserido (ID P: {novo_id_p})")

            except Exception as e_item:
                print(f"   Erro: Falha ao inserir Usuário S:{id_s}: {e_item}")
                try: cur_p.connection.rollback()
                except: pass
    
    print(f"   -> Verificação concluída. {total_inseridos} usuários foram inseridos.")

def sincronizar_habilidades(cur_p, cur_s, mapas):
    print("\n--- 4. Sincronizando Habilidades (S -> P) ---")
    
    mapa_usuarios_s_para_p = mapas['usuario']
    mapa_habilidades_s_para_p = mapas['Habilidade']

    if not mapa_usuarios_s_para_p or not mapa_habilidades_s_para_p:
        print("   -> Mapas de usuário ou habilidade vazios. Pulando.")
        return

    links_p_esperados = set()
    try:
        cur_s.execute("SELECT nCdUsuario, nCdHabilidade FROM public.HabilidadeUsuario")
        for linha_s in cur_s:
            id_p_user = mapa_usuarios_s_para_p.get(linha_s['ncdusuario'])
            id_p_hab = mapa_habilidades_s_para_p.get(linha_s['ncdhabilidade'])
            
            if id_p_user and id_p_hab:
                links_p_esperados.add((id_p_user, id_p_hab))
    except Exception as e:
        print(f"  [ERRO] Não consegui ler 'HabilidadeUsuario' do Secundário: {e}")
        return

    links_p_atuais = set()
    try:
        cur_p.execute("SELECT fk_usuario_id, fk_habilidade_id FROM public.usuario_habilidade")
        for linha_p in cur_p:
            links_p_atuais.add((linha_p['fk_usuario_id'], linha_p['fk_habilidade_id']))
    except Exception as e:
        print(f"  [ERRO] Não consegui ler 'usuario_habilidade' do Primário: {e}")
        return
        
    links_para_deletar = links_p_atuais - links_p_esperados
    links_para_inserir = links_p_esperados - links_p_atuais

    if links_para_deletar:
        print(f"   -> Opa, achei {len(links_para_deletar)} links de habilidade para apagar.")
        for id_p_usuario, id_p_habilidade in links_para_deletar:
            try:
                cur_p.execute(
                    "DELETE FROM public.usuario_habilidade WHERE fk_usuario_id = %s AND fk_habilidade_id = %s",
                    (id_p_usuario, id_p_habilidade)
                )
            except Exception as e_del:
                print(f"   [ERRO] Falha ao apagar link P_User:{id_p_usuario}, P_Hab:{id_p_habilidade}: {e_del}")

    if links_para_inserir:
        print(f"   -> Opa, achei {len(links_para_inserir)} links de habilidade para inserir.")
        for id_p_usuario, id_p_habilidade in links_para_inserir:
            try:
                cur_p.execute(
                    "INSERT INTO public.usuario_habilidade (fk_usuario_id, fk_habilidade_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (id_p_usuario, id_p_habilidade)
                )
            except Exception as e_ins:
                print(f"   [ERRO] Falha ao inserir link P_User:{id_p_usuario}, P_Hab:{id_p_habilidade}: {e_ins}")
    
    if not links_para_deletar and not links_para_inserir:
        print("   -> Links de habilidades já existem. Nada a fazer.")

# --- Orquestrador ---

def main():
    
    conn_p = None
    conn_s = None
    
    try:
        conn_p = conectar_banco(DB_PRIMARIO_CONFIG, "PRIMÁRIO")
        conn_s = conectar_banco(DB_SECUNDARIO_CONFIG, "SECUNDÁRIO")

        cur_p = conn_p.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur_s = conn_s.cursor(cursor_factory=psycopg2.extras.DictCursor)

        mapas = carregar_mapas_traducao(cur_s)
        
        sincronizar_deletes(cur_p, cur_s, mapas.get('usuario', {}))
        
        sincronizar_updates_usuarios(cur_p, cur_s, mapas)
        
        sincronizar_novos_usuarios(cur_p, cur_s, mapas)

        sincronizar_habilidades(cur_p, cur_s, mapas)

        print("\n--- FIM DA SINCRONIZAÇÃO ---")
        print("   -> Salvando alterações no banco PRIMÁRIO...")
        conn_p.commit()
        print("   -> Script concluído com sucesso.")

    except Exception as e_geral:
        print(f"        ERRO GERAL NO CDC: {e_geral}")
        if conn_p:
            print("   -> Revertendo alterações no PRIMÁRIO (Rollback)...")
            conn_p.rollback()

            
    finally:
        if 'cur_p' in locals(): cur_p.close()
        if 'cur_s' in locals(): cur_s.close()
        if conn_p: conn_p.close()
        if conn_s: conn_s.close()
        print("Conexões com os bancos fechadas.")


if __name__ == "__main__":
    main()