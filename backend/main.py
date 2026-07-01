import os
import time
import math
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://simqei:simqei@localhost:5432/simqei")
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")

app = FastAPI(
    title="SIM-QEI API",
    description="Backend do protótipo funcional do SIM-QEI com simulação de qualidade de energia e agente de IA determinístico.",
    version="0.1.0",
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
    return dict(row)


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
        return {"status": "ok", "service": "simqei_backend"}
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
    # Aproximação demonstrativa: janela de 10 min
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


def build_agent_answer(question: str) -> Dict[str, Any]:
    q = question.lower().strip()
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
                SELECT * FROM alarms
                WHERE ts > NOW() - INTERVAL '1 hour'
                ORDER BY ts DESC
                LIMIT 10;
                """
            )
            recent_alarms = [row_to_dict(r) for r in cur.fetchall()]
            cur.execute(
                """
                SELECT * FROM agent_decisions
                ORDER BY ts DESC
                LIMIT 5;
                """
            )
            recent_decisions = [row_to_dict(r) for r in cur.fetchall()]

    if not latest_rows:
        return {
            "answer": "Ainda não há dados no banco. Aguarde o simulador Edge enviar as primeiras leituras.",
            "sources": [],
        }

    avg_pf = sum(r["power_factor"] for r in latest_rows) / len(latest_rows)
    avg_thd = sum(r["thd_voltage"] for r in latest_rows) / len(latest_rows)
    total_kw = sum(r["active_power_kw"] for r in latest_rows)
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
            f"e {len(recent_alarms)} alarmes registrados na última hora. "
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

    sources = [
        {"type": "latest_telemetry", "items": latest_rows},
        {"type": "recent_alarms", "items": recent_alarms[:3]},
        {"type": "recent_decisions", "items": recent_decisions[:3]},
    ]
    return {"answer": answer, "sources": sources}


@app.post("/api/agent/chat")
def agent_chat(payload: ChatIn):
    return build_agent_answer(payload.question)


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
            "Camada de IA determinística com agentes",
            "Frontend web operacional",
        ],
        "agent_loop": {
            "input": "Telemetria elétrica simulada",
            "decision": "Regras técnicas de qualidade de energia + classificação de severidade",
            "action": "Alarme, recomendação, registro de decisão e resposta em linguagem natural",
            "metric": "FP, THD, tensão, tempo de detecção, quantidade de alarmes e retorno à faixa normal",
        },
    }
