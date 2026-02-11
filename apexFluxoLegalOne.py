import requests
import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import sys

# Importa database
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import bd.database as database

load_dotenv()

CLIENT_ID = os.environ.get("LEGAL_ONE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("LEGAL_ONE_CLIENT_SECRET")
BASE_URL = os.environ.get("LEGAL_ONE_BASE_URL", "https://api.thomsonreuters.com/legalone/v1")
auth_token_cache = { "token": None, "expires_at": datetime.now(timezone.utc) }

def get_access_token():
    if auth_token_cache["token"] and datetime.now(timezone.utc) < auth_token_cache["expires_at"] - timedelta(seconds=60):
        return auth_token_cache["token"]
    
    auth_url = "https://api.thomsonreuters.com/legalone/oauth?grant_type=client_credentials"
    resp = requests.post(auth_url, auth=(CLIENT_ID, CLIENT_SECRET))
    resp.raise_for_status()
    data = resp.json()
    auth_token_cache["token"] = data["access_token"]
    auth_token_cache["expires_at"] = datetime.now(timezone.utc) + timedelta(seconds=int(data.get("expires_in", 1800)))
    return auth_token_cache["token"]

def make_api_request(url, params):
    token = get_access_token()
    return requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params).json()

def buscar_e_abastecer_fila():
    """
    Função Principal do Produtor:
    1. Define os tipos de tarefas desejados.
    2. Itera sobre cada tipo, buscando até 30 tarefas recentes para CADA um.
    3. Insere no Banco como PENDENTE.
    """
    if not CLIENT_ID or not CLIENT_SECRET:
        print("⚠️ Credenciais Legal One ausentes.")
        return

    print("📡 [APEX] Iniciando ciclo de busca por tipo de tarefa...")

    # Lista de configurações de tarefas para iterar
    tipos_tarefa = [
        {"typeId": 30, "subTypeId": 1195}, # Teste - ONESID
        {"typeId": 28, "subTypeId": 961}, # Quesito - BB Autor
        {"typeId": 28, "subTypeId": 936}, # Apresentar  Planilha - BB Autor
        {"typeId": 26, "subTypeId": 1131}, # Solicitar  Monitoramento - ONESID
        {"typeId": 15, "subTypeId": 856}, # Solicitar Subsídio / Tarefa - Luna 12/01
        {"typeId": 13, "subTypeId": 843}, # Solicitar Subsídio / Tarefa - Luna 12/01
        {"typeId": 28, "subTypeId": 984}, # Solicitar Preposto
        {"typeId": 20, "subTypeId": 975}, # Obrigação de Fazer Complexa - Jonilson 28/01
        {"typeId": 20, "subTypeId": 1139} # Obrigação de Fazer Simples  - Jonilson 28/01

    ]

    count_novas_total = 0

    for config in tipos_tarefa:
        t_id = config['typeId']
        s_id = config['subTypeId']

        print(f"\n🔎 Buscando TOP 30 para: TypeId {t_id} / SubTypeId {s_id}")
        
        params = {
            "$filter": f"(typeId eq {t_id} and subTypeId eq {s_id}) and statusId eq 1 and relationships/any(r: r/linkType eq 'Litigation')",
            "$expand": "relationships($select=id,linkId)",
            "$select": "id,finishedBy,relationships",
            "$top": 30,
            "$orderby": "id desc"
        }
        
        try:
            url = f"{BASE_URL}/tasks"
            data = make_api_request(url, params)
            tasks = data.get("value", [])
        except Exception as e:
            print(f"❌ Erro API ao buscar tipo {t_id}/{s_id}: {e}")
            continue

        if not tasks:
            print(f"   📭 Nenhuma tarefa encontrada para este tipo.")
            continue

        print(f"   📋 Processando {len(tasks)} tarefas encontradas...")
        
        for task in tasks:
            task_id = task.get('id')
            user_id = task.get('finishedBy')
            
            # Pega CNJ
            relationships = task.get('relationships', [])
            litigation_id = relationships[0].get('linkId') if relationships else None
            
            cnj = None
            if litigation_id:
                try:
                    # Aqui, idealmente, poderia haver cache para não consultar o mesmo litigation repetidamente
                    lit_url = f"{BASE_URL}/litigations/{litigation_id}?$select=identifierNumber"
                    lit_data = make_api_request(lit_url, {})
                    cnj = lit_data.get('identifierNumber')
                except: pass
            
            if cnj:
                # Tenta inserir na fila. Se retornar True, é nova.
                if database.inserir_tarefa_na_fila(task_id, cnj, user_id):
                    print(f"   -> Nova tarefa na fila: {task_id} (CNJ: {cnj})")
                    count_novas_total += 1
        
    print(f"\n✅ Ciclo concluído. Total de tarefas novas inseridas: {count_novas_total}")

if __name__ == "__main__":
    buscar_e_abastecer_fila()