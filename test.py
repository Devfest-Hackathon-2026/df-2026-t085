import json, time
from google import genai
from google.genai import types

client = genai.Client(vertexai=True, project="unsaid-devfest", location="global")
system = open("prompt.txt", encoding="utf-8").read()
schema = json.load(open("schema.json", encoding="utf-8"))

tests = ["頭痛がずっとある", "ちょっと前に転んで、足が痛いような気がする", "薬を飲んだか覚えていない"]

for t in tests:
    start = time.time()
    res = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=t,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            thinking_config=types.ThinkingConfig(thinking_level="low"),
        ),
    )
    print(f"{time.time() - start:.1f}秒", res.text)