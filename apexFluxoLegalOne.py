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
    1. Busca tarefas na API.
    2. Insere no Banco como PENDENTE.
    """
    if not CLIENT_ID or not CLIENT_SECRET:
        print("⚠️ Credenciais Legal One ausentes.")
        return

    print("📡 [APEX] Buscando tarefas no Legal One...")
    
    # 1. Busca Candidatas
    params = {
        "$filter": "(typeId eq 30 and subTypeId eq 1195) and statusId eq 1 and relationships/any(r: r/linkType eq 'Litigation')",
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
        print(f"❌ Erro API: {e}")
        return

    if not tasks:
        print("📭 Nenhuma tarefa encontrada na API.")
        return

    print(f"🔎 Encontradas {len(tasks)} tarefas na API. Verificando CNJs...")
    
    count_novas = 0
    for task in tasks:
        task_id = task.get('id')
        user_id = task.get('finishedBy')
        
        # Pega CNJ
        relationships = task.get('relationships', [])
        litigation_id = relationships[0].get('linkId') if relationships else None
        
        cnj = None
        if litigation_id:
            try:
                lit_url = f"{BASE_URL}/litigations/{litigation_id}?$select=identifierNumber"
                lit_data = make_api_request(lit_url, {})
                cnj = lit_data.get('identifierNumber')
            except: pass
        
        if cnj:
            # Tenta inserir na fila. Se retornar True, é nova.
            if database.inserir_tarefa_na_fila(task_id, cnj, user_id):
                print(f"   -> Nova tarefa na fila: {task_id} (CNJ: {cnj})")
                count_novas += 1
        
    print(f"✅ Abastecimento concluído. {count_novas} tarefas novas inseridas na fila.")

if __name__ == "__main__":
    buscar_e_abastecer_fila()