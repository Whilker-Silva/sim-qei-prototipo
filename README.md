# SIM-QEI — Protótipo AV3 com LLM 

Protótipo funcional do **Sistema Inteligente de Monitoramento de Qualidade de Energia Industrial (SIM-QEI)** em Docker.

O sistema sobe com um único `docker compose` e inclui:

- banco PostgreSQL;
- backend FastAPI;
- simulador Edge em Python;
- frontend web em Nginx;
- agente decisório por regras;
- integração com LLM usando contexto real do banco;
- LLM local via Ollama por padrão;

## Como executar (em linux)

*Garanta que tenha o Docker já instaldo

```bash
unzip sim-qei-prototipo-llm.zip
cd sim-qei-prototipo
docker compose up --build -d
```

Na primeira execução, o Docker Compose também sobe o Ollama e tenta baixar o modelo configurado em `OLLAMA_MODEL`, por padrão `qwen2.5:1.5b`. Esse download pode demorar alguns minutos.

## Portas

- Frontend: http://localhost:8080
- Backend/API: http://localhost:8000
- Documentação da API: http://localhost:8000/docs
- Ollama: http://localhost:11434
- PostgreSQL: localhost:5432



## Como funciona o chat com LLM

Quando você pergunta algo no chat, o backend:

1. consulta as últimas leituras no banco;
2. calcula um resumo da janela recente;
3. recupera alarmes e decisões do agente;
4. monta um contexto estruturado em JSON;
5. envia a pergunta + contexto para o LLM;
6. força o LLM a responder apenas com base nesses dados.

A resposta deve trazer diagnóstico, evidências, recomendação e métrica de acompanhamento.


## Exemplo de perguntas para o chat

- Como está o fator de potência agora?
- Qual CDC está mais crítico?
- Existe problema de harmônicas?
- Gere um relatório executivo da qualidade de energia.
- Quais alarmes ocorreram nas últimas duas horas e quais ações recomenda?
- Qual é a evidência técnica para a recomendação do agente?

## Observação

O protótipo é acadêmico. Os dados são simulados e as recomendações não devem ser aplicadas em sistemas elétricos reais sem validação de engenharia, normas de segurança e análise de risco.

## Ajuste de desempenho do LLM local

Esta versão já vem com uma correção para servidores sem GPU:

- o backend envia um **contexto compacto** para o Ollama, em vez do JSON completo do banco;
- `num_ctx` foi reduzido para 2048;
- `num_predict` foi limitado para 220 tokens;
- o timeout padrão do backend foi aumentado para 180 segundos.

Se necessário pode-se ajustar esses parametros para rodar em maquinas mais potentes e reduzir o tempo de respota

Em servidores mais fracos, você pode usar um modelo menor:

```bash
docker exec -it simqei_ollama ollama pull qwen2.5:0.5b
```

Depois edite `.env`:

```env
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen2.5:0.5b
LLM_TIMEOUT_SECONDS=180
```

E recrie o backend:

```bash
docker compose up --build -d backend
```
