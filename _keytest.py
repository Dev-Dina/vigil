import httpx
from vigil.core.config import get_settings

s = get_settings()
raw = s.llm_api_key
key = raw.strip()
print(
    "len_raw="
    + str(len(raw))
    + " len_stripped="
    + str(len(key))
    + " trailing_whitespace="
    + str(raw != key)
)
r = httpx.post(
    s.llm_base_url + "/chat/completions",
    headers={"Authorization": "Bearer " + key},
    json={
        "model": s.llm_model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    },
    timeout=30,
)
print("openrouter_status=" + str(r.status_code))
if r.status_code != 200:
    print("detail:", str(r.json()))
else:
    print("detail: OK")
