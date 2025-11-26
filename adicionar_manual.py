import sys
import os
import random
import logging
from dotenv import load_dotenv

# Configura logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# 1. Carrega variáveis de ambiente (apontando para a pasta RPA)
load_dotenv("RPA/.env")

# 2. Importa o banco de dados
# (Como este script está na raiz, consegue importar 'bd' direto)
try:
    import bd.database as database
except ImportError:
    # Fallback caso o python não ache o pacote
    sys.path.append(os.path.abspath(os.path.dirname(__file__)))
    import bd.database as database

def main():
    print("🛠️  INSERÇÃO MANUAL DE PROCESSO NA FILA")
    print("="*50)

    # Solicita o CNJ via terminal
    cnj_input = input("👉 Digite o número do processo (CNJ) para adicionar à fila: ").strip()

    if not cnj_input:
        print("❌ Erro: Número do processo não pode ser vazio.")
        return

    # Dados do Processo solicitado
    cnj_alvo = cnj_input
    
    # Geramos um ID alto e aleatório para não conflitar com IDs reais do Legal One
    id_ficticio = random.randint(90000000, 99999999)
    usuario_fake = "InsercaoManual"

    print(f"\n📝 Preparando inserção...")
    print(f"   -> CNJ: {cnj_alvo}")
    print(f"   -> ID Tarefa (Fake): {id_ficticio}")

    # Inicializa banco para garantir conexão
    database.inicializar_banco()

    # Insere na fila com status PENDENTE
    sucesso = database.inserir_tarefa_na_fila(id_ficticio, cnj_alvo, usuario_fake)

    if sucesso:
        print("\n✅ SUCESSO! Processo adicionado à fila.")
        print(f"   O robô vai pegar este processo na próxima execução.")
        print("\n👉 Para rodar agora, execute: python RPA/main.py")
    else:
        print("\n❌ Erro: Não foi possível inserir. Pode ser que o banco esteja offline ou o ID já exista.")

if __name__ == "__main__":
    main()