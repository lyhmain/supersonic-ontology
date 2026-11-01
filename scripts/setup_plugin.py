#!/usr/bin/env python3
"""一键把「决策分析」插件注册进 SuperSonic,并创建/更新绑定它的助理。

用法:
  export SUPERSONIC_BASE=http://127.0.0.1:9080
  export SUPERSONIC_USER=admin
  export SUPERSONIC_PASS='你的密码'
  export ONTOLOGY_URL=http://127.0.0.1:9102   # decision-service 地址
  python scripts/setup_plugin.py [--agent-name 助理名称]

行为:
  1. 登录 SuperSonic(官方 /api/auth/user/login)。
  2. 若不存在同名插件「决策分析」→ 创建(WEB_SERVICE,指向 decision-service 插件契约)。
  3. 若不存在同名助理 → 创建并绑定该插件;已存在 → 把插件追加进其工具列表。

零源码修改:全程只调 SuperSonic 官方 REST API,产物是数据库配置,升级镜像不受影响。
"""
import json
import os
import sys
import urllib.request

BASE = os.getenv("SUPERSONIC_BASE", "http://127.0.0.1:9080").rstrip("/")
USER = os.getenv("SUPERSONIC_USER", "admin")
PASS = os.getenv("SUPERSONIC_PASS", "")
ONTO = os.getenv("ONTOLOGY_URL", "http://127.0.0.1:9102").rstrip("/")
PLUGIN_NAME = os.sys.argv[sys.argv.index("--plugin-name") + 1] if "--plugin-name" in sys.argv else "决策分析"
AGENT_NAME = os.sys.argv[sys.argv.index("--agent-name") + 1] if "--agent-name" in sys.argv else "决策分析助手"

PLUGIN_PATTERN = ("决策分析:业务数据查询,指标(如销售额/GMV/销量/单量等),"
                  "分组对比、趋势、同环比、归因分析,以及用户提出的任何其他问题")
PLUGIN_EXAMPLES = ["昨天销售额是多少", "各门店/各服务区销售额排行",
                   "昨天销量有什么异常吗", "销售额下滑的原因是什么"]


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
    plugin_cfg = {"url": f"{ONTO}/api/plugin/analyze",
                  "paramOptions": [{"key": "policy", "value": "facts_first"}],
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
    tool = {"tools": [{"type": "PLUGIN", "plugins": [plugin["id"]]}]}
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
                "description": "通过「决策分析」插件做指标归因、异常检测与经营建议",
                "examples": PLUGIN_EXAMPLES[:2],
                "status": 1, "enableSearch": 1, "enableFeedback": 1,
                "toolConfig": json.dumps(tool, ensure_ascii=False)}
        _req(f"{BASE}/api/chat/agent", "POST", body, tok)
        print(f"助理已创建: {AGENT_NAME} ← 插件{plugin['id']}")

    print("\n完成。在 SuperSonic 聊天页选择该助理,直接问归因/异常类问题即可。")


if __name__ == "__main__":
    main()
