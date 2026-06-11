#!/usr/bin/env python3
import argparse
import base64
import json
import mimetypes
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import strategy_submit_api as control
import market_data


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
SPA_ROUTES = {
    "/",
    "/overview",
    "/pipeline",
    "/blueprint",
    "/modules",
    "/data",
    "/backtests",
    "/results",
    "/artifacts",
    "/manifest",
}


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def utc_mtime(path):
    path = Path(path)
    return path.stat().st_mtime if path.exists() else 0


def count_by_kind(definitions):
    result = {}
    for item in definitions.values():
        kind = item.get("kind", "Unknown")
        result[kind] = result.get(kind, 0) + 1
    return result


def active_stage_counts(manifest):
    stages = ["inputs", "universe", "signal", "target", "constraint", "execution", "analyzer"]
    return {stage: len(manifest.get(stage) or []) for stage in stages}


def load_current_manifest(config):
    return read_json(config["liveManifestPath"], None)


def load_lane_manifest(config, lane_id):
    lane_id = control.normalize_lane_id(lane_id)
    lanes = control.load_lanes(config)
    manifest_path = lanes.get(lane_id, {}).get("liveManifestPath")
    if not manifest_path and lane_id == control.DEFAULT_LANE_ID:
        manifest_path = config["liveManifestPath"]
    if not manifest_path:
        manifest_path = control.lane_live_manifest_path(config, lane_id)
    return read_json(manifest_path, None), manifest_path, lanes.get(lane_id)


def query_limit(query, default=100, maximum=500):
    try:
        value = int((query.get("limit") or [str(default)])[0])
    except (TypeError, ValueError):
        value = default
    return max(0, min(value, maximum))


def limit_mapping(mapping, limit):
    return dict(list((mapping or {}).items())[:limit]) if limit else {}


def build_summary(config):
    control.refresh_engine_lanes_manifest(config)
    modules = control.load_module_definitions(config)
    user_modules = control.load_state(config, "modules.json", {})
    packages = control.load_state(config, "packages.json", {})
    instances = control.load_state(config, "instances.json", {})
    artifacts = control.load_state(config, "artifacts.json", {})
    iterations = control.load_state(config, "iterations.json", [])
    dataset_count = market_data.count_datasets(config)
    backtest_count = market_data.count_backtests(config)
    lanes = control.load_lanes(config)
    attachment = control.load_lane_attachment(config, control.DEFAULT_LANE_ID)
    manifest, manifest_path, current_lane = load_lane_manifest(config, control.DEFAULT_LANE_ID)
    live_path = Path(manifest_path)
    return {
        "status": "ok",
        "serviceTime": control.utc_now(),
        "paths": {
            "liveManifestPath": config["liveManifestPath"],
            "lanesManifestPath": config["lanesManifestPath"],
            "releaseRoot": config["releaseRoot"],
            "controlRoot": config["controlRoot"],
        },
        "current": {
            "laneId": control.DEFAULT_LANE_ID,
            "manifestName": (manifest or {}).get("name"),
            "manifestHash": control.json_digest(manifest) if manifest else "",
            "liveManifestUpdatedAt": utc_mtime(live_path),
            "stageCounts": active_stage_counts(manifest or {}),
            "moduleCount": len((manifest or {}).get("modules") or []),
        },
        "lanes": lanes,
        "currentLane": current_lane,
        "repositories": {
            "moduleDefinitions": len(modules),
            "customModuleDefinitions": len(user_modules),
            "moduleDefinitionsByKind": count_by_kind(modules),
            "packages": len(packages),
            "instances": len(instances),
            "artifacts": len(artifacts),
            "iterations": len(iterations),
            "datasets": dataset_count,
            "backtests": backtest_count,
        },
        "attachment": attachment,
    }


def response_json(handler, status, payload):
    body = json.dumps(payload, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def response_text(handler, status, content, content_type):
    body = content if isinstance(content, bytes) else content.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def read_request_json(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    return json.loads(handler.rfile.read(length) or b"{}")


class EngineServiceHandler(BaseHTTPRequestHandler):
    config = None
    public_url = ""

    def do_HEAD(self):
        parsed = urlparse(self.path)
        if parsed.path in SPA_ROUTES or parsed.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return
        static_path = (WEB_ROOT / parsed.path.lstrip("/")).resolve()
        if static_path.is_file() and WEB_ROOT in static_path.parents:
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(static_path.name)[0] or "application/octet-stream")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        lane_id = control.normalize_lane_id((query.get("laneId") or [control.DEFAULT_LANE_ID])[0])
        try:
            if path == "/api/health":
                response_json(self, 200, {
                    "status": "ok",
                    "publicUrl": self.public_url,
                    "serviceTime": control.utc_now(),
                })
                return
            if path == "/api/summary":
                response_json(self, 200, build_summary(self.config))
                return
            if path == "/api/lanes":
                control.refresh_engine_lanes_manifest(self.config)
                response_json(self, 200, {
                    "lanes": control.load_lanes(self.config),
                    "lanesManifestPath": self.config["lanesManifestPath"],
                })
                return
            if path == "/api/current":
                manifest, manifest_path, lane = load_lane_manifest(self.config, lane_id)
                if manifest is None:
                    response_json(self, 404, {"error": f"live manifest not found for lane '{lane_id}'"})
                else:
                    response_json(self, 200, {
                        "laneId": lane_id,
                        "lane": lane,
                        "attachment": control.load_lane_attachment(self.config, lane_id),
                        "liveManifestPath": manifest_path,
                        "manifest": manifest,
                    })
                return
            if path == "/api/attachment":
                response_json(self, 200, {
                    "laneId": lane_id,
                    "attachment": control.load_lane_attachment(self.config, lane_id),
                })
                return
            if path == "/api/modules":
                modules = control.load_module_definitions(self.config)
                requested_kind = (query.get("kind") or [""])[0]
                if requested_kind:
                    modules = {
                        key: value
                        for key, value in modules.items()
                        if str(value.get("kind", "")).lower() == requested_kind.lower()
                    }
                response_json(self, 200, {
                    "modules": limit_mapping(modules, query_limit(query, 80, 500)),
                    "total": len(modules),
                    "kind": requested_kind,
                })
                return
            if path == "/api/packages":
                packages = control.load_state(self.config, "packages.json", {})
                response_json(self, 200, {
                    "packages": limit_mapping(packages, query_limit(query, 50, 500)),
                    "total": len(packages),
                })
                return
            if path == "/api/instances":
                instances = control.load_state(self.config, "instances.json", {})
                response_json(self, 200, {
                    "instances": limit_mapping(instances, query_limit(query, 80, 500)),
                    "total": len(instances),
                })
                return
            if path == "/api/artifacts":
                artifacts = control.sanitize_artifacts(control.load_state(self.config, "artifacts.json", {}))
                response_json(self, 200, {
                    "artifacts": limit_mapping(artifacts, query_limit(query, 50, 500)),
                    "total": len(artifacts),
                })
                return
            if path == "/api/data/sources":
                response_json(self, 200, {
                    "sources": market_data.list_sources(self.config),
                    "proxy": {
                        "enabled": bool(market_data.configured_proxy(self.config)),
                        "url": market_data.configured_proxy(self.config),
                    },
                })
                return
            if path == "/api/data/proxy":
                response_json(self, 200, {
                    "enabled": bool(market_data.configured_proxy(self.config)),
                    "url": market_data.configured_proxy(self.config),
                })
                return
            if path == "/api/data/search":
                q = (query.get("q") or [""])[0]
                response_json(self, 200, market_data.search_datasets(self.config, q))
                return
            if path == "/api/data/datasets":
                limit = query_limit(query, 50, 500)
                datasets = market_data.list_datasets(self.config, limit)
                response_json(self, 200, {
                    "datasets": datasets,
                    "total": market_data.count_datasets(self.config),
                })
                return
            parts = [part for part in path.split("/") if part]
            if len(parts) == 5 and parts[:3] == ["api", "data", "datasets"] and parts[4] == "bars":
                limit = int((query.get("limit") or ["1000"])[0])
                response_json(self, 200, {
                    "dataset": market_data.get_dataset(self.config, parts[3]),
                    "bars": market_data.get_bars(self.config, parts[3], limit),
                })
                return
            if path == "/api/backtests":
                limit = query_limit(query, 50, 500)
                backtests = market_data.list_backtests(self.config, limit)
                response_json(self, 200, {
                    "backtests": backtests,
                    "total": market_data.count_backtests(self.config),
                })
                return
            if len(parts) == 4 and parts[:2] == ["api", "backtests"] and parts[3] == "meta":
                response_json(self, 200, market_data.get_backtest_meta(self.config, parts[2]))
                return
            if len(parts) == 4 and parts[:2] == ["api", "backtests"] and parts[3] == "result":
                paths = [item for item in (query.get("path") or []) if item]
                response_json(self, 200, {
                    "backtestId": parts[2],
                    "result": market_data.get_backtest_result_slice(self.config, parts[2], paths),
                })
                return
            if len(parts) == 3 and parts[:2] == ["api", "backtests"]:
                response_json(self, 200, market_data.get_backtest(self.config, parts[2]))
                return
            if len(parts) == 4 and parts[:2] == ["api", "backtests"] and parts[3] == "visualization":
                backtest = market_data.get_backtest(self.config, parts[2])
                response_json(self, 200, {
                    "backtestId": parts[2],
                    "visualization": backtest.get("visualization") or {},
                    "result": backtest.get("result") or {},
                    "customVisualizations": market_data.list_visualizations(self.config, parts[2]),
                })
                return
            if path == "/api/visualizations":
                backtest_id = (query.get("backtestId") or [""])[0]
                response_json(self, 200, {"visualizations": market_data.list_visualizations(self.config, backtest_id)})
                return
            if path == "/api/history":
                limit = int((query.get("limit") or ["100"])[0])
                payload = {
                    "events": control.load_sanitized_history_events(self.config, limit),
                }
                if (query.get("full") or ["0"])[0] == "1":
                    payload.update({
                        "iterations": control.load_state(self.config, "iterations.json", []),
                        "artifacts": control.sanitize_artifacts(control.load_state(self.config, "artifacts.json", {})),
                        "packages": control.load_state(self.config, "packages.json", {}),
                        "deletedModules": control.load_state(self.config, "deleted-modules.json", []),
                        "lanes": control.load_lanes(self.config),
                    })
                response_json(self, 200, payload)
                return
            if path == "/api/events":
                self.stream_events()
                return
            if path in SPA_ROUTES or path == "/index.html":
                response_text(self, 200, (WEB_ROOT / "index.html").read_text(encoding="utf-8"), "text/html; charset=utf-8")
                return
            static_path = (WEB_ROOT / path.lstrip("/")).resolve()
            if static_path.is_file() and WEB_ROOT in static_path.parents:
                content_type = mimetypes.guess_type(static_path.name)[0] or "application/octet-stream"
                response_text(self, 200, static_path.read_bytes(), content_type)
                return
            response_json(self, 404, {"error": "not found"})
        except Exception as exc:
            response_json(self, 500, {"error": str(exc)})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            payload = read_request_json(self)
            if path == "/api/data/download":
                result = self.handle_data_download(payload)
                self.append_event("data.downloaded", result["dataset"])
                response_json(self, 200, result)
                return
            if path == "/api/data/upload":
                result = self.handle_data_upload(payload)
                self.append_event("data.uploaded", result["dataset"])
                response_json(self, 200, result)
                return
            if path == "/api/data/sources":
                result = self.handle_data_source_definition(payload)
                self.append_event("data.source.saved", result["definition"])
                response_json(self, 200, result)
                return
            if path == "/api/data/sources/activate":
                result = self.handle_data_source_activation(payload)
                self.append_event("data.source.activated", {
                    "source": result["source"],
                    "version": result["version"],
                })
                response_json(self, 200, result)
                return
            if path == "/api/data/proxy":
                result = self.handle_data_proxy(payload)
                self.append_event("data.proxy.updated", result["proxy"])
                response_json(self, 200, result)
                return
            if path == "/api/backtests":
                result = {"accepted": True, "backtest": market_data.run_backtest(self.config, payload)}
                self.append_event("backtest.completed", result["backtest"])
                response_json(self, 200, result)
                return
            if path == "/api/visualizations":
                result = market_data.save_visualization(self.config, payload)
                self.append_event("visualization.saved", result["visualization"])
                response_json(self, 200, result)
                return
            parts = [part for part in path.split("/") if part]
            if len(parts) == 4 and parts[:2] == ["api", "backtests"] and parts[3] == "result":
                response_json(self, 200, {
                    "backtestId": parts[2],
                    "result": market_data.get_backtest_result_slice(
                        self.config,
                        parts[2],
                        payload.get("paths") or [],
                        payload.get("temporaryModules") or [],
                    ),
                })
                return
            with control.control_state_lock(self.config):
                if path == "/api/modules":
                    result = control.handle_add_module(self.config, payload)
                elif path == "/api/packages":
                    result = control.handle_add_package(self.config, payload)
                elif path == "/api/instances":
                    result = control.handle_create_instance(self.config, payload)
                elif path == "/api/attach":
                    payload.setdefault("laneId", (parse_qs(parsed.query).get("laneId") or [control.DEFAULT_LANE_ID])[0])
                    result = control.handle_attach(self.config, payload)
                elif path == "/api/detach":
                    payload.setdefault("laneId", (parse_qs(parsed.query).get("laneId") or [control.DEFAULT_LANE_ID])[0])
                    result = control.handle_detach(self.config, payload)
                elif path == "/api/artifacts":
                    result = control.handle_record_artifact(self.config, payload)
                else:
                    response_json(self, 404, {"error": "not found"})
                    return
            response_json(self, 200, result)
        except Exception as exc:
            response_json(self, 400, {"accepted": False, "error": str(exc)})

    def append_event(self, event_type, payload):
        with control.control_state_lock(self.config):
            control.append_history_event(self.config, event_type, payload)

    def handle_data_download(self, payload):
        downloaded = market_data.download_source(
            payload.get("source") or "alphavantage",
            payload.get("symbol"),
            payload.get("startDate") or payload.get("start"),
            payload.get("endDate") or payload.get("end"),
            payload.get("interval") or "d",
            payload.get("apiKey") or payload.get("apikey") or "",
            config=self.config,
        )
        dataset_id = payload.get("datasetId") or f"{downloaded['source']}-{downloaded['symbol']}-{payload.get('interval') or 'd'}"
        dataset = market_data.save_dataset(
            self.config,
            dataset_id=dataset_id,
            name=payload.get("name") or f"{downloaded['symbol']} {downloaded['source'].upper()}",
            symbol=downloaded["symbol"],
            source=downloaded["source"],
            interval=payload.get("interval") or "d",
            rows=downloaded["rows"],
            csv_text=downloaded["csvText"],
            metadata={
                "sourceUrl": downloaded["sourceUrl"],
                "downloadedAt": control.utc_now(),
            },
        )
        return {"accepted": True, "dataset": dataset}

    def handle_data_proxy(self, payload):
        proxy_url = payload.get("url") or payload.get("proxyUrl") or ""
        with control.control_state_lock(self.config):
            saved = market_data.save_configured_proxy(self.config, proxy_url)
        return {
            "accepted": True,
            "proxy": {
                "enabled": bool(saved["url"]),
                "url": saved["url"],
                "updatedAt": saved["updatedAt"],
            },
        }

    def handle_data_source_definition(self, payload):
        with control.control_state_lock(self.config):
            return market_data.save_source_definition(self.config, payload)

    def handle_data_source_activation(self, payload):
        source = str(payload.get("source") or "").strip().lower()
        version = str(payload.get("version") or "").strip()
        if not source or not version:
            raise ValueError("source and version are required.")
        with control.control_state_lock(self.config):
            return market_data.activate_source_definition(self.config, source, version)

    def handle_data_upload(self, payload):
        csv_text = payload.get("csvText") or ""
        if not csv_text and payload.get("contentBase64"):
            csv_text = base64.b64decode(payload["contentBase64"]).decode("utf-8-sig")
        rows = market_data.parse_ohlcv_csv(csv_text)
        if not rows:
            raise ValueError("Upload requires OHLCV CSV with Date, Open, High, Low, Close, Volume columns.")
        dataset_id = payload.get("datasetId") or f"upload-{payload.get('symbol') or 'dataset'}-{control.json_digest(rows)[:10]}"
        dataset = market_data.save_dataset(
            self.config,
            dataset_id=dataset_id,
            name=payload.get("name") or dataset_id,
            symbol=payload.get("symbol") or dataset_id,
            source="upload",
            interval=payload.get("interval") or "custom",
            rows=rows,
            csv_text=csv_text,
            metadata=payload.get("metadata") or {},
        )
        return {"accepted": True, "dataset": dataset}

    def do_DELETE(self):
        parsed = urlparse(self.path)
        parts = [part for part in parsed.path.split("/") if part]
        try:
            if len(parts) == 6 and parts[:2] == ["api", "modules"] and parts[4] == "versions":
                with control.control_state_lock(self.config):
                    result = control.handle_delete_module(self.config, parts[2], parts[3], parts[5])
                response_json(self, 200, result)
                return
            response_json(self, 404, {"error": "not found"})
        except Exception as exc:
            response_json(self, 400, {"accepted": False, "error": str(exc)})

    def stream_events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        last_size = 0
        events_path = Path(self.config["controlRoot"]) / "events.jsonl"
        try:
            while True:
                if events_path.exists():
                    with events_path.open(encoding="utf-8") as handle:
                        handle.seek(last_size)
                        for line in handle:
                            line = line.strip()
                            if line:
                                self.wfile.write(f"data: {line}\n\n".encode("utf-8"))
                        last_size = handle.tell()
                        self.wfile.flush()
                time.sleep(2)
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(self, fmt, *args):
        return


def main():
    parser = argparse.ArgumentParser(description="Trade Engine web service and frontend.")
    parser.add_argument("--config", default=str(ROOT / ".runtime" / "strategy-control.json"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=30808)
    parser.add_argument("--public-url", default="https://trade.duckduckrun.com")
    args = parser.parse_args()

    EngineServiceHandler.config = control.load_config(args.config)
    control.refresh_engine_lanes_manifest(EngineServiceHandler.config)
    EngineServiceHandler.public_url = args.public_url
    server = ThreadingHTTPServer((args.host, args.port), EngineServiceHandler)
    print(f"engine service listening on http://{args.host}:{args.port}", flush=True)
    print(f"public url: {args.public_url}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
