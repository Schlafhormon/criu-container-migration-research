#!/usr/bin/env python3


import os, socket, time, json, random, threading, uuid
from datetime import datetime, timezone
from flask import Flask, Response, request, jsonify

app = Flask(__name__)


COUNTER = 0
START_TIME = time.time()

LOG_PATH = os.environ.get("APP_LOG_PATH", "/data/requests.log")
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
UPLOAD_ROOT = os.environ.get("APP_UPLOAD_ROOT", "/data/uploads")


def parse_int_arg(name: str, default: int, minimum: int = 0) -> int:
    # Parse an integer argument.
    raw = request.args.get(name, str(default))
    try:
        val = int(raw)
    except Exception:
        val = default
    return max(minimum, val)

def now_iso():
    # Format the current timestamp.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def read_loadavg():
    # Read the system load average.
    try:
        with open("/proc/loadavg") as f:
            la = f.read().strip().split()[:3]
        return {"1m": float(la[0]), "5m": float(la[1]), "15m": float(la[2])}
    except:

        return {"1m": None, "5m": None, "15m": None}

def read_meminfo():
    # Read process memory details.
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k.strip()] = v.strip()
    except:
        pass

    rss_kb = None
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    rss_kb = int(parts[1])
                    break
    except:
        pass
    return {"meminfo": info, "rss_kb": rss_kb}

def do_cpu_work(n=20000):

    data = [random.random() for _ in range(n)]
    data.sort()
    return sum(data)


def readiness_payload(cpu_n: int = 2000):
    # Build the readiness payload.
    t0 = time.time()
    host = socket.gethostname()
    pid = os.getpid()
    uptime = time.time() - START_TIME
    cpu_sum = round(do_cpu_work(cpu_n), 6)
    rt_ms = (time.time() - t0) * 1000.0
    return {
        "ts": now_iso(),
        "hostname": host,
        "pid": pid,
        "uptime_s": round(uptime, 3),
        "counter": COUNTER,
        "loadavg": read_loadavg(),
        "memory": {"rss_kb": read_meminfo().get("rss_kb")},
        "cpu_probe_n": cpu_n,
        "cpu_probe_sum": cpu_sum,
        "rt_ms": round(rt_ms, 3),
    }


@app.route("/")
def index():
    # Serve the index endpoint.
    return jsonify({
        "service": "testweb",
        "ts": now_iso(),
        "pid": os.getpid(),
        "counter": COUNTER,
    })

@app.route("/health")
def health():
    # Serve the health endpoint.
    return "OK\n", 200


@app.route("/ready")
def ready():
    # Serve the readiness endpoint.
    return jsonify(readiness_payload())

@app.route("/counter")
def counter():
    # Serve the counter endpoint.
    global COUNTER
    COUNTER += 1
    return jsonify({"ts": now_iso(), "counter": COUNTER})

@app.route("/work")
def work():
    # Run the standard workload.

    sleep_ms = int(request.args.get("sleep_ms", "100"))
    cpu_n = int(request.args.get("cpu_n", "20000"))
    t0 = time.time()

    s = do_cpu_work(cpu_n)

    if sleep_ms > 0:
        time.sleep(sleep_ms / 1000.0)
    rt_ms = (time.time() - t0) * 1000.0
    return jsonify({"ts": now_iso(), "rt_ms": round(rt_ms, 2), "cpu_sum": s, "sleep_ms": sleep_ms, "cpu_n": cpu_n})

@app.route("/heavy")
def heavy():
    # Run the heavy workload.
    sleep_ms = int(request.args.get("sleep_ms", "1000"))
    cpu_n = int(request.args.get("cpu_n", "300000"))
    t0 = time.time()
    s = do_cpu_work(cpu_n)
    if sleep_ms > 0:
        time.sleep(sleep_ms / 1000.0)
    rt_ms = (time.time() - t0) * 1000.0
    return jsonify({"ts": now_iso(), "rt_ms": round(rt_ms, 2), "cpu_sum": s, "sleep_ms": sleep_ms, "cpu_n": cpu_n})

@app.route("/info")
def info():
    # Serve diagnostic information.
    host = socket.gethostname()
    pid = os.getpid()
    uptime = time.time() - START_TIME
    return jsonify({
        "ts": now_iso(),
        "hostname": host,
        "pid": pid,
        "uptime_s": round(uptime, 3),
        "loadavg": read_loadavg(),
        "memory": read_meminfo(),
        "env": {k: v for k, v in os.environ.items() if k.startswith("APP_") or k in ("PYTHONUNBUFFERED","ENV","PORT")}
    })

@app.route("/stream")
def stream():
    # Stream continuous data.
    interval_ms = parse_int_arg("interval_ms", 500, minimum=0)
    limit = request.args.get("limit")
    limit = int(limit) if limit is not None else None
    payload_kb = parse_int_arg("payload_kb", 0, minimum=0)
    stream_format = (request.args.get("format", "ndjson") or "ndjson").strip().lower()
    if stream_format not in ("ndjson", "raw"):
        stream_format = "ndjson"
    payload_bytes = payload_kb * 1024
    payload_text = ("x" * payload_bytes) if (payload_bytes > 0 and stream_format == "ndjson") else ""
    raw_chunk = (b"x" * max(1, payload_bytes)) if stream_format == "raw" else b""

    def gen():

        i = 0
        while True:
            i += 1
            if stream_format == "raw":
                yield raw_chunk
            else:
                row = {"i": i, "ts": now_iso()}
                if payload_bytes > 0:


                    row["payload_len"] = payload_bytes
                    row["payload"] = payload_text
                line = json.dumps(row, separators=(",", ":")) + "\n"
                yield line
            if limit and i >= limit:
                break
            time.sleep(max(0.0, interval_ms / 1000.0))

    mimetype = "application/octet-stream" if stream_format == "raw" else "text/plain"
    return Response(gen(), mimetype=mimetype)


@app.route("/download")
def download():
    # Stream a download.
    total_bytes = parse_int_arg("bytes", 1024 * 1024, minimum=0)
    chunk_kb = parse_int_arg("chunk_kb", 64, minimum=1)
    sleep_ms = parse_int_arg("sleep_ms", 0, minimum=0)
    pattern = (request.args.get("pattern", "zero") or "zero").strip().lower()
    include_meta = request.args.get("meta", "0").strip().lower() in ("1", "true", "yes", "on")
    if pattern not in ("zero", "repeat", "random"):
        pattern = "zero"

    chunk_size = chunk_kb * 1024
    if pattern == "zero":
        chunk_template = b"\x00" * chunk_size
    elif pattern == "repeat":
        seed = b"ContainerLiveMigration-"
        mult = (chunk_size + len(seed) - 1) // len(seed)
        chunk_template = (seed * mult)[:chunk_size]
    else:
        chunk_template = None

    workload_id = str(uuid.uuid4())

    def gen():
        sent = 0
        if include_meta:
            meta = {
                "type": "download_meta",
                "id": workload_id,
                "bytes": total_bytes,
                "chunk_kb": chunk_kb,
                "sleep_ms": sleep_ms,
                "pattern": pattern,
                "ts": now_iso(),
            }
            yield (json.dumps(meta, separators=(",", ":")) + "\n").encode("utf-8")
        while sent < total_bytes:
            to_send = min(chunk_size, total_bytes - sent)
            if pattern == "random":
                chunk = os.urandom(to_send)
            else:
                chunk = chunk_template[:to_send]
            yield chunk
            sent += to_send
            if sleep_ms > 0:
                time.sleep(sleep_ms / 1000.0)

    resp = Response(gen(), mimetype="application/octet-stream")
    resp.headers["X-Workload-Id"] = workload_id
    return resp


@app.route("/upload", methods=["POST"])
def upload():
    # Receive an upload.
    sink = (request.args.get("sink", "discard") or "discard").strip().lower()
    if sink not in ("discard", "file"):
        sink = "discard"
    chunk_kb = parse_int_arg("chunk_kb", 64, minimum=1)
    sleep_ms = parse_int_arg("sleep_ms", 0, minimum=0)
    upload_id = (request.args.get("id") or str(uuid.uuid4())).strip()
    if not upload_id:
        upload_id = str(uuid.uuid4())

    chunk_size = chunk_kb * 1024
    t0 = time.time()
    bytes_received = 0
    file_path = None
    out_fp = None

    try:
        if sink == "file":
            os.makedirs(UPLOAD_ROOT, exist_ok=True)
            safe_id = "".join(ch for ch in upload_id if ch.isalnum() or ch in ("-", "_")) or str(uuid.uuid4())
            file_path = os.path.join(UPLOAD_ROOT, f"{safe_id}.bin")
            out_fp = open(file_path, "wb")

        while True:
            chunk = request.stream.read(chunk_size)
            if not chunk:
                break
            bytes_received += len(chunk)
            if out_fp:
                out_fp.write(chunk)
            if sleep_ms > 0:
                time.sleep(sleep_ms / 1000.0)
    finally:
        if out_fp:
            out_fp.close()

    rt_ms = (time.time() - t0) * 1000.0
    resp = {
        "bytes_received": bytes_received,
        "rt_ms": round(rt_ms, 2),
        "sink": sink,
        "id": upload_id,
    }
    if file_path:
        resp["file_path"] = file_path
    return jsonify(resp)

@app.route("/random")
def random_resp():
    # Return a random response.

    codes_str = request.args.get("codes", "200,400,500")
    codes = [int(x) for x in codes_str.split(",") if x.strip().isdigit()]
    if not codes:
        codes = [200, 400, 500]
    min_ms = int(request.args.get("min_ms", "0"))
    max_ms = int(request.args.get("max_ms", "500"))
    delay = random.randint(min_ms, max_ms)
    time.sleep(delay / 1000.0)
    code = random.choice(codes)
    payload = {"ts": now_iso(), "delay_ms": delay, "status": code}
    return (jsonify(payload), code)

@app.route("/log", methods=["GET", "POST"])
def log_endpoint():
    # Log endpoint.
    entry = {
        "ts": now_iso(),
        "method": request.method,
        "path": request.path,
        "args": request.args.to_dict(flat=True),
        "headers": {k: v for k, v in request.headers.items() if k.lower() in ("user-agent","x-request-id")},
        "remote_addr": request.remote_addr
    }


    try:
        if request.method == "POST":
            entry["body_len"] = len(request.get_data(cache=False, as_text=False) or b"")
    except Exception as e:
        entry["body_err"] = str(e)

    line = json.dumps(entry, ensure_ascii=False)

    with open(LOG_PATH, "a", encoding="utf-8") as fp:
        fp.write(line + "\n")

    return jsonify({"written": True, "file": LOG_PATH, "entry": entry})
