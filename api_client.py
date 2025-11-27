import requests
import json
import logging
import os
from dotenv import load_dotenv

load_dotenv()

# URL da API interna (TwoTask)
API_URL = os.getenv("API_NOTIFICACAO_URL", "http://twotask.mdr.local:8000/api/v1/tasks/batch-create")

def post_to_api(lista_processos):
    """
    Recebe uma lista de dicionários com os dados dos processos atualizados
    e envia para a API externa no formato padrão 'Onesid'.
    """
    if not lista_processos:
        logging.info("📭 Lista vazia. Nada a postar na API.")
        return False

    # Monta o payload final conforme seu exemplo
    payload = {
        "fonte": "Onesid",
        "processos": lista_processos
    }

    logging.info(f"📤 Postando {len(lista_processos)} atualizações para: {API_URL}")
    
    try:
        headers = {'Content-Type': 'application/json'}
        response = requests.post(API_URL, json=payload, headers=headers, timeout=30)
        
        if response.status_code in [200, 201]:
            logging.info("✅ POST enviado com sucesso!")
            return True
        else:
            logging.error(f"❌ Erro na API ({response.status_code}): {response.text}")
            return False

    except Exception as e:
        logging.error(f"❌ Falha de conexão ao postar na API: {e}")
        return False