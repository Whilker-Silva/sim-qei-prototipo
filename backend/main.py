import json
import math
import os
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import httpx
import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://simqei:simqei@localhost:5432/simqei")
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")

# Provedores possíveis:
# - ollama: LLM local via container Ollama
# - openai: API OpenAI usando OPENAI_API_KEY
# - mock/deterministic: resposta por regras, sem LLM
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini").strip()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b").strip()
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "45"))

app = FastAPI(
    title="SIM-QEI API",
    description="Backend do protótipo funcional do SIM-QEI com simulação de qualidade de energia, agente decisório e integração opcional com LLM.",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if CORS_ORIGINS == "*" else [origin.strip() for origin in CORS_ORIGINS.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def row_to_dict(row):
    if row is None:
        return None
    data = dict(row)
    for key, value in list(data.items()):
        if isinstance(value, datetime):
            data[key] = value.isoformat()
    return data


class TelemetryIn(BaseModel):
    timestamp: Optional[datetime] = None
    plant: str = Field(default="Planta Simulada")
    cdc: str = Field(default="CDC-01")
    meter_id: str = Field(default="MED-01")
    voltage_a: float
    voltage_b: float
    voltage_c: float
    current_a: float
    current_b: float
    current_c: float
    active_power_kw: float
    reactive_power_kvar: float
    apparent_power_kva: float
    power_factor: float
    thd_voltage: float
    thd_current: float
    frequency: float
    event_type: str = Field(default="normal")


class ChatIn(BaseModel):
    question: str


def init_db():
    ddl = """
    CREATE TABLE IF NOT EXISTS telemetry (
        id SERIAL PRIMARY KEY,
        ts TIMESTAMPTZ NOT NULL,
        plant TEXT NOT NULL,
        cdc TEXT NOT NULL,
        meter_id TEXT NOT NULL,
        voltage_a DOUBLE PRECISION NOT NULL,
        voltage_b DOUBLE PRECISION NOT NULL,
        voltage_c DOUBLE PRECISION NOT NULL,
        current_a DOUBLE PRECISION NOT NULL,
        current_b DOUBLE PRECISION NOT NULL,
        current_c DOUBLE PRECISION NOT NULL,
        active_power_kw DOUBLE PRECISION NOT NULL,
        reactive_power_kvar DOUBLE PRECISION NOT NULL,
        apparent_power_kva DOUBLE PRECISION NOT NULL,
        power_factor DOUBLE PRECISION NOT NULL,
        thd_voltage DOUBLE PRECISION NOT NULL,
        thd_current DOUBLE PRECISION NOT NULL,
        frequency DOUBLE PRECISION NOT NULL,
        event_type TEXT NOT NULL DEFAULT 'normal'
    );

    CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON telemetry(ts DESC);
    CREATE INDEX IF NOT EXISTS idx_telemetry_cdc_ts ON telemetry(cdc, ts DESC);

    CREATE TABLE IF NOT EXISTS alarms (
        id SERIAL PRIMARY KEY,
        ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        plant TEXT NOT NULL,
        cdc TEXT NOT NULL,
        meter_id TEXT NOT NULL,
        severity TEXT NOT NULL,
        type TEXT NOT NULL,
        description TEXT NOT NULL,
        recommendation TEXT NOT NULL,
        value DOUBLE PRECISION,
        status TEXT NOT NULL DEFAULT 'open'
    );

    CREATE INDEX IF NOT EXISTS idx_alarms_ts ON alarms(ts DESC);
    CREATE INDEX IF NOT EXISTS idx_alarms_status ON alarms(status);

    CREATE TABLE IF NOT EXISTS agent_decisions (
        id SERIAL PRIMARY KEY,
        ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        alarm_id INTEGER REFERENCES alarms(id) ON DELETE SET NULL,
        cdc TEXT NOT NULL,
        input_summary TEXT NOT NULL,
        decision_process TEXT NOT NULL,
        action TEXT NOT NULL,
        performance_metric TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS chat_logs (
        id SERIAL PRIMARY KEY,
        ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        provider TEXT NOT NULL,
        model TEXT,
        question TEXT NOT NULL,
        answer TEXT NOT NULL,
        context_summary JSONB
    );

    CREATE INDEX IF NOT EXISTS idx_chat_logs_ts ON chat_logs(ts DESC);
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()


@app.on_event("startup")
def startup():
    last_error = None
    for _ in range(30):
        try:
            init_db()
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(2)
    raise RuntimeError(f"Não foi possível conectar/inicializar o banco: {last_error}")


def classify_events(t: TelemetryIn) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    voltages = [t.voltage_a, t.voltage_b, t.voltage_c]
    min_v = min(voltages)
    max_v = max(voltages)

    if t.power_factor < 0.92:
        severity = "critical" if t.power_factor < 0.86 else "high" if t.power_factor < 0.90 else "medium"
        events.append({
            "type": "baixo_fator_potencia",
            "severity": severity,
            "value": t.power_factor,
            "description": f"Fator de potência abaixo do limite operacional: FP={t.power_factor:.3f} no {t.cdc}.",
            "recommendation": "Verificar banco de capacitores, estágios de correção automática e aumento recente de carga indutiva.",
            "metric": "FP médio pós-ação deve retornar para valor >= 0,92 em até 30 minutos.",
        })

    if t.thd_voltage > 5.0:
        severity = "critical" if t.thd_voltage > 10.0 else "high" if t.thd_voltage > 8.0 else "medium"
        events.append({
            "type": "thd_tensao_elevado",
            "severity": severity,
            "value": t.thd_voltage,
            "description": f"Distorção harmônica total de tensão elevada: THDv={t.thd_voltage:.2f}% no {t.cdc}.",
            "recommendation": "Investigar cargas não lineares, inversores de frequência e necessidade de filtros harmônicos.",
            "metric": "THDv deve retornar para faixa <= 5% ou limite interno definido pela engenharia.",
        })

    if min_v < 198.0:
        severity = "critical" if min_v < 185.0 else "high"
        events.append({
            "type": "sag_tensao",
            "severity": severity,
            "value": min_v,
            "description": f"Afundamento de tensão detectado: menor fase={min_v:.1f} V no {t.cdc}.",
            "recommendation": "Correlacionar com partida de motores, falha de alimentação, manobra de carga ou evento da concessionária.",
            "metric": "Tempo de detecção inferior a 5 s e registro completo do evento para análise posterior.",
        })

    if max_v > 242.0:
        severity = "critical" if max_v > 255.0 else "high"
        events.append({
            "type": "swell_tensao",
            "severity": severity,
            "value": max_v,
            "description": f"Elevação de tensão detectada: maior fase={max_v:.1f} V no {t.cdc}.",
            "recommendation": "Verificar regulação de tensão, comutação de banco de capacitores e condições da alimentação.",
            "metric": "Tensão deve retornar para faixa nominal e evento deve ser correlacionado com operação da planta.",
        })

    if t.frequency < 59.5 or t.frequency > 60.5:
        severity = "high"
        events.append({
            "type": "frequencia_fora_faixa",
            "severity": severity,
            "value": t.frequency,
            "description": f"Frequência fora da faixa esperada: f={t.frequency:.2f} Hz no {t.cdc}.",
            "recommendation": "Verificar fonte de alimentação, geradores, transferência de carga e estabilidade da rede.",
            "metric": "Frequência deve permanecer entre 59,5 Hz e 60,5 Hz para operação estável.",
        })

    return events


def create_alarm_if_needed(conn, t: TelemetryIn, event: Dict[str, Any]) -> Optional[int]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id FROM alarms
            WHERE cdc = %s
              AND type = %s
              AND ts > NOW() - INTERVAL '60 seconds'
            ORDER BY ts DESC
            LIMIT 1;
            """,
            (t.cdc, event["type"]),
        )
        existing = cur.fetchone()
        if existing:
            return None

        cur.execute(
            """
            INSERT INTO alarms
                (plant, cdc, meter_id, severity, type, description, recommendation, value, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'open')
            RETURNING id;
            """,
            (
                t.plant,
                t.cdc,
                t.meter_id,
                event["severity"],
                event["type"],
                event["description"],
                event["recommendation"],
                event["value"],
            ),
        )
        alarm_id = cur.fetchone()["id"]

        decision_process = (
            "Entrada: telemetria elétrica em tempo real. "
            f"Regra acionada: {event['type']} com valor {event['value']:.3f}. "
            f"Severidade classificada como {event['severity']} por limites operacionais."
        )
        action = f"Registrar alarme, notificar manutenção elétrica e recomendar: {event['recommendation']}"
        cur.execute(
            """
            INSERT INTO agent_decisions
                (alarm_id, cdc, input_summary, decision_process, action, performance_metric)
            VALUES (%s, %s, %s, %s, %s, %s);
            """,
            (
                alarm_id,
                t.cdc,
                event["description"],
                decision_process,
                action,
                event["metric"],
            ),
        )
        return alarm_id


@app.get("/api/health")
def health():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
        return {
            "status": "ok",
            "service": "simqei_backend",
            "llm_provider": LLM_PROVIDER,
            "ollama_model": OLLAMA_MODEL,
            "openai_model": OPENAI_MODEL if bool(OPENAI_API_KEY) else None,
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/api/telemetry")
def ingest_telemetry(t: TelemetryIn):
    ts = t.timestamp or datetime.now(timezone.utc)
    events = classify_events(t)
    created_alarms = []

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO telemetry
                    (ts, plant, cdc, meter_id, voltage_a, voltage_b, voltage_c,
                     current_a, current_b, current_c, active_power_kw, reactive_power_kvar,
                     apparent_power_kva, power_factor, thd_voltage, thd_current, frequency, event_type)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (
                    ts,
                    t.plant,
                    t.cdc,
                    t.meter_id,
                    t.voltage_a,
                    t.voltage_b,
                    t.voltage_c,
                    t.current_a,
                    t.current_b,
                    t.current_c,
                    t.active_power_kw,
                    t.reactive_power_kvar,
                    t.apparent_power_kva,
                    t.power_factor,
                    t.thd_voltage,
                    t.thd_current,
                    t.frequency,
                    t.event_type,
                ),
            )
            telemetry_id = cur.fetchone()["id"]

        for event in events:
            alarm_id = create_alarm_if_needed(conn, t, event)
            if alarm_id is not None:
                created_alarms.append({"id": alarm_id, **event})

        conn.commit()

    return {"telemetry_id": telemetry_id, "events_detected": events, "alarms_created": created_alarms}


@app.get("/api/latest")
def latest():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (cdc) *
                FROM telemetry
                ORDER BY cdc, ts DESC;
                """
            )
            rows = cur.fetchall()
    return {"items": [row_to_dict(r) for r in rows]}


@app.get("/api/telemetry")
def telemetry(limit: int = Query(120, ge=1, le=2000), cdc: Optional[str] = None):
    where = "WHERE cdc = %s" if cdc else ""
    params = [cdc, limit] if cdc else [limit]
    sql = f"""
        SELECT * FROM telemetry
        {where}
        ORDER BY ts DESC
        LIMIT %s;
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = list(cur.fetchall())
    rows.reverse()
    return {"items": [row_to_dict(r) for r in rows]}


@app.get("/api/alarms")
def alarms(limit: int = Query(50, ge=1, le=500)):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM alarms
                ORDER BY ts DESC
                LIMIT %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()
    return {"items": [row_to_dict(r) for r in rows]}


@app.get("/api/agent/decisions")
def decisions(limit: int = Query(50, ge=1, le=500)):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM agent_decisions
                ORDER BY ts DESC
                LIMIT %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()
    return {"items": [row_to_dict(r) for r in rows]}


def safe_float(value, default=0.0):
    try:
        if value is None or math.isnan(value):
            return default
        return float(value)
    except Exception:  # noqa: BLE001
        return default


@app.get("/api/kpis")
def kpis():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS samples,
                    COUNT(DISTINCT cdc) AS monitored_cdcs,
                    AVG(power_factor) AS avg_pf,
                    AVG(thd_voltage) AS avg_thd_voltage,
                    AVG(active_power_kw) AS avg_power_kw,
                    MAX(ts) AS last_sample
                FROM telemetry
                WHERE ts > NOW() - INTERVAL '10 minutes';
                """
            )
            metrics = row_to_dict(cur.fetchone())
            cur.execute(
                """
                SELECT COUNT(*) AS open_alarms
                FROM alarms
                WHERE status = 'open'
                  AND ts > NOW() - INTERVAL '1 hour';
                """
            )
            alarm_metrics = row_to_dict(cur.fetchone())
            cur.execute(
                """
                SELECT type, COUNT(*) AS qty
                FROM alarms
                WHERE ts > NOW() - INTERVAL '1 hour'
                GROUP BY type
                ORDER BY qty DESC;
                """
            )
            by_type = [row_to_dict(r) for r in cur.fetchall()]

    avg_power_kw = safe_float(metrics.get("avg_power_kw"))
    samples = int(metrics.get("samples") or 0)
    estimated_energy_kwh = avg_power_kw * (10 / 60) if samples > 0 else 0
    estimated_cost_brl = estimated_energy_kwh * 0.85

    return {
        "samples_10min": samples,
        "monitored_cdcs": int(metrics.get("monitored_cdcs") or 0),
        "avg_power_factor": safe_float(metrics.get("avg_pf")),
        "avg_thd_voltage": safe_float(metrics.get("avg_thd_voltage")),
        "avg_power_kw": avg_power_kw,
        "open_alarms_1h": int(alarm_metrics.get("open_alarms") or 0),
        "alarms_by_type_1h": by_type,
        "estimated_energy_kwh_10min": estimated_energy_kwh,
        "estimated_cost_brl_10min": estimated_cost_brl,
        "last_sample": metrics.get("last_sample"),
    }


def summarize_cdc_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_cdc: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_cdc.setdefault(row["cdc"], []).append(row)

    stats = {}
    for cdc, items in by_cdc.items():
        voltages = [((x["voltage_a"] + x["voltage_b"] + x["voltage_c"]) / 3) for x in items]
        stats[cdc] = {
            "samples": len(items),
            "avg_power_kw": round(sum(x["active_power_kw"] for x in items) / len(items), 2),
            "avg_power_factor": round(sum(x["power_factor"] for x in items) / len(items), 4),
            "min_power_factor": round(min(x["power_factor"] for x in items), 4),
            "avg_thd_voltage_pct": round(sum(x["thd_voltage"] for x in items) / len(items), 3),
            "max_thd_voltage_pct": round(max(x["thd_voltage"] for x in items), 3),
            "min_voltage_v": round(min(voltages), 2),
            "max_voltage_v": round(max(voltages), 2),
            "latest_event_type": items[-1]["event_type"],
        }
    return stats


def collect_agent_context() -> Dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (cdc) *
                FROM telemetry
                ORDER BY cdc, ts DESC;
                """
            )
            latest_rows = [row_to_dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT * FROM telemetry
                WHERE ts > NOW() - INTERVAL '15 minutes'
                ORDER BY ts ASC
                LIMIT 900;
                """
            )
            window_rows = [row_to_dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT * FROM alarms
                WHERE ts > NOW() - INTERVAL '2 hours'
                ORDER BY ts DESC
                LIMIT 20;
                """
            )
            recent_alarms = [row_to_dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT * FROM agent_decisions
                ORDER BY ts DESC
                LIMIT 10;
                """
            )
            recent_decisions = [row_to_dict(r) for r in cur.fetchall()]

    if not latest_rows:
        return {
            "has_data": False,
            "message": "Ainda não há dados no banco.",
            "latest_by_cdc": [],
            "window_stats_by_cdc": {},
            "recent_alarms": [],
            "recent_decisions": [],
        }

    avg_pf = round(sum(r["power_factor"] for r in latest_rows) / len(latest_rows), 4)
    avg_thd = round(sum(r["thd_voltage"] for r in latest_rows) / len(latest_rows), 3)
    total_kw = round(sum(r["active_power_kw"] for r in latest_rows), 2)
    worst_pf = min(latest_rows, key=lambda r: r["power_factor"])
    worst_thd = max(latest_rows, key=lambda r: r["thd_voltage"])

    return {
        "has_data": True,
        "timestamp_context_generated": datetime.now(timezone.utc).isoformat(),
        "system": "SIM-QEI protótipo com dados simulados de qualidade de energia industrial",
        "rules": {
            "power_factor_limit": 0.92,
            "thd_voltage_limit_pct": 5.0,
            "sag_voltage_threshold_v": 198.0,
            "swell_voltage_threshold_v": 242.0,
            "frequency_range_hz": [59.5, 60.5],
        },
        "global_snapshot": {
            "monitored_cdcs": len(latest_rows),
            "total_active_power_kw_now": total_kw,
            "avg_power_factor_now": avg_pf,
            "avg_thd_voltage_pct_now": avg_thd,
            "worst_power_factor_cdc": {"cdc": worst_pf["cdc"], "power_factor": worst_pf["power_factor"]},
            "worst_thd_voltage_cdc": {"cdc": worst_thd["cdc"], "thd_voltage_pct": worst_thd["thd_voltage"]},
            "recent_alarm_count_2h": len(recent_alarms),
        },
        "latest_by_cdc": latest_rows,
        "window_stats_by_cdc": summarize_cdc_stats(window_rows) if window_rows else {},
        "recent_alarms": recent_alarms,
        "recent_decisions": recent_decisions,
    }


def build_deterministic_answer(question: str, context: Dict[str, Any]) -> Dict[str, Any]:
    if not context.get("has_data"):
        return {"answer": "Ainda não há dados no banco. Aguarde o simulador Edge enviar as primeiras leituras.", "provider": "deterministic"}

    q = question.lower().strip()
    latest_rows = context["latest_by_cdc"]
    recent_alarms = context["recent_alarms"]
    snapshot = context["global_snapshot"]
    avg_pf = snapshot["avg_power_factor_now"]
    avg_thd = snapshot["avg_thd_voltage_pct_now"]
    total_kw = snapshot["total_active_power_kw_now"]
    worst_pf = min(latest_rows, key=lambda r: r["power_factor"])
    worst_thd = max(latest_rows, key=lambda r: r["thd_voltage"])

    if "fator" in q or "fp" in q or "reativ" in q:
        answer = (
            f"O fator de potência médio atual dos CDCs monitorados é {avg_pf:.3f}. "
            f"O ponto mais crítico é {worst_pf['cdc']}, com FP={worst_pf['power_factor']:.3f}. "
        )
        if worst_pf["power_factor"] < 0.92:
            answer += (
                "O agente classifica o cenário como atenção operacional, pois está abaixo de 0,92. "
                "A recomendação é verificar banco de capacitores, estágios de correção e aumento de carga indutiva."
            )
        else:
            answer += "Todos os pontos estão acima do limite operacional de 0,92 no momento."
    elif "harm" in q or "thd" in q or "distor" in q:
        answer = (
            f"A THD de tensão média atual é {avg_thd:.2f}%. "
            f"O CDC com maior distorção é {worst_thd['cdc']}, com THDv={worst_thd['thd_voltage']:.2f}%. "
        )
        if worst_thd["thd_voltage"] > 5:
            answer += "O agente recomenda investigar cargas não lineares, inversores de frequência e necessidade de filtragem harmônica."
        else:
            answer += "A distorção está dentro da faixa configurada para o protótipo."
    elif "tens" in q or "sag" in q or "swell" in q:
        parts = []
        for r in latest_rows:
            vavg = (r["voltage_a"] + r["voltage_b"] + r["voltage_c"]) / 3
            parts.append(f"{r['cdc']}: {vavg:.1f} V")
        answer = "Tensão média atual por CDC: " + "; ".join(parts) + ". "
        answer += "O agente procura sag abaixo de 198 V e swell acima de 242 V neste protótipo."
    elif "relat" in q or "resumo" in q or "executivo" in q:
        answer = (
            "Relatório executivo SIM-QEI: "
            f"potência ativa total atual de {total_kw:.1f} kW, FP médio {avg_pf:.3f}, THDv média {avg_thd:.2f}% "
            f"e {len(recent_alarms)} alarmes registrados nas últimas 2 horas. "
        )
        if recent_alarms:
            critical = recent_alarms[0]
            answer += (
                f"Evento mais recente: {critical['type']} no {critical['cdc']} com severidade {critical['severity']}. "
                f"Recomendação: {critical['recommendation']}"
            )
        else:
            answer += "Não há alarmes recentes; operação simulada está estável no momento."
    else:
        answer = (
            "Sou o agente autônomo do SIM-QEI. Estou monitorando os CDCs simulados, classificando anomalias e registrando decisões. "
            f"No momento há {len(latest_rows)} CDCs monitorados, potência total de {total_kw:.1f} kW, "
            f"FP médio de {avg_pf:.3f} e THDv média de {avg_thd:.2f}%. "
            "Você pode perguntar sobre fator de potência, harmônicas, tensão ou pedir um relatório executivo."
        )

    return {"answer": answer, "provider": "deterministic"}


def make_llm_messages(question: str, context: Dict[str, Any]) -> List[Dict[str, str]]:
    system = (
        "Você é o Agente Autônomo do SIM-QEI, um sistema de qualidade de energia industrial. "
        "Responda em português do Brasil, com linguagem técnica mas objetiva. "
        "Use exclusivamente os dados fornecidos no CONTEXTO. Não invente medições, alarmes, horários ou causas. "
        "Quando não houver dados suficientes, diga claramente que não há evidência suficiente. "
        "Sempre que útil, cite valores numéricos dos CDCs, limite de FP 0,92, limite de THDv 5%, sag <198 V e swell >242 V. "
        "Separe a resposta em: Diagnóstico, Evidências, Recomendação e Métrica de acompanhamento. "
        "Lembre que os dados são simulados para um protótipo acadêmico."
    )
    context_json = json.dumps(context, ensure_ascii=False, default=str)
    user = f"CONTEXTO OPERACIONAL DO BANCO DE DADOS:\n{context_json}\n\nPERGUNTA DO USUÁRIO:\n{question}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def call_ollama(question: str, context: Dict[str, Any]) -> Dict[str, Any]:
    messages = make_llm_messages(question, context)
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.15, "num_ctx": 2048, "num_predict": 350},
    }
    with httpx.Client(timeout=LLM_TIMEOUT_SECONDS) as client:
        res = client.post(f"{OLLAMA_URL}/api/chat", json=payload)
        res.raise_for_status()
        data = res.json()
    answer = data.get("message", {}).get("content") or "O LLM local não retornou conteúdo."
    return {"answer": answer.strip(), "provider": "ollama", "model": OLLAMA_MODEL}


def call_openai(question: str, context: Dict[str, Any]) -> Dict[str, Any]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY não configurada.")
    messages = make_llm_messages(question, context)
    input_payload = [
        {"role": "system", "content": [{"type": "input_text", "text": messages[0]["content"]}]},
        {"role": "user", "content": [{"type": "input_text", "text": messages[1]["content"]}]},
    ]
    payload = {
        "model": OPENAI_MODEL,
        "input": input_payload,
        "temperature": 0.15,
        "max_output_tokens": 900,
    }
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    with httpx.Client(timeout=LLM_TIMEOUT_SECONDS) as client:
        res = client.post("https://api.openai.com/v1/responses", headers=headers, json=payload)
        res.raise_for_status()
        data = res.json()

    # A Responses API normalmente retorna output_text em SDKs; via HTTP o texto pode vir em output[].content[].text.
    answer = data.get("output_text")
    if not answer:
        chunks = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                if "text" in content:
                    chunks.append(content["text"])
        answer = "\n".join(chunks).strip()
    return {"answer": (answer or "A API OpenAI não retornou conteúdo.").strip(), "provider": "openai", "model": OPENAI_MODEL}


def log_chat(provider: str, model: Optional[str], question: str, answer: str, context: Dict[str, Any]) -> None:
    summary = {
        "global_snapshot": context.get("global_snapshot"),
        "recent_alarm_count": len(context.get("recent_alarms", [])),
        "cdcs": list((context.get("window_stats_by_cdc") or {}).keys()),
    }
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_logs (provider, model, question, answer, context_summary)
                    VALUES (%s, %s, %s, %s, %s::jsonb);
                    """,
                    (provider, model, question, answer, json.dumps(summary, ensure_ascii=False)),
                )
            conn.commit()
    except Exception:
        # Não derruba a resposta se o log falhar.
        return


def build_agent_answer(question: str) -> Dict[str, Any]:
    context = collect_agent_context()
    if not context.get("has_data"):
        deterministic = build_deterministic_answer(question, context)
        return {**deterministic, "model": None, "sources": context}

    provider_used = "deterministic"
    model_used = None
    fallback_reason = None

    try:
        if LLM_PROVIDER == "openai":
            llm_result = call_openai(question, context)
        elif LLM_PROVIDER == "ollama":
            llm_result = call_ollama(question, context)
        elif LLM_PROVIDER in {"mock", "deterministic", "none", "off"}:
            llm_result = build_deterministic_answer(question, context)
        else:
            raise RuntimeError(f"LLM_PROVIDER inválido: {LLM_PROVIDER}")

        answer = llm_result["answer"]
        provider_used = llm_result.get("provider", provider_used)
        model_used = llm_result.get("model")
    except Exception as exc:  # noqa: BLE001
        fallback = build_deterministic_answer(question, context)
        answer = (
            f"[Fallback determinístico usado porque o LLM não respondeu: {exc}]\n\n"
            f"{fallback['answer']}"
        )
        fallback_reason = str(exc)

    log_chat(provider_used, model_used, question, answer, context)
    return {
        "answer": answer,
        "provider": provider_used,
        "model": model_used,
        "fallback_reason": fallback_reason,
        "sources": {
            "global_snapshot": context.get("global_snapshot"),
            "latest_by_cdc": context.get("latest_by_cdc", []),
            "window_stats_by_cdc": context.get("window_stats_by_cdc", {}),
            "recent_alarms": context.get("recent_alarms", [])[:5],
            "recent_decisions": context.get("recent_decisions", [])[:3],
        },
    }


@app.post("/api/agent/chat")
def agent_chat(payload: ChatIn):
    return build_agent_answer(payload.question)


@app.get("/api/llm/status")
def llm_status():
    status = {
        "configured_provider": LLM_PROVIDER,
        "ollama_url": OLLAMA_URL,
        "ollama_model": OLLAMA_MODEL,
        "openai_model": OPENAI_MODEL,
        "openai_key_configured": bool(OPENAI_API_KEY),
        "ollama_available": False,
        "ollama_models": [],
    }
    if LLM_PROVIDER == "ollama":
        try:
            with httpx.Client(timeout=5) as client:
                res = client.get(f"{OLLAMA_URL}/api/tags")
                res.raise_for_status()
                data = res.json()
                status["ollama_available"] = True
                status["ollama_models"] = [m.get("name") for m in data.get("models", [])]
        except Exception as exc:  # noqa: BLE001
            status["ollama_error"] = str(exc)
    return status


@app.get("/api/architecture")
def architecture():
    return {
        "name": "SIM-QEI Service-as-a-Software",
        "layers": [
            "Sistemas físicos simulados",
            "Edge simulator",
            "API Gateway lógico via FastAPI",
            "Cloud/microsserviços simplificados",
            "Banco PostgreSQL para séries temporais",
            "Camada de IA com regras + LLM contextualizado",
            "Frontend web operacional",
        ],
        "llm_integration": {
            "provider": LLM_PROVIDER,
            "ollama_model": OLLAMA_MODEL,
            "openai_model": OPENAI_MODEL if bool(OPENAI_API_KEY) else None,
            "strategy": "RAG simplificado: coleta contexto do banco, monta prompt técnico e força resposta baseada nos dados.",
        },
        "agent_loop": {
            "input": "Telemetria elétrica simulada",
            "decision": "Regras técnicas de qualidade de energia + classificação de severidade + LLM consultivo",
            "action": "Alarme, recomendação, registro de decisão e resposta em linguagem natural",
            "metric": "FP, THD, tensão, tempo de detecção, quantidade de alarmes e retorno à faixa normal",
        },
    }
