@echo off
cd /d "C:\projetos\RPA"
call .venv\Scripts\activate
echo --- INICIANDO ROBÔ DE INGESTÃO (MAIN) ---
python main.py
echo --- FIM DA EXECUÇÃO ---
timeout /t 10
exit