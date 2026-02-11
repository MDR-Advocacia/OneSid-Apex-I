@echo off
cd /d "C:\OneSid-Apex-I"

echo --- INICIANDO ECOSSISTEMA DE ROBOS ---

:: 1. Inicia o Coletor do Legal One (Nova Janela)
:: Ele ativa o ambiente virtual e roda o script
start "1 - COLETOR LEGAL ONE (20min)" cmd /k "call .venv\Scripts\activate & python coletor_legalone.py"

:: Pequena pausa para garantir que não sobrecarregue a inicialização
timeout /t 3 >nul

:: 2. Inicia o Processador do Portal (Nova Janela)
start "2 - PROCESSADOR PORTAL (5min)" cmd /k "call .venv\Scripts\activate & python main.py"

timeout /t 3 >nul

:: 3. Inicia o Monitor (Nova Janela)
start "3 - MONITOR RPA (15min)" cmd /k "call .venv\Scripts\activate & python monitor_rpa.py"

echo.
echo --- TODOS OS ROBOS FORAM INICIADOS EM JANELAS SEPARADAS ---
echo Pode fechar esta janela principal se quiser, as outras continuarao rodando.
pause