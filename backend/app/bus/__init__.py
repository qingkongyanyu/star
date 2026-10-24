"""事件总线。

v1 为进程内 EventHub（调度器与 WebSocket 同 uvicorn 事件循环，直接广播）；
Kafka（topic task.events）接入后替换实现，接口签名（publish/subscribe）预留。
"""
