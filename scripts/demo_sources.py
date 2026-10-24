"""演示数据播种：为「外部 API」与「Kafka 流」数据源造真实数据，供本地/文档联调。

用法（需先 docker compose up -d postgres kafka，且 .venv 可用）：
    python scripts/demo_sources.py --kafka      # 向 kafka orders-topic 写 8 条 JSON 记录后退出
    python scripts/demo_sources.py --api        # 起 127.0.0.1:8091 JSON 接口（Ctrl-C 退出）
说明：默认走项目 .venv 的 python。Kafka 目标 topic = orders-topic。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

KAFKA_BOOTSTRAP = "127.0.0.1:9092"
KAFKA_TOPIC = "orders-topic"
API_HOST, API_PORT = "127.0.0.1", 8091

KAFKA_RECORDS = [
    {"region": "华东", "channel": "线下", "ym": "2025-02", "sales": 3970.14, "orders": 4},
    {"region": "华南", "channel": "线上", "ym": "2025-03", "sales": 2586.09, "orders": 5},
    {"region": "华北", "channel": "线下", "ym": "2025-04", "sales": 3753.21, "orders": 6},
    {"region": "华东", "channel": "线上", "ym": "2025-02", "sales": 5100.5, "orders": 7},
    {"region": "华南", "channel": "线下", "ym": "2025-03", "sales": 1999.0, "orders": 3},
    {"region": "西南", "channel": "线上", "ym": "2025-04", "sales": 3200.9, "orders": 5},
    {"region": "东北", "channel": "线下", "ym": "2025-05", "sales": 2500.4, "orders": 4},
    {"region": "华东", "channel": "线下", "ym": "2025-06", "sales": 4800.2, "orders": 6},
]

API_PAYLOAD = {
    "data": [
        {"region": "华东", "channel": "线下", "ym": "2025-02", "sales": 12000.5, "orders": 15},
        {"region": "华南", "channel": "线上", "ym": "2025-02", "sales": 8500.0, "orders": 9},
        {"region": "华北", "channel": "线下", "ym": "2025-03", "sales": 4300.25, "orders": 6},
        {"region": "西南", "channel": "线上", "ym": "2025-03", "sales": 6700.9, "orders": 8},
        {"region": "东北", "channel": "线下", "ym": "2025-04", "sales": 3300.4, "orders": 4},
        {"region": "华东", "channel": "线上", "ym": "2025-03", "sales": 10100.3, "orders": 12},
    ]
}


def produce_kafka() -> None:
    from kafka import KafkaProducer

    p = KafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP)
    for r in KAFKA_RECORDS:
        p.send(KAFKA_TOPIC, json.dumps(r, ensure_ascii=False).encode("utf-8"))
    p.flush()
    p.close()
    print(f"已向 {KAFKA_BOOTSTRAP} 的 topic '{KAFKA_TOPIC}' 写入 {len(KAFKA_RECORDS)} 条 JSON 记录")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps(API_PAYLOAD, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # 静默
        pass


def serve_api() -> None:
    print(f"JSON 接口已启动：http://{API_HOST}:{API_PORT}/  （Ctrl-C 退出）")
    HTTPServer((API_HOST, API_PORT), _Handler).serve_forever()


def main() -> None:
    os.chdir(ROOT)  # 保证可 import app（如需）
    ap = argparse.ArgumentParser(description="为 API/Kafka 演示数据源造数据")
    ap.add_argument("--kafka", action="store_true", help="向 Kafka 写演示记录后退出")
    ap.add_argument("--api", action="store_true", help="启动本地 JSON 接口(8091)")
    args = ap.parse_args()
    if not args.kafka and not args.api:
        ap.print_help()
        sys.exit(1)
    if args.kafka:
        produce_kafka()
    if args.api:
        serve_api()


if __name__ == "__main__":
    main()
