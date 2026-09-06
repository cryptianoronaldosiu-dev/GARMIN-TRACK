import os
import requests
from datetime import datetime, timedelta, timezone
from garminconnect import Garmin
from supabase import create_client

# ── Config ────────────────────────────────────────────────────────────────────
GARMIN_EMAIL     = os.environ["GARMIN_EMAIL"]
GARMIN_PASSWORD  = os.environ["GARMIN_PASSWORD"]
MAKE_WEBHOOK_URL = os.environ["MAKE_WEBHOOK_URL"]
SUPABASE_URL     = os.environ["SUPABASE_URL"]
SUPABASE_KEY     = os.environ["SUPABASE_KEY"]

# ── Helpers ───────────────────────────────────────────────────────────────────
def ms_to_pace(speed_ms):
    """Convierte m/s a min/km. Retorna None si el valor es inválido."""
    try:
        s = float(speed_ms)
        return round((1000 / s) / 60, 2) if s > 0 else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None

def safe_float(value, decimals=None):
    """Convierte a float de forma segura, retorna None si falla."""
    try:
        v = float(value)
        return round(v, decimals) if decimals is not None else v
    except (TypeError, ValueError):
        return None

def safe_int(value):
    """Convierte a int de forma segura, retorna None si falla."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None

# ── Core ──────────────────────────────────────────────────────────────────────
def build_splits(splits_raw):
    """Extrae splits por km desde la respuesta de Garmin."""
    if not splits_raw or "lapDTOs" not in splits_raw:
        return []
    return [
        {
            "km":        lap.get("lapIndex"),
            "pace":      ms_to_pace(lap.get("averageSpeed")),
            "fc_avg":    lap.get("averageHR"),
            "distancia": round((lap.get("distance") or 0) / 1000, 2),
        }
        for lap in splits_raw["lapDTOs"]
    ]

def build_row(activity, splits_data):
    """Construye el dict a insertar en Supabase."""
    cadencia_raw = activity.get("averageRunningCadenceInStepsPerMinute")
    stride_raw   = activity.get("avgStrideLength")
    stride_m     = round(float(stride_raw) / 100, 3) if stride_raw else None
    return {
        "garmin_id":                   str(activity.get("activityId", "")),
        "fecha":                       activity.get("startTimeGMT"),
        "tipo":                        activity.get("activityType", {}).get("typeKey", "running"),
        "distancia_km":                round((activity.get("distance") or 0) / 1000, 2),
        "duracion_seg":                safe_int(activity.get("duration")),
        "pace_avg":                    ms_to_pace(activity.get("averageSpeed")),
        "pace_mejor":                  ms_to_pace(activity.get("maxSpeed")),
        "fc_avg":                      safe_float(activity.get("averageHR")),
        "fc_max":                      safe_float(activity.get("maxHR")),
        "calorias":                    safe_float(activity.get("calories")),
        "elevacion_m":                 safe_float(activity.get("elevationGain"), 1),
        "cadencia_avg":                round(float(cadencia_raw) / 2, 1) if cadencia_raw else None,
        "vo2max":                      safe_float(activity.get("vO2MaxValue")),
        "training_load":               safe_float(activity.get("activityTrainingLoad"), 1),
        "training_effect_aerobico":    safe_float(activity.get("aerobicTrainingEffect"), 1),
        "training_effect_anaerobico":  safe_float(activity.get("anaerobicTrainingEffect"), 1),
        "recovery_time_h":             safe_float(activity.get("recoveryTime"), 1),
        "training_status":             activity.get("trainingStatus"),
        "hrv_status":                  activity.get("hrvStatus"),
        "body_battery_inicio":         safe_float(activity.get("differenceBodyBattery")),
        "tiempo_zona1_seg":            activity.get("hrTimeInZone_1"),
        "tiempo_zona2_seg":            activity.get("hrTimeInZone_2"),
        "tiempo_zona3_seg":            activity.get("hrTimeInZone_3"),
        "tiempo_zona4_seg":            activity.get("hrTimeInZone_4"),
        "tiempo_zona5_seg":            activity.get("hrTimeInZone_5"),
        "splits_json":                 splits_data,
        "ground_contact_time":         safe_float(activity.get("avgGroundContactTime"), 1),
        "vertical_oscillation":        safe_float(activity.get("avgVerticalOscillation"), 1),
        "stride_length":               stride_m,
        "vertical_ratio":              safe_float(activity.get("avgVerticalRatio"), 2),
        "avg_power":                   safe_float(activity.get("avgPower"), 1),
    }

def sync_activity(activity, garmin_client, supabase):
    """Sincroniza una actividad: inserta y notifica a Make."""
    garmin_id = str(activity.get("activityId", ""))

    splits_raw  = garmin_client.get_activity_splits(garmin_id)
    splits_data = build_splits(splits_raw)
    row         = build_row(activity, splits_data)

    # Insertar en Supabase y recuperar el id generado
    result = supabase.table("actividades").insert(row).execute()
    if result.data:
        row["id"] = result.data[0]["id"]
        print(f"  ✓ Guardada: {row['tipo']} {row['distancia_km']}km "
              f"| pace {row['pace_avg']} | FC {row['fc_avg']} "
              f"| load {row['training_load']}")
    else:
        print(f"  ⚠ Insert sin datos de retorno para {garmin_id}")

    # Notificar a Make.com — convertir paces a mm:ss para que Claude los lea correctamente
    def fmt_pace(dec):
        if not dec: return None
        total_seg = round(dec * 60)          # evita el caso "5:60" al redondear
        m, s = divmod(total_seg, 60)
        return f"{m}:{s:02d}"

    webhook_payload = {**row}
    webhook_payload["pace_avg_fmt"]   = fmt_pace(row.get("pace_avg"))
    webhook_payload["pace_mejor_fmt"] = fmt_pace(row.get("pace_mejor"))

    try:
        resp = requests.post(MAKE_WEBHOOK_URL, json=webhook_payload, timeout=30)
        print(f"  ✓ Webhook Make.com → {resp.status_code}")
    except requests.RequestException as e:
        print(f"  ⚠ Error webhook: {e}")


# ── Training Status ───────────────────────────────────────────────────────────
def sync_training_status(garmin, supabase):
    """Jala el Training Status actual de Garmin y lo guarda en Supabase."""
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Training readiness
        readiness_data = garmin.get_training_readiness(today)
        readiness_score = None
        if readiness_data and isinstance(readiness_data, list) and len(readiness_data) > 0:
            readiness_score = readiness_data[0].get("overallScore") or readiness_data[0].get("score")
        elif readiness_data and isinstance(readiness_data, dict):
            readiness_score = readiness_data.get("overallScore") or readiness_data.get("score")

        # Training status + cargas aguda/crónica.
        # get_stats() NO trae acuteLoad/chronicLoad — el dato real vive en
        # get_training_status() → latestTrainingStatusData → acuteTrainingLoadDTO.
        status = None
        acute_load = None
        chronic_load = None

        try:
            ts_data = garmin.get_training_status(today)
            recent = (ts_data or {}).get("mostRecentTrainingStatus", {}) or {}
            latest = recent.get("latestTrainingStatusData", {}) or {}
            if latest:
                # dict indexado por deviceId → tomar el primer device
                dev = next(iter(latest.values()), {}) or {}
                status = dev.get("trainingStatusFeedbackPhrase") or dev.get("trainingStatus") or status
                acwr_dto = dev.get("acuteTrainingLoadDTO", {}) or {}
                acute_load = safe_float(
                    acwr_dto.get("dailyTrainingLoadAcute") or acwr_dto.get("acuteLoad"), 1)
                chronic_load = safe_float(
                    acwr_dto.get("dailyTrainingLoadChronic") or acwr_dto.get("chronicLoad"), 1)
        except Exception as e:
            print(f"  ⚠ get_training_status falló ({e}), usando get_stats como fallback.")

        # Fallback: get_stats por si algún campo faltó arriba
        if status is None or acute_load is None or chronic_load is None:
            stats = garmin.get_stats(today)
            if stats:
                status = status or stats.get("trainingStatus") or stats.get("currentTrainingStatus")
                if acute_load is None:
                    acute_load = safe_float(stats.get("acuteLoad") or stats.get("shortTermLoad"), 1)
                if chronic_load is None:
                    chronic_load = safe_float(stats.get("chronicLoad") or stats.get("longTermLoad"), 1)

        # HRV weekly
        hrv_data = garmin.get_hrv_data(today)
        hrv_weekly = None
        if hrv_data and isinstance(hrv_data, dict):
            hrv_weekly = safe_float(
                hrv_data.get("hrvSummary", {}).get("weeklyAvg") or
                hrv_data.get("weeklyAvg"), 1
            )

        # VO2Max
        vo2_data = garmin.get_max_metrics(today)
        vo2max = None
        if vo2_data and isinstance(vo2_data, list) and len(vo2_data) > 0:
            vo2max = safe_float(vo2_data[0].get("generic", {}).get("vo2MaxPreciseValue"), 1)

        row = {
            "status":       status,
            "readiness":    safe_int(readiness_score),
            "acute_load":   acute_load,
            "chronic_load": chronic_load,
            "hrv_weekly":   hrv_weekly,
            "vo2max":       vo2max,
        }

        # Evitar duplicados: si ya hay una fila de hoy (corridas manuales o
        # re-runs del workflow), actualizarla en vez de insertar otra.
        updated = False
        try:
            last = supabase.table("training_status_history") \
                .select("id, fecha").order("fecha", desc=True).limit(1).execute().data
            if last and str(last[0].get("fecha", ""))[:10] == today:
                supabase.table("training_status_history") \
                    .update(row).eq("id", last[0]["id"]).execute()
                updated = True
        except Exception as e:
            print(f"  ⚠ No se pudo verificar fila existente ({e}), insertando nueva.")

        if not updated:
            supabase.table("training_status_history").insert(row).execute()

        accion = "actualizado" if updated else "guardado"
        print(f"  ✓ Training Status {accion}: {status or '—'} | Readiness: {readiness_score or '—'} | HRV: {hrv_weekly or '—'}")

    except Exception as e:
        print(f"  ⚠ Error jalando Training Status: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("Conectando a Garmin Connect...")
    garmin = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    garmin.login()
    print("Login exitoso")

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Ventana de 72h: si un día falla el workflow, la siguiente corrida
    # recupera las actividades perdidas (el dedup evita duplicados).
    end   = datetime.now(timezone.utc)
    start = end - timedelta(hours=72)

    print(f"Buscando actividades: {start.strftime('%Y-%m-%d')} → {end.strftime('%Y-%m-%d')}")
    activities = garmin.get_activities_by_date(
        start.strftime("%Y-%m-%d"),
        end.strftime("%Y-%m-%d"),
        "running"
    )

    if not activities:
        print("Sin actividades nuevas.")
    else:
        # Dedup en una sola query (en vez de un SELECT por actividad)
        ids = [str(a.get("activityId", "")) for a in activities]
        existing = supabase.table("actividades").select("garmin_id") \
            .in_("garmin_id", ids).execute().data or []
        existing_ids = {r["garmin_id"] for r in existing}
        nuevas = [a for a in activities if str(a.get("activityId", "")) not in existing_ids]

        print(f"Encontradas {len(activities)} actividad(es), {len(nuevas)} nueva(s).")
        errores = 0
        for activity in nuevas:
            # Un error en una actividad no debe tumbar el resto del sync
            try:
                sync_activity(activity, garmin, supabase)
            except Exception as e:
                errores += 1
                print(f"  ✗ Error en actividad {activity.get('activityId')}: {e}")
        if errores:
            print(f"⚠ {errores} actividad(es) fallaron — se reintentarán en la próxima corrida (ventana 72h).")

    # Sync training status (una vez por run, independiente de actividades)
    sync_training_status(garmin, supabase)
    print("Sync completo.")

if __name__ == "__main__":
    main()
