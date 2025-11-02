#Imports ===============================================================================
import psycopg2
import json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

#Pega o nome e os valores da coluna -> transforma os dados da alteração em um dicionário
def cols_list_to_dict(cols):
    return {c.get("name"): c.get("value") for c in cols or []}

#Identifica o ID do uodate ou delete
def identity_list_to_dict(identity):
    return {c.get("name"): c.get("value") for c in identity or []}

#Gerando o sql que está adaptado para a quantidade de colunas ou registros
def generate_sql(row, staging, op=None, timestamp=None, pk=None):
    # Valores sendo guardados em uma lista
    vals = []

    if staging:
        cols = [k for k in row.keys() if k not in ("criado_em", "atualizado_em", "updated_at", "deleted_at", "_op")] #Selecionar estas colunas para poder fazer a ação de UPDATE (kind: U)
        vals = [row[c] for c in cols]
        cols.extend(["updated_at", "deleted_at", "_op"])
        if op == 'D':
            vals.extend([None, timestamp, op])
        else:
            vals.extend([timestamp, None, op])
    else:
        cols = [k for k in row.keys() if k not in ("criado_em", "atualizado_em")]
        vals = [row[c] for c in cols]
    return cols, vals

def verifying_existence_table(cur, schema, tabela):
    cur.execute("""
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = %s
            AND table_name = %s
        );
    """, (schema, tabela))
    return cur.fetchone()[0]

#Pegando a primary_key da tabela
def get_primary_key(cur, table_name, schema='public'):
    cur.execute("""
        SELECT a.attname
        FROM pg_index i
        JOIN pg_attribute a 
          ON a.attrelid = i.indrelid
         AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = %s::regclass
          AND i.indisprimary;
    """, (f'{schema}.{table_name}',))
    result = cur.fetchone()
    return result[0] if result else None

def main():
    conn_primario = psycopg2.connect(
        ""
    )
    cur_prim = conn_primario.cursor()

    conn_secundario = psycopg2.connect(
        ""
    )
    
    cur_sec = conn_secundario.cursor()

    #Garantindo que o schema public exista, havia dado erro: Ocorreu um erro: schema "public" does not exist
    cur_sec.execute("CREATE SCHEMA IF NOT EXISTS public;")
    conn_secundario.commit()

    slot_name = 'slot_wal2_json3'

    try:
        #1) CRIAÇÃO DO SLOT --------------------------------------------------------------------
        #Verifica a existência do slot
        cur_prim.execute(f"SELECT 1 FROM pg_replication_slots WHERE slot_name = '{slot_name}';")
        if not cur_prim.fetchone():
            cur_prim.execute(f"SELECT pg_create_logical_replication_slot('{slot_name}', 'wal2json');")
            print(f"Slot '{slot_name}' criado.")

        #Capturando as alterações que ocorreram na tabela primária
        cur_prim.execute(f"""
            SELECT data
            FROM pg_logical_slot_get_changes(
                '{slot_name}', NULL, NULL,
                'format-version', '2',
                'include-timestamp', '1'
            );
        """)
        rows = cur_prim.fetchall()

        if not rows:
            print("Nenhuma alteração nova encontrada.")
            return 

        #2) ALTERAÇÕES --------------------------------------------------------------------
        for r in rows:
            change = json.loads(r[0])
            print(change)

            timestamp_str = change.get("timestamp")
            try:
                #Tenta transformar o timestamp gerado no json pelo wal2json
                timestamp_dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S.%f%z')
            except:
                timestamp_dt = datetime.now()

            #Verifica se há alguma mudança na tabela 
            table_changes = change.get("change") or [change]

            for evt in table_changes:
                #Pega o nome da tabela que ocorreu a alteração
                table = evt.get("table") or evt.get("table_name") or change.get("table") or change.get("table_name")
                #QUal foi o tipo e alteração: UID -> as alterações do tipo BC são gerads automaticamente, quando o sistema CDC verifica se não há nenhuma alteração 
                kind = evt.get("kind") or evt.get("action") or change.get("action")
                if not table:
                    continue

                #3) INFORMAÇÕES DA TABELA --------------------------------------------------------
                #Veriifica TODAS as COLUNAS da tabela na qual ocorreu a alteração, juntamente com seu tipo e se NULL ou NOT NULL
                cur_prim.execute("""
                    SELECT column_name, data_type, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_schema = %s
                    AND table_name = %s
                    ORDER BY ordinal_position
                """, ("public", table))
                columns = cur_prim.fetchall()
                if not columns:
                    print(f"Tabela primária '{table}' não encontrada.")
                    continue

                #Com base na consulta anterior, verifica todas as colunas ue estão no banco primário 
                col_defs = []
                for col_name, data_type, is_nullable, col_default in columns:
                    if col_default and "nextval" in str(col_default):
                        # Converte SERIAL em IDENTITY
                        col_def = f'"{col_name}" {data_type} GENERATED ALWAYS AS IDENTITY'
                    else:
                        col_def = f'"{col_name}" {data_type}'
                        if col_default:
                            col_def += f' DEFAULT {col_default}'
                    if is_nullable == 'NO':
                        col_def += ' NOT NULL'
                    col_defs.append(col_def)

                    
                #4) CRIAÇÃO DA TABELA NO SECUNDÁRIO ------------------------------------------------
                #Cria tabela schema public se não existir
                if not verifying_existence_table(cur_sec, "public", table):
                    pk = get_primary_key(cur_prim, table)
                    create_sql1 = f'CREATE TABLE public."{table}" ({", ".join(col_defs)}, PRIMARY KEY ({pk}));'
                    cur_sec.execute(create_sql1)

                #5) CRIAÇÃO DA TABELA DE STAGING  ------------------------------------------------
                #Já preparando as coluns para as atbelas de staging
                col_defs_delta = col_defs + [
                    'updated_at TIMESTAMP',
                    'deleted_at TIMESTAMP',
                    '_op CHAR(1)'
                ]

                #Cria tabela schema staging se não existir
                if not verifying_existence_table(cur_sec, "staging", f'{table}_delta'):
                    cur_sec.execute("CREATE SCHEMA IF NOT EXISTS staging")
                    pk = get_primary_key(cur_prim, table)
                    create_sql2 = f'CREATE TABLE staging."{table}_delta" ({", ".join(col_defs_delta)}, PRIMARY KEY ({pk}));'
                    cur_sec.execute(create_sql2)

                print(f"Tabelas 'public.{table}' e 'staging.{table}_delta' criadas no destino.")

                # Normaliza operação -> Para todos ficarem com a mesma nomenclatura 
                if kind in ("insert", "I"): kind = 'I'
                elif kind in ("update", "U"): kind = 'U'
                elif kind in ("delete", "D"): kind = 'D'
                else: continue

                #6) REGISTRANDO MODIFICAÇÕES DENTRO DO SCHEMA STAGING ------------------------------------------------
                #Pega a PK da tabela primária 
                pk = get_primary_key(cur_prim, table)

                # Pega as colunas da alteração 
                row = cols_list_to_dict(evt.get("columns") or []) #row pega as colunas retornadas por condição (UID) 
                print(f'\033[32m{row}\033[0m') #verde
                ident = identity_list_to_dict(evt.get("identity"))

                # Adiciona a PK corretamente na row de atualização 
                if pk: #Utilizado no final para o DELETE
                    row[pk] = row.get(pk) or ident.get(pk)

                #Lógica para atualização automática
                #Havia um erro quando o tipo era DELETE, duas opções: aplicar manualmente os erros por meio do código ou alterar a a construção da variável row, que serve como um retorno das colunas após cada operação

                #PROBLEMA: a função cols_list_to_dict aprensenta apenas o retorno das colunas na hora da geraçõa de valores
                if kind == 'D':
                    cur_sec.execute(
                        f"""
                            SELECT * FROM {table} WHERE {pk} = %s
                        """, (row[pk],)
                    )
                    vals_modification_type = cur_sec.fetchone()
                    new_cols = [col[0] for col in cur_sec.description]
                    row = dict(zip(new_cols, vals_modification_type))
                else:
                    pass

                # Staging -> INSERINDO os dados de atualização com seus respectivos valores
                cols, vals = generate_sql(row, staging=True, op=kind, timestamp=timestamp_dt)
                print(f'Indicador: \033[32m{row}\033[0m') #verde
                #cols precisa adicinar condição para se for delete, inserir os outros dados
                colnames_str = ", ".join([f'"{c}"' for c in cols])
                placeholders = ", ".join(["%s"] * len(vals))

                if kind == 'D':
                    updates = f'updated_at = NOW(), deleted_at = NOW(), _op = \'D\''
    
                else:
                    updates = ", ".join([
                        f'"{c}" = EXCLUDED."{c}"' 
                        for c in row.keys() if c not in ("criado_em", "atualizado_em", pk)
                    ])
                    updates += ", updated_at = NOW(), deleted_at = NULL, _op = EXCLUDED._op"

                sql_staging = f'''
                    INSERT INTO staging.{table}_delta ({colnames_str})
                    OVERRIDING SYSTEM VALUE
                    VALUES ({placeholders})
                    ON CONFLICT ("{pk}") DO UPDATE SET {updates} 
                ''' #O DO UPDATE SET serve para sobreescrever o dado caso já exista. Exceções: operação de INSERT 
                cur_sec.execute(sql_staging, vals)


                #7) REALIZAÇÃO DAS OPREÇÕES UID DENTRO DO SCHEMA PUBLIC ------------------------------------------------
                if kind == 'D':
                    #Aqui, a pk foi dinamizada para que sempre extraia da row(dicionário) o valor da pk
                    cur_sec.execute(f'DELETE FROM public.{table} WHERE {pk} = %s', (row[pk],)) 
                else:
                    cols_dw, vals_dw = generate_sql(row, staging=False, pk=pk)
                    colnames_dw = ", ".join([f'"{c}"' for c in cols_dw])
                    placeholders_dw = ", ".join(["%s"] * len(vals_dw))

                    update_cols_dw = [c for c in row.keys() if c != pk and c not in ("updated_at", "deleted_at", "_op")]
                    updates_dw = ", ".join([f'"{c}" = EXCLUDED."{c}"' for c in update_cols_dw])
                    print(f'Updates: \033[32m{update_cols_dw}\033[0m') #verde

                    #Cláusula OVERRIDING SYSTEM VALUE adicioando a para se a colunas na tabela de alteração estiver como GENERATED ALWAYS AS IDENTITY, há CONFLITO!
                    sql_dw = f'INSERT INTO public.{table} ({colnames_dw}) OVERRIDING SYSTEM VALUE VALUES ({placeholders_dw}) ON CONFLICT ({pk}) DO UPDATE SET {updates_dw}'
                    cur_sec.execute(sql_dw, vals_dw)

                conn_secundario.commit()
                print(f"APLICADO -> {table} {kind} {vals}")

    except Exception as e:
        print(f"Ocorreu um erro: {e}")
        conn_secundario.rollback()

    finally:
        cur_prim.close()
        conn_primario.close()
        cur_sec.close()
        conn_secundario.close()
        print("\nConexões fechadas.")


if __name__ == "__main__":
    main()