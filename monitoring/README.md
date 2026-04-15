# Observabilidade OneSid

Esta stack sobe:

- `loki`: persistencia e consulta de logs
- `grafana`: dashboard autenticado (opcional, somente para uso local)
- `processador`: `main.py`
- `monitor`: `monitor_rpa.py`
- `coletor`: `coletor_legalone.py`

## Subir observabilidade

```powershell
docker compose up -d db loki
```

## Grafana local opcional

```powershell
docker compose --profile local-grafana up -d grafana
```

## Subir os robos

```powershell
docker compose up -d --build processador monitor coletor
```

## Acessar dashboard

- Grafana remoto: valor de `GRAFANA_REMOTE_URL`
- Usuario: valor de `GRAFANA_REMOTE_USER`
- Senha: valor de `GRAFANA_REMOTE_PASSWORD`
- Loki publicado para a rede: valor de `LOKI_PUBLIC_URL`
- Dashboard principal: `OneSid Observabilidade`

## Configurar Grafana remoto

```powershell
.\monitoring\configure_remote_grafana.ps1
```

## Logs

Os servicos enviam logs para o Loki usando `LOKI_URL`.
Os arquivos locais continuam sendo gravados em `./logs`.
Os RPAs rodam no Docker com Chrome em display virtual `Xvfb`, sem depender de `headless`.
O Grafana remoto deve conseguir acessar a porta `3100` da maquina onde o Loki estiver rodando.

## Containers principais

- `onesid-processador`
- `onesid-monitor`
- `onesid-coletor`
- `onesid-grafana`
- `onesid-loki`
- `onesid-postgres`

## Dicas uteis

- Para ver logs em tempo real: `docker compose logs -f processador monitor coletor`
- Para parar so os robos: `docker compose stop processador monitor coletor`
- Para derrubar toda a stack: `docker compose down`
