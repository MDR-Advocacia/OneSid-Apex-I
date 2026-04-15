# Observabilidade OneSid

Esta stack sobe:

- `loki`: persistencia e consulta de logs
- `grafana`: dashboard autenticado
- `processador`: `main.py`
- `monitor`: `monitor_rpa.py`
- `coletor`: `coletor_legalone.py`

## Subir observabilidade

```powershell
docker compose up -d db loki grafana
```

## Subir os robos

```powershell
docker compose up -d --build processador monitor coletor
```

## Acessar dashboard

- URL: `http://localhost:3000`
- Usuario: valor de `GRAFANA_ADMIN_USER`
- Senha: valor de `GRAFANA_ADMIN_PASSWORD`
- Dashboard principal: `OneSid Observabilidade`

## Logs

Os servicos enviam logs para o Loki usando `LOKI_URL`.
Os arquivos locais continuam sendo gravados em `./logs`.
Os RPAs rodam no Docker com Chrome em display virtual `Xvfb`, sem depender de `headless`.

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
