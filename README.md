# SIM-QEI — Protótipo AV3 com LLM contextualizado

Protótipo funcional do **Sistema Inteligente de Monitoramento de Qualidade de Energia Industrial (SIM-QEI)** em Docker.

O sistema sobe com um único `docker compose` e inclui:

- banco PostgreSQL;
- backend FastAPI;
- simulador Edge em Python;
- frontend web em Nginx;
- agente decisório por regras;
- integração com LLM usando contexto real do banco;
- LLM local via Ollama por padrão;
- suporte opcional à OpenAI API.

## Como executar

```bash
unzip sim-qei-prototipo-llm.zip
cd sim-qei-prototipo
docker compose up --build
```

Na primeira execução, o Docker Compose também sobe o Ollama e tenta baixar o modelo configurado em `OLLAMA_MODEL`, por padrão `qwen2.5:1.5b`. Esse download pode demorar alguns minutos.

## Portas

- Frontend: http://localhost:8080
- Backend/API: http://localhost:8000
- Documentação da API: http://localhost:8000/docs
- Ollama: http://localhost:11434
- PostgreSQL: localhost:5432

Em outro computador da mesma rede, troque `localhost` pelo IP do servidor. Exemplo:

```text
http://192.168.1.2:8080
```

## Como funciona o chat com LLM

Quando você pergunta algo no chat, o backend:

1. consulta as últimas leituras no banco;
2. calcula um resumo da janela recente;
3. recupera alarmes e decisões do agente;
4. monta um contexto estruturado em JSON;
5. envia a pergunta + contexto para o LLM;
6. força o LLM a responder apenas com base nesses dados.

A resposta deve trazer diagnóstico, evidências, recomendação e métrica de acompanhamento.

## Usar LLM local com Ollama

É o modo padrão:

```env
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen2.5:1.5b
```

Para trocar o modelo, crie um arquivo `.env` a partir do exemplo:

```bash
cp .env.example .env
nano .env
```

Exemplo usando um modelo diferente:

```env
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.2:1b
```

Depois rode:

```bash
docker compose up --build
```

## Usar OpenAI API em vez do Ollama

Crie o arquivo `.env`:

```bash
cp .env.example .env
nano .env
```

Configure:

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=sua_chave_aqui
OPENAI_MODEL=gpt-5.4-mini
```

Depois rode novamente:

```bash
docker compose up --build -d
```

## Testar status do LLM

```bash
curl http://localhost:8000/api/llm/status
```

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

- o front-end não dispara mais pergunta automática ao abrir a tela;
- o backend envia um **contexto compacto** para o Ollama, em vez do JSON completo do banco;
- `num_ctx` foi reduzido para 2048;
- `num_predict` foi limitado para 220 tokens;
- o timeout padrão do backend foi aumentado para 180 segundos.

Se quiser verificar se o contexto realmente ficou menor, rode:

```bash
docker logs -f --tail=80 simqei_ollama
```

Ao fazer uma pergunta pelo chat, procure no log do Ollama por algo como `task.n_tokens`. O esperado é ficar muito abaixo dos ~7700 tokens da versão anterior.

Teste direto pelo backend:

```bash
time curl -s -X POST http://localhost:8000/api/agent/chat \
  -H "Content-Type: application/json" \
  -d '{"question":"Qual CDC está mais crítico agora?"}'
```

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
