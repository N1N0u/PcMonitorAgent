import requests
import time

PROMETHEUS_URL = "http://localhost:9090"


###############################
# RANGE QUERY  (for Grafana panels)
###############################

def query_prometheus_range(promql: str, duration_s: int = 1800,
                            step: str = "30s") -> dict:
    """
    Runs a PromQL range query and returns a list of series, each with:
      {"metric": {...labels...}, "timestamps": [...], "values": [...]}
    Returns [] on error or no data.
    """
    end   = int(time.time())
    start = end - duration_s
    try:
        r = requests.get(
            PROMETHEUS_URL + "/api/v1/query_range",
            params={"query": promql, "start": start,
                    "end": end, "step": step},
            timeout=10,
        )
        data = r.json()
        if data.get("status") != "success":
            return []
        out = []
        for series in data["data"]["result"]:
            ts = [float(v[0]) for v in series["values"]]
            vs = [float(v[1]) for v in series["values"]]
            out.append({"metric": series["metric"],
                        "timestamps": ts, "values": vs})
        return out
    except Exception:
        return []

###############################
# SINGLE QUERY
###############################

def query_prometheus(promql: str) -> dict:
    """
    Sends a PromQL query to Prometheus and returns a clean result.
    promql : the PromQL expression string
    returns: dict with status, value (float), and labels
    """
    try:
        response = requests.get(
            PROMETHEUS_URL + "/api/v1/query",
            params={"query": promql},
            timeout=5
        )

        data = response.json()

        if data["status"] != "success":
            return {"status": "error", "message": data.get("error", "unknown error")}

        results = data["data"]["result"]

        if not results:
            return {"status": "no_data", "value": None, "labels": {}}

        value = float(results[0]["value"][1])
        labels = results[0]["metric"]

        return {"status": "success", "value": value, "labels": labels}

    except Exception as e:
        return {"status": "error", "message": str(e)}


###############################
# ALL METRICS
###############################

def get_system_metrics() -> dict:
    """
    Queries Prometheus for all key system metrics at once.
    Returns a single dictionary the agent can read and reason about.
    """

    # --- CPU usage % (average across all cores, last 2 minutes) ---
    cpu = query_prometheus(
        '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100)'
    )

    # --- RAM usage % ---
    ram = query_prometheus(
        "100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)"
    )

    # --- RAM total in GB ---
    ram_total = query_prometheus(
        "node_memory_MemTotal_bytes / 1024 / 1024 / 1024"
    )

    # --- RAM used in GB ---
    ram_used = query_prometheus(
        "(node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes) / 1024 / 1024 / 1024"
    )

    # --- RAM available in GB ---
    ram_available = query_prometheus(
        "node_memory_MemAvailable_bytes / 1024 / 1024 / 1024"
    )

    # --- Disk usage % on root partition ---
    disk = query_prometheus(
        'avg(100 * (1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}))'
    )

    # --- NVIDIA GPU utilization % ---
    nvidia_gpu_util = query_prometheus("DCGM_FI_DEV_GPU_UTIL")

    # --- NVIDIA GPU memory used (MB) ---
    nvidia_mem_used = query_prometheus("DCGM_FI_DEV_FB_USED")

    # --- NVIDIA GPU memory total (MB) ---
    nvidia_mem_total = query_prometheus("DCGM_FI_DEV_FB_FREE + DCGM_FI_DEV_FB_USED")

    # --- NVIDIA GPU temperature (°C) ---
    nvidia_temp = query_prometheus("DCGM_FI_DEV_GPU_TEMP")

    # --- Intel GPU utilization % ---
    intel_gpu_util = query_prometheus("intel_gpu_utilization_percent")

    # --- Intel GPU power (watts) ---
    intel_gpu_power = query_prometheus("intel_gpu_power_watts")

    # --- Which services are UP (1) or DOWN (0) ---
    services = {}
    try:
        response = requests.get(
            PROMETHEUS_URL + "/api/v1/query",
            params={"query": "up"},
            timeout=5
        )
        all_results = response.json()["data"]["result"]
        for item in all_results:
            job = item["metric"].get("job", "unknown")
            value = int(float(item["value"][1]))
            services[job] = value
    except Exception as e:
        services = {}
        print(f"[WARN] Could not fetch services: {e}")

    return {
        "cpu_percent":        round(cpu["value"],            2) if cpu["status"]            == "success" else None,
        "ram_percent":        round(ram["value"],            2) if ram["status"]            == "success" else None,
        "ram_total_gb":       round(ram_total["value"],      2) if ram_total["status"]      == "success" else None,
        "ram_used_gb":        round(ram_used["value"],       2) if ram_used["status"]       == "success" else None,
        "ram_available_gb":   round(ram_available["value"],  2) if ram_available["status"]  == "success" else None,
        "disk_percent":       round(disk["value"],           2) if disk["status"]           == "success" else None,
        "nvidia_gpu_util":    round(nvidia_gpu_util["value"],  2) if nvidia_gpu_util["status"]  == "success" else None,
        "nvidia_mem_used_mb": round(nvidia_mem_used["value"],  2) if nvidia_mem_used["status"]  == "success" else None,
        "nvidia_mem_total_mb":round(nvidia_mem_total["value"], 2) if nvidia_mem_total["status"] == "success" else None,
        "nvidia_gpu_temp_c":  round(nvidia_temp["value"],     2) if nvidia_temp["status"]     == "success" else None,
        "intel_gpu_util":     round(intel_gpu_util["value"],  2) if intel_gpu_util["status"]  == "success" else None,
        "intel_gpu_power_w":  round(intel_gpu_power["value"], 2) if intel_gpu_power["status"] == "success" else None,
        "services_up":        services
    }


###############################
# FORMAT FOR LLM
###############################

def format_metrics(metrics: dict) -> str:
    """
    Converts the metrics dictionary into a clean human-readable
    text block that the LLM can easily parse and reason about.
    """

    def label(value, warn=75, critical=90):
        if value is None:
            return "N/A ⚪ (no data)"
        if value >= critical:
            return f"{value}%  🔴 CRITICAL"
        if value >= warn:
            return f"{value}%  🟡 WARNING"
        return f"{value}%  ✅ OK"

    lines = []
    lines.append("=== LIVE SYSTEM METRICS ===")

    # --- CPU & RAM & Disk ---
    lines.append(f"  CPU  Usage   : {label(metrics.get('cpu_percent'),  warn=75, critical=90)}")
    lines.append(f"  RAM  Usage   : {label(metrics.get('ram_percent'),  warn=75, critical=90)}")
    lines.append(f"  RAM  Total   : {metrics.get('ram_total_gb',    'N/A')} GB")
    lines.append(f"  RAM  Used    : {metrics.get('ram_used_gb',     'N/A')} GB")
    lines.append(f"  RAM  Free    : {metrics.get('ram_available_gb','N/A')} GB")
    lines.append(f"  Disk Usage   : {label(metrics.get('disk_percent'), warn=80, critical=95)}")

    # --- NVIDIA GPU ---
    lines.append("")
    lines.append("  GPU (NVIDIA):")
    nvidia_util  = metrics.get('nvidia_gpu_util')
    nvidia_used  = metrics.get('nvidia_mem_used_mb')
    nvidia_total = metrics.get('nvidia_mem_total_mb')
    nvidia_temp  = metrics.get('nvidia_gpu_temp_c')
    lines.append(f"    Utilization  : {f'{nvidia_util}%'  if nvidia_util  is not None else 'N/A ⚪'}")
    lines.append(f"    Mem Used     : {f'{nvidia_used} MB' if nvidia_used  is not None else 'N/A ⚪'}")
    lines.append(f"    Mem Total    : {f'{nvidia_total} MB'if nvidia_total is not None else 'N/A ⚪'}")
    lines.append(f"    Temperature  : {f'{nvidia_temp} °C' if nvidia_temp  is not None else 'N/A ⚪'}")

    # --- Intel GPU ---
    lines.append("")
    lines.append("  GPU (Intel):")
    intel_util  = metrics.get('intel_gpu_util')
    intel_power = metrics.get('intel_gpu_power_w')
    lines.append(f"    Utilization  : {f'{intel_util}%'   if intel_util  is not None else 'N/A ⚪'}")
    lines.append(f"    Power        : {f'{intel_power} W' if intel_power is not None else 'N/A ⚪'}")

    # --- Services ---
    lines.append("")
    lines.append("  Services:")
    services = metrics.get("services_up", {})
    if not services:
        lines.append("    No service data available ⚪")
    else:
        for job, status in services.items():
            icon = "✅ UP" if status == 1 else "❌ DOWN"
            lines.append(f"    {job:<25}: {icon}")

    lines.append("===========================")

    return "\n".join(lines)