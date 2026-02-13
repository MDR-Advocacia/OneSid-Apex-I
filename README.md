Guia de Execução
O ecossistema OneSid Apex é composto por 3 robôs independentes que devem ser executados simultaneamente para garantir o fluxo completo de automação.

Opção 1: Execução Automática (Recomendado para Windows)
Para iniciar todos os robôs automaticamente em janelas separadas, basta executar o arquivo de lote na raiz do projeto:

Duplo clique em: iniciar_robo.bat

Opção 2: Execução Manual (Terminal)
Caso prefira rodar manualmente, abra 3 terminais na raiz do projeto (certifique-se de que o ambiente virtual .venv esteja ativado) e execute os seguintes comandos:

Terminal 1: O Coletor (Input)
Responsável por buscar novas demandas e alimentar a fila de processamento.

Comando: python coletor_legalone.py

Função: Conecta na API do Legal One, baixa tarefas (ex: "Solicitar Subsídio", "Obrigação de Fazer") e as salva no banco de dados onesid_db.

Frequência: Executa a cada 20 minutos.

Terminal 2: O Processador (Core)
O robô principal que realiza a interação com o portal web.

Comando: python main.py

Função: Consome tarefas "PENDENTES" do banco, realiza login no portal do BB/Tribunal, extrai o NPJ e coleta os subsídios necessários.

Frequência: Executa a cada 5 minutos.

Terminal 3: O Monitor (Output/Vigia)
Responsável por acompanhar a evolução dos processos pendentes.

Comando: python monitor_rpa.py

Função: Verifica processos marcados como "Em Monitoramento" (aqueles com itens "SOLICITADO"). Compara o estado atual com o anterior e, se detectar alterações, notifica a API TwoTask.

Frequência: Executa a cada 15 minutos.