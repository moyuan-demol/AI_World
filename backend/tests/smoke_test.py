"""AI World end-to-end smoke test.

Usage (backend must be running):

    python tests/smoke_test.py
    AI_WORLD_BASE_URL=http://127.0.0.1:8000 python tests/smoke_test.py

Covers: health -> register/login -> characters CRUD -> knowledge upload ->
RAG chat -> round table -> memories -> data isolation -> upload security.
Exit code 0 means every assertion passed.
"""

import os
import sys
import uuid

import httpx

BASE_URL = os.environ.get("AI_WORLD_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print("  [PASS] " + name)
    else:
        FAILED.append(name)
        print("  [FAIL] " + name + (" -> " + detail if detail else ""))


def headers(token: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token}


def main() -> int:
    # trust_env=False: 不要使用系统/环境里的 HTTP 代理。
    # 否则当本机开着代理（例如 Clash 的 127.0.0.1:xxxx）时，
    # 连 127.0.0.1 的请求也会被送进代理，得到 502 空响应。
    client = httpx.Client(base_url=BASE_URL, timeout=180.0, trust_env=False)

    print("\n== 1. 健康检查 ==")
    response = client.get("/api/health")
    check("health returns ok", response.status_code == 200 and response.json().get("status") == "ok", response.text[:200])

    print("\n== 2. 注册 / 登录 / 鉴权 ==")
    suffix = uuid.uuid4().hex[:8]
    user_a = {"username": "smoke_a_" + suffix, "password": "smoke12345", "email": "a@example.com"}
    user_b = {"username": "smoke_b_" + suffix, "password": "smoke12345"}

    response = client.post("/api/auth/register", json=user_a)
    check("register user A", response.status_code == 200, response.text[:200])
    token_a = response.json()["access_token"]

    response = client.post("/api/auth/login", json={"username": user_a["username"], "password": user_a["password"]})
    check("login user A", response.status_code == 200, response.text[:200])

    response = client.post("/api/auth/register", json=user_b)
    check("register user B", response.status_code == 200, response.text[:200])
    token_b = response.json()["access_token"]

    response = client.post("/api/auth/login", json={"username": user_a["username"], "password": "wrong-password"})
    check("wrong password rejected", response.status_code == 401, str(response.status_code))

    response = client.get("/api/characters")
    check("unauthenticated request rejected", response.status_code == 401, str(response.status_code))

    print("\n== 3. AI 伙伴 ==")
    response = client.post(
        "/api/characters",
        headers=headers(token_a),
        json={
            "name": "烟雾测试医生",
            "role": "医学专家",
            "personality": "严谨、耐心",
            "expertise": "医疗AI、临床决策支持",
            "speaking_style": "专业",
            "system_prompt": "回答需谨慎，不替代执业医师。",
        },
    )
    check("create character", response.status_code == 201, response.text[:200])
    character_id = response.json()["id"]

    response = client.get("/api/characters", headers=headers(token_a))
    check("list characters", response.status_code == 200 and any(item["id"] == character_id for item in response.json()))

    response = client.get("/api/characters/" + str(character_id), headers=headers(token_b))
    check("data isolation: B cannot read A character", response.status_code == 404, str(response.status_code))

    response = client.delete("/api/characters/" + str(character_id), headers=headers(token_b))
    check("data isolation: B cannot delete A character", response.status_code == 404, str(response.status_code))

    print("\n== 4. 知识库上传 + 向量化 ==")
    document_text = (
        "AI World 医疗 AI 产品白皮书\n\n"
        "第一章 市场概况：医疗 AI 在影像辅助诊断、临床决策支持、病历质控三个方向增长最快。\n"
        "第二章 技术架构：推荐采用 RAG 检索增强生成，把院内指南与病历切片后向量化存储并检索。\n"
        "第三章 合规风险：必须满足数据脱敏、审计留痕与医疗器械注册要求，"
        "医疗 AI 不能替代执业医师诊断，必须提示人工复核。\n"
    )
    files = {"file": ("医疗AI产品白皮书.txt", document_text.encode("utf-8"), "text/plain")}
    response = client.post(
        "/api/knowledge/upload",
        headers=headers(token_a),
        files=files,
        data={"name": "医疗AI资料库"},
    )
    check("upload txt document", response.status_code == 200, response.text[:300])
    upload = response.json()
    check(
        "document chunked and embedded",
        upload.get("chunk_count", 0) > 0 and upload.get("char_count", 0) > 0,
        str(upload)[:200],
    )
    knowledge_id = upload["knowledge"]["id"]

    response = client.get("/api/knowledge", headers=headers(token_a))
    check("list knowledge bases", response.status_code == 200 and len(response.json()) >= 1)

    response = client.get("/api/knowledge/" + str(knowledge_id) + "/documents", headers=headers(token_a))
    check("list document chunks", response.status_code == 200 and len(response.json()) > 0, response.text[:200])

    response = client.get("/api/knowledge/" + str(knowledge_id), headers=headers(token_b))
    check("data isolation: B cannot read A knowledge base", response.status_code == 404, str(response.status_code))

    print("\n== 5. AI 聊天 + RAG 检索 ==")
    response = client.post(
        "/api/chat",
        headers=headers(token_a),
        json={
            "character_id": character_id,
            "message": "医疗 AI 的技术架构和合规风险是什么？",
            "knowledge_id": knowledge_id,
        },
    )
    check("chat returns an answer", response.status_code == 200 and len(response.json().get("answer", "")) > 0, response.text[:300])
    chat = response.json()
    check("RAG retrieved knowledge sources", len(chat.get("sources", [])) > 0, str(chat.get("sources"))[:200])

    response = client.get("/api/chat/history", headers=headers(token_a), params={"character_id": character_id})
    check("chat history persisted", response.status_code == 200 and len(response.json()) >= 2, response.text[:200])

    print("\n== 6. AI 圆桌 ==")
    response = client.post(
        "/api/roundtable",
        headers=headers(token_a),
        json={"question": "如何开发一款医疗 AI 产品？", "character_ids": [character_id], "include_manager": True},
    )
    check("roundtable returns results", response.status_code == 200, response.text[:300])
    table = response.json()
    check("agents answered", len(table.get("results", [])) >= 1, str(table)[:200])
    check("manager summary present", len(table.get("summary", "")) > 0)

    print("\n== 7. 记忆系统 ==")
    response = client.post(
        "/api/memories",
        headers=headers(token_a),
        json={"content": "用户偏好简洁的回答", "memory_type": "preference", "character_id": character_id},
    )
    check("create memory", response.status_code == 201, response.text[:200])
    memory_id = response.json()["id"] if response.status_code == 201 else None

    response = client.get("/api/memories", headers=headers(token_a))
    check("list memories", response.status_code == 200 and len(response.json()) >= 1)

    if memory_id:
        response = client.delete("/api/memories/" + str(memory_id), headers=headers(token_a))
        check("delete memory", response.status_code == 204, str(response.status_code))

    print("\n== 8. 上传安全 ==")
    response = client.post(
        "/api/knowledge/upload",
        headers=headers(token_a),
        files={"file": ("evil.exe", b"MZ\x90\x00payload", "application/octet-stream")},
    )
    check("executable extension rejected", response.status_code == 400, str(response.status_code))

    response = client.post(
        "/api/knowledge/upload",
        headers=headers(token_a),
        files={"file": ("fake.pdf", b"this is definitely not a pdf", "application/pdf")},
    )
    check("fake pdf signature rejected", response.status_code == 400, str(response.status_code))

    response = client.post(
        "/api/knowledge/upload",
        headers=headers(token_a),
        files={"file": ("../../escape.txt", "hello".encode("utf-8"), "text/plain")},
    )
    check("traversal filename handled safely", response.status_code in (200, 400), str(response.status_code))

    print("\n== 9. 清理 ==")
    response = client.delete("/api/knowledge/" + str(knowledge_id), headers=headers(token_a))
    check("delete knowledge base", response.status_code == 204, str(response.status_code))

    response = client.delete("/api/characters/" + str(character_id), headers=headers(token_a))
    check("delete character (cascades conversations)", response.status_code == 204, str(response.status_code))

    client.close()

    print("\n" + "=" * 56)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        print("Failed checks:")
        for item in FAILED:
            print("  - " + item)
        return 1
    print("全部通过 - AI World Phase 1 端到端验证成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
