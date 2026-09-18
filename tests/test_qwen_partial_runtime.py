from scripts.run_300_qwen_partial_gpu import PartialGpuClient


def test_partial_gpu_loading_option_reaches_chat_without_changing_generation(monkeypatch):
    client = PartialGpuClient(model="qwen3.5:9b", base_url="http://127.0.0.1:11435",
                              num_ctx=8192, num_predict=1024, temperature=0, seed=42, num_batch=128)
    payloads = []
    def post(path, payload):
        payloads.append((path, payload))
        return {"done": True, "done_reason": "stop", "message": {"content": "ready"}}
    monkeypatch.setattr(client, "_post", post)
    assert client.complete("same question", system="same system") == "ready"
    path, body = payloads[0]
    assert path == "/api/chat"
    assert body["options"]["num_gpu"] == 30
    assert body["options"]["num_ctx"] == 8192
    assert body["options"]["num_predict"] == 1024
    assert body["options"]["temperature"] == 0
    assert body["options"]["seed"] == 42
    assert body["options"]["num_batch"] == 128
    assert body["messages"] == [{"role": "system", "content": "same system"},
                                 {"role": "user", "content": "same question"}]
