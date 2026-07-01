import math
import os
import random
import time
from datetime import datetime, timezone

import requests

API_URL = os.getenv("API_URL", "http://localhost:8000/api/telemetry")
HEALTH_URL = os.getenv("HEALTH_URL", "http://localhost:8000/api/health")
INTERVAL = float(os.getenv("SIM_INTERVAL_SECONDS", "2"))

CDCS = ["CDC-01", "CDC-02", "CDC-03"]
PLANT = "Planta Simulada AV3"


def wait_backend():
    print(f"Aguardando backend em {HEALTH_URL} ...", flush=True)
    for _ in range(120):
        try:
            r = requests.get(HEALTH_URL, timeout=3)
            if r.status_code == 200:
                print("Backend disponível. Iniciando simulação Edge.", flush=True)
                return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError("Backend não ficou disponível.")


def scenario_for(cdc: str, cycle: int) -> str:
    # Cada ciclo tem 180 segundos. Os cenários são defasados entre os CDCs.
    if cdc == "CDC-02" and 25 <= cycle < 60:
        return "baixo_fator_potencia"
    if cdc == "CDC-03" and 65 <= cycle < 95:
        return "thd_elevado"
    if cdc == "CDC-01" and 105 <= cycle < 130:
        return "sag_tensao"
    if cdc == "CDC-02" and 140 <= cycle < 160:
        return "swell_tensao"
    return "normal"


def make_payload(cdc: str) -> dict:
    now = time.time()
    cycle = int(now) % 180
    scenario = scenario_for(cdc, cycle)
    phase = now / 20.0 + CDCS.index(cdc)

    base_voltage = 220 + 2.5 * math.sin(phase) + random.uniform(-1.2, 1.2)
    power_factor = 0.965 + random.uniform(-0.015, 0.01)
    thd_voltage = 2.6 + 0.8 * abs(math.sin(phase / 2)) + random.uniform(-0.2, 0.3)
    thd_current = 8.0 + 2.0 * abs(math.sin(phase / 3)) + random.uniform(-0.5, 0.5)
    frequency = 60.0 + random.uniform(-0.04, 0.04)

    if scenario == "baixo_fator_potencia":
        power_factor = random.uniform(0.82, 0.90)
    elif scenario == "thd_elevado":
        thd_voltage = random.uniform(6.0, 11.5)
        thd_current = random.uniform(14.0, 23.0)
    elif scenario == "sag_tensao":
        base_voltage = random.uniform(180.0, 196.0)
    elif scenario == "swell_tensao":
        base_voltage = random.uniform(244.0, 258.0)

    load_factor = {"CDC-01": 1.0, "CDC-02": 1.35, "CDC-03": 0.78}[cdc]
    current_base = 115 * load_factor + 15 * math.sin(phase / 1.5) + random.uniform(-3.0, 3.0)

    voltage_a = base_voltage + random.uniform(-1.5, 1.5)
    voltage_b = base_voltage + random.uniform(-1.5, 1.5)
    voltage_c = base_voltage + random.uniform(-1.5, 1.5)

    current_a = max(5.0, current_base + random.uniform(-3.0, 3.0))
    current_b = max(5.0, current_base + random.uniform(-3.0, 3.0))
    current_c = max(5.0, current_base + random.uniform(-3.0, 3.0))

    avg_voltage = (voltage_a + voltage_b + voltage_c) / 3.0
    avg_current = (current_a + current_b + current_c) / 3.0
    apparent_power_kva = math.sqrt(3) * avg_voltage * avg_current / 1000.0
    active_power_kw = apparent_power_kva * power_factor
    reactive_power_kvar = math.sqrt(max(apparent_power_kva**2 - active_power_kw**2, 0.0))

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "plant": PLANT,
        "cdc": cdc,
        "meter_id": f"MED-{cdc[-2:]}",
        "voltage_a": round(voltage_a, 2),
        "voltage_b": round(voltage_b, 2),
        "voltage_c": round(voltage_c, 2),
        "current_a": round(current_a, 2),
        "current_b": round(current_b, 2),
        "current_c": round(current_c, 2),
        "active_power_kw": round(active_power_kw, 2),
        "reactive_power_kvar": round(reactive_power_kvar, 2),
        "apparent_power_kva": round(apparent_power_kva, 2),
        "power_factor": round(power_factor, 4),
        "thd_voltage": round(thd_voltage, 2),
        "thd_current": round(thd_current, 2),
        "frequency": round(frequency, 3),
        "event_type": scenario,
    }


def main():
    wait_backend()
    counter = 0
    while True:
        for cdc in CDCS:
            payload = make_payload(cdc)
            try:
                response = requests.post(API_URL, json=payload, timeout=5)
                response.raise_for_status()
                result = response.json()
                alarms = result.get("alarms_created", [])
                if alarms:
                    print(f"[{payload['cdc']}] Evento={payload['event_type']} Alarmes={alarms}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"Falha ao enviar telemetria para {cdc}: {exc}", flush=True)
        counter += 1
        if counter % 10 == 0:
            print("Simulador Edge ativo: dados enviados para CDC-01, CDC-02 e CDC-03.", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
