import psycopg2
import psycopg2.extras
import json
from thefuzz import process, fuzz
from dotenv import load_dotenv
import os
import sys
import bcrypt 

"""
 --- RPA de Sincronização entre Bancos de Dados Primário e Secundário (CDC)---
Para esse RPA, estruturei para seguir as seguintes 6 regras:

1) Inserção de Novos Registros: Se um registro novo existir no banco primário e não existir no banco secundário, o RPA deve inserir esse novo registro no secundário.

2) Atualização de Alterações: Se um registro for alterado no banco primário, o RPA deve atualizar o registro correspondente no banco secundário para refletir essa alteração.

3) Remoção de Registros Deletados: Se um registro for deletado do banco primário, o RPA deve deletar o registro correspondente do banco secundário.

4) Respeito às Alterações do Secundário: Todas as movimentações do primário (inserções, atualizações, deleções) devem ser refletidas no secundário. No entanto, se uma alteração ocorrer apenas no secundário (e o registro no primário permanecer inalterado desde a última sincronização), o RPA deve respeitar e manter a alteração feita no secundário.

5) Mesclagem de Alterações (Merge): Se um registro for alterado no primário (ex: coluna_A) e o mesmo registro for alterado em uma coluna diferente no secundário (ex: coluna_B), o RPA deve mesclar as duas alterações. O resultado final no secundário deve conter tanto a alteração da coluna_A (vinda do primário) quanto a da coluna_B (que já estava no secundário). Nenhuma alteração pode ser perdida.

6) Resolução de Conflitos (Primário Vence): Se um registro for alterado no primário (ex: coluna_A mudou de "X" para "Y") e a mesma coluna do mesmo registro for alterada no secundário (ex: coluna_A mudou de "X" para "Z"), ocorre um conflito. Neste caso, a alteração do banco primário deve prevalecer. O RPA deve sobrescrever o valor do secundário ("Z") com o valor do primário ("Y"), mas deve registrar em log que esse conflito ocorreu e qual foi a decisão tomada (qual valor foi sobrescrito).

"""

load_dotenv()

psycopg2.extras.register_default_jsonb(globally=True)

DB_PRIMARIO_CONFIG = os.getenv("DATABASE_URL_PRIMARIO")
DB_SECUNDARIO_CONFIG = os.getenv("DATABASE_URL_SECUNDARIO")

if not DB_PRIMARIO_CONFIG or not DB_SECUNDARIO_CONFIG:
    print("      Erro: Variáveis de ambiente de configuração do banco de dados não encontradas.")
    sys.exit(1)


def get_mapa_log(cursor_secundario, nome_tabela):
    mapa_log = {}
    try:
        cursor_secundario.execute(
            """
            SELECT nCdOrigem
                 , nCdDestino
                 , jLinhaPrimariaAnterior 
              FROM table_log.rpa_mapa_ids
             WHERE cNmTabela = %s
            """,
            (nome_tabela,)
        )
        for linha in cursor_secundario:
            mapa_log[linha['ncdorigem']] = {
                'nCdDestino': linha['ncddestino'],
                'snapshot': linha['jlinhaprimariaanterior'] 
            }
        return mapa_log
    except Exception as e:
        print(f"    Erro ao buscar mapa de log completo para {nome_tabela}: {e}")
        return None

def logar_insercao(cursor_secundario, nome_tabela, id_origem, id_destino, linha_primario_dict):
    snapshot_json = json.dumps(linha_primario_dict, default=str)
    try:
        cursor_secundario.execute(
            """
            INSERT INTO table_log.rpa_mapa_ids (cNmTabela, nCdOrigem, nCdDestino, jLinhaPrimariaAnterior)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (cNmTabela, nCdOrigem) DO UPDATE
            SET nCdDestino             = EXCLUDED.nCdDestino
              , jLinhaPrimariaAnterior = EXCLUDED.jLinhaPrimariaAnterior;
            """,
            (nome_tabela, id_origem, id_destino, snapshot_json)
        )
    except Exception as e:
        print(f"    Erro ao logar inserção para {nome_tabela} (ID Origem: {id_origem}): {e}")

def logar_modificacao(cursor_secundario, nome_tabela, id_origem, linha_primario_dict):
    snapshot_json = json.dumps(linha_primario_dict, default=str)
    try:
        cursor_secundario.execute(
            """
            UPDATE table_log.rpa_mapa_ids 
               SET jLinhaPrimariaAnterior = %s
             WHERE cNmTabela = %s 
               AND nCdOrigem = %s
            """,
            (snapshot_json, nome_tabela, id_origem)
        )
    except Exception as e:
        print(f"    Erro ao logar modificação para {nome_tabela} (ID Origem: {id_origem}): {e}")

def logar_conflito(cursor_secundario, nome_tabela_s, id_destino, coluna_s, val_primario, val_secundario):
    try:
        cursor_secundario.execute(
            """
            INSERT INTO table_log.rpa_conflitos (cNmTabela, nCdDestino, cNmColuna, cValorPrimario, cValorSecundario)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (nome_tabela_s, id_destino, coluna_s, str(val_primario), str(val_secundario))
        )
        print(f"   > CONFLITO (Regra 6) Logado: Tabela {nome_tabela_s} (ID: {id_destino}), Coluna {coluna_s}. Primário ('{val_primario}') venceu Secundário ('{val_secundario}').")
    except Exception as e:
        print(f"Erro ao logar conflito para {nome_tabela_s} (ID Destino: {id_destino}): {e}")

def deletar_do_log(cursor_secundario, nome_tabela, id_origem):
    try:
        cursor_secundario.execute(
            "DELETE FROM table_log.rpa_mapa_ids WHERE cNmTabela = %s AND nCdOrigem = %s",
            (nome_tabela, id_origem)
        )
    except Exception as e:
        print(f"Erro ao deletar do log {nome_tabela} (ID Origem: {id_origem}): {e}")


def gerar_sigla(nome):
    if not nome:
        return ""
    
    quebra_nome = str(nome).split(' ')
    sigla = ""

    if len(quebra_nome) >= 3:
        return (quebra_nome[0][0] + quebra_nome[1][0] + quebra_nome[2][0]).upper()
    else:
        if len(quebra_nome) == 2:
            sigla = (quebra_nome[0][0] + quebra_nome[1][0]).upper()
            if len(sigla) < 3:
                sigla += quebra_nome[1][1] if len(quebra_nome[1]) > 1 else ''
            return sigla
        
    return nome[:3].upper()

def formatar_telefone(telefone):
    if not telefone:
        return None

    numeros = ''.join(filter(str.isdigit, telefone))

    if len(numeros) == 10:  
        return f"({numeros[:2]}) {numeros[2:6]}-{numeros[6:]}"
    elif len(numeros) == 11:  
        return f"({numeros[:2]}) {numeros[2:7]}-{numeros[7:]}"
    else:
        return telefone
    
def formatar_cep(cep):
    if not cep:
        return None

    numeros = ''.join(filter(str.isdigit, cep))

    if len(numeros) == 8:
        return f"{numeros[:5]}-{numeros[5:]}"
    else:
        return cep

def formatar_cnpj(cnpj):
    if not cnpj:
        return None

    numeros = ''.join(filter(str.isdigit, cnpj))

    if len(numeros) == 14:
        return f"{numeros[:2]}.{numeros[2:5]}.{numeros[5:8]}/{numeros[8:12]}-{numeros[12:]}"
    else:
        return cnpj
    
def formatar_email(email):
    if not email:
        return ''

    return email.strip().lower()

def formatar_cpf(cpf): 
    if not cpf:
        return None

    numeros = ''.join(filter(str.isdigit, cpf))

    if len(numeros) == 11:
        return f"{numeros[:3]}.{numeros[3:6]}.{numeros[6:9]}-{numeros[9:]}"
    else:
        return cpf

def processar_cargo(cursor_secundario, cargo_texto_origem):
    """
    Estamos aplicando a lógica Fuzzy Matching pois no banco primário, a coluna de cargo é um texto na tabela usuário, e para impedir que haja repetição na tabela de cargo no banco secundário (Ex.: 1-DesenvolvedOr; 2-DesenvolvedorA) essas funções encontram valores próximos e aplicam, porém existe uma taxa de erros possíveis.
    1. Busca o cargo na tabela public.Cargo.
    2. Se a pontuação for boa (>= 85), retorna o ID.
    3. Se não, insere o novo cargo em public.Cargo e retorna o novo ID.
    """
    if not cargo_texto_origem:
        return None

    cargo_texto_limpo = cargo_texto_origem.strip()

    cursor_secundario.execute("SELECT nCdCargo, cNmCargo FROM public.Cargo")
    cargos_oficiais = cursor_secundario.fetchall()
    
    mapa_cargos = {cargo['cnmcargo']: cargo['ncdcargo'] for cargo in cargos_oficiais}
    lista_nomes_cargos = list(mapa_cargos.keys())

    if lista_nomes_cargos:
        melhor_match = process.extractOne(
            cargo_texto_limpo,
            lista_nomes_cargos,
            scorer=fuzz.token_set_ratio,
            score_cutoff=85 # Nota de corte
        )
        
        if melhor_match:
            nome_cargo_encontrado = melhor_match[0]
            id_cargo_encontrado = mapa_cargos[nome_cargo_encontrado]
            print(f"Fuzzy Match: '{cargo_texto_limpo}' -> '{nome_cargo_encontrado}' (ID: {id_cargo_encontrado})")
            return id_cargo_encontrado

    print(f"    Fuzzy Match falhou para '{cargo_texto_limpo}'. Inserindo como novo cargo...")
    try:
        cursor_secundario.execute(
            """
            INSERT INTO public.Cargo (cNmCargo) 
            VALUES (%s)
            ON CONFLICT (cNmCargo) DO UPDATE SET cNmCargo = EXCLUDED.cNmCargo
            RETURNING nCdCargo;
            """,
            (cargo_texto_limpo,) 
        )
        novo_id_cargo = cursor_secundario.fetchone()['ncdcargo']
        print(f"Novo cargo criado/encontrado: '{cargo_texto_limpo}' (ID: {novo_id_cargo})")
        return novo_id_cargo
    except Exception as e:
        print(f"Erro ao inserir novo cargo '{cargo_texto_limpo}': {e}")
        return None


def sincronizar_tabela_generica(cur_p, cur_s, config_tabela, mapa_log_completo):
    
    NOME_TABELA_LOG = config_tabela['tabela_log']
    NOME_TABELA_P = config_tabela['tabela_p']
    NOME_TABELA_S = config_tabela['tabela_s']
    PK_P = config_tabela['pk_p']
    PK_S = config_tabela['pk_s']
    MAPA_COLUNAS = config_tabela['mapa_colunas']
    
    print(f"--- Iniciando Sincronização de [{NOME_TABELA_LOG}] ---")
    
    print(f"[{NOME_TABELA_LOG}] Fase 1/3: Coletando IDs...")
    cur_p.execute(f"SELECT {PK_P} FROM public.{NOME_TABELA_P}")
    ids_primario_set = {row[PK_P] for row in cur_p}
    
    mapa_log = get_mapa_log(cur_s, NOME_TABELA_LOG)
    ids_log_set = set(mapa_log.keys())

    print(f"[{NOME_TABELA_LOG}] Fase 2/3: Processando Deletes...")
    ids_para_deletar = ids_log_set - ids_primario_set
    if ids_para_deletar:
        for id_origem in ids_para_deletar:
            id_destino = mapa_log[id_origem]['nCdDestino']
            try:
                cur_s.execute(f"DELETE FROM public.{NOME_TABELA_S} WHERE {PK_S} = %s", (id_destino,))
                deletar_do_log(cur_s, NOME_TABELA_LOG, id_origem)
                print(f"   - DELETADO (Regra 3): {NOME_TABELA_S} (ID Destino: {id_destino}) pois foi removido da origem (ID Origem: {id_origem}).")
            except Exception as e:
                print(f"   X ERRO ao deletar {NOME_TABELA_S} (ID Destino: {id_destino}): {e}. Pode ser uma restrição de FK.")
                
    print(f"[{NOME_TABELA_LOG}] Fase 3/3: Processando Inserts/Updates...")
    cur_p.execute(f"SELECT * FROM public.{NOME_TABELA_P}")
    
    for linha_p in cur_p:
        linha_p_dict = dict(linha_p) 
        id_origem = linha_p_dict[PK_P]
        linha_log = mapa_log.get(id_origem)
        
        if config_tabela.get('preprocessar'):
            try:
                linha_p_dict = config_tabela['preprocessar'](cur_p, cur_s, linha_p_dict, mapa_log_completo)
                if linha_p_dict is None:
                    continue
            except Exception as e:
                print(f"   X ERRO no pre-processamento de {NOME_TABELA_LOG} (ID Origem: {id_origem}): {e}")
                continue

        dados_transformados = {}
        for col_p, col_s in MAPA_COLUNAS.items():
            if col_s: 
                dados_transformados[col_s] = linha_p_dict.get(col_p)

        if not linha_log:
            colunas_s = ", ".join(dados_transformados.keys())
            placeholders = ", ".join(["%s"] * len(dados_transformados))
            valores = list(dados_transformados.values())
            
            try:
                sql_insert = f"INSERT INTO public.{NOME_TABELA_S} ({colunas_s}) VALUES ({placeholders}) RETURNING {PK_S};"
                cur_s.execute(sql_insert, valores)
                id_destino_novo = cur_s.fetchone()[PK_S.lower()]
                
                logar_insercao(cur_s, NOME_TABELA_LOG, id_origem, id_destino_novo, linha_p_dict)
                print(f"   + INSERIDO (Regra 1): {NOME_TABELA_S} (ID Origem: {id_origem}) -> (ID Destino: {id_destino_novo}).")
            except Exception as e:
                print(f"   X ERRO ao inserir {NOME_TABELA_S} (ID Origem: {id_origem}): {e}")
            continue

        id_destino = linha_log['nCdDestino']
        snapshot_anterior = linha_log['snapshot']

        if not snapshot_anterior:
            logar_modificacao(cur_s, NOME_TABELA_LOG, id_origem, linha_p_dict)
            print(f"   ! ATENÇÃO: Snapshot não encontrado para {NOME_TABELA_LOG} (ID Origem: {id_origem}). Log foi atualizado; alterações serão processadas na próxima execução.")
            continue
            
        if snapshot_anterior == linha_p_dict:
            continue
            
        cur_s.execute(f"SELECT * FROM public.{NOME_TABELA_S} WHERE {PK_S} = %s", (id_destino,))
        linha_s = cur_s.fetchone()
        if not linha_s:
            if NOME_TABELA_LOG == 'usuario':
                print(f"   ! AVISO (REGRA 7): Usuário (ID P: {id_origem}) existe no log mas (ID S: {id_destino}) foi deletado do Secundário.")
                print(f"   -> Pulando. O item não será recriado e o log será mantido.")
                continue

            print(f"   X ERRO: Inconsistência de dados! Log existe mas {NOME_TABELA_S} (ID Destino: {id_destino}) não foi encontrado. Recriando...")
            deletar_do_log(cur_s, NOME_TABELA_LOG, id_origem)
            continue
            
        dados_para_update = {} 
        
        for col_p, col_s in MAPA_COLUNAS.items():
            if not col_s: 
                continue
                
            val_p_atual = linha_p_dict.get(col_p)
            val_p_anterior = snapshot_anterior.get(col_p)
            
            col_s_lower = col_s.lower()
            if col_s_lower not in linha_s:
                print(f"   X ERRO: Coluna {col_s_lower} não encontrada na tabela de destino {NOME_TABELA_S}.")
                continue
            val_s_atual = linha_s[col_s_lower]
            
            if str(val_p_atual) != str(val_p_anterior):
                if str(val_s_atual) != str(val_p_anterior):
                    logar_conflito(cur_s, NOME_TABELA_S, id_destino, col_s, val_p_atual, val_s_atual)
                    dados_para_update[col_s] = val_p_atual
                else:
                    dados_para_update[col_s] = val_p_atual

        if dados_para_update:
            set_clause = ", ".join([f"{col} = %s" for col in dados_para_update.keys()])
            valores = list(dados_para_update.values()) + [id_destino]
            
            try:
                sql_update = f"UPDATE public.{NOME_TABELA_S} SET {set_clause} WHERE {PK_S} = %s;"
                cur_s.execute(sql_update, valores)
                
                logar_modificacao(cur_s, NOME_TABELA_LOG, id_origem, linha_p_dict)
                print(f"   = ATUALIZADO (Regras 2,5,6): {NOME_TABELA_S} (ID Destino: {id_destino}). Colunas: {list(dados_para_update.keys())}")
            except Exception as e:
                print(f"   X ERRO ao atualizar {NOME_TABELA_S} (ID Destino: {id_destino}): {e}")
        else:
            logar_modificacao(cur_s, NOME_TABELA_LOG, id_origem, linha_p_dict)
            
    print(f"--- Concluída Sincronização de [{NOME_TABELA_LOG}] ---")


# --- Regras de Transformação ---

def pre_processar_administracao(cur_p, cur_s, linha_p_dict, mapa_log_completo):
    """Aplica Hashing BCrypt na senha do Administrador."""
    senha_plain = linha_p_dict.get('senha')
    
    if senha_plain and not senha_plain.startswith('$2') and senha_plain != "criptografadoSegundoAnoBcrypt$2": 
        print(f"   * HASHING: Senha para Administracao ID {linha_p_dict['id']}...")
        senha_bytes = senha_plain.encode('utf-8')
        salt = bcrypt.gensalt(rounds=10) 
        hash_bcrypt = bcrypt.hashpw(senha_bytes, salt).decode('utf-8')
        linha_p_dict['senha'] = hash_bcrypt
        

    linha_p_dict['email'] = formatar_email(linha_p_dict.get('email'))
    
    return linha_p_dict

def pre_processar_empresa(cur_p, cur_s, linha_p_dict, mapa_log_completo):
    linha_p_dict['sigla'] = gerar_sigla(linha_p_dict['nome'])
    
    linha_p_dict['telefone'] = formatar_telefone(linha_p_dict.get('telefone'))
    linha_p_dict['cep'] = formatar_cep(linha_p_dict.get('cep'))
    linha_p_dict['cnpj'] = formatar_cnpj(linha_p_dict.get('cnpj'))
    linha_p_dict['email'] = formatar_email(linha_p_dict.get('email'))

    mapa_planos = mapa_log_completo['Planos']
    mapa_plano_origem = mapa_planos.get(linha_p_dict['fk_plano_id'])
    if not mapa_plano_origem:
        print(f"   ! ERRO FK: Plano (ID Origem: {linha_p_dict['fk_plano_id']}) não encontrado no log. Pulando Empresa (ID Origem: {linha_p_dict['id']}).")
        return None
    
    linha_p_dict['nCdPlanoPagamento'] = mapa_plano_origem['nCdDestino']
    return linha_p_dict
    
def pre_processar_setor(cur_p, cur_s, linha_p_dict, mapa_log_completo):
    linha_p_dict['sigla'] = gerar_sigla(linha_p_dict['nome'])
    
    mapa_empresas = mapa_log_completo['Empresa']
    mapa_empresa_origem = mapa_empresas.get(linha_p_dict['fk_empresa_id'])
    if not mapa_empresa_origem:
        print(f"   ! ERRO FK: Empresa (ID Origem: {linha_p_dict['fk_empresa_id']}) não encontrado no log. Pulando Setor (ID Origem: {linha_p_dict['id']}).")
        return None
        
    linha_p_dict['nCdEmpresa'] = mapa_empresa_origem['nCdDestino']
    return linha_p_dict

def pre_processar_usuario(cur_p, cur_s, linha_p_dict, mapa_log_completo):
    
    senha_plain = linha_p_dict.get('senha')
    if senha_plain and not senha_plain.startswith('$2') and senha_plain != "criptografadoSegundoAnoBcrypt$2": 
        print(f"   * HASHING: Senha para Usuario ID {linha_p_dict['id']}...")
        senha_bytes = senha_plain.encode('utf-8')
        salt = bcrypt.gensalt(rounds=10) 
        hash_bcrypt = bcrypt.hashpw(senha_bytes, salt).decode('utf-8')
        linha_p_dict['senha'] = hash_bcrypt

    linha_p_dict['bGestor'] = True

    linha_p_dict['bAtivo'] = True if linha_p_dict.get('status') == 'Ativo' else False

    linha_p_dict['cpf'] = formatar_cpf(linha_p_dict.get('cpf'))

    linha_p_dict['email'] = formatar_email(linha_p_dict.get('email')) if linha_p_dict.get('email') else ''

    linha_p_dict['telefone'] = formatar_telefone(linha_p_dict.get('telefone'))
    
    mapa_setores = mapa_log_completo['Setor']
    mapa_setor_origem = mapa_setores.get(linha_p_dict['fk_setor_id'])
    if not mapa_setor_origem:
        print(f"   ! ERRO FK: Setor (ID Origem: {linha_p_dict['fk_setor_id']}) não encontrado no log. Pulando Usuário (ID Origem: {linha_p_dict['id']}).")
        return None
    linha_p_dict['nCdSetor'] = mapa_setor_origem['nCdDestino']
    
    id_cargo_destino = processar_cargo(cur_s, linha_p_dict['cargo'])
    if not id_cargo_destino:
        print(f"   ! ERRO CARGO: Falha ao processar cargo '{linha_p_dict['cargo']}'. Pulando Usuário (ID Origem: {linha_p_dict['id']}).")
        return None
    linha_p_dict['nCdCargo'] = id_cargo_destino
    
    snapshot_setor = mapa_log_completo['Setor'].get(linha_p_dict['fk_setor_id'], {}).get('snapshot')
    if not snapshot_setor:
        print(f"   ! ERRO LÓGICO: Snapshot do Setor (ID Origem: {linha_p_dict['fk_setor_id']}) não encontrado. Pulando Usuário (ID Origem: {linha_p_dict['id']}).")
        return None
        
    id_empresa_origem = snapshot_setor['fk_empresa_id']
    mapa_empresas = mapa_log_completo['Empresa']
    mapa_empresa_origem = mapa_empresas.get(id_empresa_origem)

    if not mapa_empresa_origem:
        print(f"   ! ERRO FK: Empresa (ID Origem: {id_empresa_origem}) não encontrada. Pulando Usuário (ID Origem: {linha_p_dict['id']}).")
        return None
    
    linha_p_dict['nCdEmpresa'] = mapa_empresa_origem['nCdDestino']
    
    linha_p_dict['nCdGestor'] = None
    return linha_p_dict

def atualizar_gestores_usuario(cur_p, cur_s, mapa_log_completo):
    print("--- Iniciando Sincronização de [Usuario.nCdGestor] ---")
    mapa_usuarios = mapa_log_completo['usuario']
    
    cur_p.execute("SELECT id, fk_supervisor_id FROM public.usuario WHERE fk_supervisor_id IS NOT NULL")
    
    for linha_p in cur_p:
        mapa_usuario_origem = mapa_usuarios.get(linha_p['id'])
        mapa_supervisor_origem = mapa_usuarios.get(linha_p['fk_supervisor_id'])
        
        if not mapa_usuario_origem:
            continue 
        
        id_destino_usuario = mapa_usuario_origem['nCdDestino']
        
        id_destino_supervisor = None
        if mapa_supervisor_origem:
            id_destino_supervisor = mapa_supervisor_origem['nCdDestino']
        
        try:
            if id_destino_supervisor == id_destino_usuario:
                id_destino_supervisor = None

            cur_s.execute(
                f"""
                UPDATE public.Usuario 
                   SET nCdGestor = %s 
                 WHERE nCdUsuario = %s 
                   AND (nCdGestor IS DISTINCT FROM %s)
                """,
                (id_destino_supervisor, id_destino_usuario, id_destino_supervisor)
            )
        except Exception as e:
            print(f"   X ERRO ao atualizar gestor do Usuário (ID Destino: {id_destino_usuario}): {e}")
    print("--- Concluída Sincronização de [Usuario.nCdGestor] ---")

def sincronizar_usuario_habilidade(cur_p, cur_s, mapa_log_completo):
    print("--- Iniciando Sincronização de [usuario_habilidade] ---")
    mapa_usuarios = mapa_log_completo['usuario']
    mapa_habilidades = mapa_log_completo['Habilidade']
    
    cur_p.execute("SELECT fk_usuario_id, fk_habilidade_id FROM public.usuario_habilidade")
    links_primario_set = {(row['fk_usuario_id'], row['fk_habilidade_id']) for row in cur_p}
    
    cur_s.execute("SELECT nCdUsuario, nCdHabilidade FROM public.HabilidadeUsuario")
    links_secundario_set = {(row['ncdusuario'], row['ncdhabilidade']) for row in cur_s}
    
    mapa_usuarios_inv = {v['nCdDestino']: k for k, v in mapa_usuarios.items()}
    mapa_habilidades_inv = {v['nCdDestino']: k for k, v in mapa_habilidades.items()}

    ids_para_deletar = set()
    for id_s_usuario, id_s_habilidade in links_secundario_set:
        id_p_usuario = mapa_usuarios_inv.get(id_s_usuario)
        id_p_habilidade = mapa_habilidades_inv.get(id_s_habilidade)
        

        if id_p_usuario is not None and id_p_habilidade is not None:
           
            if (id_p_usuario, id_p_habilidade) not in links_primario_set:
                ids_para_deletar.add((id_s_usuario, id_s_habilidade))

    for id_s_usuario, id_s_habilidade in ids_para_deletar:
        try:
            cur_s.execute(
                "DELETE FROM public.HabilidadeUsuario WHERE nCdUsuario = %s AND nCdHabilidade = %s",
                (id_s_usuario, id_s_habilidade)
            )
            print(f"   - DELETADO (Regra 3): Link HabilidadeUsuario (Usuário: {id_s_usuario}, Hab: {id_s_habilidade})")
        except Exception as e:
            print(f"   X ERRO ao deletar link HabilidadeUsuario: {e}")

    for id_p_usuario, id_p_habilidade in links_primario_set:
        mapa_u = mapa_usuarios.get(id_p_usuario)
        mapa_h = mapa_habilidades.get(id_p_habilidade)
        
        if not mapa_u or not mapa_h:
            continue 
        
        id_s_usuario = mapa_u['nCdDestino']
        id_s_habilidade = mapa_h['nCdDestino']
        
        if (id_s_usuario, id_s_habilidade) not in links_secundario_set:
            try:
                cur_s.execute(
                    "INSERT INTO public.HabilidadeUsuario (nCdUsuario, nCdHabilidade) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (id_s_usuario, id_s_habilidade)
                )
                print(f"   + INSERIDO (Regra 1): Link HabilidadeUsuario (Usuário: {id_s_usuario}, Hab: {id_s_habilidade})")
            except Exception as e:
                print(f"   X ERRO ao inserir link HabilidadeUsuario: {e}")
                
    print("--- Concluída Sincronização de [usuario_habilidade] ---")

def sincronizar_plano_vantagem(cur_p, cur_s, mapa_log_completo):
    print("--- Iniciando Sincronização de [PlanoVantagem] ---")
    mapa_planos = mapa_log_completo['Planos']
    
    cur_p.execute("SELECT id, descricao FROM public.planos WHERE descricao IS NOT NULL AND descricao != ''")
    planos_p = cur_p.fetchall()
    
    cur_s.execute("SELECT nCdPlano, cNmVantagem FROM public.PlanoVantagem")
    links_secundario_set = {(row['ncdplano'], row['cnmvantagem']) for row in cur_s}
    
    links_primario_set = set()
    
    for plano_p in planos_p:
        id_origem = plano_p['id']
        mapa = mapa_planos.get(id_origem)
        
        if not mapa:
            print(f"   ! AVISO: Plano (ID Origem: {id_origem}) não encontrado no log. Pulando vantagens.")
            continue
            
        id_destino_plano = mapa['nCdDestino']
        vantagens = plano_p['descricao'].split(';')
        
        for v in vantagens:
            vantagem_limpa = v.strip()
            if vantagem_limpa:
                links_primario_set.add((id_destino_plano, vantagem_limpa))

    mapa_planos_inv = {v['nCdDestino']: k for k, v in mapa_planos.items()}
    
    ids_para_deletar = set()
    for id_s_plano, nm_vantagem in links_secundario_set:
        id_p_plano = mapa_planos_inv.get(id_s_plano)
        
        if id_p_plano is not None:
            if (id_s_plano, nm_vantagem) not in links_primario_set:
                ids_para_deletar.add((id_s_plano, nm_vantagem))
                
    for id_s_plano, nm_vantagem in ids_para_deletar:
        try:
            cur_s.execute(
                "DELETE FROM public.PlanoVantagem WHERE nCdPlano = %s AND cNmVantagem = %s",
                (id_s_plano, nm_vantagem)
            )
            print(f"   - DELETADO (Regra 3): Link PlanoVantagem (Plano: {id_s_plano}, Vantagem: {nm_vantagem})")
        except Exception as e:
            print(f"   X ERRO ao deletar link PlanoVantagem: {e}")

    ids_para_inserir = links_primario_set - links_secundario_set
    for id_s_plano, nm_vantagem in ids_para_inserir:
        try:
            cur_s.execute(
                "INSERT INTO public.PlanoVantagem (nCdPlano, cNmVantagem, cDescricao) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (id_s_plano, nm_vantagem, nm_vantagem) 
            )
            print(f"   + INSERIDO (Regra 1): Link PlanoVantagem (Plano: {id_s_plano}, Vantagem: {nm_vantagem})")
        except Exception as e:
            print(f"   X ERRO ao inserir link PlanoVantagem: {e}")
            
    print("--- Concluída Sincronização de [PlanoVantagem] ---")


# --- Orquestrador ---

def executar_sincronizacao(cur_p, cur_s):
    mapa_log_completo = {
        'Planos': get_mapa_log(cur_s, 'Planos'),
        'Administracao': get_mapa_log(cur_s, 'Administracao'),
        'Empresa': get_mapa_log(cur_s, 'Empresa'),
        'Setor': get_mapa_log(cur_s, 'Setor'),
        'Habilidade': get_mapa_log(cur_s, 'Habilidade'),
        'usuario': get_mapa_log(cur_s, 'usuario')
    }
    
    config_planos = {
        'tabela_log': 'Planos', 'tabela_p': 'planos', 'tabela_s': 'PlanoPagamento',
        'pk_p': 'id', 'pk_s': 'nCdPlano',
        'mapa_colunas': {'nomeplano': 'cNmPlano', 'descricao': None, 'qnt_max_funcionario': None, 'custo': 'nPreco'}
    }
    sincronizar_tabela_generica(cur_p, cur_s, config_planos, mapa_log_completo)
    mapa_log_completo['Planos'] = get_mapa_log(cur_s, 'Planos')

    sincronizar_plano_vantagem(cur_p, cur_s, mapa_log_completo)
    
    config_admin = {
        'tabela_log': 'Administracao', 'tabela_p': 'administracao', 'tabela_s': 'Administracao',
        'pk_p': 'id', 'pk_s': 'nCdAdm',
        'mapa_colunas': {'nome': 'cNmAdm', 'email': 'cEmailAdm', 'senha': 'cSenha'},
        'preprocessar': pre_processar_administracao # <--- 5. HASHING ADMIN ATIVADO
    }
    sincronizar_tabela_generica(cur_p, cur_s, config_admin, mapa_log_completo)
    mapa_log_completo['Administracao'] = get_mapa_log(cur_s, 'Administracao')

    config_empresa = {
        'tabela_log': 'Empresa', 'tabela_p': 'empresa', 'tabela_s': 'Empresa',
        'pk_p': 'id', 'pk_s': 'nCdEmpresa',
        'mapa_colunas': {
            'nome': 'cNmEmpresa', 'cnpj': 'cCNPJ', 'email': 'cEmail', 'telefone': 'cTelefone',
            'cep': 'cCEP', 'sigla': 'cSgEmpresa', 'nCdPlanoPagamento': 'nCdPlanoPagamento'
        },
        'preprocessar': pre_processar_empresa
    }
    sincronizar_tabela_generica(cur_p, cur_s, config_empresa, mapa_log_completo)
    mapa_log_completo['Empresa'] = get_mapa_log(cur_s, 'Empresa')
    
    config_setor = {
        'tabela_log': 'Setor', 'tabela_p': 'setor', 'tabela_s': 'Setor',
        'pk_p': 'id', 'pk_s': 'nCdSetor',
        'mapa_colunas': {'nome': 'cNmSetor', 'sigla': 'cSgSetor', 'nCdEmpresa': 'nCdEmpresa'},
        'preprocessar': pre_processar_setor
    }
    sincronizar_tabela_generica(cur_p, cur_s, config_setor, mapa_log_completo)
    mapa_log_completo['Setor'] = get_mapa_log(cur_s, 'Setor')
    
    config_habilidade = {
        'tabela_log': 'Habilidade', 'tabela_p': 'habilidade', 'tabela_s': 'Habilidade',
        'pk_p': 'id', 'pk_s': 'nCdHabilidade',
        'mapa_colunas': {'nome': 'cNmHabilidade', 'descricao': 'cDescricao'} 
    }
    sincronizar_tabela_generica(cur_p, cur_s, config_habilidade, mapa_log_completo)
    mapa_log_completo['Habilidade'] = get_mapa_log(cur_s, 'Habilidade')

    config_usuario = {
        'tabela_log': 'usuario', 'tabela_p': 'usuario', 'tabela_s': 'Usuario',
        'pk_p': 'id', 'pk_s': 'nCdUsuario',
        'mapa_colunas': {
            'nome': 'cNmUsuario', 'cpf': 'cCPF', 'senha': 'cSenha', 'bGestor': 'bGestor',
            'nCdEmpresa': 'nCdEmpresa', 'nCdSetor': 'nCdSetor', 'nCdCargo': 'nCdCargo', 'nCdGestor': 'nCdGestor', 'telefone': 'cTelefone', 'bAtivo': 'bAtivo', 'email': 'cEmail'
        },
        'preprocessar': pre_processar_usuario 
    }
    sincronizar_tabela_generica(cur_p, cur_s, config_usuario, mapa_log_completo)
    mapa_log_completo['usuario'] = get_mapa_log(cur_s, 'usuario')
    
    sincronizar_usuario_habilidade(cur_p, cur_s, mapa_log_completo)
    
    atualizar_gestores_usuario(cur_p, cur_s, mapa_log_completo)



if __name__ == "__main__":
    conn_primario = None
    conn_secundario = None
    
    try:
        print("Conectando ao banco de dados PRIMÁRIO (Origem)...")
        conn_primario = psycopg2.connect(dsn=DB_PRIMARIO_CONFIG)
        
        print("Conectando ao banco de dados SECUNDÁRIO (Destino)...")
        conn_secundario = psycopg2.connect(dsn=DB_SECUNDARIO_CONFIG)
        
        conn_primario.set_session(readonly=True, autocommit=True)
        
        cur_p = conn_primario.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur_s = conn_secundario.cursor(cursor_factory=psycopg2.extras.DictCursor)

        print(">>> INICIANDO PROCESSO DE SINCRONIZAÇÃO (RPA) <<<")
        
        executar_sincronizacao(cur_p, cur_s)

        print("Sincronização concluída. Fazendo commit das alterações...")
        conn_secundario.commit()
        print("Commit realizado com sucesso.")

    except Exception as e:
        print(f"!!! ERRO GERAL NO RPA !!!: {e}")
        if conn_secundario:
            print("Executando ROLLBACK para reverter alterações...")
            conn_secundario.rollback()
            print("ROLLBACK concluído.")
            
    finally:
        if cur_p: cur_p.close()
        if cur_s: cur_s.close()
        if conn_primario: conn_primario.close()
        if conn_secundario: conn_secundario.close()
        print("Conexões com os bancos de dados fechadas.")