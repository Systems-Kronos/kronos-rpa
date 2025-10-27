# 🤖 RPA 
## Funções
### *`verificando_existencia_tabela`*
Verifica se a tabla existe dentro do banco que for passado como parâmetro, a partir do `information_schema.tables` (apresenta metadados sobre tabelas e visualizações de um banco de dados).
      - Parâmetro: cursor banco (primário | secundário), schema (public | staging), tabela (dinâmico)
      - Retorno: true ou false 
### *`identifica_chave_primaria`*
Fluxo de funcionamento: acessa a tabela que chaves primárias (`pg_index`) e a tabela que armazena todas as colunas do banco (`pg_attribute`) => `JOIN` irá resultar em uma consulta retornando apenas aos índices (`pg_index.indrelid = 'nome_da_tabela'::regclass`) da tabela passada, podendo selecionar apenas chave primária da mesma (`pg_index.indisprimary`). 
    - Explicando partes:
        - `pg_attribute | attrelid `: contém todas as colunas de todas as tabelas + colunas do sistema.
            Supondo que temos uma tabela "colaboradores" com os campos: `id_colaborador`, `nome`, `idade`; é esperado que, ao executarmos `SELECT * FROM pg_attribute`, tenhamos as informações de gerais das colunas da tabela e do sistema, juntamente com o seu id (coluna padrão: `attrelid`). Exemplo:
            ![alt text](image.png)
        - `indrelid`:iremos fazer um *de/para* com ela para descobrirmos as colunas que são primaidry_key nas tabelas. 
            Ou seja, no exemplo acima, o único id de coluna que irá aparecer será o do `id_colaborador`.
            Coluna que se relacionará com a `attrelid`:
            ![alt text](image-1.png)
        - Como funciona esse *de/para*? Pelo JOIN! 
          ![alt text](image-2.png)
    - Parâmetro: cursor banco (primário | secundário), nome da tabela ( `f'public.{table_name}` | `f'staging.{table_name}'`), tabela (dinâmico)
      - Retorno: nome da coluna id da tabela passada
### *`main`*
Inclui as conexões PostgreSQL; criação do slot_wal2_json (para armazenar as alterações no banco -> INSERT, UPDATE e DELETE); verifica as alterações lendo o slot_wal2_json (retorno é um dicionário) -> evt pegando o nome da tabela e o tipo da alteração.

