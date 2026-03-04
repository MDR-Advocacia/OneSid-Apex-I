import subprocess
import os
import time
from flask import Flask, render_template, jsonify

app = Flask(__name__)

# Dicionário para gerenciar os processos em background
processos = {
    "coletor": None,
    "processador": None,
    "monitor": None
}

comandos = {
    "coletor": ["python", "coletor_legalone.py"],
    "processador": ["python", "main.py"],
    "monitor": ["python", "monitor_rpa.py"]
}

def iniciar_processo(nome):
    # Se já estiver rodando, mata o processo antes de reiniciar
    if processos[nome] and processos[nome].poll() is None:
        processos[nome].terminate()
        time.sleep(1) # Aguarda encerrar
        
    # Inicia o processo (os scripts já salvam log na pasta 'logs/')
    processos[nome] = subprocess.Popen(comandos[nome])

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status')
def status():
    status_dict = {}
    for nome, proc in processos.items():
        status_dict[nome] = "Rodando 🟢" if proc and proc.poll() is None else "Parado 🔴"
    return jsonify(status_dict)

@app.route('/api/restart/<nome>', methods=['POST'])
def restart(nome):
    if nome in processos:
        iniciar_processo(nome)
        return jsonify({"status": "success", "message": f"{nome} reiniciado com sucesso!"})
    return jsonify({"status": "error", "message": "Processo não encontrado."}), 404

@app.route('/api/logs/<nome>')
def get_logs(nome):
    arquivos = {
        "coletor": "logs/coletor.log",
        "processador": "logs/processador.log",
        "monitor": "logs/monitor.log"
    }
    caminho = arquivos.get(nome)
    if os.path.exists(caminho):
        with open(caminho, 'r', encoding='utf-8') as f:
            # Pega apenas as últimas 50 linhas para não pesar a tela
            linhas = f.readlines()[-50:]
            return jsonify({"logs": "".join(linhas)})
    return jsonify({"logs": f"Aguardando logs de {nome}..."})

if __name__ == '__main__':
    # Garante que a pasta de logs existe
    os.makedirs('logs', exist_ok=True)
    
    # Inicia os 3 robôs automaticamente ao subir o painel
    iniciar_processo("coletor")
    iniciar_processo("processador")
    iniciar_processo("monitor")
    
    # Roda o servidor web na porta 5000
    app.run(host='0.0.0.0', port=5000)