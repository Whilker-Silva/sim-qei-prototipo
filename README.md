# SIM-QEI - Protótipo funcional com dados simulados

Protótipo da arquitetura AV3 do SIM-QEI: simulação de dados elétricos, banco de dados, backend, agente de IA determinístico e frontend web.

## Como executar

```bash
unzip sim-qei-prototipo.zip
cd sim-qei-prototipo
docker compose up --build
```

Aguarde de 20 a 40 segundos na primeira execução. O simulador começa a enviar dados automaticamente.

## Portas

- Frontend: http://localhost:8080
- Backend/API: http://localhost:8000
- Documentação interativa da API: http://localhost:8000/docs
- PostgreSQL: localhost:5432

## O que roda no Docker Compose

- `db`: PostgreSQL 16, usado como banco de séries temporais simplificado.
- `backend`: FastAPI com endpoints REST, análise de qualidade de energia e agente decisório.
- `edge-simulator`: simulador de gateway Edge gerando eventos de qualidade de energia.
- `frontend`: dashboard web estático servido por Nginx.

## Eventos simulados

O simulador injeta automaticamente cenários como:

- operação normal;
- baixo fator de potência;
- THD elevado;
- sag de tensão;
- swell de tensão.

## Observações

Este protótipo usa PostgreSQL puro para aumentar compatibilidade em diferentes máquinas Linux. Em uma versão industrial, o banco de séries temporais pode ser substituído por TimescaleDB ou InfluxDB sem alterar o conceito da arquitetura.

O agente de IA deste protótipo é determinístico/simbólico para fins de demonstração acadêmica: ele recebe telemetria, detecta eventos, classifica severidade, recomenda ações, registra decisões e responde perguntas no painel. Em produção, essa camada pode ser complementada com LLM + RAG e modelos de detecção de anomalias.
