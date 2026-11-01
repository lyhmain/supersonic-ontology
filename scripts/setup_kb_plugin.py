#!/usr/bin/env python3
"""一键把「知识库问答」插件(JEV+RAG)注册进 SuperSonic,并创建绑定它的助理。

用法:
  export SUPERSONIC_BASE=http://127.0.0.1:9080
  export SUPERSONIC_USER=admin
  export SUPERSONIC_PASS='你的密码(协议同构加密值)'
  export KB_PLUGIN_URL=http://127.0.0.1:9102  # 插件回调地址(推荐: decision-service 的 /api/plugin/kb,
                                            #  含数据意图拦截+引用排版层;也可直连 kb-rag 裸服务)
  python scripts/setup_kb_plugin.py [--plugin-name 知识库问答] [--agent-name 知识库助手]

行为:
  1. 登录 SuperSonic(官方 /api/auth/user/login)。
  2. 若不存在同名插件「知识库问答」→ 创建(WEB_SERVICE,指向 KB_PLUGIN_URL 的 /api/plugin/kb);
     已存在但 URL 不一致 → 拒绝(防多实例串台),请用 --plugin-name 区分。
  3. 若不存在同名助理 → 创建并精确绑定该插件;已存在 → 替换其工具列表为该插件。

注意:
  - kb-rag 需先部署并导入文档(见 docs/INTEGRATION_KB.md);插件推荐指向 decision-service
    (数据意图拦截+引用排版),decision-service 内部再转发 kb-rag。
  - /api/plugin/kb 内置数据意图前置拦截:业务数据问题会被拒答并引导至问数助手,
    与「决策分析」插件天然互补,可挂给同一个助理双工具并存。
"""
import json
import os
import sys
import urllib.request

BASE = os.getenv("SUPERSONIC_BASE", "http://127.0.0.1:9080").rstrip("/")
USER = os.getenv("SUPERSONIC_USER", "admin")
PASS = os.getenv("SUPERSONIC_PASS", "")
KB = os.getenv("KB_PLUGIN_URL", os.getenv("ONTOLOGY_URL", "http://127.0.0.1:9102")).rstrip("/")
ARGS = sys.argv
PLUGIN_NAME = ARGS[ARGS.index("--plugin-name") + 1] if "--plugin-name" in ARGS else "知识库问答"
AGENT_NAME = ARGS[ARGS.index("--agent-name") + 1] if "--agent-name" in ARGS else "知识库助手"

PLUGIN_PATTERN = ("知识库问答:基于企业文档、报告、通知、规范的知识咨询;"
                  "产品说明、流程规范、活动规则、报告解读,以及用户提出的任何其他问题")
PLUGIN_EXAMPLES = ["金秋出游季促销活动规则是什么", "公司支持哪些文档格式",
                   "云梯服务区什么时候开业", "xx报告的TOP3结论是什么"]


def _req(url, method="GET", body=None, token=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    return json.load(urllib.request.urlopen(r, timeout=timeout))


def main():
    if not PASS:
        sys.exit("请先 export SUPERSONIC_PASS")
    tok = _req(f"{BASE}/api/auth/user/login", "POST",
               {"name": USER, "password": PASS})["data"]
    print(f"登录成功: {USER}")

    # ---- 插件 ----
    plugins = (_req(f"{BASE}/api/chat/plugin/getPluginList", "POST", {}, tok)
               .get("data") or [])
    plugin = next((p for p in plugins if p.get("name") == PLUGIN_NAME), None)
    plugin_cfg = {"url": f"{KB}/api/plugin/kb",
                  "paramOptions": [],
                  "params": []}
    if plugin:
        try:
            cur_url = (json.loads(plugin.get("config") or "{}").get("url") or "").split("?")[0]
        except Exception:
            cur_url = ""
        want_url = plugin_cfg["url"]
        if cur_url and cur_url != want_url:
            sys.exit(
                f"已存在同名插件「{PLUGIN_NAME}」(id={plugin['id']})但指向 {cur_url},"
                f"与本次 {want_url} 不一致。"
                f"多实例/多公司部署请用 --plugin-name 指定不同插件名,避免插件串台。")
        print(f"插件已存在且URL一致: id={plugin['id']} {plugin['name']}")
    else:
        body = {"type": "WEB_SERVICE", "name": PLUGIN_NAME,
                "pattern": PLUGIN_PATTERN,
                "parseModeConfig": json.dumps({
                    "name": PLUGIN_NAME, "description": PLUGIN_PATTERN,
                    "examples": PLUGIN_EXAMPLES}, ensure_ascii=False),
                "config": json.dumps(plugin_cfg, ensure_ascii=False),
                "containsAllDataSet": True}
        _req(f"{BASE}/api/chat/plugin", "POST", body, tok)
        plugins = (_req(f"{BASE}/api/chat/plugin/getPluginList", "POST", {}, tok)
                   .get("data") or [])
        plugin = next((p for p in plugins if p.get("name") == PLUGIN_NAME), None)
        if plugin is None:
            sys.exit("插件创建后未在列表中找到——通常是鉴权失败"
                     "(SuperSonic 登录需协议同构加密密码,检查 SUPERSONIC_PASS)。")
        print(f"插件已创建: id={plugin['id']}")

    # ---- 助理 ----
    agents = (_req(f"{BASE}/api/chat/agent/getAgentList", "POST", {}, tok)
              .get("data") or [])
    agent = next((a for a in agents if a.get("name") == AGENT_NAME), None)
    if agent:
        cfg = agent.get("toolConfig")
        try:
            cfg = json.loads(cfg) if isinstance(cfg, str) else (cfg or {})
        except Exception:
            cfg = {}
        # 精确绑定:替换为本次插件(避免残留指向其他部署的插件造成串台)
        cfg["tools"] = [{"type": "PLUGIN", "plugins": [plugin["id"]]}]
        agent["toolConfig"] = json.dumps(cfg, ensure_ascii=False)
        _req(f"{BASE}/api/chat/agent", "POST", agent, tok)   # 带 id 即更新
        print(f"助理已更新: id={agent['id']} {agent['name']} ← 插件{plugin['id']}")
    else:
        body = {"name": AGENT_NAME,
                "description": "基于企业知识库(JEV+RAG)的知识问答,带引用来源与拒答门控",
                "examples": PLUGIN_EXAMPLES[:2],
                "status": 1, "enableSearch": 1, "enableFeedback": 1,
                "toolConfig": json.dumps(
                    {"tools": [{"type": "PLUGIN", "plugins": [plugin["id"]]}]},
                    ensure_ascii=False)}
        _req(f"{BASE}/api/chat/agent", "POST", body, tok)
        print(f"助理已创建: {AGENT_NAME} ← 插件{plugin['id']}")

    print("\n完成。验证: curl %s/api/kb -d '{\"question\":\"...\"}' 或在聊天页选该助理提问。" % KB)
    print("提示: SuperSonic 新建插件的 embedding 召回可能有上游不稳定(见 docs/KNOWN_ISSUES.md #6),"
          "注册后请先用 parse 接口确认候选再交人工。")


if __name__ == "__main__":
    main()
